"""Writing an edit of the preview's text back to a block of Markdown.

POST /__block {file, h, before, after} takes the block's rendered text before
and after the edit, with each locked chip as one private-use character: from
U+E000 for the block's atoms (trazo.preview_blocks), in their order, and from
U+F000 for any other chip on the page. The text is aligned with the Markdown, and the words
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
from .edit_diff import Lines, merge
from .local_server import Conflict, RequestError
from .preview_block_patch import BREAK, CHIP_FIRST, is_chip, lead_escaped, patch
from .preview_blocks import ITEM, PROTECTED, QUOTE, SOURCES, Block, atoms, atoms_in, blank, blocks_of, editable, escaped
from .preview_mentions import sources
from .preview_sources import change_source, get_source, source_path

# Between two blocks that can join: blank lines and the markers of the second.
GAP = re.compile(r"[\s>]*(?:(?:[-*+]|\d+[.)]|#[.)])[ \t]+)?")
BLANK_LINE = re.compile(r"[ \t]*\r?\n")
# What Pandoc's smart typography makes of plain characters, undone.
TYPED = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "—": "---", "–": "--", "…": "..."})
DEFINITION = re.compile(r"(?m)^ {0,3}\[([^\]^@]+)\]:.*$")
QUOTE_LINE = re.compile(rf"(?m)^{QUOTE.pattern}")
CHECK_TIMEOUT = 30  # seconds; Pandoc takes a few hundredths for one block
SAME_BLOCK = 0.5    # how alike a changed block and the one the author started from must be


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
    """`source` with a chip's character for each atom: what `**[@a]**` holds still counts as text."""
    return PROTECTED.sub(lambda m: chr(CHIP_FIRST) if m["atom"] and not escaped(source, m.start()) else m[0], source)


def _comparable(text: str) -> str:
    """Text as the check compares it: without chips, runs of spaces or smart typography."""
    return " ".join("".join(ch for ch in text if not is_chip(ch)).translate(TYPED).split())


def alone_html(block: str, definitions: str, heading: bool = False) -> str:
    """The HTML of a block without its atoms, built alone: a heading's text as a
    heading, since `1. Introduction` is no list there."""
    from . import preview
    source = ("# " if heading else "") + _without_atoms(QUOTE_LINE.sub("", block))
    result = pandoc_run.run(["--from=markdown", "--to=html", "--wrap=none"],
                            input=f"{source}\n\n{definitions}".encode(), timeout=CHECK_TIMEOUT,
                            command=preview.PROJECT.command("pandoc"), cwd=preview.ROOT)
    if result.returncode:
        raise OSError(pandoc_run.decode(result.stderr))
    return pandoc_run.decode(result.stdout)


def _alone(block: str, definitions: str, heading: bool = False) -> tuple[str, list[str]]:
    """What a block without its atoms reads built alone, and its kind."""
    page = _Page()
    page.feed(alone_html(block, definitions, heading))
    return _comparable("".join(page.text)), page.kinds


def check(before: str, after: str, source: str, patched: str, definitions: str, heading: bool = False) -> str:
    """Why `patched` is not what the author left on the page ("" if it is)."""
    remaining = iter(m[0] for m in atoms(source))
    if not all(m[0] in remaining for m in atoms(patched)):
        return "a locked element would change"
    try:
        text, kinds = _alone(source, definitions, heading)
        built = [_alone(part, definitions, heading) for part in patched.split(BREAK)]
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


def _definitions(text: str) -> str:
    return "\n".join(m[0] for m in DEFINITION.finditer(text))


def _rewritten(request: dict[str, Any], text: str, source: str, spans: list[tuple[int, int]],
               lead: tuple[str, str] = ("", ""), heading: bool = False) -> tuple[str | None, str]:
    """`source` with the author's edit, or None and why not. `text` is its file,
    and `lead` the rendered text and Markdown of a block it joins."""
    before, after = _squashed(request, "before"), _squashed(request, "after")
    if not after:  # what goes must be all of what the page showed
        if "<!--" in source:
            return None, "this paragraph holds a comment the page does not show"
        try:
            same = _alone(source, _definitions(text), heading)[0] == _comparable(before)
        except (OSError, subprocess.SubprocessError) as exc:
            return None, f"could not check the result: {exc}"
        return ("", "") if same else (None, "this paragraph has more on the page than its Markdown shows")
    references = {m[1].lower() for m in DEFINITION.finditer(text)}
    patched = patch(before, after, source, spans, references)
    if patched is None:
        return None, "this change has no place in the Markdown"
    first, *others = patched.split(BREAK)
    patched = BREAK.join([first, *map(lead_escaped, others)])
    shown, written = lead
    why = check(shown + before, shown + after, written + source, written + patched, _definitions(text), heading)
    if why:
        return None, why
    patched = written + patched
    for ch, mention in sources(request).items():  # after a chip, a parenthesis would make it a link
        patched = patched.replace(f"{ch}(", f"{ch}\\(").replace(ch, mention)
    if any(is_chip(ch) for ch in patched):
        return None, "a new element has no Markdown"
    return patched, ""


def _spans(text: str, block: Block, lines: Lines) -> list[tuple[int, int]]:
    return [(start - block.at, end - block.at) for start, end, _ in atoms_in(text, block, lines)]


def _kept_mine(request: dict[str, Any], text: str, sha256: str) -> tuple[Block, str | None, str]:
    """The block most like the one the author started from, with their edit
    merged in, or None and why not: where both changed the same words, theirs stay."""
    source = SOURCES.get(request.get("h"))
    if source is None:
        raise Conflict("the preview no longer knows this paragraph", sha256=sha256)
    if "-" in request["h"]:  # of equal paragraphs: nothing says which one this is now
        raise Conflict("more than one paragraph was like this one", sha256=sha256)
    matcher = SequenceMatcher(None, autojunk=False)
    matcher.set_seq2(source.split())

    def likeness(block: Block) -> float:
        matcher.set_seq1(text[block.at:block.end].split())
        return matcher.ratio()
    heading = str(request.get("h")).startswith("h")
    scored = sorted(((likeness(block), block.at, block) for block in editable(text) if block.heading == heading), reverse=True)
    if not scored or scored[0][0] < SAME_BLOCK:
        raise Conflict("this paragraph is no longer in the file", sha256=sha256)
    if len(scored) > 1 and scored[1][0] == scored[0][0]:
        raise Conflict("more than one paragraph is like this one", sha256=sha256)
    block = scored[0][2]
    alone = Block(0, len(source), "")
    patched, why = (_markdown(request), "") if "markdown" in request else (
        _rewritten(request, text, source, _spans(source, alone, Lines(source)), heading=heading))
    current = text[block.at:block.end]
    if not patched:  # deleted: not if the file's change would go with it
        return block, patched if current == source else None, why or "the file changed what you deleted"
    merged = merge(source, current, patched)
    if "markdown" in request:
        return block, merged, ""
    # Two good edits can merge into a bad one: it must read as the two texts merged.
    definitions = _definitions(text)
    try:
        now, kinds = _alone(current, definitions, heading)
        wanted = merge(_alone(source, definitions, heading)[0], now, _comparable(_squashed(request, "after").replace(BREAK, " ")))
        built = [_alone(part, definitions, heading) for part in merged.split(BREAK)]
    except (OSError, subprocess.SubprocessError) as exc:
        return block, None, f"could not check the result: {exc}"
    if " ".join(part for part, _ in built) != wanted or any(part_kinds != kinds for _, part_kinds in built):
        return block, None, "your text and the file's cannot be combined here"
    return block, merged, ""


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


def _holds_more(text: str, block: Block) -> bool:
    """Whether a list item goes on after this block: taking it out would take its marker."""
    prefix = QUOTE_LINE.sub("", text[text.rfind("\n", 0, block.at) + 1:block.at])
    if not ITEM.match(prefix):
        return False
    following = next((line for line in text[block.end:].split("\n")[1:] if not blank(line)), "")
    following = QUOTE_LINE.sub("", following)
    return len(following) - len(following.lstrip()) > len(prefix) - len(prefix.lstrip())


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
                _rewritten(request, text, text[block.at:block.end], _spans(text, block, lines), heading=block.heading))
    source = text[block.at:block.end]
    if block.heading and new is not None and (not new.strip() or BREAK in new or "\n" in new):
        new, why = None, "a heading stays one line of text"
    if new == "" and _holds_more(text, block):
        new, why = None, "this list item holds more than this text"
    if new is None:
        return {"refused": why, "file": file, "markdown": source}
    if new == source:
        return {"saved": True, "file": file, "markdown": new, "parts": [block.key]}
    start, end = (block.at, block.end) if new else _whole_lines(text, block)
    written = new.replace(BREAK, _between(text, block))
    changed = text[:start] + written + text[end:]
    parts = [b.key for b in editable(changed) if start <= b.at < start + len(written)]
    if BREAK in new and len(parts) != new.count(BREAK) + 1:
        return {"refused": "a paragraph cannot start there", "file": file, "markdown": source}
    change_source({"file": file, "text": changed, "base_sha256": state["sha256"]})
    return {"saved": True, "file": file, "markdown": written, "parts": parts}
