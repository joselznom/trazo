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
                                     values}}, values, records (first 50), count, inputs},
                                     model, issues, format_usage, map_usage, undo}
                                   (map_usage: {field: [{label, own}]}: the other tables
                                   that show the field and the values their own maps
                                   rename, for "this table only or everywhere")
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
GET  /__table/sections             {documents: [{doc, title, sections: [{file, line, title}]}]}
POST /__table/new                  {title, file, line, rows?} a table typed by hand: writes
                                     its spec (rows: pasted cells, the first row the
                                     headers) and adds its !include at the end of the
                                     section at `line` of `file`. Returns {label}
POST /__table/convert              {op: check|convert|undo, doc, id} Make editable
                                     (trazo.table_convert): check says {ok}, or 400 with
                                     the reason; convert returns {label, markdown}; undo
                                     takes those and puts the Markdown back in place of
                                     the !include, refused (409) once the table was edited
GET  /__table-comments?label=L     {comments, undo}
POST /__table-comments             {op: add|update|reply|purge|undo, ...}
                                     (trazo.review_comments; add takes {table,
                                     comment, target?})
GET  /__table-status               {tables: {label: sha256}, shared: sha256,
                                     data: {name: sha256}, comments: mtime_ns} for polling
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import table as tb
from . import table_hand
from .local_server import Conflict, NotFound, RequestError, Unprocessable
from .project import ASSETS_DIR, PROJECT

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
    except tb.TableConflict as exc:  # `extra`: another program wrote the file during a save
        raise Conflict(str(exc), **getattr(exc, "extra", {})) from exc
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
                    "kind": "hand" if table.hand else "spec"})
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
    inputs = table.data.get("inputs", {}) if isinstance(table.data, dict) else {}
    return {"file": tb.rel(tb.data_path(name)) if isinstance(name, str) else None,
            "sha256": table.data_sha256, "inputs": list(inputs) if isinstance(inputs, dict) else [],
            "fields": fields, "values": table.data.get("values", {}) if table.fields else {},
            "records": records[:RECORDS_SHOWN], "count": len(records)}


def shown_fields(spec: dict[str, Any]) -> set[str]:
    """The fields a spec shows: row keys, column fields and pivots."""
    rows, columns = spec.get("rows"), spec.get("columns")
    keys = rows.get("keys", []) if isinstance(rows, dict) else []
    fields = {k for k in keys if isinstance(k, str)}
    for col in columns if isinstance(columns, list) else []:
        children = col.get("columns") if isinstance(col, dict) else None
        for item in [col, *(children if isinstance(children, list) else [])]:
            if isinstance(item, dict):
                fields |= {item[k] for k in ("field", "pivot") if isinstance(item.get(k), str)}
    return fields


def map_usage(label: str) -> dict[str, list[dict[str, Any]]]:
    """For each field: the other tables that show it, so they share its labels in
    _shared.json, with the values their own maps rename. Read from the specs
    only (no table is built)."""
    usage: dict[str, list[dict[str, Any]]] = {}
    for other in tb.all_labels():
        try:
            spec = tb.read_json(tb.spec_path(other), "table spec")[0] if other != label else None
        except tb.TableError:
            continue
        if not isinstance(spec, dict):
            continue
        maps = spec.get("maps")
        for field in sorted(shown_fields(spec)):
            own = maps.get(field) if isinstance(maps, dict) else None
            usage.setdefault(field, []).append({"label": other, "own": sorted(own) if isinstance(own, dict) else []})
    return usage


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
            "issues": table.issues, "format_usage": {} if table.hand else tb.format_usage(),
            "map_usage": {} if table.hand else map_usage(label),
            "undo": next_undo(label)}


def get_table(query: dict[str, list[str]]) -> dict[str, Any]:
    label = spec_file(query.get("label", [None])[0])
    with tb.review_lock():
        tb.HISTORY.sweep(tb.spec_path(label))
    return errors_to_http(lambda: table_state(label, query.get("hidden", ["0"])[0] == "1"))


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
    return {**table_state(label, bool(request.get("hidden"))), **warned()}


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
    return {**(table_state(label, bool(request.get("hidden"))) if label else {}), **warned()}


def warned() -> dict[str, str]:
    """The warning of the publication this request just made, if any (review_history)."""
    warning = tb.HISTORY.take_warning()
    return {"warning": warning} if warning else {}


def undo_table(request: Any) -> dict[str, Any]:
    request = body(request)
    label = spec_file(request.get("label"))
    message = errors_to_http(lambda: tb.undo(label))
    if message == "nothing to undo":
        raise RequestError("nothing to undo")
    return {**table_state(label, bool(request.get("hidden"))), "undone": message}


def sections() -> dict[str, Any]:
    """Where a new table can go: every heading of the documents the preview edits."""
    from . import preview
    from . import submission_check as sc
    from .preview_sources import read_source
    documents = []
    for doc in PROJECT.documents:
        found: list[dict[str, Any]] = []
        files = [p for p in PROJECT.document_files(doc)
                 if any(p.is_relative_to(preview.ROOT / root) for root in preview.SOURCE_ROOTS)]
        for path in files:
            found += [{"file": tb.rel(path), "line": s["line"], "title": s["heading"]}
                      for s in sc.sections(read_source(path)[1])]
        if files and not found:
            found.append({"file": tb.rel(files[-1]), "line": None, "title": "End of the document"})
        if found:
            documents.append({"doc": doc, "title": PROJECT.titles.get(doc, doc), "sections": found})
    return {"documents": documents}


def with_include(text: str, line: Any, label: str) -> str:
    """The file's text with the table's !include at the end of the section at `line`."""
    from .submission_check import sections as sections_of
    end = len(text)
    if line is not None:
        section = next((s for s in sections_of(text) if s["line"] == line), None)
        if section is None:
            raise Conflict("the section changed on disk; choose it again")
        end = section["end"]
    tail = text[end:]
    return f"{text[:end].rstrip()}\n\n{table_hand.include_line(label)}\n" + (f"\n{tail}" if tail.strip() else "")


def publish(label: str, spec: dict[str, Any], source: dict[str, Any], text: str) -> None:
    """Write a new table typed by hand and the document text that shows it. Not in the
    table history: undoing the file would leave its !include behind."""
    from .preview_sources import change_source
    with tb.review_lock():
        tb.write_atomic(tb.spec_path(label), tb.dump_spec(spec))
    tb.render_one(label)
    try:
        change_source({"file": source["file"], "text": text, "base_sha256": source["sha256"]})
    except Exception:  # the document changed: no table without its line
        tb.spec_path(label).unlink()
        tb.output_path(label).unlink(missing_ok=True)
        raise


def new_table(request: Any) -> dict[str, Any]:
    from .preview_sources import get_source
    request = body(request)
    title, rows = request.get("title"), request.get("rows")
    if not isinstance(title, str) or not title.strip():
        raise RequestError("a new table needs a title")
    if rows is not None and not table_hand.is_rows(rows):
        raise RequestError("rows must be a list of lists of text")
    source = get_source(request.get("file"))
    label = table_hand.free_label(title)
    text = with_include(source["text"], request.get("line"), label)
    spec = table_hand.new_spec(" ".join(title.split()), rows)
    problems = tb.Checker().spec(spec)
    if problems:
        raise Unprocessable("the table cannot be created", issues=problems)
    publish(label, spec, source, text)
    return {"label": label}


def convert_table(request: Any) -> dict[str, Any]:
    from . import table_convert
    from .preview_sources import change_source, get_source
    request = body(request)
    op, doc = request.get("op"), request.get("doc")
    if op in ("check", "convert"):
        found = table_convert.plan(doc, request.get("id"))
        if op == "check":
            return {"ok": True}
        publish(found["label"], found["spec"], found["source"], found["text"])
        return {"label": found["label"], "markdown": found["markdown"]}
    if op != "undo":
        raise RequestError("op must be check, convert or undo")
    label, markdown = spec_file(request.get("label")), request.get("markdown")
    if not isinstance(markdown, str) or not markdown.strip():
        raise RequestError("undo needs the table's markdown")
    if next_undo(label):
        raise tb.TableConflict("this table was edited after it became editable")
    # An ordinary save of the file, so what was saved since the conversion stays.
    source = get_source(file_including(doc, label))
    lines = source["text"].split("\n")
    lines[lines.index(table_hand.include_line(label))] = markdown
    change_source({"file": source["file"], "text": "\n".join(lines), "base_sha256": source["sha256"]})
    tb.spec_path(label).unlink()
    tb.output_path(label).unlink(missing_ok=True)
    return {}


def file_including(doc: Any, label: str) -> str:
    """The document file with the table's !include."""
    from .expand import include_lines
    wanted = table_hand.include_line(label)
    for path in PROJECT.document_files(doc):
        if any(line == wanted for line, _ in include_lines(path.read_text(encoding="utf-8"))):
            return tb.rel(path)
    raise NotFound(f"no document includes {label}")


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


GET = {"/__tables": lambda q: list_tables(), "/__table": get_table, "/__table/sections": lambda q: sections(),
       "/__table-comments": get_comments, "/__table-status": lambda q: status()}
POST = {"/__table": save_table, "/__table/preview": preview_spec, "/__table/shared": save_shared,
        "/__table/undo": undo_table, "/__table/new": new_table, "/__table/convert": lambda r: errors_to_http(lambda: convert_table(r)),
        "/__table-comments": change_comments}


def get_route(path, query):
    return (lambda: GET[path](query)) if path in GET else None


def post_route(path, body):
    return (lambda: POST[path](body())) if path in POST else None
