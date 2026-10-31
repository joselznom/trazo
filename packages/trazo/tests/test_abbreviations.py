"""Abbreviation source, deliverable and advisory-check contracts."""

import io
import shutil
import subprocess
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
import zipfile
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)

from trazo import (
    abbreviations,
    counts,
    docx_indexes,
    expand,
    frontmatter,
    pandoc_run,
    submission_check,
    submission_fields,
)
from trazo.project import DEFAULTS, ConfigError, Project, merge

LIST = "# List of ABBREVIATIONS {.uncounted}\n\nCI\n:   Confidence interval\n\nAUC\n:   Area under the curve\n"


class AbbreviationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
    def test_pandoc_definition_forms_keep_every_definition_and_paragraph(self):
        text = "# Abbreviations\n\nMRI\n\n:   Magnetic resonance\n    imaging\n\n    Continued paragraph.\n\n~   Another meaning\n\nROI\n~   Region of interest\n"
        found = abbreviations.entries(text)
        self.assertEqual([(entry["term"], entry["meaning"]) for entry in found], [
            ("MRI", "Magnetic resonance imaging Continued paragraph.; Another meaning"),
            ("ROI", "Region of interest")])
        for style in ("table", "list", "inline"):
            with self.subTest(style=style):
                rendered = abbreviations.render(text, {"style": style, "sort": True})
                html = subprocess.run(["pandoc", "-t", "html", "--wrap=none"], input=rendered,
                                      capture_output=True, text=True, check=True).stdout
                for phrase in ("Another meaning", "Continued paragraph.", "Region of interest"):
                    self.assertIn(phrase, html)
                self.assertNotIn("<pre>", html)
                if style == "list":
                    self.assertEqual(html.count("<dd>"), 3)
                    self.assertIn("<p>Continued paragraph.</p>", html)

    def test_commented_and_fenced_headings_leave_prose_untouched(self):
        for hidden in ("<!-- # Abbreviations -->", "<!--\n# Abbreviations\n-->", "```md\n# Abbreviations\n```"):
            with self.subTest(hidden=hidden):
                text = hidden + "\n\nThis prose must survive.\n\n# Results\n\nResults.\n"
                self.assertEqual(abbreviations.render(text, DEFAULTS["abbreviations"]), text)

    def test_recognition_sort_and_style_configuration(self):
        for heading in ("# List of ABBREVIATIONS", "## 2.3 Abbreviations", "# Glossary {#abbreviations}"):
            text = heading + "\n\nZ-2\n:   Last\n\na-b\n:   First\n\nAB\n:   Second\n"
            self.assertEqual([entry["term"] for entry in abbreviations.entries(text)], ["a-b", "AB", "Z-2"])
            self.assertEqual([entry["term"] for entry in abbreviations.entries(text, False)], ["Z-2", "a-b", "AB"])
        self.assertFalse(abbreviations.entries("```md\n" + LIST + "```"))
        self.assertEqual([entry["term"] for entry in abbreviations.entries(
            LIST + "\n```md\nXYZ\n:   Not an entry\n```\n")], ["AUC", "CI"])
        for extra in ({"style": "footnote"}, {"sort": "yes"}, {"ignore": [1]}):
            with self.subTest(extra=extra), self.assertRaises(ConfigError):
                merge(DEFAULTS, {"abbreviations": extra})

    @unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
    def test_layouts_word_style_and_counts_exclusion(self):
        for style in ("table", "list", "inline"):
            with self.subTest(style=style):
                text = "# Introduction\n\nThree real words.\n\n" + LIST + "\n# End\n\nTwo words.\n"
                rendered = abbreviations.render(text, {"style": style, "sort": True})
                self.assertLess(rendered.index("AUC"), rendered.index("CI"))
                html = pandoc_run.run(["-f", "markdown", "-t", "html", "--filter=pandoc-crossref"],
                                      input=rendered, text=True).stdout
                found = counts.count_page(html, DEFAULTS["counts"]["exclude"])
                self.assertEqual((found["words"], found["tables"]), (5, 0))
                word = subprocess.run(["pandoc", "-f", "markdown", "-t", "docx"],
                                      input=rendered.encode(), capture_output=True, check=True).stdout
                with zipfile.ZipFile(io.BytesIO(docx_indexes.apply(word))) as archive:
                    document = ET.fromstring(archive.read("word/document.xml"))
                ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
                paragraphs = ["".join(node.itertext()) for node in document.findall(".//w:body/w:p", ns)]
                if style == "table":
                    table = document.find(".//w:tbl", ns)
                    self.assertIsNotNone(table)
                    self.assertEqual(table.find("w:tblPr/w:tblStyle", ns).get("{" + ns["w"] + "}val"), "Abbreviations")
                    self.assertEqual(["".join(node.itertext()) for node in table.findall("w:tr/w:tc", ns)],
                                     ["Abbreviation", "Meaning", "AUC", "Area under the curve", "CI", "Confidence interval"])
                    self.assertFalse(any("Table 1" in paragraph for paragraph in paragraphs))
                elif style == "inline":
                    self.assertIn("AUC, Area under the curve; CI, Confidence interval.", paragraphs)
                else:
                    self.assertIn("<dl>", html)
                    self.assertIn("CI", paragraphs)

    def test_annotation_preserves_text_and_protected_nodes(self):
        source = '<p>AUC &amp; <em>CI</em> <span class="pv-val">AUC</span></p><code>AUC</code><math><mi>CI</mi></math>'
        html = abbreviations.annotate(source, abbreviations.entries(LIST))
        self.assertIn('<abbr title="Area under the curve">AUC</abbr> &amp;', html)
        self.assertIn('<em><abbr title="Confidence interval">CI</abbr></em>', html)
        self.assertIn('<span class="pv-val">AUC</span>', html)
        self.assertIn('<code>AUC</code><math><mi>CI</mi></math>', html)

    def test_annotation_only_changes_body_prose_and_recognizes_plurals(self):
        source = '<html><head><title>MRI study</title></head><body><h1>MRI study</h1><p data-term="MRI">MRIs MRI\'s <a href="MRI">MRI</a> <span class="math">MRI</span></p></body></html>'
        found = abbreviations.entries("# Abbreviations\n\nMRI\n:   Magnetic resonance imaging\n")
        html = abbreviations.annotate(source, found)
        self.assertIn('<title>MRI study</title>', html)
        self.assertIn('<h1>MRI study</h1>', html)
        self.assertIn('data-term="MRI"', html)
        self.assertIn('<a href="MRI">MRI</a>', html)
        self.assertIn('<span class="math">MRI</span>', html)
        self.assertEqual(html.count('<abbr title="Magnetic resonance imaging">'), 2)
        self.assertIn('>MRIs</abbr>', html)
        self.assertIn(">MRI's</abbr>", html)

    @unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
    def test_empty_sections_disappear_from_preview_and_word(self):
        for body in ("", "\n<!-- Add terms here.\nAUC\n:   Area under the curve\n-->\n"):
            with self.subTest(body=body):
                text = "# Introduction\n\nKeep this.\n\n# Abbreviations {#abbreviations}\n" + body + "\n# Results\n\nKeep that.\n"
                rendered = abbreviations.render(text, DEFAULTS["abbreviations"])
                html = subprocess.run(["pandoc", "-f", "markdown", "-t", "html"],
                                      input=rendered, capture_output=True, text=True, check=True).stdout
                self.assertNotIn("Abbreviations", html)
                self.assertIn("Keep that.", html)
                word = subprocess.run(["pandoc", "-f", "markdown", "-t", "docx"],
                                      input=rendered.encode(), capture_output=True, check=True).stdout
                with zipfile.ZipFile(io.BytesIO(word)) as archive:
                    document = ET.fromstring(archive.read("word/document.xml"))
                self.assertNotIn("Abbreviations", "".join(document.itertext()))
                self.assertIn("Keep this.", "".join(document.itertext()))


@unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
class AbbreviationCheckTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / "manuscript/main").mkdir(parents=True)
        self.source = self.root / "manuscript/main/paper.md"
        self.project = Project(self.root, merge(DEFAULTS, {"documents": {
            "manuscript": {"path": "manuscript/main"}}, "abbreviations": {"ignore": ["DNA"]}}), True)
        stack = ExitStack()
        self.addCleanup(stack.close)
        for module in (expand, frontmatter, submission_check, submission_fields):
            stack.enter_context(patch.object(module, "PROJECT", self.project))

    def findings(self, text):
        self.source.write_text(text)
        return abbreviations.check(self.project, "manuscript")

    def test_unreadable_content_is_preserved_and_reported_even_without_entries(self):
        for body in ("MRI means imaging.", "MRI\n:   Imaging\n\nUnrecognised prose.", "```md\nMRI\n:   Imaging\n```"):
            with self.subTest(body=body):
                text = "# Abbreviations\n\n" + body + "\n\n# Results\n\nKeep me.\n"
                self.assertEqual(abbreviations.render(text, DEFAULTS["abbreviations"]), text)
                warning = self.findings(text)[0]
                self.assertEqual(warning["label"], "Abbreviations list could not be read")
                self.assertIn("Abbreviations list could not be read:", warning["detail"])
                self.assertEqual((warning["file"], warning["line"]), ("manuscript/main/paper.md", 1))

    def test_large_prose_check_has_bounded_runtime(self):
        text = "# Methods\n\n" + "ordinary " * 60000 + "\n\n" + LIST
        started = time.monotonic()
        found = self.findings(text)
        self.assertLess(time.monotonic() - started, 5.0)
        self.assertEqual([(item["label"], item["value"]) for item in found],
                         [("Listed but unused", "AUC"), ("Listed but unused", "CI")])

    def test_plural_uses_share_canonical_first_use_and_unused_checks(self):
        text = "# Abstract\n\nMagnetic resonance imaging (MRIs).\n\n# Methods\n\nMRI's and ROIs.\n\n# Abbreviations\n\nMRI\n:   Magnetic resonance imaging\n\nROI\n:   Region of interest\n"
        found = self.findings(text)
        self.assertEqual([(item["label"], item["value"]) for item in found],
                         [("Define at first use", "MRI"), ("Define at first use", "ROI")])

    def test_each_warning_has_its_source_and_separate_first_use(self):
        text = "# Abstract\n\nArea under the curve (AUC) was measured.\n\n# Methods {#methods}\n\nAUC improved. pCR and kBET predicted COVID-19. HbA1c DNA II.\n\n" + LIST
        findings = self.findings(text)
        self.assertEqual([(item["label"], item["value"]) for item in findings], [
            ("Listed but unused", "CI"), ("Define at first use", "AUC"),
            ("Used but not listed", "pCR"), ("Used but not listed", "kBET"), ("Used but not listed", "COVID-19"),
            ("Used but not listed", "HbA1c")])
        first = findings[1]
        self.assertEqual((first["file"], first["line"], first["hash"], first["doc"], first["edit"]),
                         ("manuscript/main/paper.md", 7, "methods", "manuscript", True))
        self.assertEqual(findings[0]["section"], "List of ABBREVIATIONS")

    def test_definitions_plural_hyphen_variants_and_ignored_material(self):
        text = "# Abstract\n\nAUC (area under the curve) and confidence intervals (CI).\n\n# Methods\n\nArea-under-the-curve (AUC) and CI (confidence interval). DNA II IV.\n\n"
        text += "`XYZ` $XYZ$ https://XYZ.org [@XYZ] [@fig:XYZ] {#XYZ}\n\n```\nXYZ\n```\n\n"
        text += "    XYZ\n\n[XYZ]: https://XYZ.org\n\n\\(XYZ\\)\n\n"
        text += "DataWeaver PyRadiomics CheckList.\n\n"
        text += "![CI](fig.png){#fig:XYZ}\n\n| CI | Value |\n|---|---|\n| 1 | 2 |\n\n" + LIST
        self.assertEqual(self.findings(text), [])

    def test_first_use_in_abstract_is_independent_of_main(self):
        found = self.findings("# Abstract\n\nAUC rose.\n\n# Results\n\nArea under the curve (AUC).\n\n" + LIST)
        self.assertEqual([(item["label"], item["value"]) for item in found],
                         [("Listed but unused", "CI"), ("Define at first use", "AUC")])
        self.assertEqual(found[-1]["line"], 3)
        self.assertIn("abstract", found[-1]["detail"])

    def test_absent_or_empty_list_has_no_findings_or_form_field(self):
        for suffix in ("", "\n# Abbreviations\n", "\n# Abbreviations\n\n<!-- Add terms. -->\n"):
            with self.subTest(suffix=suffix):
                self.assertEqual(self.findings("# Methods\n\nCT MRI CLEAR.\n" + suffix), [])
                result = submission_check.report(lambda: dict.fromkeys((row[0] for row in counts.ROWS), 0))
                group = next(group for group in result["groups"] if group["id"] == "abbreviations")
                self.assertFalse(any(item["status"] == "warn" for item in group["items"]))
                self.assertNotIn("Abbreviations", [field["label"] for field in submission_fields.form_fields({})])

    def test_table_and_caption_uses_only_prevent_unused_warnings(self):
        text = "# Methods\n\nPlain prose.\n\n![AUC and MRI](figure.png)\n\n"
        text += "| CI | DICOM |\n|---|---|\n| 1 | 2 |\n\nTable: CMS results.\n\n"
        text += "::: {.table-note}\n\nGE and SIGNA.\n\n:::\n\n"
        for heading in ("# List of ABBREVIATIONS {.uncounted}", "# Glossary {#abbreviations}"):
            with self.subTest(heading=heading):
                self.assertEqual(self.findings(text + LIST.replace(LIST.splitlines()[0], heading)), [])

    def test_hyphenated_names_do_not_hide_next_real_first_use(self):
        text = "# Abstract\n\nNSCLC-Radiomics MAMA-MIA.\n\nNSCLC rose.\n\n"
        text += "# Methods\n\nNSCLC-Radiomics AUC-Based CI-NAME. Non-small-cell lung cancer (NSCLC). "
        text += "\n\nAUC-based COVID-19 ISPY2.\n\n" + LIST + "\nNSCLC\n:   Non-small-cell lung cancer\n"
        found = self.findings(text)
        self.assertEqual([(item["label"], item["value"]) for item in found], [
            ("Define at first use", "NSCLC"), ("Define at first use", "AUC"),
            ("Used but not listed", "COVID-19"), ("Used but not listed", "ISPY2")])
        self.assertIn("abstract", found[0]["detail"])
        self.assertIn("main", found[1]["detail"])
        self.assertEqual((found[0]["line"], found[1]["line"]), (5, 11))

    def test_metadata_abstract_warning_points_to_its_yaml_line(self):
        self.project.path("metadata").write_text('title: Paper\nabstract: |\n  AUC rose.\n')
        found = self.findings("# Methods\n\nArea under the curve (AUC).\n\n" + LIST)
        warning = next(item for item in found if item["label"] == "Define at first use")
        self.assertEqual((warning["value"], warning["file"], warning["line"], warning["block"]),
                         ("AUC", "manuscript/metadata.yaml", 3, "front"))

    def test_included_text_keeps_parent_sections_and_exact_source_lines(self):
        (self.source.parent / "parts").mkdir()
        (self.source.parent / "parts/abstract.md").write_text("AUC rose.\n")
        (self.source.parent / "parts/terms.md").write_text("CI\n:   Confidence interval\n\nAUC\n:   Area under the curve\n")
        found = self.findings("# Abstract {#abstract}\n\n!include parts/abstract.md\n\n"
                              "# Methods\n\nArea under the curve (AUC).\n\n"
                              "# Abbreviations\n\n!include parts/terms.md\n")
        self.assertEqual([(item["label"], item["value"], item["file"], item["line"]) for item in found],
                         [("Listed but unused", "CI", "manuscript/main/parts/terms.md", 1),
                          ("Define at first use", "AUC", "manuscript/main/parts/abstract.md", 1)])
        self.assertEqual(found[-1]["hash"], "abstract")
        self.assertIn("abstract", found[-1]["detail"])

    def test_report_document_and_inline_form_field(self):
        self.source.write_text("# Abstract\n\nArea under the curve (AUC).\n\n# Methods\n\nAUC (area under the curve).\n\n" + LIST)
        fields = submission_fields.form_fields({})
        field = next(field for field in fields if field["label"] == "Abbreviations")
        self.assertEqual(field["text"], "AUC, Area under the curve; CI, Confidence interval.")
        report = Project(self.root, merge(self.project.config, {"kind": "report"}), True)
        findings = abbreviations.check(report, "manuscript")
        self.assertEqual([(item["label"], item["value"]) for item in findings], [("Listed but unused", "CI")])
