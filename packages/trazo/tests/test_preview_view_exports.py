"""preview_export.save: where an MCP App view's export is kept, since a view
cannot download."""

import shutil
import tempfile
import unittest
from pathlib import Path

import _support

from trazo import preview_export

ROOT = Path(_support.ROOT)


class SaveTests(unittest.TestCase):
    def setUp(self):
        for folder in (ROOT / "output", ROOT / ".build" / "exports"):
            self.addCleanup(lambda f=folder: shutil.rmtree(f) if f.is_dir() and not f.is_symlink() else f.unlink(missing_ok=True))

    def test_beside_the_word_file_never_over_a_file(self):
        self.assertEqual(preview_export.save(b"one", "manuscript-sections.docx", "manuscript"),
                         "output/manuscript-sections.docx")
        self.assertEqual(preview_export.save(b"two", "manuscript-sections.docx", "manuscript"),
                         "output/manuscript-sections (2).docx")
        self.assertEqual((ROOT / "output" / "manuscript-sections.docx").read_bytes(), b"one")
        self.assertEqual((ROOT / "output" / "manuscript-sections (2).docx").read_bytes(), b"two")

    def test_a_link_in_the_way_is_not_followed(self):
        outside = Path(tempfile.mkdtemp(prefix="trazo-outside-"))
        self.addCleanup(shutil.rmtree, outside, True)
        (outside / "private.txt").write_bytes(b"keep")
        (ROOT / "output").mkdir(exist_ok=True)
        (ROOT / "output" / "manuscript-intro.docx").symlink_to(outside / "private.txt")
        self.assertEqual(preview_export.save(b"new", "manuscript-intro.docx", "manuscript"),
                         "output/manuscript-intro (2).docx")
        self.assertEqual((outside / "private.txt").read_bytes(), b"keep")

    def test_a_word_folder_outside_the_paper_is_not_used(self):
        outside = Path(tempfile.mkdtemp(prefix="trazo-outside-"))
        self.addCleanup(shutil.rmtree, outside, True)
        (ROOT / "output").symlink_to(outside, target_is_directory=True)
        self.assertEqual(preview_export.save(b"new", "manuscript-intro.docx", "manuscript"),
                         ".build/exports/manuscript-intro.docx")
        self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
