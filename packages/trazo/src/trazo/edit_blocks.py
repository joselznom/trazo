"""Figures, tables and display equations need no review: a change to one goes
into the review's baseline as it is, and only the prose around it stays pending.

`take_blocks(base, current)` is `base` with the blocks of `current` that are a
figure (an image alone in its paragraph), a pipe, grid or simple table with
its caption, a `$$` equation or an `!include` line, in place of those of
`base` it changed, added or removed. A figure or table in both keeps the
caption of `base`: caption text is prose, reviewed word by word. Prose and
code stay as `base` has them. The preview cannot reject a block's change.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Literal, NamedTuple

from .expand import INCLUDE_PREFIX, TABLE_ROW
from .preview_edits import RULE, closing, markdown_context

BLANK_RUN = re.compile(r"\n(?:[ \t\r]*\n)+")
SEPARATED = re.compile(r"\n[ \t\r]*\n[ \t\r]*$")
CAPTION = re.compile(r" {0,3}(?:Table:|:)\s")
PIPE_RULE = re.compile(r" {0,3}\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)+\|?\s*$")
MATH = re.compile(r"\$\$[\s\S]*?\$\$\s*(?:\{[^{}]*\})?")
ATTRIBUTES = re.compile(r"\s*(?:\{[^{}]*\})?")


Kind = Literal["figure", "table", "caption", "math", "include"]


class Chunk(NamedTuple):
    text: str
    masked: str
    kind: Kind | None


def take_blocks(base: str, current: str) -> str:
    """`base` with the figures, tables and equations of `current`."""
    if base == current:
        return base
    old, new = _chunks(base), _chunks(current)
    if not any(c.kind for c in old + new):
        return base
    out: list[str] = []
    matcher = SequenceMatcher(None, [c.text.strip() for c in old], [c.text.strip() for c in new], autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            out += [c.text for c in old[i1:i2]]
            continue
        prose = [c.text for c in old[i1:i2] if not c.kind]
        blocks = [c for c in old[i1:i2] if c.kind]
        for chunk in new[j1:j2]:
            if not chunk.kind:
                if prose:
                    out.append(prose.pop(0))
                continue
            reviewed = next((c for c in blocks if c.kind == chunk.kind), None)
            if reviewed:
                blocks.remove(reviewed)
            out.append(_with_reviewed_caption(chunk, reviewed))
        out += prose
    return _joined(out)


def _chunks(text: str) -> list[Chunk]:
    masked = "".join("\n" if ch == "\n" else m for ch, m in zip(text, markdown_context(text)))
    cuts = [0, *(m.end() for m in BLANK_RUN.finditer(masked)), len(text)]
    pieces = [(text[a:b], masked[a:b]) for a, b in zip(cuts, cuts[1:]) if a < b]
    kinds = [_kind(m) for _, m in pieces]
    for i, kind in enumerate(kinds):
        if kind == "caption" and not _beside_a_table(kinds, i):
            kinds[i] = None
    return [Chunk(t, m, k) for (t, m), k in zip(pieces, kinds)]


def _beside_a_table(kinds: list[Kind | None], i: int) -> bool:
    return any(0 <= j < len(kinds) and kinds[j] == "table" for j in (i - 1, i + 1))


def _kind(masked: str) -> Kind | None:
    core = masked.strip()
    if not core:
        return None
    if core.startswith(INCLUDE_PREFIX) and "\n" not in core:
        return "include"
    if core.count("$$") == 2 and MATH.fullmatch(core):
        return "math"
    if _figure_caption_span(masked):
        return "figure"
    lines = core.split("\n")
    if _table_rows(lines):
        return "table"
    return "caption" if CAPTION.match(lines[0]) else None


def _figure_caption_span(masked: str) -> tuple[int, int] | None:
    lead = len(masked) - len(masked.lstrip())
    core = masked.strip()
    if not core.startswith("!["):
        return None
    end = closing(core, 1, "[", "]")
    if end is None or core[end + 1:end + 2] != "(":
        return None
    tail = closing(core, end + 1, "(", ")")
    if tail is None or not ATTRIBUTES.fullmatch(core[tail + 1:]):
        return None
    return lead + 2, lead + end


def _table_rows(lines: list[str]) -> range | None:
    start = 0
    if CAPTION.match(lines[0]):
        start = next((i - 1 if RULE.match(line) else i for i, line in enumerate(lines)
                      if i and (TABLE_ROW.match(line) or RULE.match(line))), len(lines))
    end = next((i for i in range(start + 1, len(lines)) if CAPTION.match(lines[i])), len(lines))
    rows = lines[start:end]
    if not rows or re.fullmatch(r"[-\s]+", rows[0]):
        return None
    if TABLE_ROW.match(rows[0]) or (len(rows) > 1 and (RULE.match(rows[1]) or PIPE_RULE.match(rows[1]))):
        return range(start, end)
    return None


def _with_reviewed_caption(block: Chunk, reviewed: Chunk | None) -> str:
    if reviewed is None or block.kind in ("math", "include"):
        return block.text
    if block.kind == "caption":
        return reviewed.text
    if block.kind == "figure":
        new, old = _figure_caption_span(block.masked), _figure_caption_span(reviewed.masked)
        assert new and old
        return block.text[:new[0]] + reviewed.text[old[0]:old[1]] + block.text[new[1]:]
    lead, core = len(block.text) - len(block.text.lstrip()), block.text.strip()
    new_lines, old_lines = core.split("\n"), reviewed.text.strip().split("\n")
    new_rows, old_rows = _table_rows(new_lines), _table_rows(old_lines)
    assert new_rows and old_rows
    head = old_lines[:old_rows.start] if old_rows.start and new_rows.start else new_lines[:new_rows.start]
    old_tail, new_tail = old_lines[old_rows.stop:], new_lines[new_rows.stop:]
    lines = head + new_lines[new_rows.start:new_rows.stop] + (old_tail if old_tail and new_tail else new_tail)
    return block.text[:lead] + "\n".join(lines) + block.text[lead + len(core):]


def _joined(chunks: list[str]) -> str:
    out = ""
    for chunk in chunks:
        if out and not SEPARATED.search(out):
            out += "\n" if out.endswith("\n") else "\n\n"
        out += chunk
    return out
