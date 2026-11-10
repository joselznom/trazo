"""What trazo does differently on Windows: process groups, file locks,
checking a PID and syncing a folder; and on macOS, flushing the drive's cache.

POSIX ends a process group with killpg and locks with flock. Windows has
neither: a group is the process tree that taskkill /T ends (forcefully: a
console program started without a window gets no Ctrl+Break), and a lock
is a byte range locked with msvcrt.locking. Windows byte-range locks are
mandatory, so the locked byte lies far past the content, which other
processes may still read. os.kill(pid, 0) is no check there: it calls
TerminateProcess.
"""

from __future__ import annotations

import errno
import functools
import os
import signal
import subprocess
import sys
import time
from typing import IO, Any

if os.name == "nt":
    import ctypes
    import msvcrt
else:
    import fcntl

LOCK_OFFSET = 1 << 30  # Windows: the locked byte, past any content


def address_in_use(exc: OSError) -> bool:
    """Whether a bind failed because the port is taken (Windows reports WSAEADDRINUSE)."""
    return exc.errno == errno.EADDRINUSE or getattr(exc, "winerror", None) == 10048


def replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
    """os.replace. Windows refuses it while another process has `target`
    open (a reader of the same state file), so there it retries for a second."""
    for _ in range(20 if os.name == "nt" else 0):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            time.sleep(0.05)
    os.replace(source, target)


# fsync answers these where a filesystem cannot sync (some network and FUSE
# volumes, a folder on some systems): nothing more can be done there.
_NO_SYNC = frozenset({errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOTTY})


def sync(fd: int, full: bool = False) -> None:
    """Flush an open file to the disk (fsync). macOS fsync leaves the data in
    the drive's own cache, so `full` there uses F_FULLFSYNC, which empties it
    (about 7 ms), and falls back to fsync where the filesystem does not support
    it. Any other failure, such as EIO, is raised."""
    if full and sys.platform == "darwin":
        try:
            fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
            return
        except OSError as exc:
            if exc.errno not in _NO_SYNC:
                raise
    try:
        os.fsync(fd)
    except OSError as exc:
        if exc.errno not in _NO_SYNC:
            raise


def sync_file(path: str | os.PathLike[str]) -> None:
    """sync() a file that is not open (Windows flushes only a writable handle)."""
    fd = os.open(path, os.O_RDWR if os.name == "nt" else os.O_RDONLY)
    try:
        sync(fd)
    finally:
        os.close(fd)


def sync_folder(folder: str | os.PathLike[str], full: bool = False) -> None:
    """sync() a folder, so the names a rename put in it survive a power loss.
    Windows cannot open a folder that way: there it is left to the filesystem."""
    if os.name == "nt":
        return
    fd = os.open(folder, os.O_RDONLY)
    try:
        sync(fd, full)
    finally:
        os.close(fd)


# Filesystems whose atomic exchange trazo uses: macOS APFS (renamex_np with
# RENAME_SWAP) and, on Linux, ext4, XFS, Btrfs and tmpfs (renameat2 with
# RENAME_EXCHANGE). Others are never tried, not even to catch ENOTSUP: on a
# FAT volume renamex_np left the calling process stuck in the kernel.
EXCHANGE_FILESYSTEMS = frozenset({"apfs", "ext4", "xfs", "btrfs", "tmpfs"})
_LINUX_FILESYSTEMS = {0xEF53: "ext4", 0x58465342: "xfs", 0x9123683E: "btrfs", 0x01021994: "tmpfs"}


class ExchangeUnsupported(OSError):
    """This platform or filesystem cannot swap two names atomically."""


@functools.cache
def _libc() -> Any:
    """The C library, with errno, on macOS and Linux; None elsewhere."""
    if sys.platform != "darwin" and not sys.platform.startswith("linux"):
        return None
    import ctypes
    return ctypes.CDLL(None, use_errno=True)


@functools.cache
def _statfs() -> Any:
    import ctypes
    libc = _libc()
    if libc is None:
        return None
    # Intel Macs name the 64-bit-inode struct statfs$INODE64; Apple silicon has only that one.
    statfs = getattr(libc, "statfs$INODE64", None) if sys.platform == "darwin" else None
    statfs = statfs or libc.statfs
    statfs.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
    return statfs


def filesystem_type(folder: str | os.PathLike[str]) -> str:
    """The type of the filesystem `folder` is on ("apfs", "ext4"...); "" when unknown."""
    import ctypes
    statfs = _statfs()
    buffer = ctypes.create_string_buffer(4096)  # larger than struct statfs on either system
    if statfs is None or statfs(os.fsencode(folder), buffer) != 0:
        return ""
    if sys.platform == "darwin":
        return buffer.raw[72:88].split(b"\0", 1)[0].decode("ascii", "replace")  # f_fstypename
    magic = ctypes.c_long.from_buffer(buffer).value & 0xFFFFFFFF  # f_type comes first
    return _LINUX_FILESYSTEMS.get(magic, f"{magic:#x}")


@functools.cache
def _exchange_call() -> Any:
    """The native swap of two paths, or None where the C library has none."""
    import ctypes
    libc = _libc()
    if libc is None:
        return None
    if sys.platform == "darwin":
        renamex_np = libc.renamex_np
        renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        return lambda a, b: renamex_np(a, b, 0x2)  # RENAME_SWAP
    renameat2 = getattr(libc, "renameat2", None)  # glibc 2.28 and later
    if renameat2 is None:
        return None
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    return lambda a, b: renameat2(-100, a, -100, b, 0x2)  # AT_FDCWD, RENAME_EXCHANGE


def can_exchange(folder: str | os.PathLike[str]) -> bool:
    """Whether exchange() works for two names in `folder`."""
    return filesystem_type(folder) in EXCHANGE_FILESYSTEMS and _exchange_call() is not None


def exchange(a: str | os.PathLike[str], b: str | os.PathLike[str]) -> None:
    """Swap the files named `a` and `b` (in one folder) in one step. Raises
    ExchangeUnsupported, without trying, where can_exchange() is false, and
    when the system refuses the call; OSError for any other failure."""
    import ctypes
    if not can_exchange(os.path.dirname(os.fspath(b)) or "."):
        raise ExchangeUnsupported(errno.ENOTSUP, "this filesystem cannot swap files atomically", os.fspath(b))
    if _exchange_call()(os.fsencode(a), os.fsencode(b)) != 0:
        code = ctypes.get_errno()
        error = ExchangeUnsupported if code in (errno.ENOTSUP, errno.EINVAL, errno.ENOSYS) else OSError
        raise error(code, os.strerror(code), os.fspath(b))


def no_window() -> dict[str, Any]:
    """Popen options: no console window on Windows."""
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def group_options() -> dict[str, Any]:
    """Popen options that start a process group kill_group can end, descendants included."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def kill_group(pid: int, graceful: bool = False) -> None:
    """End the group `pid` leads: SIGTERM when graceful, else SIGKILL; on
    Windows, always force-end the process tree. A group already gone is fine."""
    if os.name == "nt":
        taskkill = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "taskkill.exe")
        try:
            subprocess.run([taskkill, "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=30,
                           **no_window())
        except (OSError, subprocess.SubprocessError):
            pass
        return
    try:
        os.killpg(pid, signal.SIGTERM if graceful else signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def pid_alive(pid: int) -> bool:
    """Whether a process with this PID runs (one we may not signal counts)."""
    if os.name == "nt":
        kernel32 = _kernel32()
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED: it exists
        try:
            code = ctypes.c_ulong()
            ok = kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))
            return not ok or code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@functools.cache
def _kernel32() -> Any:
    """A private kernel32 (its restype settings stay out of ctypes.windll)."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    return kernel32


def try_lock(handle: IO[Any]) -> bool:
    """Take the exclusive lock on an open file without waiting; False when another holds it."""
    if os.name == "nt":
        try:
            _locking(handle, msvcrt.LK_NBLCK)
            return True
        except PermissionError:
            return False
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def lock(handle: IO[Any], timeout: float | None = None) -> bool:
    """Wait for the exclusive lock on an open file, at most `timeout` seconds
    (None: as long as it takes; Ctrl+C stops the wait); False when the time
    ran out. Closing the file releases it."""
    if timeout is None and os.name != "nt":
        fcntl.flock(handle, fcntl.LOCK_EX)  # PEP 475 retries it after a signal handler
        return True
    deadline = None if timeout is None else time.monotonic() + timeout
    while not try_lock(handle):
        if deadline is not None and time.monotonic() >= deadline:
            return False
        time.sleep(0.05)
    return True


def unlock(handle: IO[Any]) -> None:
    """Release the lock before closing the file."""
    if os.name == "nt":
        _locking(handle, msvcrt.LK_UNLCK)
        return
    fcntl.flock(handle, fcntl.LOCK_UN)


def _locking(handle: IO[Any], mode: int) -> None:
    """msvcrt.locking on the byte at LOCK_OFFSET; the descriptor's position is
    restored (the file object's buffer never sees the move)."""
    fd = handle.fileno()
    position = os.lseek(fd, 0, os.SEEK_CUR)
    try:
        os.lseek(fd, LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(fd, mode, 1)
    finally:
        os.lseek(fd, position, os.SEEK_SET)
