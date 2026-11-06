"""What the tests of saving a block share: a chapter built into a real page."""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _block_roundtrip as roundtrip
import _support

from trazo import preview, preview_render

CHAPTER = "manuscript/main/02_syntax.md"
INTRO = "manuscript/main/01_introduction.md"


@unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
class SaveCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        for name, value in (("EDITS_PATH", self.folder / "edits.json"),
                            ("FILE_HISTORY_PATH", self.folder / "file_history.json"),
                            ("WATCHER", {"synced": 0.0, "rebuild": set(), "outputs": []})):
            patcher = patch.object(preview, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.intro = _support.ROOT / INTRO
        self.addCleanup(self.intro.write_bytes, self.intro.read_bytes())
        self.chapter = _support.ROOT / CHAPTER
        self.addCleanup(self.chapter.unlink, missing_ok=True)

    def page(self, text=None):
        if text is not None:
            self.chapter.write_bytes(text.encode("utf-8"))
        error, _ = preview_render.render("manuscript", self.folder / "page")
        self.assertEqual(error, "")
        return (self.folder / "page" / "manuscript.html").read_text(encoding="utf-8")

    def block(self, text, words):
        """(key, rendered text) of the block of the chapter that holds `words`."""
        for (file, key), node in roundtrip.page_blocks(self.page(text)).items():
            rendered = roundtrip.squash(roundtrip.flat(node))
            if file == CHAPTER and words in rendered:
                return key, rendered
        self.fail(f"no block holds {words!r}")

    def save(self, key, before, after):
        return preview.post_route("/__block", lambda: {"file": CHAPTER, "h": key, "before": before, "after": after})()
