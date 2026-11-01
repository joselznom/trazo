"""One build of a project at a time, across processes (flock; on Windows, msvcrt.locking: trazo.portable).

Everything that writes build records (<build>/steps.json) or build outputs
takes the lock: `trazo build` (also --prepare; the MCP `build` tool and
the preview's Word job run it), `run`, `verify`, a table render
(table.render_one: `table render`, the table editor, the preview's spec
tables), a diagram export (`diagram export`, the preview's export job) and
the preview's tables stage. A caller that finds it taken prints one line and
waits (Ctrl+C or killing the process stops it). With $TRAZO_BUILD_LOCK_WAIT
(seconds) the first lock a process takes waits that long at most, then the
command exits with BUSY: the MCP preview_start (trazo.preview_inputs) sets
it on its build, since an app ends a tool call that waits too long. The
process removes it from its environment, so the commands it runs do not
inherit it.

The lock file is in the project's .cache/trazo/ (git-ignored by every
paper since v0.1.0), named after the build folder: outside the build folder,
`rm -rf .build` during a build (by a person, an agent or `verify`) cannot
drop it, and every process building the paper finds the same file whatever
its temp folder (the desktop app's MCP server and an agent's sandboxed shell
often differ) and can write it (a sandbox may not write outside the
project). A [paths] build that contains that folder (".cache", ".") is
refused (LockError): `verify` would delete the lock it holds, and the next
build would lock a new file while it runs. The holder writes its PID into
the file for the nested runs below.

Nesting: the commands a build runs ([[build.steps]], [commands] entries, the
preview's tables command) get $TRAZO_BUILD_LOCK ("pid:path"). A trazo
they start for the same project runs inside its parent's turn, instead of
waiting for it forever, while the process the marker names is alive and
holds the lock; a stale marker locks normally. The lock's descriptor is not
inherited (Python opens files non-inheritable), so it is released when its
holder exits: trazo's own cancellations (the MCP server's, the preview's
jobs) kill the holder's whole process group, nested runs included.
"""

from __future__ import annotations

import hashlib
import os
import sys
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from functools import wraps
from pathlib import Path

from .portable import lock, pid_alive, try_lock
from .project import PROJECT, Project

LOCK_ENV = "TRAZO_BUILD_LOCK"
WAIT_ENV = "TRAZO_BUILD_LOCK_WAIT"
BUSY = 75  # EX_TEMPFAIL: the lock was still taken after $TRAZO_BUILD_LOCK_WAIT
WAITING = "[build] waiting for another build of this project"
# What this thread holds: .path, and .marker for the commands it runs.
_HELD = threading.local()


class LockError(OSError):
    """A build lock that cannot be made (an OSError: callers report those already)."""


def lock_path(project: Project = PROJECT) -> Path:
    """The lock file of a project's build folder (resolved: the marker names it)."""
    build = project.path("build").resolve()
    key = hashlib.sha256(str(build).encode()).hexdigest()[:32]
    path = project.root.resolve() / ".cache" / "trazo" / f"build-{key}.lock"
    if path.is_relative_to(build):
        raise LockError(f"[paths] build = {project.config['paths']['build']!r} contains .cache/trazo, "
                        "where the build lock is; choose a build folder outside it (default .build)")
    return path


def child_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment for a command this thread's build runs: `env`
    (default os.environ) with the marker, when this thread holds a lock."""
    marker: str | None = getattr(_HELD, "marker", None)
    return {**(os.environ if env is None else env), **({LOCK_ENV: marker} if marker else {})}


@contextmanager
def build_lock(project: Project = PROJECT,
               log: Callable[[str], None] = lambda text: print(text, flush=True)) -> Iterator[None]:
    """Hold the project's build lock; reentrant in one thread and for the
    processes the holder starts (see the module docstring). A wait bounded by
    $TRAZO_BUILD_LOCK_WAIT that runs out raises TimeoutError."""
    wait = _bounded_wait()
    path = lock_path(project) if project.found else None  # outside a project: nothing to share
    if path is None or getattr(_HELD, "path", None) == path:
        yield
        return
    saved = {key: getattr(_HELD, key, None) for key in ("path", "marker")}
    handle = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # O_NOFOLLOW: the holder truncates the file; never one a symlink points to (Windows has no flag).
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        handle = os.fdopen(os.open(path, flags, 0o644), "r+", encoding="utf-8")
        if not try_lock(handle):
            pid = _marker_pid(path)
            if pid is not None and _holder(path) == pid and pid_alive(pid):  # started by the holder
                _HELD.path, _HELD.marker = path, os.environ[LOCK_ENV]
                yield
                return
            log(WAITING)
            if not lock(handle, wait):  # Ctrl+C interrupts the wait
                raise TimeoutError(f"another build of this project was still running after {wait:g} s")
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        _HELD.path, _HELD.marker = path, f"{os.getpid()}:{path}"
        yield
    finally:
        for key, value in saved.items():
            setattr(_HELD, key, value)
        if handle is not None:
            handle.close()  # closing the file releases the lock


def _bounded_wait() -> float | None:
    try:
        return float(os.environ.pop(WAIT_ENV))
    except (KeyError, ValueError):
        return None


def _marker_pid(path: Path) -> int | None:
    """The PID in $TRAZO_BUILD_LOCK when the marker names this lock."""
    pid, _, named = os.environ.get(LOCK_ENV, "").partition(":")
    return int(pid) if named == str(path) and pid.isdigit() and int(pid) > 0 else None


def _holder(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def exclusive[**P, R](function: Callable[P, R]) -> Callable[P, R]:
    """`function` run under the build lock of PROJECT."""
    @wraps(function)
    def run(*args: P.args, **kwargs: P.kwargs) -> R:
        with build_lock():
            return function(*args, **kwargs)
    return run


def locked[**P](main: Callable[P, int]) -> Callable[P, int]:
    """exclusive, for a command's main: a lock that cannot be made is one error line."""
    @wraps(main)
    def run(*args: P.args, **kwargs: P.kwargs) -> int:
        with ExitStack() as stack:
            try:
                stack.enter_context(build_lock())
            except TimeoutError as exc:
                print(f"trazo: {exc}", file=sys.stderr)
                return BUSY
            except OSError as exc:
                print(f"trazo: cannot lock the build: {exc}", file=sys.stderr)
                return 1
            return main(*args, **kwargs)
    return run
