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
from typing import NamedTuple

from .expand import INCLUDE_PREFIX, TABLE_ROW
from .preview_edits import RULE, closing, markdown_context

BLANK_RUN = re.compile(r"\n(?:[ \t\r]*\n)+")
SEPARATED = re.compile(r"\n[ \t\r]*\n[ \t\r]*$")
CAPTION = re.compile(r" {0,3}(?:Table:|:)\s")
PIPE_RULE = re.compile(r" {0,3}\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)+\|?\s*$")
MATH = re.compile(r"\$\$[\s\S]*?\$\$\s*(?:\{[^{}]*\})?")
ATTRIBUTES = re.compile(r"\s*(?:\{[^{}]*\})?")


class Chunk(NamedTuple):
    text: str
    plain: str
    kind: str | None


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
            pair = next((c for c in blocks if c.kind == chunk.kind), None)
            if pair:
                blocks.remove(pair)
            out.append(_taken(chunk, pair))
        out += prose
    return _joined(out)


def _chunks(text: str) -> list[Chunk]:
    plain = "".join("\n" if ch == "\n" else p for ch, p in zip(text, markdown_context(text)))
    cuts = [0, *(m.end() for m in BLANK_RUN.finditer(plain)), len(text)]
    pieces = [(text[a:b], plain[a:b]) for a, b in zip(cuts, cuts[1:]) if a < b]
    kinds = [_kind(p) for _, p in pieces]
    for i, kind in enumerate(kinds):
        if kind == "caption" and "table" not in kinds[max(0, i - 1):i + 2:2]:
            kinds[i] = None
    return [Chunk(t, p, k) for (t, p), k in zip(pieces, kinds)]


def _kind(plain: str) -> str | None:
    core = plain.strip()
    if not core:
        return None
    if core.startswith(INCLUDE_PREFIX) and "\n" not in core:
        return "include"
    if core.count("$$") == 2 and MATH.fullmatch(core):
        return "math"
    if _figure_caption(plain):
        return "figure"
    lines = core.split("\n")
    if _table_parts(lines):
        return "table"
    return "caption" if CAPTION.match(lines[0]) else None


def _figure_caption(plain: str) -> tuple[int, int] | None:
    lead = len(plain) - len(plain.lstrip())
    core = plain.strip()
    if not core.startswith("!["):
        return None
    end = closing(core, 1, "[", "]")
    if end is None or core[end + 1:end + 2] != "(":
        return None
    tail = closing(core, end + 1, "(", ")")
    if tail is None or not ATTRIBUTES.fullmatch(core[tail + 1:]):
        return None
    return lead + 2, lead + end


def _table_parts(lines: list[str]) -> tuple[int, int] | None:
    start = 0
    if CAPTION.match(lines[0]):
        start = next((i - 1 if RULE.match(line) else i for i, line in enumerate(lines)
                      if i and (TABLE_ROW.match(line) or RULE.match(line))), len(lines))
    end = next((i for i in range(start + 1, len(lines)) if CAPTION.match(lines[i])), len(lines))
    rows = lines[start:end]
    if not rows or re.fullmatch(r"[-\s]+", rows[0]):
        return None
    if TABLE_ROW.match(rows[0]) or (len(rows) > 1 and (RULE.match(rows[1]) or PIPE_RULE.match(rows[1]))):
        return start, end
    return None


def _taken(chunk: Chunk, pair: Chunk | None) -> str:
    text, plain, kind = chunk
    if pair is None:
        return text
    if kind == "caption":
        return pair.text
    if kind == "figure":
        new, old = _figure_caption(plain), _figure_caption(pair.plain)
        assert new and old
        return text[:new[0]] + pair.text[old[0]:old[1]] + text[new[1]:]
    if kind == "table":
        lead, core = len(text) - len(text.lstrip()), text.strip()
        new_lines, old_lines = core.split("\n"), pair.text.strip().split("\n")
        new_rows, old_rows = _table_parts(new_lines), _table_parts(old_lines)
        assert new_rows and old_rows
        head = old_lines[:old_rows[0]] if old_rows[0] and new_rows[0] else new_lines[:new_rows[0]]
        tail_old, tail_new = old_lines[old_rows[1]:], new_lines[new_rows[1]:]
        lines = head + new_lines[new_rows[0]:new_rows[1]] + (tail_old if tail_old and tail_new else tail_new)
        return text[:lead] + "\n".join(lines) + text[lead + len(core):]
    return text


def _joined(chunks: list[str]) -> str:
    out = ""
    for chunk in chunks:
        if out and not SEPARATED.search(out):
            out += "\n" if out.endswith("\n") else "\n\n"
        out += chunk
    return out
