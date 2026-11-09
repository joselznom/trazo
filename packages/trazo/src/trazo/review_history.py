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
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

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

    def publish(self, path: Path, data: bytes, entry: Entry, *, replace_last: bool = False) -> None:
        """Journal the prior text and undo entry before replacing a file, under the lock.

        The pending record resolves from the file contents on the next read. This
        avoids a second commit whose failure could break an existing autosave group.
        """
        history = self.load()
        pending = {"before_sha": sha256_bytes(path.read_bytes()) if path.exists() else None,
                   "previous": history[-1] if replace_last else None}
        prepared = {**entry, "pending": pending}
        if replace_last:
            history[-1] = prepared
        else:
            history.append(prepared)
        self.save(history)
        write_atomic(path, data)

    def revert_file(self, entry: Entry) -> None:
        path = Path(entry["file"]) if Path(entry["file"]).is_absolute() else ROOT / entry["file"]
        current = path.read_bytes() if path.exists() else None
        if current is None or sha256_bytes(current) != entry["after_sha"]:
            raise self.error(f"undo discarded: {entry['file']} changed since "
                             f"({entry['label']}); that entry was removed")
        if entry["before"] is None:
            path.unlink()
        else:
            write_atomic(path, entry["before"].encode("utf-8"))

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
            return f"undone: {entry['label']}; next undo: {following}"
