"""Review storage: configuration, cross-process locking and atomic JSON writes."""

import json
import os
import stat
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .portable import lock, unlock
from .project import PROJECT

ROOT = PROJECT.root
# PREVIEW_REVIEW_DIR overrides the project review folder.
REVIEW_DIR = Path(os.environ.get("PREVIEW_REVIEW_DIR") or PROJECT.path("review"))
LOCK_PATH = REVIEW_DIR / ".lock"
_THREAD_LOCK = threading.Lock()


class StoreError(Exception):
    """A review store is unreadable; never overwritten."""


@contextmanager
def review_lock() -> Iterator[None]:
    """Exclusive lock for read-modify-write, across threads and processes.

    Not reentrant: never take it twice in the same thread.
    """
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    with _THREAD_LOCK, open(LOCK_PATH, "a+") as handle:
        lock(handle)
        try:
            yield
        finally:
            unlock(handle)


def write_atomic(path: Path, data: bytes, default_mode: int = 0o644) -> None:
    """Write bytes atomically through a uniquely named temporary file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else default_mode
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(tmp, mode)  # mkstemp creates 0600
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_json(path: Path, data: Any, **dump: Any) -> None:
    write_atomic(path, (json.dumps(data, ensure_ascii=False, **dump) + "\n").encode("utf-8"))


def read_list(path: Path) -> list[Any]:
    """A JSON list store; [] when missing, StoreError when malformed."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise StoreError(f"{path} is malformed ({exc}); fix or remove it") from exc
    if not isinstance(data, list):
        raise StoreError(f"{path} is malformed (expected a JSON list)")
    return data
