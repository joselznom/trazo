"""Opt-in instrumentation in disposable child interpreters; no product edits."""

import atexit
import cProfile
import functools
import json
import os
import pstats
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from types import FrameType
from typing import Any


def install() -> None:
    destination = Path(os.environ["TRAZO_MEASURE_PROFILE"])
    destination.mkdir(parents=True, exist_ok=True)
    events = []
    depth = threading.local()
    lock = threading.Lock()
    profilers = []
    started = time.perf_counter()
    started_cpu = time.process_time()

    def wrap(owner: object, name: str, category: str) -> None:
        original = getattr(owner, name)

        @functools.wraps(original)
        def observed(*args: Any, **kwargs: Any) -> Any:
            active = getattr(depth, "active", False)
            if active:
                return original(*args, **kwargs)
            depth.active = True
            begin = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                elapsed = time.perf_counter() - begin
                first = args[0] if args else kwargs.get("args", "")
                label = str(first.args) if isinstance(first, subprocess.Popen) else str(first)
                with lock:
                    events.append({"category": category, "method": name, "label": label[:500],
                                   "start": begin - started, "seconds": elapsed,
                                   "thread": threading.current_thread().name})
                depth.active = False
        setattr(owner, name, observed)

    for name in ("run",):
        wrap(subprocess, name, "subprocess")
    wrap(subprocess.Popen, "communicate", "subprocess")
    for name in ("read_bytes", "read_text", "write_bytes", "write_text", "stat", "mkdir", "unlink"):
        wrap(Path, name, "filesystem_api")
    original_thread = threading.Thread.run

    def span(original: Callable[..., Any], category: str) -> Callable[..., Any]:
        @functools.wraps(original)
        def observed(*args: Any, **kwargs: Any) -> Any:
            begin = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                with lock:
                    events.append({"category": category, "method": original.__name__, "label": str(args),
                                   "start": begin - started, "seconds": time.perf_counter() - begin,
                                   "thread": threading.current_thread().name})
        return observed

    def profiled_thread(self: threading.Thread) -> None:
        if getattr(getattr(self, "_target", None), "__name__", "") != "build_and_watch":
            original_thread(self)
            return
        from trazo import preview
        preview.snapshot = span(preview.snapshot, "watcher_scan")
        preview.build = span(preview.build, "python_render_span")
        profiler = cProfile.Profile()
        with lock:
            profilers.append((self.name + "-" + str(threading.get_ident()), profiler))
        profiler.enable()
        try:
            original_thread(self)
        finally:
            profiler.disable()

    threading.Thread.run = profiled_thread
    main_profile = cProfile.Profile()
    # Python 3.12 reserves one monitoring tool ID for cProfile per interpreter.
    # In the preview process, give it to the render/watcher thread, not the HTTP accept loop.
    if "--serve" not in sys.argv:
        main_profile.enable()
        profilers.append(("main", main_profile))

    def finish() -> None:
        main_profile.disable()
        rows = []
        for name, profiler in profilers:
            safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
            path = destination / f"{os.getpid()}-{safe}.prof"
            profiler.dump_stats(str(path))
            stats = pstats.Stats(profiler)
            for (file, line, function), (primitive, calls, own, cumulative, callers) in stats.stats.items():
                rows.append({"thread": name, "file": file, "line": line, "function": function,
                             "calls": calls, "own_seconds": own, "cumulative_seconds": cumulative})
        # Use builtin open: recording the profiler's own report would obscure I/O measurements.
        with open(destination / f"{os.getpid()}.json", "w", encoding="utf-8") as handle:
            json.dump({"pid": os.getpid(), "start_perf_counter": started, "wall_seconds": time.perf_counter() - started,
                       "process_cpu_seconds": time.process_time() - started_cpu,
                       "events": events, "functions": sorted(rows, key=lambda r: -r["own_seconds"])}, handle)

    atexit.register(finish)
    if signal.getsignal(signal.SIGTERM) == signal.SIG_DFL:
        def terminate(signum: int, frame: FrameType | None) -> None:
            raise SystemExit(128 + signum)
        signal.signal(signal.SIGTERM, terminate)


if os.environ.get("TRAZO_MEASURE_PROFILE"):
    install()
