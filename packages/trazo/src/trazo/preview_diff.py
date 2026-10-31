"""Readable diff of hard-wrapped Markdown prose for the live preview.

Both versions are split into paragraphs and aligned with difflib; changed
pairs get a word-level diff. Only plain prose is reflowed. Indentation,
hard breaks and structured Markdown retain their whitespace.
"""

import re
from difflib import SequenceMatcher

FENCE = re.compile(r"^\s*(```|~~~)")
# Be conservative: these lines may make whitespace or line boundaries
# significant (code, lists, tables, headings, HTML, links, math, etc.).
STRUCTURED = re.compile(r"^(?:\s|[#<>*|+`~:=\[{!$\\-]|\d+[.)]\s)|(?: {2,}|\\)$")
WORDS = re.compile(r"\s+|\S+")
CONTEXT = 1       # unchanged paragraphs kept around a change
PAIR_RATIO = 0.5  # minimum word similarity to show two paragraphs as edited
SNIPPET = 200


def paragraphs(text):
    # Raw HTML, YAML and display math may span blank-separated blocks. Avoid
    # reflow throughout such a document rather than guess their boundaries.
    reflow = not re.search(r"(?m)^\s*(?:<|---\s*$|\$\$)", text)
    blocks, current, fence = [], [], None
    for line in text.replace("\r\n", "\n").split("\n"):
        marker = FENCE.match(line)
        if fence is not None:
            current.append(line)
            if marker and marker.group(1) == fence:
                fence = None
            continue
        if marker:
            fence = marker.group(1)
            current.append(line)
        elif line.strip():
            current.append(line)
        else:
            if current:
                blocks.append(current)
                current = []
            # Blank lines can be content inside indented code or raw blocks.
            blocks.append([line])
    if current:
        blocks.append(current)
    return ["\n".join(b) if not reflow or any(STRUCTURED.search(line) for line in b)
            else " ".join(b) for b in blocks]


def word_ops(old, new):
    a, b = WORDS.findall(old), WORDS.findall(new)
    ops = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            ops.append(["=", "".join(a[i1:i2])])
        else:
            if i2 > i1:
                ops.append(["-", "".join(a[i1:i2])])
            if j2 > j1:
                ops.append(["+", "".join(b[j1:j2])])
    return ops


def similar(old, new):
    return SequenceMatcher(None, old.split(), new.split(), autojunk=False).ratio()


def pair_up(olds, news):
    """Align a replaced run: edits where paragraphs are alike, else del/add."""
    items, j = [], 0
    for old in olds:
        best, best_j = 0.0, None
        for k in range(j, len(news)):
            ratio = similar(old, news[k])
            if ratio > best:
                best, best_j = ratio, k
        if best_j is not None and best >= PAIR_RATIO:
            items += [{"t": "add", "text": n} for n in news[j:best_j]]
            items.append({"t": "chg", "words": word_ops(old, news[best_j])})
            j = best_j + 1
        else:
            items.append({"t": "del", "text": old})
    items += [{"t": "add", "text": n} for n in news[j:]]
    return items


def diff(old, new):
    """Hunks (with context) between two texts and add/remove paragraph counts."""
    a, b = paragraphs(old), paragraphs(new)
    items = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            items += [{"t": "eq", "text": p[:SNIPPET]} for p in a[i1:i2]]
        else:
            items += pair_up(a[i1:i2], b[j1:j2])
    # Blank separators take part in the alignment only: as context they hide
    # the neighbouring paragraph, and in gap counts they are not paragraphs.
    items = [it for it in items if it["t"] == "chg" or it["text"].strip()]
    changed = [i for i, it in enumerate(items) if it["t"] != "eq"]
    keep = set()
    for i in changed:
        keep.update(range(max(0, i - CONTEXT), min(len(items), i + CONTEXT + 1)))
    out, skipped = [], 0
    for i, it in enumerate(items):
        if i in keep:
            if skipped:
                out.append({"t": "gap", "n": skipped})
                skipped = 0
            out.append(it)
        else:
            skipped += 1
    if skipped and out:
        out.append({"t": "gap", "n": skipped})
    return {"hunks": out, "changed": len(changed)}
