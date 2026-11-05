"""The edits between two versions of a Markdown file, and the preview's marks for them.

`edits(base, current)` aligns the lines, then the words inside changed
lines, and returns each change as an Edit: its exact span in both texts,
so accepting it rewrites the base and rejecting it rewrites the current
text, and an id that stays the same while the text around it does.
Changes a few words apart are one edit; changes of spacing alone are none,
unless they split or join paragraphs.

`marked(current, edits)` is the current text with Pandoc spans the preview
styles: inserted text in `[...]{.pv-edit-ins}`, deleted text (escaped, so it shows
as written) in `[...]{.pv-edit-del}`, both with the edit's id in data-edit. A span
goes only where it cannot change how Pandoc reads the text (the rules of
the value marks, trazo.expand.markable_lines and UNMARKABLE, plus whole
emphasis, brackets and code). Otherwise the block the edit is in gets an
empty `[]{.pv-blk}` at its start, or, where no span is safe (tables, code,
figures alone in their paragraph), the edit is not marked.

`merge(before, ours, theirs)` applies the changes from `before` to `theirs`
to `ours`; where both changed the same words, `theirs` wins.
"""

from __future__ import annotations

import hashlib
import re
from bisect import bisect_right
from dataclasses import dataclass
from difflib import SequenceMatcher
from itertools import accumulate

from .expand import INCLUDE_PREFIX, INLINE_CODE, PLACEHOLDER, UNMARKABLE, markable_lines
from .preview_edits import markdown_context
from .preview_locate import HEADING

# Words, spaces and single punctuation marks, so an edit next to Markdown
# syntax ("here." to "here, edited.") leaves the syntax out of the edit.
TOKEN = re.compile(r"\s+|\w+|[^\w\s]")
# Citations: a span may hold one whole, never cut one or sit inside its brackets.
CITATION = re.compile(r"\[[^\[\]]*@[^\[\]]*\]|@[\w:.#$%&+?<>~/-]*\w")
BLANK_LINE = re.compile(r"\n[ \t\r]*\n")
# A line that starts a block: a span may not begin a line with one.
BLOCK_START = re.compile(r"[ \t]*(?:[-*+]\s|\d+[.)]\s|#|>|\||:|`{3}|~{3}|\$\$|!\[|<|\[\^|Table:|$)")
# Where a block's text starts after a list, quote or heading marker.
BLOCK_PREFIX = re.compile(r"[ \t]{0,3}(?:(?:[-*+]|\d+[.)])[ \t]+|>[ \t]?|#{1,6}[ \t]+)*")
DEFINITION = re.compile(r" {0,3}[:~][ \t]")
FENCED_DIV = re.compile(r" {0,3}:{3,}")
# The markers a line's block starts with: a span may not cover any of them.
STRUCTURE = re.compile(r"[ \t]*(?:(?:[-*+]|\d+[.)]|[:~])[ \t]+|>[ \t]?)*")
# Blocks that get no mark: figures alone in their paragraph, fenced divs,
# table captions, footnote definitions and definition-list markers.
NO_BLOCK_MARK = re.compile(r"!\[|:::|Table:|:\s|\[\^|\$\$")
# Characters that open and close inline Markdown in pairs.
PAIRED = "*~^`$"
GAP_WORDS = 1     # edits this many words apart (or fewer) are one edit
CONTEXT = 24      # characters around an edit that its id depends on
# Above this many words and spaces a changed run lets difflib skip the commonest
# tokens: still an exact diff, and about 18 times faster, since a long run with
# no blank line to split it would otherwise hold up the preview for seconds.
LONG_RUN = 2000


@dataclass(frozen=True)
class Edit:
    """One change: base[b0:b1] became current[c0:c1]."""
    id: str
    b0: int
    b1: int
    c0: int
    c1: int
    old: str
    new: str
    line: int   # 1-based line of current where it starts


def _tokens(text: str, offset: int) -> tuple[list[str], list[int]]:
    found = [(m.group(0), offset + m.start()) for m in TOKEN.finditer(text)]
    return [t for t, _ in found], [s for _, s in found]


def changes(base: str, current: str, exact: bool = False) -> list[tuple[int, int, int, int]]:
    """Word-level changes as (b0, b1, c0, c1): lines first, then the words
    inside each changed run of lines. `exact` keeps a long run's changes as
    narrow as a short one's, at the cost of LONG_RUN's time."""
    if base == current:
        return []
    a, b = base.splitlines(keepends=True), current.splitlines(keepends=True)
    a_at, b_at = list(accumulate(map(len, a), initial=0)), list(accumulate(map(len, b), initial=0))
    out = []
    # CRLF and LF compare equal: an editor converting a file's line endings
    # changes every line and no word. A missing last ending stays a difference.
    unix_a, unix_b = [line.replace("\r\n", "\n") for line in a], [line.replace("\r\n", "\n") for line in b]
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, unix_a, unix_b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        ta, sa = _tokens(base[a_at[i1]:a_at[i2]], a_at[i1])
        tb, sb = _tokens(current[b_at[j1]:b_at[j2]], b_at[j1])
        ends_a, ends_b = sa[1:] + [a_at[i2]], sb[1:] + [b_at[j2]]
        for op, k1, k2, m1, m2 in SequenceMatcher(None, ta, tb, autojunk=not exact and len(tb) > LONG_RUN).get_opcodes():
            if op == "equal":
                continue
            b0 = sa[k1] if k1 < len(ta) else a_at[i2]
            b1 = ends_a[k2 - 1] if k2 > k1 else b0
            c0 = sb[m1] if m1 < len(tb) else b_at[j2]
            c1 = ends_b[m2 - 1] if m2 > m1 else c0
            out.append((b0, b1, c0, c1))
    return out


def _significant(old: str, new: str) -> bool:
    if old.strip() or new.strip():
        return True
    return bool(BLANK_LINE.search(old)) != bool(BLANK_LINE.search(new))


def edits(base: str, current: str, name: str = "") -> list[Edit]:
    """The edits that turn `base` into `current`, in text order. `name` (the
    file's) goes into the ids, so edits in two files never share one."""
    if base == current:
        return []
    spans: list[list[int]] = []
    for b0, b1, c0, c1 in changes(base, current):
        if spans:
            gap = current[spans[-1][3]:c0]
            if not BLANK_LINE.search(gap) and len(gap.split()) <= GAP_WORDS:
                spans[-1][1], spans[-1][3] = b1, c1
                continue
        spans.append([b0, b1, c0, c1])
    out: list[Edit] = []
    seen: dict[str, int] = {}
    for b0, b1, c0, c1 in spans:
        old, new = base[b0:b1], current[c0:c1]
        if not _significant(old, new):
            continue
        key = "\0".join((name, old, new, current[max(0, c0 - CONTEXT):c0], current[c1:c1 + CONTEXT]))
        ident = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
        seen[ident] = seen.get(ident, 0) + 1
        if seen[ident] > 1:
            ident += f"-{seen[ident]}"
        out.append(Edit(ident, b0, b1, c0, c1, old, new, current.count("\n", 0, c0) + 1))
    return out


def accept(base: str, chosen: list[Edit]) -> str:
    """`base` with the chosen edits made: they are no longer pending."""
    for e in sorted(chosen, key=lambda e: e.b0, reverse=True):
        base = base[:e.b0] + e.new + base[e.b1:]
    return base


def reject(current: str, chosen: list[Edit]) -> str:
    """`current` with the chosen edits undone."""
    for e in sorted(chosen, key=lambda e: e.c0, reverse=True):
        current = current[:e.c0] + e.old + current[e.c1:]
    return current


def _kept(before: str, start: int, end: int, removed: list[tuple[int, int, str, bool]]) -> str:
    """before[start:end] without the parts `removed` deletes."""
    out, at = [], start
    for b0, b1, _, _ in sorted(removed):
        if b1 <= at or b0 >= end:
            continue
        out.append(before[at:max(at, b0)])
        at = max(at, b1)
    out.append(before[at:end] if at < end else "")
    return "".join(out)


def merge(before: str, ours: str, theirs: str) -> str:
    """`ours` with the changes from `before` to `theirs`. Where both changed
    the same words, `theirs` wins; where `ours` only lacks text (a pending
    insertion) and `theirs` changed part of it, only that part is taken."""
    # Exact: wider changes would overlap, and `theirs` would take what it did not touch.
    mine = [(b0, b1, ours[c0:c1], False) for b0, b1, c0, c1 in changes(before, ours, exact=True)]
    other = [(b0, b1, theirs[c0:c1], True) for b0, b1, c0, c1 in changes(before, theirs, exact=True)]
    if not other:
        return ours
    pieces, at = [], 0
    hunks = sorted(mine + other, key=lambda h: (h[0], h[1]))
    i = 0
    while i < len(hunks):
        start, end, group = hunks[i][0], hunks[i][1], [hunks[i]]
        i += 1
        # Overlapping changes, or two insertions at one place, go together;
        # changes that only touch stay apart.
        while i < len(hunks) and (hunks[i][0] < end or hunks[i][0] == hunks[i][1] == group[-1][0] == group[-1][1]):
            end = max(end, hunks[i][1])
            group.append(hunks[i])
            i += 1
        theirs_part = [h for h in group if h[3]]
        removed = [h for h in group if not h[3]]
        side = theirs_part or group
        texts, cursor = [], start
        for b0, b1, text, _ in sorted(side, key=lambda h: (h[0], h[1])):
            gap = _kept(before, cursor, b0, removed) if theirs_part and not any(h[2] for h in removed) else before[cursor:b0]
            texts.append(gap + text)
            cursor = b1
        tail = _kept(before, cursor, end, removed) if theirs_part and not any(h[2] for h in removed) else before[cursor:end]
        pieces.append(before[at:start] + "".join(texts) + tail)
        at = end
    pieces.append(before[at:])
    return "".join(pieces)


def _escaped(text: str) -> str:
    """Deleted text as Pandoc shows it literally, on one line."""
    return re.sub(r"([!-/:-@\[-`{-~])", r"\\\1", " ".join(text.split()))


def _balanced(text: str) -> bool:
    plain = re.sub(r"\\.", "", text)
    depth = 0
    for ch in plain:
        depth += {"[": 1, "]": -1}.get(ch, 0)
        if depth < 0:
            return False
    if depth or any(plain.count(ch) % 2 for ch in PAIRED):
        return False
    # `_` emphasis opens or closes at a word boundary; inside words it is text.
    return len(re.findall(r"(?<!\w)_|_(?!\w)", plain)) % 2 == 0


class Lines:
    """Line offsets of a text and where spans may go."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.lines = text.split("\n")
        self.starts = [0]
        for line in self.lines:
            self.starts.append(self.starts[-1] + len(line) + 1)
        # trazo reads headings, `!include` lines and definition terms line by
        # line before Pandoc does (sections, the abstract's keywords, the
        # abbreviation list): a span there would change what it reads.
        self.markable = [ok and not HEADING.match(line) and not line.startswith(INCLUDE_PREFIX)
                         and not FENCED_DIV.match(line) and not self._term(i)
                         for i, (ok, line) in enumerate(zip(markable_lines(self.lines), self.lines))]
        context = markdown_context(text)
        self.hidden = [a != b for a, b in zip(context, text)]  # code and comments
        self.blocked: list[list[tuple[int, int, bool]]] = [[] for _ in self.lines]  # (start, end, may be inside a span)
        for i, line in enumerate(self.lines):
            for pattern in (UNMARKABLE, INLINE_CODE, PLACEHOLDER, CITATION):
                for m in pattern.finditer(line):
                    # Attributes and HTML may not move into a span; math, link
                    # targets, code, values and citations may, whole.
                    inner = pattern is not UNMARKABLE or m.group(0)[0] not in "{<"
                    self.blocked[i].append((self.starts[i] + m.start(), self.starts[i] + m.end(), inner))

    def _term(self, i: int) -> bool:
        """Whether line i is a definition list's term."""
        if not self.lines[i].strip() or DEFINITION.match(self.lines[i]):
            return False
        following = self.lines[i + 1:i + 3]
        if following and not following[0].strip():
            following = following[1:]
        return bool(following) and bool(DEFINITION.match(following[0]))

    def line_of(self, offset: int) -> int:
        return min(bisect_right(self.starts, offset), len(self.lines)) - 1

    def inline(self, c0: int, c1: int) -> bool:
        """Whether a span may cover text[c0:c1] (or sit at c0 when empty)."""
        first, last = self.line_of(c0), self.line_of(max(c0, c1 - 1))
        for i in range(first, last + 1):
            if not self.markable[i]:
                return False
        segment = self.text[c0:c1]
        if BLANK_LINE.search(segment) or not _balanced(segment) or segment.endswith("\n"):
            return False
        # Whole lines: a span ending on a marker would cut it off from what makes it one.
        if any(BLOCK_START.match(self.lines[i]) for i in range(first + 1, last + 1)):
            return False
        if c0 < self.starts[first] + len(_prefix(self.lines[first], STRUCTURE)):
            return False
        if not self.text[self.starts[first]:c0].strip() and segment.strip() and BLOCK_START.match(segment):
            return False
        if any(self.hidden[max(0, c0 - 1):min(len(self.text), c1 + 1)]):
            return False
        for i in range(first, last + 1):
            for start, end, inner in self.blocked[i]:
                if start < c0 < end or start < c1 < end:
                    return False
                if not inner and c0 <= start and end <= c1 and c1 > c0:
                    return False
        return True

    def holds(self, c0: int, c1: int) -> bool:
        """Whether a span may wrap text[c0:c1], which Pandoc reads as one unit."""
        first, last = self.line_of(c0), self.line_of(max(c0, c1 - 1))
        return (all(self.markable[first:last + 1])
                and c0 >= self.starts[first] + len(_prefix(self.lines[first], STRUCTURE))
                and not any(start < edge < end for i in range(first, last + 1)
                            for start, end, _ in self.blocked[i] for edge in (c0, c1)))

    def block_mark_at(self, offset: int) -> int | None:
        """Where an empty span marks the block that `offset` is in, or None."""
        i = self.line_of(offset)
        while (i > 0 and self.lines[i - 1].strip() and self.markable[i - 1]
               and not _prefix(self.lines[i], BLOCK_PREFIX)):
            i -= 1
        line = self.lines[i]
        if not self.markable[i] or not line.strip():
            return None
        prefix = BLOCK_PREFIX.match(line)
        at = prefix.end() if prefix else 0
        if NO_BLOCK_MARK.match(line, at) or not line[at:].strip():
            return None
        position = self.starts[i] + at
        if any(start < position < end for start, end, _ in self.blocked[i]):
            return None
        return position


def _prefix(line: str, pattern: re.Pattern[str]) -> str:
    """The block markers (`pattern`) a line starts with ("" if none)."""
    found = pattern.match(line)
    return found.group(0) if found else ""


def _blocks_inserted(text: str, c0: int, c1: int) -> list[int]:
    """Starts of the paragraphs inside text[c0:c1] that are wholly new."""
    starts = []
    for m in BLANK_LINE.finditer(text, c0, c1):
        start = m.end()
        while start < c1 and text[start] in " \t\r\n":
            start += 1
        if start < c1:
            starts.append(start)
    return starts


def edit_marks(current: str, found: list[Edit]) -> list[tuple[int, int, str]]:
    """The marks of `found` as (offset, order, text), where the order at one
    offset is closers 0, block marks 1, deletions 2, openers 3. An edit with
    no safe place has none."""
    if not found:
        return []
    lines = Lines(current)
    inserts: list[tuple[int, int, str]] = []
    for e in found:
        attr = f'data-edit="{e.id}"'
        new_text, old_text = e.new.strip(), e.old.strip()
        if not BLANK_LINE.search(e.new) and not BLANK_LINE.search(e.old) and lines.inline(e.c0, e.c1):
            # Spaces stay outside the spans: Pandoc drops them at a span's edges.
            start = e.c0 + len(e.new) - len(e.new.lstrip())
            end = e.c1 - (len(e.new) - len(e.new.rstrip()))
            if old_text:
                inserts.append((start if new_text else e.c0, 2, f"[{_escaped(e.old)}]{{.pv-edit-del {attr}}}"))
            if new_text:
                inserts.append((start, 3, "["))
                inserts.append((end, 0, f"]{{.pv-edit-ins {attr}}}"))
            continue
        marks = []
        new_blocks = _blocks_inserted(current, e.c0, e.c1) if new_text else []
        if new_text and not old_text and (e.c0 == 0 or re.search(r"\n[ \t\r]*\n[ \t]*$", current[:e.c0])):
            new_blocks = list(dict.fromkeys([e.c0 + len(e.new) - len(e.new.lstrip()), *new_blocks]))  # it starts a block too
        for start in new_blocks:
            at = lines.block_mark_at(start)
            if at is not None:
                marks.append((at, 1, f"[]{{.pv-blk .pv-new {attr}}}"))
        head = e.c0 if not new_blocks or current[e.c0:new_blocks[0]].strip() else None
        if head is not None:
            at = lines.block_mark_at(head)
            if at is not None:
                marks.append((at, 1, f"[]{{.pv-blk {attr}}}"))
        if old_text and BLANK_LINE.search(e.old) and lines.inline(e.c0, e.c0) and not marks:
            marks.append((e.c0, 2, f"[{_escaped(e.old)}]{{.pv-edit-del .pv-edit-del-block {attr}}}"))
        inserts.extend(marks)
    return inserts


def with_marks(current: str, inserts: list[tuple[int, int, str]]) -> str:
    """`current` with each (offset, order, text) in place."""
    pieces, at = [], 0
    for offset, _, text in sorted(inserts, key=lambda x: (x[0], x[1])):
        pieces.append(current[at:offset] + text)
        at = offset
    pieces.append(current[at:])
    return "".join(pieces)


def marked(current: str, found: list[Edit]) -> str:
    """`current` with the marks of `found`."""
    return with_marks(current, edit_marks(current, found))
