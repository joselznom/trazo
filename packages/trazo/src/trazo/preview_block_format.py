"""Bold and italic on the preview's text.

POST /__block-format {file, h, before, style, from, to}: `style` is strong or
em, and from and to are offsets into the block's rendered text, in the form
POST /__block takes it (trazo.preview_block_save). It is a toggle: text that
all has the style loses it, and any other gets it. Markers go in or come out
of the Markdown, and the block built alone must then have exactly the styles
expected, character by character, or nothing is written ({refused}).
"""

from __future__ import annotations

import re
import subprocess
from html.parser import HTMLParser
from typing import Any

from .local_server import RequestError
from .preview_block_save import (
    DEFINITION,
    _comparable,
    _find,
    _masked,
    _Offsets,
    _spans,
    _squashed,
    alone_html,
)
from .preview_blocks import atoms, editable
from .preview_sources import change_source, get_source, source_path

MARKER = {"strong": "**", "em": "*"}
# The emphasis a run of markers stands for, by its length.
STYLES = {1: {"em"}, 2: {"strong"}, 3: {"strong", "em"}}
# Underscores inside a word are text.
RUN = re.compile(r"\*{1,3}|(?<!\w)_{1,3}|_{1,3}(?!\w)")


def _letters(text: str) -> str:
    """Text as the check compares it, character by character."""
    return _comparable(text).replace(" ", "")


class _Styled(HTMLParser):
    """Each character of Pandoc's HTML with the styles on it, spaces left out."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chars: list[tuple[str, frozenset[str]]] = []
        self.depth = {"strong": 0, "em": 0}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.depth:
            self.depth[tag] += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self.depth:
            self.depth[tag] -= 1

    def handle_data(self, data: str) -> None:
        styles = frozenset(tag for tag, depth in self.depth.items() if depth)
        self.chars += [(ch, styles) for ch in _letters(data)]


def _styled(block: str, definitions: str) -> list[tuple[str, frozenset[str]]]:
    page = _Styled()
    page.feed(alone_html(block, definitions))
    return page.chars


def _pairs(masked: str) -> list[tuple[re.Match[str], re.Match[str]]]:
    """The emphasis in a block's Markdown: its opening and closing runs."""
    open_runs: list[re.Match[str]] = []
    pairs = []
    for run in RUN.finditer(masked):
        if open_runs and open_runs[-1][0] == run[0]:
            pairs.append((open_runs.pop(), run))
        else:
            open_runs.append(run)
    return pairs


def _with_style(source: str, at: int, end: int, style: str) -> str:
    """`source` with source[at:end] in the style, without the same style inside it."""
    inner = [run for pair in _pairs(_masked(source)) for run in pair
             if at <= pair[0].start() and pair[1].end() <= end and style in STYLES[len(pair[0][0])]]
    out, last = [], at
    for run in sorted(inner, key=lambda run: run.start()):
        out.append(source[last:run.start()] + _kept(run[0], style))
        last = run.end()
    text = "".join(out) + source[last:end]
    return f"{source[:at]}{MARKER[style]}{text}{MARKER[style]}{source[end:]}"


def _kept(run: str, style: str) -> str:
    """What is left of a run of markers without a style."""
    return run[:len(run) - len(MARKER[style])]


def _without_style(source: str, at: int, end: int, style: str) -> str | None:
    """`source` with the emphasis around source[at:end] closed before it and open
    again after it, or None if none holds it whole."""
    holding = [(a, b) for a, b in _pairs(_masked(source))
               if a.end() <= at and end <= b.start() and style in STYLES[len(a[0])]]
    if not holding:
        return None
    opening, closing = max(holding, key=lambda pair: pair[0].start())
    marker, rest = opening[0], _kept(opening[0], style)
    left, right = source[opening.end():at], source[end:closing.start()]
    words, spaces = left.rstrip(), left[len(left.rstrip()):]
    head = f"{marker}{words}{marker}{spaces}" if words else left
    words = right.lstrip()
    tail = f"{right[:len(right) - len(words)]}{marker}{words}{marker}" if words else right
    return f"{source[:opening.start()]}{head}{rest}{source[at:end]}{rest}{tail}{source[closing.end():]}"


def _range(request: dict[str, Any], before: str) -> tuple[int, int]:
    start, end = request.get("from"), request.get("to")
    if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start <= end <= len(before):
        raise RequestError("from and to must be offsets into before")
    while start < end and before[start].isspace():
        start += 1
    while end > start and before[end - 1].isspace():
        end -= 1
    return start, end


def _toggled(request: dict[str, Any], text: str, source: str, spans: list[tuple[int, int]]) -> tuple[str | None, bool, str]:
    """`source` with the style toggled, whether the style is now on, and why not."""
    style = request.get("style")
    if style not in MARKER:
        raise RequestError("style must be strong or em")
    before = _squashed(request, "before")
    start, end = _range(request, before)
    if start == end:
        return None, False, "there is no text selected"
    references = {m[1].lower() for m in DEFINITION.finditer(text)}
    offsets = _Offsets(before, source, spans, references)
    at, stop = offsets.at(start), offsets.after(end - 1)
    if at is None or stop is None or stop <= at:
        return None, False, "this text has no place in the Markdown"
    definitions = "\n".join(m[0] for m in DEFINITION.finditer(text))
    try:
        chars = _styled(source, definitions)
        first, last = len(_letters(before[:start])), len(_letters(before[:end]))
        if len(chars) != len(_letters(before)):
            return None, False, "this paragraph has more on the page than its Markdown shows"
        on = not (last > first and all(style in styles for _, styles in chars[first:last]))
        patched = _with_style(source, at, stop, style) if on else _without_style(source, at, stop, style)
        changed = {style}
        wanted = [(ch, (styles | changed if on else styles - changed) if first <= n < last else styles)
                  for n, (ch, styles) in enumerate(chars)]
        if patched is None or _styled(patched, definitions) != wanted:
            return None, on, "select all of the formatted text, or none of it"
    except (OSError, subprocess.SubprocessError) as exc:
        return None, False, f"could not check the result: {exc}"
    if [m[0] for m in atoms(patched)] != [m[0] for m in atoms(source)]:
        return None, on, "a locked element would change"
    return patched, on, ""


def change_format(request: Any) -> dict[str, Any]:
    """Toggle a style on part of a block; see the module's docstring."""
    if not isinstance(request, dict):
        raise RequestError("request body must be a JSON object")
    _, file = source_path(request.get("file"))
    state = get_source(file)
    text = state["text"]
    block, lines = _find(text, request.get("h"), state["sha256"])
    new, on, why = _toggled(request, text, text[block.at:block.end], _spans(text, block, lines))
    if new is None:
        return {"refused": why, "file": file}
    changed = text[:block.at] + new + text[block.end:]
    change_source({"file": file, "text": changed, "base_sha256": state["sha256"]})
    parts = [b.key for b in editable(changed) if b.at == block.at]
    return {"saved": True, "file": file, "on": on, "parts": parts}
