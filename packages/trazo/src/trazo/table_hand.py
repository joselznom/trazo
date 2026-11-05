"""Tables typed by hand: the cells live in the spec, with no data and no script.

A spec with `body` instead of `data` and `rows`; trazo.table renders it like any
other table (Markdown, numbering, exports, history, comments):

    columns: leaf {id, header, align, merge, width, hidden}; group {id, header,
             columns: [leaves]}. Leaf ids are unique in the whole table.
    body:    {"id": "r1", "cells": {<leaf id>: text}} for a row (a missing cell is
             empty), {"id": "b1", "block": text} for a full-width bold row
    emphasis, marks: as in any spec, with the row as {"row": <row id>}

Text is kept and published as typed; nothing is computed or reformatted. Bold,
italic, ^superscript^, ~subscript~ and citations ([@key]) work; every other
Markdown character is literal, and a cell is one paragraph.

Patch operations, besides set, add, remove and move on columns:
    {"op": "cell", "row": ID, "column": LEAF, "text": "..."}   ("" empties it)
    {"op": "block", "row": ID, "text": "..."}
    {"op": "row-add", "id": ID, "before": ID | null, "block": text?}
    {"op": "row-remove", "row": ID}          (with its bold and marks)
    {"op": "row-move", "row": ID, "before": ID | null}
    {"op": "paste", "row": ID, "column": LEAF, "rows": [[text]]}   fills from that
        cell; rows and columns are added when the range does not fit, and a new
        column of numbers is aligned right
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from .table import (
    GROUP_KEYS,
    ID_RE,
    LABEL_RE,
    Checker,
    Table,
    TableError,
    all_labels,
)

SPEC_KEYS = {"schema_version", "title", "legend", "note", "columns", "body", "emphasis", "marks"}
LEAF_KEYS = {"id", "header", "align", "merge", "width", "hidden"}
OPS = ("cell", "block", "row-add", "row-remove", "row-move", "paste")
# Literal in a cell: all of table.MD_SPECIAL but *, ^ and ~, whose pairs Pandoc
# reads as bold, italic, superscript and subscript. Pairing them here would be a
# second, different reading of the same rules.
MD_TEXT = re.compile(r"([\\`_{}\[\]<>|#$@&])")
STRIKEOUT = re.compile(r"~{2,}")
CITATION = re.compile(r"(\[[^\[\]\n]*?(?<![\w.])-?@[\w:.#$%&+?<>~/-]+[^\[\]\n]*\](?!\())")
# The start of a list or a div in a table cell: (1) a. i) : :::
BLOCK_START = re.compile(r"^(\(?\w+)([.)])(?=\s)")
NUMBER = re.compile(r"^[-+−]?\d[\d.,]*%?$")


def is_hand(spec: Any) -> bool:
    return isinstance(spec, dict) and "body" in spec


def one_line(text: str) -> str:
    return " ".join(text.split())


def put(cells: dict[str, str], column: str, text: str) -> None:
    """Set a cell; blank text empties it."""
    if text.strip():
        cells[column] = one_line(text)
    else:
        cells.pop(column, None)


def is_rows(rows: Any) -> bool:
    """A non-empty list of lists of text: a pasted range."""
    return isinstance(rows, list) and bool(rows) and all(
        isinstance(r, list) and all(isinstance(t, str) for t in r) for r in rows)


def literal(text: str) -> str:
    return STRIKEOUT.sub(lambda run: run[0].replace("~", r"\~"), MD_TEXT.sub(r"\\\1", text))


def cell_markdown(text: str) -> str:
    """A typed cell as one line of Markdown: bold, italic, superscript, subscript and
    citations stay active, the rest is literal, and it cannot start a block."""
    text = one_line(text)
    if not text.strip("* "):  # asterisks alone would be a horizontal rule
        return text.replace("*", r"\*")
    parts = CITATION.split(text)
    out = "".join(part if i % 2 else literal(part) for i, part in enumerate(parts))
    if out[:1] in ("-", "+", ":") or out[:2] == "* ":
        out = "\\" + out
    return BLOCK_START.sub(r"\1\\\2", out)


def leaves_of(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """The leaf columns of a spec, groups opened, in order."""
    out: list[dict[str, Any]] = []
    for col in spec.get("columns", []):
        if isinstance(col, dict):
            out += [c for c in col.get("columns", [col]) if isinstance(c, dict)]
    return out


# -------------------------------------------------- schema


def check_columns(checker: Checker, columns: Any) -> set[str]:
    """Check the columns; the ids of the leaves."""
    if not isinstance(columns, list) or not columns:
        checker.error("columns", "columns must be a non-empty list")
        return set()
    leaves: list[dict[str, Any]] = []

    def leaf(col: Any, where: str) -> None:
        if not isinstance(col, dict) or not isinstance(col.get("id"), str) or not ID_RE.match(col["id"]):
            checker.error(where, f"{where}: a column id must be letters, digits, - and _ (no dots)")
            return
        checker.leaf(col, where, LEAF_KEYS, ("id", "header"))
        leaves.append(col)

    for col in columns:
        name = col.get("id") if isinstance(col, dict) else None
        where = f"columns.{name}" if name else "columns"
        if not isinstance(col, dict) or "columns" not in col:
            leaf(col, where)
            continue
        checker.keys(col, GROUP_KEYS, where, ("id", "header", "columns"))
        checker.string(col, "header", where)
        if not isinstance(col["columns"], list) or not col["columns"]:
            checker.error(where, f"{where}.columns must be a non-empty list")
            continue
        for child in col["columns"]:
            leaf(child, where)
    checker.unique(leaves, "columns")
    return {c["id"] for c in leaves}


def check_body(checker: Checker, body: Any, leaves: set[str]) -> None:
    if not isinstance(body, list):
        checker.error("body", "body must be a list of rows")
        return
    seen: set[str] = set()
    for i, item in enumerate(body):
        where = f"body.{i}"
        if not checker.keys(item, {"id", "cells", "block"}, where, ("id",)):
            continue
        name = item.get("id")
        if not isinstance(name, str) or not ID_RE.match(name):
            checker.error(where, f"{where}.id must be letters, digits, - and _")
        elif name in seen:
            checker.error(where, f"{where}: duplicate row id {name!r}")
        else:
            seen.add(name)
        if "block" in item:
            checker.string(item, "block", where)
        cells = item.get("cells", {})
        if not isinstance(cells, dict):
            checker.error(where, f"{where}.cells must be an object")
            continue
        for column, text in cells.items():
            if column not in leaves:
                checker.error(where, f"{where}: unknown column {column!r}")
            elif not isinstance(text, str):
                checker.error(where, f"{where}.cells.{column} must be text")


def check(checker: Checker, spec: dict[str, Any]) -> list[dict[str, Any]]:
    if not checker.keys(spec, SPEC_KEYS, "spec", ("schema_version", "title", "columns", "body")):
        return checker.issues
    checker.version(spec, "spec", True)
    # A legend alone is enough: a Markdown table's caption stays as it was.
    checker.string(spec, "title", "spec", empty=bool(spec.get("legend")))
    checker.string(spec, "legend", "spec")
    checker.string(spec, "note", "spec")
    check_body(checker, spec.get("body"), check_columns(checker, spec.get("columns")))
    checker.rules(spec)
    return checker.issues


# -------------------------------------------------- build


def fill(table: Table) -> None:
    """The rows and cells of a table typed by hand (Table.build_rows and fill, for data)."""
    table.keys = ["row"]
    table.expand_columns([])
    for leaf in table.all_leaves:
        leaf.header = cell_markdown(leaf.header)
        if leaf.group:
            leaf.group = (leaf.group[0], cell_markdown(leaf.group[1]))
    for item in table.spec["body"]:
        if "block" in item:
            table.blocks.setdefault(len(table.rows), []).append(
                {**item, "markdown": cell_markdown(item["block"])})
            continue
        table.rows.append({"key": {"row": item["id"]}, "records": []})
        texts, raws = [], []
        for leaf in table.all_leaves:
            raw = item.get("cells", {}).get(leaf.spec["id"], "")
            if leaf.hidden:
                table.hidden_cells.setdefault(leaf.id, []).append((cell_markdown(raw), raw))
            else:
                texts.append(cell_markdown(raw))
                raws.append(raw)
        table.cells.append(texts)
        table.values.append(raws)
    if not table.rows:
        table.error("rows", "the table has no rows", "rows")


# -------------------------------------------------- patch


def find_row(spec: dict[str, Any], row: Any) -> int:
    for i, item in enumerate(spec["body"]):
        if item.get("id") == row:
            return i
    raise TableError(f"no row {row!r}")


def insert_at(spec: dict[str, Any], before: Any) -> int:
    return len(spec["body"]) if before is None else find_row(spec, before)


def fresh_id(prefix: str, taken: set[str]) -> str:
    n = 1
    while f"{prefix}{n}" in taken:
        n += 1
    taken.add(f"{prefix}{n}")
    return f"{prefix}{n}"


def numbers_only(texts: list[str]) -> bool:
    filled = [t.strip() for t in texts if t.strip()]
    return bool(filled) and all(NUMBER.match(t) for t in filled)


def paste(spec: dict[str, Any], op: dict[str, Any]) -> None:
    if not is_rows(op.get("rows")):
        raise TableError("paste needs 'rows', a list of lists of text")
    rows: list[list[str]] = op["rows"]
    leaves = [c for c in leaves_of(spec) if not c.get("hidden")]
    ids = [c["id"] for c in leaves]
    if op.get("column") not in ids:
        raise TableError(f"no column {op.get('column')!r}")
    start = ids.index(op["column"])
    data = [item for item in spec["body"][find_row(spec, op.get("row")):] if "block" not in item]
    taken = {c["id"] for c in leaves_of(spec)}
    width = max(map(len, rows))
    added = []
    while start + width > len(ids):
        col = {"id": fresh_id("c", taken), "header": f"Column {len(ids) + 1}"}
        spec["columns"].append(col)
        added.append(col)
        ids.append(col["id"])
    rows_taken = {item["id"] for item in spec["body"]}
    while len(data) < len(rows):
        row: dict[str, Any] = {"id": fresh_id("r", rows_taken), "cells": {}}
        spec["body"].append(row)
        data.append(row)
    for item, texts in zip(data, rows):
        cells = item.setdefault("cells", {})
        for offset, text in enumerate(texts):
            put(cells, ids[start + offset], text)
    for col in added:
        if numbers_only([item.get("cells", {}).get(col["id"], "") for item in spec["body"]]):
            col["align"] = "right"


def apply_op(spec: dict[str, Any], op: dict[str, Any]) -> str:
    kind = op.get("op")
    if not is_hand(spec):
        raise TableError(f"{kind} works on a table typed by hand; this one comes from its data")
    if kind == "cell":
        item = spec["body"][find_row(spec, op.get("row"))]
        column, text = op.get("column"), op.get("text")
        if not isinstance(column, str) or not isinstance(text, str):
            raise TableError("cell needs 'row', 'column' and 'text'")
        put(item.setdefault("cells", {}), column, text)
        return f"cell {op['row']} {column}"
    if kind == "block":
        if not isinstance(op.get("text"), str):
            raise TableError("block needs 'row' and 'text'")
        spec["body"][find_row(spec, op.get("row"))]["block"] = one_line(op["text"])
        return f"block {op['row']}"
    if kind == "row-add":
        fresh: dict[str, Any] = {"id": op.get("id")}
        if isinstance(op.get("block"), str):
            fresh["block"] = op["block"]
        else:
            fresh["cells"] = {}
        spec["body"].insert(insert_at(spec, op.get("before")), fresh)
        return f"add row {op.get('id')}"
    if kind == "row-remove":
        spec["body"].pop(find_row(spec, op.get("row")))
        for key in ("emphasis", "marks"):
            kept = [r for r in spec.get(key, []) if r.get("row") != {"row": op["row"]}]
            if kept:
                spec[key] = kept
            else:
                spec.pop(key, None)
        return f"remove row {op['row']}"
    if kind == "row-move":
        moved = spec["body"].pop(find_row(spec, op.get("row")))
        spec["body"].insert(insert_at(spec, op.get("before")), moved)
        return f"move row {op['row']}"
    paste(spec, op)
    return "paste"


def prune(spec: dict[str, Any]) -> None:
    """Drop the cells of columns that are gone."""
    leaves = {c.get("id") for c in leaves_of(spec)}
    for item in spec["body"]:
        if isinstance(item.get("cells"), dict):
            item["cells"] = {k: v for k, v in item["cells"].items() if k in leaves}


# -------------------------------------------------- new tables


def include_line(label: str) -> str:
    """The line of a document that shows a table."""
    from .expand import INCLUDE_PREFIX
    from .table import output_path, rel
    return f"{INCLUDE_PREFIX}{rel(output_path(label))}"


def new_spec(title: str, rows: list[list[str]] | None) -> dict[str, Any]:
    """A spec of three empty columns and rows, or of pasted rows (the first is the header)."""
    rows = rows or [["Column 1", "Column 2", "Column 3"], [], [], []]
    width = max(map(len, rows))
    ids = [f"c{i + 1}" for i in range(width)]
    columns: list[dict[str, Any]] = []
    for i, name in enumerate(ids):
        header = one_line(rows[0][i]) if i < len(rows[0]) else ""
        columns.append({"id": name, "header": header or f"Column {i + 1}"})
        if numbers_only([r[i] for r in rows[1:] if i < len(r)]):
            columns[-1]["align"] = "right"
    body = [{"id": f"r{n + 1}", "cells": {ids[i]: one_line(t) for i, t in enumerate(r) if t.strip()}}
            for n, r in enumerate(rows[1:] or [[]])]
    return {"schema_version": 1, "title": title, "columns": columns, "body": body}


def free_label(title: str) -> str:
    """A new table's label, from its title: also its cross-reference."""
    plain = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    base = re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")[:40].rstrip("-") or "table"
    labels, label, n = set(all_labels()), base, 2
    while label in labels or not LABEL_RE.match(label):
        label, n = f"{base}-{n}", n + 1
    return label
