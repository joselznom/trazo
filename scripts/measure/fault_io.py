"""Inject OS failures at reached file boundaries in disposable workers only."""

from __future__ import annotations

import builtins
import errno
import inspect
import io
import json
import os
import signal
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from common import SOURCE


class Injector:
    def __init__(self, root: Path, journal: Path, fault: str, target: str) -> None:
        self.root, self.journal, self.fault, self.target = root, journal, fault, target
        self.open = builtins.open
        self.seen: set[str] = set()
        self.hits = 0
        self.enabled = True

    def location(self) -> str:
        frames, frame = [], inspect.currentframe()
        while frame is not None:
            filename = Path(frame.f_code.co_filename)
            if filename.is_relative_to(SOURCE):
                frames.append(f"{filename.name}:{frame.f_lineno}:{frame.f_code.co_name}")
            frame = frame.f_back
        return " > ".join(frames[:4])

    def path(self, value: object) -> Path | None:
        try:
            if isinstance(value, int):
                if sys.platform == "darwin":
                    import fcntl
                    value = fcntl.fcntl(value, 50, b"\0" * 1024).split(b"\0", 1)[0].decode()
                else:
                    value = os.readlink(f"/proc/self/fd/{value}")
            path = Path(value).absolute()
            return path if path.is_relative_to(self.root) else None
        except (TypeError, OSError, ValueError):
            return None

    def point(self, stage: str, path: Path) -> bool:
        if not self.enabled:
            return False
        location = self.location()
        if not location:
            return False
        key = stage + " | " + location
        if key not in self.seen:
            self.seen.add(key)
            with self.open(self.journal, "a", encoding="utf-8") as handle:
                handle.write(json.dumps({"site": key, "path": str(path.relative_to(self.root))}) + "\n")
                handle.flush()
        active = key == self.target
        if active:
            self.hits += 1
            with self.open(self.journal.with_suffix(".hit"), "w", encoding="utf-8") as handle:
                handle.write(key)
        return active

    def fail(self) -> None:
        if self.fault == "race":
            with self.open(self.journal.with_suffix(".ready"), "w") as handle:
                handle.write("paused before replacing the user's file")
            deadline = time.perf_counter() + 10
            while not self.journal.with_suffix(".release").exists():
                if time.perf_counter() > deadline:
                    raise TimeoutError("The independent writer did not release the barrier")
                time.sleep(.01)
            return
        if self.fault == "kill":
            os.kill(os.getpid(), signal.SIGKILL)
            # On macOS another thread can execute briefly before process-wide delivery.
            # Never let the worker perform the next publication while SIGKILL is pending.
            time.sleep(3600)
            raise RuntimeError("SIGKILL was not delivered")
        if self.fault == "full":
            raise OSError(errno.ENOSPC, "Injected disk full")
        if self.fault == "readonly":
            raise PermissionError(errno.EACCES, "Injected read-only directory")

    def install(self) -> None:
        injector = self

        class File:
            def __init__(self, handle: object, path: Path) -> None:
                self.handle, self.path = handle, path

            def __getattr__(self, name: str) -> object:
                return getattr(self.handle, name)

            def __enter__(self) -> File:
                self.handle.__enter__()
                return self

            def __exit__(self, *args: object) -> object:
                return self.handle.__exit__(*args)

            def __iter__(self) -> Iterator[Any]:
                return iter(self.handle)

            def write(self, data: bytes | str) -> int:
                if injector.point("write", self.path):
                    if injector.fault in ("kill", "full"):
                        self.handle.write(data[:max(1, len(data) // 2)])
                        self.handle.flush()
                    injector.fail()
                return self.handle.write(data)

            def writelines(self, lines: object) -> None:
                for line in lines:
                    self.write(line)

        def decorate(original: Callable[..., Any]) -> Callable[..., Any]:
            def opened(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
                path = injector.path(file)
                writable = any(c in mode for c in "wax+")
                if path and writable and injector.point("open", path):
                    injector.fail()
                handle = original(file, mode, *args, **kwargs)
                return File(handle, path) if path and writable else handle
            return opened

        builtins.open = decorate(builtins.open)
        io.open = decorate(io.open)
        original_replace = os.replace

        def replaced(source: Any, destination: Any, *args: Any, **kwargs: Any) -> Any:
            path = injector.path(destination)
            if path and injector.point("before_replace", path):
                injector.fail()
            result = original_replace(source, destination, *args, **kwargs)
            if path and injector.point("after_replace", path):
                injector.fail()
            return result
        os.replace = replaced
        from trazo import source_publish
        original_exchange = source_publish.exchange

        def exchanged(source: Any, destination: Any) -> None:
            # A source save publishes by swapping names where it can: the same stages as a rename.
            path = injector.path(destination)
            if path and injector.point("before_replace", path):
                injector.fail()
            original_exchange(source, destination)
            if path and injector.point("after_replace", path):
                injector.fail()
        source_publish.exchange = exchanged
