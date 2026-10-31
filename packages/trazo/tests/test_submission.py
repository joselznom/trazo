"""Submission placement, editable sources and actual Pandoc deliverables."""

import io
import json
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import _support

from trazo import counts, dependencies, expand, frontmatter, numbering, preview, preview_export
from trazo.project import DEFAULTS, ConfigError, Project, merge


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.write("manuscript/main/01.md", "# Introduction\n\nMain words [@first].\n")
        self.write("manuscript/main/02.md", "# Conclusion\n\nFinal words.\n")
        self.declaration = self.write("manuscript/declarations/01.md",
            "# Declarations\n\n## Local wording {#decl-ethics}\n\nUnique ethics paragraph [@second].\n\n"
            "## Funding {#decl-funding .form-only}\n\nSecret form text.\n\n"
            "### Detail\n\nHidden detail.\n\n## Data {#decl-data}\n\nRepository data.\n")
        self.write("manuscript/declarations/02.md", "## Code {#decl-code}\n\nRepository scripts.\n")
        self.write("manuscript/cover-letter/letter.md", "Dear Editor,\n\nPlease consider this work.\n")
        self.write("manuscript/metadata.yaml", json.dumps({"title": "Unique paper title",
            "date": "Publication date sentinel",
            "author": [{"name": "Ana Pérez", "email": "ana@example.org", "corresponding": True,
                        "equal-contributor": True, "orcid": "0000-0002-1825-0097",
                        "affiliations": ["University"]}], "reference-section-title": "References"}))
        self.write("bibliography/references.bib",
            "@article{first, author={Alpha, A}, title={First source}, journal={Journal}, year={2020}}\n"
            "@article{second, author={Beta, B}, title={Second source}, journal={Journal}, year={2021}}\n")
        shutil.copy(_support.ROOT / "bibliography/csl/ieee.csl", self.root / "bibliography/ieee.csl")

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def project(self, extra=None):
        config = merge(DEFAULTS, {"paths": {"csl": "bibliography/ieee.csl"}})
        return Project(self.root, merge(config, extra or {}), True)

    def bind(self, project):
        stack = ExitStack()
        self.addCleanup(stack.close)
        for module in (expand, frontmatter, numbering, preview, preview_export, dependencies):
            stack.enter_context(patch.object(module, "PROJECT", project))
        stack.enter_context(patch.object(preview, "ROOT", self.root))
        stack.enter_context(patch.object(preview, "DOCS", project.documents))
        stack.enter_context(patch.object(preview, "FILE_HISTORY_PATH", self.root / "notes/file_history.json"))
        roots = tuple(str(folder.relative_to(self.root)) for name in project.documents
                      for folder in project.document_roots(name))
        stack.enter_context(patch.object(preview, "SOURCE_ROOTS", roots))

    def test_settings_reject_invalid_choices_and_paths(self):
        for key, value in (("title_page", "blind"), ("declarations", "append"),
                           ("declarations", []), ("output", ""), ("journal", 12),
                           ("guidelines", False), ("unknown", "")):
            with self.subTest(key=key, value=value), self.assertRaises(ConfigError):
                self.project({"submission": {key: value}})

    def test_virtual_documents_placement_order_and_user_ownership(self):
        for placement in ("end", "separate", "form"):
            with self.subTest(placement=placement):
                project = self.project({"submission": {"declarations": placement, "title_page": "separate"}})
                expected = ["manuscript", "supplementary"]
                if placement != "end":
                    expected.append("declarations")
                    self.assertEqual(bool(project.document_docx("declarations")), placement == "separate")
                self.assertEqual(list(project.documents), expected + ["title_page", "cover_letter"])
                # The preview's Submission menu lists them, in this order.
                self.assertEqual(project.submission_parts, expected[2:] + ["title_page", "cover_letter"])
        project = self.project({"kind": "report", "submission": {"title_page": "separate", "declarations": "separate"}})
        self.assertEqual(list(project.documents), ["manuscript", "supplementary"])
        report = self.project({"kind": "report", "documents": {"cover_letter": {"path": "manuscript/cover-letter"}}})
        self.assertEqual((list(report.documents)[-1], report.submission_parts), ("cover_letter", []))
        project = self.project({"documents": {"paper": {"path": "manuscript/main"},
            "declarations": {"path": "custom", "title": "My statements"}},
            "submission": {"declarations": "separate"}})
        self.assertEqual(project.documents["declarations"], "custom")
        self.assertEqual(project.titles["declarations"], "My statements")
        self.assertNotIn("declarations", project.submission_documents)
        shutil.rmtree(self.root / "manuscript/declarations")
        shutil.rmtree(self.root / "manuscript/cover-letter")
        self.assertEqual(list(self.project({"submission": {"declarations": "separate"}}).documents),
                         ["manuscript", "supplementary"])

    def test_appended_sources_are_editable_locatable_and_watched(self):
        project = self.project()
        self.bind(project)
        files = preview.doc_files("manuscript")
        self.assertEqual([Path(entry["file"]).name for entry in files], ["01.md", "02.md", "01.md", "02.md"])
        self.assertEqual(preview.source_path(files[2]["file"])[0], self.declaration)
        located = preview.locate_source({"file": files[0]["file"], "text": "Unique ethics paragraph"})
        self.assertEqual(located["file"], files[2]["file"])
        self.assertEqual(located["start_line"], 5)
        self.assertEqual(preview.changed_documents([self.declaration]), ["manuscript"])
        self.assertIn(self.declaration, dependencies.snapshot(watch=[]))
        state = preview.get_source(files[2]["file"])
        updated = state["text"].replace("Unique ethics paragraph", "Author-confirmed ethics paragraph")
        preview.change_source({"file": state["file"], "text": updated, "base_sha256": state["sha256"]})
        self.assertEqual(self.declaration.read_text(), updated)

    def test_form_only_sections_respect_code_fences_and_heading_levels(self):
        text = "# Kept\n\n```md\n## Literal {.form-only}\n```\n\n" \
               "## Hidden {.form-only}\n\nSecret.\n\n### Child\n\nNested.\n\n## Next\n\nVisible.\n"
        word = expand.form_sections(text)
        self.assertIn("## Literal {.form-only}", word)
        self.assertNotIn("Secret", word)
        self.assertNotIn("Nested", word)
        self.assertIn("Visible", word)
        marked = expand.form_sections(text, mark=True)
        self.assertIn("Secret", marked)
        self.assertEqual(marked.count("Form only – not in the Word file"), 1)
        for boundary in ("#", "##", "## "):
            with self.subTest(boundary=boundary):
                source = f"## Hidden {{.form-only}}\n\nSecret.\n\n{boundary}\n\nVisible.\n"
                self.assertIn("Visible.", expand.form_sections(source))
                self.assertNotIn("Secret.", expand.form_sections(source))

    @unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
    def test_end_expansion_citations_counts_and_reference_order(self):
        project = self.project()
        self.bind(project)
        source = expand.expand_files(sorted((self.root / "manuscript/main").glob("*.md")), mark=True)
        self.assertLess(source.index("# Conclusion"), source.index("# Declarations"))
        self.assertLess(source.index("# Declarations"), source.index("## Code"))
        result = subprocess.run(["pandoc", "-f", "markdown", "-t", "html", "--section-divs",
            "--citeproc", f"--bibliography={project.path('bibliography')}",
            f"--csl={project.path('csl')}", "-M", "reference-section-title=References"],
            input=source, text=True, capture_output=True, check=True).stdout
        self.assertEqual(result.count('id="refs"'), 1)
        self.assertLess(result.index("Repository scripts"), result.index('id="refs"'))
        self.assertIn("[1]", result)
        self.assertIn("[2]", result)
        found = counts.count_page(result, DEFAULTS["counts"]["exclude"])
        self.assertEqual(found["words"], 5)
        self.assertEqual(found["references"], 2)

    @unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
    def test_form_only_counts_do_not_depend_on_heading_names(self):
        source = "# Body\n\nThree ordinary words.\n\n## Administrative {.form-only}\n\nHidden words.\n\n" \
                 "##### Nested\n\nHidden detail.\n\n###### More\n\nMore hidden words.\n\n" \
                 "## Continuation\n\nThree more words.\n"
        html = subprocess.run(["pandoc", "-f", "markdown", "-t", "html", "--section-divs"],
            input=expand.form_sections(source, mark=True), text=True, capture_output=True, check=True).stdout
        found = counts.count_page(html, [])
        self.assertEqual(found["words"], 6)
        self.assertEqual(found["left_out"], [("Administrative", 7)])

    @unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
    def test_failed_submission_preserves_existing_word_file(self):
        project = self.project()
        self.bind(project)
        target = project.document_docx("cover_letter")
        target.parent.mkdir(parents=True)
        target.write_bytes(b"Previous deliverable")
        project.path("bibliography").unlink()
        with self.assertRaisesRegex(Exception, "references.bib"):
            _support.docx(project, "cover_letter")
        self.assertEqual(target.read_bytes(), b"Previous deliverable")
        self.assertEqual(list(target.parent.iterdir()), [target])

    @unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
    def test_separate_word_files_and_frontmatter_rules(self):
        project = self.project({"submission": {"title_page": "separate", "declarations": "separate"}})
        self.bind(project)
        metadata = project.path("metadata")
        content = json.loads(metadata.read_text())
        content.update(abstract="Abstract sentinel not for submission attachments",
                       subtitle="Subtitle sentinel", keywords=["Keyword sentinel"])
        metadata.write_text(json.dumps(content))
        self.write("manuscript/title-page/addition.md", "Additional title-page note.\n")
        self.assertNotIn("Ana Pérez", expand.expand_files(project.document_files("manuscript")))
        self.assertNotIn("pk-front", expand.expand_files(project.document_files("supplementary"), document="supplementary"))
        _support.docx(project)
        documents = {}
        for name in ("title_page", "declarations", "cover_letter"):
            with zipfile.ZipFile(project.document_docx(name)) as archive:
                xml = ET.fromstring(archive.read("word/document.xml"))
            documents[name] = " ".join(xml.itertext())
        title = documents["title_page"]
        for expected in ("Unique paper title", "Ana Pérez", "University", "ana@example.org",
                         "contributed equally", "ORCID:", "0000-0002-1825-0097", "Additional title-page note",
                         "Publication date sentinel"):
            self.assertIn(expected, title)
        with zipfile.ZipFile(project.document_docx("title_page")) as archive:
            styles = archive.read("word/styles.xml").decode()
        self.assertIn('w:styleId="Affiliation"', styles)
        declaration = documents["declarations"]
        self.assertIn("Declarations", declaration)
        self.assertIn("Second source", declaration)
        self.assertNotIn("First source", declaration)
        self.assertNotIn("Secret form text", declaration)
        self.assertNotIn("Hidden detail", declaration)
        self.assertNotIn("Publication date sentinel", declaration)
        self.assertIn("Dear Editor", documents["cover_letter"])
        self.assertNotIn("Unique paper title", documents["cover_letter"])
        self.assertNotIn("Ana Pérez", documents["cover_letter"])
        self.assertIn("Subtitle sentinel", title)
        for name, text in documents.items():
            with self.subTest(document=name):
                self.assertNotIn("Abstract sentinel", text)
                self.assertNotIn("Keyword sentinel", text)
                if name != "title_page":
                    self.assertNotIn("Subtitle sentinel", text)

    @unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
    def test_authors_option_puts_a_separate_title_page_in_the_manuscript_too(self):
        # A copy for coauthors: the journal still gets the authors on the title page.
        from trazo import build_graph
        project = self.project({"submission": {"title_page": "separate"}})
        self.bind(project)
        output = project.document_docx("manuscript")

        def manuscript(authors):
            build_graph.Builder(project, authors=authors).pandoc_docx("manuscript")
            with zipfile.ZipFile(output) as archive:
                return " ".join(ET.fromstring(archive.read("word/document.xml")).itertext())

        for authors in (True, False, True):
            with self.subTest(authors=authors):
                text = manuscript(authors)
                self.assertIn("Unique paper title", text)
                self.assertEqual("Ana Pérez" in text, authors)
                self.assertEqual("University" in text, authors)
        build_graph.Builder(project, submission=True, authors=True).pandoc_docx("title_page")
        with zipfile.ZipFile(project.document_docx("title_page")) as archive:
            self.assertIn("Ana Pérez", " ".join(ET.fromstring(archive.read("word/document.xml")).itertext()))

    @unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
    def test_submission_removes_only_inactive_known_outputs(self):
        project = self.project({"submission": {"output": "deliverables", "title_page": "separate",
                                                "declarations": "separate"}})
        self.bind(project)
        _support.docx(project)
        output = self.root / "deliverables"
        untouched = {"manuscript.docx": b"Main paper", "notes.txt": b"Notes",
                     "custom.docx": b"Other Word file"}
        for name, content in untouched.items():
            (output / name).write_bytes(content)
        for placement in ("end", "form"):
            with self.subTest(placement=placement):
                (output / "declarations.docx").write_bytes(b"Old declarations")
                project = self.project({"submission": {"output": "deliverables", "declarations": placement}})
                self.bind(project)
                messages = io.StringIO()
                with redirect_stdout(messages):
                    _support.docx(project)
                self.assertIn("Removed deliverables/declarations.docx", messages.getvalue())
                self.assertFalse((output / "declarations.docx").exists())
                self.assertFalse((output / "title_page.docx").exists())
                self.assertTrue((output / "cover_letter.docx").is_file())
        shutil.rmtree(self.root / "manuscript/cover-letter")
        project = self.project({"submission": {"output": "deliverables"}})
        self.bind(project)
        messages = io.StringIO()
        with redirect_stdout(messages):
            _support.docx(project)
        self.assertIn("Removed deliverables/cover_letter.docx", messages.getvalue())
        self.assertEqual({path.name: path.read_bytes() for path in output.iterdir()}, untouched)
