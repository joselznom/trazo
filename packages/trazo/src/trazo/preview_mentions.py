"""What the @ menu of the preview's text offers, and the Markdown of what it inserts.

GET /__references lists the bibliography's entries. A save (trazo.preview_block_save)
takes {mentions: {character: {kind, key}}}: each private-use character of `after`
from U+E800 is a new chip, and becomes the Markdown of its kind. A key must
exist: the menu cites what the paper already has, and adds nothing to it.
"""

from __future__ import annotations

import re
from typing import Any

from . import labels
from .citations import BIB_RE, BIBLIOGRAPHY, bibliography_keys
from .local_server import RequestError

NEW_CHIP_FIRST, NEW_CHIP_LAST = 0xE800, 0xEFFF
FIELD = r"(?im)^\s*{}\s*=\s*[{{\"]+(.+?)[}}\"]+\s*,?\s*$"


def _field(entry: str, name: str) -> str:
    found = re.search(FIELD.format(name), entry)
    return found[1].strip() if found else ""


def references() -> dict[str, list[dict[str, str]]]:
    """Each entry of the bibliography: its key and what shows in the menu."""
    if not BIBLIOGRAPHY.exists():
        return {"references": []}
    text = BIBLIOGRAPHY.read_text(encoding="utf-8")
    starts = list(BIB_RE.finditer(text))
    out = []
    for this, following in zip(starts, [*starts[1:], None]):
        entry = text[this.end():following.start() if following else len(text)]
        out.append({"key": this[1], "text": " ".join(filter(None, (_field(entry, "title"), _field(entry, "year"))))})
    return {"references": out}


def _source(mention: Any) -> str:
    """The Markdown of a mention, or RequestError if what it names does not exist."""
    from . import preview
    kind, key = (mention.get("kind"), mention.get("key")) if isinstance(mention, dict) else (None, None)
    if not isinstance(key, str):
        raise RequestError("a mention needs a kind and a key")
    if kind == "reference" and key in bibliography_keys():
        return f"[@{key}]"
    if kind == "object" and any(key in entry["labels"] for entry in labels.index(preview.PROJECT).values()):
        return f"[@{key}]"
    if kind == "value" and key in preview.values_info():
        return f"{{{{v:{key}}}}}"
    raise RequestError(f"nothing to mention: {key}")


def sources(request: dict[str, Any]) -> dict[str, str]:
    """Each new chip's character and its Markdown."""
    mentions = request.get("mentions", {})
    if not isinstance(mentions, dict):
        raise RequestError("mentions must be an object")
    for ch in mentions:
        if len(ch) != 1 or not NEW_CHIP_FIRST <= ord(ch) <= NEW_CHIP_LAST:
            raise RequestError("a mention's character must be from U+E800 to U+EFFF")
    return {ch: _source(mention) for ch, mention in mentions.items()}
