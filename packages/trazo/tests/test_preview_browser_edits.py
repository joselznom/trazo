"""Pending edits in Chrome: the marks, their bar, the Edits control and a review comment's edits."""

import unittest
from unittest.mock import patch

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)
from _preview_browser import PreviewBrowserTestCase

from trazo import preview

INTRO = "manuscript/main/01_introduction.md"
EDITS = [
    {"file": INTRO, "id": "e1", "line": 3, "old": "short", "new": "long", "seen": "2025-01-05 10:57:00"},
    {"file": INTRO, "id": "e2", "line": 5, "old": "", "new": "\n\nA new paragraph.", "seen": "2026-11-03 10:57:00"},
    {"file": INTRO, "id": "e3", "line": 9, "old": "1", "new": "2", "seen": ""},  # a table cell: not marked
]


class EditMarksTests(PreviewBrowserTestCase):
    def setUp(self):
        super().setUp()
        page = preview.OUT_DIR / "manuscript.html"
        page.write_text(page.read_text().replace(
            "Another paragraph.",
            'Another <span class="pv-edit-del" data-edit="e1">short</span><span class="pv-edit-ins" data-edit="e1">long</span>'
            ' paragraph.</p><p><span class="pv-blk pv-new" data-edit="e2"></span>A new paragraph.'))
        self.calls = []
        self.edits = EDITS
        self.stale = False

        def change(request):
            self.calls.append(request)
            if self.stale:
                return {"done": 0, "missing": len(request["edits"]), "missed": request["edits"]}
            return {"done": len(request["edits"]), "missing": 0, "missed": []}
        for name, value in (("edits_payload", lambda doc: {"doc": doc, "edits": self.edits}), ("change_edits", change)):
            patcher = patch.object(preview, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def asked(self, op, *ids):
        return {"op": op, "edits": [{"file": INTRO, "id": i} for i in ids]}

    def test_a_mark_selects_its_edit_and_a_key_accepts_it(self):
        self.open()
        self.wait_for("!document.querySelector('.pv-edits').hidden")
        self.assertEqual(self.evaluate("document.querySelector('.pv-edits-toggle .n').textContent"), "3")
        self.assertTrue(self.evaluate("document.querySelector('[data-edit-block=e2]').classList.contains('pv-edit-new')"))
        self.click_text('.pv-edit-ins[data-edit="e1"]')
        self.wait_for("!document.querySelector('.pv-edit-bar').hidden")
        self.assertEqual(self.evaluate("document.querySelectorAll('.pv-edit-cur').length"), 2)
        self.assertEqual(self.evaluate("document.querySelector('.pv-edit-bar [data-edit-op=accept]').dataset.tip"),
                         "Accept (A) · Changed 2025-01-05 10:57")
        self.key("a", 65)
        self.wait_for("document.querySelector('.pv-edits-toggle .n').textContent === '2'")
        self.assertEqual(self.calls, [self.asked("accept", "e1")])
        # The page shows the result at once: the new word stays, the old one goes.
        self.assertFalse(self.evaluate("!!document.querySelector('[data-edit=e1]')"))
        self.assertIn("Another long paragraph.", self.evaluate("document.querySelector('.pv-page').textContent"))
        self.assertTrue(self.evaluate("document.querySelector('.pv-edit-bar').hidden"))

    def test_an_unmarked_edit_opens_its_source_and_can_be_accepted(self):
        self.open()
        self.wait_for("!document.querySelector('.pv-edits').hidden")
        for _ in range(3):
            self.evaluate("document.querySelector('[data-edit-step=\"1\"]').click()")
        self.wait_for("document.querySelector('[data-tab=source]').getAttribute('aria-selected') === 'true'")
        self.assertIn("not marked on the page", self.evaluate("document.querySelector('.pv-toast').textContent"))
        self.wait_for("!document.querySelector('.pv-edit-bar').hidden")
        self.assertEqual(self.evaluate("document.querySelector('.pv-edit-was').textContent"), "Was: 1\nNow: 2")
        self.evaluate("document.querySelector('.pv-edit-bar [data-edit-op=accept]').click()")
        self.wait_for("document.querySelector('.pv-edits-toggle .n').textContent === '2'")
        self.assertEqual(self.calls, [self.asked("accept", "e3")])

    def test_reject_all_asks_first(self):
        self.open()
        self.wait_for("!document.querySelector('.pv-edits').hidden")
        self.evaluate("window.confirm = () => false")
        self.evaluate("document.querySelector('.pv-edits-toggle').click()")
        self.evaluate("document.querySelector('[data-edit-op=\"reject-all\"]').click()")
        self.assertEqual(self.calls, [])
        self.evaluate("window.confirm = () => true")
        self.evaluate("document.querySelector('.pv-edits-toggle').click()")
        self.evaluate("document.querySelector('[data-edit-op=\"reject-all\"]').click()")
        self.wait_for("document.querySelector('.pv-edits').hidden")
        self.assertEqual(self.calls, [self.asked("reject", "e1", "e2", "e3")])
        self.assertTrue(self.evaluate("document.querySelector('[data-label=\"Introduction ¶3\"]').hidden"))
        self.assertIn("Another short paragraph.", self.evaluate("document.querySelector('.pv-page').textContent"))

    def test_hidden_marks_leave_the_text_as_it_is_now(self):
        self.review_comment()
        self.open()
        self.wait_for("!document.querySelector('.pv-edits').hidden")
        self.evaluate("document.querySelector('.pv-edits-toggle').click()")
        self.evaluate("document.querySelector('[data-edit-op=\"marks\"]').click()")
        self.assertEqual(self.evaluate("getComputedStyle(document.querySelector('.pv-edit-del')).display"), "none")
        self.assertEqual(self.evaluate("document.querySelector('[data-edit-op=\"marks\"]').getAttribute('aria-checked')"),
                         "false")
        # The comments' own trash button is not a mark.
        self.wait_for("document.querySelector('.pv-card button.pv-del')")
        self.assertNotEqual(self.evaluate("getComputedStyle(document.querySelector('.pv-card button.pv-del')).display"), "none")

    def review_comment(self):
        cid = preview.change_comments({"op": "add", "doc": "manuscript", "para": "Introduction ¶2",
            "section": "Introduction", "quote": "Another long paragraph.", "comment": "Say more"})["comments"][-1]["id"]
        preview.change_comments({"op": "reply", "id": cid, "by": "agent", "text": "Added a paragraph."})
        return cid

    def test_resolve_accepts_a_review_comments_edits(self):
        cid = self.review_comment()
        self.open()
        self.wait_for("document.querySelector('.pv-card.review [data-op=\"reject-edits\"]')")
        self.evaluate("document.querySelector('.pv-card.review [data-op=\"resolve\"]').click()")
        self.wait_for("!document.querySelector('.pv-card.review')")
        self.assertEqual(self.calls, [self.asked("accept", "e1", "e2")])
        self.assertEqual(next(c for c in preview.load_comments() if c["id"] == cid)["status"], "done")

    def test_a_block_with_two_edits_gives_the_comment_both(self):
        page = preview.OUT_DIR / "manuscript.html"
        page.write_text(page.read_text().replace(
            "<p>A <em>word</em>", '<p><span class="pv-blk" data-edit="e4"></span><span class="pv-blk" data-edit="e5"></span>A <em>word</em>'))
        self.edits = [{"file": INTRO, "id": i, "line": 1, "old": "x", "new": "y", "seen": ""} for i in ("e4", "e5")]
        cid = preview.change_comments({"op": "add", "doc": "manuscript", "para": "Introduction ¶1",
            "section": "Introduction", "quote": "word", "comment": "Check"})["comments"][-1]["id"]
        preview.change_comments({"op": "reply", "id": cid, "by": "agent", "text": "Done."})
        self.open()
        self.wait_for("document.querySelector('.pv-card.review [data-op=\"reject-edits\"]')")
        self.evaluate("document.querySelector('.pv-card.review [data-op=\"resolve\"]').click()")
        self.wait_for("!document.querySelector('.pv-card.review')")
        self.assertEqual(self.calls, [self.asked("accept", "e4", "e5")])

    def test_stale_edits_keep_the_comment_in_review(self):
        cid = self.review_comment()
        self.stale = True
        self.open()
        self.wait_for("document.querySelector('.pv-card.review [data-op=\"reject-edits\"]')")
        self.evaluate("document.querySelector('.pv-card.review [data-op=\"reject-edits\"]').click()")
        self.wait_for("document.querySelector('.pv-toast')?.textContent.includes('changed since the page was built')")
        self.assertEqual(next(c for c in preview.load_comments() if c["id"] == cid)["status"], "review")
        self.assertTrue(self.evaluate("!!document.querySelector('.pv-edit-ins[data-edit=e1]')"))
        # Until the rebuilt page arrives, Resolve is refused the same way.
        self.evaluate("document.querySelector('.pv-card.review [data-op=\"resolve\"]').click()")
        self.wait_for("document.querySelector('.pv-card.review')")
        self.evaluate("new Promise(resolve => setTimeout(resolve, 300))")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(next(c for c in preview.load_comments() if c["id"] == cid)["status"], "review")

    def test_reject_undoes_a_review_comments_edits_and_reopens_it(self):
        cid = self.review_comment()
        self.open()
        self.wait_for("document.querySelector('.pv-card.review [data-op=\"reject-edits\"]')")
        self.evaluate("document.querySelector('.pv-card.review [data-op=\"reject-edits\"]').click()")
        self.wait_for("document.querySelector('.pv-card.open')")
        self.assertEqual(self.calls, [self.asked("reject", "e1", "e2")])
        comment = next(c for c in preview.load_comments() if c["id"] == cid)
        self.assertEqual(comment["status"], "open")
        self.assertEqual(comment["replies"][-1]["by"], "author")
        self.assertIn("rejected your edits", comment["replies"][-1]["text"])


if __name__ == "__main__":
    unittest.main()
