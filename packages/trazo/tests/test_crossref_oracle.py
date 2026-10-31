"""trazo's cross-reference pass gives what pandoc-crossref gives: the
real filter as the oracle, where it is installed (development and CI)."""

import base64
import html
import json
import re
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

import _support

from trazo import crossref, numbering, pandoc_run

NBSP = "\u00a0"
# trazo's pass (pandoc_run) and the real filter, its oracle.
ENGINES = ("trazo", "pandoc-crossref")


def run_with(engine, args, cwd=None, text=False):
    if engine == "trazo":
        return pandoc_run.run(args, cwd=cwd, text=text)
    return subprocess.run(["pandoc", *args], cwd=cwd or _support.ROOT, capture_output=True, text=text)


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
SOURCE = """# Methods {#sec:methods}

We studied patients [@smith2020] as in [@fig:one; @fig:two; @fig:three], -@fig:two and [@tbl:counts].
[@sec:methods] and [@sec:later] describe it; see also [@fig:one, panel A].

![[Title one.]{.caption-title} Legend one.](figures/crossref.png){#fig:one width=50%}

![Two.](figures/crossref.png){#fig:two}

![Three.](figures/crossref.png){#fig:three}

Table: Counts by group [@fig:one]. {#tbl:counts}

| Group | N |
|-------|---|
| A     | 1 |

## Later {#sec:later}

$$
E = w_b B
$$ {#eq:score}

As in [@eq:score].
"""
METADATA = """figureTitle: "Figure"
tableTitle: "Table"
figPrefix: "Figure"
tblPrefix: "Table"
secPrefix: "Section"
figureTemplate: "**$$figureTitle$$ $$i$$.** $$t$$"
tableTemplate: "**$$tableTitle$$ $$i$$.** $$t$$"
numbersections: false
link-citations: true
reference-section-title: "References"
"""


def document_xml(path):
    with zipfile.ZipFile(path) as archive:
        return re.sub(r'\s+w:rsid\w*="[^"]*"', "", archive.read("word/document.xml").decode("utf-8"))


@unittest.skipUnless(shutil.which("pandoc") and shutil.which("pandoc-crossref"), "needs pandoc and pandoc-crossref")
class SameAsPandocCrossref(unittest.TestCase):
    def setUp(self):
        self.folder = _support.ROOT / ".build" / "crossref-test"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, self.folder, True)
        image = _support.ROOT / "figures" / "crossref.png"
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(PNG)
        self.addCleanup(image.unlink, missing_ok=True)
        (self.folder / "paper.md").write_text(SOURCE, encoding="utf-8")
        (self.folder / "metadata.yaml").write_text(METADATA, encoding="utf-8")
        (self.folder / "numbering.json").write_text(json.dumps(numbering.metadata("supplementary")))

    def build(self, engine, *flags):
        result = run_with(engine, [str(self.folder / "paper.md"), f"--metadata-file={self.folder / 'metadata.yaml'}",
                                   "--standalone", "--bibliography=bibliography/references.bib",
                                   "--csl=bibliography/csl/ieee.csl", "--resource-path=.", "--filter",
                                   "pandoc-crossref", "--citeproc", *flags])
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def test_word_and_html_output_match(self):
        for name, flags in (("manuscript", []), ("supplementary", [f"--metadata-file={self.folder / 'numbering.json'}"])):
            with self.subTest(document=name):
                outputs = {}
                for engine in ENGINES:
                    target = self.folder / f"{name}-{engine}.docx"
                    self.build(engine, *flags, "-o", str(target))
                    page = self.build(engine, *flags, "--metadata=linkReferences:true", "--to=html5").stdout.decode()
                    outputs[engine] = (document_xml(target), page)
                self.assertEqual(outputs["trazo"][0], outputs["pandoc-crossref"][0])
                self.assertEqual(outputs["trazo"][1], outputs["pandoc-crossref"][1])
                prefix = "S" if name == "supplementary" else ""
                visible = html.unescape(re.sub(r"<[^>]+>", "", outputs["trazo"][1])).replace(NBSP, " ")
                self.assertIn(f"Figure {prefix}1-{prefix}3, {prefix}2 and Table {prefix}1", visible)
                self.assertIn(f"Section {prefix}1 and Section {prefix}1.1", visible)
                self.assertIn("(1)", visible)


CASES = sorted((_support.FIXTURE.parent / "crossref_cases").glob("*.md"))


@unittest.skipUnless(shutil.which("pandoc") and shutil.which("pandoc-crossref"), "needs pandoc and pandoc-crossref")
class SameAstAsPandocCrossref(unittest.TestCase):
    """tests/crossref_cases/*.md: each feature against the real filter."""

    def test_cases(self):
        self.assertTrue(CASES)
        for case in CASES:
            for flags in ([], ["--metadata=linkReferences:true"]):
                with self.subTest(case=case.name, flags=flags):
                    def ast(*extra):
                        result = subprocess.run(["pandoc", str(case), *flags, *extra, "-t", "json"],
                                                capture_output=True, check=True, text=True)
                        return json.loads(result.stdout), result.stderr
                    expected, expected_stderr = ast("--filter=pandoc-crossref")
                    source, stderr = ast()
                    before = json.dumps(source)
                    warnings = []
                    self.assertEqual(crossref.apply(source, warn=warnings.append)["blocks"], expected["blocks"])
                    # Same messages, in the same order, as pandoc-crossref.
                    self.assertEqual(stderr + "".join(f"{w}\n" for w in warnings), expected_stderr)
                    self.assertEqual(json.dumps(source), before)

    def test_duplicate_label_fails_like_pandoc_crossref(self):
        folder = Path(tempfile.mkdtemp(prefix="trazo-duplicate-"))
        self.addCleanup(shutil.rmtree, folder, True)
        (folder / "dup.md").write_text("![A](x.png){#fig:a}\n\n![B](x.png){#fig:a}\n")
        results = {engine: run_with(engine, ["dup.md", "--filter", "pandoc-crossref", "-t", "plain"], folder, True)
                   for engine in ENGINES}
        for engine, result in results.items():
            with self.subTest(engine=engine):
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Duplicate label: fig:a\n", result.stderr)
        self.assertEqual(results["trazo"].stderr, "trazo crossref: Duplicate label: fig:a\n")

if __name__ == "__main__":
    unittest.main()
