"""The review of pending edits in the preview: baseline, accept, reject and the author's saves."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _support

from trazo import preview, preview_edit_review, preview_watch, table

INTRO = "manuscript/main/01_introduction.md"


class EditReviewTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        for target, name, value in (
                (preview, "EDITS_PATH", root / "edits.json"),
                (preview, "FILE_HISTORY_PATH", root / "file_history.json"),
                (preview, "WATCHER", {"synced": 0.0, "rebuild": set(), "outputs": []})):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.path = _support.ROOT / INTRO
        original = self.path.read_bytes()
        self.addCleanup(self.path.write_bytes, original)

    def agent_edits(self, old, new):
        text = self.path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        self.path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def pending(self):
        return preview.edits_payload("manuscript")["edits"]

    def test_the_first_review_takes_the_files_as_they_are(self):
        self.assertEqual(self.pending(), [])
        self.agent_edits("We studied", "We followed")
        [edit] = self.pending()
        self.assertEqual((edit["file"], edit["old"], edit["new"], edit["line"]), (INTRO, "studied", "followed", 3))

    def test_accepting_keeps_the_file_and_rebuilds_the_page(self):
        self.pending()
        self.agent_edits("We studied", "We followed")
        text = self.path.read_text(encoding="utf-8")
        [edit] = self.pending()
        self.assertEqual(preview.change_edits({"op": "accept", "edits": [edit]}),
                         {"done": 1, "missing": 0, "missed": []})
        self.assertEqual(self.path.read_text(encoding="utf-8"), text)
        self.assertEqual(self.pending(), [])
        self.assertEqual(preview.WATCHER["rebuild"], {"manuscript"})

    def test_rejecting_restores_the_text_and_undo_save_brings_the_edit_back(self):
        self.pending()
        original = self.path.read_text(encoding="utf-8")
        self.agent_edits("We studied", "We followed")
        [edit] = self.pending()
        self.assertEqual(preview.change_edits({"op": "reject", "edits": [edit]}),
                         {"done": 1, "missing": 0, "missed": []})
        self.assertEqual(self.path.read_text(encoding="utf-8"), original)
        self.assertEqual(self.pending(), [])
        preview.change_source({"op": "undo", "file": INTRO})
        self.assertEqual([(e["old"], e["new"]) for e in self.pending()], [("studied", "followed")])

    def test_the_authors_save_is_accepted_but_not_the_agents_edit_around_it(self):
        self.pending()
        self.agent_edits("We studied", "We followed")
        source = preview.get_source(INTRO)
        text = source["text"].replace("# Introduction", "# Background")
        preview.change_source({"file": INTRO, "text": text, "base_sha256": source["sha256"]})
        self.assertEqual([(e["old"], e["new"]) for e in self.pending()], [("studied", "followed")])
        # Undoing the author's save is the author's change too.
        preview.change_source({"op": "undo", "file": INTRO})
        self.assertEqual([(e["old"], e["new"]) for e in self.pending()], [("studied", "followed")])

    def test_a_file_with_windows_line_endings_is_reviewed_exactly(self):
        lf = self.path.read_bytes().replace(b"\r\n", b"\n")
        crlf = lf.replace(b"\n", b"\r\n")
        self.path.write_bytes(crlf)
        self.pending()
        self.path.write_bytes(crlf.replace(b"We studied", b"We followed").replace(
            b"[@smith2020].\r\n", b"[@smith2020].\r\n\r\nA new paragraph.\r\n"))
        edits = self.pending()
        self.assertEqual([e["new"].strip() for e in edits], ["followed", "A new paragraph."])
        self.assertIn("pv-new", preview_edit_review.reader()[0](self.path))
        self.assertEqual(preview.change_edits({"op": "reject", "edits": edits})["done"], 2)
        self.assertEqual(self.path.read_bytes(), crlf)

    def test_a_correction_in_a_new_file_leaves_the_rest_of_it_pending(self):
        self.pending()
        path = _support.ROOT / "manuscript/main/02_new.md"
        path.write_text("# New\n\nFirst paragraph here.\n\nSecond paragraph here.\n", encoding="utf-8")
        self.addCleanup(path.unlink, missing_ok=True)
        name = "manuscript/main/02_new.md"
        source = preview.get_source(name)
        preview.change_source({"file": name, "text": source["text"].replace("First", "Opening"),
                               "base_sha256": source["sha256"]})
        pending = " ".join(e["new"] for e in self.pending() if e["file"] == name)
        self.assertIn("Second paragraph here.", pending)
        # Only the author's word went into what was reviewed.
        self.assertEqual(json.loads(preview.EDITS_PATH.read_text())["files"][name], "Opening")

    def test_edits_that_changed_since_the_page_was_built_are_left_as_they_are(self):
        self.pending()
        self.agent_edits("We studied", "We followed")
        [edit] = self.pending()
        self.agent_edits("We followed", "We tracked")
        self.assertEqual(preview.change_edits({"op": "reject", "edits": [edit]}),
                         {"done": 0, "missing": 1, "missed": [{"file": INTRO, "id": edit["id"]}]})
        self.assertIn("We tracked", self.path.read_text(encoding="utf-8"))
        for request in ({"op": "keep", "edits": [edit]}, {"op": "accept", "edits": []},
                        {"op": "accept", "edits": [{"file": "../x.md", "id": "a"}]}):
            with self.subTest(request=request), self.assertRaises(preview.CommentError):
                preview.change_edits(request)

    @unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
    def test_the_page_marks_the_pending_edits_and_when_they_were_first_seen(self):
        self.pending()
        self.agent_edits("We studied", "We followed")
        [edit] = self.pending()
        self.assertTrue(preview.build("manuscript"), preview.STATE["manuscript"]["error"])
        page = " ".join((preview.OUT_DIR / "manuscript.html").read_text(encoding="utf-8").split())
        self.assertIn(f'<span class="pv-edit-ins" data-edit="{edit["id"]}">followed</span>', page)
        self.assertIn(f'<span class="pv-edit-del" data-edit="{edit["id"]}">studied</span>', page)
        [seen] = self.pending()
        self.assertRegex(seen["seen"], r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")
        self.assertEqual(json.loads(preview.EDITS_PATH.read_text())["seen"][INTRO], {edit["id"]: seen["seen"]})

    def test_a_spec_tables_output_edited_by_hand_is_noted(self):
        path = table.output_path("cohorts")
        table.render_one("cohorts")
        self.addCleanup(path.unlink, missing_ok=True)
        with patch.object(preview_watch, "own_tables_stage", return_value=False):
            preview_edit_review.check_outputs()
            self.assertEqual(preview.WATCHER["outputs"], [])
            path.write_text(path.read_text(encoding="utf-8") + "\nAdded by hand.\n", encoding="utf-8")
            preview_edit_review.check_outputs()
            self.assertEqual(preview.WATCHER["outputs"], ["tables/output/md/cohorts.md"])
            preview.WATCHER["outputs"] = []
            path.write_bytes(path.read_bytes() + "Café, saved as Latin-1.\n".encode("latin-1"))
            preview_edit_review.check_outputs()  # not what the spec makes either: the next render replaces it
            self.assertEqual(preview.WATCHER["outputs"], ["tables/output/md/cohorts.md"])
