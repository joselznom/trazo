"""The Source and Diff tabs: reading, saving and undoing a manuscript file, locating a
paragraph in its source and diffing the file against git HEAD.

The preview's paths and state stay in trazo.preview, and this code reads them from
there when it runs (preview.NAME), so patching them on trazo.preview reaches it.
"""

import hashlib
import os
import subprocess
import time
from pathlib import Path

from . import preview_diff, preview_locate
from .expand import Values, load_entries, resolve_include
from .local_server import Conflict, NotFound
from .local_server import RequestError as CommentError
from .review_storage import StoreError, read_list, review_lock, write_atomic, write_json


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
    only if the file is still exactly what that save wrote.
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
            if entry.get("after_sha") != current:
                del entries[mine[-1]]
                save_file_history(entries)
                raise Conflict("undo discarded: the file changed since that save",
                               sha256=current)
            write_atomic(path, entry["before_text"].encode("utf-8"))
            del entries[mine[-1]]
            save_file_history(entries)
        else:
            text = request.get("text")
            if not isinstance(text, str):
                raise CommentError("text must be a string")
            if request.get("base_sha256") != current:
                raise Conflict("the file changed on disk since it was loaded",
                               sha256=current)
            new = text.encode("utf-8")
            if new != data:
                entries.append({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "file": file,
                                "before_text": old, "before_sha": current,
                                "after_sha": sha256_of(new)})
                save_file_history(entries)
                write_atomic(path, new)
        data, text = read_source(path)
        return source_state(file, path, data, text)


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


class GitError(StoreError):
    """git is missing or the repository is unusable."""

def git(*args):
    from . import preview
    try:
        return subprocess.run(["git", "-C", str(preview.ROOT), *args], capture_output=True,
                              timeout=20)
    except FileNotFoundError as exc:
        raise GitError("History is off: git is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError("git timed out") from exc


def diff_source(file):
    """Word-level diff of a manuscript file against git HEAD."""
    path, file = source_path(file)
    text = read_source(path)[1]
    probe = git("rev-parse", "--is-inside-work-tree")
    if probe.returncode != 0:
        raise GitError("History is off: this folder is not a git repository")
    head = git("show", f"HEAD:{file}")
    if head.returncode != 0:
        err = head.stderr.decode(errors="replace")
        if any(m in err for m in ("exists on disk, but not in", "does not exist in",
                                  "unknown revision", "bad revision", "invalid object name")):
            return {"file": file, "status": "new"}  # untracked or no commits yet
        raise GitError(f"git show failed: {err.strip()}")
    old = head.stdout.decode("utf-8", errors="replace")
    if old.replace("\r\n", "\n") == text.replace("\r\n", "\n"):
        return {"file": file, "status": "clean"}
    result = preview_diff.diff(old, text)
    if not result["changed"]:
        return {"file": file, "status": "clean",
                "note": "only blank lines or plain-prose line wrapping changed"}
    return {"file": file, "status": "changed", **result}
