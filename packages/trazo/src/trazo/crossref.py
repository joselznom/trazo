"""trazo's own cross-references, in place of the pandoc-crossref filter.

`apply(doc)` numbers figures (`{#fig:id}`), tables (`{#tbl:id}`),
sections and display equations (`$$...$$ {#eq:id}`) in document order on a
Pandoc JSON AST, applies the caption templates and replaces `[@fig:id]`,
`[@tbl:id]`, `[@sec:id]` and `[@eq:id]` citations with their text, as
pandoc-crossref 0.3.24 does outside LaTeX: grouped and sorted references,
ranges of three or more ("Figures 1-3"), `-@` without the prefix,
capitalised `@Fig:`, singular/plural prefixes, per-document index templates
(numbering.py), `linkReferences`, a table in a `::: {#tbl:id}` block, and
subfigures (a `::: {#fig:id}` block of figures or images ending with its
caption paragraph). It must run before citeproc. A label used twice is an
error, as in pandoc-crossref. Listings (`lst:`) and the options in
`UNSUPPORTED_OPTIONS` are not supported: each one set warns once.
trazo.pandoc_run runs it between two Pandoc calls.
"""

from __future__ import annotations

import copy
import re
import sys
from collections.abc import Callable
from typing import Any

API_VERSION = (1, 23)  # pandoc-types major.minor this pass understands
KINDS = ("fig", "tbl", "sec", "eq")
UNSUPPORTED = ("lst",)  # pandoc-crossref labels the pass does not number
EQUATION_LABEL = re.compile(r"\{#(eq:[^}\s]+)\}")
TEMPLATE_VAR = re.compile(r"\$\$([A-Za-z]+)\$\$")


class CrossrefError(ValueError):
    """The AST comes from a Pandoc this pass does not support, or a label is
    used twice."""


def text(value: str) -> list[dict]:
    """Inlines of plain text, split at spaces like pandoc-types' `text`."""
    out = []
    for index, part in enumerate(re.split(r"([ \t\r\n]+)", value)):
        if part:  # odd parts are the spaces between words (not U+00A0)
            out = meld(out, [{"t": "Space"} if index % 2 else {"t": "Str", "c": part}])
    return out


def template(value: str) -> list[dict]:
    """Inlines of a template string, with `$$name$$` as Pandoc parses it."""
    out, last = [], 0
    for match in TEMPLATE_VAR.finditer(value):
        out = meld(out, text(value[last:match.start()]))
        out = meld(out, [{"t": "Math", "c": [{"t": "DisplayMath"}, match[1]]}])
        last = match.end()
    return meld(out, text(value[last:]))


# pandoc-crossref's defaults for the options the pass reads.
DEFAULTS = {
    "figureTitle": "Figure", "tableTitle": "Table", "titleDelim": ":",
    "figPrefix": ["fig.", "figs."], "tblPrefix": ["tbl.", "tbls."], "secPrefix": ["sec.", "secs."],
    "eqnPrefix": ["eq.", "eqns."], "eqnIndexTemplate": "($$i$$)", "equationNumberTeX": "\\qquad",
    "figureTemplate": "$$figureTitle$$ $$i$$$$titleDelim$$ $$t$$",
    "tableTemplate": "$$tableTitle$$ $$i$$$$titleDelim$$ $$t$$",
    "refIndexTemplate": "$$i$$$$suf$$",
    "figPrefixTemplate": "$$p$$\u00a0$$i$$", "tblPrefixTemplate": "$$p$$\u00a0$$i$$",
    "secPrefixTemplate": "$$p$$\u00a0$$i$$", "eqnPrefixTemplate": "$$p$$\u00a0$$i$$",
    "refDelim": ", ", "pairDelim": ", ", "lastDelim": ", ", "rangeDelim": "-",
    "subfigureTemplate": "$$figureTitle$$ $$i$$$$titleDelim$$ $$t$$. $$ccs$$",
    "subfigureChildTemplate": "$$i$$", "subfigureRefIndexTemplate": "$$i$$$$suf$$ ($$s$$)",
    "ccsTemplate": "$$i$$$$ccsLabelSep$$$$t$$", "ccsDelim": ", ", "ccsLabelSep": " \u2014 ",
}
# The pandoc-crossref options the pass reads (DEFAULTS above), and those it
# ignores with their pandoc-crossref default (None: any value is unsupported).
SUPPORTED_OPTIONS = (*DEFAULTS, "linkReferences")
UNSUPPORTED_OPTIONS = {
    "chapters": False, "chaptersDepth": "1", "chapDelim": ".", "numberSections": False,
    "sectionsDepth": "0", "secHeaderTemplate": None, "secHeaderDelim": None,
    "autoSectionLabels": False, "autoEqnLabels": False, "tableEqns": False, "cref": False,
    "nameInLink": False, "setLabelAttribute": False, "eqnBlockTemplate": None,
    "eqnBlockInlineMath": False, "eqnInlineTemplate": None, "eqnDisplayTemplate": None,
    "eqnInlineTableTemplate": None, "listings": False, "codeBlockCaptions": False,
    "subfigGrid": False, "figLabels": "arabic", "subfigLabels": "alpha a", "tblLabels": "arabic",
    "eqLabels": "arabic", "secLabels": "arabic",
}
PREFIX_KEYS = {"fig": "figPrefix", "tbl": "tblPrefix", "sec": "secPrefix", "eq": "eqnPrefix"}
TEMPLATE_KEYS = {"fig": "figPrefixTemplate", "tbl": "tblPrefixTemplate", "sec": "secPrefixTemplate",
                 "eq": "eqnPrefixTemplate"}

# Inline nodes whose content is a list of inlines, and where it is in "c".
CHILDREN = {"Emph": None, "Underline": None, "Strong": None, "Strikeout": None,
            "Superscript": None, "Subscript": None, "SmallCaps": None,
            "Quoted": 1, "Cite": 1, "Link": 1, "Image": 1, "Span": 1}
MERGED = ("Emph", "Strong", "Subscript", "Superscript", "Strikeout")


def meld(left: list[dict], right: list[dict]) -> list[dict]:
    """`left <> right` for pandoc-types' Inlines: the two nodes where they
    meet merge (Str+Str, Space+Space, Emph+Emph, ...)."""
    if not left or not right:
        return [*left, *right]
    a, b = left[-1], right[0]
    ta, tb = a["t"], b["t"]
    breaks = {"Space", "SoftBreak", "LineBreak"}
    if ta == tb == "Str":
        joined = [{"t": "Str", "c": a["c"] + b["c"]}]
    elif ta == tb and ta in MERGED:
        joined = [{"t": ta, "c": meld(a["c"], b["c"])}]
    elif ta in breaks and tb in breaks:
        joined = [{"t": "LineBreak" if "LineBreak" in (ta, tb) else
                   "SoftBreak" if "SoftBreak" in (ta, tb) else "Space"}]
    else:
        return [*left, *right]
    return [*left[:-1], *joined, *right[1:]]


def concat(*parts: list[dict]) -> list[dict]:
    out: list[dict] = []
    for part in parts:
        out = meld(out, part)
    return out


def join(items: list[list[dict]], delimiter: list[dict]) -> list[dict]:
    out: list[dict] = []
    for index, item in enumerate(items):
        out = concat(out, delimiter, item) if index else concat(out, item)
    return out


def inlines(value: Any) -> list[dict] | None:
    """A metadata value as inlines (None if it is not text)."""
    if isinstance(value, str):
        return template(value)
    if not isinstance(value, dict):
        return None
    kind, content = value.get("t"), value.get("c")
    if kind == "MetaInlines":
        return copy.deepcopy(content)
    if kind == "MetaString":
        return template(content)
    if kind == "MetaBlocks":
        found = [b["c"] for b in content if b["t"] in ("Plain", "Para")]
        return copy.deepcopy(found[0]) if found else []
    return None


class Options:
    def __init__(self, meta: dict):
        self.meta = meta

    def raw(self, key: str) -> Any:
        return self.meta.get(key, DEFAULTS.get(key))

    def inlines(self, key: str) -> list[dict]:
        found = inlines(self.raw(key))
        return found if found is not None else inlines(DEFAULTS.get(key, "")) or []

    def unsupported(self) -> list[str]:
        """Each option in UNSUPPORTED_OPTIONS set to other than its default."""
        found = []
        for key, default in UNSUPPORTED_OPTIONS.items():
            if key not in self.meta:
                continue
            if isinstance(default, bool):
                changed = self.flag(key) != default
            else:
                value = inlines(self.meta[key])
                changed = default is None or value is None or plain(value) != default
            if changed:
                found.append(key)
        return found

    def flag(self, key: str) -> bool:
        value = self.meta.get(key)
        if isinstance(value, dict) and value.get("t") == "MetaBool":
            return bool(value["c"])
        found = inlines(value)
        return found is not None and plain(found).strip().lower() in ("true", "yes", "on")

    def prefix(self, kind: str, count: int) -> list[dict]:
        """The singular (count 0) or plural (count 1+) reference prefix."""
        value = self.raw(PREFIX_KEYS[kind])
        if isinstance(value, dict) and value.get("t") == "MetaList":
            value = value["c"]
        if isinstance(value, list):
            if not value:
                return []
            found = inlines(value[min(count, len(value) - 1)])
            return found or []
        return inlines(value) or []

    def ref_index(self, kind: str) -> list[dict]:
        value = self.raw("refIndexTemplate")
        if isinstance(value, dict) and value.get("t") == "MetaMap":
            value = value["c"].get(kind, value["c"].get("default", DEFAULTS["refIndexTemplate"]))
        found = inlines(value)
        return found if found is not None else template(DEFAULTS["refIndexTemplate"])

    def fill(self, inlines_: list[dict], values: dict[str, list[dict]]) -> list[dict]:
        """A template with $$name$$ replaced by `values`, else by metadata."""
        out: list[dict] = []
        for node in inlines_:
            if node["t"] == "Math" and node["c"][0]["t"] == "DisplayMath":
                name = node["c"][1]
                if name in values:
                    part = copy.deepcopy(values[name])
                else:
                    part = self.fill(self.inlines(name), values) if name in self.meta or name in DEFAULTS else []
                out = meld(out, part)
                continue
            node = copy.copy(node)
            where = CHILDREN.get(node["t"], "missing")
            if where is None:
                node["c"] = self.fill(node["c"], values)
            elif where != "missing" and node["t"] != "Cite":
                node["c"] = [*node["c"][:where], self.fill(node["c"][where], values), *node["c"][where + 1:]]
            out = meld(out, [node])
        return out


def plain(nodes: Any) -> str:
    """The text of inlines, for messages and option values."""
    out = []
    for node in nodes if isinstance(nodes, list) else [nodes]:
        if not isinstance(node, dict):
            continue
        if node.get("t") == "Str":
            out.append(node["c"])
        elif node.get("t") in ("Space", "SoftBreak", "LineBreak"):
            out.append(" ")
        elif node.get("t") in CHILDREN:
            where = CHILDREN[node["t"]]
            out.append(plain(node["c"] if where is None else node["c"][where]))
    return "".join(out)


def kind_of(identifier: str) -> str | None:
    """fig/tbl/sec/eq (or lst) for a citation id (`Fig:x` too)."""
    return block_kind(identifier[:1].lower() + identifier[1:])


def block_kind(identifier: str) -> str | None:
    """fig/tbl/sec/eq (or lst) for a block's id; `Fig:x` is not a label."""
    head, colon, _ = identifier.partition(":")
    return head if colon and head in KINDS + UNSUPPORTED else None


def label_of(identifier: str) -> str:
    return identifier[:1].lower() + identifier[1:]


def check_version(doc: dict) -> None:
    version = doc.get("pandoc-api-version")
    if not isinstance(version, list) or tuple(version[:2]) != API_VERSION:
        shown = ".".join(map(str, version)) if isinstance(version, list) else "unknown"
        raise CrossrefError(
            f"trazo's cross-references support pandoc-api-version {API_VERSION[0]}.{API_VERSION[1]}.x "
            f"(Pandoc 3.x), not {shown}; use the Pandoc trazo setup installs")


def blocks_in(value: Any):
    """Every block in document order, nested ones after their parent."""
    if isinstance(value, list):
        for item in value:
            yield from blocks_in(item)
    elif isinstance(value, dict) and "t" in value:
        if value["t"] in BLOCK_TYPES:
            yield value
        yield from blocks_in(value.get("c"))


BLOCK_TYPES = {"Plain", "Para", "LineBlock", "CodeBlock", "RawBlock", "BlockQuote", "OrderedList",
               "BulletList", "DefinitionList", "Header", "HorizontalRule", "Table", "Figure", "Div"}


def caption_inlines(caption: list) -> list[dict]:
    found = [b["c"] for b in caption[1] if b["t"] in ("Plain", "Para")]
    return found[0] if found else []


def set_caption(caption: list, value: list[dict]) -> None:
    for block in caption[1]:
        if block["t"] in ("Plain", "Para"):
            block["c"] = value
            return
    caption[1].insert(0, {"t": "Plain", "c": value})


def legacy_table_label(node: dict) -> None:
    """A trailing `{#tbl:id}` Pandoc left in a table caption becomes its id."""
    caption = caption_inlines(node["c"][1])
    if node["c"][0][0] or not caption or caption[-1]["t"] != "Str":
        return
    match = re.fullmatch(r"\{#(tbl:[^}\s]+)\}", caption[-1]["c"])
    if match:
        del caption[-1]
        while caption and caption[-1]["t"] in ("Space", "SoftBreak"):
            del caption[-1]
        node["c"][0][0] = match[1]


def is_subfigure(block: dict) -> bool:
    """A `::: {#fig:id}` Div ending with a paragraph (its caption)."""
    return (block.get("t") == "Div" and block_kind(block["c"][0][0]) == "fig" and bool(block["c"][1])
            and block["c"][1][-1]["t"] == "Para")


def set_alt(figure: dict, value: list[dict]) -> None:
    """A figure's single image gets its caption as alt text."""
    body = figure["c"][2]
    if (len(body) == 1 and body[0]["t"] in ("Plain", "Para") and len(body[0]["c"]) == 1
            and body[0]["c"][0]["t"] == "Image"):
        body[0]["c"][0]["c"][1] = copy.deepcopy(value)


Label = tuple[str, tuple[int, ...], str | None]  # kind, number, subfigure letter


def number(doc: dict, options: Options) -> dict[str, Label]:
    """Number labelled figures, tables, sections and equations; apply caption
    templates. Raises CrossrefError for a figure, table or equation label
    used twice (a repeated section label takes the later heading)."""
    labels: dict[str, Label] = {}
    counts = {"fig": 0, "tbl": 0}
    sections: list[int] = []
    panels: set[int] = set()  # figures numbered as part of a subfigure block

    def store(label, kind, index, letter=None):
        if label in labels and kind != "sec":
            raise CrossrefError(f"Duplicate label: {label}")
        labels[label] = (kind, index, letter)

    def numbered(block, kind, label):
        """A Figure or Table numbered under `label`, with the caption template."""
        counts[kind] += 1
        store(label, kind, (counts[kind],))
        caption = block["c"][1]
        title = options.fill(options.inlines("figureTemplate" if kind == "fig" else "tableTemplate"),
                             {"i": text(str(counts[kind])), "t": caption_inlines(caption)})
        set_caption(caption, title)
        if kind == "fig":
            set_alt(block, title)

    def subfigure(block, number_):
        """A `::: {#fig:id}` Div of figures (or images in paragraphs) and a
        final caption paragraph becomes one figure with class "subfigures"
        and lettered panels, in document order."""
        identifier, classes, attributes = block["c"][0]
        *body, caption = block["c"][1]
        captions = []

        def panel(own, label):
            letter = chr(ord("a") + len(captions))
            captions.append((letter, own))
            if block_kind(label) == "fig":
                store(label, "fig", (number_, len(captions)), letter)
            return options.fill(options.inlines("subfigureChildTemplate"), {"i": text(letter)})

        def images(value):
            if isinstance(value, list):
                for item in value:
                    images(item)
            elif isinstance(value, dict) and "t" in value:
                if value["t"] == "Image":  # its alt text is the panel's caption
                    value["c"][1] = panel(value["c"][1], value["c"][0][0])
                else:
                    images(value.get("c"))

        for child in body:
            if child["t"] == "Figure":
                shown = panel(caption_inlines(child["c"][1]), child["c"][0][0])
                set_caption(child["c"][1], shown)
                set_alt(child, shown)
                panels.add(id(child))
            elif child["t"] in ("Plain", "Para") and not (
                    len(child["c"]) == 1 and child["c"][0]["t"] == "Image" and not child["c"][0]["c"][1]):
                images(child["c"])  # (a lone image without alt text is not a panel)
        # Panels without a caption get a letter but no entry in the list.
        ccs = join([options.fill(options.inlines("ccsTemplate"), {"i": text(letter), "t": own})
                    for letter, own in captions if own], options.inlines("ccsDelim"))
        title = options.fill(options.inlines("subfigureTemplate"),
                             {"i": text(str(number_)), "t": caption["c"], "ccs": ccs})
        store(identifier, "fig", (number_,))
        block["t"] = "Figure"
        block["c"] = [[identifier, ["subfigures", *classes], attributes],
                      [None, [{"t": "Para", "c": title}]], body]

    count = 0

    def equations(value, outside=True):
        """`$$...$$ {#eq:id}`: the label Str right after display math (or one
        space), in document order, before any caption template. In a figure
        (caption, alt text, panels) the equation only gets its id, no number
        and no label, as pandoc-crossref does."""
        nonlocal count
        if isinstance(value, dict):
            outside = outside and value.get("t") != "Figure" and not is_subfigure(value)
            for item in value.values():
                equations(item, outside)
            return
        if not isinstance(value, list):
            return
        index = 0
        while index < len(value):
            node = value[index]
            match = None
            if isinstance(node, dict) and node.get("t") == "Math" and node["c"][0]["t"] == "DisplayMath":
                after = index + 1
                if after < len(value) and value[after].get("t") in ("Space", "SoftBreak"):
                    after += 1
                label = value[after] if after < len(value) else None
                if isinstance(label, dict) and label.get("t") == "Str":
                    match = EQUATION_LABEL.fullmatch(label["c"])
            if not match:
                equations(node, outside)
                index += 1
                continue
            math = node
            if outside:  # not in a figure
                count += 1
                store(match[1], "eq", (count,))
                shown = plain(options.fill(options.inlines("eqnIndexTemplate"), {"i": text(str(count))}))
                tex = f"{node['c'][1]}{plain(options.inlines('equationNumberTeX'))}{{{shown}}}"
                math = {"t": "Math", "c": [{"t": "DisplayMath"}, tex]}
            value[index:after + 1] = [{"t": "Span", "c": [[match[1], [], []], [math]]}]
            index += 1

    equations(doc["blocks"])

    for block in blocks_in(doc["blocks"]):
        if block["t"] == "Header":
            level, (identifier, classes, _), _ = block["c"]
            if "unnumbered" in classes:
                continue
            sections = (sections + [0] * level)[:level]
            sections[-1] += 1
            if block_kind(identifier) == "sec":
                store(identifier, "sec", tuple(sections))
        elif block["t"] == "Div":
            identifier, body = block["c"][0][0], block["c"][1]
            kind = block_kind(identifier)
            if is_subfigure(block):
                counts["fig"] += 1
                subfigure(block, counts["fig"])
            elif kind == "tbl" and len(body) == 1 and body[0]["t"] == "Table":
                # The Div keeps its id; the table inside is numbered under it.
                numbered(body[0], "tbl", identifier)
        elif block["t"] in ("Figure", "Table"):
            if block["t"] == "Table":
                legacy_table_label(block)
            kind = "fig" if block["t"] == "Figure" else "tbl"
            identifier = block["c"][0][0]
            if block_kind(identifier) == kind and id(block) not in panels:
                numbered(block, kind, identifier)
    return labels


def clone(value: Any) -> Any:
    """A copy of JSON data (about twice as fast as copy.deepcopy)."""
    if isinstance(value, dict):
        return {key: clone(item) for key, item in value.items()}
    if isinstance(value, list):
        return [clone(item) for item in value]
    return value


def apply(doc: dict, *, link_references: bool = False,
          warn: Callable[[str], None] | None = None, in_place: bool = False) -> dict:
    """The document with cross-references resolved (a new dict, or `doc`
    itself, changed, with `in_place`).

    `warn` receives each message (default: standard error, as pandoc-crossref
    does). `linkReferences: true` in the metadata also turns on links.
    Raises CrossrefError for an unsupported AST or a duplicate label.
    """
    check_version(doc)
    warn = warn or (lambda message: print(message, file=sys.stderr))
    if not in_place:
        doc = clone(doc)
    options = Options(doc.get("meta", {}))
    for key in options.unsupported():
        warn(f"trazo's cross-references ignore the pandoc-crossref option {key}")
    labels = number(doc, options)
    link = link_references or options.flag("linkReferences")
    from .crossref_references import References  # it imports this module
    References(labels, options, link, warn).walk(doc["blocks"])
    return doc
