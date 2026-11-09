"""The diagram editor's save: POST /__diagrams/file/NAME (see trazo.diagram_editor).

A save writes figures/diagrams/NAME.json only if the file still has the
base_sha256 the editor loaded, and records it for undo; autosaves from one
client are coalesced (COALESCE_SECONDS, COALESCE_SPAN). A program that writes
the file during the save keeps its version and the save answers 409, as when
the file changed before it (review_history.History.publish). The reply's
sha256 is that of the bytes written; `warning` reports trouble after publishing.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from . import diagram as dg
from .dependencies import file_digest
from .local_server import Conflict, NotFound, RequestError, Unprocessable
from .review_storage import review_lock

COALESCE_SECONDS = 20.0
COALESCE_SPAN = 300.0


def diagram_file(name: Any, must_exist: bool = True) -> Path:
    """figures/diagrams/NAME.json for a valid NAME; never a symbolic link."""
    if not isinstance(name, str) or not dg.NAME_RE.match(name):
        raise RequestError(f"invalid diagram name {name!r} "
                           "(use lowercase letters, digits, - and _)")
    path = dg.DIAGRAM_DIR / f"{name}.json"
    if path.is_symlink():
        raise RequestError("symbolic links are not allowed")
    if must_exist and not path.is_file():
        raise NotFound(f"no diagram named {name}")
    return path


def file_state(path: Path) -> dict[str, Any]:
    info = path.stat()
    return {"name": path.stem, "file": dg.rel(path), "sha256": file_digest(path),
            "mtime_ns": info.st_mtime_ns, "bytes": info.st_size}


def save_diagram(name: str, request: Any) -> dict[str, Any]:
    """Write a diagram if the file still has base_sha256; record history (see docstring)."""
    if not isinstance(request, dict):
        raise RequestError("request body must be a JSON object")
    diagram = request.get("diagram")
    if not isinstance(diagram, dict):
        raise RequestError("diagram must be a JSON object")
    base = request.get("base_sha256")
    if base is not None and not isinstance(base, str):
        raise RequestError("base_sha256 must be a string or null")
    autosave = request.get("autosave", False)
    client = request.get("client", "")
    label = request.get("label", "")
    if not isinstance(autosave, bool) or not isinstance(client, str) or not isinstance(label, str):
        raise RequestError("autosave must be a boolean; client and label strings")
    issues = dg.validate(diagram, dg.load_tokens())
    if issues:
        raise Unprocessable("not a valid diagram; nothing was written", issues=issues)
    data = dg.dump_diagram(diagram)
    with review_lock():
        path = diagram_file(name, must_exist=False)
        current = path.read_bytes() if path.exists() else None
        current_sha = dg.sha256_bytes(current) if current is not None else None
        if base != current_sha:
            why = ("the diagram no longer exists" if current is None
                   else "a diagram with this name already exists" if base is None
                   else "the diagram changed on disk since it was loaded")
            raise Conflict(why,
                           sha256=current_sha,
                           text=current.decode("utf-8", "replace") if current is not None else None)
        recorded, warning = None, ""
        if data != current:
            try:
                recorded, warning = record_save(path, current, data, autosave, client, label[:200])
            except dg.DiagramError as exc:  # with `extra`: another program wrote the file meanwhile
                if not hasattr(exc, "extra"):
                    raise
                raise Conflict(str(exc), **exc.extra) from exc
    # What this save wrote, not a re-read: the editor keeps this sha256 as its base,
    # so a write landing right after the save must make its next save a 409.
    return {**file_state(path), "sha256": dg.sha256_bytes(data), "changed": recorded is not None,
            "history": recorded, **({"warning": warning} if warning else {})}


def record_save(path: Path, before: bytes | None, data: bytes, autosave: bool,
                client: str, label: str) -> tuple[str, str]:
    """Publish an editor save with undo; return its grouping and any warning."""
    history = dg.load_history()
    now = time.time()
    file = dg.rel(path)
    last = history[-1] if history else None
    before_sha = dg.sha256_bytes(before) if before is not None else None
    if (autosave and last is not None and last.get("kind") == "file"
            and last.get("file") == file and last.get("source") == "editor"
            and last.get("autosave") is True and last.get("client") == client
            and last.get("after_sha") == before_sha
            and now - float(last.get("epoch", 0)) <= COALESCE_SECONDS
            and now - float(last.get("started", 0)) <= COALESCE_SPAN):
        saves = int(last.get("saves", 1)) + 1
        last.update(after_sha=dg.sha256_bytes(data), epoch=now, time=dg.now(), saves=saves,
                    label=f"editor autosave {path.stem} ({saves} saves)")
        outcome = "merged"
    else:
        history.append({
            "time": dg.now(), "kind": "file", "file": file,
            "label": label or f"editor {'autosave' if autosave else 'save'} {path.stem}",
            "before": before.decode("utf-8") if before is not None else None,
            "after_sha": dg.sha256_bytes(data), "source": "editor", "autosave": autosave,
            "client": client, "started": now, "epoch": now, "saves": 1})
        outcome = "new"
    # `before` is what the caller read under the lock; a merged entry's "before" is older.
    warning = dg.HISTORY.publish(path, data, history[-1], before, replace_last=outcome == "merged")
    return outcome, warning
