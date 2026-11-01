"""Supplementary numbering survives preview, references and selected exports."""

import io
import re
import shutil
import threading
import unittest
import xml.etree.ElementTree as ET
import zipfile
from html import unescape
from unittest import mock

import _support

from trazo import build_graph, docx_freshness, expand, labels, preview, preview_export
from trazo.project import PROJECT

NBSP = "\u00a0"


def docx_text(path):
    with zipfile.ZipFile(path) as archive:
        return "".join(ET.fromstring(archive.read("word/document.xml")).itertext())


@unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
class DocumentNumbering(unittest.TestCase):
    def setUp(self):
        self.image = _support.ROOT / "figures/external/numbering.svg"
        self.image.parent.mkdir(parents=True, exist_ok=True)
        self.image.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
                              '<rect width="10" height="10"/></svg>')
        self.addCleanup(self.image.unlink, missing_ok=True)
        self.source = ("# Numbering contract\n\n"
            "![Numbering figure.](figures/external/numbering.svg){#fig:numbering}\n\n"
            "| A | B |\n|---|---|\n| 1 | 2 |\n\nTable: Numbering table. {#tbl:numbering}\n\n"
            "Figure reference: [@fig:numbering]. Table reference: [@tbl:numbering].\n\n"
            "Bare reference: [-@fig:numbering; -@tbl:numbering].\n")
        for folder in ("main", "supplementary"):
            path = _support.ROOT / f"manuscript/{folder}/99_numbering.md"
            path.write_text(self.source)
            self.addCleanup(path.unlink, missing_ok=True)

    def test_caption_and_reference_numbers_match_in_html_and_selected_exports(self):
        for document, prefix, table_number in (("manuscript", "", 2), ("supplementary", "S", 1)):
            with self.subTest(document=document):
                self.assertTrue(preview.build(document), preview.STATE[document]["error"])
                page = (preview.OUT_DIR / f"{document}.html").read_text()
                visible = unescape(re.sub(r"<[^>]+>", "", page))
                self.assertIn(f"Figure {prefix}1", visible)
                self.assertIn(f"Table {prefix}{table_number}", visible)
                self.assertRegex(page, rf'href="#fig:numbering">{prefix}1</a>')
                self.assertRegex(page, rf'href="#tbl:numbering">{prefix}{table_number}</a>')
                self.assertIn(f"Bare reference: {prefix}1, {prefix}{table_number}.",
                              " ".join(visible.split()))
                for kind, identifier in (("section", "numbering-contract"),
                                         ("figure", "fig:numbering"), ("table", "tbl:numbering")):
                    with self.subTest(kind=kind):
                        data, _, _ = preview_export.export(document, kind, identifier, "docx")
                        with zipfile.ZipFile(io.BytesIO(data)) as archive:
                            text = "".join(ET.fromstring(archive.read("word/document.xml")).itertext())
                        if kind != "table":
                            self.assertIn(f"Figure {prefix}1", text)
                        if kind != "figure":
                            self.assertIn(f"Table {prefix}{table_number}", text)
                data, _, _ = preview_export.export(document, "table", "tbl:numbering", "md")
                self.assertIn(f"Table {prefix}{table_number}", data.decode())

    def test_panelled_figures_keep_the_document_prefix(self):
        image = "figures/external/numbering.svg"
        self.source = (f"# Panels\n\n::: {{#fig:panels}}\n![First.]({image}){{#fig:first}}\n\n"
                       f"![Second.]({image}){{#fig:second}}\n\nBoth panels.\n:::\n\n"
                       "See [@fig:panels] and [@fig:second].\n")
        for document, folder, prefix in (("manuscript", "main", ""), ("supplementary", "supplementary", "S")):
            with self.subTest(document=document):
                (_support.ROOT / f"manuscript/{folder}/99_numbering.md").write_text(self.source)
                self.assertTrue(preview.build(document), preview.STATE[document]["error"])
                page = (preview.OUT_DIR / f"{document}.html").read_text()
                visible = " ".join(unescape(re.sub(r"<[^>]+>", "", page)).split())
                number = re.search(rf"Figure {prefix}(\d+): Both panels", visible)
                self.assertTrue(number, visible)
                self.assertRegex(page, rf'href="#fig:panels">{prefix}{number[1]}</a>')
                self.assertRegex(page, rf'href="#fig:second">{prefix}{number[1]}\s\(b\)</a>')
                data, _, _ = preview_export.export(document, "figure", "fig:panels", "docx")
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    text = "".join(ET.fromstring(archive.read("word/document.xml")).itertext())
                self.assertRegex(text, rf"Figure {prefix}\d+: Both panels")


@unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
class BetweenDocuments(unittest.TestCase):
    """The manuscript cites the supplementary and back."""

    def write(self, folder, text):
        path = _support.ROOT / f"manuscript/{folder}/98_cross.md"
        path.write_text(text)
        self.addCleanup(path.unlink, missing_ok=True)

    def setUp(self):
        image = _support.ROOT / "figures/external/cross.svg"
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10"/></svg>')
        self.addCleanup(image.unlink, missing_ok=True)
        self.write("main", "# Cross\n\n![Main.](figures/external/cross.svg){#fig:cross-main}\n\n"
                           "See [@fig:cross-extra] and [@sec:cross-sens].\n")
        self.supplementary("")

    def supplementary(self, before):
        self.write("supplementary", f"# Cross\n\n## Sensitivity {{#sec:cross-sens}}\n\n{before}"
                                    "![Extra.](figures/external/cross.svg){#fig:cross-extra}\n\n"
                                    "As in [@fig:cross-main].\n")

    def test_preview_pages_link_to_the_other_document(self):
        for name in ("manuscript", "supplementary"):
            self.assertTrue(preview.build(name), preview.STATE[name]["error"])
        page = (preview.OUT_DIR / "manuscript.html").read_text()
        self.assertIn('href="supplementary.html#fig:cross-extra">S1</a>', page)
        self.assertIn('href="supplementary.html#sec:cross-sens">S1.1</a>', page)
        self.assertNotIn("trazo-documents", page)
        page = (preview.OUT_DIR / "supplementary.html").read_text()
        self.assertIn('href="manuscript.html#fig:cross-main">1</a>', page)
        self.assertEqual(preview.STATE["manuscript"]["warnings"], [])

    def test_a_title_prefix_words_captions_and_references(self):
        words = {"number_prefix": "", "title_prefix": "Supplementary"}
        with mock.patch.dict(PROJECT.config["documents"]["supplementary"], words):
            for name in ("manuscript", "supplementary"):
                self.assertTrue(preview.build(name), preview.STATE[name]["error"])
        page = re.sub(r"[ \n]+", " ", (preview.OUT_DIR / "manuscript.html").read_text())
        self.assertIn('Supplementary fig.\u00a0<a href="supplementary.html#fig:cross-extra">1</a>', page)
        page = re.sub(r"[ \n]+", " ", (preview.OUT_DIR / "supplementary.html").read_text())
        self.assertIn("<strong>Supplementary Figure 1.</strong> Extra.", page)

    def watched(self, edit, refresh=False):
        """The pages the watcher rebuilds after `edit` of the supplementary
        (`refresh`: and a build or check reindexing before the watcher).
        self.indexed: the documents the label index expanded for them."""
        for name in ("manuscript", "supplementary"):
            self.assertTrue(preview.build(name), preview.STATE[name]["error"])
        built, polls, build, expand_files = [], 0, preview.build, expand.expand_files
        self.indexed = []

        def tick(_):  # the watcher's wait between passes; True stops it after two passes
            nonlocal polls
            polls += 1
            if polls == 1:
                self.supplementary(edit)
                if refresh:
                    labels.index(PROJECT)
                self.indexed.clear()
            return polls > 2

        def indexing(*args, **kwargs):  # the pages expand theirs through preview_render's own name
            self.indexed.append(kwargs["document"])
            return expand_files(*args, **kwargs)

        # Through stop_event, not a patched time.sleep: another thread's sleep would count as a pass.
        with mock.patch.object(expand, "expand_files", side_effect=indexing), \
             mock.patch.object(preview, "build", side_effect=lambda name: built.append(name) or build(name)):
            preview.watch(stop_event=mock.Mock(**{"wait.side_effect": tick}))
        return built

    def test_the_watcher_rebuilds_pages_that_cite_new_numbers(self):
        self.assertEqual(self.watched("![New.](figures/external/cross.svg){#fig:cross-new}\n\n"),
                         ["supplementary", "manuscript"])
        self.assertIn('href="supplementary.html#fig:cross-extra">S2</a>',
                      (preview.OUT_DIR / "manuscript.html").read_text())

    def test_pages_rebuilt_together_index_each_document_once(self):
        self.watched("![New.](figures/external/cross.svg){#fig:cross-new}\n\n")
        self.assertEqual(sorted(self.indexed), sorted(PROJECT.documents))

    def test_the_first_pages_and_the_watcher_share_one_index(self):
        expand_files, indexed = expand.expand_files, []

        def indexing(*args, **kwargs):
            indexed.append(kwargs["document"])
            return expand_files(*args, **kwargs)

        stop = mock.Mock(**{"is_set.return_value": False, "wait.return_value": True})  # stop at the first poll
        with mock.patch.object(expand, "expand_files", side_effect=indexing):
            preview.build_and_watch(stop)
        self.assertEqual(sorted(indexed), sorted(PROJECT.documents))
        self.assertIn('href="supplementary.html#fig:cross-extra">S1</a>',
                      (preview.OUT_DIR / "manuscript.html").read_text())

    def test_a_check_during_a_rebuild_does_not_hide_the_next_numbers(self):
        # A submission check in a request thread refreshes labels.json while
        # the watcher's pages still use the index it started with.
        for name in ("manuscript", "supplementary"):
            self.assertTrue(preview.build(name), preview.STATE[name]["error"])
        polls, build = 0, preview.build
        figure = "![New.](figures/external/cross.svg){{#fig:cross-new{}}}\n\n"

        def tick(_):  # the watcher's wait between passes; True stops it after three passes
            nonlocal polls
            polls += 1
            if polls == 1:
                self.supplementary(figure.format(1))
            return polls > 3

        def building(name):
            done = build(name)
            if name == "supplementary" and polls == 1:
                self.supplementary(figure.format(1) + figure.format(2))
                check = threading.Thread(target=labels.index, args=(PROJECT,))
                check.start()
                check.join()
            return done

        with mock.patch.object(preview, "build", side_effect=building):
            preview.watch(stop_event=mock.Mock(**{"wait.side_effect": tick}))
        self.assertIn('href="supplementary.html#fig:cross-extra">S3</a>',
                      (preview.OUT_DIR / "manuscript.html").read_text())

    def test_the_watcher_compares_with_the_numbers_its_pages_used(self):
        self.assertEqual(self.watched("![New.](figures/external/cross.svg){#fig:cross-new}\n\n", refresh=True),
                         ["supplementary", "manuscript"])

    def test_an_edit_during_the_first_builds_rebuilds_every_page(self):
        build = preview.build

        def building(name):
            done = build(name)
            if name == "manuscript":
                self.supplementary("![New.](figures/external/cross.svg){#fig:cross-new}\n\n")
            return done

        stop = mock.Mock(**{"is_set.return_value": False, "wait.side_effect": [False, True]})  # one watcher pass
        with mock.patch.object(preview, "build", side_effect=building):
            preview.build_and_watch(stop)
        self.assertIn('href="supplementary.html#fig:cross-extra">S2</a>',
                      (preview.OUT_DIR / "manuscript.html").read_text())

    def test_an_edit_while_the_watcher_starts_rebuilds_every_page(self):
        for name in ("manuscript", "supplementary"):
            self.assertTrue(preview.build(name), preview.STATE[name]["error"])
        before, expand_files = preview.snapshot(), expand.expand_files

        def indexing(*args, **kwargs):  # the edit lands while the watcher reads the numbers
            if not self.edited:
                self.edited = True
                self.supplementary("![New.](figures/external/cross.svg){#fig:cross-new}\n\n")
            return expand_files(*args, **kwargs)

        self.edited = False
        with mock.patch.object(expand, "expand_files", side_effect=indexing):  # one watcher pass
            preview.watch(previous=before, stop_event=mock.Mock(**{"wait.side_effect": [False, True]}))
        self.assertIn('href="supplementary.html#fig:cross-extra">S2</a>',
                      (preview.OUT_DIR / "manuscript.html").read_text())

    def test_a_text_edit_rebuilds_only_its_page(self):
        self.assertEqual(self.watched("More text.\n\n"), ["supplementary"])

    def test_a_section_export_reads_the_supplementarys_numbers(self):
        data, _, _ = preview_export.export("manuscript", "section", "cross", "docx")
        self.assertIn(f"See fig.{NBSP}S1 and sec.{NBSP}S1.1.", docx_text(io.BytesIO(data)))

    def test_the_submission_check_lists_what_cannot_be_resolved(self):
        self.write("main", "# Cross\n\n![Main.](figures/external/cross.svg){#fig:cross-main}\n\n"
                           "See [@fig:cross-extra], [@fig:nowhere] and [@eq:cross-eq].\n")
        self.supplementary("$$x$$ {#eq:cross-eq}\n\n![Again.](figures/external/cross.svg){#fig:cross-main}\n\n")
        found = [(item["status"], item["detail"]) for item in labels.check(PROJECT, "manuscript")]
        self.assertEqual(found, [
            ("error", "Undefined cross-reference: fig:nowhere"),
            ("error", "Cross-document references cover figures, tables and sections: eq:cross-eq is in Supplementary"),
            ("warn", "fig:cross-main is labelled in Manuscript and Supplementary")])
        # The manuscript's Word file is stale when the supplementary changes.
        self.assertIn(_support.ROOT / "manuscript/supplementary/98_cross.md",
                      docx_freshness.word_inputs(PROJECT, "manuscript"))

    def test_the_word_file_follows_the_supplementarys_numbering(self):
        # The fixture's metadata keeps pandoc-crossref's "fig." and "sec.".
        builder = build_graph.Builder(PROJECT, log=lambda text: None)
        output = PROJECT.document_docx("manuscript")
        self.addCleanup(output.unlink, missing_ok=True)
        builder.pandoc_docx("manuscript")
        self.assertIn(f"See fig.{NBSP}S1 and sec.{NBSP}S1.1.", docx_text(output))
        self.supplementary("![Inserted.](figures/external/cross.svg){#fig:cross-new}\n\n")
        builder.pandoc_docx("manuscript")
        self.assertIn(f"See fig.{NBSP}S2 and sec.{NBSP}S1.1.", docx_text(output))
        self.assertEqual(labels.stored(PROJECT)["supplementary"]["labels"]["fig:cross-extra"], "fig 2")


if __name__ == "__main__":
    unittest.main()
