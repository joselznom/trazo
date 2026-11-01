"""Shared headless browser transport for rendering, checks and exports."""

from __future__ import annotations

import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .portable import no_window
from .project import PROJECT, chrome_flags

if os.name == "nt":
    import msvcrt
else:
    import fcntl
    import select

CHROME = PROJECT.chrome()
CHROME_TIMEOUT = 60.0
_STDERR_LIMIT = 8192
_CLOSE_TIMEOUT = 2.0

# Chrome requires descriptors 3/4. Map them in a fresh interpreter, since
# Popen's preexec_fn can deadlock when the preview server has other threads.
_BIND_PIPES = """
import os, sys
source_in, source_out = map(int, sys.argv[1:3])
os.dup2(source_in, 3)
os.dup2(source_out, 4)
os.close(source_in)
os.close(source_out)
os.execv(sys.argv[3], sys.argv[3:])
"""


class BrowserError(Exception):
    """A user-facing render or browser failure."""


def _check_macos_sandbox() -> None:
    """Refuse a known macOS crash before launching Chrome's GUI executable."""
    if sys.platform != "darwin":
        return
    import ctypes

    try:
        sandbox = ctypes.CDLL("/usr/lib/system/libsystem_sandbox.dylib")
        check = sandbox.sandbox_check
        # sandbox_check is variadic: only the first three arguments are fixed
        # (this distinction matters for the arm64 calling convention).
        check.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        check.restype = ctypes.c_int
        no_report = ctypes.c_int.in_dll(sandbox, "SANDBOX_CHECK_NO_REPORT").value
        global_name = 2  # SANDBOX_FILTER_GLOBAL_NAME, as declared by WebKit's SandboxSPI.h.
        denied = check(os.getpid(), b"mach-lookup", global_name | no_report,
                       ctypes.c_char_p(b"com.apple.coreservices.launchservicesd"))
    except (OSError, AttributeError, ValueError):
        return  # The optional system API is unavailable on this macOS version.
    if denied > 0:
        raise BrowserError("macOS sandbox blocks Chrome's access to LaunchServices; "
                           "headless Chrome still needs this system service. Run the command "
                           "from a terminal or with permission to launch GUI processes.")


class Chrome:
    """Headless Chrome driven over the DevTools pipe (--remote-debugging-pipe):
    fds 3/4 on POSIX; on Windows, inherited pipe handles that
    --remote-debugging-io-pipes names, read by a thread since select()
    takes only sockets there."""

    def __init__(self, timeout: float = CHROME_TIMEOUT) -> None:
        if not CHROME or not Path(CHROME).exists():
            raise BrowserError(f"Chrome not found ({CHROME or 'no known location'}); run `scripts/trazo setup` "
                               "(it downloads one where it can), or set TRAZO_CHROME or [browser] chrome in trazo.toml")
        _check_macos_sandbox()
        self.timeout = timeout
        self.profile = ""
        self.proc = None
        self.writer = None
        self.reader = None
        self.buffer = b""
        self.chunks: queue.Queue[bytes] = queue.Queue()
        self.next_id = 0
        self._stderr = b""
        self._stderr_stop = threading.Event()
        self._stderr_thread = None
        self._reader_thread = None
        self._closed = False
        try:
            self.profile = tempfile.mkdtemp(prefix="trazo-chrome-")
            fds: set[int] = set()
            try:
                cmd_read, cmd_write = os.pipe()
                fds.update((cmd_read, cmd_write))
                res_read, res_write = os.pipe()
                fds.update((res_read, res_write))
                self.writer = os.fdopen(cmd_write, "wb")
                fds.remove(cmd_write)
                self.reader = res_read
                fds.remove(res_read)
                command = [CHROME, *chrome_flags(), "--headless=new", "--remote-debugging-pipe", "--disable-gpu",
                           "--hide-scrollbars", "--allow-file-access-from-files", "--no-first-run",
                           "--no-default-browser-check", "--disable-extensions", "--mute-audio",
                           f"--user-data-dir={self.profile}", "about:blank"]
                if os.name == "nt":
                    self.proc = self._start_windows(command, cmd_read, res_write)
                else:
                    child_in = fcntl.fcntl(cmd_read, fcntl.F_DUPFD_CLOEXEC, 10)
                    fds.add(child_in)
                    child_out = fcntl.fcntl(res_write, fcntl.F_DUPFD_CLOEXEC, 10)
                    fds.add(child_out)
                    self.proc = subprocess.Popen(
                        [sys.executable, "-I", "-c", _BIND_PIPES, str(child_in), str(child_out), *command],
                        pass_fds=(child_in, child_out), stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            finally:
                for fd in fds:
                    os.close(fd)
            self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True,
                                                    name="trazo-chrome-stderr")
            self._stderr_thread.start()
            if os.name == "nt":
                self._reader_thread = threading.Thread(target=self._read_pipe, daemon=True,
                                                       name="trazo-chrome-pipe")
                self._reader_thread.start()
            self.call("Browser.getVersion")  # Validate startup while we own partial resources.
        except OSError as exc:
            self.close()
            raise BrowserError(f"could not start headless Chrome ({CHROME}): {exc}") from exc
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _start_windows(command: list[str], read_fd: int, write_fd: int) -> subprocess.Popen:
        """Start Chrome with only these two pipe ends inherited, and no console window."""
        handles = [msvcrt.get_osfhandle(read_fd), msvcrt.get_osfhandle(write_fd)]
        for handle in handles:
            os.set_handle_inheritable(handle, True)
        return subprocess.Popen(
            [*command, f"--remote-debugging-io-pipes={handles[0]},{handles[1]}"],
            startupinfo=subprocess.STARTUPINFO(lpAttributeList={"handle_list": handles}),
            stdin=subprocess.DEVNULL, **no_window(),
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def _read_pipe(self) -> None:
        """Windows: queue Chrome's replies; an empty chunk is the end of the pipe."""
        while True:
            try:
                chunk = os.read(self.reader, 1 << 20)
            except OSError:
                chunk = b""
            self.chunks.put(chunk)
            if not chunk:
                return

    def _read(self, timeout: float) -> bytes | None:
        """The next chunk of replies; b"" once Chrome closed the pipe, None after `timeout`."""
        if os.name == "nt":
            try:
                chunk = self.chunks.get(timeout=timeout)
            except queue.Empty:
                return None
            if not chunk:
                self.chunks.put(chunk)  # every later read sees the end too
            return chunk
        if not select.select([self.reader], [], [], timeout)[0]:
            return None
        return os.read(self.reader, 1 << 20)

    def _read_stderr(self) -> None:
        fd = self.proc.stderr.fileno()
        while not self._stderr_stop.is_set():
            # Windows: a blocking read, which ends when Chrome exits.
            if os.name != "nt" and not select.select([fd], [], [], 0.1)[0]:
                continue
            chunk = os.read(fd, 4096)
            if not chunk:
                return
            self._stderr = (self._stderr + chunk)[-_STDERR_LIMIT:]

    def _failure(self, message: str, method: str, exited: bool = False) -> BrowserError:
        if exited:
            try:
                self.proc.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                pass
        status = self.proc.poll()
        if status is not None and self._stderr_thread is not None:
            self._stderr_thread.join(timeout=0.2)
        detail = f"headless Chrome {message} on {method}"
        if status is not None:
            if status < 0:
                detail += f" (signal {signal.Signals(-status).name})"
            else:
                detail += f" (exit status {status})"
        stderr = self._stderr.decode("utf-8", errors="replace").strip()
        return BrowserError(detail + (f"\n{stderr}" if stderr else ""))

    def call(self, method: str, params: dict[str, Any] | None = None,
             session: str | None = None, *, timeout: float | None = None) -> dict[str, Any]:
        if self.writer is None or self.writer.closed or self.reader is None:
            raise BrowserError("headless Chrome is closed")
        self.next_id += 1
        message: dict[str, Any] = {"id": self.next_id, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        try:
            self.writer.write(json.dumps(message).encode("utf-8") + b"\0")
            self.writer.flush()
        except OSError as exc:
            raise self._failure("exited unexpectedly", method, exited=True) from exc
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        while True:
            while b"\0" not in self.buffer:
                left = deadline - time.monotonic()
                chunk = self._read(left) if left > 0 else None
                if chunk is None:
                    raise self._failure("timed out", method)
                if not chunk:
                    raise self._failure("exited unexpectedly", method, exited=True)
                self.buffer += chunk
            raw, self.buffer = self.buffer.split(b"\0", 1)
            reply = json.loads(raw)
            if reply.get("id") == self.next_id:
                if "error" in reply:
                    raise BrowserError(f"Chrome {method} failed: {reply['error'].get('message')}")
                return reply.get("result", {})

    @contextmanager
    def target(self) -> Iterator[str]:
        """Own a tab, including failed attachment, without hiding render errors."""
        target = self.call("Target.createTarget", {"url": "about:blank"})["targetId"]
        failed = False
        try:
            session = self.call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
            yield session
        except BaseException:
            failed = True
            raise
        finally:
            try:
                self.call("Target.closeTarget", {"targetId": target}, timeout=min(self.timeout, _CLOSE_TIMEOUT))
            except BrowserError:
                if not failed:
                    raise

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self.proc is not None:
                if self.proc.poll() is None:
                    try:
                        self.call("Browser.close", timeout=min(self.timeout, _CLOSE_TIMEOUT))
                    except BrowserError:
                        pass
                try:
                    self.proc.wait(timeout=_CLOSE_TIMEOUT)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait()  # Reap before deleting the profile or returning.
        finally:
            self._stderr_stop.set()
            if self._stderr_thread is not None and self._stderr_thread.ident is not None:
                self._stderr_thread.join(timeout=0.5)
            if self.proc is not None and self.proc.stderr is not None:
                self.proc.stderr.close()
            if self.writer is not None:
                try:
                    self.writer.close()
                except OSError:
                    pass
            if self._reader_thread is not None and self._reader_thread.ident is not None:
                self._reader_thread.join(timeout=0.5)
            if self.reader is not None:
                os.close(self.reader)
                self.reader = None
            if self.profile:
                shutil.rmtree(self.profile, ignore_errors=True)

    def __enter__(self) -> Chrome:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
