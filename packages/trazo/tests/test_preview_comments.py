"""Preview: review comments and the source paths they may edit."""

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)

from trazo import preview


class CommentTests(unittest.TestCase):
    def setUp(self):
        review = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, review)
        for name in ("COMMENTS_PATH", "HISTORY_PATH", "FILE_HISTORY_PATH"):
            patcher = patch.object(preview, name, review / getattr(preview, name).name)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_add_resolve_and_undo(self):
        added = preview.change_comments({"op": "add", "doc": "manuscript", "comment": "Fix"})
        cid = added["comments"][0]["id"]
        replied = preview.change_comments({"op": "reply", "id": cid, "by": "agent",
                                           "text": "Done"})
        self.assertEqual(replied["comments"][0]["status"], "review")
        undone = preview.change_comments({"op": "undo"})
        self.assertEqual(undone["comments"][0]["status"], "open")

    def test_legacy_comments_keep_anchors_and_document_scoped_undo(self):
        legacy = {"id": "old", "doc": "manuscript", "section": "Introduction",
                  "para": "Introduction ¶1", "quote": "Selected text", "comment": "Check",
                  "reply": "Previous reply", "status": "review", "created": "2025-01-01"}
        preview.COMMENTS_PATH.write_text(json.dumps([legacy]))
        preview.HISTORY_PATH.write_text(json.dumps([
            {"comments": []},  # obsolete whole-file history cannot be safely undone
            {"time": "2025-01-01", "label": "add comment (Introduction ¶1)",
             "doc": "manuscript", "changes": [{"id": "old", "before": None, "after": legacy}]}]))
        preview.change_comments({"op": "reply", "id": "old", "text": "Follow-up"})
        preview.change_comments({"op": "add", "doc": "supplementary", "comment": "Other"})
        state = preview.comments_payload("manuscript")
        self.assertEqual(len(state["comments"]), 2)  # viewer payload includes every document
        self.assertEqual(state["undo"], "follow-up (Introduction ¶1)")
        item = state["comments"][0]
        for field in ("doc", "section", "para", "quote"):
            self.assertEqual(item[field], legacy[field])
        self.assertEqual([r["text"] for r in item["replies"]], ["Previous reply", "Follow-up"])
        preview.change_comments({"op": "undo", "doc": "manuscript"})
        self.assertEqual(preview.load_comments()[0], legacy)
        self.assertEqual(len(preview.load_comments()), 2)
        entries = json.loads(preview.HISTORY_PATH.read_text())
        self.assertTrue(all("kind" not in entry and "changes" in entry for entry in entries))
        # An external edit refuses and removes only the conflicting history entry.
        changed = preview.load_comments()
        changed[0]["comment"] = "Edited on disk"
        preview.COMMENTS_PATH.write_text(json.dumps(changed))
        with self.assertRaises(preview.Conflict):
            preview.change_comments({"op": "undo", "doc": "manuscript"})
        self.assertIsNone(preview.comments_payload("manuscript")["undo"])
        self.assertEqual(preview.load_comments()[0]["comment"], "Edited on disk")

    def test_anchor_keeps_the_words_around_the_quote_and_refuses_other_keys(self):
        anchor = {"text": "Alpha beta, then beta again.", "prefix": "Alpha beta, then ",
                  "suffix": " again.", "sectionId": "intro"}
        added = preview.change_comments({"op": "add", "doc": "manuscript", "quote": "beta",
                                         "anchor": anchor, "comment": "Which one"})
        self.assertEqual(added["comments"][0]["anchor"], anchor)
        with self.assertRaises(preview.CommentError):
            preview.change_comments({"op": "add", "doc": "manuscript", "comment": "x",
                                     "anchor": {"suffix": "ok", "other": "no"}})

    def test_a_quote_of_only_whitespace_is_stored_as_no_quote(self):
        added = preview.change_comments({"op": "add", "doc": "manuscript", "quote": " \n ", "comment": "General"})
        self.assertEqual(added["comments"][0]["quote"], "")

    def test_resolved_time_and_removing_several_underlines_in_one_undo(self):
        cid = preview.change_comments({"op": "add", "doc": "manuscript", "comment": "One"})["comments"][-1]["id"]
        preview.change_comments({"op": "update", "id": cid, "status": "done"})
        self.assertTrue(preview.load_comments()[0]["resolved"])
        # Reopening clears the time.
        reopened = preview.change_comments({"op": "update", "id": cid, "status": "open"})["comments"][0]
        self.assertNotIn("resolved", reopened)
        ids = [preview.change_comments({"op": "add", "doc": doc, "kind": "underline", "comment": "Underline"})
               ["comments"][-1]["id"] for doc in ("manuscript", "supplementary")]
        state = preview.change_comments({"op": "delete", "ids": ids})
        self.assertEqual(state["undo"], "remove 2 underlines")
        # Both documents' views see the step as their next undo.
        for doc in ("manuscript", "supplementary"):
            payload = preview.comments_payload(doc)
            self.assertEqual((payload["undo"], payload["undo_id"]), ("remove 2 underlines", state["undo_id"]))
        # An undo meant for another entry is refused and keeps this one.
        with self.assertRaises(preview.Conflict):
            preview.change_comments({"op": "undo", "doc": "manuscript", "expect": "other"})
        undone = preview.change_comments({"op": "undo", "doc": "manuscript", "expect": state["undo_id"]})
        self.assertEqual([c["status"] for c in undone["comments"]], ["open", "open", "open"])
        # A missing id changes nothing; delete only removes underlines (comments are purged).
        with self.assertRaises(preview.NotFound):
            preview.change_comments({"op": "delete", "ids": [ids[1], "missing"]})
        for bad in ({"op": "delete", "ids": []}, {"op": "delete", "ids": [ids[0], ids[0]]},
                    {"op": "update", "ids": ids, "status": "done"}, {"op": "delete", "id": cid}):
            with self.subTest(bad=bad), self.assertRaises(preview.CommentError):
                preview.change_comments(bad)
        self.assertEqual({c["status"] for c in preview.load_comments()}, {"open"})

    def test_purge_deletes_comments_and_their_history_for_good(self):
        def add(text):
            return preview.change_comments({"op": "add", "doc": "manuscript", "comment": text})["comments"][-1]["id"]
        kept, first, second = add("Keep me"), add("Gone one"), add("Gone two")
        for cid in (first, second):
            preview.change_comments({"op": "update", "id": cid, "status": "done"})
        # Several at once only takes resolved comments, and refuses the whole request otherwise.
        with self.assertRaises(preview.CommentError):
            preview.change_comments({"op": "purge", "ids": [first, kept]})
        with self.assertRaises(preview.NotFound):
            preview.change_comments({"op": "purge", "ids": [first, "missing"]})
        self.assertEqual(len(preview.load_comments()), 3)
        state = preview.change_comments({"op": "purge", "ids": [first, second], "doc": "manuscript"})
        self.assertEqual([c["id"] for c in state["comments"]], [kept])
        # Their text leaves the review files, history included; the rest stays undoable.
        for path in (preview.COMMENTS_PATH, preview.HISTORY_PATH):
            self.assertNotIn("Gone", path.read_text())
        self.assertEqual(state["undo"], f"add comment ({kept})")
        # One comment may be deleted in any state.
        self.assertEqual(preview.change_comments({"op": "purge", "id": kept})["comments"], [])
        self.assertIsNone(preview.comments_payload("manuscript")["undo"])

    def test_comments_left_in_the_old_trash_read_as_resolved(self):
        store = preview.PreviewComments()
        store.save([{"id": "old", "doc": "manuscript", "comment": "Trashed", "status": "deleted",
                     "deleted_from": "open", "replies": []},
                    {"id": "mark", "doc": "manuscript", "kind": "underline", "comment": "Underline",
                     "status": "deleted", "deleted_from": "open", "replies": []}])
        old, mark = preview.comments_payload("manuscript")["comments"]
        self.assertEqual((old["status"], "deleted_from" in old), ("done", False))
        self.assertEqual(mark["status"], "deleted")  # a removed underline stays removed
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            preview.print_comments(True)
        self.assertIn("[old] done", out.getvalue())
        self.assertEqual(preview.change_comments({"op": "purge", "ids": ["old"]})["comments"][0]["id"], "mark")

    def test_underlines_are_marks_in_history_and_hidden_from_listings(self):
        mark = preview.change_comments({"op": "add", "doc": "manuscript", "para": "Methods ¶1",
                                        "kind": "underline", "comment": "Underline"})
        self.assertEqual(mark["undo"], "underline (Methods ¶1)")
        preview.change_comments({"op": "add", "doc": "manuscript", "para": "Methods ¶2", "comment": "Fix"})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            preview.print_comments(True)
        self.assertNotIn("Methods ¶1", out.getvalue())
        self.assertIn("Methods ¶2", out.getvalue())
        other = preview.change_comments({"op": "add", "doc": "manuscript", "para": "Methods ¶3",
                                         "kind": "underline", "comment": "Underline"})["comments"][-1]["id"]
        removed = preview.change_comments({"op": "delete", "ids": [mark["comments"][0]["id"], other]})
        self.assertEqual(removed["undo"], "remove 2 underlines")

    def test_a_change_without_a_string_id_is_a_bad_request(self):
        preview.change_comments({"op": "add", "doc": "manuscript", "comment": "Fix"})
        for request, message in (({"op": "update", "status": "done"}, "id is required"),
                                 ({"op": "reply", "id": 7, "text": "Done"}, "id must be a string")):
            with self.subTest(request=request):
                with self.assertRaisesRegex(preview.CommentError, message) as raised:
                    preview.change_comments(request)
                self.assertEqual(raised.exception.status, 400)

    def test_unknown_document_is_refused(self):
        with self.assertRaises(preview.CommentError):
            preview.change_comments({"op": "add", "doc": "nope", "comment": "x"})

    def test_source_edits_stay_inside_document_folders(self):
        state = preview.get_source("manuscript/main/01_introduction.md")
        self.assertIn("{{v:cohort.n}}", state["text"])
        for bad in ("trazo.toml", "../x.md", "/etc/passwd", "manuscript/metadata.yaml"):
            with self.subTest(bad=bad), self.assertRaises(preview.CommentError):
                preview.get_source(bad)
