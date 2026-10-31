"""Tables from typed data and a JSON spec: tables/specs/<label>.json.

The project's own generator computes the numbers and writes unformatted
data to tables/output/data/<name>.json; trazo never runs project code.
The spec holds all presentation (columns, order, headers, grouped headers,
formats, row order, block rows, merged cells and emphasis), and `render`
turns data plus spec into tables/output/md/<label>.md, a Pandoc grid table
with the caption `Table: [<title>]{.caption-title} <legend> {#tbl:<label>}`
(the preview shows the title in bold; Word keeps its caption style). A
`note` follows the table as `::: {.table-note custom-style="Table Note"}`
(abbreviations, symbols, sources), in Word's Table Note style. The
folders are [paths] table_specs, table_data and table_output in trazo.toml.

    trazo table new LABEL --data NAME       scaffold a spec, one column per field
    trazo table render LABEL|--all          write <table_output>/md/<label>.md
    trazo table check LABEL|--all [--json] [--outputs]
    trazo table describe LABEL              fields, values, columns, rows, the table
    trazo table patch LABEL OPS.json        (OPS.json may be "-" for stdin)
    trazo table patch --shared OPS.json     set formats.* / maps.* in _shared.json
    trazo table undo [LABEL]                the last change (of LABEL's spec only)
    trazo table list [--json]               label, data, status (ok/stale/error)
    trazo table comments [LABEL] [--all]    open table comments (--all: every state)
    trazo table resolve ID "reply"          answer a comment; it moves to review

Data file (schema_version 1): {"generator", "inputs": {path: sha256},
"values": {name: number}, "records": [{field: value}]}. A value is a string,
an int, a float, null (missing) or a composite: an object of numbers such as
{est, low, high}, {mean, sd} or {k, n}. Long and wide formats both work. The
data is stale (an error) when an input's sha256 changed.

Spec keys (unknown keys are errors):
    schema_version 1; data (name); title; legend; note (below the table;
          a blank line starts a paragraph); missing (default "–")
    rows: keys (one row per distinct combination), order {field: [values]}
          (listed values first, the rest in data order), filter {field:
          [values]}, block (a key; a full-width bold row when it changes)
    columns: leaf {id, field, header, format, align left|center|right,
             merge, width, hidden}; group {id, header, columns: [leaves]};
             pivot {id, pivot: field, values, columns: [leaves]}, one group
             per value headed by its mapped value (a single leaf: one column
             per value). Leaf ids: <id>, <group>.<leaf>, <pivot>.<value>.<leaf>.
    formats {name: "{mean:.2f} ({sd:.2f})"}: str.format on the composite's
             keys, or {value} for a scalar; {k, n} adds pct = 100*k/n
    maps {field: {value: text}}; highlight [{column, best max|min, within
    table|block|<key>, key}]; emphasis [{row: {key: value}, column}]
_shared.json holds formats and maps for every table; a spec's own entries
override them. Headers, maps, formats, title, legend and note are Markdown
as written; text from data is escaped. Title, legend and note quote data values as
{name} or {name:.2f} ({{ and }} are literal braces). A float or composite
needs a format. `merge` joins a cell to the one above when the text is equal
and every column to its left (merge or not) is joined too; spans stop at
block rows. Highlight finds the best raw number, then bolds every cell of
the set whose text equals the best cell's text (ties go by what is shown).

Widths follow content length (or the leaves' `width` weights, where leaves
without one weigh 1). Pandoc (3.x, gridtables) gives a grid column of d
dashes the width (d + 1) / max(72, sum(d + 1) + n - 2) of the text, for n
columns and --columns 72, so a grid table never quite reaches the full width
with more than two columns. Narrow tables are padded until sum(d + 1) + n - 2
reaches 72, which gives about (74 - n) / 72 of the text width (97% for 4
columns, 93% for 7); wider content gets sum(d + 1) / (sum(d + 1) + n - 2).

Patch operations (a JSON list, applied all-or-nothing, then checked):
    {"op": "set", "path": "columns.score.mean.header", "value": "Mean"}
        segments after "columns" are column ids, then keys; other paths are
        plain keys ("title", "rows.order.site", "formats.ci", "maps.group.A");
        null deletes the key
    {"op": "add", "into": "columns" | "columns.<group or pivot id>", "item": {...}, "before": ID}
    {"op": "remove", "column": ID}          ID is <id> or <group>.<leaf>
    {"op": "move", "column": ID, "before": ID | null}   within the same parent

`patch` renders the table's Markdown after saving; `patch --shared` takes
set ops on formats.<name> and maps.<field>.<value>, refuses when a table
without errors would get some, and renders every table. `new`, `patch` and
comment changes record history in notes/review/table_history.json; `undo`
reverts the newest entry (with LABEL: the newest change of that spec, or a
shared edit made from its editor) and refuses if the file or comment changed
since. The preview's table editor (/tables, trazo.table_editor) uses the
same functions.

Comments (made in the table editor) live in notes/review/table_comments.json:
{id, table, target?, comment, status, replies, created}, target {"column":
leaf id} or {"column": leaf id, "row": {key: value}}, with the preview's
states (open, review after `resolve`, done set by the author). See
trazo.review_comments.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

from .build_lock import exclusive
from .dependencies import file_digest
from .local_server import NotFound, RequestError
from .project import PROJECT
from .review_comments import CommentStore
from .review_history import History, rel
from .review_storage import REVIEW_DIR, StoreError, review_lock, write_atomic

ROOT = PROJECT.root
SPEC_DIR = PROJECT.path("table_specs")
DATA_DIR = PROJECT.path("table_data")
OUTPUT_DIR = PROJECT.path("table_output") / "md"
SHARED_PATH = SPEC_DIR / "_shared.json"
HISTORY_PATH = REVIEW_DIR / "table_history.json"
COMMENTS_PATH = REVIEW_DIR / "table_comments.json"
TABLES_HINT = PROJECT.command_hint("tables")
TEXT_WIDTH = 72  # Pandoc's default --columns
DISTINCT_SHOWN = 12

LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
SPEC_KEYS = {"schema_version", "data", "title", "legend", "note", "rows", "columns", "maps", "formats",
             "highlight", "emphasis", "missing"}
SHARED_KEYS = {"schema_version", "formats", "maps"}
DATA_KEYS = {"schema_version", "generator", "inputs", "values", "records"}
ROWS_KEYS = {"keys", "order", "filter", "block"}
LEAF_KEYS = {"id", "field", "header", "format", "align", "merge", "width", "hidden"}
GROUP_KEYS = {"id", "header", "columns"}
PIVOT_KEYS = {"id", "pivot", "values", "columns"}
HIGHLIGHT_KEYS = {"column", "best", "within", "key"}
EMPHASIS_KEYS = {"row", "column"}
ALIGNS = ("left", "center", "right")
DEFAULT_MISSING = "–"
# Markdown punctuation escaped in text that comes from data.
MD_SPECIAL = re.compile(r"([\\`*_{}\[\]<>|#~^$@&])")
LIST_START = re.compile(r"^(\d+)([.)])")

Issue = dict[str, Any]


class TableError(Exception):
    """A user-facing failure; the message is printed and the exit code is 1."""


def issue(level: str, code: str, where: str | None, message: str) -> Issue:
    return {"level": level, "code": code, "id": where, "message": message}


# -------------------------------------------------- values and formats


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_composite(value: Any) -> bool:
    return isinstance(value, dict) and bool(value) and all(is_number(v) for v in value.values())


def show(value: Any) -> str:
    """A data value as JSON, for messages."""
    return json.dumps(value, ensure_ascii=False)


def format_value(template: str, value: Any) -> str:
    """Apply a format: the composite's keys, or {value} for a scalar ({k, n} adds pct)."""
    if value is None:
        raise TableError("no value to format")
    if isinstance(value, dict):
        context = dict(value)
        if is_number(value.get("k")) and is_number(value.get("n")) and value["n"]:
            context.setdefault("pct", 100 * value["k"] / value["n"])
    else:
        context = {"value": value}
    try:
        return template.format_map(context)
    except KeyError as exc:
        raise TableError(f"format {template!r} needs {{{exc.args[0]}}}, "
                         f"but the value {show(value)} has {', '.join(context)}") from exc
    except (IndexError, ValueError, TypeError, AttributeError) as exc:
        raise TableError(f"format {template!r} does not fit {show(value)}: {exc}") from exc


def escape(text: str) -> str:
    """Text from data as literal Markdown on one line."""
    text = MD_SPECIAL.sub(r"\\\1", " ".join(text.split()))
    if text[:1] in ("-", "+"):
        text = "\\" + text
    return LIST_START.sub(r"\1\\\2", text)


def display_width(text: str) -> int:
    """Columns the text takes in a monospaced grid (wide characters count 2)."""
    return sum(0 if unicodedata.combining(ch) else
               2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def pad(text: str, width: int) -> str:
    return text + " " * (width - display_width(text))


# -------------------------------------------------- files


def spec_path(label: str) -> Path:
    if not isinstance(label, str) or not LABEL_RE.match(label):
        raise TableError(f"invalid table label {label!r} (lowercase letters, digits, - and _)")
    return SPEC_DIR / f"{label}.json"


def output_path(label: str) -> Path:
    return OUTPUT_DIR / f"{label}.md"


def data_path(name: str) -> Path:
    return DATA_DIR / f"{name}.json"


def all_labels() -> list[str]:
    return sorted(p.stem for p in SPEC_DIR.glob("*.json") if LABEL_RE.match(p.stem))


def read_json(path: Path, what: str) -> tuple[Any, bytes]:
    if not path.is_file():
        raise TableError(f"{what} not found: {rel(path)}")
    data = path.read_bytes()
    try:
        return json.loads(data.decode("utf-8")), data
    except (UnicodeDecodeError, ValueError) as exc:
        raise TableError(f"{rel(path)} is not valid JSON: {exc}") from exc


def dump_spec(spec: dict[str, Any]) -> bytes:
    return (json.dumps(spec, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def load_shared() -> dict[str, Any]:
    """_shared.json ({} when absent); TableError when it is unusable."""
    if not SHARED_PATH.exists():
        return {}
    shared, _ = read_json(SHARED_PATH, "shared formats")
    problems = Checker().shared(shared)
    if problems:
        raise TableError(f"{rel(SHARED_PATH)}: {problems[0]['message']}")
    return shared


def shared_formats() -> tuple[dict[str, str], str]:
    """The formats of _shared.json (for {{v:key|name}} in prose) and the file's name."""
    return load_shared().get("formats", {}), rel(SHARED_PATH)


# -------------------------------------------------- schema


class Checker:
    """Schema checks of a spec, _shared.json and a data file; collects issues."""

    def __init__(self) -> None:
        self.issues: list[Issue] = []

    def error(self, where: str | None, message: str, code: str = "schema") -> None:
        self.issues.append(issue("error", code, where, message))

    def keys(self, obj: Any, allowed: set[str], where: str, required: tuple[str, ...] = ()) -> bool:
        if not isinstance(obj, dict):
            self.error(where, f"{where} must be an object")
            return False
        for key in sorted(set(obj) - allowed):
            self.error(where, f"{where}: unknown key {key!r} "
                              f"(allowed: {', '.join(sorted(allowed))})")
        for key in required:
            if key not in obj:
                self.error(where, f"{where}: {key!r} is required")
        return True

    def string(self, obj: dict[str, Any], key: str, where: str, empty: bool = True) -> None:
        if key in obj and (not isinstance(obj[key], str) or (not empty and not obj[key].strip())):
            self.error(where, f"{where}.{key} must be a {'non-empty ' if not empty else ''}string")

    def text_map(self, value: Any, where: str, nested: bool) -> None:
        if not isinstance(value, dict):
            self.error(where, f"{where} must be an object")
            return
        for key, item in value.items():
            if nested:
                self.text_map(item, f"{where}.{key}", False)
            elif not isinstance(item, str):
                self.error(where, f"{where}.{key} must be a string")

    def version(self, obj: dict[str, Any], where: str, required: bool) -> None:
        if obj.get("schema_version", None if required else 1) != 1:
            self.error(where, f"{where}: schema_version must be 1")

    def shared(self, shared: Any) -> list[Issue]:
        if self.keys(shared, SHARED_KEYS, "_shared"):
            self.version(shared, "_shared", False)
            if "formats" in shared:
                self.text_map(shared["formats"], "formats", False)
            if "maps" in shared:
                self.text_map(shared["maps"], "maps", True)
        return self.issues

    def leaf(self, col: dict[str, Any], where: str) -> None:
        if not self.keys(col, LEAF_KEYS, where, ("id", "field", "header")):
            return
        for key in ("field", "header", "format"):
            self.string(col, key, where, empty=key == "header")
        if "align" in col and col["align"] not in ALIGNS:
            self.error(where, f"{where}.align must be one of {', '.join(ALIGNS)}")
        for key in ("merge", "hidden"):
            if key in col and not isinstance(col[key], bool):
                self.error(where, f"{where}.{key} must be true or false")
        if "width" in col and not (is_number(col["width"]) and col["width"] > 0):
            self.error(where, f"{where}.width must be a positive number")

    def column(self, col: Any, where: str, top: bool) -> None:
        if not isinstance(col, dict):
            self.error(where, f"{where} must be an object")
            return
        if not isinstance(col.get("id"), str) or not ID_RE.match(col["id"]):
            self.error(where, f"{where}.id must be letters, digits, - and _ (no dots)")
        if "columns" not in col and "pivot" not in col:
            self.leaf(col, where)
            return
        if not top:
            self.error(where, f"{where}: groups and pivots hold leaves only (two header levels)")
            return
        if "pivot" in col:
            self.keys(col, PIVOT_KEYS, where, ("id", "pivot", "columns"))
            self.string(col, "pivot", where)
            if "values" in col and (not isinstance(col["values"], list) or not all(
                    isinstance(v, str) or is_number(v) for v in col["values"])):
                self.error(where, f"{where}.values must be a list of strings or numbers")
        else:
            self.keys(col, GROUP_KEYS, where, ("id", "header", "columns"))
            self.string(col, "header", where)
        children = col.get("columns")
        if not isinstance(children, list) or not children:
            self.error(where, f"{where}.columns must be a non-empty list")
            return
        self.unique(children, where)
        for child in children:
            name = child.get("id") if isinstance(child, dict) else None
            self.column(child, f"{where}.{name}" if name else where, top=False)

    def unique(self, columns: list[Any], where: str) -> None:
        ids = [c.get("id") for c in columns if isinstance(c, dict)]
        for name in sorted({i for i in ids if ids.count(i) > 1 and isinstance(i, str)}):
            self.error(where, f"{where}: duplicate column id {name!r}")

    def spec(self, spec: Any) -> list[Issue]:
        if not self.keys(spec, SPEC_KEYS, "spec", ("schema_version", "data", "title", "rows",
                                                   "columns")):
            return self.issues
        self.version(spec, "spec", True)
        name = spec.get("data")
        if "data" in spec and (not isinstance(name, str) or not NAME_RE.match(name)):
            self.error("spec", "spec.data must be a data file name (without .json)")
        self.string(spec, "title", "spec", empty=False)
        self.string(spec, "legend", "spec")
        self.string(spec, "note", "spec")
        self.string(spec, "missing", "spec")
        rows = spec.get("rows")
        if "rows" in spec and self.keys(rows, ROWS_KEYS, "rows", ("keys",)):
            keys = rows.get("keys")
            if not (isinstance(keys, list) and keys and all(isinstance(k, str) for k in keys)
                    and len(set(keys)) == len(keys)):
                self.error("rows", "rows.keys must be a non-empty list of distinct field names")
            for part in ("order", "filter"):
                value = rows.get(part, {})
                if not isinstance(value, dict) or not all(
                        isinstance(v, list) for v in value.values()):
                    self.error("rows", f"rows.{part} must map fields to lists of values")
            self.string(rows, "block", "rows")
        columns = spec.get("columns")
        if "columns" in spec:
            if not isinstance(columns, list) or not columns:
                self.error("columns", "columns must be a non-empty list")
            else:
                self.unique(columns, "columns")
                for col in columns:
                    name = col.get("id") if isinstance(col, dict) else None
                    self.column(col, f"columns.{name}" if name else "columns", top=True)
        if "formats" in spec:
            self.text_map(spec["formats"], "formats", False)
        if "maps" in spec:
            self.text_map(spec["maps"], "maps", True)
        for i, rule in enumerate(spec.get("highlight", []) if isinstance(
                spec.get("highlight", []), list) else [None]):
            where = f"highlight.{i}"
            if self.keys(rule, HIGHLIGHT_KEYS, where, ("column", "best")):
                self.string(rule, "column", where)
                self.string(rule, "within", where)
                self.string(rule, "key", where)
                if rule.get("best") not in ("max", "min"):
                    self.error(where, f"{where}.best must be max or min")
        for i, rule in enumerate(spec.get("emphasis", []) if isinstance(
                spec.get("emphasis", []), list) else [None]):
            where = f"emphasis.{i}"
            if self.keys(rule, EMPHASIS_KEYS, where, ("row", "column")):
                self.string(rule, "column", where)
                if not isinstance(rule.get("row"), dict) or not rule.get("row"):
                    self.error(where, f"{where}.row must be a non-empty object of row keys")
        return self.issues

    def data(self, data: Any, name: str) -> list[Issue]:
        where = f"data {name}"
        if not self.keys(data, DATA_KEYS, where, ("schema_version", "records")):
            return self.issues
        self.version(data, where, True)
        self.string(data, "generator", where)
        inputs = data.get("inputs", {})
        if not isinstance(inputs, dict) or not all(isinstance(v, str) for v in inputs.values()):
            self.error(where, f"{where}: inputs must map paths to sha256 strings")
        if not isinstance(data.get("values", {}), dict):
            self.error(where, f"{where}: values must be an object")
        records = data.get("records")
        if not isinstance(records, list):
            self.error(where, f"{where}: records must be a list of objects")
            return self.issues
        for i, record in enumerate(records):
            if not isinstance(record, dict):
                self.error(where, f"{where}: record {i} must be an object")
                continue
            for field, value in record.items():
                if not (value is None or isinstance(value, str) or is_number(value)
                        or is_composite(value)):
                    self.error(where, f"{where}: record {i} field {field!r} is {show(value)}; "
                                      "a value is a string, number, null or an object of numbers")
        return self.issues


# -------------------------------------------------- build


def hkey(value: Any) -> str:
    """A hashable, order-free key for a data value."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


class Leaf:
    """One rendered column."""

    def __init__(self, col: dict[str, Any], full_id: str, header: str,
                 group: tuple[str, str] | None = None,
                 pivot: tuple[str, Any] | None = None) -> None:
        self.spec = col
        self.id = full_id
        self.field = col.get("field", "")
        self.header = header
        self.format = col.get("format")
        self.align = col.get("align", "left")
        self.merge = bool(col.get("merge"))
        self.width = col.get("width")
        self.hidden = bool(col.get("hidden"))
        self.group = group  # (key, header) of the spanning header above
        self.pivot = pivot  # (field, value)


class Table:
    """A spec checked against its data and rendered; see build()."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.path = spec_path(label)
        self.spec: dict[str, Any] = {}
        self.source = b""
        self.data: dict[str, Any] = {}
        self.data_sha256: str | None = None
        self.fields: dict[str, list[Any]] = {}  # field -> values in data order
        self.formats: dict[str, str] = {}
        self.maps: dict[str, dict[str, str]] = {}
        self.leaves: list[Leaf] = []  # visible
        self.all_leaves: list[Leaf] = []  # visible and hidden, in column order
        self.hidden_cells: dict[str, list[tuple[str, Any]]] = {}  # leaf id -> (text, raw) per row
        self.all_ids: list[str] = []
        self.keys: list[str] = []
        self.rows: list[dict[str, Any]] = []  # {"key": {field: value}, "records": [...]}
        self.cells: list[list[str]] = []
        self.values: list[list[Any]] = []  # raw cell values
        self.bold: set[tuple[int, int]] = set()
        self.caption = ""
        self.note: list[str] = []  # paragraphs below the table
        self.markdown: str | None = None
        self.issues: list[Issue] = []
        self.filled = False  # cells computed (the editor can draw the table)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i["level"] == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i["level"] == "warning"]

    def error(self, where: str | None, message: str, code: str = "reference") -> None:
        self.issues.append(issue("error", code, where, message))

    def warn(self, where: str | None, message: str, code: str = "reference") -> None:
        self.issues.append(issue("warning", code, where, message))

    # ------------------------------------------------ loading

    def load(self, spec: dict[str, Any] | None = None,
             shared: dict[str, Any] | None = None) -> bool:
        try:
            if spec is None:
                spec, self.source = read_json(self.path, "table spec")
            self.spec = spec
            problems = Checker().spec(spec)
            if problems:
                self.issues += problems
                return False
            shared = load_shared() if shared is None else shared
        except TableError as exc:
            self.error(None, str(exc), "load")
            return False
        self.formats = {**shared.get("formats", {}), **spec.get("formats", {})}
        self.maps = {f: {**shared.get("maps", {}).get(f, {}), **spec.get("maps", {}).get(f, {})}
                     for f in {*shared.get("maps", {}), *spec.get("maps", {})}}
        try:
            self.data, source = read_json(data_path(spec["data"]), "table data")
            self.data_sha256 = hashlib.sha256(source).hexdigest()
        except TableError as exc:
            self.error("data", f"{exc}; run {TABLES_HINT}", "data")
            return False
        problems = Checker().data(self.data, spec["data"])
        if problems:
            self.issues += problems
            return False
        for name, digest in self.data.get("inputs", {}).items():
            source = Path(name) if Path(name).is_absolute() else ROOT / name
            if not source.is_file() or file_digest(source) != digest:
                self.error("data", f"{rel(data_path(spec['data']))} is stale: {name} changed "
                                   f"since it was written; run {TABLES_HINT}", "stale-data")
        for record in self.data["records"]:
            for field, value in record.items():
                self.fields.setdefault(field, []).append(value)
        return True

    def known(self, field: Any, where: str) -> bool:
        if field in self.fields:
            return True
        self.error(where, f"{where}: unknown field {field!r} "
                          f"(data fields: {', '.join(self.fields)})")
        return False

    # ------------------------------------------------ text

    def mapped(self, field: str, value: Any) -> str:
        """A data value as cell text: its map entry (Markdown) or escaped text."""
        if value is None:
            return self.spec.get("missing", DEFAULT_MISSING)
        mapping = self.maps.get(field, {})
        key = value if isinstance(value, str) else str(value)
        return mapping[key] if key in mapping else escape(key)

    def cell_text(self, leaf: Leaf, value: Any, where: str) -> str:
        if value is None:
            return self.spec.get("missing", DEFAULT_MISSING)
        if leaf.format:
            if leaf.format not in self.formats:
                return "?"  # reported by expand_columns
            try:
                return format_value(self.formats[leaf.format], value)
            except TableError as exc:
                self.error(leaf.id, f"{where}: {exc}", "format")
                return "?"
        if isinstance(value, str):
            return self.mapped(leaf.field, value)
        if isinstance(value, int):
            return str(value)
        kind = "composite" if isinstance(value, dict) else "float"
        self.error(leaf.id, f"column {leaf.id}: {kind} values need a format "
                            f"(e.g. \"format\": \"dec2\" with formats.dec2 = \"{{value:.2f}}\")",
                   "format")
        return "?"

    # ------------------------------------------------ columns

    def add_leaf(self, leaf: Leaf) -> None:
        self.all_ids.append(leaf.id)
        self.all_leaves.append(leaf)
        if not leaf.hidden:
            self.leaves.append(leaf)

    def expand_columns(self, records: list[dict[str, Any]]) -> None:
        for col in self.spec["columns"]:
            if "pivot" in col:
                field = col["pivot"]
                if not self.known(field, f"columns.{col['id']}.pivot"):
                    continue
                if field in self.keys:
                    self.error(col["id"], f"pivot {col['id']}: {field!r} is also in rows.keys")
                present: list[Any] = []
                for record in records:
                    if record.get(field) is not None and record.get(field) not in present:
                        present.append(record.get(field))
                values = col.get("values", present)
                for value in values:
                    if value not in present:
                        self.warn(col["id"], f"pivot {col['id']}: value {show(value)} is not in "
                                             "the data (its cells are missing)")
                shown = [c for c in col["columns"] if not c.get("hidden")]
                for value in values:
                    header = self.mapped(field, value)
                    single = len(shown) == 1
                    for child in col["columns"]:
                        full = f"{col['id']}.{value}.{child['id']}"
                        if single and child.get("hidden"):  # beside the value's single column
                            self.add_leaf(Leaf(child, full, f"{header} {child['header']}",
                                               None, (field, value)))
                            continue
                        self.add_leaf(Leaf(
                            child, full, header if single else child["header"],
                            None if single else (f"{col['id']}.{value}", header), (field, value)))
            elif "columns" in col:
                for child in col["columns"]:
                    self.add_leaf(Leaf(child, f"{col['id']}.{child['id']}", child["header"],
                                       (col["id"], col["header"])))
            else:
                self.add_leaf(Leaf(col, col["id"], col["header"]))
        for name in sorted({i for i in self.all_ids if self.all_ids.count(i) > 1}):
            self.error(name, f"duplicate leaf id {name!r}")
        for leaf in self.leaves:
            self.known(leaf.field, f"column {leaf.id}")
            if leaf.format and leaf.format not in self.formats:
                self.error(leaf.id, f"column {leaf.id}: unknown format {leaf.format!r} "
                                    f"(formats: {', '.join(self.formats) or 'none'})")
        seen: dict[tuple[str, str], str] = {}
        for leaf in self.leaves:
            name = (leaf.group[1] if leaf.group else "", leaf.header)
            if name in seen:
                self.error(leaf.id, f"columns {seen[name]} and {leaf.id} have the same header "
                                    f"{' / '.join(n for n in name if n)!r}")
            seen.setdefault(name, leaf.id)
        if not self.leaves:
            self.error("columns", "every column is hidden")

    # ------------------------------------------------ rows

    def row_name(self, key: dict[str, Any]) -> str:
        return ", ".join(f"{k}={show(v)}" for k, v in key.items())

    def build_rows(self) -> list[dict[str, Any]]:
        rows_spec = self.spec["rows"]
        self.keys = list(rows_spec["keys"])
        ok = all([self.known(k, "rows.keys") for k in self.keys])
        for part in ("order", "filter"):
            for field in rows_spec.get(part, {}):
                ok = self.known(field, f"rows.{part}") and ok
        block = rows_spec.get("block")
        if block is not None and block not in self.keys:
            self.error("rows", f"rows.block {block!r} must be one of rows.keys")
            ok = False
        if not ok:
            return []
        records = [r for r in self.data["records"] if all(
            r.get(f) in allowed for f, allowed in rows_spec.get("filter", {}).items())]
        groups: dict[tuple[str, ...], dict[str, Any]] = {}
        first: dict[str, dict[str, int]] = {k: {} for k in self.keys}
        for record in records:
            for k in self.keys:
                if isinstance(record.get(k), dict):
                    self.error("rows", f"rows.keys: field {k!r} has composite values")
                    return []
                first[k].setdefault(hkey(record.get(k)), len(first[k]))
            ident = tuple(hkey(record.get(k)) for k in self.keys)
            row = groups.setdefault(ident, {"key": {k: record.get(k) for k in self.keys},
                                            "records": []})
            row["records"].append(record)
        order = rows_spec.get("order", {})
        for field, listed in order.items():
            if field not in self.keys:
                continue
            extra = [v for v in (r["key"][field] for r in groups.values())
                     if v not in listed]
            extra = list(dict.fromkeys(hkey(v) for v in extra))
            if extra:
                self.warn("rows", f"rows.order.{field} does not list {', '.join(extra)} "
                                  "(shown after the listed values, in data order)")

        def rank(row: dict[str, Any]) -> tuple[tuple[int, int], ...]:
            out = []
            for k in self.keys:
                listed = order.get(k, [])
                value = row["key"][k]
                out.append((0, listed.index(value)) if value in listed
                           else (1, first[k][hkey(value)]))
            return tuple(out)

        self.expand_columns(records)
        return sorted(groups.values(), key=rank)

    def cell(self, row: dict[str, Any], leaf: Leaf) -> tuple[str, Any]:
        """(text, raw value) of one cell; problems are reported for visible leaves."""
        where = f"row {self.row_name(row['key'])}, column {leaf.id}"
        issues = self.issues
        if leaf.hidden:  # hidden columns are drawn by the editor but never checked
            self.issues = []
        try:
            if leaf.pivot:
                field, value = leaf.pivot
                matches = [rec for rec in row["records"] if rec.get(field) == value]
                if len(matches) > 1:
                    self.error(leaf.id, f"{where}: {len(matches)} records have "
                                        f"{field}={show(value)}; add the field that tells "
                                        "them apart to rows.keys")
                raw = matches[0].get(leaf.field) if matches else None
            else:
                seen = list(dict.fromkeys(hkey(rec.get(leaf.field)) for rec in row["records"]))
                if len(seen) > 1 and leaf.field not in self.keys:
                    self.error(leaf.id, f"{where}: field {leaf.field!r} differs between the "
                                        f"row's records ({', '.join(seen)}); add it to "
                                        "rows.keys or pivot it", "rows")
                raw = row["records"][0].get(leaf.field)
            return self.cell_text(leaf, raw, where), raw
        finally:
            self.issues = issues

    def fill(self) -> None:
        for row in self.rows:
            texts, raws = [], []
            for leaf in self.all_leaves:
                text, raw = self.cell(row, leaf)
                if leaf.hidden:
                    self.hidden_cells.setdefault(leaf.id, []).append((text, raw))
                else:
                    texts.append(text)
                    raws.append(raw)
            self.cells.append(texts)
            self.values.append(raws)

    # ------------------------------------------------ emphasis

    def leaf_index(self, column: str, where: str) -> int | None:
        for i, leaf in enumerate(self.leaves):
            if leaf.id == column:
                return i
        hidden = " (it is hidden)" if column in self.all_ids else ""
        self.error(where, f"{where}: no visible column {column!r}{hidden} "
                          f"(leaf ids: {', '.join(leaf.id for leaf in self.leaves)})")
        return None

    def number(self, value: Any, key: str | None) -> float | None:
        if is_number(value):
            return value
        if isinstance(value, dict):
            for name in ([key] if key is not None else ["est", "mean"]):
                if is_number(value.get(name)):
                    return value[name]
        return None

    def emphasize(self) -> None:
        block = self.spec["rows"].get("block")
        for i, rule in enumerate(self.spec.get("highlight", [])):
            where = f"highlight.{i}"
            col = self.leaf_index(rule["column"], where)
            within = rule.get("within", "table")
            if within == "block" and not block:
                self.error(where, f"{where}: within block needs rows.block")
                continue
            if within not in ("table", "block") and within not in self.keys:
                self.error(where, f"{where}: within must be table, block or one of rows.keys")
                continue
            if col is None:
                continue
            field = block if within == "block" else None if within == "table" else within
            sets: dict[str, list[tuple[int, float]]] = {}
            for r, row in enumerate(self.rows):
                number = self.number(self.values[r][col], rule.get("key"))
                if number is not None:
                    group = hkey(row["key"][field]) if field else ""
                    sets.setdefault(group, []).append((r, number))
            if not sets:
                self.warn(where, f"{where}: column {rule['column']} has no numbers")
            for members in sets.values():
                best_row, _ = (max if rule["best"] == "max" else min)(
                    members, key=lambda m: m[1])
                shown = self.cells[best_row][col]
                self.bold.update((r, col) for r, _ in members if self.cells[r][col] == shown)
        for i, rule in enumerate(self.spec.get("emphasis", [])):
            where = f"emphasis.{i}"
            wanted = rule["row"]
            unknown = [k for k in wanted if k not in self.keys]
            if unknown:
                self.error(where, f"{where}: row uses {', '.join(unknown)}, not in rows.keys "
                                  f"({', '.join(self.keys)})")
                continue
            matches = [r for r, row in enumerate(self.rows)
                       if all(row["key"][k] == v for k, v in wanted.items())]
            col = self.leaf_index(rule["column"], where)
            if len(matches) != 1:
                self.error(where, f"{where}: row {show(wanted)} matches {len(matches)} rows "
                                  f"(stale emphasis; it must match exactly one)")
            elif col is not None:
                self.bold.add((matches[0], col))

    # ------------------------------------------------ caption

    def fill_values(self, key: str, text: str) -> str | None:
        """Text with its {name} data values filled in, or None (an error)."""
        try:
            return text.format_map(self.data.get("values", {}))
        except KeyError as exc:
            self.error(key, f"{key}: unknown value {{{exc.args[0]}}} (data values: "
                            f"{', '.join(self.data.get('values', {})) or 'none'})")
        except (ValueError, IndexError, TypeError, AttributeError) as exc:
            self.error(key, f"{key}: {exc} (write a literal brace as {{{{ or }}}})")
        return None

    def make_caption(self) -> str:
        parts = []
        for key in ("title", "legend"):
            text = " ".join(str(self.spec.get(key, "")).split())
            if text and (text := self.fill_values(key, text)) is not None:
                # The preview shows the title in bold; Word keeps its caption style.
                parts.append(f"[{text}]{{.caption-title}}" if key == "title" else text)
        return " ".join(parts)

    def make_note(self) -> list[str]:
        """The note's paragraphs (a blank line separates them), values filled in."""
        paragraphs = [" ".join(p.split()) for p in re.split(r"\n\s*\n", str(self.spec.get("note", "")))]
        filled = [self.fill_values("note", p) for p in paragraphs if p]
        return [p for p in filled if p is not None]


def build(label: str, spec: dict[str, Any] | None = None,
          shared: dict[str, Any] | None = None) -> Table:
    """Check a spec (from disk, or the given one) against its data and render it."""
    table = Table(label)
    if not table.load(spec, shared):
        return table
    table.rows = table.build_rows()
    if table.errors and not table.leaves:
        return table
    block = table.spec["rows"].get("block")
    if block and any(leaf.field == block and not leaf.pivot for leaf in table.leaves):
        table.warn("rows", f"the block field {block!r} is also a visible column")
    if not table.rows and not table.errors:
        table.error("rows", "the table has no rows (no records, or none left after rows.filter)",
                    "rows")
    table.fill()
    table.emphasize()
    table.caption = table.make_caption()
    table.note = table.make_note()
    table.filled = True
    if not table.errors:
        table.markdown = render_markdown(table)
    return table


# -------------------------------------------------- grid table


def body_items(table: Table) -> list[tuple[str, Any]]:
    """("block", text) and ("row", [cell text]) in order, with bold applied."""
    items: list[tuple[str, Any]] = []
    block = table.spec["rows"].get("block")
    current: str | None = None
    for r, row in enumerate(table.rows):
        if block and hkey(row["key"][block]) != current:
            current = hkey(row["key"][block])
            items.append(("block", f"**{table.mapped(block, row['key'][block])}**"))
        items.append(("row", [f"**{text}**" if (r, c) in table.bold and text else text
                              for c, text in enumerate(table.cells[r])]))
    return items


def joined_cells(table: Table, items: list[tuple[str, Any]]) -> list[list[bool]]:
    """Per item and column: True when the cell continues the one above (merge)."""
    merge = [leaf.merge for leaf in table.leaves]
    joined: list[list[bool]] = []
    for i, (kind, cells) in enumerate(items):
        row = [False] * len(merge)
        above = items[i - 1] if i else None
        if kind == "row" and above and above[0] == "row":
            for c, flag in enumerate(merge):  # stop at the first column that is not joined
                if not (flag and cells[c] == above[1][c]):
                    break
                row[c] = True
        joined.append(row)
    return joined


def column_widths(table: Table, items: list[tuple[str, Any]]) -> list[int]:
    """Content widths per leaf: content length (or `width` weights), padded to the text width."""
    leaves = table.leaves
    content = [display_width(leaf.header) for leaf in leaves]
    for kind, cells in items:
        if kind == "row":
            content = [max(w, display_width(t)) for w, t in zip(content, cells)]
    content = [max(1, w) for w in content]
    weighted = any(leaf.width for leaf in leaves)
    weights = [(leaf.width or 1) if weighted else w for leaf, w in zip(leaves, content)]
    # Pad to sum(w + 3) + n - 2 >= TEXT_WIDTH (see the module docstring).
    target = TEXT_WIDTH + 2 - 4 * len(leaves)
    scale = max(max(c / w for c, w in zip(content, weights)), max(target, 0) / sum(weights))
    widths = [max(c, math.ceil(w * scale - 1e-9)) for c, w in zip(content, weights)]

    def fit(start: int, end: int, text: str) -> None:
        room = sum(widths[start:end]) + 3 * (end - start - 1)
        if display_width(text) > room:
            widths[end - 1] += display_width(text) - room

    for start, end, text in header_groups(leaves):
        fit(start, end, text)
    for kind, text in items:
        if kind == "block":
            fit(0, len(leaves), text)
    return widths


def header_groups(leaves: list[Leaf]) -> list[tuple[int, int, str]]:
    """(first, end, header) of each spanning header."""
    spans: list[tuple[int, int, str]] = []
    for i, leaf in enumerate(leaves):
        if leaf.group and i and leaves[i - 1].group and leaves[i - 1].group[0] == leaf.group[0]:
            spans[-1] = (spans[-1][0], i + 1, spans[-1][2])
        elif leaf.group:
            spans.append((i, i + 1, leaf.group[1]))
    return spans


def rule_line(widths: list[int], blank: list[bool], char: str = "-") -> str:
    """A separator; blank columns continue a cell from the row above."""
    out = "+" if not blank[0] else "|"
    for c, w in enumerate(widths):
        out += " " * (w + 2) if blank[c] else char * (w + 2)
        if c + 1 < len(widths):
            out += "|" if blank[c] and blank[c + 1] else "+"
    return out + ("|" if blank[-1] else "+")


def cell_line(widths: list[int], cells: list[tuple[int, int, str]]) -> str:
    """A text line of (first, end, text) cells covering every column."""
    out = "|"
    for start, end, text in cells:
        room = sum(widths[start:end]) + 3 * (end - start - 1)
        out += " " + pad(text, room) + " |"
    return out


def render_grid(table: Table) -> list[str]:
    leaves = table.leaves
    items = body_items(table)
    joined = joined_cells(table, items)
    widths = column_widths(table, items)
    n = len(leaves)
    none = [False] * n
    lines = [rule_line(widths, none)]
    groups = header_groups(leaves)
    if groups:
        top: list[tuple[int, int, str]] = []
        bottom: list[tuple[int, int, str]] = []
        for i, leaf in enumerate(leaves):
            span = next((g for g in groups if g[0] <= i < g[1]), None)
            if span is None:
                top.append((i, i + 1, leaf.header))
                bottom.append((i, i + 1, ""))
            else:
                if span[0] == i:
                    top.append(span)
                bottom.append((i, i + 1, leaf.header))
        lines.append(cell_line(widths, top))
        lines.append(rule_line(widths, [not leaf.group for leaf in leaves]))
        lines.append(cell_line(widths, bottom))
    else:
        lines.append(cell_line(widths, [(i, i + 1, leaf.header) for i, leaf in enumerate(leaves)]))
    marks = {"left": (":", "="), "right": ("=", ":"), "center": (":", ":")}
    head = "+"
    for leaf, w in zip(leaves, widths):
        left, right = marks[leaf.align]
        head += left + "=" * w + right + "+"
    lines.append(head)
    for i, (kind, cells) in enumerate(items):
        if i:
            lines.append(rule_line(widths, joined[i]))
        if kind == "block":
            lines.append(cell_line(widths, [(0, n, cells)]))
        else:
            lines.append(cell_line(widths, [(c, c + 1, "" if joined[i][c] else text)
                                            for c, text in enumerate(cells)]))
    lines.append(rule_line(widths, none))
    return lines


NOTE_OPEN = '::: {.table-note custom-style="Table Note"}'


def render_markdown(table: Table) -> str:
    lines = [f"<!-- Generated by trazo table render from {rel(table.path)}. Do not edit. -->",
             f"Table: {table.caption} {{#tbl:{table.label}}}", ""]
    note = ["", NOTE_OPEN, "\n\n".join(table.note), ":::"] if table.note else []
    return "\n".join(lines + render_grid(table) + note) + "\n"


# -------------------------------------------------- render model (editor)


def md_inline(text: str) -> str:
    """The inline Markdown of a generated cell or caption as HTML: escaped text,
    **bold**, *italic*, a caption title span and backslash escapes as literal
    characters."""
    kept: list[str] = []

    def keep(match: re.Match[str]) -> str:
        kept.append(match.group(1))
        return f"\x00{len(kept) - 1}\x01"

    text = re.sub(r"\\([^A-Za-z0-9\s])", keep, text)
    text = html.escape(text, quote=False)
    text = re.sub(r"\[(.+?)\]\{\.caption-title\}", r'<span class="caption-title">\1</span>', text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![*\w])\*(?![\s*])(.+?)(?<![\s*])\*(?![*\w])", r"<em>\1</em>", text)
    return re.sub("\x00(\\d+)\x01", lambda m: html.escape(kept[int(m.group(1))]), text)


def group_key(leaf: Leaf) -> tuple[str, str]:
    """(id, kind) of the spanning header above a leaf."""
    assert leaf.group
    return leaf.group[0], "pivot-value" if leaf.pivot else "group"


def render_model(table: Table, hidden: bool = False) -> dict[str, Any] | None:
    """The table as the editor draws it: the spans of render_grid, with each
    cell's leaf id, row key and raw value. With hidden, hidden leaves are
    included (flagged); they never join cells or change the other columns."""
    if not table.filled:
        return None
    leaves = table.all_leaves if hidden else table.leaves
    visible = {leaf.id: i for i, leaf in enumerate(table.leaves)}
    items = body_items(table)
    joined = joined_cells(table, items)
    groups = header_groups(leaves)

    def head(leaf: Leaf, rowspan: int) -> dict[str, Any]:
        return {"text_html": md_inline(leaf.header), "colspan": 1, "rowspan": rowspan,
                "id": leaf.id, "kind": "leaf", "align": leaf.align, "hidden": leaf.hidden}

    top: list[dict[str, Any]] = []
    bottom: list[dict[str, Any]] = []
    for i, leaf in enumerate(leaves):
        span = next((g for g in groups if g[0] <= i < g[1]), None)
        if span is None:
            top.append(head(leaf, 2 if groups else 1))
            continue
        if span[0] == i:
            key, kind = group_key(leaf)
            top.append({"text_html": md_inline(span[2]), "colspan": span[1] - span[0],
                        "rowspan": 1, "id": key, "kind": kind, "align": "center",
                        "hidden": all(x.hidden for x in leaves[span[0]:span[1]])})
        bottom.append(head(leaf, 1))
    body: list[dict[str, Any]] = []
    r = -1
    for i, (kind, cells) in enumerate(items):
        if kind == "block":
            body.append({"block": md_inline(cells)})
            continue
        r += 1
        out = []
        for leaf in leaves:
            if leaf.hidden:
                text, raw = table.hidden_cells[leaf.id][r]
                out.append({"id": leaf.id, "text_html": md_inline(text), "raw": raw, "rowspan": 1,
                            "bold": False, "align": leaf.align, "hidden": True})
                continue
            c = visible[leaf.id]
            if joined[i][c]:
                continue
            span = 1
            while i + span < len(items) and joined[i + span][c]:
                span += 1
            out.append({"id": leaf.id, "text_html": md_inline(cells[c]),
                        "raw": table.values[r][c], "rowspan": span,
                        "bold": (r, c) in table.bold, "align": leaf.align, "hidden": False})
        body.append({"row": r, "key": table.rows[r]["key"], "cells": out})
    return {"header": [top, bottom] if groups else [top], "body": body,
            "caption_html": md_inline(table.caption),
            "note_html": "".join(f"<p>{md_inline(p)}</p>" for p in table.note)}


def stale_output(table: Table) -> list[Issue]:
    path = output_path(table.label)
    if table.markdown is None:
        return []
    if not path.is_file():
        state = "missing"
    elif path.read_text(encoding="utf-8") != table.markdown:
        state = "out of date"
    else:
        return []
    return [issue("error", "stale-output", None,
                  f"{rel(path)} is {state}; run `trazo table render {table.label}`")]


# -------------------------------------------------- patch


def find_column(columns: list[Any], dotted: str) -> tuple[list[Any], int]:
    """(parent list, index) of a column addressed as <id> or <group>.<leaf>."""
    parts = dotted.split(".")
    siblings = columns
    for depth, part in enumerate(parts):
        match = [i for i, c in enumerate(siblings) if isinstance(c, dict) and c.get("id") == part]
        if not match:
            raise TableError(f"no column {dotted!r}" + (
                " (pivot leaves are addressed as <pivot>.<leaf>; drop the value)"
                if len(parts) == 3 else ""))
        if depth == len(parts) - 1:
            return siblings, match[0]
        siblings = siblings[match[0]].get("columns")
        if not isinstance(siblings, list):
            raise TableError(f"column {'.'.join(parts[:depth + 1])!r} has no child columns")
    raise TableError(f"no column {dotted!r}")


def set_key(target: Any, parts: list[str], value: Any, dotted: str) -> None:
    for part in parts[:-1]:
        if isinstance(target, list) and part.isdigit() and int(part) < len(target):
            target = target[int(part)]
        elif isinstance(target, dict):
            target = target.setdefault(part, {})
        else:
            raise TableError(f"cannot follow {dotted!r} at {part!r}")
    last = parts[-1]
    if isinstance(target, list) and last.isdigit() and int(last) < len(target):
        if value is None:
            target.pop(int(last))
        else:
            target[int(last)] = value
    elif isinstance(target, dict):
        if value is None:
            target.pop(last, None)
        else:
            target[last] = value
    else:
        raise TableError(f"cannot set {dotted!r}")


def apply_op(spec: dict[str, Any], op: dict[str, Any]) -> str:
    kind = op.get("op")
    columns = spec.setdefault("columns", [])
    if kind == "set":
        path = op.get("path")
        if not isinstance(path, str) or not path or "value" not in op:
            raise TableError("set needs a 'path' and a 'value' (null deletes)")
        parts = path.split(".")
        if parts[0] == "columns" and len(parts) > 1:
            siblings, at, rest = columns, None, parts[1:]
            while rest and isinstance(siblings, list) and any(
                    isinstance(c, dict) and c.get("id") == rest[0] for c in siblings):
                at = next(c for c in siblings if isinstance(c, dict) and c.get("id") == rest[0])
                siblings, rest = at.get("columns"), rest[1:]
            if at is None:
                raise TableError(f"set {path}: no column {parts[1]!r}")
            if not rest:
                raise TableError(f"set {path}: name a key of the column (e.g. {path}.header)")
            if rest == ["id"]:
                raise TableError("column ids are stable; remove and add the column to rename it")
            set_key(at, rest, op["value"], path)
        else:
            set_key(spec, parts, op["value"], path)
        return f"set {path}"
    if kind == "add":
        into, item = op.get("into"), op.get("item")
        if not isinstance(into, str) or not isinstance(item, dict):
            raise TableError("add needs 'into' (columns or columns.<id>) and an 'item' object")
        if into == "columns":
            siblings = columns
        elif into.startswith("columns."):
            parent_list, at = find_column(columns, into[len("columns."):])
            siblings = parent_list[at].get("columns")
            if not isinstance(siblings, list):
                raise TableError(f"add: {into} is a leaf; add into a group or pivot")
        else:
            raise TableError("add: 'into' must be columns or columns.<group or pivot id>")
        at = len(siblings)
        if op.get("before") is not None:
            short = str(op["before"]).rsplit(".", 1)[-1]
            match = [i for i, c in enumerate(siblings) if c.get("id") == short]
            if not match:
                raise TableError(f"add: no column {op['before']!r} in {into}")
            at = match[0]
        siblings.insert(at, item)
        return f"add {item.get('id')}"
    if kind == "remove":
        column = str(op.get("column"))
        siblings, at = find_column(columns, column)
        siblings.pop(at)
        return f"remove {column}"
    if kind == "move":
        column = str(op.get("column"))
        siblings, at = find_column(columns, column)
        item = siblings.pop(at)
        before = op.get("before")
        if before is None:
            siblings.append(item)
        else:
            short = str(before).rsplit(".", 1)[-1]
            match = [i for i, c in enumerate(siblings) if c.get("id") == short]
            if not match or ("." in str(before) and str(before).rsplit(".", 1)[0]
                             != column.rpartition(".")[0]):
                raise TableError(f"move: {before!r} is not a sibling of {column!r}")
            siblings.insert(match[0], item)
        return f"move {column}"
    raise TableError(f"unknown op {kind!r} (use set, add, remove or move)")


class TableConflict(TableError):
    """A file or comment changed since it was read (undo refused; HTTP 409)."""


HISTORY = History(HISTORY_PATH, ("file", "comments"), TableConflict)


class SpecChanged(TableConflict):
    """The spec changed since the caller read it (sha256 is the current one)."""

    def __init__(self, message: str, sha256: str | None) -> None:
        super().__init__(message)
        self.sha256 = sha256


class SpecInvalid(TableError):
    """The operations would leave errors; nothing was written."""

    def __init__(self, message: str, issues: list[Issue]) -> None:
        super().__init__(message)
        self.issues = issues


def sha256_of(path: Path) -> str | None:
    return file_digest(path) if path.is_file() else None


def as_ops(ops: Any) -> list[dict[str, Any]]:
    if isinstance(ops, dict):
        ops = [ops]
    if not isinstance(ops, list) or not all(isinstance(o, dict) for o in ops):
        raise TableError("operations must be a JSON object or a list of objects")
    return ops


def blocking(table: Table) -> list[Issue]:
    """Errors that refuse a change (stale or missing data does not)."""
    return [i for i in table.errors if i["code"] not in ("stale-data", "data")]


@exclusive  # a build renders the same files
def render_one(label: str) -> Table:
    """Build a table and write its Markdown when it has no errors."""
    table = build(label)
    if table.markdown is not None:
        path = output_path(label)
        if not path.is_file() or path.read_text(encoding="utf-8") != table.markdown:
            write_atomic(path, table.markdown.encode("utf-8"))
    return table


def render_all() -> list[Table]:
    """render_one for every spec (used after shared edits and by the preview)."""
    return [render_one(label) for label in all_labels()]


def patch(label: str, ops: Any, base_sha256: str | None = None) -> list[str]:
    """Apply operations all-or-nothing, record history and render the table.

    Refuses (SpecInvalid) a result with errors other than stale data, and
    (SpecChanged) a spec whose sha256 is not base_sha256 when one is given.
    The CLI and the editor's POST /__table both call this.
    """
    ops = as_ops(ops)
    path = spec_path(label)
    with review_lock():
        spec, before = read_json(path, "table spec")
        current = hashlib.sha256(before).hexdigest()
        if base_sha256 is not None and base_sha256 != current:
            raise SpecChanged(f"{rel(path)} changed since it was loaded", current)
        if not isinstance(spec, dict):
            raise TableError(f"{rel(path)}: the top level must be a JSON object")
        summary = [apply_op(spec, op) for op in ops]
        problems = blocking(build(label, spec))
        if problems:
            raise SpecInvalid("patch not applied: the result has errors", problems)
        data = dump_spec(spec)
        if data == before:
            return []
        write_atomic(path, data)
        HISTORY.record(HISTORY.file_entry(path, before, data,
                                          f"patch {label}: {'; '.join(summary)}"[:300]))
    render_one(label)
    return summary


def patch_shared(ops: Any, base_sha256: str | None = None,
                 label: str | None = None) -> list[str]:
    """Set ops on formats.* and maps.* of _shared.json, then render every table.

    Refuses (SpecInvalid) when a table without errors before would get some.
    `label` names the table whose editor made the change (its undo reverts it).
    """
    ops = as_ops(ops)
    with review_lock():
        before = SHARED_PATH.read_bytes() if SHARED_PATH.is_file() else None
        current = hashlib.sha256(before).hexdigest() if before is not None else None
        if base_sha256 is not None and base_sha256 != current:
            raise SpecChanged(f"{rel(SHARED_PATH)} changed since it was loaded", current)
        shared = load_shared() if before is not None else {}
        summary = []
        for op in ops:
            path = op.get("path")
            if (op.get("op") != "set" or not isinstance(path, str) or "value" not in op
                    or path.split(".")[0] not in ("formats", "maps") or path.count(".") < 1):
                raise TableError("shared patches are set ops on formats.<name> or "
                                 "maps.<field>.<value>")
            set_key(shared, path.split("."), op["value"], path)
            summary.append(f"set {path}")
        problems = Checker().shared(shared)
        if problems:
            raise SpecInvalid("shared patch not applied: _shared.json would be invalid", problems)
        for name in all_labels():
            if blocking(build(name)):
                continue
            new = blocking(build(name, shared=shared))
            if new:
                raise SpecInvalid(f"shared patch not applied: {name} would get errors",
                                  [{**i, "message": f"{name}: {i['message']}"} for i in new])
        data = dump_spec(shared)
        if data == before:
            return []
        write_atomic(SHARED_PATH, data)
        entry = HISTORY.file_entry(SHARED_PATH, before, data,
                                   f"patch shared: {'; '.join(summary)}"[:300])
        if label:
            entry["table"] = label
        HISTORY.record(entry)
    render_all()
    return summary


def undo(label: str | None = None) -> str:
    """Revert the newest history entry, or the newest of one table (its spec,
    or a shared edit made from its editor)."""
    if label is None:
        message = HISTORY.undo(lambda entry: COMMENTS.revert(entry, TableConflict))
    else:
        spec, shared = rel(spec_path(label)), rel(SHARED_PATH)
        message = HISTORY.undo(match=lambda e: e.get("kind") == "file" and (
            e.get("file") == spec or (e.get("file") == shared and e.get("table") == label)))
    if message != "nothing to undo":
        render_all()  # cheap; keeps every output in step with its spec
    return message


# -------------------------------------------------- format usage


def format_usage() -> dict[str, dict[str, Any]]:
    """For each format: {shared, tables: [{label, leaves, local}], prose: [{file, line, key}]}.

    `local` is true when the table defines the format itself (so editing the
    shared one does not change it). Prose is every {{v:key|name}} in the
    documents' Markdown.
    """
    from .expand import PLACEHOLDER

    shared = load_shared().get("formats", {})
    usage: dict[str, dict[str, Any]] = {
        name: {"shared": True, "tables": [], "prose": []} for name in shared}
    for label in all_labels():
        table = build(label)
        local = table.spec.get("formats", {}) if isinstance(table.spec, dict) else {}
        used: dict[str, list[str]] = {}
        for leaf in table.all_leaves:
            if leaf.format:
                used.setdefault(leaf.format, []).append(leaf.id)
        for name, leaves in used.items():
            entry = usage.setdefault(name, {"shared": name in shared, "tables": [], "prose": []})
            entry["tables"].append({"label": label, "leaves": leaves, "local": name in local})
    for document in PROJECT.documents:
        for path in PROJECT.document_files(document):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for key, name in PLACEHOLDER.findall(line):
                    if name:
                        usage.setdefault(name, {"shared": name in shared, "tables": [],
                                                "prose": []})["prose"].append(
                            {"file": rel(path), "line": number, "key": key})
    return usage


# -------------------------------------------------- comments


def comment_targets(request: dict[str, Any]) -> dict[str, Any]:
    """The table and the optional target {column[, row]} of a new comment."""
    label = request.get("table")
    if not isinstance(label, str) or not LABEL_RE.match(label) or not spec_path(label).is_file():
        raise NotFound(f"no table spec named {label!r}")
    fields: dict[str, Any] = {"table": label}
    target = request.get("target")
    if target is not None:
        if not (isinstance(target, dict) and isinstance(target.get("column"), str)
                and target["column"] and set(target) <= {"column", "row"}
                and isinstance(target.get("row", {}), dict)):
            raise RequestError('target must be {"column": leaf id} or '
                               '{"column": leaf id, "row": {key: value}}')
        fields["target"] = target
    return fields


def comment_place(comment: dict[str, Any]) -> str:
    target = comment.get("target")
    if not isinstance(target, dict):
        return ""
    row = target.get("row")
    rows = " · ".join(str(v) for v in row.values()) if isinstance(row, dict) and row else ""
    return f"{target.get('column')}" + (f" · {rows}" if rows else "")


COMMENTS = CommentStore(COMMENTS_PATH, "table", HISTORY, comment_targets, comment_place)


# -------------------------------------------------- commands


def format_issue(item: Issue) -> str:
    where = f" [{item['id']}]" if item.get("id") else ""
    return f"  {item['level']}: {item['code']}{where}: {item['message']}"


def report(table: Table) -> None:
    for item in table.issues:
        print(format_issue(item))
    status = "FAIL" if table.errors else "ok"
    print(f"{table.label}: {status} ({len(table.errors)} error(s), "
          f"{len(table.warnings)} warning(s))")


def labels_of(names: list[str]) -> list[str]:
    return names or all_labels()


def cmd_check(names: list[str], as_json: bool, outputs: bool) -> int:
    tables = []
    for label in labels_of(names):
        table = build(label)
        if outputs:
            table.issues += stale_output(table)
        tables.append(table)
    if as_json:
        payload = [{"label": t.label, "file": rel(t.path), "ok": not t.errors,
                    "errors": len(t.errors), "warnings": len(t.warnings), "issues": t.issues}
                   for t in tables]
        print(json.dumps(payload[0] if len(names) == 1 else payload, indent=2, ensure_ascii=False))
    else:
        if not tables:
            print(f"no tables in {rel(SPEC_DIR)}")
        for table in tables:
            report(table)
    return 1 if any(t.errors for t in tables) else 0


@exclusive
def cmd_render(names: list[str]) -> int:
    failed = False
    for label in labels_of(names):
        table = build(label)
        if table.markdown is None:
            report(table)
            print(f"{label}: not rendered (fix the errors)")
            failed = True
            continue
        for item in table.issues:
            print(format_issue(item))
        write_atomic(output_path(label), table.markdown.encode("utf-8"))
        print(f"rendered {rel(output_path(label))}")
    return 1 if failed else 0


def cmd_new(label: str, data_name: str) -> None:
    path = spec_path(label)
    if path.exists():
        raise TableError(f"{rel(path)} already exists")
    if not NAME_RE.match(data_name):
        raise TableError(f"invalid data name {data_name!r}")
    data, _ = read_json(data_path(data_name), "table data")
    problems = Checker().data(data, data_name)
    if problems:
        raise TableError(problems[0]["message"])
    fields: dict[str, list[Any]] = {}
    for record in data["records"]:
        for field, value in record.items():
            fields.setdefault(field, []).append(value)
    keys = ([f for f, vs in fields.items() if any(isinstance(v, str) for v in vs)]
            or list(fields)[:1])
    columns, formats = [], {}
    for field, values in fields.items():
        col: dict[str, Any] = {"id": re.sub(r"[^A-Za-z0-9_-]", "_", field), "field": field,
                               "header": field}
        sample = next((v for v in values if v is not None), None)
        if isinstance(sample, dict):
            col["format"] = field
            formats[field] = " / ".join(f"{{{k}:.2f}}" for k in sample)
        elif isinstance(sample, float) or any(isinstance(v, float) for v in values):
            col["format"] = "dec2"
            formats["dec2"] = "{value:.2f}"
        if is_number(sample) or isinstance(sample, dict):
            col["align"] = "right"
        columns.append(col)
    spec: dict[str, Any] = {"schema_version": 1, "data": data_name,
                            "title": f"TODO: title of {label}.", "rows": {"keys": keys},
                            "columns": columns}
    if formats:
        spec["formats"] = formats
    HISTORY.write_file(path, dump_spec(spec), f"new {label}")
    print(f"created {rel(path)} ({len(columns)} column(s), rows by {', '.join(keys)})")
    print(f"next: trazo table describe {label}")


def cmd_describe(label: str) -> int:
    table = build(label)
    spec = table.spec
    status = "FAIL" if table.errors else "ok"
    print(f"{label}: {rel(table.path)} -> {rel(output_path(label))} ({status}: "
          f"{len(table.errors)} error(s), {len(table.warnings)} warning(s))")
    if not table.fields:
        report(table)
        return 1
    data = table.data
    stale = any(i["code"] == "stale-data" for i in table.issues)
    print(f"data {spec['data']}: {rel(data_path(spec['data']))}, {len(data['records'])} record(s), "
          f"generator {data.get('generator', '?')}, inputs {'STALE' if stale else 'current'}")
    print("fields:")
    width = max(map(len, table.fields))
    for field, values in table.fields.items():
        kinds = sorted({"null" if v is None else "string" if isinstance(v, str) else
                        "int" if isinstance(v, int) else "float" if is_number(v) else
                        "composite {" + ", ".join(v) + "}" for v in values})
        line = f"  {field:<{width}}  {' | '.join(kinds)}"
        strings = list(dict.fromkeys(v for v in values if isinstance(v, str)))
        if strings:
            more = f", ... ({len(strings)} distinct)" if len(strings) > DISTINCT_SHOWN else ""
            line += ": " + ", ".join(strings[:DISTINCT_SHOWN]) + more
        print(line)
    if data.get("values"):
        print("values (for {name} in title and legend): "
              + ", ".join(f"{k} = {show(v)}" for k, v in data["values"].items()))
    rows = spec["rows"]
    print(f"rows: {len(table.rows)}, one per {' x '.join(rows['keys'])}"
          + (f"; block rows by {rows['block']}" if rows.get("block") else ""))
    for part in ("order", "filter"):
        for field, values in rows.get(part, {}).items():
            print(f"  {part} {field}: {', '.join(map(show, values))}")
    print("columns (patch paths: columns.<id>[.<leaf id>].<key>; leaf ids for highlight "
          "and emphasis):")
    used: dict[str, str] = {}

    def leaf_line(col: dict[str, Any], full: str, indent: str) -> None:
        extras = [f"field {col.get('field')}"]
        if col.get("format"):
            extras.append(f"format {col['format']}")
            used[col["format"]] = table.formats.get(col["format"], "(unknown)")
        extras += [k for k in ("merge", "hidden") if col.get(k)]
        extras += [f"{k} {col[k]}" for k in ("align", "width") if k in col]
        print(f"{indent}{full}  {show(col.get('header'))}  ({', '.join(extras)})")

    for col in spec["columns"]:
        if "pivot" in col:
            values = col.get("values") or list(dict.fromkeys(
                v for v in table.fields.get(col["pivot"], []) if v is not None))
            print(f"  {col['id']}  pivot on {col['pivot']}: {', '.join(map(show, values))} "
                  f"(leaf ids {col['id']}.<value>.<leaf>)")
            for child in col["columns"]:
                leaf_line(child, f"{col['id']}.{child['id']}", "    ")
        elif "columns" in col:
            print(f"  {col['id']}  group {show(col['header'])}")
            for child in col["columns"]:
                leaf_line(child, f"{col['id']}.{child['id']}", "    ")
        else:
            leaf_line(col, col["id"], "  ")
    if used:
        print("formats in use: " + "; ".join(f"{k} = {show(v)}" for k, v in used.items()))
    unused = sorted(set(table.formats) - set(used))
    if unused:
        print("other formats: " + ", ".join(unused))
    for i, rule in enumerate(spec.get("highlight", [])):
        print(f"highlight.{i}: {rule['best']} of {rule['column']} within "
              f"{rule.get('within', 'table')}")
    for i, rule in enumerate(spec.get("emphasis", [])):
        print(f"emphasis.{i}: {rule['column']} in row {show(rule['row'])}")
    if table.issues:
        print("issues:")
        for item in table.issues:
            print(format_issue(item))
    if table.markdown:
        print("rendered:")
        print(table.markdown, end="")
    return 0


def cmd_patch(label: str | None, ops_file: str, shared: bool) -> int:
    text = sys.stdin.read() if ops_file == "-" else Path(ops_file).read_text(encoding="utf-8")
    try:
        ops = json.loads(text)
    except ValueError as exc:
        raise TableError(f"operations are not valid JSON: {exc}") from exc
    try:
        summary = patch_shared(ops) if shared else patch(str(label), ops)
    except SpecInvalid as exc:
        for item in exc.issues:
            print(format_issue(item))
        raise
    if not summary:
        print("no change")
        return 0
    target = rel(SHARED_PATH) if shared else rel(spec_path(str(label)))
    print(f"patched {target}: {'; '.join(summary)}")
    print("rendered every table" if shared else f"rendered {rel(output_path(str(label)))}")
    return 0


def status_of(table: Table) -> str:
    if any(i["code"] not in ("stale-data", "stale-output") for i in table.errors):
        return "error"
    if table.errors or stale_output(table):
        return "stale"
    return "ok"


def cmd_list(as_json: bool) -> int:
    entries = []
    for label in all_labels():
        table = build(label)
        spec = table.spec if isinstance(table.spec, dict) else {}
        entries.append({"label": label, "data": spec.get("data"),
                        "status": status_of(table)})
    if as_json:
        print(json.dumps(entries, indent=2, ensure_ascii=False))
        return 0
    if not entries:
        print(f"no tables in {rel(SPEC_DIR)}")
    for entry in entries:
        print(f"{entry['label']}  data {entry['data']}  {entry['status']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="trazo table",
        description=__doc__.splitlines()[0],
        epilog="\n".join(__doc__.splitlines()[1:]),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("new", help="scaffold a spec with one column per data field")
    p.add_argument("label")
    p.add_argument("--data", required=True, help="data file name in the table data folder")
    for name, about in (("render", "write the Markdown table (refuses on check errors)"),
                        ("check", "check spec, data and formats (non-zero exit on error)")):
        p = sub.add_parser(name, help=about)
        p.add_argument("labels", nargs="*", metavar="LABEL")
        p.add_argument("--all", action="store_true", help="every table")
        if name == "check":
            p.add_argument("--json", action="store_true", help="machine-readable output")
            p.add_argument("--outputs", action="store_true",
                           help="also fail when a rendered file is missing or out of date")
    p = sub.add_parser("describe", help="fields, values, columns, rows and the rendered table")
    p.add_argument("label")
    p = sub.add_parser("patch", help="apply set/add/remove/move operations")
    p.add_argument("args", nargs="+", metavar="LABEL OPS",
                   help='LABEL and a JSON file with the operations ("-" for stdin); '
                        "with --shared only the file")
    p.add_argument("--shared", action="store_true",
                   help="set formats.* and maps.* in _shared.json, then render every table")
    p = sub.add_parser("undo", help="revert the last table spec, shared or comment change")
    p.add_argument("label", nargs="?", help="only this table's last spec change")
    p = sub.add_parser("comments", help="list open table comments")
    p.add_argument("label", nargs="?", help="only this table")
    p.add_argument("--all", action="store_true", help="every state, with threads")
    p = sub.add_parser("resolve", help="reply to a table comment and set it to review")
    p.add_argument("id")
    p.add_argument("reply")
    p = sub.add_parser("list", help="every table with its data and status")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    try:
        if args.command == "new":
            cmd_new(args.label, args.data)
        elif args.command in ("render", "check"):
            if bool(args.labels) == args.all:
                parser.error(f"{args.command} needs LABEL(s) or --all")
            for label in args.labels:
                spec_path(label)
            if args.command == "render":
                return cmd_render(args.labels)
            return cmd_check(args.labels, args.json, args.outputs)
        elif args.command == "describe":
            return cmd_describe(args.label)
        elif args.command == "patch":
            if len(args.args) != (1 if args.shared else 2):
                parser.error("patch needs LABEL OPS, or --shared OPS")
            if args.shared:
                return cmd_patch(None, args.args[0], True)
            return cmd_patch(args.args[0], args.args[1], False)
        elif args.command == "undo":
            if args.label:
                spec_path(args.label)
            print(undo(args.label))
        elif args.command == "comments":
            COMMENTS.print(args.label, args.all)
        elif args.command == "resolve":
            COMMENTS.resolve(args.id, args.reply, TableError)
            print(f"[{args.id}] review: reply added")
        elif args.command == "list":
            return cmd_list(args.json)
    except (TableError, StoreError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
