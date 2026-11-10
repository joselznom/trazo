"""Review storage: configuration, cross-process locking, atomic writes and
damaged stores.

A writer killed mid-write (SIGKILL, a crash) leaves its temporary file or
folder behind. Each is locked while in use, and the next write of the same
file (for a folder, the next scratch_folder in the same place) removes those
older than ORPHAN_SECONDS that nobody holds: a live writer keeps its file even
when suspended for longer than that.
"""

import json
import os
import re
import shutil
import stat
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, NoReturn

from .portable import lock, replace, sync, sync_folder, try_lock, unlock
from .project import PROJECT

ROOT = PROJECT.root
# PREVIEW_REVIEW_DIR overrides the project review folder.
REVIEW_DIR = Path(os.environ.get("PREVIEW_REVIEW_DIR") or PROJECT.path("review"))
LOCK_PATH = REVIEW_DIR / ".lock"
_THREAD_LOCK = threading.Lock()
ORPHAN_SECONDS = 600  # a writer's own time with its temporary file (source_publish.DISPLACED_GRACE too)


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


def write_atomic(path: Path, data: bytes, default_mode: int = 0o644, *,
                 durable: bool = False, full: bool = False) -> None:
    """Write bytes atomically through a uniquely named temporary file beside
    `path` (`<name>.<8 characters>.tmp`), then remove_orphans(path).

    `durable` is for the user's own data (sources, specs, histories, comments):
    the file and then its folder are synced (portable.sync), so a power loss
    leaves the old or the new file, never a renamed empty one. `full` (implies
    `durable`) uses F_FULLFSYNC on macOS. Build outputs need neither: a build
    makes them again.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else default_mode
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            try_lock(handle)  # held until closed: remove_orphans leaves the file alone
            handle.write(data)
            handle.flush()
            os.chmod(tmp, mode)  # mkstemp creates 0600
            if durable or full:
                sync(handle.fileno(), full)
        replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    if durable or full:
        sync_folder(path.parent, full)
    remove_orphans(path)


def remove_orphans(path: Path) -> None:
    """Remove the temporary files killed writers of `path` left: named as
    write_atomic names them, older than ORPHAN_SECONDS and not locked. Never
    an error of the write that calls it."""
    pattern = re.compile(re.escape(path.name) + r"\.[a-z0-9_]{8}\.tmp")
    try:
        names = os.listdir(path.parent)
    except OSError:
        return
    for name in names:
        candidate = path.parent / name
        if pattern.fullmatch(name) and _abandoned(candidate):
            try:
                candidate.unlink()
            except OSError:
                pass


@contextmanager
def scratch_folder(parent: Path, prefix: str) -> Iterator[Path]:
    """A temporary folder in `parent`, locked through its `.lock` file while in
    use and removed after. The folders with `prefix` that killed users left
    there are removed first, as remove_orphans does for files."""
    for leftover in parent.iterdir():
        if leftover.name.startswith(prefix) and leftover.is_dir() and _abandoned(leftover, leftover / ".lock"):
            shutil.rmtree(leftover, ignore_errors=True)
    with tempfile.TemporaryDirectory(prefix=prefix, dir=parent) as folder, \
            open(Path(folder) / ".lock", "wb") as handle:
        try_lock(handle)
        yield Path(folder)


def _abandoned(candidate: Path, lock_file: Path | None = None) -> bool:
    """Whether `candidate` was left by a killed writer: older than
    ORPHAN_SECONDS (a live writer took its lock right after creating it) and
    its lock free: on `lock_file` for a folder, on the file itself otherwise.
    A folder without a lock file comes from an earlier trazo."""
    try:
        if time.time() - candidate.stat().st_mtime < ORPHAN_SECONDS:
            return False
        with open(lock_file or candidate, "rb") as handle:
            return try_lock(handle)
    except FileNotFoundError:
        return lock_file is not None and candidate.is_dir()
    except OSError:  # unreadable: not trazo's to remove
        return False


def quarantine(path: Path, what: str, cause: Exception) -> NoReturn:
    """Move the damaged store at `path` aside, unchanged, and raise StoreError
    saying where; the next operation starts a new one. `what` names it."""
    # Imported here: setup imports this module, and a stdlib module new to it
    # would write its bytecode into the Python that setup must leave unchanged.
    from uuid import uuid4
    backup = path.with_name(f"{path.stem}.corrupt-{uuid4().hex}{path.suffix}")
    try:
        replace(path, backup)
    except OSError as error:
        raise StoreError(f"{what} is corrupt and could not be preserved: {error}. "
                         f"No file was changed. Check the filesystem error and retry.") from error
    raise StoreError(f"{what} is corrupt; preserved unchanged at {backup}. "
                     f"Retry your action to start new undo history. Previous undo entries are unavailable.") from cause


def holds_text(path: Path, text: str) -> bool:
    """Whether the file at `path` reads as `text` (UTF-8, newlines as Python
    reads them). Bytes that are not UTF-8, such as a file re-saved by hand in
    another encoding, never do: the file differs instead of raising."""
    return path.read_text(encoding="utf-8", errors="surrogateescape") == text


def keep_newest[T](entries: list[T], count: int, size: int, measure: Callable[[T], int],
                   pending: bool) -> list[T]:
    """The newest `entries` within `count` entries and `size` (by `measure`),
    always at least one. A `pending` last entry (a save not yet published) is
    not counted: if its publication fails, the next read drops it, and the
    oldest entry must still be there."""
    spare = 1 if pending and entries else 0
    entries = entries[-(count + spare):]
    sizes = [measure(entry) for entry in entries[:len(entries) - spare]]
    while len(sizes) > 1 and sum(sizes) > size:  # drop the oldest
        entries.pop(0)
        sizes.pop(0)
    return entries


def write_json(path: Path, data: Any, *, durable: bool = False, **dump: Any) -> None:
    write_atomic(path, (json.dumps(data, ensure_ascii=False, **dump) + "\n").encode("utf-8"), durable=durable)


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
