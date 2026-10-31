"""Preview: builds, the diff without git and the full DOCX build."""

import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import _support

from trazo import pandoc_run, preview


@unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
class BuildTests(unittest.TestCase):
    def test_source_manifest_recognizes_headings_after_longer_closing_fences(self):
        path = _support.ROOT / "manuscript/main/99_fenced.md"
        path.write_text("````markdown\n# Example heading\n```\n# Still code\n`````\n\n# Actual section\n")
        self.addCleanup(path.unlink, missing_ok=True)
        entry = next(item for item in preview.doc_files("manuscript") if item["file"].endswith("99_fenced.md"))
        self.assertEqual(entry["title"], "Actual section")

    def test_build_writes_both_documents(self):
        for name in preview.DOCS:
            with self.subTest(name=name):
                self.assertTrue(preview.build(name), preview.STATE[name]["error"])
                page = (preview.OUT_DIR / f"{name}.html").read_text(encoding="utf-8")
                self.assertIn("/__assets/preview/preview.js", page)
                self.assertIn("PREVIEW_TITLES", page)
                # The model comes first: preview.js reads window.PreviewModel.
                self.assertLess(page.index("/__assets/preview/preview-model.js"),
                                page.index("/__assets/preview/preview.js"))
        page = (preview.OUT_DIR / "manuscript.html").read_text(encoding="utf-8")
        self.assertIn('data-key="cohort.n"', page)

    def test_references_to_figures_and_tables_are_links(self):
        # The Figures & Tables view lists "Cited in" from these links.
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "cites.md"
            source.write_text("![Caption.](missing.png){#fig:a}\n\nSee [@fig:a].\n")
            out = Path(folder) / "cites.html"
            result = pandoc_run.run([str(source), *preview.PANDOC_FLAGS, "-o", str(out)],
                                    cwd=_support.ROOT, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            page = out.read_text(encoding="utf-8")
        self.assertIn('<figure id="fig:a">', page)
        self.assertIn('<a href="#fig:a">1</a>', page)

    def test_submission_report_is_reused_until_a_file_it_reads_or_a_build_changes(self):
        from trazo import submission_check
        source = _support.ROOT / "manuscript/main/01_introduction.md"
        text = source.read_text(encoding="utf-8")
        self.addCleanup(source.write_text, text, encoding="utf-8")
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        outside, other, link = folder / "shared.md", folder / "other.md", folder / "link.md"
        inside = _support.ROOT / "notes/inside.md"
        self.addCleanup(inside.unlink, missing_ok=True)
        outside.write_text("Shared text.\n", encoding="utf-8")
        other.write_text("Other text [TODO: through a link].\n", encoding="utf-8")
        inside.write_text("Inside text.\n", encoding="utf-8")
        link.symlink_to(outside)
        source.write_text(text + f"\n!include {link}\n", encoding="utf-8")

        def placeholders(report):
            return [item["value"] for group in report["groups"] if group["id"] == "placeholders"
                    for item in group["items"]]
        with patch.object(submission_check, "report", wraps=submission_check.report) as report:
            preview.submission_report(False)
            preview.submission_report(False)
            self.assertEqual(report.call_count, 1)
            outside.write_text("Shared text [TODO: from outside].\n", encoding="utf-8")
            self.assertIn("[TODO: from outside]", placeholders(preview.submission_report(False)))
            for target in (inside, other):  # a link outside, pointed into the project and out again
                link.unlink()
                link.symlink_to(target)
                preview.submission_report(False)
            self.assertIn("[TODO: through a link]", placeholders(preview.submission_report(False)))
            source.write_text(text + f"\n!include {link}\n[TODO: newly added]\n", encoding="utf-8")
            self.assertIn("[TODO: newly added]", placeholders(preview.submission_report(False)))
            version = preview.STATE["manuscript"]["version"]
            with patch.dict(preview.STATE["manuscript"], {"version": version + 1}):
                preview.submission_report(False)
            self.assertEqual(report.call_count, 6)


    def test_submission_fields_reuse_the_checks_and_one_label_index(self):
        from trazo import expand, preview_submission, submission_check
        with patch.object(preview_submission, "_REPORT", None), \
                patch.object(submission_check, "report", wraps=submission_check.report) as report, \
                patch.object(expand, "expand_files", wraps=expand.expand_files) as expanded:
            checks = preview.submission_report(False)
            full = preview.submission_report(True)
        self.assertEqual(report.call_count, 1)  # the Submission view adds the fields to the badge's checks
        self.assertTrue(full["fields"])
        self.assertEqual({**checks, "fields": full["fields"]}, full)
        # One expansion per document to find the inputs, one for numbering,
        # cited documents and Cross-references after their stamps.
        self.assertEqual(expanded.call_count, 2 * len(preview.DOCS))

    def test_submission_report_follows_a_configured_folder_outside_the_project(self):
        from trazo import submission_check
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (folder / "_shared.json").write_text("{}\n", encoding="utf-8")
        link = _support.ROOT / "notes/specs"  # [paths] table_specs, linked to a shared folder
        link.symlink_to(folder)
        self.addCleanup(link.unlink)
        paths = {**preview.PROJECT.config["paths"], "table_specs": "notes/specs"}
        with patch.dict(preview.PROJECT.config, {"paths": paths}), \
                patch.object(submission_check, "report", wraps=submission_check.report) as report:
            preview.submission_report(False)
            preview.submission_report(False)
            (folder / "_shared.json").write_text('{"formats": {}}\n', encoding="utf-8")
            preview.submission_report(False)
            self.assertEqual(report.call_count, 2)

    def test_an_input_changed_during_a_submission_check_is_checked_again(self):
        from trazo import preview_submission, submission_check
        source = _support.ROOT / "manuscript/main/01_introduction.md"
        text = source.read_text(encoding="utf-8")
        self.addCleanup(source.write_text, text, encoding="utf-8")
        outside = Path(self.enterContext(tempfile.TemporaryDirectory())) / "shared.md"
        outside.write_text("Shared [TODO: old].\n", encoding="utf-8")
        source.write_text(text + f"\n!include {outside}\n", encoding="utf-8")
        real = submission_check.report

        def edited_meanwhile(*args):
            result = real(*args)
            outside.write_text("Shared and edited.\n", encoding="utf-8")
            return result
        self.enterContext(patch.object(preview_submission, "_REPORT", None))
        with patch.object(submission_check, "report", side_effect=edited_meanwhile):
            preview.submission_report(False)
        placeholders = [item["value"] for group in preview.submission_report(False)["groups"]
                        if group["id"] == "placeholders" for item in group["items"]]
        self.assertNotIn("[TODO: old]", placeholders)

    def test_labels_read_before_the_stamps_are_not_reused_by_the_check(self):
        from trazo import preview_submission
        source = _support.ROOT / "manuscript/main/01_introduction.md"
        text = source.read_text(encoding="utf-8")
        self.addCleanup(source.write_text, text, encoding="utf-8")
        outside = Path(self.enterContext(tempfile.TemporaryDirectory())) / "shared.md"
        outside.write_text("Shared.\n", encoding="utf-8")
        source.write_text(text + f"\n!include {outside}\n", encoding="utf-8")
        real = preview_submission.read_inputs

        def edited_meanwhile():
            found = real()  # finding the inputs reads the label index
            outside.write_text("See [@fig:nowhere].\n", encoding="utf-8")
            return found
        self.enterContext(patch.object(preview_submission, "_REPORT", None))
        with patch.object(preview_submission, "read_inputs", side_effect=edited_meanwhile):
            report = preview.submission_report(False)
        crossrefs = next(group for group in report["groups"] if group["id"] == "cross-references")
        self.assertIn("fig:nowhere", [item["label"] for item in crossrefs["items"]])

    def test_a_fresh_submission_report_catches_what_the_cache_missed(self):
        from trazo import submission_check
        source = _support.ROOT / "manuscript/main/01_introduction.md"
        text = source.read_text(encoding="utf-8")
        self.addCleanup(source.write_text, text, encoding="utf-8")
        later = Path(self.enterContext(tempfile.TemporaryDirectory())) / "later.md"
        source.write_text(text + f"\n!include {later}\n", encoding="utf-8")  # missing for now

        def placeholders(report):
            return [item["value"] for group in report["groups"] if group["id"] == "placeholders"
                    for item in group["items"]]
        with patch.object(submission_check, "report", wraps=submission_check.report) as report:
            first = preview.submission_report(False)
            later.write_text("Later text [TODO: created later].\n", encoding="utf-8")
            self.assertEqual(preview.submission_report(False), first)  # the cache's known blind spot
            fresh = preview.submission_report(False, fresh=0)
            self.assertIn("[TODO: created later]", placeholders(fresh))
            self.assertGreater(fresh["checked"], first["checked"])
            self.assertEqual(preview.submission_report(False), fresh)  # kept for the next request
            preview.submission_report(False, fresh=60)  # another tab's minute check
            self.assertEqual(report.call_count, 2)

    def test_a_check_asked_for_while_one_runs_shares_it(self):
        from trazo import preview_submission, submission_check
        started, release, real = threading.Event(), threading.Event(), submission_check.report

        def slow(*args):
            started.set()
            release.wait(10)
            return real(*args)
        self.enterContext(patch.object(preview_submission, "_REPORT", None))
        with patch.object(submission_check, "report", side_effect=slow) as report:
            first = threading.Thread(target=preview.submission_report, args=(False,), kwargs={"fresh": 0})
            first.start()
            self.assertTrue(started.wait(10))
            second = threading.Thread(target=preview.submission_report, args=(False,), kwargs={"fresh": 60})
            second.start()
            time.sleep(0.2)  # the second waits for the lock
            release.set()
            first.join(10)
            second.join(10)
            self.assertEqual(report.call_count, 1)

if __name__ == "__main__":
    unittest.main()


class NoGitTests(unittest.TestCase):
    """Tests run in a copy of the fixture outside any git repository."""

    def test_the_diff_says_history_is_off(self):
        probe = subprocess.run(["git", "-C", str(_support.ROOT), "rev-parse"], capture_output=True)
        self.assertNotEqual(probe.returncode, 0, "the test project must not be in a git repository")
        with self.assertRaisesRegex(preview.GitError, "History is off: this folder is not a git repository"):
            preview.diff_source("manuscript/main/01_introduction.md")
        with patch.object(preview.subprocess, "run", side_effect=FileNotFoundError("git")), \
             self.assertRaisesRegex(preview.GitError, "History is off: git is not installed"):
            preview.diff_source("manuscript/main/01_introduction.md")


class DocxBuildTests(unittest.TestCase):
    def test_full_build_runs_the_requested_document_with_its_indexes(self):
        started = []
        with patch.object(preview.DOCX_JOB, "start", lambda command, cwd, timeout, initial, **details:
                          started.append((command, initial, details)) or True):
            preview.start_docx_build({"doc": "supplementary", "lot": True, "toc": False, "authors": True})
            preview.start_docx_build({})
        (command, initial, details), (default, _, first) = started
        self.assertEqual(command[-5:], ["build", "--doc", "supplementary", "--lot", "--authors"])
        self.assertEqual(details, {"doc": "supplementary", "shown": "trazo build --doc supplementary"})
        self.assertIn("$ trazo build --doc supplementary", initial[0])
        self.assertEqual(default[-3:], ["build", "--doc", "manuscript"])
        self.assertEqual(first["doc"], "manuscript")
        for request, message in (({"doc": "nope"}, "unknown document"), ({"lof": "yes"}, "booleans"),
                                 ({"authors": 1}, "booleans")):
            with self.subTest(request=request), self.assertRaisesRegex(preview.CommentError, message):
                preview.start_docx_build(request)
