"""Reading a command's pipes for trazo.processes, without blocking on them:
selectors on POSIX, a thread per pipe on Windows (its pipes cannot be
selected). Output is kept per pipe, or passed line by line to on_line."""

from __future__ import annotations

import os
import selectors
import subprocess
import threading
from collections.abc import Callable
from typing import Any


def decode(data: bytes) -> str:
    """Text as subprocess.run(text=True) gives it, never failing: UTF-8 with
    replacement characters and universal newlines (Windows programs write CRLF)."""
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


class Reader:
    """What both readers share: output kept per pipe, or lines for on_line."""

    def __init__(self, process: subprocess.Popen[bytes], on_line: Callable[[str], None] | None) -> None:
        self.process, self.on_line = process, on_line
        self.chunks: dict[Any, list[bytes]] = {process.stdout: [], process.stderr: []}
        self.partial: dict[Any, bytearray] = {process.stdout: bytearray(), process.stderr: bytearray()}

    def feed(self, stream: Any, chunk: bytes) -> None:
        """Keep a chunk, or pass on_line its complete lines (b"" at the end flushes the rest)."""
        if self.on_line is None:
            self.chunks[stream].append(chunk)
            return
        partial = self.partial[stream]
        partial += chunk
        end = partial.rfind(b"\n") + 1 if chunk else len(partial)
        if end:
            for line in bytes(partial[:end]).split(b"\n")[:-1 if chunk or partial.endswith(b"\n") else None]:
                self.on_line(decode(line).rstrip("\n"))
            del partial[:end]

    def output(self) -> tuple[bytes | None, bytes | None]:
        if self.on_line is not None:
            return b"", None
        return tuple(None if s is None else b"".join(list(self.chunks[s]))  # type: ignore[return-value]
                     for s in (self.process.stdout, self.process.stderr))


class Selector(Reader):
    """POSIX: the pipes read (and stdin written) as they are ready."""

    def __init__(self, process: subprocess.Popen[bytes], data: bytes | None,
                 on_line: Callable[[str], None] | None) -> None:
        super().__init__(process, on_line)
        self.selector = selectors.DefaultSelector()
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                self.selector.register(stream, selectors.EVENT_READ)
        self.data, self.offset = data or b"", 0
        if process.stdin is not None:
            os.set_blocking(process.stdin.fileno(), False)
            self.selector.register(process.stdin, selectors.EVENT_WRITE)

    def open(self) -> bool:
        return any(key.fileobj is not self.process.stdin for key in self.selector.get_map().values())

    def pump(self, wait: float) -> None:
        for key, _ in self.selector.select(wait):
            if key.fileobj is self.process.stdin:
                self.write(key.fileobj)
                continue
            chunk = os.read(key.fd, 65536)
            if not chunk:
                self.selector.unregister(key.fileobj)
            self.feed(key.fileobj, chunk)

    def write(self, stdin: Any) -> None:
        try:
            self.offset += os.write(stdin.fileno(), self.data[self.offset:self.offset + 65536])
        except BlockingIOError:
            return
        except OSError:  # the command stopped reading
            self.offset = len(self.data)
        if self.offset >= len(self.data):
            self.selector.unregister(stdin)
            stdin.close()

    def close(self) -> None:
        self.selector.close()
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream is not None and not stream.closed:
                stream.close()


class Threads(Reader):
    """Windows, where pipes cannot be selected: a thread per pipe. A pipe
    still held at the end stays with its thread, which ends with the holder."""

    def __init__(self, process: subprocess.Popen[bytes], data: bytes | None,
                 on_line: Callable[[str], None] | None) -> None:
        super().__init__(process, on_line)
        self.readers = {stream: threading.Thread(target=self.read, args=(stream,), daemon=True)
                        for stream in (process.stdout, process.stderr) if stream is not None}
        threads = list(self.readers.values())
        if process.stdin is not None:
            threads.append(threading.Thread(target=self.write, args=(process.stdin, data), daemon=True))
        for thread in threads:
            thread.start()

    def read(self, stream: Any) -> None:
        chunk = b"-"
        while chunk:
            try:
                chunk = os.read(stream.fileno(), 65536)
            except OSError:  # the other end closed
                chunk = b""
            self.feed(stream, chunk)

    @staticmethod
    def write(stdin: Any, data: bytes) -> None:
        try:
            stdin.write(data)
            stdin.close()
        except OSError:  # the command stopped reading
            pass

    def open(self) -> bool:
        return any(thread.is_alive() for thread in self.readers.values())

    def pump(self, wait: float) -> None:
        for thread in self.readers.values():
            if thread.is_alive():
                thread.join(wait)
                return

    def close(self) -> None:
        for stream, thread in self.readers.items():
            if not thread.is_alive():  # one still blocked owns its stream
                stream.close()
