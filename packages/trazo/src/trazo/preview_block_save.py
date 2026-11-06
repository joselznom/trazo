"""Writing an edit of the preview's text back to a block of Markdown.

POST /__block {file, h, before, after} takes the block's rendered text before
and after the edit, with each locked chip as one private-use character:
U+E000 + i for the block's atom i (trazo.preview_blocks), U+F000 and up for any
other chip on the page. The text is aligned with the Markdown, and the words
that changed are replaced in it, with what was typed escaped so it reads
literally. The result is then checked: built alone, the block must read
exactly what the author left on the page, keep its kind, and keep the source
of its chips. If not, nothing is written and the answer says why ({refused,
markdown}). {file, h, markdown} replaces the block with Markdown the author
wrote, unchecked. A block that changed on disk answers 409, unless the author
chose to keep their text ({keep: true}): the edit is then made to the Markdown
the key stood for (trazo.preview_blocks.SOURCES) and merged into the block most
like it. A save goes through change_source, so it has an undo and counts as the
author's own change.

Characters from U+E800 in `after` are new chips, named by {mentions}
(trazo.preview_mentions). U+F8FF in `after` is a paragraph break: the block becomes several, each checked
alone (in a list, new items). An empty `after` deletes the block. {into, previous}
joins the block to the one before it, whose key and text they are. The answer's
`parts` are the keys of the blocks the edit left.
"""

from __future__ import annotations

import re
import subprocess
from difflib import SequenceMatcher
from html.parser import HTMLParser
from typing import Any

from . import pandoc_run
from .counts import VOID
from .edit_diff import Lines, changes, merge
from .local_server import Conflict, RequestError
from .preview_blocks import ITEM, PROTECTED, QUOTE, SOURCES, Block, atoms, atoms_in, blocks_of, editable, escaped
from .preview_mentions import sources
from .preview_sources import change_source, get_source, source_path

CHIP_FIRST, OTHER_CHIP_FIRST, CHIP_LAST = 0xE000, 0xF000, 0xF8FE
BREAK = "\uf8ff"
# Between two blocks that can join: blank lines and the markers of the second.
GAP = re.compile(r"[\s>]*(?:(?:[-*+]|\d+[.)]|#[.)])[ \t]+)?")
BLANK_LINE = re.compile(r"[ \t]*\r?\n")
ACTIVE = re.compile(r"([\\`*_\[\]{}$@~^<>#|])")
# Typed text that would start a list, a definition or a caption: ends in the character to escape.
LEAD = re.compile(r"^\s*(?:[-+:](?=\s)|(?:\d+|[A-Za-z])[.)](?=\s)|Table:)")
# What Pandoc's smart typography makes of plain characters, undone.
TYPOGRAPHY = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "\u00a0": " "})
TYPED = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "—": "---", "–": "--", "…": "..."})
MARKERS = ("***", "**", "__", "~~", "*", "_", "~", "^")
DEFINITION = re.compile(r"(?m)^ {0,3}\[([^\]^]+)\]:.*$")
SHORTCUT_LINK = re.compile(r"\[([^\[\]]+)\](?![(\[{:])")
QUOTE_LINE = re.compile(rf"(?m)^{QUOTE.pattern}")
CHECK_TIMEOUT = 30  # seconds; Pandoc takes a few hundredths for one block
SAME_BLOCK = 0.5    # how alike a changed block and the one the author started from must be


def _is_chip(ch: str) -> bool:
    return CHIP_FIRST <= ord(ch) <= CHIP_LAST


def _masked(source: str) -> str:
    return PROTECTED.sub(lambda m: "\0" * len(m.group(0)), source)


def _squeezed(source: str) -> tuple[str, list[tuple[int, int]]]:
    """`source` with each run of spaces as one, and where each character was:
    the page has no trace of the source's line breaks or indents."""
    found = list(re.finditer(r"\s+|\S", source))
    return "".join(" " if m[0].isspace() else m[0] for m in found), [m.span() for m in found]


class _Offsets:
    """Where each character of the rendered text is in the block's Markdown."""

    def __init__(self, before: str, source: str, spans: list[tuple[int, int]], references: set[str]) -> None:
        self.before, self.source, self.spans = before, source, spans
        plain, where = _squeezed(_masked(source))
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
        i = ord(ch) - CHIP_FIRST
        return self.spans[i] if ord(ch) < OTHER_CHIP_FIRST and i < len(self.spans) else None

    def _edge(self, i: int, side: int) -> int | None:
        if _is_chip(self.before[i]):
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
    return _lead_escaped(text) if starts_line else text


def _lead_escaped(text: str) -> str:
    return LEAD.sub(lambda m: f"{m[0][:-1]}\\{m[0][-1]}", text)


def patch(before: str, after: str, source: str, spans: list[tuple[int, int]],
          references: set[str] | None = None) -> str | None:
    """`source` with the change from `before` to `after`, or None if some
    change has no place in it. `spans` are the block's atoms."""
    kept = [ch for ch in after if _is_chip(ch) and ch in before]  # the others are new: typed text for now
    remaining = iter(ch for ch in before if _is_chip(ch))
    if not all(ch in remaining for ch in kept):
        return None  # a chip moved
    gone = {ch for ch in before if _is_chip(ch)} - set(kept)
    offsets = _Offsets(before, source, spans, references or set())
    changes: list[tuple[int, int, str]] = []
    for ch in gone:
        span = offsets.atom(ch)
        if span is None:
            return None
        changes.append((*span, ""))
    masked = _masked(source)
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
            if at is None or stop is None or stop < at:
                return None
            changes.append((*_balanced(masked, at, stop), text))
        old_at, new_at = old_end + 1, new_end + 1
    changes.sort()
    if any(a[1] > b[0] for a, b in zip(changes, changes[1:])):
        return None
    out = source
    for at, end, text in reversed(changes):
        out = out[:at] + _escaped_text(text, at == 0 or out[:at].endswith("\n")) + out[end:]
    return out


class _Page(HTMLParser):
    """The text of Pandoc's HTML and the tags of its top-level blocks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.kinds: list[str] = []
        self.depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not self.depth:
            self.kinds.append(tag)
        if tag == "br":
            self.text.append(" ")
        self.depth += tag not in VOID

    def handle_endtag(self, tag: str) -> None:
        self.depth -= tag not in VOID

    def handle_data(self, data: str) -> None:
        self.text.append(data)


def _without_atoms(source: str) -> str:
    return PROTECTED.sub(lambda m: "" if m["atom"] and not escaped(source, m.start()) else m[0], source)


def _comparable(text: str) -> str:
    """Text as the check compares it: without chips, runs of spaces or smart typography."""
    return " ".join("".join(ch for ch in text if not _is_chip(ch)).translate(TYPED).split())


def alone_html(block: str, definitions: str) -> str:
    """The HTML of a block without its atoms, built alone."""
    from . import preview
    source = _without_atoms(QUOTE_LINE.sub("", block))
    result = pandoc_run.run(["--from=markdown", "--to=html", "--wrap=none"],
                            input=f"{source}\n\n{definitions}".encode(), timeout=CHECK_TIMEOUT,
                            command=preview.PROJECT.command("pandoc"), cwd=preview.ROOT)
    if result.returncode:
        raise OSError(pandoc_run.decode(result.stderr))
    return pandoc_run.decode(result.stdout)


def _alone(block: str, definitions: str) -> tuple[str, list[str]]:
    """What a block without its atoms reads built alone, and its kind."""
    page = _Page()
    page.feed(alone_html(block, definitions))
    return _comparable("".join(page.text)), page.kinds


def check(before: str, after: str, source: str, patched: str, definitions: str) -> str:
    """Why `patched` is not what the author left on the page ("" if it is)."""
    remaining = iter(m[0] for m in atoms(source))
    if not all(m[0] in remaining for m in atoms(patched)):
        return "a locked element would change"
    try:
        text, kinds = _alone(source, definitions)
        built = [_alone(part, definitions) for part in patched.split(BREAK)]
    except (OSError, subprocess.SubprocessError) as exc:
        return f"could not check the result: {exc}"
    if text != _comparable(before):
        return "this paragraph has more on the page than its Markdown shows"
    if [part for part, _ in built] != [_comparable(part) for part in after.split(BREAK)]:
        return "the Markdown would not read as written"
    if any(new_kinds != kinds for _, new_kinds in built):
        return "the paragraph would become something else"
    return ""


def _squashed(request: dict[str, Any], key: str) -> str:
    value = request.get(key)
    if not isinstance(value, str):
        raise RequestError(f"{key} must be a string")
    return re.sub(f" ?{BREAK} ?", BREAK, " ".join(value.split()))


def _find(text: str, key: Any, sha256: str) -> tuple[Block, Lines]:
    lines = Lines(text)
    for block in editable(text, lines):
        if block.key == key:
            return block, lines
    raise Conflict("this paragraph changed on disk", sha256=sha256)


def _rewritten(request: dict[str, Any], text: str, source: str, spans: list[tuple[int, int]],
               lead: tuple[str, str] = ("", "")) -> tuple[str | None, str]:
    """`source` with the author's edit, or None and why not. `text` is its file,
    and `lead` the rendered text and Markdown of a block it joins."""
    before, after = _squashed(request, "before"), _squashed(request, "after")
    if not after:
        return "", ""
    references = {m[1].lower() for m in DEFINITION.finditer(text)}
    patched = patch(before, after, source, spans, references)
    if patched is None:
        return None, "this change has no place in the Markdown"
    first, *others = patched.split(BREAK)
    patched = BREAK.join([first, *map(_lead_escaped, others)])
    definitions = "\n".join(m[0] for m in DEFINITION.finditer(text))
    shown, written = lead
    why = check(shown + before, shown + after, written + source, written + patched, definitions)
    if why:
        return None, why
    patched = written + patched
    for ch, mention in sources(request).items():
        patched = patched.replace(ch, mention)
    return patched, ""


def _spans(text: str, block: Block, lines: Lines) -> list[tuple[int, int]]:
    return [(start - block.at, end - block.at) for start, end, _ in atoms_in(text, block, lines)]


def _kept_mine(request: dict[str, Any], text: str, sha256: str) -> tuple[Block, str | None, str]:
    """The block most like the one the author started from, with their edit
    merged in, or None and why not: where both changed the same words, theirs stay."""
    source = SOURCES.get(request.get("h"))
    if source is None:
        raise Conflict("the preview no longer knows this paragraph", sha256=sha256)
    matcher = SequenceMatcher(None, autojunk=False)
    matcher.set_seq2(source.split())

    def likeness(block: Block) -> float:
        matcher.set_seq1(text[block.at:block.end].split())
        return matcher.ratio()
    block = max(editable(text), key=likeness, default=None)
    if block is None or likeness(block) < SAME_BLOCK:
        raise Conflict("this paragraph is no longer in the file", sha256=sha256)
    alone = Block(0, len(source), "")
    patched, why = (_markdown(request), "") if "markdown" in request else (
        _rewritten(request, text, source, _spans(source, alone, Lines(source))))
    if not patched:
        return block, patched, why
    return block, merge(source, text[block.at:block.end], patched), why


def _markdown(request: dict[str, Any]) -> str:
    markdown = request["markdown"]
    if not isinstance(markdown, str):
        raise RequestError("markdown must be a string")
    return markdown


def _joined(request: dict[str, Any], text: str, block: Block, lines: Lines, sha256: str) -> tuple[Block, str | None, str]:
    """The span of this block and the one before it, and their Markdown joined, or None and why not."""
    blocks = blocks_of(text, lines)
    at = blocks.index(block)
    previous = blocks[at - 1] if at else None
    if previous is None or previous.key != request["into"]:
        raise Conflict("the paragraph before this one changed on disk", sha256=sha256)
    both = Block(previous.at, block.end, previous.key)
    if not GAP.fullmatch(text, previous.end, block.at):
        return both, None, "there is something between these two paragraphs"
    lead = (f"{_squashed(request, 'previous')} ", f"{text[previous.at:previous.end]} ")
    new, why = _rewritten(request, text, text[block.at:block.end], _spans(text, block, lines), lead)
    return both, new or None, why or ("" if new else "there is nothing to join")


def _between(text: str, block: Block) -> str:
    """What goes between two parts of a split block: a blank line, or in a list a new item."""
    newline = "\r\n" if "\r\n" in text else "\n"
    prefix = text[text.rfind("\n", 0, block.at) + 1:block.at]
    quote = QUOTE.match(prefix)
    quoted = quote[0] if quote else ""
    marker = prefix[len(quoted):]
    if ITEM.match(marker):
        return newline + quoted + re.sub(r"\d+", lambda m: str(int(m[0]) + 1), marker, count=1)
    return newline + quoted.rstrip() + newline + prefix


def _whole_lines(text: str, block: Block) -> tuple[int, int]:
    """The span of a block with its marker and line ending, and of the blank line
    after it when one comes before it too."""
    start = text.rfind("\n", 0, block.at) + 1
    end = text.find("\n", block.end)
    end = len(text) if end < 0 else end + 1
    blank = BLANK_LINE.match(text, end)
    if blank and (not start or BLANK_LINE.fullmatch(text, text.rfind("\n", 0, start - 1) + 1, start)):
        end = blank.end()
    return start, end


def change_block(request: Any) -> dict[str, Any]:
    """Write an edit of one block; see the module's docstring."""
    if not isinstance(request, dict):
        raise RequestError("request body must be a JSON object")
    _, file = source_path(request.get("file"))
    state = get_source(file)
    text = state["text"]
    try:
        block, lines = _find(text, request.get("h"), state["sha256"])
    except Conflict:
        if not request.get("keep"):
            raise
        block, new, why = _kept_mine(request, text, state["sha256"])
    else:
        if "into" in request:
            block, new, why = _joined(request, text, block, lines, state["sha256"])
        else:
            new, why = (_markdown(request), "") if "markdown" in request else (
                _rewritten(request, text, text[block.at:block.end], _spans(text, block, lines)))
    source = text[block.at:block.end]
    if block.heading and new is not None and (not new or BREAK in new):
        new, why = None, "a heading stays one line of text"
    if new is None:
        return {"refused": why, "file": file, "markdown": source}
    if new == source:
        return {"saved": True, "file": file, "markdown": new, "parts": [block.key]}
    start, end = (block.at, block.end) if new else _whole_lines(text, block)
    written = new.replace(BREAK, _between(text, block))
    changed = text[:start] + written + text[end:]
    change_source({"file": file, "text": changed, "base_sha256": state["sha256"]})
    parts = [b.key for b in editable(changed) if start <= b.at < start + len(written)]
    return {"saved": True, "file": file, "markdown": written, "parts": parts}
