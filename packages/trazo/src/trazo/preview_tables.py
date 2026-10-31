"""Generated values in a table caption, for the preview's caption editor.

A spec's `title`, `legend` and `note` may quote values as `{name}` or
`{name:.2f}`; the page shows them as locked chips with the value from the
last table build. A literal brace is written `{{` or `}}`.
"""

from __future__ import annotations

import string

from .local_server import RequestError

CAPTION_KEYS = ("title", "legend")


def parse(template, where):
    """Split a template into literal text and placeholders (name, spec)."""
    try:
        parts = list(string.Formatter().parse(template))
    except ValueError as exc:
        raise RequestError(f"{where}: {exc} (write a literal brace as {{{{ or }}}})") from exc
    out = []
    for literal, name, spec, conversion in parts:
        if literal:
            out.append(("text", literal))
        if name is not None:
            if not name or conversion:
                raise RequestError(f"{where}: unsupported placeholder {{{name}}}")
            out.append(("value", name, spec or ""))
    return out


def segments(template, values, where):
    """Template parts for the page: text, or a chip with its shown value."""
    result = []
    for part in parse(template, where):
        if part[0] == "text":
            result.append({"text": part[1]})
            continue
        _, name, spec = part
        token = "{" + name + (":" + spec if spec else "") + "}"
        if name in values:
            try:
                shown = format(values[name], spec)
            except (TypeError, ValueError):
                shown = str(values[name])
            result.append({"value": token, "name": name, "shown": shown})
        else:  # no value yet (the data is not built): shown by name
            result.append({"value": token, "name": name, "shown": name, "row": True})
    return result
