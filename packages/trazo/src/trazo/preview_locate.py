"""Map a rendered paragraph back to its place in a Markdown source file.

Pandoc's markdown reader has no source positions, so both sides are
normalized to lowercase word tokens (Markdown syntax, citations, attributes
and entities removed; value placeholders replaced by their text when known)
and the source block with the best token overlap (Dice coefficient) wins.
Fenced code, `!include` lines and headings are never candidates.
"""

import html
import re
from collections import Counter

FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^\s{0,3}#{1,6}(\s|$)")
LIST_ITEM = re.compile(r"^\s*([-*+]|\d+[.)])\s")
PLACEHOLDER = re.compile(r"\{\{v:([A-Za-z0-9_.\-]+)(\|[A-Za-z0-9_.\-]+)?\}\}")
TOKEN = re.compile(r"\w+", re.UNICODE)
# Rendered numeric citations such as [4], [1], [2] or [3]-[5].
RENDERED_CITE = re.compile(r"\[\d+(?:\s*[,–—-]\s*\d+)*\]")


MATH = re.compile(r"\$\$.*?\$\$|\$[^$\n]+\$", re.S)


def lookup(values, name):
    """A value's text ("key" or "key|format"; expand.Values formats on demand), else " "."""
    try:
        return values[name]
    except Exception:  # unknown key or a format that does not fit
        return " "


def normalize_source(text, values=None):
    """Tokens of a source block; TeX math is kept as TeX words (the page
    side contributes the same text from the MathML annotation)."""
    values = values or {}
    text = PLACEHOLDER.sub(lambda m: lookup(values, m.group(1) + (m.group(2) or "")), text)
    count = Counter()
    pieces, last = [], 0
    for m in MATH.finditer(text):
        pieces.append(text[last:m.start()])
        count += tokens(m.group(0).replace("$", " "))
        last = m.end()
    pieces.append(text[last:])
    return count + tokens(strip_markup(" ".join(pieces)))


def strip_markup(text):
    text = re.sub(r"\[[^\]]*@[^\]]*\]", " ", text)             # [@a; @b]
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)       # images: keep alt
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)       # links
    text = re.sub(r"\{[^{}]*\}", " ", text)                    # {#id .cls}
    text = re.sub(r"@[A-Za-z][\w:.\-]*", " ", text)            # @fig:x, @key
    text = re.sub(r"<[^>]+>", " ", text)                       # raw html
    return re.sub(r"[~^]([^~^\s]+)[~^]", r"\1", text)          # ~sub~ ^sup^


def normalize_page(text):
    return tokens(RENDERED_CITE.sub(" ", text))


def tokens(text):
    text = html.unescape(text).replace("\\", " ").replace("_", " ")
    return Counter(TOKEN.findall(text.lower()))


def blocks(source):
    """Candidate paragraphs as (start_line, end_line, text), 1-based."""
    result, current, fence = [], [], None

    def flush():
        if current:
            result.append((current[0][0], current[-1][0], " ".join(t for _, t in current)))
            current.clear()

    for number, line in enumerate(source.replace("\r\n", "\n").split("\n"), 1):
        marker = FENCE.match(line)
        if fence is not None:
            if marker and marker.group(1) == fence:
                fence = None
            continue
        if marker:
            flush()
            fence = marker.group(1)
            continue
        if not line.strip() or HEADING.match(line) or line.lstrip().startswith("!include"):
            flush()
            continue
        if LIST_ITEM.match(line):
            flush()
        current.append((number, line.strip()))
    flush()
    return result


def dice(a, b):
    total = sum(a.values()) + sum(b.values())
    if not total:
        return 0.0
    return 2 * sum((a & b).values()) / total


def locate(source, text, values=None, read_include=None):
    """Best block for the rendered `text`: {start_line, end_line, score}.

    `read_include(path_text)` may return the text of an `!include`d file (or
    None). If the best match is inside one, the result points at the
    `!include` line and carries `include` (the path as written).
    """
    page = normalize_page(text)
    best = (0.0, 1, 1, None)
    for start, end, block in blocks(source):
        score = dice(page, normalize_source(block, values))
        if score > best[0]:
            best = (score, start, end, None)
    if read_include:
        for number, line in enumerate(source.replace("\r\n", "\n").split("\n"), 1):
            if not line.startswith("!include "):
                continue
            path_text = line.split(None, 1)[1].strip()
            included = read_include(path_text)
            if not included:
                continue
            for _, _, block in blocks(included):
                score = dice(page, normalize_source(block, values))
                if score > best[0]:
                    best = (score, number, number, path_text)
    result = {"start_line": best[1], "end_line": best[2], "score": round(best[0], 3)}
    if best[3]:
        result["include"] = best[3]
    return result
