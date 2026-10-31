"""Metadata expansion through Pandoc, shared by Word and the preview."""

import http.client
import io
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)

from trazo import citations, expand, frontmatter, preview, preview_export
from trazo.local_server import LocalServer
from trazo.project import DEFAULTS, Project, merge


@unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
class FrontmatterTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.metadata = self.root / "metadata.yaml"
        self.source = self.root / "manuscript/main/section.md"
        self.source.parent.mkdir(parents=True)
        self.source.write_text("# Abstract\n\nSummary.\n\n# Introduction\n\nBody.\n")
        project = Project(self.root, merge(DEFAULTS, {"paths": {"metadata": "metadata.yaml"}}), True)
        for module in (frontmatter, expand, preview_export, preview):
            patcher = patch.object(module, "PROJECT", project)
            patcher.start()
            self.addCleanup(patcher.stop)

    def expanded(self, metadata, **kwargs):
        self.metadata.write_text(json.dumps(metadata, ensure_ascii=False))
        return expand.expand_files([self.source], **kwargs)

    def html(self, text):
        return subprocess.run(["pandoc", "-f", "markdown", "-t", "html5", "-s",
                               f"--metadata-file={self.metadata}"], input=text,
                              capture_output=True, text=True, check=True).stdout

    def test_structured_authors_order_numbering_and_links(self):
        metadata = {"author": [
            {"name": {"given": "Ana", "family": "Pérez"}, "email": "ana+work@example.org",
             "corresponding": True, "equal-contributor": True,
             "affiliations": [{"ref": "second"}, "first"]},
            {"name": "Luis", "corresponding": True, "email": "luis@example.org",
             "affiliations": ["first", {"name": "Inline Institute"}, "Named Institute"]},
            "Guest"], "affiliations": [{"id": "first", "name": "First Institute"},
                {"id": "second", "department": "Radiology", "name": "Hospital", "address": "Street",
                 "city": "Madrid", "region": "Region", "postal-code": "123", "country": "Spain"}],
            "date": "21 October 2026", "keywords": ["one", "two"]}
        text = self.expanded(metadata)
        self.assertTrue(text.startswith('---\nauthor: []\naffiliations: []\ndate: ""\n---'))
        self.assertIn(r"Ana Pérez^1,2,\*,†^, Luis^2,3,4,\*^, Guest", text)
        self.assertIn("^1^ Radiology, Hospital, Street, Madrid, Region, 123, Spain", text)
        self.assertIn("^2^ First Institute", text)
        self.assertIn("^3^ Inline Institute", text)
        self.assertIn("^4^ Named Institute", text)
        self.assertIn(r"\* Corresponding author: Ana Pérez (<ana+work@example.org>); Luis (<luis@example.org>)", text)
        self.assertLess(text.index("contributed equally"), text.index("21 October 2026"))
        self.assertLess(text.index('custom-style="Date"'), text.index("# Abstract"))
        html = self.html(text)
        self.assertIn('href="mailto:ana+work@example.org"', html)
        self.assertIn("<sup>1,2,*,†</sup>", html)
        with patch.object(citations, "PROJECT", expand.PROJECT):
            self.source.write_text("# Abstract\n\nSummary [@realkey].\n")
            self.assertEqual(citations.manuscript_keys(), {"realkey"})

    def test_single_shared_affiliation_omits_numbers(self):
        text = self.expanded({"author": [{"name": "A", "affiliations": ["one"]},
                                         {"name": "B", "affiliations": [{"ref": "one"}]}],
                              "affiliations": [{"id": "one", "name": "Hospital"}]})
        self.assertIn("\nA, B\n", text)
        self.assertIn("\nHospital\n", text)
        self.assertNotIn("^", text)

    def test_plain_authors_and_absent_metadata_are_unchanged(self):
        original = self.source.read_text()
        self.assertEqual(expand.expand_files([self.source]), original)
        self.assertEqual(self.expanded({"author": ["A", "B"], "date": "Today"}), original)

    def test_non_document_and_mixed_expansions_keep_original_content(self):
        self.expanded({"author": [{"name": "Document Author"}], "keywords": ["term"]})
        letter = self.root / "cover-letter.md"
        letter.write_text("# Abstract\n\nDear editor.\n")
        for paths in ([letter], [self.source, letter], iter([letter])):
            with self.subTest(paths=paths):
                paths = list(paths)
                expected = "\n\n".join(path.read_text().rstrip() for path in paths) + "\n"
                self.assertEqual(expand.expand_files(paths), expected)

    def test_keywords_follow_abstract_outside_code_fences(self):
        cases = [
            ("```\n# Abstract\n```\n\n# Introduction\n\nText.\n", None),
            ("# Summary\n\nText.\n", None),
            ("# ABSTRACT {.unnumbered}\n\nSummary.\n\n````\n# Fake\n```\n# Still fake\n````\n\n## Detail\n\nMore.\n\n# Next\n", "# Next"),
            ("# Abstract\n\nLast summary.\n", "end"),
        ]
        for source, next_heading in cases:
            with self.subTest(source=source):
                self.source.write_text(source)
                text = self.expanded({"keywords": ["alpha", "beta"], "keywords-title": "Key terms"})
                if next_heading is None:
                    self.assertEqual(text, source)
                else:
                    self.assertIn("**Key terms:** alpha; beta", text)
                    self.assertLess(text.index(source.rstrip().splitlines()[-1]) if next_heading == "end"
                                    else text.index("More."), text.index(".pk-keywords"))
                    if next_heading != "end":
                        self.assertLess(text.index(".pk-keywords"), text.index(next_heading))

    def test_escaping_date_and_value_marking_survive_pandoc(self):
        self.source.write_text("# Abstract\n\nSample {{v:count}}.\n")
        values = self.root / "values.json"
        values.write_text(json.dumps({"values": {"count": {"text": "12"}}}))
        title = 'A "quoted" title: 😀'
        text = self.expanded({"title": title, "author": [{"name": 'A "quoted" name: 😀'}],
                              "date": 'Today: "yes" 😀', "keywords": ["term"]},
                             values_path=values, mark=True)
        self.assertIn('[12]{.pv-val data-key="count"}', text)
        parsed = subprocess.run(["pandoc", "-t", "json", f"--metadata-file={self.metadata}"],
                                input=text, text=True, capture_output=True, check=True)
        metadata = json.loads(parsed.stdout)["meta"]
        self.assertEqual(metadata["author"]["c"], [])
        self.assertEqual(metadata["date"]["c"], [])
        html = self.html(text)
        self.assertIn("name: 😀", html)
        self.assertIn("Today:", html)
        self.assertEqual(html.count('custom-style="Date"'), 1)

    def test_unknown_ref_fails_cli_and_preview(self):
        self.source.write_text("# Introduction\n\nBody.\n")
        self.metadata.write_text('author: [{name: A, affiliations: [{ref: missing}]}]')
        with redirect_stderr(io.StringIO()) as error, redirect_stdout(io.StringIO()):
            self.assertEqual(expand.main([str(self.source)]), 1)
        self.assertIn("unknown affiliation ref: missing", error.getvalue())
        with patch.object(preview, "ROOT", self.root):
            error, _ = preview.render("manuscript", self.root / "preview")
        self.assertIn("unknown affiliation ref: missing", error)

    def test_invalid_metadata_returns_clear_export_http_errors_and_preview_errors(self):
        self.source.write_text("# Introduction\n\nBody.\n")
        with LocalServer(("127.0.0.1", 0), preview.Handler) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                for metadata, message in (
                        ('author: [{name: A, affiliations: [{ref: missing}]}]', "unknown affiliation ref: missing"),
                        ('author: [\n', "cannot read metadata")):
                    with self.subTest(metadata=metadata):
                        self.metadata.write_text(metadata)
                        connection = http.client.HTTPConnection(*server.server_address, timeout=10)
                        try:
                            connection.request("GET", "/__export?doc=manuscript&kind=section&id=introduction&format=docx")
                            response = connection.getresponse()
                            self.assertEqual(response.status, 400)
                            self.assertIn(message, json.loads(response.read())["error"])
                        finally:
                            connection.close()
                        with patch.object(preview, "ROOT", self.root):
                            error, _ = preview.render("manuscript", self.root / "preview")
                        self.assertIn(message, error)
            finally:
                server.shutdown()
                worker.join()

    def test_metadata_failure_names_file_and_edits_invalidate_cache(self):
        self.expanded({"author": [{"name": "Before"}]})
        self.metadata.write_text('author: [{name: After edit}]')
        self.assertIn("After edit", expand.expand_files([self.source]))
        self.metadata.write_text('author: [\n')
        with self.assertRaisesRegex(frontmatter.MetadataError, str(self.metadata)):
            expand.expand_files([self.source])
