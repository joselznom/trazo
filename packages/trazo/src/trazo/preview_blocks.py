"""The blocks of a Markdown file that the author can write in on the preview.

A block is a paragraph or a list item. The preview build marks each one with
an empty span naming its file and key (`marks`, through
trazo.preview_edit_review.reader) and wraps each atom of its text (citation,
value, math, code, note call, inline image) in a span, so a locked chip on the
page and its Markdown are one to one. The Word build never sees these marks.
trazo.preview_block_save writes an edit of the page back to the block.

A key is a hash of the block's text, so a change elsewhere in the file leaves
it valid and a change to the block does not. Text alone cannot tell equal blocks
apart, so their keys also hold their number and a hash of the whole file: any
change to the file ends them, rather than passing one to a twin.
Text whose reading a mark could change gets none: it is not a block.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass

from . import edit_diff
from .expand import FENCE

# Text the alignment must not treat as prose. `atom` is what a chip stands for.
PROTECTED = re.compile(r"""
    <!--.*?-->
  | (?P<atom>
        (?<!`)(?P<tick>`+)(?!`)[^\n]*?[^`\n](?P=tick)(?!`)(?:\{[^{}]*\})?
      | \$(?=[^\s$])[^$\n]*(?<![\s\\])\$(?!\d)
      | \{\{v:[^{}]*\}\}
      | !\[[^\]]*\]\((?:[^()]|\([^()]*\))*\)(?:\{[^{}]*\})?
      | \^\[(?:[^\[\]]|\[[^\]]*\])*\]
      | \[\^[^\]]+\]
      | \[[^\[\]]*\]\(\#[^)]*\)(?:\{[^{}]*\})?
      | \[[^\[\]]*@[^\[\]]*\](?![(\[{])
      | (?<![\w@\\])-?@[\w:.\#$%&+?<>~/-]*\w(?:[ ]\[[^\[\]]*\](?![(\[]))?
    )
  | \]\([^)]*\) | \]\[[^\]]*\] | \{[^{}]*\} | <[^>\n]+> | \\[A-Za-z]+(?:\{[^{}]*\})*
""", re.X | re.S)
QUOTE = re.compile(r"[ \t]{0,3}(?:>[ \t]?)+")
# One capital, a period and one space are prose to Pandoc: `E. coli`, and not `II. Next`.
ITEM = re.compile(r"[ \t]*(?![A-Z]\. \S)(?:[-*+]|\(?\d+[.)]|\(?#[.)]|\(@\w*\)|\(?[A-Za-z][.)]|\(?[ivxlcdmIVXLCDM]+[.)])[ \t]+")
# Where a block's text would start, text that only reads as it does at the start
# of a block: task boxes, example lists, line blocks, a lone attribute,
# definitions, raw HTML and raw TeX environments.
NOT_TEXT = re.compile(r"\[[ xX]\][ \t]|\(@\w*\)[ \t]|\|[ \t]|\{[^}]*\}[ \t\r]*$|~[ \t]|<|\\begin\{")
RULE = re.compile(r"[ \t]{0,3}(?:(?:-[ \t]*){3,}|(?:\*[ \t]*){3,}|(?:_[ \t]*){3,})\r?")
SETEXT = re.compile(r"[ \t]*(?:=+|-+)[ \t\r]*")
COMMENT_LINE = re.compile(r"[ \t]*<!--.*-->[ \t\r]*")
RAW_OPENING = re.compile(r"[ \t]*(?:<[A-Za-z!/?]|\\begin\{)")
# Groups: the marker, the text, closing marks, the attributes. Other braces at the
# end of a heading are text, or belong to the span before them.
ATTRIBUTE = r"""(?:[#.][\w:.\-]+|-|[\w:.\-]+=(?:"[^"]*"|'[^']*'|[^\s}]*))"""
HEADING_LINE = re.compile(rf"( {{0,3}}#{{1,6}}[ \t]+)(.*?)((?:[ \t]+#+)?[ \t]*)((?<=[ \t])\{{\s*(?:{ATTRIBUTE}\s*)*\}})?[ \t]*\r?")
# A name that could not sit in the mark's quoted attribute: its file gets no marks.
UNQUOTABLE = re.compile(r'["\\\n]')
# Key -> the Markdown of each block marked since the preview started, oldest first. A
# page can be older than the last build, and an author keeping their text over a
# change on disk needs the Markdown that text came from. A key is a hash of it, so
# an entry never goes stale; the limit is a few builds of a long thesis.
SOURCES: dict[str, str] = {}
SOURCES_LIMIT = 50_000


@dataclass(frozen=True)
class Block:
    """text[at:end] is the block, without its list or quote marker."""
    at: int
    end: int
    key: str
    heading: bool = False


def _digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def blank(line: str) -> bool:
    """Whether a line separates paragraphs: empty, or only quote marks."""
    return not line.strip() or bool(QUOTE.fullmatch(line.rstrip()))


def _indented(line: str) -> bool:
    quote = QUOTE.match(line)
    return (line[quote.end():] if quote else line)[:1] in (" ", "\t")


def _starts_item(line: str) -> bool:
    quote = QUOTE.match(line)
    return bool(ITEM.match(line[quote.end():] if quote else line))


def blocks_of(text: str, lines: edit_diff.Lines | None = None) -> list[Block]:
    """Each paragraph or list item that may hold a mark, in text order."""
    lines = lines or edit_diff.Lines(text)
    found: list[tuple[int, int]] = []
    i, count = 0, len(lines.lines)
    listed = False  # in a list: an item, or a paragraph of the item before it
    while i < count:
        # A heading, a comment line or a div's fence ends with itself: the next line starts afresh.
        if (blank(lines.lines[i]) or edit_diff.HEADING.match(lines.lines[i]) or COMMENT_LINE.fullmatch(lines.lines[i])
                or edit_diff.FENCED_DIV.match(lines.lines[i])):
            listed = listed and blank(lines.lines[i])
            i += 1
            continue
        at = lines.block_mark_at(lines.starts[i])
        prose = (at is not None and lines.line_of(at) == i and not lines.hidden[at] and not NOT_TEXT.match(text, at)
                 and not ITEM.match(text, at) and not RULE.fullmatch(lines.lines[i]))
        listed = _starts_item(lines.lines[i]) or (listed and _indented(lines.lines[i]))
        # In a list a line like an item starts one. In a paragraph it goes on with the text.
        raw = RAW_OPENING.match(lines.lines[i]) or (at is not None and RAW_OPENING.match(text, at))
        last, items = i, listed and not raw
        while (last + 1 < count and not blank(lines.lines[last + 1]) and not FENCE.match(lines.lines[last + 1])
               and not edit_diff.FENCED_DIV.match(lines.lines[last + 1])
               and not (items and _starts_item(lines.lines[last + 1]))):
            last += 1
            items = items and "\\begin{" not in lines.lines[last]  # in an environment, a `+ a` is no item
        if prose and not (last > i and SETEXT.fullmatch(lines.lines[i + 1])):
            found.append((at, lines.starts[last] + len(lines.lines[last].rstrip("\r"))))
        i = last + 1
    return _keyed(text, found)


def _keyed(text: str, found: list[tuple[int, int]], heading: bool = False) -> list[Block]:
    digests = [_digest(text[at:end]) for at, end in found]
    total, seen = Counter(digests), Counter[str]()
    revision = _digest(text) if len(total) < len(digests) else ""
    blocks = []
    for (at, end), digest in zip(found, digests):
        key = digest if total[digest] == 1 else f"{digest}-{seen[digest]}-{revision}"
        blocks.append(Block(at, end, f"h{key}" if heading else key, heading))
        seen[digest] += 1
    return blocks


def headings_of(text: str, lines: edit_diff.Lines | None = None) -> list[Block]:
    """The text of each heading of `#` marks, without them or its attributes."""
    lines = lines or edit_diff.Lines(text)
    found = []
    for i, line in enumerate(lines.lines):
        m = HEADING_LINE.fullmatch(line)
        if m and m[2] and lines.prose[i] and not lines.hidden[lines.starts[i]]:
            found.append((lines.starts[i] + m.start(2), lines.starts[i] + m.end(2)))
    return _keyed(text, found, heading=True)


def editable(text: str, lines: edit_diff.Lines | None = None) -> list[Block]:
    """Every block and heading of a file."""
    lines = lines or edit_diff.Lines(text)
    return [*blocks_of(text, lines), *headings_of(text, lines)]


def _heading_mark(lines: edit_diff.Lines, block: Block, name: str) -> tuple[int, int, str]:
    """A heading takes no span: its mark is an attribute at the end of its line,
    which the preview's sections carry to the <section>."""
    i = lines.line_of(block.at)
    m = HEADING_LINE.fullmatch(lines.lines[i])
    attributes = f'.pv-head data-file="{name}" data-h="{block.key}"'
    if m is None or not m[4]:
        return lines.starts[i] + len(lines.lines[i].rstrip()), 1, f' {{{attributes} data-auto="1"}}'
    auto = "" if "#" in m[4] else ' data-auto="1"'
    return lines.starts[i] + m.end(4) - 1, 1, f" {attributes}{auto}"


def escaped(text: str, at: int) -> bool:
    """Whether a backslash escapes the character at `at`."""
    run = 0
    while at - run > 0 and text[at - run - 1] == "\\":
        run += 1
    return run % 2 == 1


def atoms(text: str, start: int = 0, end: int | None = None) -> Iterator[re.Match[str]]:
    """The atoms of text[start:end]."""
    return (m for m in PROTECTED.finditer(text, start, len(text) if end is None else end)
            if m["atom"] and not escaped(text, m.start()))


def atoms_in(text: str, block: Block, lines: edit_diff.Lines) -> list[tuple[int, int, bool]]:
    """(start, end, bare) of each atom of a block a span may wrap; `bare` is a
    citation without brackets, which a bracketed span would change."""
    return [(m.start(), m.end(), m[0][0] in "@-") for m in atoms(text, block.at, block.end) if lines.holds(*m.span())]


def marks(text: str, name: str) -> list[tuple[int, int, str]]:
    """The marks of a file's blocks for trazo.edit_diff.with_marks. An atom's
    span has orders -1 and 4 to go inside an edit's span that starts or ends
    with it."""
    if UNQUOTABLE.search(name):
        return []
    lines = edit_diff.Lines(text)
    out = []
    for block in blocks_of(text, lines):
        SOURCES[block.key] = text[block.at:block.end]
        out.append((block.at, 1, f'[]{{.pv-src data-file="{name}" data-h="{block.key}"}}'))
        for i, (start, end, bare) in enumerate(atoms_in(text, block, lines)):
            out.append((start, 4, f'<span class="pv-atom" data-i="{i}">' if bare else "["))
            out.append((end, -1, "</span>" if bare else f']{{.pv-atom data-i="{i}"}}'))
    for heading in headings_of(text, lines):
        SOURCES[heading.key] = text[heading.at:heading.end]
        out.append(_heading_mark(lines, heading, name))
    for key in list(SOURCES)[:max(0, len(SOURCES) - SOURCES_LIMIT)]:
        del SOURCES[key]
    return out
