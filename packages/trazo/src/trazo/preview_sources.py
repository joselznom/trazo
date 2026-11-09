"""The Source tab: reading, saving and undoing a manuscript file, and locating a
paragraph in its source. Saves and their undo are the author's own changes:
they are accepted in the edit review as they are made (preview_edit_review).

The preview's paths and state stay in trazo.preview, and this code reads them from
there when it runs (preview.NAME), so patching them on trazo.preview reaches it.
"""

import hashlib
import os
import time
from pathlib import Path

from . import preview_edit_review, preview_locate
from .expand import Values, load_entries, resolve_include
from .local_server import Conflict, NotFound
from .local_server import RequestError as CommentError
from .review_storage import read_list, review_lock, write_atomic, write_json


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


def source_state(file, path, data, text):
    from . import preview
    sha = sha256_of(data)
    entries = [e for e in read_list(preview.FILE_HISTORY_PATH) if e.get("file") == file]
    return {"file": file, "text": text, "sha256": sha,
            "can_undo": bool(entries) and entries[-1].get("after_sha") == sha}


def save_file_history(entries):
    from . import preview
    entries = entries[-preview.FILE_HISTORY_LIMIT:]
    sizes = [len(e.get("before_text", "")) for e in entries]
    while len(entries) > 1 and sum(sizes) > preview.FILE_HISTORY_BYTES:  # drop the oldest
        entries.pop(0)
        sizes.pop(0)
    write_json(preview.FILE_HISTORY_PATH, entries)


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
    only if the file is still exactly what that save wrote; with {expect:
    save_id} only if that save is still the file's latest (409 otherwise,
    and the save stays undoable). Undoing a rejection of edits (kind
    "reject") makes those edits pending again. A save that wrote answers
    with its `save_id`, what `expect` names.

    Every write sets WATCHER["written"] (trazo.preview), so a page holds its
    reload until the watcher has built it.
    """
    from . import preview
    if not isinstance(request, dict):
        raise CommentError("request body must be a JSON object")
    op = request.get("op", "save")
    if op not in ("save", "undo"):
        raise CommentError(f"unknown op: {op!r}")
    with review_lock():
        path, file = source_path(request.get("file"))
        data, old = read_source(path)
        current = sha256_of(data)
        entries = read_list(preview.FILE_HISTORY_PATH)
        if op == "undo":
            mine = [i for i, e in enumerate(entries) if e.get("file") == file]
            if not mine:
                raise CommentError("nothing to undo")
            entry = entries[mine[-1]]
            if "expect" in request and str(entry.get("at")) != str(request["expect"]):
                raise Conflict("the file has a newer save", sha256=current)
            if entry.get("after_sha") != current:
                del entries[mine[-1]]
                save_file_history(entries)
                raise Conflict("undo discarded: the file changed since that save",
                               sha256=current)
            write_atomic(path, entry["before_text"].encode("utf-8"))
            if entry.get("kind") != "reject":
                preview_edit_review.absorb(file, old, entry["before_text"])
            del entries[mine[-1]]
            save_file_history(entries)
            mark_written()
            wrote = {}
        else:
            text = request.get("text")
            if not isinstance(text, str):
                raise CommentError("text must be a string")
            if request.get("base_sha256") != current:
                raise Conflict("the file changed on disk since it was loaded",
                               sha256=current)
            new = text.encode("utf-8")
            wrote = {}
            if new != data:
                at = time.time_ns()
                entries.append({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "at": at, "file": file,
                                "before_text": old, "before_sha": current,
                                "after_sha": sha256_of(new)})
                save_file_history(entries)
                write_atomic(path, new)
                preview_edit_review.absorb(file, old, text)
                mark_written()
                wrote = {"save_id": str(at)}  # a JavaScript number would round a time in nanoseconds
        data, text = read_source(path)
        return {**source_state(file, path, data, text), **wrote}


def mark_written():
    """A manuscript file was just written: pages hold their reload until the
    watcher's `synced` is later (both in /__workspace)."""
    from . import preview
    with preview.LOCK:
        preview.WATCHER["written"] = time.time()


def latest_change(doc):
    """What Cmd+Z outside the text undoes in a document: the most recent of a
    save and a comment change, each with its own undo. {kind: file|comment|"", file}."""
    from . import preview
    from .preview_comments import PreviewComments, PreviewHistory
    files = set(preview_edit_review.page_files(doc))
    with review_lock():
        saves = [e for e in read_list(preview.FILE_HISTORY_PATH) if e.get("file") in files]
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
