"""Review of the edits made to the manuscript since the author last reviewed it.

The baseline, edits.json in the review folder, keeps each manuscript file
(the .md files under the documents' folders) as the author last accepted
it. The first preview of a paper with this version takes it from the files
as they are, so nothing is pending then; a file added later starts empty, so
all of it is pending. A pending edit is a difference between a file's
baseline and its current text (trazo.edit_diff).

The preview build marks the pending edits in the page (`reader`). The
author accepts an edit (its new text goes into the baseline; the file does
not change) or rejects it (its old text goes back into the file, recorded
in file_history.json as a save, so the Source tab's "Undo save" brings the
edit back). The author's own saves from the preview (Source tab, caption
editors) are accepted as they are saved (`absorb`), without accepting the
pending edits around them. Nothing records who made an edit: an edit made
outside the preview is pending like any other.

GET /__edits?doc=NAME lists a document's pending edits (file, id, line,
old and new text, when the preview first saw it); POST /__edits {op:
accept|reject, edits: [{file, id}]} accepts or rejects some and answers how
many were done and which changed since the page was built (missed).

The preview's paths and state stay in trazo.preview, and this code reads them from
there when it runs (preview.NAME), so patching them on trazo.preview reaches it.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from . import edit_diff, preview_blocks
from .local_server import RequestError
from .review_storage import StoreError, review_lock, write_json
from .source_publish import publish

MAX_ITEMS = 10_000


def tracked() -> dict[str, Path]:
    """repo-relative name -> path of every manuscript file the preview may edit."""
    from . import preview
    found = {}
    for root in preview.SOURCE_ROOTS:
        for path in sorted((preview.ROOT / root).rglob("*.md")):
            if path.is_file() and not path.is_symlink():
                found[path.relative_to(preview.ROOT).as_posix()] = path
    return found


def exact_text(path: Path) -> str:
    """A file's text with its line endings: offsets into it stay true when
    an edit is written back, also in a file with Windows line endings."""
    return path.read_bytes().decode("utf-8")


def load() -> dict[str, Any] | None:
    """The baseline store, or None before the first one is taken."""
    from . import preview
    path = preview.EDITS_PATH
    if not path.exists():
        return None
    try:
        store = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise StoreError(f"{path} is malformed ({exc}); fix or remove it") from exc
    if not isinstance(store, dict) or not isinstance(store.get("files"), dict):
        raise StoreError(f"{path} is malformed (expected an object with files)")
    store.setdefault("seen", {})
    return store


def save(store: dict[str, Any]) -> None:
    from . import preview
    write_json(preview.EDITS_PATH, store, durable=True)


def ensure() -> dict[str, Any]:
    """The baseline, taken from the files as they are when there is none."""
    store = load()
    if store is not None:
        return store
    with review_lock():
        store = load()
        if store is None:
            store = {"files": {name: exact_text(path) for name, path in tracked().items()},
                     "seen": {}}
            save(store)
    return store


def pending(store: dict[str, Any], name: str, text: str) -> list[edit_diff.Edit]:
    return edit_diff.edits(store["files"].get(name, ""), text, name)


def reader() -> tuple[Callable[[Path], str] | None, Callable[[], None]]:
    """(read, done) for one page build: `read` gives a file's text with the
    marks of its pending edits and of its blocks (trazo.preview_blocks);
    `done` records when each edit was first seen. An unreadable baseline leaves the page unmarked (read None)
    rather than unbuilt."""
    try:
        store = ensure()
    except StoreError as exc:
        print(f"[preview] edits not marked: {exc}", flush=True)
        return None, lambda: None
    names = {path.resolve(): name for name, path in tracked().items()}
    seen: dict[str, list[str]] = {}

    def read(path: Path) -> str:
        text = exact_text(Path(path))
        name = names.get(Path(path).resolve())
        if name is None:
            return text
        found = pending(store, name, text)
        seen[name] = [e.id for e in found]
        return edit_diff.with_marks(text, edit_diff.edit_marks(text, found) + preview_blocks.marks(text, name))

    def done() -> None:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with review_lock():
            current = load()
            if current is None:
                return
            times = current["seen"]
            changed = False
            for name, ids in seen.items():
                before = times.get(name, {})
                after = {i: before.get(i, stamp) for i in ids}
                if after != before:
                    times[name], changed = after, True
            if changed:
                save(current)
    return read, done


def page_files(doc: str) -> list[str]:
    """The tracked files a document's page is built from, in page order."""
    from . import preview
    from .build_inputs import expand_includes
    names = {path.resolve(): name for name, path in tracked().items()}
    found = (names.get(path.resolve()) for path in expand_includes(preview.PROJECT.document_files(doc)))
    return list(dict.fromkeys(name for name in found if name))


def edits_payload(doc: str | None) -> dict[str, Any]:
    from . import preview
    if doc not in preview.DOCS:
        raise RequestError(f"doc must be one of: {', '.join(preview.DOCS)}")
    store = ensure()
    files, out = tracked(), []
    for name in page_files(doc):
        if name not in files:
            continue
        times = store["seen"].get(name, {})
        for e in pending(store, name, exact_text(files[name])):
            out.append({"file": name, "id": e.id, "line": e.line, "old": e.old, "new": e.new,
                        "seen": times.get(e.id, "")})
    return {"doc": doc, "edits": out}


def _items(request: Any) -> tuple[str, dict[str, list[str]]]:
    if not isinstance(request, dict) or request.get("op") not in ("accept", "reject"):
        raise RequestError("op must be accept or reject")
    items = request.get("edits")
    if not isinstance(items, list) or not items or len(items) > MAX_ITEMS:
        raise RequestError("edits must be a non-empty list of {file, id}")
    by_file: dict[str, list[str]] = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("file"), str) or not isinstance(item.get("id"), str):
            raise RequestError("each edit must be {file, id}")
        ids = by_file.setdefault(item["file"], [])
        if item["id"] not in ids:
            ids.append(item["id"])
    return request["op"], by_file


def change_edits(request: Any) -> dict[str, Any]:
    """Accept or reject edits; those that changed since the page was built
    are left as they are and listed as missed ({file, id}), as are those of
    a file another program wrote while their rejection was being published
    (source_publish keeps that version and the rejected text)."""
    from . import preview
    from .preview_sources import read_file_history, read_source, save_file_history, sha256_of, source_path
    op, by_file = _items(request)
    paths = {name: source_path(name) for name in by_file}  # validates every name first
    ensure()
    done, missed, warnings = 0, [], []
    with review_lock():
        store = load()
        if store is None:
            raise StoreError("the edits baseline disappeared; reload the page")
        history = None
        for asked, ids in by_file.items():
            path, name = paths[asked]
            data, text = read_source(path)
            found = {e.id: e for e in pending(store, name, text)}
            chosen = [found[i] for i in ids if i in found]
            missed += [{"file": asked, "id": i} for i in ids if i not in found]
            if not chosen:
                continue
            done += len(chosen)
            if op == "accept":
                store["files"][name] = edit_diff.accept(store["files"].get(name, ""), chosen)
                continue
            new = edit_diff.reject(text, chosen).encode("utf-8")
            # Recorded before publishing, as a save is: a damaged history stops the
            # rejection first, and the text it replaces is in history if trazo dies.
            history = read_file_history() if history is None else history
            history.append({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "at": time.time_ns(), "file": name, "kind": "reject",
                            "before_text": text, "before_sha": sha256_of(data), "after_sha": sha256_of(new),
                            "pending": True})
            save_file_history(history)
            result = publish(path, new, data, name)
            warnings += [result.warning] if result.warning else []
            if not result.published:
                history.pop()
                save_file_history(history)
                done -= len(chosen)
                missed += [{"file": asked, "id": e.id} for e in chosen]
        if op == "accept" and done:
            save(store)
    if op == "accept" and done:
        names = {paths[asked][1] for asked in by_file}
        rebuild = {doc for doc in preview.DOCS if names & set(page_files(doc))}
        with preview.LOCK:
            cast(set[str], preview.WATCHER["rebuild"]).update(rebuild)
    return {"done": done, "missing": len(missed), "missed": missed,
            **({"warning": " ".join(warnings)} if warnings else {})}


def absorb(name: str, before: str, after: str) -> None:
    """Accept the author's own change of a file from `before` to `after`,
    leaving the pending edits it did not touch pending. The caller holds
    review_lock and has published `after`, so a page built since then may
    mark the change as pending: its documents are rebuilt."""
    from . import preview
    store = load()
    if store is None:
        return  # the first baseline is taken from the files as they are
    merged = edit_diff.merge(before, store["files"].get(name, ""), after)
    if merged == store["files"].get(name):
        return  # the marks stay what any build of the file shows
    store["files"][name] = merged
    save(store)
    # The source does not change again, so the watcher would not build the page again.
    rebuild = {doc for doc in preview.DOCS if name in page_files(doc)}
    with preview.LOCK:
        cast(set[str], preview.WATCHER["rebuild"]).update(rebuild)


def check_outputs() -> None:
    """Note spec tables whose Markdown output is not what their spec makes
    (edited by hand, or not rendered since the spec or data changed): the
    next table render replaces it. Projects with their own tables stage are
    not checked.
    The status shows them as `outputs`."""
    from . import preview, table
    from .preview_watch import own_tables_stage
    from .review_storage import holds_text
    notes = []
    if not own_tables_stage(preview.PROJECT):
        for label in table.all_labels():
            try:
                rendered = table.build(label)  # an invalid spec or data: in rendered.errors
            except OSError:  # unreadable: the page build (spec) or the next table render (data) reports it
                continue
            path = table.output_path(label)
            # A hand edit re-saved in another encoding counts as edited by hand too.
            if rendered.markdown is not None and path.is_file() and not holds_text(path, rendered.markdown):
                notes.append(path.relative_to(preview.ROOT).as_posix())
    with preview.LOCK:
        preview.WATCHER["outputs"] = notes
