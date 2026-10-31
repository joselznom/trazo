"""Table editing routes in the shared trazo workspace.

server.py mounts these paths; the guards, anti-framing headers and
JSON errors are local_server's (400 bad request, 404 unknown table or
comment, 409 conflict, 422 invalid result, 500 server error). Every change
goes through trazo.table (the same functions, validation and history as
`trazo table patch` and `undo`), and a saved table is rendered at once.

GET  /tables                       the editor page (assets/table/editor.html;
                                   the table label is the URL hash, /tables#LABEL)
GET  /__tables                     [{label, title, data, status (ok|stale|error),
                                     open_comments, kind: "spec"}]
GET  /__table?label=L[&hidden=1]   {label, file, sha256, spec, shared: {formats, maps,
                                     sha256}, data: {file, sha256, fields: {name: {types,
                                     values}}, values, records (first 50), count},
                                     model, issues, format_usage, undo}
                                   (hidden=1: hidden leaves in the model, flagged)
POST /__table/preview              {label, spec, hidden?} -> {model, issues}; nothing
                                     is written (live typing)
POST /__table                      {label, base_sha256, ops, hidden?} -> the new state,
                                     as GET. 409 {error, sha256} when the spec changed
                                     since base_sha256; 422 {error, issues} when the
                                     result has errors (nothing written)
POST /__table/shared               {base_sha256, ops, label?, hidden?} set ops on
                                     formats.* and maps.* of _shared.json, then every
                                     table is rendered; 422 when a clean table would get
                                     errors. Returns the state of `label` (or {}).
POST /__table/undo                 {label, hidden?} reverts this table's last spec (or
                                     its shared) change; 409 when the file changed since
GET  /__table-comments?label=L     {comments, undo}
POST /__table-comments             {op: add|update|reply|delete|restore|undo, ...}
                                     (trazo.review_comments; add takes {table,
                                     comment, target?})
GET  /__table-status               {tables: {label: sha256}, shared: sha256,
                                     data: {name: sha256}, comments: mtime_ns} for polling
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import table as tb
from .local_server import Conflict, NotFound, RequestError, Unprocessable
from .project import ASSETS_DIR

EDITOR_PAGE = ASSETS_DIR / "table" / "editor.html"
RECORDS_SHOWN = 50
VALUES_SHOWN = 200


def spec_file(label: Any) -> str:
    """A valid label with an existing spec."""
    if not isinstance(label, str) or not tb.LABEL_RE.match(label):
        raise RequestError("label must look like 'summary' or 'tab1_results'")
    if not tb.spec_path(label).is_file():
        raise NotFound(f"no table spec named {label}")
    return label


def errors_to_http(run: Callable[[], Any]) -> Any:
    """Map trazo.table errors to HTTP errors."""
    try:
        return run()
    except tb.SpecChanged as exc:
        raise Conflict(str(exc), sha256=exc.sha256) from exc
    except tb.SpecInvalid as exc:
        raise Unprocessable(str(exc), issues=exc.issues) from exc
    except tb.TableConflict as exc:
        raise Conflict(str(exc)) from exc
    except tb.TableError as exc:
        raise RequestError(str(exc)) from exc


def open_comments() -> dict[str, int]:
    counts: dict[str, int] = {}
    for c in tb.COMMENTS.load():
        if c.get("status") == "open":
            counts[c.get("table")] = counts.get(c.get("table"), 0) + 1
    return counts


def list_tables() -> list[dict[str, Any]]:
    counts = open_comments()
    out = []
    for label in tb.all_labels():
        table = tb.build(label)
        spec = table.spec if isinstance(table.spec, dict) else {}
        out.append({"label": label, "title": spec.get("title", ""), "data": spec.get("data"),
                    "status": tb.status_of(table), "open_comments": counts.get(label, 0),
                    "kind": "spec"})
    return out


def data_payload(table: tb.Table) -> dict[str, Any]:
    fields = {}
    for name, values in table.fields.items():
        types = sorted({"null" if v is None else "string" if isinstance(v, str) else
                        "int" if isinstance(v, int) else "float" if tb.is_number(v) else
                        "composite" for v in values})
        distinct = list(dict.fromkeys(v for v in values if isinstance(v, str)))
        keys = list(dict.fromkeys(k for v in values if isinstance(v, dict) for k in v))
        fields[name] = {"types": types, "values": distinct[:VALUES_SHOWN], "keys": keys}
    records = table.data.get("records", []) if isinstance(table.data, dict) else []
    name = table.spec.get("data") if isinstance(table.spec, dict) else None
    return {"file": tb.rel(tb.data_path(name)) if isinstance(name, str) else None,
            "sha256": table.data_sha256,
            "fields": fields, "values": table.data.get("values", {}) if table.fields else {},
            "records": records[:RECORDS_SHOWN], "count": len(records)}


def next_undo(label: str) -> str | None:
    spec, shared = tb.rel(tb.spec_path(label)), tb.rel(tb.SHARED_PATH)
    mine = [e for e in tb.HISTORY.load() if e.get("kind") == "file" and (
        e.get("file") == spec or (e.get("file") == shared and e.get("table") == label))]
    return mine[-1]["label"] if mine else None


def table_state(label: Any, hidden: bool = False) -> dict[str, Any]:
    label = spec_file(label)
    path = tb.spec_path(label)
    table = tb.build(label)
    shared = tb.load_shared() if tb.SHARED_PATH.is_file() else {}
    return {"label": label, "file": tb.rel(path), "sha256": tb.sha256_of(path),
            "spec": table.spec,
            "shared": {"formats": shared.get("formats", {}), "maps": shared.get("maps", {}),
                       "sha256": tb.sha256_of(tb.SHARED_PATH), "file": tb.rel(tb.SHARED_PATH)},
            "data": data_payload(table), "model": tb.render_model(table, hidden),
            "issues": table.issues, "format_usage": tb.format_usage(),
            "undo": next_undo(label)}


def get_table(query: dict[str, list[str]]) -> dict[str, Any]:
    return errors_to_http(lambda: table_state(query.get("label", [None])[0],
                                              query.get("hidden", ["0"])[0] == "1"))


def body(request: Any) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise RequestError("request body must be a JSON object")
    return request


def preview_spec(request: Any) -> dict[str, Any]:
    request = body(request)
    label = spec_file(request.get("label"))
    spec = request.get("spec")
    if not isinstance(spec, dict):
        raise RequestError("spec must be a JSON object")
    table = tb.build(label, spec)
    return {"model": tb.render_model(table, bool(request.get("hidden"))), "issues": table.issues}


def save_table(request: Any) -> dict[str, Any]:
    request = body(request)
    label = spec_file(request.get("label"))
    if not isinstance(request.get("base_sha256"), str):
        raise RequestError("base_sha256 is required")
    errors_to_http(lambda: tb.patch(label, request.get("ops"), request["base_sha256"]))
    return table_state(label, bool(request.get("hidden")))


def save_shared(request: Any) -> dict[str, Any]:
    request = body(request)
    label = request.get("label")
    if label is not None:
        spec_file(label)
    base = request.get("base_sha256")
    if base is not None and not isinstance(base, str):
        raise RequestError("base_sha256 must be a string or null")
    if base is None and tb.SHARED_PATH.is_file():
        raise RequestError("base_sha256 is required when _shared.json exists")
    errors_to_http(lambda: tb.patch_shared(request.get("ops"), base, label))
    return table_state(label, bool(request.get("hidden"))) if label else {}


def undo_table(request: Any) -> dict[str, Any]:
    request = body(request)
    label = spec_file(request.get("label"))
    message = errors_to_http(lambda: tb.undo(label))
    if message == "nothing to undo":
        raise RequestError("nothing to undo")
    return {**table_state(label, bool(request.get("hidden"))), "undone": message}


def get_comments(query: dict[str, list[str]]) -> dict[str, Any]:
    label = query.get("label", [None])[0]
    if label is not None:
        spec_file(label)
    return tb.COMMENTS.payload(label)


def change_comments(request: Any) -> dict[str, Any]:
    return tb.COMMENTS.change(request)


def status() -> dict[str, Any]:
    comments = tb.COMMENTS_PATH.stat().st_mtime_ns if tb.COMMENTS_PATH.exists() else None
    return {"tables": {label: tb.sha256_of(tb.spec_path(label)) for label in tb.all_labels()},
            "data": {path.stem: tb.sha256_of(path) for path in sorted(tb.DATA_DIR.glob("*.json"))},
            "shared": tb.sha256_of(tb.SHARED_PATH), "comments": comments}


GET = {"/__tables": lambda q: list_tables(), "/__table": get_table,
       "/__table-comments": get_comments, "/__table-status": lambda q: status()}
POST = {"/__table": save_table, "/__table/preview": preview_spec, "/__table/shared": save_shared,
        "/__table/undo": undo_table, "/__table-comments": change_comments}


def get_route(path, query):
    return (lambda: GET[path](query)) if path in GET else None


def post_route(path, body):
    return (lambda: POST[path](body())) if path in POST else None
