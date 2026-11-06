"""Replacing the words that changed in a block's Markdown.

`patch` takes the block's rendered text before and after an edit, with each
locked chip as one private-use character, and aligns it with the Markdown. A
chip's character is only its name: the n-th chip of `before` is the block's n-th
atom, so a save stays right when an earlier one changed what the chips are.
What was typed is escaped so it reads literally.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from .edit_diff import changes
from .preview_blocks import PROTECTED, atoms

CHIP_FIRST, OTHER_CHIP_FIRST, CHIP_LAST = 0xE000, 0xF000, 0xF8FE
BREAK = "\uf8ff"
ACTIVE = re.compile(r"([\\`*_\[\]{}$@~^<>#|])")
# Typed text that would start a list, a definition or a caption: ends in the character to escape.
LEAD = re.compile(r"^\s*(?:[-+:](?=\s)|(?:\d+|[A-Za-z])[.)](?=\s)|Table:)")
# What Pandoc's smart typography makes of plain characters, undone.
TYPOGRAPHY = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "\u00a0": " "})
MARKERS = ("***", "**", "__", "~~", "*", "_", "~", "^")
SHORTCUT_LINK = re.compile(r"\[([^\[\]]+)\](?![(\[{:])")


def is_chip(ch: str) -> bool:
    return CHIP_FIRST <= ord(ch) <= CHIP_LAST


def masked(source: str) -> str:
    return PROTECTED.sub(lambda m: "\0" * len(m.group(0)), source)


def _squeezed(source: str) -> tuple[str, list[tuple[int, int]]]:
    """`source` with each run of spaces as one, and where each character was:
    the page has no trace of the source's line breaks or indents."""
    found = list(re.finditer(r"\s+|\S", source))
    return "".join(" " if m[0].isspace() else m[0] for m in found), [m.span() for m in found]


class Offsets:
    """Where each character of the rendered text is in the block's Markdown."""

    def __init__(self, before: str, source: str, spans: list[tuple[int, int]], references: set[str]) -> None:
        self.before, self.source, self.spans = before, source, spans
        self.chips = [ch for ch in before if CHIP_FIRST <= ord(ch) < OTHER_CHIP_FIRST]
        plain, where = _squeezed(masked(source))
        self.table: dict[int, tuple[int, int]] = {}
        for a, b, size in SequenceMatcher(None, before.translate(TYPOGRAPHY), plain.translate(TYPOGRAPHY),
                                          autojunk=False).get_matching_blocks():
            for k in range(size):
                self.table[a + k] = where[b + k]
        # The brackets of a link by reference are not on the page: nothing maps inside them.
        for m in SHORTCUT_LINK.finditer(source):
            if m[1].lower() in references:
                self.table = {k: v for k, v in self.table.items() if not m.start() <= v[0] < m.end()}

    def atom(self, ch: str) -> tuple[int, int] | None:
        return self.spans[self.chips.index(ch)] if ch in self.chips else None

    def _edge(self, i: int, side: int) -> int | None:
        if is_chip(self.before[i]):
            span = self.atom(self.before[i])
            return span[side] if span else None
        return self.table[i][side] if i in self.table else None

    def after(self, i: int) -> int | None:
        """The offset just after character i of the rendered text."""
        return 0 if i < 0 else self._edge(i, 1)

    def at(self, i: int) -> int | None:
        """The offset of character i of the rendered text."""
        return len(self.source) if i >= len(self.before) else self._edge(i, 0)


def _word_changes(old: str, new: str) -> list[tuple[int, int, str]]:
    """Word by word, the changes from `old` to `new`, trimmed to what differs."""
    out = []
    for start, end, new_start, new_end in changes(old, new, exact=True):
        gone, text = old[start:end], new[new_start:new_end]
        head = 0
        while head < min(len(gone), len(text)) and gone[head] == text[head]:
            head += 1
        tail = 0
        while tail < min(len(gone), len(text)) - head and gone[-1 - tail] == text[-1 - tail]:
            tail += 1
        out.append((start + head, end - tail, text[head:len(text) - tail]))
    return out


def _balanced(masked: str, at: int, end: int) -> tuple[int, int]:
    """Widen a change over an emphasis marker it would leave without its pair."""
    for marker in MARKERS:
        if masked[at:end].count(marker) % 2:
            if masked[:at].endswith(marker):
                at -= len(marker)
            elif masked[end:].startswith(marker):
                end += len(marker)
    return at, end


def _escaped_text(text: str, starts_line: bool) -> str:
    text = ACTIVE.sub(r"\\\1", text)
    return lead_escaped(text) if starts_line else text


def lead_escaped(text: str) -> str:
    return LEAD.sub(lambda m: f"{m[0][:-1]}\\{m[0][-1]}", text)


def patch(before: str, after: str, source: str, spans: list[tuple[int, int]],
          references: set[str] | None = None) -> str | None:
    """`source` with the change from `before` to `after`, or None if some
    change has no place in it. `spans` are the block's atoms."""
    kept = [ch for ch in after if is_chip(ch) and ch in before]  # the others are new: typed text for now
    remaining = iter(ch for ch in before if is_chip(ch))
    if not all(ch in remaining for ch in kept):
        return None  # a chip moved
    gone = {ch for ch in before if is_chip(ch)} - set(kept)
    offsets = Offsets(before, source, spans, references or set())
    if len(offsets.chips) != len(spans):
        return None  # this is not the text the chips of this block were counted on
    edits: list[tuple[int, int, str]] = []
    for ch in gone:
        span = offsets.atom(ch)
        if span is None:
            return None
        edits.append((*span, ""))
    hidden = masked(source)
    old_at = new_at = 0
    for chip in [*kept, None]:
        old_end = before.index(chip, old_at) if chip else len(before)
        new_end = after.index(chip, new_at) if chip else len(after)
        # Positions of this stretch of `before`, without the chips deleted from it.
        real = [i for i in range(old_at, old_end) if before[i] not in gone]
        old = "".join(before[i] for i in real)
        for start, end, text in _word_changes(old, after[new_at:new_end]):
            if start == end:
                at = offsets.after(real[start - 1] if start else old_at - 1)
                if at is None:
                    at = offsets.at(real[start] if start < len(real) else old_end)
                stop = at
            else:
                at, stop = offsets.at(real[start]), offsets.after(real[end - 1])
            if at is None or stop is None or stop < at or "<!--" in source[at:stop]:
                return None
            edits.append((*_balanced(hidden, at, stop), text))
        old_at, new_at = old_end + 1, new_end + 1
    edits.sort()
    if any(a[1] > b[0] for a, b in zip(edits, edits[1:])):
        return None
    out = source
    for at, end, text in reversed(edits):
        typed = _escaped_text(text, at == 0 or out[:at].endswith("\n"))
        # After a closing bracket, a parenthesis would make the chip a link.
        out = out[:at] + ("\\" if typed.startswith("(") and out[:at].endswith("]") else "") + typed + out[end:]
    # Every atom but those deleted is still there, whatever the alignment did.
    deleted = {offsets.atom(ch) for ch in gone}
    if [m[0] for m in atoms(out)] != [m[0] for m in atoms(source) if m.span() not in deleted]:
        return None
    return out
