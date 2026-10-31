"""Diagram editing routes in the shared trazo workspace.

server.py mounts /diagrams and everything under /__diagrams/ here; the
request guards, anti-framing headers, JSON errors, review lock and atomic
writes are local_server's. Errors are JSON
{"error": ...}: 400 bad request, 404 unknown diagram, comment or path, 409
conflict, 422 invalid diagram, 503 busy, 500 server error. `get_route` and
`post_route` return the function that answers a request (None when the path
is not a diagram path); `shutdown` stops the export and the shared Chrome.

GET  /diagrams                      the editor (trazo's assets/diagram/editor.html;
                                    the diagram name is the URL hash, /diagrams#NAME)
GET  /__diagrams/tokens             figures/style/tokens.json
GET  /__diagrams/values             {values: {key: text}, error} from data/values.json
GET  /__diagrams/list               [{name, file, sha256, mtime_ns, bytes}]
GET  /__diagrams/file/NAME          {name, file, text, sha256, mtime_ns,
                                    diagram (null if not JSON), parse_error}
POST /__diagrams/file/NAME          save {diagram, base_sha256, autosave?, client?,
                                    label?}; base_sha256 null creates a new file.
                                    409 {error, sha256, text} when the file changed
                                    since base_sha256; 422 {error, issues} when the
                                    schema is invalid (nothing written). Same
                                    validation and history as `trazo diagram patch`.
GET  /__diagrams/payload/NAME       renderer input {name, sha256, payload: {diagram,
                                    tokens, values, embeds, images} or null, issues}
GET  /__diagrams/check/NAME[?exports=1]
                                    the `trazo diagram check NAME --json` result
                                    (exports=1 adds the stale-export check)
POST /__diagrams/export/NAME        start `trazo diagram export NAME` ({force?});
                                    one at a time, 409 while one runs
POST /__diagrams/cancel-export      stop the running export
GET  /__diagrams/export             export status {state: idle|running|ok|error,
                                    name, started, finished, exit_code, messages}
GET  /__diagrams/status[?name=NAME] polling: {diagrams: {name: {sha256, mtime_ns}},
                                    comments, history (mtime_ns), export, and with
                                    name: assets {path: mtime_ns | null}}
GET  /__diagrams/embeds             figures/output/svg/*.svg [{path, bytes,
                                    mtime_ns, width, height}] for the embed picker
GET  /__diagrams/comments[?diagram=N]
                                    {comments, undo (label of the last comment
                                    change of that diagram)}
POST /__diagrams/comments           {op: add|update|reply|delete|restore|undo, ...}
GET  /__assets/diagram/...          the editor's files; figures/fonts/...,
                                    figures/output/svg/..., figures/external/... and
                                    image files under figures/ are read-only
                                    (the preview serves figures/; see `static_file`)

Comments live in notes/review/diagram_comments.json with the preview
states: open (author), review (the agent replied with `trazo diagram
resolve`), done (only the author marks it); delete moves a comment to the
trash and restore brings it back. A comment has a target element id or a
point [x, y] in mm (or neither, for the whole diagram).

History: every save and comment change is an entry in
notes/review/diagram_history.json, so `trazo diagram undo` reverts it (and
refuses if the file or comment changed since). Autosaves are coalesced:
an autosave from the same client to the same diagram updates the previous
autosave entry instead of adding one, when that entry is the last in the
history, the file still has the content it wrote, the previous autosave
was less than COALESCE_SECONDS (20 s) ago and the entry spans less than
COALESCE_SPAN (5 min). Explicit saves (autosave false) always add an entry.

Check reuses one long-lived headless Chrome (started on first use, closed
after CHROME_IDLE seconds unused or when it fails). A check waits at most
CHECK_WAIT seconds for it (503 otherwise) and each Chrome call times out
after DIAGRAM_CHECK_TIMEOUT seconds (default 20), so other requests never
wait on Chrome. Export runs `trazo diagram export` in a subprocess, stopped
after DIAGRAM_EXPORT_TIMEOUT seconds (default 300). Writes go only to
figures/diagrams/NAME.json (NAME: lowercase letters, digits, - and _; no
symbolic links) and, through export, to figures/external/NAME.{svg,pdf,png}.
PREVIEW_REVIEW_DIR moves the review folder, as for preview.py.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from . import diagram as dg
from .background import CommandJob
from .dependencies import file_digest, render_revision
from .expand import ValuesError, load_values
from .local_server import Conflict, NotFound, RequestError, Unprocessable
from .project import ASSETS_DIR, PROJECT
from .review_comments import CommentStore
from .review_storage import review_lock

ROOT = PROJECT.root

CHECK_TIMEOUT = float(os.environ.get("DIAGRAM_CHECK_TIMEOUT") or 20)  # per Chrome call
EXPORT_TIMEOUT = float(os.environ.get("DIAGRAM_EXPORT_TIMEOUT") or 300)
CHECK_WAIT = 30.0
CHROME_IDLE = 300.0
COALESCE_SECONDS = 20.0
COALESCE_SPAN = 300.0
EMBED_DIR = PROJECT.path("figure_embeds")
SERVED_PREFIXES = ("figures/fonts/", "figures/output/svg/", "figures/external/")
PREFIX = "/__diagrams/"
ASSET_URL = "__assets/diagram/"
DIAGRAM_ASSETS = ASSETS_DIR / "diagram"
EDITOR_PAGE = DIAGRAM_ASSETS / "editor.html"


class Busy(RequestError):
    """The shared Chrome is in use for too long (HTTP 503)."""

    status = 503


# ---------------------------------------------------------------- diagrams


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


def diagram_paths() -> list[Path]:
    return [p for p in dg.all_diagrams() if dg.NAME_RE.match(p.stem) and not p.is_symlink()]


def list_diagrams() -> list[dict[str, Any]]:
    return [file_state(p) for p in diagram_paths()]


def get_diagram(name: str) -> dict[str, Any]:
    path = diagram_file(name)
    data = path.read_bytes()
    state = file_state(path)
    state.pop("bytes")
    text = data.decode("utf-8", "replace")
    try:
        parsed: Any | None = json.loads(text)
        error = None
    except ValueError as exc:
        parsed, error = None, str(exc)
    return {**state, "text": text, "diagram": parsed, "parse_error": error}


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
        recorded = None
        if data != current:
            dg.write_atomic(path, data)
            recorded = record_save(path, current, data, autosave, client, label[:200])
    return {**file_state(path), "changed": recorded is not None, "history": recorded}


def record_save(path: Path, before: bytes | None, data: bytes, autosave: bool,
                client: str, label: str) -> str:
    """Add or extend (coalesce) the history entry of an editor save; returns which."""
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
    dg.save_history(history)
    return outcome


def get_payload(name: str) -> dict[str, Any]:
    """What the browser renderer needs; payload is null when the schema is invalid."""
    path = diagram_file(name)
    try:
        diagram, source = dg.read_diagram(path)
    except dg.DiagramError as exc:
        return {"name": name, "sha256": None, "payload": None,
                "issues": [dg.issue("error", "load", None, str(exc))]}
    tokens = dg.load_tokens()
    issues = dg.validate(diagram, tokens)
    if issues:
        return {"name": name, "sha256": dg.sha256_bytes(source), "payload": None, "issues": issues}
    payload, problems, stamp = dg.render_payload(diagram, source, tokens)
    return {"name": name, "sha256": dg.sha256_bytes(source), "payload": payload,
            "revision": render_revision(stamp, tokens),
            "font_url": "/" + PROJECT.relative("figure_fonts").strip("/") + "/",
            "issues": problems}


def all_values() -> dict[str, Any]:
    """Every {{v:key}} value for the editor (stale inputs are not an error here)."""
    try:
        return {"values": load_values(check_inputs=False), "error": None}
    except (ValuesError, OSError, ValueError, KeyError) as exc:
        return {"values": {}, "error": str(exc)}


def svg_size(path: Path) -> tuple[str | None, str | None]:
    head = path.read_bytes()[:4096].decode("utf-8", "replace")
    root = re.search(r"<svg\b[^>]*>", head)
    if not root:
        return None, None
    found = [re.search(rf'\b{k}="([^"]*)"', root.group(0)) for k in ("width", "height")]
    return tuple(m.group(1) if m else None for m in found)  # type: ignore[return-value]


def list_embeds() -> list[dict[str, Any]]:
    items = []
    for path in sorted(EMBED_DIR.glob("*.svg")):
        if path.is_symlink() or not dg.EMBED_RE.match(dg.rel(path)):
            continue
        width, height = svg_size(path)
        items.append({"path": dg.rel(path), "bytes": path.stat().st_size,
                      "mtime_ns": path.stat().st_mtime_ns, "width": width, "height": height})
    return items


def mtime(path: Path) -> int:
    return path.stat().st_mtime_ns if path.exists() else 0


def status(name: str | None) -> dict[str, Any]:
    """Cheap polling state for live reload."""
    paths = [diagram_file(name)] if name else diagram_paths()
    payload: dict[str, Any] = {
        "diagrams": {p.stem: diagram_state(p) for p in paths if p.exists()},
        "comments": mtime(dg.COMMENTS_PATH), "history": mtime(dg.HISTORY_PATH),
        "export": export_status()}
    if name:
        assets: dict[str, int | None] = {}
        try:
            diagram, _ = dg.read_diagram(paths[0])
            panels = diagram.get("panels", []) if isinstance(diagram.get("panels"), list) else []
            nodes = diagram.get("nodes", []) if isinstance(diagram.get("nodes"), list) else []
            refs = [p.get("embed") for p in panels if isinstance(p, dict)]
            refs += [n.get("src") for n in nodes if isinstance(n, dict) and n.get("kind") == "image"]
            for ref in refs:
                if isinstance(ref, str) and ref:
                    file = ROOT / ref
                    assets[ref] = file.stat().st_mtime_ns if file.is_file() else None
        except dg.DiagramError:
            pass
        payload["assets"] = assets
    return payload


def diagram_state(path):
    state = file_state(path)
    try:
        diagram, source = dg.read_diagram(path)
        tokens = dg.load_tokens()
        if dg.validate(diagram, tokens):
            raise ValueError("invalid diagram")
        _, _, stamp = dg.render_payload(diagram, source, tokens)
        state["revision"] = render_revision(stamp, tokens)
        state["draft"] = dg.is_draft(diagram)
        state["export_state"] = dg.export_state(path, diagram, source, tokens)
    except (ValueError, TypeError, KeyError, OSError, dg.DiagramError) as exc:
        state.update(revision=state["sha256"], export_state="error", error=str(exc))
    return state


# ---------------------------------------------------------------- check (shared Chrome)


class SharedChrome:
    """One headless Chrome reused by checks; restarted after a failure, closed when idle."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.chrome: dg.Chrome | None = None
        self.last_used = 0.0

    def drop(self) -> None:
        if self.chrome is not None:
            try:
                self.chrome.close()
            except Exception:
                pass
            self.chrome = None

    def check(self, name: str, exports: bool) -> dict[str, Any]:
        path = diagram_file(name)
        try:
            dg.read_diagram(path)  # load errors need no Chrome
        except dg.DiagramError as exc:
            result = dg.Rendered(path)
            result.issues = [dg.issue("error", "load", None, str(exc))]
            return dg.check_payload(result)
        if not self.lock.acquire(timeout=CHECK_WAIT):
            raise Busy("the checker is busy; try again")
        try:
            if self.chrome is None or self.chrome.proc.poll() is not None:
                self.drop()
                self.chrome = dg.Chrome(timeout=CHECK_TIMEOUT)
            try:
                result = dg.process(path, self.chrome)
            except dg.DiagramError as exc:  # Chrome failed or timed out: start afresh next time
                self.drop()
                result = dg.Rendered(path)
                result.issues = [dg.issue("error", "load", None, str(exc))]
            except Exception:
                self.drop()
                raise
            if exports and result.stamp:
                result.issues += dg.stale_exports(result)
            return {**dg.check_payload(result), "revision": result.revision}
        finally:
            self.last_used = time.monotonic()
            self.lock.release()

    def reap(self, stop_event=None) -> None:
        """Close Chrome after CHROME_IDLE seconds without a check (runs forever)."""
        while True:
            if stop_event is None:
                time.sleep(15)
            elif stop_event.wait(15):
                return
            if self.lock.acquire(blocking=False):
                try:
                    if self.chrome is not None and time.monotonic() - self.last_used > CHROME_IDLE:
                        self.drop()
                finally:
                    self.lock.release()


CHECKER = SharedChrome()


# ---------------------------------------------------------------- export


EXPORT_JOB = CommandJob("export", "messages", 200, name=None)


def export_status() -> dict[str, Any]:
    return EXPORT_JOB.status()


def start_export(name: str, request: Any) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise RequestError("request body must be a JSON object")
    force = request.get("force", False)
    if not isinstance(force, bool):
        raise RequestError("force must be a boolean")
    diagram_file(name)
    command = [sys.executable, "-m", "trazo", "diagram", "export", name]
    if force:
        command.append("--force")
    if not EXPORT_JOB.start(command, ROOT, EXPORT_TIMEOUT, name=name):
        raise Conflict(f"an export is already running ({export_status()['name']})")
    return export_status()


def cancel_export(request: Any) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise RequestError("request body must be a JSON object")
    if not EXPORT_JOB.stop("cancelled"):
        raise Conflict("no export is running")
    return export_status()


# ---------------------------------------------------------------- comments


def comment_targets(request: dict[str, Any]) -> dict[str, Any]:
    """The diagram and the optional target id or point of a new comment."""
    name = request.get("diagram")
    diagram_file(name)
    target = request.get("target")
    if target is not None and (not isinstance(target, str) or not dg.ID_RE.match(target)):
        raise RequestError("target must be an element id")
    point = request.get("point")
    if point is not None and not (isinstance(point, list) and len(point) == 2
                                  and all(dg.is_number(v) for v in point)):
        raise RequestError("point must be [x, y] in mm")
    fields: dict[str, Any] = {"diagram": name}
    if target is not None:
        fields["target"] = target
    if point is not None:
        fields["point"] = point
    return fields


COMMENTS = CommentStore(dg.COMMENTS_PATH, "diagram", dg.HISTORY, comment_targets)


def comments_payload(name: str | None) -> dict[str, Any]:
    if name is not None:
        diagram_file(name, must_exist=False)
    return COMMENTS.payload(name)


def change_comments(request: Any) -> dict[str, Any]:
    """Apply one comment change and record it in the diagram history.

    Ops (as in the preview): add {diagram, comment, target?, point?},
    update {id, status?, comment?}, reply {id, text, by?, status?},
    delete {id}, restore {id} and undo {diagram?} (reverts the last comment
    change of that diagram, refused with 409 if the comment changed since).
    See trazo.review_comments.
    """
    return COMMENTS.change(request)


# ---------------------------------------------------------------- HTTP


def static_file(rel_path: str) -> Path | None:
    """A served read-only file for a decoded URL path, or None."""
    from .server import static_file as resolve
    # Keep the old helper's public subset; containment is owned by the host.
    if not (rel_path.startswith((ASSET_URL, *SERVED_PREFIXES)) or dg.IMAGE_RE.match(rel_path)):
        return None
    return resolve("/" + rel_path)


Route = Callable[[], Any]


def unknown() -> Any:
    raise NotFound("not found")


def split_name(route: str) -> tuple[str, str | None]:
    """`kind/NAME` -> (kind, NAME percent-decoded; validated by diagram_file)."""
    kind, slash, name = route.partition("/")
    return kind, unquote(name) if slash else None


def get_route(path: str, query: dict[str, list[str]]) -> Route | None:
    """The function answering GET `path` (query: parse_qs), or None if not a diagram path."""
    if not path.startswith(PREFIX):
        return None
    route = path[len(PREFIX):]
    first = lambda key: query.get(key, [None])[0]  # noqa: E731
    fixed: dict[str, Route] = {
        "tokens": dg.load_tokens, "values": all_values, "list": list_diagrams,
        "status": lambda: status(first("name")), "embeds": list_embeds,
        "export": export_status, "comments": lambda: comments_payload(first("diagram"))}
    if route in fixed:
        return fixed[route]
    kind, name = split_name(route)
    if name is not None:
        if kind == "file":
            return lambda: get_diagram(name)
        if kind == "payload":
            return lambda: get_payload(name)
        if kind == "check":
            return lambda: CHECKER.check(name, first("exports") in ("1", "true"))
    return unknown


def post_route(path: str, body: Callable[[], Any]) -> Route | None:
    """The function answering POST `path`, or None if not a diagram path.

    `body` returns the parsed JSON body; it is called only by routes that need it.
    """
    if not path.startswith(PREFIX):
        return None
    route = path[len(PREFIX):]
    if route == "cancel-export":
        return lambda: cancel_export(body())
    if route == "comments":
        return lambda: change_comments(body())
    kind, name = split_name(route)
    if name is not None:
        if kind == "file":
            return lambda: save_diagram(name, body())
        if kind == "export":
            return lambda: start_export(name, body())
    return unknown


def shutdown() -> None:
    """Stop a running export and close the shared Chrome (at server shutdown)."""
    EXPORT_JOB.stop("cancelled")
    if CHECKER.lock.acquire(timeout=5):
        try:
            CHECKER.drop()
        finally:
            CHECKER.lock.release()
