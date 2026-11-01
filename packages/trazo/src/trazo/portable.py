"""What trazo does differently on Windows: process groups, file locks and
checking a PID.

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
