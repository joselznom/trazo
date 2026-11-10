"""The Source tab: reading, saving and undoing a manuscript file, and locating a
paragraph in its source. Saves and their undo are the author's own changes:
they are accepted in the edit review as they are made (preview_edit_review).
Malformed source history is preserved beside the store and reported once; the
next action can start a new undo history without changing manuscript text.

The preview's paths and state stay in trazo.preview, and this code reads them from
there when it runs (preview.NAME), so patching them on trazo.preview reaches it.
"""

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from . import preview_edit_review, preview_locate, source_publish
from .expand import Values, load_entries, resolve_include
from .local_server import Conflict, NotFound
from .local_server import RequestError as CommentError
from .portable import can_exchange
from .review_storage import StoreError, quarantine, read_list, review_lock, write_json


def sha256_of(data):
    return hashlib.sha256(data).hexdigest()


def source_path(file):
    """Resolve a repo-relative manuscript .md path to (path, canonical name).

    Accepts only existing regular files under a document folder
    (manuscript/main or manuscript/supplementary): no absolute paths, `..`, trailing slash,
    symlinks, or names that differ in case from the directory entry. The
    canonical name (posix path relative to the repo) keys history and replies.
    """
    from . import preview
    if (not isinstance(file, str) or not file or "\0" in file or os.path.isabs(file)
            or file.endswith("/") or "\\" in file):
        raise CommentError("file must be a repository-relative path")
    path = Path(os.path.normpath(preview.ROOT / file))
    if not any(path.is_relative_to(preview.ROOT / r) for r in preview.SOURCE_ROOTS) or path.suffix != ".md":
        raise CommentError("file must be a .md under " + " or ".join(preview.SOURCE_ROOTS))
    if Path(os.path.realpath(path)) != path:
        raise CommentError("symbolic links are not allowed")
    if not path.is_file():
        raise NotFound(f"no such file: {file}")
    walk = preview.ROOT
    for part in path.relative_to(preview.ROOT).parts:
        if part not in os.listdir(walk):
            raise CommentError("file name does not match the directory entry (case)")
        walk = walk / part
    return path, path.relative_to(preview.ROOT).as_posix()


def read_source(path):
    data = path.read_bytes()
    try:
        return data, data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CommentError(f"{path.name} is not valid UTF-8: {exc}") from exc


def read_file_history() -> list[dict[str, Any]]:
    """Read under review_lock; preserve corrupt bytes before allowing a retry.

    A damaged undo store cannot safely describe an earlier manuscript. The first
    request reports its preserved copy; later requests record a new history.
    A save is recorded "pending" before its file is published; reading settles
    it (resolve_pending), so a save that failed or was killed leaves the undo
    history as it was before it.
    """
    from . import preview
    path = preview.FILE_HISTORY_PATH
    try:
        entries = read_list(path)
        for entry in entries:
            if (not isinstance(entry, dict)
                    or any(not isinstance(entry.get(key), str) for key in ("file", "before_text", "after_sha"))
                    or not isinstance(entry.get("time", ""), str)
                    or not isinstance(entry.get("at", 0), int)):
                raise StoreError("invalid source history entry")
        json.dumps(entries, ensure_ascii=False).encode("utf-8")
    except (StoreError, UnicodeEncodeError) as exc:
        quarantine(path, "Source history", exc)
    return [settled for settled in map(resolve_pending, entries) if settled is not None]


def resolve_pending(entry: dict[str, Any]) -> dict[str, Any] | None:
    """A pending save: dropped (None) when its file still holds the text from
    before it, settled when it holds the saved text; otherwise (a third
    version, or an unreadable file) kept pending, and undo refuses it."""
    from . import preview
    if not entry.get("pending"):
        return entry
    try:
        current: str | None = sha256_of((preview.ROOT / entry["file"]).read_bytes())
    except FileNotFoundError:
        current = None
    except OSError:
        return entry
    if current == entry.get("before_sha"):
        return None
    if current == entry["after_sha"]:
        return {key: value for key, value in entry.items() if key != "pending"}
    return entry


def source_state(file, path, data, text):
    """The Source tab's view of a file. It also keeps what other programs'
    writes and killed saves left behind (source_publish.sweep): `conflicts`
    lists the copies kept; `exact` is false where a save cannot rule out
    losing a write made at the instant it publishes."""
    sha = sha256_of(data)
    entries = [e for e in read_file_history() if e.get("file") == file]
    # Only bytes the history stores count: an after_sha names text kept nowhere else.
    source_publish.sweep(path, file, lambda: {sha} | {
        e.get("before_sha") or sha256_of(e["before_text"].encode("utf-8")) for e in entries})
    return {"file": file, "text": text, "sha256": sha,
            "can_undo": bool(entries) and entries[-1].get("after_sha") == sha,
            "conflicts": source_publish.conflicts(file), "exact": can_exchange(path.parent)}


def collided(file, path, result):
    """The 409 of a save or undo that met another program's write after its
    locked read: that version stays on disk, trazo's text is in `kept`."""
    try:
        current = sha256_of(path.read_bytes())
    except OSError:
        current = ""
    kept = [source_publish.shown(p) for p in result.kept]
    return Conflict("another program changed the file while it was being saved: its version is on disk, "
                    "and the text that was not saved is kept at " + ", ".join(kept),
                    sha256=current, kept=kept, conflicts=source_publish.conflicts(file), exact=result.exact)


def save_file_history(entries):
    """Keep the newest entries within the limits. A pending entry at the end
    does not count: if its publication fails, reading drops it, and the
    oldest entry must still be there."""
    from . import preview
    spare = 1 if entries and entries[-1].get("pending") else 0
    entries = entries[-(preview.FILE_HISTORY_LIMIT + spare):]
    sizes = [len(e.get("before_text", "")) for e in entries[:len(entries) - spare]]
    while len(sizes) > 1 and sum(sizes) > preview.FILE_HISTORY_BYTES:  # drop the oldest
        entries.pop(0)
        sizes.pop(0)
    write_json(preview.FILE_HISTORY_PATH, entries, durable=True)


def get_source(file):
    with review_lock():
        path, file = source_path(file)
        data, text = read_source(path)
        return source_state(file, path, data, text)


def change_source(request):
    """Save a manuscript file, or undo the last save of one.

    save: {file, text, base_sha256} writes only if the file on disk still has
    the loaded sha256 (409 with the current sha otherwise). The previous
    content goes to file_history.json. undo: {op: "undo", file} restores it,
    only if the file is still exactly what that save wrote. Undoing a
    rejection of edits (kind "reject") makes those edits pending again.
    Either is published by source_publish.publish: a write by another program
    after the locked read answers 409 with `kept`, and records no undo. A
    save answers the sha256 of the bytes it wrote, so a write landing just
    after it makes the next save a 409; `warning` reports trouble after
    publishing.
    """
    if not isinstance(request, dict):
        raise CommentError("request body must be a JSON object")
    op = request.get("op", "save")
    if op not in ("save", "undo"):
        raise CommentError(f"unknown op: {op!r}")
    with review_lock():
        path, file = source_path(request.get("file"))
        data, old = read_source(path)
        current = sha256_of(data)
        entries = read_file_history()
        if op == "undo":
            mine = [i for i, e in enumerate(entries) if e.get("file") == file]
            if not mine:
                raise CommentError("nothing to undo")
            entry = entries[mine[-1]]
            if entry.get("after_sha") != current:
                del entries[mine[-1]]
                save_file_history(entries)
                raise Conflict("undo discarded: the file changed since that save",
                               sha256=current)
            result = source_publish.publish(path, entry["before_text"].encode("utf-8"), data, file)
            if not result.published:
                del entries[mine[-1]]
                save_file_history(entries)
                raise collided(file, path, result)
            if entry.get("kind") != "reject":
                preview_edit_review.absorb(file, old, entry["before_text"])
            del entries[mine[-1]]
            save_file_history(entries)
            data, text = read_source(path)
            return {**source_state(file, path, data, text), "warning": result.warning}
        else:
            text = request.get("text")
            if not isinstance(text, str):
                raise CommentError("text must be a string")
            if request.get("base_sha256") != current:
                raise Conflict("the file changed on disk since it was loaded",
                               sha256=current)
            new = text.encode("utf-8")
            reply: dict[str, Any] = {}
            if new != data:
                entries.append({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "at": time.time_ns(), "file": file,
                                "before_text": old, "before_sha": current,
                                "after_sha": sha256_of(new), "pending": True})
                save_file_history(entries)
                result = source_publish.publish(path, new, data, file)
                if not result.published:
                    entries.pop()
                    save_file_history(entries)
                    raise collided(file, path, result)
                reply = {"exact": result.exact, "warning": result.warning}
                preview_edit_review.absorb(file, old, text)
            # What this save wrote, not a re-read: the browser keeps this sha256 as
            # its base, so a write landing after the rename must make its next save a 409.
            return {**source_state(file, path, new, text), **reply}
        data, text = read_source(path)
        return source_state(file, path, data, text)


def latest_change(doc):
    """What Cmd+Z outside the text undoes in a document: the most recent of a
    save and a comment change, each with its own undo. {kind: file|comment|"", file}."""
    from . import preview
    from .preview_comments import PreviewComments, PreviewHistory
    files = set(preview_edit_review.page_files(doc))
    with review_lock():
        saves = [e for e in read_file_history() if e.get("file") in files]
        history = PreviewHistory(preview.HISTORY_PATH).load()
        comments = [history[i] for i in PreviewComments().history_of(history, doc)]
    # `at` tells apart two changes of one second; older entries have only `time`.
    latest = [(entries[-1].get("time", ""), entries[-1].get("at", 0)) if entries else ("", 0) for entries in (saves, comments)]
    if saves and latest[0] >= latest[1]:
        return {"kind": "file", "file": saves[-1].get("file", "")}
    return {"kind": "comment" if comments else "", "file": ""}


def read_include_from(path_text, source_file):
    """Text of an `!include`d file (inside the repo only), else None."""
    from . import preview
    try:
        target = resolve_include(path_text, source_file).resolve()
        if target.is_file() and target.is_relative_to(preview.ROOT):
            return target.read_text(encoding="utf-8")
    except (OSError, ValueError, UnicodeDecodeError):
        pass
    return None


def locate_source(request):
    """Best source lines for a rendered paragraph: {file, text[, source]}."""
    from . import preview
    if not isinstance(request, dict):
        raise CommentError("request body must be a JSON object")
    text = request.get("text")
    source = request.get("source")
    if not isinstance(text, str) or not (source is None or isinstance(source, str)):
        raise CommentError("text (and source, if given) must be strings")
    path, file = source_path(request.get("file"))
    if source is None:
        source = read_source(path)[1]
    try:
        values = Values(load_entries(check_inputs=False))
    except Exception:  # values are only a matching aid
        values = {}
    result = {"file": file, **preview_locate.locate(
        source, text, values, lambda t: read_include_from(t, path))}
    if result["score"] < preview.LOCATE_LOW:
        # The paragraph may come from another file of the document (for
        # example one without a level-1 heading): search the siblings.
        document = preview.PROJECT.source_document([path])
        for other in preview.PROJECT.document_files(document) if document else []:
            if other == path or other.is_symlink():
                continue
            try:
                found = preview_locate.locate(read_source(other)[1], text, values,
                                              lambda t, o=other: read_include_from(t, o))
            except CommentError:
                continue
            if found["score"] > result["score"]:
                result = {"file": other.relative_to(preview.ROOT).as_posix(), **found}
    return result
