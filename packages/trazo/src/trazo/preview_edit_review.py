"""Review of the edits made to the manuscript since the author last reviewed it.

The baseline, edits.json in the review folder, keeps each manuscript file
(the .md files under the documents' folders) as the author last accepted
it. The first preview of a paper with this version takes it from the files
as they are, so nothing is pending then; a file added later starts empty, so
all of it is pending. A pending edit is a difference between a file's
baseline and its current text (trazo.edit_diff), except in figures, tables
and display equations: the baseline takes those as they are when a page is
built (trazo.edit_blocks), so they are never pending.

The preview build marks the pending edits in the page (`reader`). The
author accepts an edit (its new text goes into the baseline; the file does
not change; the last accept can be undone, which puts back the baseline
from before it) or rejects it (its old text goes back into the file, recorded
in file_history.json as a save, so the Source tab's "Undo save" brings the
edit back). The author's own saves from the preview (Source tab, caption
editors) are accepted as they are saved (`absorb`), without accepting the
pending edits around them. Nothing records who made an edit: an edit made
outside the preview is pending like any other.

GET /__edits?doc=NAME lists a document's pending edits (file, id, line,
old and new text, when the preview first saw it); POST /__edits {op:
accept|reject, edits: [{file, id}]} accepts or rejects some and answers how
many were done and which changed since the page was built (missed), and an
accept its `accept_id`; POST /__edits {op: undo, expect?: accept_id} undoes
the last accept (409 when `expect` names an older one) and answers how many
edits are pending again (undone).

The preview's paths and state stay in trazo.preview, and this code reads them from
there when it runs (preview.NAME), so patching them on trazo.preview reaches it.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from . import edit_blocks, edit_diff, preview_blocks
from .local_server import Conflict, RequestError
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


def baseline(store: dict[str, Any], name: str, text: str) -> str:
    return edit_blocks.take_blocks(store["files"].get(name, ""), text)


def pending(store: dict[str, Any], name: str, text: str) -> list[edit_diff.Edit]:
    return edit_diff.edits(baseline(store, name, text), text, name)


def reader() -> tuple[Callable[[Path], str] | None, Callable[[], None]]:
    """(read, done) for one page build: `read` gives a file's text with the
    marks of its pending edits and of its blocks (trazo.preview_blocks);
    `done` records when each edit was first seen and puts the figures,
    tables and equations of the files read into the baseline. An unreadable
    baseline leaves the page unmarked (read None) rather than unbuilt."""
    try:
        store = ensure()
    except StoreError as exc:
        print(f"[preview] edits not marked: {exc}", flush=True)
        return None, lambda: None
    names = {path.resolve(): name for name, path in tracked().items()}
    seen: dict[str, list[str]] = {}
    with_blocks: dict[str, str] = {}

    def read(path: Path) -> str:
        text = exact_text(Path(path))
        name = names.get(Path(path).resolve())
        if name is None:
            return text
        base = baseline(store, name, text)
        if base != store["files"].get(name, ""):
            with_blocks[name] = base
        found = edit_diff.edits(base, text, name)
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
            for name, base in with_blocks.items():
                if current["files"].get(name, "") == store["files"].get(name, ""):
                    current["files"][name], changed = base, True
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
        raise RequestError("op must be accept, reject or undo")
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
    """Accept or reject edits, or undo the last accept; those that changed
    since the page was built are left as they are and listed as missed
    ({file, id}), as are those of a file another program wrote while their
    rejection was being published (source_publish keeps that version and the
    rejected text). An accept keeps the baselines it replaced for its undo."""
    from .preview_sources import read_file_history, read_source, save_file_history, sha256_of, source_path
    if isinstance(request, dict) and request.get("op") == "undo":
        return undo_accept(request)
    op, by_file = _items(request)
    paths = {name: source_path(name) for name in by_file}  # validates every name first
    ensure()
    done, missed, warnings = 0, [], []
    undo: dict[str, Any] = {"at": time.time_ns(), "time": time.strftime("%Y-%m-%d %H:%M:%S"), "files": {}, "seen": {}}
    with review_lock():
        store = load()
        if store is None:
            raise StoreError("the edits baseline disappeared; reload the page")
        history = None
        for asked, ids in by_file.items():
            path, name = paths[asked]
            data, text = read_source(path)
            base = baseline(store, name, text)
            found = {e.id: e for e in edit_diff.edits(base, text, name)}
            chosen = [found[i] for i in ids if i in found]
            missed += [{"file": asked, "id": i} for i in ids if i not in found]
            if not chosen:
                continue
            done += len(chosen)
            if op == "accept":
                after = edit_diff.accept(base, chosen)
                undo["files"][name] = [store["files"].get(name, ""), after]
                undo["seen"][name] = store["seen"].get(name, {})
                store["files"][name] = after
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
            store["accepted"] = {**undo, "count": done}
            save(store)
    if op == "accept" and done:
        _rebuild_pages({paths[asked][1] for asked in by_file})
    return {"done": done, "missing": len(missed), "missed": missed,
            **({"accept_id": str(undo["at"])} if op == "accept" and done else {}),
            **({"warning": " ".join(warnings)} if warnings else {})}


def undo_accept(request: dict[str, Any]) -> dict[str, Any]:
    with review_lock():
        store = load()
        last = store.get("accepted") if store else None
        if store is None or not isinstance(last, dict) or not isinstance(last.get("files"), dict):
            raise RequestError("nothing to undo")
        if "expect" in request and str(last.get("at")) != str(request["expect"]):
            raise Conflict("the edits were accepted again since; undo that accept first")
        for name, (before, after) in last["files"].items():
            store["files"][name] = _unaccepted(store["files"].get(name, ""), before, after)
            store["seen"][name] = {**last.get("seen", {}).get(name, {}), **store["seen"].get(name, {})}
        del store["accepted"]
        save(store)
    _rebuild_pages(set(last["files"]))
    return {"undone": last.get("count", 0)}


def _unaccepted(now: str, before: str, after: str) -> str:
    return before if now == after else edit_diff.merge(after, now, before)


def absorb(name: str, before: str, after: str) -> None:
    """Accept the author's own change of a file from `before` to `after`,
    leaving the pending edits it did not touch pending. The caller holds
    review_lock and has published `after`, so a page built since then may
    mark the change as pending: its documents are rebuilt."""
    store = load()
    if store is None:
        return  # the first baseline is taken from the files as they are
    reviewed = store["files"].get(name, "")
    merged = edit_diff.merge(before, reviewed, after)
    if merged == reviewed:
        return  # the marks stay what any build of the file shows
    store["files"][name] = merged
    save(store)
    _rebuild_pages({name})  # the file does not change again, so no pass would rebuild its pages


def _rebuild_pages(names: set[str]) -> None:
    """Have the watcher rebuild the pages built from the files `names`,
    whose baseline changed although the files did not."""
    from . import preview
    try:
        rebuild = {doc for doc in preview.DOCS if names & set(page_files(doc))}
    except (OSError, ValueError):  # an unreadable file: its page shows the error, and a published save goes on
        rebuild = set(preview.DOCS)
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
