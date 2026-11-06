"""What the @ menu offers, and the Markdown a saved chip becomes."""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _block_roundtrip as roundtrip
import _support

from trazo import preview, preview_mentions, preview_render
from trazo.local_server import RequestError

CHAPTER = "manuscript/main/02_mentions.md"
CHIP = ""


class ReferenceTests(unittest.TestCase):
    def test_the_menu_lists_the_bibliography_with_titles(self):
        self.assertEqual(preview_mentions.references(), {"references": [{"key": "smith2020", "text": "A study 2020"}]})


@unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
class SavedMentionTests(unittest.TestCase):
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
        self.chapter = _support.ROOT / CHAPTER
        self.addCleanup(self.chapter.unlink, missing_ok=True)

    def save(self, mention, text="Plain words.\n"):
        self.chapter.write_text(text, encoding="utf-8")
        self.assertEqual(preview_render.render("manuscript", self.folder / "page")[0], "")
        html = (self.folder / "page" / "manuscript.html").read_text(encoding="utf-8")
        for (file, key), node in roundtrip.page_blocks(html).items():
            if file == CHAPTER:
                before = roundtrip.squash(roundtrip.flat(node))
                after = before.replace("words", f"words {CHIP}")
                return preview.post_route("/__block", lambda: {
                    "file": file, "h": key, "before": before, "after": after, "mentions": {CHIP: mention}})()
        self.fail("no block in the chapter")

    def source(self):
        return self.chapter.read_text(encoding="utf-8")

    def test_each_kind_becomes_its_markdown(self):
        for mention, written in (({"kind": "reference", "key": "smith2020"}, "[@smith2020]"),
                                 ({"kind": "object", "key": "tbl:example"}, "[@tbl:example]"),
                                 ({"kind": "value", "key": "cohort.n"}, "{{v:cohort.n}}")):
            self.assertTrue(self.save(mention)["saved"])
            self.assertEqual(self.source(), f"Plain words {written}.\n")

    def test_what_does_not_exist_is_not_inserted(self):
        for mention in ({"kind": "reference", "key": "nobody2020"}, {"kind": "value", "key": "no.such"},
                        {"kind": "object", "key": "fig:no-such"}, {"kind": "other", "key": "smith2020"}, "smith2020"):
            with self.assertRaises(RequestError):
                self.save(mention)
            self.assertEqual(self.source(), "Plain words.\n")

    def test_a_character_outside_the_new_chips_is_refused(self):
        with self.assertRaises(RequestError):
            preview_mentions.sources({"mentions": {"a": {"kind": "reference", "key": "smith2020"}}})


if __name__ == "__main__":
    unittest.main()
