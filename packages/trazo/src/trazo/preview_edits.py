"""Locate editable figure and table captions in original Markdown."""

import re


def closing(text, start, left, right):
    depth, escaped = 0, False
    for at in range(start, len(text)):
        ch = text[at]
        if escaped:
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == left:
            depth += 1
        elif ch == right:
            depth -= 1
            if depth == 0:
                return at
    return None


def markdown_context(text):
    """Mask code and comments without changing source offsets."""
    masked, fence = [], None
    for line in text.splitlines(keepends=True):
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if marker:
            if fence is None:
                fence = marker[1]
            elif marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not line[marker.end():].strip():
                fence = None
            masked.append(" " * len(line))
        else:
            masked.append(" " * len(line) if fence else line)
    plain = "".join(masked)
    # Preserve offsets while hiding examples and commented-out images.
    plain = re.sub(r"<!--[\s\S]*?(?:-->|$)", lambda m: " " * len(m[0]), plain)
    plain = re.sub(r"(`+)(?!`)([\s\S]*?)(?<!`)\1(?!`)", lambda m: " " * len(m[0]), plain)
    return plain


def image_captions(text, identifier):
    """Caption spans for identified Pandoc images, including nested inlines.

    Attributes belong to the image after its inline URL or reference label,
    rather than an arbitrary occurrence of the ID.
    """
    plain = markdown_context(text)
    for match in re.finditer(r"!\[", plain):
        escapes = len(plain[:match.start()]) - len(plain[:match.start()].rstrip("\\"))
        if escapes % 2:
            continue
        end = closing(plain, match.end() - 1, "[", "]")
        if end is None:
            continue
        at = end + 1
        while at < len(plain) and plain[at].isspace():
            at += 1
        if at < len(plain) and plain[at] in "([":
            left, right = ("(", ")") if plain[at] == "(" else ("[", "]")
            tail = closing(plain, at, left, right)
            if tail is None:
                continue
            at = tail + 1
        while at < len(plain) and plain[at].isspace():
            at += 1
        attrs = re.match(r"\{([^{}]*)\}", plain[at:])
        if attrs and re.search(r"(?:^|\s)#" + re.escape(identifier) + r"(?:\s|$)", attrs[1]):
            yield match.end(), end


def table_captions(text, identifier):
    """Caption paragraphs introduced by ``Table:`` or ``:`` with a table ID.

    Keep the caption marker, its surrounding whitespace and Pandoc attributes
    outside the editable span. Continuation lines belong to the same paragraph.
    """
    plain = markdown_context(text)
    for match in re.finditer(r"(?m)^ {0,3}(?:Table:|:)\s+([^\n]*(?:\n(?!\s*\n)[^\n]+)*)", plain):
        paragraph = match[1]
        attrs = re.search(r"\{([^{}]*)\}[ \t]*$", paragraph)
        if not attrs or not re.search(r"(?:^|\s)#" + re.escape(identifier) + r"(?:\s|$)", attrs[1]):
            continue
        start = match.start(1)
        end = start + attrs.start()
        while end > start and text[end - 1].isspace():
            end -= 1
        yield start, end


NOTE_OPEN = re.compile(r"^ {0,3}(:{3,})\s*(?:\{[^{}\n]*\.table-note(?=[\s}])[^{}\n]*\}|table-note)\s*$")
NOTE_CLOSE = re.compile(r"^ {0,3}:{3,}\s*$")
TABLE_START = re.compile(r"^ {0,3}(?:[|+]|-{3,}[\s-]*$)")
RULE = re.compile(r"^ {0,3}[-:|+ ]*-{3}[-:|+ ]*$")


def blocks(plain):
    """(start, end) offsets of blank-line separated blocks; a multiline table
    (dashed top and bottom lines) stays one block across its blank lines."""
    lines, at = [], 0
    for line in plain.splitlines(keepends=True):
        lines.append((at, at + len(line.rstrip("\n")), line.strip()))
        at += len(line)
    out, i = [], 0
    while i < len(lines):
        if not lines[i][2]:
            i += 1
            continue
        j, multiline = i, re.fullmatch(r"-{3,}", lines[i][2]) is not None
        while j + 1 < len(lines) and (lines[j + 1][2] or multiline):
            j += 1
            if multiline and j > i and re.fullmatch(r"-{3,}", lines[j][2]) and (
                    j + 1 >= len(lines) or not lines[j + 1][2]):
                break
        out.append((lines[i][0], lines[j][1]))
        i = j + 1
    return out


def is_table(plain, span):
    """A pipe, grid, simple or multiline table block (by its first lines)."""
    first = plain[span[0]:span[1]].splitlines()
    return bool(first) and (TABLE_START.match(first[0]) is not None
                            or (len(first) > 1 and RULE.match(first[1]) is not None))


def table_note(text, identifier):
    """The ::: table-note block that follows a captioned Markdown table.

    The caption may come before or after its table; the note follows both.
    Returns (note, end): the editable (start, end) span of the note's text,
    or None, and the offset after the table and caption, where a new note
    goes. Returns None when the table caption is not found once.
    """
    spans = list(table_captions(text, identifier))
    if len(spans) != 1:
        return None
    plain = markdown_context(text)
    found = blocks(plain)
    at = next((i for i, (start, end) in enumerate(found) if start <= spans[0][0] < end), None)
    if at is None:
        return None
    # The caption follows its table when the block before it is a table.
    group_end = at if at > 0 and is_table(plain, found[at - 1]) else at + 1
    if group_end >= len(found):
        return None
    end = found[group_end][1]
    following = found[group_end + 1] if group_end + 1 < len(found) else None
    if following:
        first = plain[following[0]:].split("\n", 1)[0]
        if NOTE_OPEN.match(first):
            # The note's paragraphs run to its closing fence, across blank lines.
            body_start = offset = following[0] + len(first) + 1
            for line in plain[body_start:].splitlines(keepends=True):
                if NOTE_CLOSE.match(line.rstrip("\n")):
                    return (body_start, max(body_start, offset - 1)), end
                offset += len(line)
    return None, end
