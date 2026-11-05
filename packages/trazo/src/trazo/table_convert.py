"""Make editable: a Markdown table of a manuscript becomes a table typed by hand.

Pandoc reads the table; its cells become a spec (trazo.table_hand) and the table,
its caption and its note leave the Markdown for a line that includes the rendered
table, so numbering and cross-references stay. A table that cannot be converted
without losing something is refused with the reason; it keeps Edit source. The
check is not a list of rules alone: the new table is rendered and read back with
Pandoc, and its cells must match.
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any

from . import pandoc_run, table_hand
from . import table as tb
from .project import PROJECT

ALIGN = {"AlignRight": "right", "AlignCenter": "center"}
TITLED = re.compile(r"^\[(.+?)\]\{\.caption-title\}\s*(.*)$", re.S)
# Inline elements a cell typed by hand cannot hold, as the author would name them.
UNSUPPORTED = {"Link": "a link", "Image": "an image", "Math": "an equation", "Code": "code",
               "Note": "a footnote", "Strikeout": "crossed-out text", "RawInline": "raw markup",
               "Span": "styled text", "SmallCaps": "small capitals", "Underline": "underlined text",
               "LineBreak": "a line break", "Quoted": "quotation marks of Markdown"}
MARKS = {"Strong": "**", "Emph": "*", "Superscript": "^", "Subscript": "~"}
PANDOC_SECONDS = 120


class CannotConvert(tb.TableError):
    """The reason, in the author's words."""


def blocks_of(markdown: str) -> list[dict[str, Any]]:
    try:
        done = pandoc_run.run(["--from=markdown", "--to=json"], input=markdown, text=True, timeout=PANDOC_SECONDS)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CannotConvert(f"Pandoc could not read it: {exc}") from exc
    if done.returncode:
        raise CannotConvert(f"Pandoc could not read it: {done.stderr.strip()}")
    return json.loads(done.stdout)["blocks"]


def tables_in(blocks: list[dict[str, Any]]) -> list[list[Any]]:
    """The tables of some blocks, those inside divs too."""
    found = []
    for block in blocks:
        if block["t"] == "Table":
            found.append(block["c"])
        elif block["t"] == "Div":
            found += tables_in(block["c"][1])
    return found


def table_of(markdown: str, identifier: str) -> list[Any]:
    found = [t for t in tables_in(blocks_of(markdown)) if t[0][0] == identifier]
    if len(found) != 1:
        raise CannotConvert("it is not one table with its caption")
    return found[0]


def soft(node: Any) -> Any:
    """Blocks without line wrapping, which changes nothing a reader sees."""
    if isinstance(node, list):
        return [soft(item) for item in node]
    if isinstance(node, dict):
        return {"t": "Space"} if node.get("t") == "SoftBreak" else {k: soft(v) for k, v in node.items()}
    return node


def inlines_of(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The inlines of a cell, or CannotConvert when it is more than a paragraph."""
    if not blocks:
        return []
    if len(blocks) > 1 or blocks[0]["t"] not in ("Plain", "Para"):
        raise CannotConvert("a cell has more than one paragraph, or a list")
    return blocks[0]["c"]


def typed(inlines: list[dict[str, Any]]) -> str:
    """Inlines as the text of a cell typed by hand."""
    out = []
    for item in inlines:
        kind = item["t"]
        content: Any = item.get("c")
        if kind == "Str":
            out.append(content)
        elif kind in ("Space", "SoftBreak"):
            out.append(" ")
        elif kind == "Cite":
            out.append(typed(content[1]))
        elif kind in MARKS:
            out.append(f"{MARKS[kind]}{typed(content)}{MARKS[kind]}")
        else:
            raise CannotConvert(f"a cell has {UNSUPPORTED.get(kind, 'markup a typed table cannot hold')}")
    return "".join(out)


def rows_of(source: list[Any]) -> tuple[list[list[Any]], list[list[Any]]]:
    """(header rows, body rows) of a Pandoc table, each row a list of its cells."""
    head, bodies, foot = source[3], source[4], source[5]
    if foot[1]:
        raise CannotConvert("it has a footer row")
    rows = [row[1] for body in bodies for part in (body[2], body[3]) for row in part]
    return [row[1] for row in head[1]], rows


def plain(inlines: list[dict[str, Any]]) -> list[Any]:
    """Inlines without what does not show: line wrapping and citation details."""
    out: list[Any] = []
    for item in inlines:
        kind = item["t"]
        content: Any = item.get("c")
        if kind == "SoftBreak":
            out.append("Space")
        elif kind == "Cite":
            out.append(["Cite", [c["citationId"] for c in content[0]]])
        elif kind in MARKS:
            out.append([kind, plain(content)])
        else:
            out.append(kind if content is None else [kind, content])
    return out


def shown(source: list[Any]) -> list[Any]:
    """What a reader sees of a table's cells, to compare two tables."""
    head, body = rows_of(source)
    return [[[cell[3], plain(inlines_of(cell[4]))] for cell in row] for row in [*head, *body]]


def spec_of(source: list[Any]) -> dict[str, Any]:
    head, rows = rows_of(source)
    if len(head) != 1:
        raise CannotConvert("its header is not one row")
    width = len(source[2])
    if any(cell[2] != 1 or cell[3] != 1 for cell in head[0]):
        raise CannotConvert("it has merged cells")
    columns: list[dict[str, Any]] = []
    for i, (cell, colspec) in enumerate(zip(head[0], source[2])):
        column: dict[str, Any] = {"id": f"c{i + 1}", "header": typed(inlines_of(cell[4]))}
        if colspec[0]["t"] in ALIGN:
            column["align"] = ALIGN[colspec[0]["t"]]
        if colspec[1]["t"] == "ColWidth":
            column["width"] = round(colspec[1]["c"] * 10, 2)
        columns.append(column)
    body: list[dict[str, Any]] = []
    for n, row in enumerate(rows, 1):
        if len(row) == 1 and width > 1 and row[0][3] == width:
            inlines = inlines_of(row[0][4])
            if len(inlines) != 1 or inlines[0]["t"] != "Strong":
                raise CannotConvert("it has merged cells")
            body.append({"id": f"b{n}", "block": typed(inlines[0]["c"])})
            continue
        if len(row) != width or any(cell[2] != 1 or cell[3] != 1 for cell in row):
            raise CannotConvert("it has merged cells")
        cells = {f"c{i + 1}": typed(inlines_of(cell[4])) for i, cell in enumerate(row)}
        body.append({"id": f"r{n}", "cells": {k: v for k, v in cells.items() if v}})
    return {"schema_version": 1, "title": "", "columns": columns, "body": body}


def plan(doc: Any, identifier: Any) -> dict[str, Any]:
    """What converting a table would do, or CannotConvert: {label, spec, source, text,
    markdown}: the new text of the source, and the Markdown that leaves it."""
    from . import preview_edits
    from .preview_captions import caption_source
    from .preview_sources import get_source
    file, _, caption_start, caption_end = caption_source(doc, identifier, "table")
    label = identifier[4:]
    if not tb.LABEL_RE.fullmatch(label):
        raise CannotConvert("its name has characters a table file cannot have")
    if tb.spec_path(label).exists():
        raise CannotConvert(f"a table named {label} already exists")
    source = get_source(file)
    text = source["text"]
    found = preview_edits.locate_table(text, identifier)
    if found is None:
        raise CannotConvert("its caption is not beside a table")
    group = text[found["start"]:found["stop"]]
    if "{{v:" in group:
        raise CannotConvert("it uses a value from the data ({{v:...}})")
    # What leaves the text must be the table, its caption and its note, and nothing else.
    leaving = blocks_of(group)
    if [block["t"] for block in leaving] not in (["Table"], ["Table", "Div"]):
        raise CannotConvert("other text sits against it: leave a blank line around it")
    if len(leaving) == 2 and (leaving[1]["c"][0][:2] != ["", ["table-note"]]
                              or leaving[1]["c"][0][2] not in ([], [["custom-style", "Table Note"]])):
        raise CannotConvert("its note has attributes a typed table cannot keep")
    # The whole document: a link or footnote may be defined far from its table.
    document = "\n\n".join(path.read_text(encoding="utf-8") for path in PROJECT.document_files(doc))
    pandoc_table = table_of(document, identifier)
    if pandoc_table[0][1] or pandoc_table[0][2]:
        raise CannotConvert("its caption has attributes a typed table cannot keep")
    spec = spec_of(pandoc_table)
    caption = text[caption_start:caption_end].strip()
    titled = TITLED.match(caption)
    spec["title"], legend = (titled[1], titled[2]) if titled else ("", caption)
    if legend:
        spec["legend"] = table_hand.one_line(legend)
    if found["note"]:
        spec["note"] = text[slice(*found["note"])].strip()
    built = tb.build(label, spec)
    if built.errors or built.markdown is None:
        raise CannotConvert(built.errors[0]["message"] if built.errors else "it cannot be rendered")
    rendered = table_of(built.markdown, identifier)
    if shown(rendered) != shown(pandoc_table):
        raise CannotConvert("its text would change as a typed table: edit it in the source")
    if plain(inlines_of(rendered[1][1])) != plain(inlines_of(pandoc_table[1][1])):
        raise CannotConvert("its caption would change as a typed table")
    if found["note"] and soft(blocks_of(spec["note"])) != soft(blocks_of("\n\n".join(built.note))):
        raise CannotConvert("its note would change as a typed table")
    new = f"{text[:found['start']]}{table_hand.include_line(label)}{text[found['stop']:]}"
    return {"label": label, "spec": spec, "source": source, "text": new, "markdown": group}
