"""Viewer regressions in Chrome: going to the text of a comment from its card."""

import unittest

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)
from _preview_browser import PreviewBrowserTestCase

from trazo import preview


class CommentNavigationTests(PreviewBrowserTestCase):
    def test_a_far_comment_is_shown_at_once_and_a_near_one_scrolls_smoothly(self):
        page = preview.OUT_DIR / "manuscript.html"
        page.write_text(page.read_text().replace("<p>Another paragraph.</p>",
            "<p>Filler.</p>" * 40 + "<p>Near target.</p>" + "<p>Filler.</p>" * 400 + "<p>Far target.</p>"))
        ids = {}
        for quote in ("Near target.", "Far target."):
            ids[quote] = preview.change_comments({"op": "add", "doc": "manuscript", "para": "Introduction ¶2",
                "section": "Introduction", "quote": quote, "comment": quote})["comments"][-1]["id"]
        self.open()
        self.evaluate("document.querySelector('[data-side=right]').click()")
        self.wait_for("document.querySelectorAll('.pv-card.open').length === 2")
        scroller = "document.querySelector('.pv-scroller')"
        shown = lambda quote: f"""(() => {{
          const el = [...document.querySelectorAll('p')].find(p => p.textContent.includes({quote!r}));
          const r = el.getBoundingClientRect(), s = {scroller}.getBoundingClientRect();
          return r.top >= s.top && r.bottom <= s.bottom; }})()"""
        two_frames = "new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
        for quote, at_once in (("Far target.", True), ("Near target.", False)):
            with self.subTest(quote):
                self.evaluate(f"{scroller}.scrollTop = 0")
                self.assertFalse(self.evaluate(shown(quote)))
                self.evaluate(f"document.querySelector('.pv-card[data-id=\"{ids[quote]}\"] .pv-text').click()")
                # Smooth scrolling over a long paper took Chrome about 1.5 s.
                self.evaluate(two_frames)
                self.assertEqual(self.evaluate(shown(quote)), at_once)
                self.wait_for(shown(quote))


if __name__ == "__main__":
    unittest.main()
