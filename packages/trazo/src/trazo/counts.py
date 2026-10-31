"""Count main-text words, figures, tables and references, as journals do.

    trazo counts [--check] [DOCUMENT ...]

Builds each document's preview page (in a temporary folder, apart from a
running `trazo preview serve`) and counts it. The preview shows the same counts:
the server counts each page it sends (`page_counts`), so this module is the
only implementation of the rules:

- the title block, headings, figures, tables, table notes and the
  references are never words; in-text citations are, as in Word;
- a section is outside the main text, with its sub-sections, when its
  heading has `.uncounted`, `.form-only`, a `decl-` id or a [counts] exclude phrase
  (`excluded_heading`);
- a footnote counts with the section that cites it;
- the abstract is the section headed "Abstract", else the `abstract:` of
  the metadata (None without either).

--check exits 1 when the first document is over a [limits] value, or has an
abstract limit but no abstract.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path

from .project import PROJECT, ConfigError, Project

# Subtrees that hold no main-text words.
SKIP_TAGS = {"head", "figure", "table", "h1", "h2", "h3", "h4", "h5", "h6", "button", "script", "style", "annotation"}
SKIP_CLASSES = {"table-note", "footnote-back", "abstract-title", "pk-front", "pk-keywords", "pk-form-badge"}
# Tags whose start or end separates words.
BLOCKS = {"p", "li", "pre", "blockquote", "dt", "dd", "div", "section", "br", "ul", "ol", "dl",
          "h1", "h2", "h3", "h4", "h5", "h6", "table", "figure", "header", "hr"}
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
VOID = {"br", "hr", "img", "input", "meta", "link", "col", "wbr", "source", "area", "base", "embed"}
NUMBER = re.compile(r"^\d+(?:\.\d+)*\.?\s+")
# What may follow a phrase: "statement(s)", a label ("Appendix A",
# "Appendix 2") and a subtitle after a colon or dash. Nothing else, so
# "Funding models" or "Abstract syntax trees" stay main text.
TAIL = r"(?: statements?)?(?: (?:[a-z]|\d+|[ivxlc]+))?(?:\s*[:.]|\s*[:.–—-]\s.*)?"
ROWS = (("words", "Main text", "words"), ("abstract", "Abstract", "words"),
        ("figures", "Figures", ""), ("tables", "Tables", ""), ("references", "References", ""))


def excluded_heading(text, phrases):
    """Whether a heading names a section outside the main text: in lowercase
    and without its section number, it is a phrase followed at most by TAIL."""
    text = NUMBER.sub("", " ".join(text.split()).lower())
    return any(re.fullmatch(re.escape(" ".join(p.split()).lower()) + TAIL, text)
               for p in phrases if p.strip())


class Counter(HTMLParser):
    """Words per heading of a built preview page, with figures and references."""

    def __init__(self, total=False):
        super().__init__(convert_charrefs=True)
        self.total = total
        self.stack = []  # open elements: (tag, id, classes, skips its words)
        self.heads = []  # {"level", "text", "words", "uncounted"}: h1..h6 outside the title block
        self.intro = 0  # words before the first heading
        self.yaml_abstract = None  # words of the title block's abstract (metadata)
        self.notes = {}  # footnote id -> words
        self.cited_at = {}  # footnote id -> index of the heading that cites it (-1: none yet)
        self.in_word = False
        self.figures = self.tables = self.references = 0

    def inside(self, test):
        return any(test(*entry) for entry in self.stack)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set((attrs.get("class") or "").split())
        ident = attrs.get("id") or ""
        if tag in BLOCKS:
            self.in_word = False
        in_header = self.inside(lambda t, i, c, s: t == "header")
        in_refs = self.inside(lambda t, i, c, s: i == "refs")
        # Panels of a composite figure are part of it, not figures of their own.
        if not (in_header or in_refs or self.inside(lambda t, i, c, s: t == "figure" and i.startswith("fig:"))):
            self.figures += tag == "figure" and ident.startswith("fig:")
            self.tables += tag == "table" and ident.startswith("tbl:")
        self.references += in_refs and "csl-entry" in classes
        if tag == "a" and "footnote-ref" in classes:
            self.cited_at.setdefault((attrs.get("href") or "").lstrip("#"), len(self.heads) - 1)
        if tag in HEADINGS and not in_header:
            self.heads.append({"level": int(tag[1]), "text": "", "words": 0,
                               "uncounted": bool({"uncounted", "form-only"} & classes)
                               or ident == "abbreviations" or ident.startswith("decl-") or self.inside(
                                   lambda tag, ident, classes, skip: ident == "abbreviations" or ident.startswith("decl-")
                                   or "form-only" in classes)})
        if tag == "div" and "abstract" in classes and in_header:
            self.yaml_abstract = 0
        if tag not in VOID:
            skip = tag in SKIP_TAGS or bool(classes & SKIP_CLASSES) or ident == "refs"
            if self.total:
                skip = tag in {"head", "button", "script", "style", "annotation"} or "pk-form-badge" in classes
            self.stack.append((tag, ident, classes, skip))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in BLOCKS:
            self.in_word = False
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        in_header = self.inside(lambda t, i, c, s: t == "header")
        if self.heads and not in_header and self.inside(lambda t, i, c, s: t in HEADINGS):
            self.heads[-1]["text"] += data
        if not data or self.inside(lambda t, i, c, s: s):
            return
        in_abstract = in_header and self.inside(lambda t, i, c, s: t == "div" and "abstract" in c)
        if in_header and not in_abstract and not self.total:
            return
        # Inline markup can split a word (H<sub>2</sub>O): only whitespace or
        # a block boundary starts a new one.
        words = len(data.split())
        if self.in_word and not data[0].isspace():
            words -= 1
        self.in_word = not data[-1].isspace()
        if self.total:
            self.intro += words
            return
        note = next((i for t, i, c, s in reversed(self.stack) if t == "li" and i.startswith("fn")), None)
        if in_abstract:
            self.yaml_abstract += words
        elif note and self.inside(lambda t, i, c, s: "footnotes" in c):
            self.notes[note] = self.notes.get(note, 0) + words
        elif self.heads:
            self.heads[-1]["words"] += words
        else:
            self.intro += words


def count_page(html, exclude):
    """Counts of one built preview page: words (main text), abstract (None
    without one), figures, tables, references, left_out (the sections
    outside the main text, as (heading, words)) and heads (every h1..h6 in
    order, with its level, text, own words and whether it is main text)."""
    counter = Counter()
    counter.feed(html)
    counter.close()
    heads = counter.heads
    for note, words in counter.notes.items():  # a footnote counts where it is cited
        at = counter.cited_at.get(note, -1)
        if at >= 0:
            heads[at]["words"] += words
        else:
            counter.intro += words
    open_heads, left_out, totals = [], [], []  # totals: words with the sub-headings
    for i, head in enumerate(heads):
        head["text"] = " ".join(head["text"].split())
        while open_heads and open_heads[-1]["level"] >= head["level"]:
            open_heads.pop()
        parent_counted = all(x["counted"] for x in open_heads)
        head["counted"] = (parent_counted and not head.pop("uncounted")
                           and not excluded_heading(head["text"], exclude))
        end = next((j for j in range(i + 1, len(heads)) if heads[j]["level"] <= head["level"]), len(heads))
        totals.append(sum(x["words"] for x in heads[i:end]))
        if parent_counted and not head["counted"]:
            left_out.append((head["text"], totals[i]))
        open_heads.append(head)
    abstract = next((totals[i] for i, h in enumerate(heads) if excluded_heading(h["text"], ["abstract"])),
                    counter.yaml_abstract)
    return {"words": counter.intro + sum(h["words"] for h in heads if h["counted"]),
            "abstract": abstract, "figures": counter.figures, "tables": counter.tables,
            "references": counter.references, "left_out": left_out,
            "heads": [{k: h[k] for k in ("level", "text", "words", "counted")} for h in heads]}


def current_config():
    """trazo.toml as it is now, so [counts] and [limits] edits show
    without restarting `trazo preview serve`; the loaded settings while it is invalid."""
    try:
        return Project.load(PROJECT.root).config
    except ConfigError:
        return PROJECT.config


def page_counts(name, html):
    """The counts of a document page, with the [limits] that apply to it
    (the first document's only, without the zeros). Preview-only sections
    keep their outline entries; totals use the Word content before citeproc."""
    config = current_config()
    found = count_page(html, config["counts"]["exclude"])
    if name in PROJECT.auto_documents:
        counter = Counter(total=True)
        counter.feed(html)
        counter.close()
        found.update(words=counter.intro, total=True, left_out=[])
    if re.search(r'class="[^"]*\bpk-form-only\b', html):
        snapshot = re.search(r'<script type="application/json" id="pk-word-counts">(.*?)</script>', html, re.S)
        if snapshot:
            found.update(json.loads(snapshot[1]))
    first = next(iter(PROJECT.documents))
    found["limits"] = {k: v for k, v in config["limits"].items() if v} if name == first else {}
    return found


def document_counts(name):
    """Build a document's preview page in a temporary folder and count it;
    raises RuntimeError if the build fails."""
    from . import preview
    with tempfile.TemporaryDirectory(prefix="trazo-counts-") as folder:
        error, _ = preview.render(name, Path(folder), for_counts=True)
        if error:
            raise RuntimeError(error)
        return page_counts(name, (Path(folder) / f"{name}.html").read_text(encoding="utf-8"))


def problems(counts):
    """What is wrong with a document's counts against its limits."""
    found = []
    for key, label, _ in ROWS:
        limit = counts["limits"].get(key, 0)
        if limit and counts[key] is None:
            found.append(f"{label.lower()} not found (no Abstract heading or abstract: metadata)")
        elif limit and counts[key] > limit:
            found.append(f"{label.lower()} over the limit ({counts[key]:,} / {limit:,})")
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(prog="trazo counts", description=__doc__.splitlines()[0])
    parser.add_argument("documents", nargs="*", metavar="DOCUMENT",
                        help=f"documents to count (default: all; {', '.join(PROJECT.documents)})")
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if the first document is over a limit, or has no abstract to limit")
    args = parser.parse_args(argv)
    names = args.documents or list(PROJECT.documents)
    unknown = [name for name in names if name not in PROJECT.documents]
    if unknown:
        parser.error(f"unknown document {unknown[0]!r} (documents: {', '.join(PROJECT.documents)})")
    failed = []
    for name in names:
        try:
            counts = document_counts(name)
        except (RuntimeError, OSError) as exc:
            print(f"trazo counts: {name}: {exc}", file=sys.stderr)
            return 1
        limits = counts["limits"]
        print(PROJECT.titles[name])
        rows = (("words", "Total", "words"),) if counts.get("total") else ROWS
        for key, label, unit in rows:
            value, limit = counts[key], limits.get(key, 0)
            if value is None and not limit:
                continue
            if value is None:
                text, unit, flag = "not found", "", ""
            else:
                text = f"{value:,}" + (f" / {limit:,}" if limit else "")
                flag = "  over the limit" if limit and value > limit else ""
            print(f"  {label:<11} {text:>13}{' ' + unit if unit else ''}{flag}")
        if counts["left_out"]:
            print("  Not in the main text: "
                  + ", ".join(f"{text} ({words:,})" for text, words in counts["left_out"]))
        failed += [f"{PROJECT.titles[name]}: {problem}" for problem in problems(counts)]
    if args.check and failed:
        sys.stdout.flush()
        print("\n" + "\n".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
