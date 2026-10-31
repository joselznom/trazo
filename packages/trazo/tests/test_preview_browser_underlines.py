"""Viewer regressions in Chrome: underlines, the reading marks on the text."""

import unittest

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)
from _preview_browser import PreviewBrowserTestCase

from trazo import preview


class UnderlineTests(PreviewBrowserTestCase):
    def test_a_rewritten_paragraph_shows_every_state_it_holds_and_its_underline_can_be_removed(self):
        page = preview.OUT_DIR / "manuscript.html"
        page.write_text(page.read_text().replace("<p>Another paragraph.</p>",
            '<p>The finding now <span id="reads">reads</span> more precisely.</p>'))
        anchor = {"text": "Another paragraph.", "before": "A word, H2O.",
                  "after": "Figure 1. External image.", "sectionId": "intro"}
        ids = {}
        for state, extra in (("open", {}), ("review", {}), ("underline", {"kind": "underline"})):
            ids[state] = preview.change_comments({"op": "add", "doc": "manuscript", "para": "Introduction ¶2",
                "section": "Introduction", "quote": "Another paragraph.", "comment": state, "anchor": anchor,
                **extra})["comments"][-1]["id"]
        preview.change_comments({"op": "reply", "id": ids["review"], "by": "agent", "text": "Changed"})
        self.open()
        self.wait_for("document.querySelectorAll('.pv-card').length === 2")
        para = "document.querySelector('[data-label=\"Introduction ¶2\"]')"
        # One strip per state, in three colours; nothing is painted in the text.
        self.assertEqual(self.evaluate(para + ".dataset.lost"), "open review underline")
        strips = self.evaluate(f"""(() => {{
          const probe = document.createElement('i'); document.body.append(probe);
          const colour = (v) => {{ probe.style.color = `var(${{v}})`; return getComputedStyle(probe).color; }};
          const wanted = ['--pv-hl-open-edge', '--pv-hl-review-sel', '--pv-warn-dot'].map(colour);
          probe.remove();
          const shadow = getComputedStyle({para}).boxShadow;
          return wanted.map((c) => shadow.indexOf(c));
        }})()""")
        self.assertEqual(strips, sorted(strips))
        self.assertNotIn(-1, strips)
        self.assertEqual(self.evaluate("[...CSS.highlights.values()].reduce((n, hl) => n + hl.size, 0)"), 0)
        # Hovering cards repaints highlights but leaves the markers alone.
        self.evaluate("""window.touched = 0;
          new MutationObserver((records) => { window.touched += records.length; }).observe(document.querySelector('.pv-page'),
            {attributes: true, subtree: true});""")
        self.evaluate("""for (const card of document.querySelectorAll('.pv-card')) {
          card.dispatchEvent(new MouseEvent('mouseenter')); card.dispatchEvent(new MouseEvent('mouseleave'));
        }""")
        self.evaluate("new Promise((resolve) => setTimeout(resolve, 50))")
        self.assertEqual(self.evaluate("window.touched"), 0)
        # The gallery has no margin markers.
        self.evaluate("document.querySelector('[data-view=figures]').click()")
        self.wait_for("location.search === '?view=figures' && !document.querySelector('.pv-lost')")

    def test_an_underline_of_a_rewritten_paragraph_is_marked_and_removed_by_a_click_in_it(self):
        page = preview.OUT_DIR / "manuscript.html"
        page.write_text(page.read_text().replace("<p>Another paragraph.</p>",
            '<p>The finding now <span id="reads">reads</span> more precisely.</p>'))
        preview.change_comments({"op": "add", "doc": "manuscript", "para": "Introduction ¶2",
            "section": "Introduction", "quote": "Another paragraph.", "comment": "Underline", "kind": "underline",
            "anchor": {"text": "Another paragraph.", "before": "A word, H2O.",
                       "after": "Figure 1. External image.", "sectionId": "intro"}})
        self.open()
        para = "document.querySelector('[data-label=\"Introduction ¶2\"]')"
        self.wait_for(f"{para}.dataset.lost === 'underline'")
        # A click elsewhere offers nothing; a click in the paragraph offers to remove its underline.
        self.click_text(".pv-page em")
        self.evaluate("new Promise((resolve) => setTimeout(resolve, 100))")
        self.assertTrue(self.evaluate("document.querySelector('.pv-fab').hidden"))
        self.click_text("#reads")
        self.wait_for("document.querySelector('.pv-fab').hidden === false")
        self.assertEqual(self.fab_buttons(), ["Remove underline"])
        self.evaluate("document.querySelector('[data-select=unmark]').click()")
        self.wait_for(f"!{para}.classList.contains('pv-lost')")
        self.assertEqual(preview.load_comments()[0]["status"], "deleted")
        self.assertEqual(preview.comments_payload("manuscript")["undo"], "remove underline (Introduction ¶2)")

    def fab_buttons(self):
        return self.evaluate("Array.from(document.querySelectorAll('.pv-fab button'), b => b.textContent)")

    def test_underline_is_a_reading_mark_not_a_comment(self):
        self.open()
        self.select_second_paragraph()
        self.assertEqual(self.fab_buttons(), ["Comment", "Underline"])
        self.evaluate("document.querySelector('[data-select=underline]').click()")
        self.wait_for("CSS.highlights.get('pv-underline')?.size === 1")
        self.wait_for("document.querySelector('.pv-toast')?.textContent.startsWith('Underlined')")
        # No card, count or index badge.
        self.assertEqual(self.evaluate("document.querySelector('.pv-empty b').textContent"), "No comments")
        self.assertEqual(self.evaluate("document.querySelector('[data-filter=active] .n').textContent"), "0")
        self.assertTrue(self.evaluate("document.querySelector('.pv-rtabs .pv-badge').hidden"))
        self.assertFalse(self.evaluate("!!document.querySelector('.pv-bd')"))
        self.assertEqual(preview.load_comments()[0]["kind"], "underline")
        self.open()
        self.wait_for("CSS.highlights.get('pv-underline')?.size === 1")
        # Selecting underlined text offers its removal instead.
        self.select_second_paragraph()
        self.assertEqual(self.fab_buttons(), ["Comment", "Remove underline"])
        self.evaluate("getSelection().removeAllRanges(); document.querySelector('.pv-fab').hidden = true")
        # So does a click on it (not a right-click, which keeps its menu).
        click = """((button) => {
          const rect = [...CSS.highlights.get('pv-underline')][0].getClientRects()[0];
          const x = rect.left + 4, y = rect.top + rect.height / 2;
          document.elementFromPoint(x, y).dispatchEvent(new MouseEvent('mouseup', {bubbles: true, clientX: x, clientY: y, button}));
        })"""
        self.evaluate(click + "(2)")
        self.evaluate("new Promise(resolve => setTimeout(resolve, 100))")
        self.assertTrue(self.evaluate("document.querySelector('.pv-fab').hidden"))
        self.evaluate(click + "(0)")
        self.wait_for("document.querySelector('.pv-fab').hidden === false")
        self.assertEqual(self.fab_buttons(), ["Remove underline"])
        self.evaluate("document.querySelector('[data-select=unmark]').click()")
        self.wait_for("CSS.highlights.get('pv-underline')?.size === 0")
        self.assertEqual(preview.load_comments()[0]["status"], "deleted")
        self.assertEqual(preview.comments_payload("manuscript")["undo"], "remove underline (Introduction ¶2)")


if __name__ == "__main__":
    unittest.main()
