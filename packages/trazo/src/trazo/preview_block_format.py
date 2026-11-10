"""Bold, italic and underline on the preview's text.

POST /__block-format {file, h, before, style, from, to}: `style` is strong,
em or underline, and from and to are offsets into the block's rendered text, in the form
POST /__block takes it (trazo.preview_block_save). It is a toggle: text that
all has the style loses it, and any other gets it. Markers go in or come out
of the Markdown, and the block built alone must then have exactly the styles
expected, character by character, or nothing is written ({refused}). The
answer's `save_id` names the save, as POST /__block's does.
Underline is Pandoc's bracketed span `[text]{.underline}`, which Word gets as
underlined text; bold and italic are `**` and `*`.
"""

from __future__ import annotations

import re
import subprocess
from html.parser import HTMLParser
from typing import Any

from .local_server import RequestError
from .preview_block_patch import Offsets, masked
from .preview_block_save import DEFINITION, _comparable, _definitions, _find, _spans, _squashed, alone_html
from .preview_blocks import PROTECTED, atoms, editable
from .preview_sources import change_source, get_source, source_path

MARKER = {"strong": "**", "em": "*"}
# The emphasis a run of markers stands for, by its length.
STYLES = {1: {"em"}, 2: {"strong"}, 3: {"strong", "em"}}
# Underscores inside a word are text.
RUN = re.compile(r"\*{1,3}|(?<!\w)_{1,3}|_{1,3}(?!\w)")
UNDERLINE = "underline"
# The end of an underline span; Pandoc also reads `.ul` as one.
UNDERLINE_END = re.compile(r"\]\{\.(?:underline|ul)\}")
# The style each tag of Pandoc's HTML stands for.
TAGS = {"strong": "strong", "em": "em", "u": UNDERLINE}


def _letters(text: str) -> str:
    """Text as the check compares it, character by character."""
    return _comparable(text).replace(" ", "")


class _Styled(HTMLParser):
    """Each character of Pandoc's HTML with the styles on it, spaces left out."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chars: list[tuple[str, frozenset[str]]] = []
        self.depth = {style: 0 for style in TAGS.values()}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in TAGS:
            self.depth[TAGS[tag]] += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in TAGS:
            self.depth[TAGS[tag]] -= 1

    def handle_data(self, data: str) -> None:
        styles = frozenset(style for style, depth in self.depth.items() if depth)
        self.chars += [(ch, styles) for ch in _letters(data)]


def _styled(block: str, definitions: str, heading: bool) -> list[tuple[str, frozenset[str]]]:
    page = _Styled()
    page.feed(alone_html(block, definitions, heading))
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


def _unescaped(text: str, i: int) -> bool:
    slashes = len(text[:i]) - len(text[:i].rstrip("\\"))
    return slashes % 2 == 0


def _brackets(source: str) -> str:
    """`source` masked, with the `]` of a link's `](url)` or `][ref]` back:
    the mask hides it, and its `[` is still there."""
    plain = list(masked(source))
    for m in PROTECTED.finditer(source):
        if m[0].startswith(("](", "][")):
            plain[m.start()] = "]"
    return "".join(plain)


def _underlines(source: str) -> list[tuple[int, int, int]]:
    """The underline spans of a block's Markdown: where its `[` is, and where
    its `]{.underline}` starts and ends."""
    plain, out = _brackets(source), []
    for close in UNDERLINE_END.finditer(source):
        if plain[close.start()] != "]" or not _unescaped(source, close.start()):
            continue  # in code or math, or an escaped bracket
        depth, i = 0, close.start() - 1
        while i >= 0:
            if plain[i] in "[]" and _unescaped(source, i):
                if plain[i] == "]":
                    depth += 1
                elif not depth:
                    break
                else:
                    depth -= 1
            i -= 1
        if i >= 0:
            out.append((i, close.start(), close.end()))
    return out


def _with_underline(source: str, at: int, end: int) -> str:
    """`source` with source[at:end] in one underline span, without those inside
    it; a span the selection only starts or ends in joins it whole."""
    for open_at, _, close_end in _underlines(source):
        if open_at < at < close_end <= end or at <= open_at < end < close_end:
            at, end = min(at, open_at), max(end, close_end)
    inner = sorted(edge for open_at, close, close_end in _underlines(source)
                   if at <= open_at and close_end <= end for edge in ((open_at, open_at + 1), (close, close_end)))
    out, last = [], at
    for start, stop in inner:
        out.append(source[last:start])
        last = stop
    return f"{source[:at]}[{''.join(out)}{source[last:end]}]{{.underline}}{source[end:]}"


def _underlined(text: str) -> str:
    """Text left of a span the selection leaves: underlined again, unless it is
    only emphasis markers, which stay as they are to pair with their other half."""
    return f"[{text}]{{.underline}}" if text.strip("*_") else text


def _without_underline(source: str, at: int, end: int) -> str | None:
    """`source` without underline on source[at:end]: each span over it loses its
    brackets, and its text outside the selection stays underlined. None if no
    span is over it."""
    spans = [span for span in _underlines(source) if span[0] < end and at < span[1]]
    # A span inside another is the outer one's text.
    spans = [s for s in spans if not any(o[0] < s[0] and s[2] <= o[2] for o in spans)]
    if not spans:
        return None
    edits = []
    for open_at, close, close_end in spans:
        if open_at < at:
            # An emphasis that opens right before the selection stays with it.
            left = source[open_at + 1:at]
            marker = re.search(r"(?<!\S)[*_]+$", left)
            core = left[:marker.start()] if marker else left
            words = core.rstrip()
            edits.append((open_at, at, _underlined(words) + left[len(words):] if words else left))
        else:
            edits.append((open_at, open_at + 1, ""))
        if end < close:
            # And one that closes right after it.
            right = source[end:close]
            marker = re.match(r"[*_]+(?!\S)", right)
            core = right[marker.end():] if marker else right
            words = core.lstrip()
            edits.append((end, close_end, right[:len(right) - len(words)] + _underlined(words) if words else right))
        else:
            edits.append((close, close_end, ""))
    for start, stop, text in sorted(edits, reverse=True):
        source = source[:start] + text + source[stop:]
    return source


def _with_style(source: str, at: int, end: int, style: str) -> str:
    """`source` with source[at:end] in the style, without the same style inside it."""
    if style == UNDERLINE:
        return _with_underline(source, at, end)
    inner = [run for pair in _pairs(masked(source)) for run in pair
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
    if style == UNDERLINE:
        return _without_underline(source, at, end)
    holding = [(a, b) for a, b in _pairs(masked(source))
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


def _toggled(request: dict[str, Any], text: str, source: str, spans: list[tuple[int, int]],
             heading: bool) -> tuple[str | None, bool, str]:
    """`source` with the style toggled, whether the style is now on, and why not."""
    style = request.get("style")
    if style not in (*MARKER, UNDERLINE):
        raise RequestError("style must be strong, em or underline")
    before = _squashed(request, "before")
    start, end = _range(request, before)
    if start == end:
        return None, False, "there is no text selected"
    references = {m[1].lower() for m in DEFINITION.finditer(text)}
    offsets = Offsets(before, source, spans, references)
    if len(offsets.chips) != len(spans):
        return None, False, "this paragraph changed on the page"
    at, stop = offsets.at(start), offsets.after(end - 1)
    if at is None or stop is None or stop <= at:
        return None, False, "this text has no place in the Markdown"
    definitions = _definitions(text)
    try:
        chars = _styled(source, definitions, heading)
        first, last = len(_letters(before[:start])), len(_letters(before[:end]))
        if len(chars) != len(_letters(before)):
            return None, False, "this paragraph has more on the page than its Markdown shows"
        on = not (last > first and all(style in styles for _, styles in chars[first:last]))
        patched = _with_style(source, at, stop, style) if on else _without_style(source, at, stop, style)
        changed = {style}
        wanted = [(ch, (styles | changed if on else styles - changed) if first <= n < last else styles)
                  for n, (ch, styles) in enumerate(chars)]
        if patched is None or _styled(patched, definitions, heading) != wanted:
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
    new, on, why = _toggled(request, text, text[block.at:block.end], _spans(text, block, lines), block.heading)
    if new is None:
        return {"refused": why, "file": file}
    changed = text[:block.at] + new + text[block.end:]
    saved = change_source({"file": file, "text": changed, "base_sha256": state["sha256"]})
    parts = [b.key for b in editable(changed) if b.at == block.at]
    return {"saved": True, "file": file, "on": on, "parts": parts, "save_id": saved.get("save_id"),
            **({"warning": saved["warning"]} if saved.get("warning") else {})}
