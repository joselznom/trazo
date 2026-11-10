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
previous undo entry, so a failed replacement can be reconciled on the next read. The pending entry
does not count against the limits, so a failed publication never evicts an older entry. A damaged
history file is set aside unchanged as the manuscript's is (review_storage.quarantine): the operation
that finds it reports where, and the next one starts a new history.
An existing file is replaced through source_publish: a program that ignores the
lock and writes the file after the caller's read keeps its version, the text not
saved goes to the conflict copies, the history is left as it was and `error` is
raised with `extra` (the file now on disk and the copies), which the editors
answer as HTTP 409.
"""

from __future__ import annotations

import hashlib
import json
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import source_publish
from .review_storage import ROOT, StoreError, quarantine, read_list, review_lock, write_atomic

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
        self._thread = threading.local()  # this thread's last publication warning

    def take_warning(self) -> str:
        """The warning of this thread's last publication (see publish), once; for
        callers that publish through code that does not return it."""
        warning, self._thread.warning = getattr(self._thread, "warning", ""), ""
        return warning

    def load(self) -> list[Entry]:
        try:
            stored = read_list(self.path)
        except StoreError as exc:
            quarantine(self.path, f"{self.path.name} (undo history)", exc)
        history: list[Entry] = []
        for entry in stored:
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
        # A pending entry at the end is not counted: if its publication fails,
        # the next load drops it, and the oldest entry must still be there.
        spare = 1 if history and "pending" in history[-1] else 0
        history = history[-(HISTORY_LIMIT + spare):]
        sizes = [len(json.dumps(e, ensure_ascii=False)) for e in history[:len(history) - spare]]
        while len(sizes) > 1 and sum(sizes) > HISTORY_BYTES:
            history.pop(0)
            sizes.pop(0)
        write_atomic(self.path, (json.dumps(history, ensure_ascii=False) + "\n").encode("utf-8"), durable=True)

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

    def publish(self, path: Path, data: bytes, entry: Entry, expected: bytes | None = None,
                replace_last: bool = False) -> str:
        """Journal the prior text and undo entry before replacing a file, under the lock.

        `expected` is the file as the caller read it under the lock; None takes the
        entry's "before", which is that text (None there too: the file did not
        exist). The pending record resolves from the file contents on the next
        read. This avoids a second commit whose failure could break an existing
        autosave group. Returns a warning about trouble after publishing, or "",
        also kept for take_warning().
        """
        if expected is None and entry["before"] is not None:
            expected = entry["before"].encode("utf-8")
        self._thread.warning = ""
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
            write_atomic(path, data, full=True)
            return ""
        result = self._replace(path, data, expected)
        if not result.published:
            self.save(unchanged)
            raise self._collided(path, result, "it was being saved")
        self._thread.warning = result.warning
        if result.warning:  # the CLI's user and the server log see it too
            print(f"trazo: {result.warning}", file=sys.stderr)
        return result.warning

    def sweep(self, path: Path) -> None:
        """Keep what late writes and killed saves left beside `path` (source_publish.sweep).
        Under review_lock; the editors' reads call it, as the Source tab's do."""
        self._report(path, source_publish.sweep(path, rel(path), self._known(path, None)))

    def _known(self, path: Path, current: bytes | None) -> Callable[[], set[str]]:
        """The texts of `path` this history can still restore, and the file itself
        (`current`, else read when asked; the caller holds the lock)."""
        name = rel(path)

        def known() -> set[str]:
            data = current if current is not None else path.read_bytes() if path.is_file() else None
            return ({sha256_bytes(data)} if data is not None else set()) | {
                sha256_bytes(e["before"].encode("utf-8")) for e in self.load()
                if e.get("file") == name and isinstance(e.get("before"), str)}
        return known

    @staticmethod
    def _report(path: Path, copies: list[Path]) -> None:
        for copy in copies:  # the editors have no place for them: the CLI's user and the server log
            print(f"trazo: kept text another program wrote to {rel(path)} at {source_publish.shown(copy)}",
                  file=sys.stderr)

    def _replace(self, path: Path, data: bytes, expected: bytes) -> source_publish.Publication:
        name = rel(path)  # publish() sweeps the parked files itself
        self._report(path, source_publish.sweep_orphans(path, name, self._known(path, expected)))
        return source_publish.publish(path, data, expected, name)

    def _collided(self, path: Path, result: source_publish.Publication, during: str) -> Exception:
        try:
            current: bytes | None = path.read_bytes()
        except OSError:
            current = None
        kept = [source_publish.shown(p) for p in result.kept]
        error = self.error(f"{rel(path)} changed on disk while {during}: its version stays, and the text "
                           f"that was not saved is kept at {', '.join(kept)}")
        # The editors answer it as HTTP 409 with these fields; the error classes do not declare it.
        setattr(error, "extra", {"sha256": sha256_bytes(current) if current is not None else None,
                                 "text": current.decode("utf-8", "replace") if current is not None else None,
                                 "kept": kept})
        return error

    def revert_file(self, entry: Entry) -> str:
        """Restore an entry's "before"; return a warning about trouble after publishing, or ""."""
        path = Path(entry["file"]) if Path(entry["file"]).is_absolute() else ROOT / entry["file"]
        current = path.read_bytes() if path.exists() else None
        if current is None or sha256_bytes(current) != entry["after_sha"]:
            raise self.error(f"undo discarded: {entry['file']} changed since "
                             f"({entry['label']}); that entry was removed")
        if entry["before"] is None:
            path.unlink()
            return ""
        result = self._replace(path, entry["before"].encode("utf-8"), current)
        if not result.published:
            raise self._collided(path, result, "the undo was being written")
        return result.warning

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
            warning = ""
            try:
                if entry["kind"] == "file":
                    warning = self.revert_file(entry)
                elif revert is not None:
                    revert(entry)
            except self.error:
                self.save(history)
                raise
            self.save(history)
            rest = [e for e in history if match is None or match(e)]
            following = rest[-1]["label"] if rest else "nothing"
            self._thread.warning = warning  # the editors show it on its own
            return f"undone: {entry['label']}; next undo: {following}" + (f". {warning}" if warning else "")
