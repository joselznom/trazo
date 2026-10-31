"""The stress paper (stress_fixture.py), small, built and previewed for real.

What a reader sees must match what the generator wrote: the number of every
figure, panel, table and equation in its caption and in each reference to
it, the counts, the checks, and the text that review comments quote.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)
import stress_fixture

from trazo import diagram, preview_locate

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "src"
SCALE = 0.03
NUMBER = re.compile(r"^(?:Figure|Table)\s+(\S+?)[.:]")
REFERENCE = re.compile(r"@((?:fig|tbl|eq):[\w-]+)")
CODE = re.compile(r"^```.*?^```|`[^`\n]*`", re.S | re.M)  # references in code stay literal
FOLDERS = {"manuscript": ("main", "shared"), "supplementary": ("supplementary",)}


class Page(HTMLParser):
    """Caption numbers, equation numbers, references, paragraphs and text of a preview page."""

    def __init__(self, path: Path):
        super().__init__()
        self.captions, self.equations, self.refs, self.paragraphs = {}, {}, [], []
        self.figures, self.equation, self.text = [], None, []
        self.caption, self.link, self.paragraph = None, None, None  # (label, parts) being read
        self.feed(path.read_text(encoding="utf-8"))
        self.text = re.sub(r"\s+", " ", "".join(self.text))

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "figure" or (tag == "table" and attrs.get("id")):
            self.figures.append(attrs.get("id"))
        elif tag in ("figcaption", "caption") and self.figures:
            self.caption = (self.figures[-1], [])
        elif tag == "a" and re.match(r"(\w+\.html)?#(fig|tbl|eq):", attrs.get("href") or ""):
            self.link = (attrs["href"].partition("#")[2], [])
        elif tag == "span" and (attrs.get("id") or "").startswith("eq:"):
            self.equation = attrs["id"]
        elif tag == "p" and not self.caption:
            self.paragraph = (None, [])

    def handle_endtag(self, tag):
        def text(parts):
            return re.sub(r"\s+", " ", "".join(parts)).strip()
        if tag in ("figcaption", "caption") and self.caption:
            match = NUMBER.match(text(self.caption[1]))
            self.captions[self.caption[0]] = match[1] if match else None
            self.caption = None
        elif tag in ("figure", "table") and self.figures:
            self.figures.pop()
        elif tag == "a" and self.link:
            self.refs.append((self.link[0], text(self.link[1])))
            self.link = None
        elif tag == "p" and self.paragraph:
            self.paragraphs.append(text(self.paragraph[1]))
            self.paragraph = None

    def handle_data(self, data):
        for reading in (self.caption, self.link, self.paragraph):
            if reading:
                reading[1].append(data)
        self.text.append(data)
        found = re.search(r"\\qquad\{\((\S+)\)\}", data)
        if self.equation and found:
            self.equations[self.equation] = found[1]
            self.equation = None


def run(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "TRAZO_ROOT": str(root), "PYTHONPATH": str(PACKAGE_DIR)}
    return subprocess.run([sys.executable, "-m", "trazo", *args], cwd=root, env=env,
                          capture_output=True, text=True, timeout=300)


@unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
class StressPaper(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = tempfile.TemporaryDirectory(prefix="trazo-stress-")
        cls.addClassCleanup(folder.cleanup)
        cls.root = Path(folder.name) / "paper"
        chrome = bool(diagram.CHROME and Path(diagram.CHROME).exists())  # diagram exports need it
        cls.expected = stress_fixture.create(cls.root, SCALE, image=(40, 30), diagrams=chrome)
        for args in (["build", "--submission"], ["preview", "build"]):
            result = run(cls.root, *args)
            if result.returncode:
                raise AssertionError(f"trazo {' '.join(args)}:\n{result.stdout}{result.stderr}")
        cls.pages = {name: Page(cls.root / ".build/preview" / f"{name}.html") for name in cls.expected["documents"]}

    def numbers(self, name):
        """Label -> number the generator gave it."""
        return self.expected["documents"][name]["numbers"]

    def test_captions_and_equations_show_the_generated_numbers(self):
        for name, page in self.pages.items():
            numbers = self.numbers(name)
            for label, number in numbers.items():
                kind = label.split(":")[0]
                if kind == "fig" and number.endswith(")"):
                    continue  # a panel shows its letter only
                shown = page.equations.get(label) if kind == "eq" else page.captions.get(label)
                with self.subTest(document=name, label=label):
                    self.assertEqual(shown, number)

    def written_refs(self, name, numbers):
        """How often the sources of a document reference each label."""
        files = [path for folder in FOLDERS[name] for path in (self.root / "manuscript" / folder).glob("*.md")]
        return Counter(label for path in files for label in REFERENCE.findall(CODE.sub(" ", path.read_text("utf-8")))
                       if label in numbers)

    def test_references_show_the_number_of_their_target(self):
        for name, page in self.pages.items():
            numbers = self.numbers(name)
            refs = [(label, text) for label, text in page.refs if label in numbers]
            # Each reference written is on the page as a link: none lost or left unresolved.
            self.assertEqual(Counter(label for label, _ in refs), self.written_refs(name, numbers), name)
            for label, text in refs:
                with self.subTest(document=name, label=label):
                    self.assertEqual(text.split(",")[0], numbers[label])  # "4, last row" keeps its suffix

    def test_supplementary_panelled_figures_keep_the_S_prefix(self):
        page, info = self.pages["supplementary"], self.expected["documents"]["supplementary"]
        figures = [label for label in info["panels"] if not info["numbers"][label].endswith(")")]
        shown = {label: page.captions.get(label) for label in figures}
        shown.update((label, text) for label, text in page.refs if label in info["panels"])
        self.assertEqual(shown, {label: info["numbers"][label] for label in shown})

    def test_manuscript_references_to_the_supplementary_resolve(self):
        page = self.pages["manuscript"]
        for label, number in self.expected["across"].items():
            self.assertIn((label, number), page.refs)

    def test_counts_match_what_was_written(self):
        result = run(self.root, "counts")
        self.assertEqual(result.returncode, 0, result.stderr)
        blocks = dict(re.findall(r"(?m)^(Manuscript|Supplementary)\n((?:  .*\n?)+)", result.stdout))
        for name, info in self.expected["documents"].items():
            counted = dict(re.findall(r"(?m)^  (Figures|Tables|References)\s+([\d,]+)", blocks[name.capitalize()]))
            self.assertEqual(counted, {"Figures": str(info["figures"]), "Tables": str(info["tables"]),
                                       "References": str(len(info["cited"]))}, name)

    def test_checks_find_nothing_to_fix(self):
        for args, clean in ((["abbreviations", "check"], "Abbreviations: 0 warning(s)"),
                            (["submission", "check", "--strict"], "0 errors, 0 warnings")):
            result = run(self.root, *args)
            with self.subTest(command=args[0]):
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(clean, result.stdout)

    def test_citation_check_accepts_every_key_pandoc_resolves(self):
        result = run(self.root, "citations")
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_a_second_build_has_nothing_to_do(self):
        result = run(self.root, "build", "--submission")
        self.assertIn("[build] up to date", result.stdout, result.stdout + result.stderr)

    def test_comment_quotes_are_on_the_page_once_or_as_often_as_written(self):
        comments = self.expected["comments"]
        for item in comments["quotes"]:
            with self.subTest(comment=item["id"]):
                self.assertIn(item["quote"], self.pages[item["doc"]].text)
        texts = {c["id"]: c["quote"] for c in json.loads((self.root / "notes/review/comments.json").read_text())}
        self.assertNotIn(texts[comments["stale"][0]], self.pages["manuscript"].text)
        self.assertEqual(self.pages["manuscript"].text.count(texts[comments["repeated"]]), 3)

    def test_locate_finds_the_source_lines_of_commented_paragraphs(self):
        # What a click on a paragraph does (POST /__locate), on the rendered text.
        values = {key: entry["text"] for key, entry in json.loads(
            (self.root / "data/values.json").read_text(encoding="utf-8"))["values"].items()}
        quotes = [item for item in self.expected["comments"]["quotes"] if item["doc"] == "manuscript"][:25]
        self.assertTrue(quotes)
        for item in quotes:
            paragraph = next(p for p in self.pages["manuscript"].paragraphs if item["quote"] in p)
            source = (self.root / item["file"]).read_text(encoding="utf-8")
            found = preview_locate.locate(source, paragraph, values)
            lines = " ".join(source.splitlines()[found["start_line"] - 1:found["end_line"]])
            with self.subTest(comment=item["id"]):
                self.assertIn(item["quote"], lines)


if __name__ == "__main__":
    unittest.main()
