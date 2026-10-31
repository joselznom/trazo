"""Citations of cross-references: `[@fig:id]` and the rest replaced with
their text (trazo.crossref numbers the labels first). A figure, table or
section label the citing document lacks resolves in the project's other
documents, which numbering.metadata lists."""

from __future__ import annotations

import copy
import json
from typing import Any

from .crossref import TEMPLATE_KEYS, UNSUPPORTED, Options, concat, inlines, join, kind_of, label_of, meld, plain, text

DOCUMENT, DOCUMENTS = "trazo-document", "trazo-documents"  # numbering.metadata's keys
WORDS = {"fig": "figures", "tbl": "tables", "sec": "sections"}


class Documents:
    """The project's documents as numbering.metadata lists them: each one's
    labels ("fig 2 a": kind, number, panel letter) and reference templates.
    Reading them removes them from the metadata, so no writer sees them."""

    def __init__(self, options: Options):
        meta = options.meta
        self.current = plain(inlines(meta.pop(DOCUMENT, "")) or [])
        entries = meta.pop(DOCUMENTS, {}).get("c") or []
        self.order: list[str] = []
        self.titles: dict[str, str] = {}
        self.labels: dict[str, dict[str, tuple]] = {}
        self.options: dict[str, Options] = {}
        for entry in entries:
            fields = dict(entry["c"])
            name = plain(inlines(fields.pop("name")) or [])
            self.order.append(name)
            self.titles[name] = plain(inlines(fields.pop("title", "")) or []) or name
            labels = fields.pop("labels", {}).get("c") or {}
            self.labels[name] = {label: parse(plain(inlines(value) or [])) for label, value in labels.items()}
            self.options[name] = Options({**meta, **fields})

    def holders(self, label: str, kind: str) -> list[str]:
        """The other documents with `label` as an item of `kind`."""
        return [name for name in self.order
                if name != self.current and self.labels[name].get(label, ("",))[0] == kind]

    def title(self, name: str) -> str:
        return self.titles.get(name or self.current, name or "this document")


def problem(label: str, kind: str, holders: list[str]) -> str | None:
    """Why a label the citing document lacks cannot be cited, given the
    titles of the other documents that have it (None: it can)."""
    names = " and ".join(holders)
    if not holders:
        return f"Undefined cross-reference: {label}"
    if len(holders) > 1:
        return f"Ambiguous cross-reference: {label} is in {names}"
    if kind not in WORDS:
        return f"Cross-document references cover figures, tables and sections: {label} is in {names}"
    return None


def parse(value: str) -> tuple[str, tuple[int, ...], str | None]:
    kind, number, *letter = value.split()
    return kind, tuple(int(part) for part in number.split(".")), (letter[0] if letter else None)


class References:
    def __init__(self, labels, options: Options, link: bool, warn):
        self.labels, self.options, self.link, self.warn = labels, options, link, warn
        self.documents = Documents(options)
        self.alike: set[tuple[str, str]] = set()

    def options_of(self, document: str) -> Options:
        return self.documents.options[document] if document else self.options

    def index(self, kind: str, label: str, idx: tuple[int, ...], suffix: list[dict],
              document: str = "") -> list[dict]:
        options = self.options_of(document)
        letter = (self.documents.labels[document] if document else self.labels)[label][2]
        if letter:  # a subfigure: "2 (a)", without the document's index template
            shown = options.fill(options.inlines("subfigureRefIndexTemplate"),
                                 {"i": text(str(idx[0])), "suf": suffix, "s": text(letter)})
        else:
            shown = options.fill(options.ref_index(kind), {"i": text(".".join(map(str, idx))), "suf": suffix})
        if self.link:
            return [{"t": "Link", "c": [["", [], []], shown, [f"{document}.html#{label}" if document
                                                              else "#" + label, ""]]}]
        return shown

    def indices(self, kind: str, refs: list[tuple]) -> list[dict]:
        """pandoc-crossref's makeIndices: sort, drop repeats, collapse runs.
        Refs are (number, label, suffix, document); the citing document's
        come first, then the others' in the project's order."""
        place = self.place()
        # Stable sort by number (missing labels first, in citation order).
        unique = sorted(dict.fromkeys(refs), key=lambda r: (r[0] is not None, place[r[3]], r[0] or ()))
        runs: list[list[tuple]] = []
        for ref in unique:
            last = runs[-1][-1] if runs else None
            if (last is not None and last[2] == ref[2] == "[]" and last[0] is not None
                    and ref[0] is not None and last[3] == ref[3] and not self.letter(last)
                    and not self.letter(ref) and last[0][:-1] == ref[0][:-1]
                    and last[0][-1] + 1 == ref[0][-1]):
                runs[-1].append(ref)
            else:
                runs.append([ref])

        def show(ref):
            idx, label, suffix, document = ref
            suffix = json.loads(suffix)
            if idx is None:
                return concat([{"t": "Strong", "c": text(f"¿{label}?")}], suffix)
            return self.index(kind, label, idx, suffix, document)

        items: list[list[dict]] = []
        for run in runs:
            if len(run) > 2:
                items.append(concat(show(run[0]), self.options.inlines("rangeDelim"), show(run[-1])))
            else:
                items += [show(ref) for ref in run]
        if len(items) <= 1:
            return items[0] if items else []
        if len(items) == 2:
            return concat(items[0], self.options.inlines("pairDelim"), items[1])
        return concat(join(items[:-1], self.options.inlines("refDelim")),
                      self.options.inlines("lastDelim"), items[-1])

    def place(self) -> dict[str, int]:
        """Each document's position in [documents] order; "" is the citing one."""
        order = self.documents.order
        return {name: index for index, name in enumerate(order)} | {
            "": order.index(self.documents.current) if self.documents.current in order else -1}

    def letter(self, ref: tuple) -> str | None:
        return (self.documents.labels[ref[3]] if ref[3] else self.labels)[ref[1]][2]

    def find(self, label: str, kind: str) -> tuple[str, tuple]:
        """The document ("" for the citing one) and entry of a label; a
        missing, ambiguous or unsupported one warns and has no number."""
        found = self.labels.get(label)
        if found is not None and found[0] == kind:
            return "", found
        holders = self.documents.holders(label, kind)
        message = problem(label, kind, [self.documents.title(name) for name in holders])
        if message:
            self.warn(message)
            return "", (kind, None, None)
        self.check_alike(holders[0], kind)
        return holders[0], self.documents.labels[holders[0]][label]

    def wording(self, document: str, kind: str) -> str:
        """The word before the numbers ("Supplementary $$p$$ $$i$$")."""
        return json.dumps(self.options_of(document).inlines(TEMPLATE_KEYS[kind]))

    def check_alike(self, document: str, kind: str) -> None:
        """Warn once when another document's items read like the citing one's."""
        if (document, kind) in self.alike or self.wording(document, kind) != self.wording("", kind) \
                or self.options_of(document).ref_index(kind) != self.options.ref_index(kind):
            return
        self.alike.add((document, kind))
        self.warn(f"{self.documents.title(document)}'s {WORDS[kind]} read like {self.documents.title('')}'s: "
                  "give one of them a number_prefix or title_prefix in trazo.toml")

    def group(self, citations: list[dict]) -> list[dict]:
        """One run of citations of the same kind, mode and prefix; items
        whose documents word them differently get their own word."""
        first = citations[0]
        kind = kind_of(first["citationId"]) or ""  # citations() groups only crossref kinds
        parts: dict[str, list[tuple]] = {}
        for citation in citations:
            label = label_of(citation["citationId"])
            document, found = self.find(label, kind)
            ref = (found[1], label, json.dumps(citation["citationSuffix"]), document)
            parts.setdefault(self.wording(document, kind), []).append(ref)
        place = self.place()
        groups = sorted(parts.values(), key=lambda refs: min(place[ref[3]] for ref in refs))
        shown = [self.indices(kind, refs) for refs in groups]
        if all(c["citationMode"]["t"] == "SuppressAuthor" for c in citations):
            return join(shown, text(", "))
        if first["citationPrefix"]:
            return concat(copy.deepcopy(first["citationPrefix"]), [{"t": "Space"}], join(shown, text(", ")))
        out = []
        for position, (refs, part) in enumerate(zip(groups, shown)):
            options = self.options_of(refs[0][3])
            prefix = options.prefix(kind, len(refs) - 1)
            if not prefix:
                out.append(part)
                continue
            if position == 0 and first["citationId"][:1].isupper():
                prefix = capitalise(prefix)
            out.append(options.fill(options.inlines(TEMPLATE_KEYS[kind]), {"p": prefix, "i": part}))
        return join(out, text(", "))

    def cite(self, node: dict) -> list[dict]:
        """A Cite's replacement: crossref runs as text, the rest as Cites.
        Its warnings come out last citation first, as in pandoc-crossref."""
        found: list[str] = []
        warn, self.warn = self.warn, found.append
        try:
            return self.citations(node)
        finally:
            self.warn = warn
            for message in reversed(found):
                warn(message)

    def citations(self, node: dict) -> list[dict]:
        groups: list[tuple[Any, list[dict]]] = []
        for citation in node["c"][0]:
            kind = kind_of(citation["citationId"])
            if kind in UNSUPPORTED:
                self.warn(f"trazo's cross-references do not support {citation['citationId']}")
                kind = None
            key = (kind, citation["citationMode"]["t"], json.dumps(citation["citationPrefix"])) if kind else None
            if groups and groups[-1][0] == key:
                groups[-1][1].append(citation)
            else:
                groups.append((key, [citation]))
        parts = []
        for key, citations in groups:
            if key is None:
                content = concat(text("["), join(
                    [concat(c["citationPrefix"], text("@" + c["citationId"]), c["citationSuffix"])
                     for c in citations], text("; ")), text("]"))
                parts.append([{"t": "Cite", "c": [citations, copy.deepcopy(content)]}])
            else:
                parts.append(self.group(citations))
        return join(parts, text(", "))

    def walk(self, value: Any) -> Any:
        """`value` with crossref Cites replaced in document order, changed in
        place; each replacement melds with what follows it."""
        if isinstance(value, list):
            if value and all(isinstance(item, dict) and item.get("t") in INLINE_TYPES for item in value) \
                    and any(item["t"] == "Cite" for item in value):
                parts = [(item["t"] == "Cite", self.cite(item) if item["t"] == "Cite" else self.walk(item))
                         for item in value]
                rest: list[dict] = []
                for is_cite, part in reversed(parts):  # pandoc-crossref rebuilds every Cite's text
                    rest = meld(part, rest) if is_cite else [part, *rest]
                value[:] = rest
            else:
                for index, item in enumerate(value):
                    value[index] = self.walk(item)
        elif isinstance(value, dict):
            for key, item in value.items():
                value[key] = self.walk(item)
        return value


INLINE_TYPES = {"Str", "Emph", "Underline", "Strong", "Strikeout", "Superscript", "Subscript",
                "SmallCaps", "Quoted", "Cite", "Code", "Space", "SoftBreak", "LineBreak", "Math",
                "RawInline", "Link", "Image", "Note", "Span"}


def capitalise(nodes: list[dict]) -> list[dict]:
    nodes = copy.deepcopy(nodes)
    for node in nodes:
        if node["t"] == "Str" and node["c"]:
            node["c"] = node["c"][:1].upper() + node["c"][1:]
            break
    return nodes
