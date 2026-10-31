"""trazo submission check: the report, its form fields, the CLI and GET /__submission."""

import io
import json
import os
import shutil
import subprocess
import threading
import unittest
from contextlib import ExitStack, redirect_stdout
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import _support
from _submission_project import COUNTS, SubmissionProject

from trazo import (
    counts,
    frontmatter,
    preview,
    preview_export,
    server,
    submission,
    submission_check,
    submission_fields,
)
from trazo.local_server import LocalServer
from trazo.project import DEFAULTS


@unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
class SubmissionCheckTests(SubmissionProject, unittest.TestCase):
    def test_report_groups_statuses_and_places(self):
        # A command builds the supplementary's Word file: compared by time (test_docx_freshness).
        documents = {**DEFAULTS["documents"], "supplementary": {**DEFAULTS["documents"]["supplementary"],
                                                                "build": ["true"]}}
        self.bind(self.project({"limits": {"words": 100, "abstract": 50, "keywords": 2}, "documents": documents}))
        output = self.root / "output"
        output.mkdir()
        (output / "supplementary.docx").write_bytes(b"old")
        os.utime(output / "supplementary.docx", (1, 1))
        (output / "cover_letter.docx").write_bytes(b"current")
        result = submission_check.report(lambda: dict(COUNTS))
        self.assertEqual((result["kind"], result["errors"], result["warnings"]), ("article", 10, 5))
        self.assertNotIn("fields", result)
        groups = self.groups(result)
        self.assertEqual([group["label"] for group in result["groups"]], ["Journal", "Limits", "Metadata",
                         "Declarations", "Placeholders", "Files to upload", "Abbreviations", "Cross-references"])
        rows = lambda name: [(item["label"], item["status"], item["value"], item["detail"])  # noqa: E731
                             for item in groups[name]]
        self.assertEqual([item["status"] for item in groups["journal"]], ["warn", "warn", "info", "info"])
        self.assertEqual(result["placements"],
                         "Title page in the manuscript · Declarations at the end of the manuscript")
        self.assertEqual([item["detail"] for item in groups["journal"][2:]], ["Set in trazo.toml [submission]"] * 2)
        self.assertFalse(any("doc" in item for item in groups["journal"]))  # settings open nothing
        limit, unset, edit = " · Limit set in trazo.toml [limits]", "No limit set in trazo.toml [limits]", \
            "Edit manuscript/metadata.yaml"
        self.assertEqual(rows("limits"), [
            ("Main text", "error", "120 / 100 words", "20 over the limit" + limit),
            ("Abstract", "ok", "40 / 50 words", "10 left" + limit),
            ("Figures", "info", "2", unset),
            ("Tables", "info", "1", unset),
            ("References", "info", "2", unset),
            ("Keywords", "error", "3 / 2", f"1 over the limit{limit} · {edit}")])
        # Each limit opens what it counts: the abstract's section and line, the
        # Figures & Tables view, the references, the manuscript's keywords.
        place = lambda item: {key: item[key] for key in (  # noqa: E731
            "doc", "file", "line", "edit", "hash", "section", "block", "view") if key in item}
        self.assertEqual([place(item) for item in groups["limits"]], [
            {"doc": "manuscript"},
            {"doc": "manuscript", "file": "manuscript/main/00_abstract.md", "line": 1, "edit": True, "section": "Abstract"},
            {"doc": "manuscript", "view": "figures"}, {"doc": "manuscript", "view": "figures"},
            {"doc": "manuscript", "hash": "refs"},
            {"doc": "manuscript", "file": "manuscript/metadata.yaml", "block": "keywords"}])
        self.assertEqual(rows("metadata"), [
            ("Authors", "ok", "Ana Pérez; Bo Li", edit),
            ("Affiliations", "warn", "Missing for Bo Li", edit),
            ("Corresponding author", "ok", "Ana Pérez <ana@example.org>", edit),
            ("ORCID", "info", "Missing for Bo Li", edit),
            ("Keywords", "ok", "alpha; beta; gamma", edit)])
        # Metadata rows open the title block or keywords.
        self.assertEqual([place(item) for item in groups["metadata"]],
                         [{"doc": "manuscript", "file": "manuscript/metadata.yaml", "block": "front"}] * 4
                         + [{"doc": "manuscript", "file": "manuscript/metadata.yaml", "block": "keywords"}])
        self.assertEqual(rows("declarations"), [
            ("Ethics", "ok", "", "Filled"),
            ("Consent for publication", "ok", "", "Not applicable"),
            ("Funding", "info", "", "Type into the journal's form"),
            ("Data", "error", "[PLACEHOLDER: repository]", "Contains a placeholder"),
            ("Code", "error", "", "Empty")])
        ethics = groups["declarations"][0]
        self.assertEqual({key: ethics[key] for key in ("file", "line", "doc", "hash", "edit")},
                         {"file": "manuscript/declarations/declarations.md", "line": 3, "doc": "manuscript",
                          "hash": "decl-ethics", "edit": True})
        # Across lines, never in code, the declarations' own only once.
        self.assertEqual(rows("placeholders"), [
            ("manuscript/main/01_intro.md:3", "error", "[PLACEHOLDER: a long instruction across lines]", ""),
            ("manuscript/main/01_intro.md:13", "error", "[TODO]", ""),
            ("manuscript/supplementary/S1.md:3", "error", "[TBD: table S1]", ""),
            ("manuscript/cover-letter/letter.md:3", "error", "[PLACEHOLDER: the main finding]", ""),
            ("manuscript/metadata.yaml:10", "error", "[TODO: Bo Li's affiliation]", edit)])
        first, letter, metadata = (groups["placeholders"][index] for index in (0, 3, 4))
        self.assertEqual((first["doc"], first["line"], first["end_line"], first["edit"], first["section"]),
                         ("manuscript", 3, 5, True, "Introduction"))
        self.assertEqual((letter["doc"], letter["edit"], letter.get("section")), ("cover_letter", True, None))
        self.assertEqual((metadata["doc"], metadata.get("edit"), metadata["block"]), ("manuscript", None, "front"))
        files, fix = rows("files"), ": run scripts/trazo build --submission"
        self.assertEqual(files[0], ("Manuscript", "error", "output/manuscript.docx", "Missing" + fix))
        self.assertEqual([row[:3] for row in files[1:]], [
            ("Supplementary", "warn", "output/supplementary.docx"), ("Cover letter", "warn", "output/cover_letter.docx")])
        self.assertRegex(files[1][3].removesuffix(fix), r"^Older than \S+$")
        # Written by hand, so trazo's build has no record of it.
        self.assertEqual(files[2][3], "Changed since the last build" + fix)
        self.assertEqual(groups["files"][1]["doc"], "supplementary")

    def test_placeholders_sections_and_counts_failures(self):
        text = ("A [PLACEHOLDER: one\ntwo [@cite] three] b `[TODO: no]` c ``x ` [TBD: no] ``\n\n"
                "```\n[TBD: no]\n# Not a heading\n```\n\n~~~~\n[TODO: no]\n~~~~\n\n[TBD] [todo: lower] [TODOS]\n")
        self.assertEqual([(line, last, excerpt) for _, line, last, excerpt in submission_check.placeholders(text)],
                         [(1, 2, "[PLACEHOLDER: one two [@cite] three]"), (13, 13, "[TBD]")])
        self.assertEqual(list(submission_check.placeholders("[TODO: never closed")), [])
        found = submission_check.sections("# One {#one .x}\n\nText.\n\n```\n## Code\n```\n\n## Two ##\n\nMore.\n\n# Three\n")
        self.assertEqual([(s["heading"], s["id"], s["classes"], s["level"], s["line"]) for s in found],
                         [("One", "one", ["x"], 1, 1), ("Two", "", [], 2, 9), ("Three", "", [], 1, 13)])
        self.assertEqual(found[0]["end"], found[2]["start"])
        # A placeholder's section: the innermost one, by id, else by heading.
        text = "# One {#one}\n\nA.\n\n## Two\n\nB.\n\n# Three\n"
        self.assertEqual([submission_check.section_place(submission_check.section_at(text, text.index(mark)))
                          for mark in ("A.", "B.", "Three")],
                         [{"hash": "one"}, {"section": "Two"}, {"section": "Three"}])
        self.assertEqual(submission_check.section_place(submission_check.section_at("Text.\n# One\n", 0)), {})
        self.bind(self.project({"limits": {"words": 100}}))

        def failing():
            raise RuntimeError("pandoc exited with 64")
        groups = self.groups(submission_check.report(failing))
        self.assertEqual([(item["label"], item["status"]) for item in groups["limits"]],
                         [("Counts", "error"), ("Keywords", "info")])
        self.assertIn("pandoc exited with 64", groups["limits"][0]["detail"])

    def test_form_placement_reports_and_missing_declarations(self):
        self.bind(self.project({"submission": {"declarations": "form", "journal": "J",
                                               "guidelines": "https://example.org/authors"}}))
        groups = self.groups(submission_check.report(lambda: dict(COUNTS)))
        self.assertEqual([item["status"] for item in groups["journal"]], ["ok", "ok", "info", "info"])
        self.assertEqual(groups["journal"][1]["url"], "https://example.org/authors")
        self.assertEqual(groups["journal"][3]["detail"],
                         "Not in any Word file: type them into the journal's form · Set in trazo.toml [submission]")
        # A separate title page shows the authors; keywords stay in the manuscript.
        self.bind(self.project({"submission": {"title_page": "separate"}}))
        groups = self.groups(submission_check.report(lambda: dict(COUNTS)))
        self.assertEqual([(item["label"], item["doc"], item["block"]) for item in groups["metadata"]], [
            ("Authors", "title_page", "front"), ("Affiliations", "title_page", "front"),
            ("Corresponding author", "title_page", "front"), ("ORCID", "title_page", "front"),
            ("Keywords", "manuscript", "keywords")])
        self.assertEqual(groups["placeholders"][-1]["doc"], "title_page")
        self.bind(self.project({"submission": {"declarations": "form", "journal": "J",
                                               "guidelines": "https://example.org/authors"}}))
        groups = self.groups(submission_check.report(lambda: dict(COUNTS)))
        self.assertEqual([(item["status"], item["value"], item["doc"]) for item in groups["declarations"]], [
            ("info", "", "declarations"), ("info", "Not applicable", "declarations"),
            ("info", "", "declarations"), ("error", "[PLACEHOLDER: repository]", "declarations"),
            ("error", "", "declarations")])
        shutil.rmtree(self.root / "manuscript/declarations")
        self.bind(self.project())
        groups = self.groups(submission_check.report(lambda: dict(COUNTS)))
        self.assertEqual([(item["label"], item["status"], item["value"]) for item in groups["declarations"]],
                         [("Declarations", "warn", "None found")])
        self.bind(self.project({"kind": "report"}))
        self.assertEqual(submission_check.report(lambda: dict(COUNTS), fields=True),
                         {"kind": "report", "groups": [], "errors": 0, "warnings": 0, "fields": []})

    def test_declaration_completeness_expands_includes_and_keeps_source_location(self):
        self.write("empty.md", "")
        self.write("manuscript/declarations/declarations.md",
                   "# Declarations\n\n## Ethics {#decl-ethics}\n\n!include empty.md\n")
        self.bind(self.project())
        item = self.groups(submission_check.report(lambda: dict(COUNTS)))["declarations"][0]
        self.assertEqual((item["status"], item["detail"]), ("error", "Empty"))
        self.assertEqual((item["file"], item["line"], item["hash"]),
                         ("manuscript/declarations/declarations.md", 3, "decl-ethics"))

    @unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
    def test_form_only_citations_do_not_change_word_fields_or_counts(self):
        self.write("manuscript/main/00_abstract.md", "# Abstract\n\nSummary.\n")
        self.write("manuscript/main/01_intro.md", "# Introduction\n\nOrdinary words [@first].\n")
        with (self.root / "bibliography/references.bib").open("a") as bibliography:
            bibliography.write("@article{third, author={Gamma, G}, title={Third source}, "
                               "journal={Journal}, year={2022}}\n")
        self.write("manuscript/declarations/declarations.md",
                   "# Declarations\n\n## Visible {#decl-visible}\n\nVisible [@second].\n")
        project = self.bind(self.project())
        _support.docx(project, "manuscript")
        baseline = subprocess.run(["pandoc", str(project.document_docx("manuscript")), "-t", "plain"],
                                  check=True, capture_output=True, text=True).stdout
        self.write("manuscript/declarations/declarations.md",
                   "# Declarations\n\n## Hidden {#decl-hidden .form-only}\n\nHidden [@second].\n\n"
                   "## Only in form {#decl-only .form-only}\n\nOnly in form [@third].\n\n"
                   "## Visible {#decl-visible}\n\nVisible [@second].\n")
        _support.docx(project, "manuscript")
        word = subprocess.run(["pandoc", str(project.document_docx("manuscript")), "-t", "plain"],
                              check=True, capture_output=True, text=True).stdout
        self.assertEqual(word, baseline)
        self.assertIn("Ordinary words [1].", word)
        self.assertIn("Visible [2].", word)
        fields = {field["label"]: field for field in submission_fields.form_fields(frontmatter.read_metadata())}
        self.assertEqual(fields["Visible"]["text"], "Visible [2].")
        self.assertEqual(fields["Hidden"]["text"], "Hidden [2].")
        self.assertEqual(fields["Only in form"]["text"], "Only in form [3].")
        self.assertTrue(fields["Hidden"]["form_only"])
        result = submission_check.report()
        references = next(item for item in self.groups(result)["limits"] if item["label"] == "References")
        self.assertEqual(references["value"], "2")
        folder = self.root / "preview"
        self.assertEqual(preview.render("manuscript", folder)[0], "")
        html = (folder / "manuscript.html").read_text()
        self.assertIn("Hidden", html)
        found = counts.page_counts("manuscript", html)
        self.assertEqual(found["references"], 2)
        self.assertIn("Hidden", [head["text"] for head in found["heads"]])

    @unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
    def test_abbreviation_markdown_citations_match_word_preview_fields_and_exports(self):
        import xml.etree.ElementTree as ET
        import zipfile

        from trazo import preview_diff
        self.write("data/values.json", json.dumps({"values": {"cohort.n": {"value": 120, "text": "120"}}}))
        self.write("manuscript/main/00_abbreviations.md",
                   "# Abbreviations {#abbreviations .uncounted}\n\nMRI\n:   *Imaging* $x^2$ {{v:cohort.n}} [@first]; see [@tbl:study]\n")
        self.write("manuscript/main/00_abstract.md", "# Abstract\n\nSummary [@second].\n")
        self.write("manuscript/main/01_intro.md", "# Introduction\n\nBody.\n\n"
                   "| Measure | Value |\n|---|---|\n| Result | 5 |\n\nTable: Study results. {#tbl:study}\n")
        self.write("manuscript/declarations/declarations.md", "")
        for style in ("table", "list", "inline"):
            with self.subTest(style=style):
                project = self.bind(self.project({"abbreviations": {"style": style}}))
                with patch.object(preview_export, "PROJECT", project):
                    _support.docx(project, "manuscript")
                    word = subprocess.run(["pandoc", str(project.document_docx("manuscript")), "-t", "plain"],
                                          check=True, capture_output=True, text=True).stdout
                    self.assertIn("Summary [2].", word)
                    self.assertIn("120 [1]", word)
                    self.assertRegex(word, r"see tbl\.\s+1")
                    self.assertRegex(word, r"Table\s+1[:.]")
                    self.assertNotRegex(word, r"Table\s+2[:.]")
                    folder = self.root / "preview"
                    self.assertEqual(preview.render("manuscript", folder)[0], "")
                    html = (folder / "manuscript.html").read_text()
                    self.assertIn("<em>Imaging</em>", html)
                    self.assertIn('<math', html)
                    self.assertIn('<span class="pv-val" data-key="cohort.n">120</span>', html)
                    self.assertRegex(html, r'see tbl\.\s*<a\s+href="#tbl:study">1</a>')
                    self.assertRegex(html, r'Summary .*?\[2\]')
                    found = counts.page_counts("manuscript", html)
                    self.assertEqual((found["references"], found["tables"]), (2, 1))
                    fields = {field["label"]: field for field in submission_fields.form_fields(frontmatter.read_metadata())}
                    self.assertEqual(fields["Abstract"]["text"], "Summary [2].")
                    self.assertIn("120 [1]", fields["Abbreviations"]["text"])
                    self.assertRegex(fields["Abbreviations"]["text"], r"see tbl\.\s+1")
                    from trazo import docx_indexes
                    data, _, _ = preview_export.export("manuscript", "section", "abbreviations", "docx")
                    with zipfile.ZipFile(io.BytesIO(data)) as archive:
                        document = ET.fromstring(archive.read("word/document.xml"))
                    self.assertIn("Imaging", "".join(document.itertext()))
                    ns = {"w": docx_indexes.W}
                    if style == "table":
                        self.assertEqual(document.find(".//w:tblPr/w:tblStyle", ns).get(f"{{{docx_indexes.W}}}val"), "Abbreviations")
                        self.assertIsNotNone(document.find(".//w:i", ns))
                        self.assertIn("oMath", ET.tostring(document).decode())
                    self.assertGreater(preview_diff.diff("MRI\n:   Old meaning\n", "MRI\n:   *Imaging*\n")["changed"], 0)

    def test_failed_form_only_build_keeps_serving_cold_cached_snapshot(self):
        self.write("manuscript/main/00_abstract.md", "# Abstract\n\nSummary.\n")
        self.write("manuscript/declarations/declarations.md", "# Hidden {.form-only}\n\nHidden [@second].\n")
        self.bind(self.project())
        folder = self.root / ".build/preview"
        with patch.object(preview, "OUT_DIR", folder), patch.object(preview, "STATE", {
                name: {"version": 0, "ok": False, "error": "", "warnings": [], "built": ""}
                for name in preview.DOCS}):
            self.assertTrue(preview.build("manuscript"))
            page = (folder / "manuscript.html").read_bytes()
            self.write("manuscript/main/01_intro.md", "# Introduction\n\n!include missing.md\n")
            self.assertFalse(preview.build("manuscript"))
            self.assertEqual((folder / "manuscript.html").read_bytes(), page)
            server._counts.cache_clear()
            app = LocalServer(("127.0.0.1", 0), preview.Handler)
            thread = threading.Thread(target=app.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.object(counts, "document_counts", side_effect=AssertionError("request rebuilt sources")):
                    response = urlopen(f"http://127.0.0.1:{app.server_port}/manuscript.html")
                    self.assertEqual(response.status, 200)
                    self.assertIn(b'window.PREVIEW_COUNTS', response.read())
                    result = json.load(urlopen(f"http://127.0.0.1:{app.server_port}/__submission?fields=0"))
                    self.assertIn("missing.md", self.groups(result)["limits"][0]["detail"])
            finally:
                app.shutdown()
                thread.join()
                app.server_close()

    def test_cli_output_exit_codes_and_json(self):
        self.bind(self.project())

        def run(*args):
            out = io.StringIO()
            with redirect_stdout(out), patch.object(counts, "document_counts", return_value=dict(COUNTS)):
                code = submission.main(["check", *args])
            return code, out.getvalue()
        code, text = run()
        self.assertEqual(code, 0)
        self.assertIn("Placeholders\n  ERROR  manuscript/main/01_intro.md:3: "
                      "[PLACEHOLDER: a long instruction across lines]\n", text)
        self.assertIn("  WARN   Journal: Not set — Set [submission] journal", text)
        self.assertIn("  info   Abstract: 40 words — No limit set in trazo.toml [limits]\n", text)
        self.assertTrue(text.endswith("\n10 errors, 3 warnings\n"), text[-80:])
        self.assertEqual(run("--strict")[0], 1)
        code, text = run("--json")
        self.assertEqual(code, 0)
        result = json.loads(text)
        self.assertEqual(result["errors"], 10)
        self.assertEqual(result["fields"][0]["label"], "Title")
        clean = {"kind": "article", "groups": [], "errors": 0, "warnings": 2}
        with patch.object(submission_check, "report", return_value=clean):
            self.assertEqual(run("--strict")[0], 0)
        self.bind(self.project({"kind": "report"}))
        code, text = run("--strict")
        self.assertEqual((code, text), (0, "Reports have no submission layer: nothing to check.\n"))

    def test_endpoint_reuses_the_served_page_counts(self):
        self.bind(self.project({"limits": {"words": 3}}))
        folder = self.root / ".build/preview"
        folder.mkdir(parents=True)
        page = folder / "manuscript.html"
        page.write_text('<!doctype html><html><head><title>x</title></head><body><section id="intro" '
                        'class="level1"><h1>Intro</h1><p>one two three four</p></section></body></html>')
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(preview, "OUT_DIR", folder))
        stack.enter_context(patch.object(preview, "STATE", {name: {"version": 1, "ok": False, "error": "",
                                                                   "warnings": [], "built": ""}
                                                            for name in preview.DOCS}))
        stack.enter_context(patch.object(counts, "document_counts", side_effect=AssertionError("rebuilt")))
        app = LocalServer(("127.0.0.1", 0), preview.Handler)
        self.addCleanup(app.server_close)
        thread = threading.Thread(target=app.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(app.shutdown)
        base = f"http://127.0.0.1:{app.server_port}"
        urlopen(base + "/manuscript.html").read()
        hits = server._counts.cache_info().hits
        result = json.load(urlopen(base + "/__submission?fields=0"))
        self.assertEqual(server._counts.cache_info().hits, hits + 1)
        self.assertNotIn("fields", result)
        words = self.groups(result)["limits"][0]
        self.assertEqual((words["status"], words["value"], words["detail"]),
                         ("error", "4 / 3 words", "1 over the limit · Limit set in trazo.toml [limits]"))
        with self.assertRaises(HTTPError) as refused:
            urlopen(Request(base + "/__submission", headers={"Origin": "http://example.org"}))
        self.assertEqual(refused.exception.code, 403)
        previous = page.read_bytes()
        self.write("manuscript/main/00_abstract.md", "# Abstract\n\n!include missing.md\n")
        self.assertFalse(preview.build("manuscript"))
        self.assertEqual(page.read_bytes(), previous)
        result = json.load(urlopen(base + "/__submission?fields=0"))
        self.assertEqual(self.groups(result)["limits"][0]["label"], "Counts")
        self.assertEqual(self.groups(result)["limits"][0]["status"], "error")
        self.assertIn("Build failed: counts may be out of date", self.groups(result)["limits"][0]["detail"])
        self.assertIn("missing.md", self.groups(result)["limits"][0]["detail"])
        page.unlink()
        result = json.load(urlopen(base + "/__submission?fields=0"))
        self.assertIn("missing.md", self.groups(result)["limits"][0]["detail"])
        self.bind(self.project({"kind": "report"}))
        self.assertEqual(json.load(urlopen(base + "/__submission"))["kind"], "report")


if __name__ == "__main__":
    unittest.main()
