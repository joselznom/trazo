"""Viewer regressions in Chrome: writing, resolving and clearing comments in the text, diagrams and tables."""

import json
import unittest

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)
from _preview_browser import PreviewBrowserTestCase

from trazo import preview


class CommentFlowTests(PreviewBrowserTestCase):
    def test_comment_editor_can_move_without_losing_the_draft_and_stays_in_the_viewport(self):
        self.open()
        self.select_second_paragraph()
        self.evaluate("document.querySelector('[data-select=comment]').click()")
        self.evaluate("document.querySelector('.pv-editor textarea').value = 'Keep this draft'")
        box = "(() => { const r = document.querySelector('.pv-editor').getBoundingClientRect(); return {left:r.left, top:r.top, right:r.right, bottom:r.bottom}; })()"
        before = self.evaluate(box)
        handle = self.evaluate("""(() => { const r = document.querySelector('.pv-editor .pv-where').getBoundingClientRect();
          return [r.left + 8, r.top + r.height / 2]; })()""")
        self.mouse("mousePressed", *handle)
        self.mouse("mouseMoved", handle[0] + 90, handle[1] + 70)
        self.mouse("mouseReleased", handle[0] + 90, handle[1] + 70)
        after = self.evaluate(box)
        self.assertGreater(after["left"], before["left"] + 50)
        self.assertGreater(after["top"], before["top"] + 40)
        self.assertEqual(self.evaluate("document.querySelector('.pv-editor textarea').value"), "Keep this draft")
        self.evaluate("""const move = document.querySelector('.pv-editor-move'); move.focus();
          move.dispatchEvent(new KeyboardEvent('keydown', {key:'ArrowLeft', bubbles:true}));""")
        self.assertAlmostEqual(self.evaluate(box)["left"], after["left"] - 10, delta=1)
        self.evaluate("""const area = document.querySelector('.pv-editor textarea');
          area.focus(); area.setSelectionRange(5, 9);""")
        self.assertEqual(self.evaluate("[document.activeElement.selectionStart, document.activeElement.selectionEnd]"), [5, 9])
        self.chrome.call("Emulation.setDeviceMetricsOverride", {
            "width": 520, "height": 400, "deviceScaleFactor": 1, "mobile": False}, self.session)
        self.wait_for("document.querySelector('.pv-editor').getBoundingClientRect().right <= innerWidth - 7")
        moved = self.evaluate(box)
        self.assertGreaterEqual(moved["left"], 7)
        self.assertGreaterEqual(moved["top"], 7)
        self.assertLessEqual(moved["bottom"], 393)
        self.evaluate("""document.querySelector('.pv-editor textarea').style.height = '600px';""")
        self.wait_for("document.querySelector('.pv-editor').getBoundingClientRect().bottom <= innerHeight - 7")
        self.evaluate("document.querySelector('.pv-editor [data-op=save]').click()")
        self.wait_for("document.querySelector('.pv-card.open.current')")
        self.assertEqual(preview.load_comments()[0]["comment"], "Keep this draft")

    def test_comment_flow_resolves_reopens_and_clears_resolved_in_one_undo(self):
        self.open()
        self.select_second_paragraph()
        self.evaluate("""(() => {
          document.querySelector('[data-select=comment]').click();
          const area = document.querySelector('.pv-editor textarea');
          area.value = 'Clarify';
          area.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', metaKey: true, bubbles: true}));
        })()""")
        # The new comment is the current card; it offers Edit, Resolve and delete.
        self.wait_for("document.querySelector('.pv-card.open.current')")
        ops = "Array.from(document.querySelectorAll('.pv-card [data-op]'), b => b.dataset.op)"
        self.assertEqual(self.evaluate(ops), ["edit", "resolve", "delete"])
        cid = preview.load_comments()[0]["id"]
        preview.change_comments({"op": "reply", "id": cid, "by": "agent", "text": "Clarified"})
        self.open()
        self.wait_for("document.querySelector('.pv-card.review')")
        self.assertEqual(self.evaluate("document.querySelector('.pv-csum-text').textContent"), "1 to check")
        self.assertEqual(self.evaluate(ops), ["reply", "resolve", "delete"])
        # Resolving the current card leaves no highlight in the text.
        self.evaluate("document.querySelector('.pv-card.review .pv-text').click()")
        self.wait_for("document.querySelector('.pv-card.review.current')")
        self.evaluate("document.querySelector('.pv-card [data-op=resolve]').click()")
        self.wait_for("document.querySelector('.pv-empty b')?.textContent === 'Nothing to handle'")
        self.assertTrue(preview.load_comments()[0]["resolved"])
        highlighted = "[...CSS.highlights.values()].reduce((n, hl) => n + hl.size, 0)"
        self.assertEqual(self.evaluate(highlighted), 0)
        # Resolved: one line, then the full card with Reopen, its text shown.
        self.evaluate("document.querySelector('[data-filter=resolved]').click()")
        self.wait_for("document.querySelector('.pv-card.done.compact')")
        self.evaluate("document.querySelector('.pv-card.done.compact').click()")
        self.wait_for("document.querySelector('.pv-card.done [data-op=reopen]')")
        self.assertEqual(self.evaluate(highlighted), 0)
        self.evaluate("document.querySelector('[data-filter=active]').click()")
        self.assertEqual(self.evaluate(highlighted), 0)
        # Back under Resolved the card is folded again.
        self.evaluate("document.querySelector('[data-filter=resolved]').click()")
        self.wait_for("document.querySelector('.pv-card.done.compact')")
        self.assertEqual(self.evaluate(highlighted), 0)
        self.evaluate("document.querySelector('.pv-card.done.compact').click()")
        self.wait_for("document.querySelector('.pv-card.done [data-op=reopen]')")
        self.assertEqual(self.evaluate("Array.from(document.querySelectorAll('.pv-card.done [data-op]'), b => b.dataset.op)"),
                         ["show", "reopen", "delete"])
        self.evaluate("document.querySelector('.pv-card.done [data-op=reopen]').click()")
        self.wait_for("document.querySelector('.pv-empty b')?.textContent === 'No resolved comments'")
        self.assertEqual(preview.load_comments()[0]["status"], "open")
        self.assertNotIn("resolved", preview.load_comments()[0])
        # Delete all resolved is one step, undone from the notice.
        other = preview.change_comments({"op": "add", "doc": "manuscript", "para": "Introduction ¶1",
                                         "comment": "Second"})["comments"][1]["id"]
        for comment_id in (cid, other):
            preview.change_comments({"op": "update", "id": comment_id, "status": "done"})
        self.open()
        self.wait_for("document.querySelectorAll('.pv-card.done.compact').length === 2")
        self.evaluate("Array.from(document.querySelectorAll('.pv-cfoot button')).find(b => b.textContent === 'Delete all resolved').click()")
        self.wait_for("document.querySelector('.pv-toast')?.textContent.startsWith('2 resolved comments deleted')")
        self.assertEqual({c["status"] for c in preview.load_comments()}, {"deleted"})
        self.evaluate("document.querySelector('.pv-toast button').click()")
        self.wait_for("document.querySelectorAll('.pv-card.done.compact').length === 2")
        self.assertEqual({c["status"] for c in preview.load_comments()}, {"done"})

    def click(self, selector, text):
        """Click the element matching selector whose text (or aria-label) is text."""
        self.evaluate(f"""Array.from(document.querySelectorAll({json.dumps(selector)}))
            .find(b => (b.textContent.trim() || b.getAttribute('aria-label')) === {json.dumps(text)}).click()""")

    def test_diagram_comments_resolve_reply_and_clear_resolved_with_undo(self):
        from trazo import diagram as dg
        from trazo import diagram_editor
        self.addCleanup(diagram_editor.shutdown)
        self.keep_files(dg.COMMENTS_PATH, dg.HISTORY_PATH)
        store = diagram_editor.COMMENTS
        cid = store.change({"op": "add", "diagram": "example", "comment": "Bigger"})["comments"][-1]["id"]
        self.chrome.call("Page.navigate", {"url": self.url.replace("/manuscript.html", "/diagrams#example")}, self.session)
        self.wait_for("document.querySelector('#paper')?.textContent.length > 0")
        self.evaluate("document.querySelector('[data-tab=comments]').click()")
        self.wait_for("document.querySelector('#comments .ccard.open')")
        actions = ("Array.from(document.querySelectorAll('#comments .ccard .actions button'), "
                   "b => b.textContent.trim() || b.getAttribute('aria-label'))")
        self.assertEqual(self.evaluate(actions), ["Edit", "✓ Resolve", "Delete comment"])
        self.assertEqual(self.evaluate("document.querySelector('#comments .csum').textContent"), "1 waiting for the agent")
        store.change({"op": "reply", "id": cid, "by": "agent", "text": "Done"})
        self.wait_for("document.querySelector('#comments .ccard.review')")
        self.assertEqual(self.evaluate("document.querySelector('#comments .csum').textContent"), "1 to check")
        self.assertEqual(self.evaluate(actions), ["Reply", "✓ Resolve", "Delete comment"])
        # Resolve, then Undo from the notice.
        self.click("#comments .ccard .actions button", "✓ Resolve")
        self.wait_for("document.querySelector('#comments .empty b')?.textContent === 'Nothing to handle'")
        self.assertTrue(store.load()[0]["resolved"])
        self.wait_for("document.querySelector('#toast')?.textContent.startsWith('Comment resolved')")
        self.evaluate("document.querySelector('#toast button').click()")
        self.wait_for("document.querySelector('#comments .ccard.review')")
        self.assertEqual(store.load()[0]["status"], "review")
        # A reply reopens the comment for the agent.
        self.click("#comments .ccard .actions button", "Reply")
        self.evaluate("""(() => {
          const area = document.querySelector('#comments .reply-box textarea');
          area.value = 'Bigger still';
          area.dispatchEvent(new Event('input', {bubbles: true}));
          area.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', metaKey: true, bubbles: true}));
        })()""")
        self.wait_for("document.querySelector('#comments .ccard.open')")
        self.assertEqual([r["by"] for r in store.load()[0]["replies"]], ["agent", "author"])
        # Resolved comments: one line each, deleted together in one undo step.
        store.change({"op": "update", "id": cid, "status": "done"})
        self.wait_for("document.querySelector('#comments .empty b')?.textContent === 'Nothing to handle'")
        self.evaluate("document.querySelector('#comments [data-filter=resolved]').click()")
        self.wait_for("document.querySelector('#comments .ccard.done.compact')")
        self.click("#comments .cfoot button", "Delete all resolved")
        self.wait_for("document.querySelector('#toast')?.textContent.startsWith('1 resolved comment deleted')")
        self.assertEqual(store.load()[0]["status"], "deleted")
        self.evaluate("document.querySelector('#toast button').click()")
        self.wait_for("document.querySelector('#comments .ccard.done.compact')")
        self.assertEqual(store.load()[0]["status"], "done")
        # After the notice, Undo next to the filter takes back the resolve.
        self.evaluate("document.querySelector('#comments .cfilter button[title^=\"Undo\"]').click()")
        self.wait_for("document.querySelector('#comments .empty b')?.textContent === 'No resolved comments'")
        self.assertEqual(store.load()[0]["status"], "open")

    def test_table_comments_edit_reply_and_clear_resolved_with_undo(self):
        from trazo import table
        self.keep_files(table.COMMENTS_PATH, table.HISTORY_PATH)
        cid = table.COMMENTS.change({"op": "add", "table": "results", "comment": "Round"})["comments"][-1]["id"]
        self.open_table()
        self.evaluate("document.querySelector('.te-tabs [data-tab=comments]').click()")
        self.wait_for("document.querySelector('.te-card.open')")
        actions = ("Array.from(document.querySelectorAll('.te-card .te-cacts button'), "
                   "b => b.textContent.trim() || b.getAttribute('aria-label'))")
        self.assertEqual(self.evaluate(actions), ["Edit", "✓ Resolve", "Delete comment"])
        self.assertEqual(self.evaluate("document.querySelector('#te-cbadge').textContent"), "1")
        # Edit inline, then Undo from the notice.
        self.click(".te-card .te-cacts button", "Edit")
        self.evaluate("""(() => {
          const area = document.querySelector('.te-card textarea');
          area.value = 'Round to 2 decimals';
          area.dispatchEvent(new Event('input', {bubbles: true}));
        })()""")
        self.click(".te-card .te-cacts button", "Save")
        self.wait_for("document.querySelector('.te-card > p')?.textContent === 'Round to 2 decimals'")
        self.assertEqual(table.COMMENTS.load()[0]["comment"], "Round to 2 decimals")
        self.wait_for("document.querySelector('.te-flash')?.textContent.startsWith('Comment edited')")
        self.evaluate("document.querySelector('.te-flash button').click()")
        self.wait_for("document.querySelector('.te-card > p')?.textContent === 'Round'")
        # The agent's reply: Reply sends it back, Resolve closes it.
        table.COMMENTS.change({"op": "reply", "id": cid, "by": "agent", "text": "Rounded"})
        self.wait_for("document.querySelector('.te-card.review')")
        self.assertEqual(self.evaluate("document.querySelector('.te-csum').textContent"), "1 to check")
        self.assertEqual(self.evaluate(actions), ["Reply", "✓ Resolve", "Delete comment"])
        self.click(".te-card .te-cacts button", "Reply")
        self.evaluate("""(() => {
          const area = document.querySelector('.te-card textarea');
          area.value = 'Also the CI';
          area.dispatchEvent(new Event('input', {bubbles: true}));
          area.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', metaKey: true, bubbles: true}));
        })()""")
        self.wait_for("document.querySelector('.te-card.open') && !document.querySelector('.te-card textarea')")
        self.assertEqual([r["by"] for r in table.COMMENTS.load()[0]["replies"]], ["agent", "author"])
        self.click(".te-card .te-cacts button", "✓ Resolve")
        self.wait_for("document.querySelector('.te-cempty b')?.textContent === 'Nothing to handle'")
        self.assertTrue(table.COMMENTS.load()[0]["resolved"])
        # Resolved comments: one line each, deleted together in one undo step.
        self.evaluate("document.querySelector('.te-cbar [data-filter=resolved]').click()")
        self.wait_for("document.querySelector('.te-card.done.compact')")
        self.click(".te-cfoot button", "Delete all resolved")
        self.wait_for("document.querySelector('.te-flash')?.textContent.startsWith('1 resolved comment deleted')")
        self.assertEqual(table.COMMENTS.load()[0]["status"], "deleted")
        self.evaluate("document.querySelector('.te-flash button').click()")
        self.wait_for("document.querySelector('.te-card.done.compact')")
        self.assertEqual(table.COMMENTS.load()[0]["status"], "done")
        # After the notice, Undo next to the filter takes back the resolve.
        self.evaluate("document.querySelector('.te-cundo').click()")
        self.wait_for("document.querySelector('.te-cempty b')?.textContent === 'No resolved comments'")
        self.assertEqual(table.COMMENTS.load()[0]["status"], "open")


if __name__ == "__main__":
    unittest.main()
