"""The blocks of a Markdown file that the author can write in on the preview.

A block is a paragraph or a list item. The preview build marks each one with
an empty span naming its file and key (`marks`, through
trazo.preview_edit_review.reader) and wraps each atom of its text (citation,
value, math, code, note call, inline image) in a span, so a locked chip on the
page and its Markdown are one to one. The Word build never sees these marks.
trazo.preview_block_save writes an edit of the page back to the block.

A key is a hash of the block's text and its number among equal blocks, so a
change elsewhere in the file leaves the key valid and a change to the block
does not. Text whose reading a mark could change gets none: it is not a block.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass

from . import edit_diff

# Text the alignment must not treat as prose. `atom` is what a chip stands for.
PROTECTED = re.compile(r"""
    <!--.*?-->
  | (?P<atom>
        (?<!`)(?P<tick>`+)(?!`)[^\n]*?[^`\n](?P=tick)(?!`)
      | \$(?=[^\s$])[^$\n]*(?<![\s\\])\$(?!\d)
      | \{\{v:[^{}]*\}\}
      | !\[[^\]]*\]\([^)]*\)(?:\{[^{}]*\})?
      | \^\[(?:[^\[\]]|\[[^\]]*\])*\]
      | \[\^[^\]]+\]
      | \[[^\[\]]*@[^\[\]]*\](?![(\[{])
      | (?<![\w@\\])-?@[\w:.\#$%&+?<>~/-]*\w(?:[ ]\[[^\[\]]*\](?![(\[]))?
    )
  | \]\([^)]*\) | \]\[[^\]]*\] | \{[^{}]*\} | <[^>\n]+> | \\[A-Za-z]+(?:\{[^{}]*\})*
""", re.X | re.S)
QUOTE = re.compile(r"[ \t]{0,3}(?:>[ \t]?)+")
ITEM = re.compile(r"[ \t]*(?:[-*+]|\d+[.)]|#[.)]|\(@\w*\)|\(?[A-Za-z][.)]|\(?[ivxIVX]+[.)])[ \t]+")
# Where a block's text would start, text that only reads as it does at the start
# of a block: task boxes, example lists, line blocks, a lone attribute,
# definitions, raw HTML and raw TeX environments.
NOT_TEXT = re.compile(r"\[[ xX]\][ \t]|\(@\w*\)[ \t]|\|[ \t]|\{[^}]*\}[ \t\r]*$|~[ \t]|<|\\begin\{")
RULE = re.compile(r"[ \t]{0,3}(?:(?:-[ \t]*){3,}|(?:\*[ \t]*){3,}|(?:_[ \t]*){3,})\r?")
SETEXT = re.compile(r"[ \t]*(?:=+|-+)[ \t\r]*")
# A name that could not sit in the mark's quoted attribute: its file gets no marks.
UNQUOTABLE = re.compile(r'["\\\n]')


@dataclass(frozen=True)
class Block:
    """text[at:end] is the block, without its list or quote marker."""
    at: int
    end: int
    key: str


def _starts_item(line: str) -> bool:
    quote = QUOTE.match(line)
    return bool(ITEM.match(line[quote.end():] if quote else line))


def blocks_of(text: str, lines: edit_diff.Lines | None = None) -> list[Block]:
    """Each paragraph or list item that may hold a mark, in text order."""
    lines = lines or edit_diff.Lines(text)
    found: list[tuple[int, int]] = []
    i, count = 0, len(lines.lines)
    while i < count:
        if not lines.lines[i].strip():
            i += 1
            continue
        last = i
        while last + 1 < count and lines.lines[last + 1].strip() and not _starts_item(lines.lines[last + 1]):
            last += 1
        at = lines.block_mark_at(lines.starts[i])
        if (at is not None and lines.line_of(at) == i and not lines.hidden[at]
                and not NOT_TEXT.match(text, at) and not ITEM.match(text, at) and not RULE.fullmatch(lines.lines[i])
                and not (last > i and SETEXT.fullmatch(lines.lines[i + 1]))):
            found.append((at, lines.starts[last] + len(lines.lines[last].rstrip("\r"))))
        i = last + 1
    seen: Counter[str] = Counter()
    blocks = []
    for at, end in found:
        digest = hashlib.sha1(text[at:end].encode("utf-8")).hexdigest()[:12]
        blocks.append(Block(at, end, f"{digest}-{seen[digest]}"))
        seen[digest] += 1
    return blocks


def escaped(text: str, at: int) -> bool:
    """Whether a backslash escapes the character at `at`."""
    run = 0
    while at - run > 0 and text[at - run - 1] == "\\":
        run += 1
    return run % 2 == 1


def atoms_in(text: str, block: Block, lines: edit_diff.Lines) -> list[tuple[int, int, bool]]:
    """(start, end, bare) of each atom of a block a span may wrap; `bare` is a
    citation without brackets, which a bracketed span would change."""
    return [(m.start(), m.end(), m.group(0)[0] in "@-")
            for m in PROTECTED.finditer(text, block.at, block.end)
            if m["atom"] and not escaped(text, m.start()) and lines.inline(*m.span())]


def marks(text: str, name: str) -> list[tuple[int, int, str]]:
    """The marks of a file's blocks for trazo.edit_diff.marked. An atom's span
    goes inside any edit span that starts or ends with it."""
    if UNQUOTABLE.search(name):
        return []
    lines = edit_diff.Lines(text)
    out = []
    for block in blocks_of(text, lines):
        out.append((block.at, 1, f'[]{{.pv-src data-file="{name}" data-h="{block.key}"}}'))
        for i, (start, end, bare) in enumerate(atoms_in(text, block, lines)):
            out.append((start, 4, f'<span class="pv-atom" data-i="{i}">' if bare else "["))
            out.append((end, -1, "</span>" if bare else f']{{.pv-atom data-i="{i}"}}'))
    return out
