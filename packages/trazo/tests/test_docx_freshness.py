"""Word files out of date as `trazo build --submission` decides (the
submission check's "Files to upload")."""

import os
import shutil
import unittest

import _support
from _submission_project import COUNTS, SubmissionProject

from trazo import submission_check


@unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
class WordFilesFreshness(SubmissionProject, unittest.TestCase):
    def files(self):
        return self.groups(submission_check.report(lambda: dict(COUNTS)))["files"]

    def test_configuration_and_metadata_changes_stale_every_word_file(self):
        project = self.bind(self.project({"submission": {"title_page": "separate", "declarations": "separate"}}))
        settings = self.write("trazo.toml", '[submission]\ntitle_page = "separate"\n')
        for path in (self.root / "manuscript").rglob("*.md"):
            path.write_text(path.read_text().replace("{{v:n}}", "42"))
        _support.docx(project, *project.documents)
        self.assertTrue(all(item["status"] == "ok" for item in self.files()))
        for path in (settings, project.path("metadata")):
            with self.subTest(path=path.name):
                # Saved again unchanged: `build --submission` has nothing to do, so neither has the check.
                newest = max(project.document_docx(name).stat().st_mtime_ns for name in project.documents)
                os.utime(path, ns=(newest + 1_000_000_000, newest + 1_000_000_000))
                self.assertTrue(all(item["status"] == "ok" for item in self.files()), self.files())
                original = path.read_text()
                path.write_text(original + "\n")
                items = self.files()
                self.assertTrue(all(item["status"] == "warn" for item in items), items)
                self.assertTrue(all(item["detail"].startswith("Changed since the last build") for item in items),
                                items)
                path.write_text(original)


if __name__ == "__main__":
    unittest.main()
