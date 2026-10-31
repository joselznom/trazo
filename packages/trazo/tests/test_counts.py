"""trazo counts: main-text words, figures, tables and references."""

import copy
import io
import shutil
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo is imported)

from trazo import counts, preview
from trazo.project import PROJECT

# A built page; test_preview_browser shows it in the preview.
SAMPLE = '''<html><head><title>Title words</title></head><body>
<header id="title-block-header"><h1 class="title">Title words</h1></header>
<div class="pk-front"><div custom-style="Author"><p>Author names here.</p></div>
<div custom-style="Affiliation"><p>Hospital affiliation.</p></div></div>
<section class="level1" id="abstract"><h1>Abstract</h1><p>Four words of summary.</p>
<div class="pk-keywords"><p><strong>Keywords:</strong> invisible; terms.</p></div></section>
<section class="level1" id="introduction"><h1>1. Introduction</h1>
<p>One <em>two</em>, H<sub>2</sub>O [1].</p>
<table id="tbl:a"><caption>Table 1. Caption words.</caption><tr><td>cell words</td></tr></table>
<div class="table-note"><p>Note words here.</p></div>
<figure id="fig:a"><img src="figures/external/ordinary.png"><figcaption>Figure 1. Caption.</figcaption></figure>
<section class="level2" id="use-of-ai-tools"><h2>Use of AI tools</h2><p>We used a model.</p></section>
<section class="level2" id="analysis"><h2>Funding models</h2>
<p>Five more words here.<a href="#fn1" class="footnote-ref" id="fnref1"><sup>1</sup></a></p></section>
<section class="level2 uncounted" id="odd"><h2 class="uncounted">Journal form</h2><p>Hidden by class.</p></section></section>
<section class="level1" id="declarations"><h1>Declarations</h1>
<section class="level2" id="funding-1"><h2>Results funding</h2>
<p>None declared.<a href="#fn2" class="footnote-ref" id="fnref2"><sup>2</sup></a></p></section></section>
<section class="level1 unnumbered" id="bibliography"><h1>References</h1>
<div id="refs" class="references csl-bib-body"><div class="csl-entry" id="ref-a">A. Author. Title.</div>
<div class="csl-entry" id="ref-b">B. Author. Title.</div></div></section>
<section id="footnotes" class="footnotes footnotes-end-of-document"><hr><ol>
<li id="fn1"><p>A counted note.<a href="#fnref1" class="footnote-back">↩︎</a></p></li>
<li id="fn2"><p>An uncounted note.<a href="#fnref2" class="footnote-back">↩︎</a></p></li></ol></section>
</body></html>'''
# The footnote of "Funding models" (3 words) counts with it; the one in the
# declarations does not.
EXPECTED = {"words": 11, "abstract": 4, "figures": 1, "tables": 1, "references": 2,
            "left_out": [("Abstract", 4), ("Use of AI tools", 4), ("Journal form", 3), ("Declarations", 5)]}


class CountTests(unittest.TestCase):
    def test_main_text_leaves_out_what_journals_do_not_count(self):
        found = counts.count_page(SAMPLE, PROJECT.config["counts"]["exclude"])
        self.assertEqual({k: found[k] for k in EXPECTED}, EXPECTED)
        self.assertEqual([(h["text"], h["words"], h["counted"]) for h in found["heads"]], [
            ("Abstract", 4, False), ("1. Introduction", 4, True), ("Use of AI tools", 4, False),
            ("Funding models", 7, True), ("Journal form", 3, False), ("Declarations", 0, False),
            ("Results funding", 5, False), ("References", 0, True)])

    def test_without_exclusions_only_the_class_leaves_a_section_out(self):
        found = counts.count_page(SAMPLE, [])
        self.assertEqual((found["words"], found["abstract"], found["left_out"]), (24, 4, [("Journal form", 3)]))

    def test_abstract_from_the_metadata(self):
        page = ('<header id="title-block-header"><h1 class="title">T</h1><div class="abstract">'
                '<div class="abstract-title">Abstract</div><p>Three abstract words.</p></div></header>'
                '<section class="level1"><h1>Introduction</h1><p>Body.</p></section>')
        found = counts.count_page(page, ["abstract"])
        self.assertEqual((found["words"], found["abstract"]), (1, 3))
        self.assertIsNone(counts.count_page(page.replace('class="abstract"', 'class="other"'), [])["abstract"])

    def test_heading_matches_whole_phrases(self):
        phrases = ["abstract", "data availability", "declarations", "funding", "appendix", "ethics"]
        for text, excluded in {"Abstract": True, "ABSTRACT:": True, "2.1 Data availability statement": True,
                               "Appendix A": True, "Appendix B: Proofs": True, " Declarations ": True,
                               "Abstract syntax trees": False, "Funding models for science": False,
                               "Ethics of AI": False, "Data": False, "Statements and declarations": False}.items():
            with self.subTest(text=text):
                self.assertIs(counts.excluded_heading(text, phrases), excluded)

    def test_check_fails_over_a_limit_or_without_an_abstract_to_limit(self):
        found = dict(EXPECTED, left_out=[], limits={"words": 10, "figures": 1})
        with patch.object(counts, "document_counts", return_value=found):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                self.assertEqual(counts.main(["manuscript", "--check"]), 1)
            self.assertIn("11 / 10 words  over the limit", out.getvalue())
            self.assertIn("1 / 1", out.getvalue())
            self.assertIn("Manuscript: main text over the limit (11 / 10)", err.getvalue())
            with redirect_stdout(io.StringIO()):
                self.assertEqual(counts.main(["manuscript"]), 0)
        found = dict(EXPECTED, abstract=None, left_out=[], limits={"abstract": 250})
        with patch.object(counts, "document_counts", return_value=found):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                self.assertEqual(counts.main(["manuscript", "--check"]), 1)
            self.assertRegex(out.getvalue(), r"Abstract +not found")
            self.assertIn("abstract not found", err.getvalue())

    def test_limits_apply_to_the_first_document_and_follow_the_config_file(self):
        config = copy.deepcopy(PROJECT.config)
        config["limits"].update(words=3000, tables=0)
        with patch.object(counts, "current_config", return_value=config):
            self.assertEqual(counts.page_counts("manuscript", SAMPLE)["limits"], {"words": 3000})
            self.assertEqual(counts.page_counts("supplementary", SAMPLE)["limits"], {})

    @unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
    def test_counts_build_apart_from_the_served_preview(self):
        page = preview.OUT_DIR / "manuscript.html"
        before = page.read_bytes() if page.exists() else None
        found = counts.document_counts("manuscript")
        self.assertGreater(found["words"], 0)
        self.assertEqual(page.read_bytes() if page.exists() else None, before)


if __name__ == "__main__":
    unittest.main()
