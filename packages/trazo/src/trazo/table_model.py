"""What the table editor draws: a table as cells with their ids and spans."""

from __future__ import annotations

import html
import re
from typing import Any

from .table import Leaf, Table, body_items, header_groups, joined_cells


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
    text = re.sub(r"\*\*(.+?)\*\*(?!\*)", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![*\w])\*(?![\s*])(.+?)(?<![\s*])\*(?![*\w])", r"<em>\1</em>", text)
    text = re.sub(r"\^([^\s^]+?)\^", r"<sup>\1</sup>", text)
    text = re.sub(r"~([^\s~]+?)~", r"<sub>\1</sub>", text)
    return re.sub("\x00(\\d+)\x01", lambda m: html.escape(kept[int(m.group(1))]), text)


def label_of(table: Table, field: str, value: str) -> dict[str, Any]:
    """The map entry that names a text value from the data, for renaming it in the
    editor; `shared` when it comes from _shared.json (not this spec's maps)."""
    mapping = table.maps.get(field, {})
    own = table.spec.get("maps", {}).get(field, {})
    return {"field": field, "value": value, "text": mapping.get(value, value),
            "shared": value in mapping and value not in own}


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
                "id": leaf.id, "kind": "leaf", "align": leaf.align, "hidden": leaf.hidden,
                **({"mark": leaf.mark} if leaf.mark else {}),
                # A single-column pivot is headed by its value's label.
                **({"label": label_of(table, *leaf.pivot)} if leaf.pivot and not leaf.group
                   and isinstance(leaf.pivot[1], str) else {})}

    def named(cell: dict[str, Any], leaf: Leaf) -> dict[str, Any]:
        if isinstance(cell["raw"], str) and not leaf.format and not table.hand:
            cell["label"] = label_of(table, leaf.field, cell["raw"])
        return cell

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
            if leaf.pivot and isinstance(leaf.pivot[1], str):
                top[-1]["label"] = label_of(table, *leaf.pivot)
        bottom.append(head(leaf, 1))
    body: list[dict[str, Any]] = []
    r = -1
    typed = iter([b for blocks in table.blocks.values() for b in blocks])
    for i, (kind, cells) in enumerate(items):
        if kind == "block" and table.hand:
            b = next(typed)
            body.append({"block": md_inline(cells), "id": b["id"], "text": b["block"]})
            continue
        if kind == "block":
            value = table.rows[r + 1]["key"][table.spec["rows"]["block"]]
            body.append({"block": md_inline(cells),
                         **({"label": label_of(table, table.spec["rows"]["block"], value)}
                            if isinstance(value, str) else {})})
            continue
        r += 1
        out = []
        for leaf in leaves:
            if leaf.hidden:
                text, raw = table.hidden_cells[leaf.id][r]
                out.append(named({"id": leaf.id, "text_html": md_inline(text), "raw": raw,
                                  "rowspan": 1, "bold": False, "align": leaf.align,
                                  "hidden": True}, leaf))
                continue
            c = visible[leaf.id]
            if joined[i][c]:
                continue
            span = 1
            while i + span < len(items) and joined[i + span][c]:
                span += 1
            out.append(named({"id": leaf.id, "text_html": md_inline(cells[c]),
                              "raw": table.values[r][c], "rowspan": span,
                              "bold": (r, c) in table.bold, "align": leaf.align, "hidden": False,
                              **({"mark": table.marks[r, c]} if (r, c) in table.marks else {})},
                             leaf))
        body.append({"row": r, "key": table.rows[r]["key"], "cells": out})
    return {"header": [top, bottom] if groups else [top], "body": body,
            "caption_html": md_inline(table.caption),
            "note_html": "".join(f"<p>{md_inline(p)}</p>" for p in table.note)}
