"""Undo history of agent and editor changes, shared by `trazo diagram` and `trazo table`.

Each tool keeps its own JSON list in the review folder (diagram_history.json,
table_history.json). An entry is {"time", "label", "kind", ...}. Kind "file"
holds the file's previous text ("before"; None when the change created the
file) and the sha256 of what was written ("after_sha"); undo restores
"before" and refuses (dropping the entry) when the file changed since. Other
kinds (the diagram comments) are reverted by the caller's `revert`. The
newest HISTORY_LIMIT entries are kept, within HISTORY_BYTES.

Read-modify-write happens under review_storage.review_lock(); `record`,
`revert_file` and `publish` expect the caller to hold it; `write_file` and
`undo` take it. A pending publication keeps the prior file's sha256 and the
previous undo entry, so a failed replacement can be reconciled on the next read. Journaling an attempted
save can evict the oldest entries at the history limits even if publication fails.
An existing file is replaced through source_publish: a program that ignores the
lock and writes the file after the caller's read keeps its version, the text not
saved goes to the conflict copies, the history is left as it was and `collision`
(the tool's error and an HTTP 409) is raised.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import source_publish
from .local_server import Conflict
from .review_storage import ROOT, read_list, review_lock, write_atomic

HISTORY_LIMIT = 200
HISTORY_BYTES = 5_000_000

Entry = dict[str, Any]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def rel(path: Path) -> str:
    """A path relative to the project root when it is inside it."""
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class History:
    """One tool's history file; `kinds` are the entry kinds it keeps."""

    def __init__(self, path: Path, kinds: tuple[str, ...] = ("file",),
                 error: type[Exception] = RuntimeError) -> None:
        self.path = path
        self.kinds = kinds
        self.error = error  # raised (with a user-facing message) when undo is refused
        # A write by another program during a publication: the tool's error, for its
        # CLI, and an HTTP 409 with the file now on disk, for its editor.
        self.collision: type[Exception] = type(error.__name__, (error, Conflict), {})
        self._thread = threading.local()  # this thread's last publication warning

    def take_warning(self) -> str:
        """The warning of this thread's last publication (see publish), once; for
        callers that publish through code that does not return it."""
        warning, self._thread.warning = getattr(self._thread, "warning", ""), ""
        return warning

    def load(self) -> list[Entry]:
        history: list[Entry] = []
        for entry in read_list(self.path):
            if not isinstance(entry, dict) or entry.get("kind") not in self.kinds:
                continue
            pending = entry.get("pending")
            if isinstance(pending, dict):
                path = ROOT / entry["file"]
                try:
                    current = sha256_bytes(path.read_bytes())
                except FileNotFoundError:
                    current = None
                except OSError:
                    history.append(entry)  # Preserve unresolved evidence without blocking other files.
                    continue
                if current == pending["before_sha"]:
                    if pending["previous"] is not None:
                        history.append(pending["previous"])
                    continue
                if current == entry["after_sha"]:
                    del entry["pending"]
            history.append(entry)
        return history

    def save(self, history: list[Entry]) -> None:
        history = history[-HISTORY_LIMIT:]
        sizes = [len(json.dumps(e, ensure_ascii=False)) for e in history]
        while len(history) > 1 and sum(sizes) > HISTORY_BYTES:
            history.pop(0)
            sizes.pop(0)
        write_atomic(self.path, (json.dumps(history, ensure_ascii=False) + "\n").encode("utf-8"))

    def record(self, entry: Entry) -> None:
        """Append one entry (the caller holds review_lock)."""
        history = self.load()
        history.append(entry)
        self.save(history)

    @staticmethod
    def file_entry(path: Path, before: bytes | None, data: bytes, label: str) -> Entry:
        return {"time": now(), "label": label, "kind": "file", "file": rel(path),
                "before": before.decode("utf-8") if before is not None else None,
                "after_sha": sha256_bytes(data)}

    def write_file(self, path: Path, data: bytes, label: str) -> None:
        """Write a file atomically and record its previous text for undo."""
        with review_lock():
            before = path.read_bytes() if path.exists() else None
            self.publish(path, data, self.file_entry(path, before, data, label))

    def publish(self, path: Path, data: bytes, entry: Entry, expected: bytes | None | str = "before",
                replace_last: bool = False) -> str:
        """Journal the prior text and undo entry before replacing a file, under the lock.

        `expected` is the file as the caller read it under the lock (None: it did
        not exist); by default the entry's "before", which is that text. The
        pending record resolves from the file contents on the next read. This
        avoids a second commit whose failure could break an existing autosave group.
        Returns a warning about trouble after publishing, or "".
        """
        if isinstance(expected, str):
            expected = None if entry["before"] is None else entry["before"].encode("utf-8")
        history = self.load()
        unchanged = list(history)
        pending = {"before_sha": sha256_bytes(expected) if expected is not None else None,
                   "previous": history[-1] if replace_last else None}
        prepared = {**entry, "pending": pending}
        if replace_last:
            history[-1] = prepared
        else:
            history.append(prepared)
        self.save(history)
        if expected is None:
            write_atomic(path, data)
            return ""
        result = self._replace(path, data, expected, history)
        if not result.published:
            self.save(unchanged)
            raise self._collided(path, result, "it was being saved")
        return result.warning

    def sweep(self, path: Path, current: bytes | None = None, history: list[Entry] | None = None) -> None:
        """Keep what late writes and killed saves left beside `path` (source_publish.sweep);
        `current` is the file as read under the lock. Under review_lock; the editors'
        reads call it, as the Source tab's do."""
        name = rel(path)
        if current is None and path.is_file():
            current = path.read_bytes()

        def known() -> set[str]:  # the texts this history can still restore
            entries = self.load() if history is None else history
            return ({sha256_bytes(current)} if current is not None else set()) | {
                sha256_bytes(e["before"].encode("utf-8")) for e in entries
                if e.get("file") == name and isinstance(e.get("before"), str)}
        source_publish.sweep(path, name, known)

    def _replace(self, path: Path, data: bytes, expected: bytes, history: list[Entry]) -> source_publish.Publication:
        name = rel(path)
        self.sweep(path, expected, history)
        result = source_publish.publish(path, data, expected, name)
        self._thread.warning = result.warning
        return result

    def _collided(self, path: Path, result: source_publish.Publication, during: str) -> Exception:
        try:
            current: bytes | None = path.read_bytes()
        except OSError:
            current = None
        kept = ", ".join(source_publish.shown(p) for p in result.kept)
        error = self.collision(f"{rel(path)} changed on disk while {during}: its version stays, and the text "
                               f"that was not saved is kept at {kept}")
        error.extra = {"sha256": sha256_bytes(current) if current is not None else None,  # type: ignore[attr-defined]
                       "text": current.decode("utf-8", "replace") if current is not None else None,
                       "kept": [source_publish.shown(p) for p in result.kept]}
        return error

    def revert_file(self, entry: Entry) -> None:
        path = Path(entry["file"]) if Path(entry["file"]).is_absolute() else ROOT / entry["file"]
        current = path.read_bytes() if path.exists() else None
        if current is None or sha256_bytes(current) != entry["after_sha"]:
            raise self.error(f"undo discarded: {entry['file']} changed since "
                             f"({entry['label']}); that entry was removed")
        if entry["before"] is None:
            path.unlink()
        else:
            result = self._replace(path, entry["before"].encode("utf-8"), current, self.load())
            if not result.published:
                raise self._collided(path, result, "the undo was being written")

    def undo(self, revert: Callable[[Entry], None] | None = None,
             match: Callable[[Entry], bool] | None = None) -> str:
        """Revert the newest entry (the newest for which `match` is true, keeping
        the others); a refused entry is dropped and `error` raised."""
        with review_lock():
            history = self.load()
            mine = [i for i, e in enumerate(history) if match is None or match(e)]
            if not mine:
                return "nothing to undo"
            entry = history.pop(mine[-1])
            try:
                if entry["kind"] == "file":
                    self.revert_file(entry)
                elif revert is not None:
                    revert(entry)
            except self.error:
                self.save(history)
                raise
            self.save(history)
            rest = [e for e in history if match is None or match(e)]
            following = rest[-1]["label"] if rest else "nothing"
            warning = self.take_warning() if entry["kind"] == "file" else ""
            return f"undone: {entry['label']}; next undo: {following}" + (f". {warning}" if warning else "")
