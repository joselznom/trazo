"""Figures, tables and display equations need no review: a change to one goes
into the review's baseline as it is, and only the prose around it stays pending.

`take_blocks(base, current)` is `base` with the blocks of `current` that are a
figure (an image alone in its paragraph), a pipe, grid or simple table with
its caption or a `$$` equation, in place of those of
`base` it changed, added or removed. A figure or table in both keeps the
caption of `base`: caption text is prose, reviewed word by word. Prose and
code stay as `base` has them. The preview cannot reject a block's change.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Literal, NamedTuple

from .expand import TABLE_ROW, TABLE_RULE, fenced_lines
from .preview_edits import RULE, closing, markdown_context

BLANK_RUN = re.compile(r"\n(?:[ \t\r]*\n)+")
SEPARATED = re.compile(r"\n[ \t\r]*\n[ \t\r]*$")
CAPTION = re.compile(r" {0,3}(?:Table:|:)\s")
PIPE_RULE = re.compile(r" {0,3}\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)+\|?\s*$")
MATH = re.compile(r"\$\$[\s\S]*?\$\$\s*(?:\{[^{}]*\})?")
ATTRIBUTES = re.compile(r"\s*(?:\{[^{}]*\})?")
CAPTION_TEXT = re.compile(r"( {0,3}(?:Table:|:)\s+)([\s\S]*?)(\s*\{[^{}]*\})?")
LABEL = re.compile(r"\{[^{}]*#((?:fig|tbl|eq):[^\s{}]+)")


Kind = Literal["figure", "table", "caption", "math"]


class Chunk(NamedTuple):
    text: str
    masked: str
    kind: Kind | None


class Piece(NamedTuple):
    text: str
    old: int | None  # its place among the chunks of base, of current
    new: int | None


def take_blocks(base: str, current: str) -> str:
    """`base` with the figures, tables and equations of `current`."""
    if base == current:
        return base
    old, new = _chunks(base), _chunks(current)
    if not any(c.kind for c in old + new):
        return base
    out: list[Piece] = []
    matcher = SequenceMatcher(None, [c.text.strip() for c in old], [c.text.strip() for c in new], autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            out += [Piece(old[i].text, i, j1 + i - i1) for i in range(i1, i2)]
            continue
        prose = [Piece(old[i].text, i, None) for i in range(i1, i2) if not old[i].kind]
        blocks = [i for i in range(i1, i2) if old[i].kind]
        claimed = {_label(c) for c in new[j1:j2] if c.kind} - {None}
        for j in range(j1, j2):
            if not new[j].kind:
                if prose:
                    out.append(prose.pop(0))
                continue
            at = _same_block(new[j], [old[i] for i in blocks], claimed)
            place = blocks.pop(at) if at is not None else None
            reviewed = old[place] if place is not None else None
            while place is not None and prose and prose[0].old < place:
                out.append(prose.pop(0))
            out.append(Piece(_with_reviewed_caption(new[j], reviewed), None, j))
        out += prose
    return _joined(out)


def _label(chunk: Chunk) -> str | None:
    found = LABEL.search(chunk.masked)
    return found[1] if found else None


def _same_block(block: Chunk, blocks: list[Chunk], claimed: set[str | None]) -> int | None:
    same = [i for i, c in enumerate(blocks) if c.kind == block.kind]
    label = _label(block)
    return next((i for i in same if label and _label(blocks[i]) == label),
                next((i for i in same if _label(blocks[i]) not in claimed), None))


def _chunks(text: str) -> list[Chunk]:
    masked = markdown_context(text)
    cuts = {0, len(text), *(m.end() for m in BLANK_RUN.finditer(text)
                            if masked[m.start()] == masked[m.end() - 1] == "\n")}
    starts = [0, *(m.end() for m in re.finditer("\n", text))]
    code = [literal for _, literal in fenced_lines(text.split("\n"))]
    cuts |= {starts[i] for i in range(1, len(starts)) if code[i] != code[i - 1]}
    edges = sorted(cuts)
    pieces = [(text[a:b], masked[a:b]) for a, b in zip(edges, edges[1:]) if a < b]
    kinds = [_kind(m) if m.count("\n") == t.count("\n") else None for t, m in pieces]
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
    if not rows:
        return None
    headless = len(rows) > 2 and bool(TABLE_RULE.match(rows[0]) and TABLE_RULE.match(rows[-1]))
    if re.fullmatch(r"[-\s]+", rows[0]) and not headless:
        return None
    if (TABLE_ROW.match(rows[0]) or headless
            or (len(rows) > 1 and (RULE.match(rows[1]) or PIPE_RULE.match(rows[1])))):
        return range(start, end)
    return None


def _with_reviewed_caption(block: Chunk, reviewed: Chunk | None) -> str:
    if reviewed is None or block.kind == "math":
        return block.text
    if block.kind == "caption":
        return _with_caption_text(block.text, reviewed.text)
    if block.kind == "figure":
        new, old = _figure_caption_span(block.masked), _figure_caption_span(reviewed.masked)
        assert new and old
        return block.text[:new[0]] + reviewed.text[old[0]:old[1]] + block.text[new[1]:]
    lead, core = len(block.text) - len(block.text.lstrip()), block.text.strip()
    new_lines, old_lines = core.split("\n"), reviewed.text.strip().split("\n")
    new_rows, old_rows = _table_rows(new_lines), _table_rows(old_lines)
    assert new_rows and old_rows
    parts = ["\n".join(new_lines[:new_rows.start]), "\n".join(new_lines[new_rows.start:new_rows.stop]),
             "\n".join(new_lines[new_rows.stop:])]
    if old_rows.start and new_rows.start:
        parts[0] = _with_caption_text(parts[0], "\n".join(old_lines[:old_rows.start]))
    if old_lines[old_rows.stop:] and parts[2]:
        parts[2] = _with_caption_text(parts[2], "\n".join(old_lines[old_rows.stop:]))
    return block.text[:lead] + "\n".join(part for part in parts if part) + block.text[lead + len(core):]


def _with_caption_text(new: str, old: str) -> str:
    """The caption paragraph `new` with the words of `old`."""
    lead, core = len(new) - len(new.lstrip()), new.strip()
    mine, theirs = CAPTION_TEXT.fullmatch(core), CAPTION_TEXT.fullmatch(old.strip())
    if not (mine and theirs):
        return old
    return new[:lead + mine.start(2)] + theirs[2] + new[lead + mine.end(2):]


def _joined(pieces: list[Piece]) -> str:
    out = ""
    for before, piece in zip([None, *pieces], pieces):
        next_to = before is not None and any(
            a is not None and b == a + 1 for a, b in ((before.old, piece.old), (before.new, piece.new)))
        if out and not next_to and not SEPARATED.search(out):
            out += "\n" if out.endswith("\n") else "\n\n"
        out += piece.text
    return out
