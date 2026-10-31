"""Viewer regressions in Chrome: opening threads from the text and locating resolved comments."""

import unittest

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)
from _preview_browser import PreviewBrowserTestCase

from trazo import preview


class ResolvedCommentTests(PreviewBrowserTestCase):
    def test_commented_links_open_the_sidebar_and_cycle_overlapping_threads(self):
        page = preview.OUT_DIR / "manuscript.html"
        page.write_text(page.read_text().replace("Another paragraph.",
            'Another <a href="#intro">paragraph</a>. <a href="#ordinary">Ordinary link</a>.'))
        ids = []
        for quote, text in (("Another paragraph.", "Broader explanation"), ("paragraph", "Precise wording")):
            ids.append(preview.change_comments({"op": "add", "doc": "manuscript",
                "para": "Introduction ¶2", "section": "Introduction", "quote": quote,
                "comment": text})["comments"][-1]["id"])
        resolved = preview.change_comments({"op": "add", "doc": "manuscript", "para": "Introduction ¶1",
            "section": "Introduction", "quote": "word", "comment": "Resolved wording"})["comments"][-1]["id"]
        preview.change_comments({"op": "update", "id": resolved, "status": "done"})
        self.open()
        self.wait_for("document.querySelectorAll('.pv-card').length === 2")
        self.evaluate("document.querySelector('[data-side=right]').click()")
        self.click_text('.pv-page a[href="#intro"]')
        self.wait_for("document.querySelector('.pv-side.right').classList.contains('open')")
        self.assertEqual(self.evaluate("document.querySelector('.pv-card.current')?.dataset.id"), ids[0])
        self.assertEqual(self.evaluate("location.hash"), "")
        self.click_text('.pv-page a[href="#intro"]')
        self.assertEqual(self.evaluate("document.querySelector('.pv-card.current')?.dataset.id"), ids[1])
        self.assertEqual(self.evaluate("document.querySelector('.pv-card.current .pv-text').textContent"), "Precise wording")
        self.click_text('.pv-page a[href="#ordinary"]')
        self.assertEqual(self.evaluate("location.hash"), "#ordinary")
        # Resolved text is not clickable: nothing changes.
        self.click_text(".pv-page em")
        self.assertEqual(self.evaluate("document.querySelector('.pv-card.current')?.dataset.id"), ids[1])
        self.assertFalse(self.evaluate("!!document.querySelector('.pv-card.done')"))

    def test_resolved_comments_are_archived_not_picked_by_a_click_in_the_text(self):
        page = preview.OUT_DIR / "manuscript.html"
        page.write_text(page.read_text().replace("Another paragraph.",
            'Another <span id="word">paragraph</span>.'))
        quotes = {"Another paragraph.": "Resolved earlier", "paragraph": "Open now"}
        ids = {}
        for quote, text in quotes.items():
            ids[text] = preview.change_comments({"op": "add", "doc": "manuscript", "para": "Introduction ¶2",
                "section": "Introduction", "quote": quote, "comment": text})["comments"][-1]["id"]
        preview.change_comments({"op": "update", "id": ids["Resolved earlier"], "status": "done"})
        self.open()
        self.wait_for("document.querySelectorAll('.pv-card').length === 1")
        names = "[...CSS.highlights].filter(([k, v]) => v.size).map(([k]) => k).sort()"
        self.assertEqual(self.evaluate(names), ["pv-open"])
        self.click_text("#word")
        self.wait_for("document.querySelector('.pv-card.open.current')")
        self.assertEqual(self.evaluate("document.querySelector('.pv-card.current').dataset.id"), ids["Open now"])
        self.assertEqual(self.evaluate("document.querySelector('[data-filter=active]').getAttribute('aria-pressed')"), "true")
        self.assertEqual(self.evaluate(names), ["pv-open-sel"])
        # Another click does not cycle to the resolved comment.
        self.click_text("#word")
        self.assertEqual(self.evaluate("document.querySelector('.pv-card.current').dataset.id"), ids["Open now"])
        self.assertEqual(self.evaluate("document.querySelector('[data-filter=active]').getAttribute('aria-pressed')"), "true")
        # Under Resolved the card opens without painting the text.
        self.evaluate("document.querySelector('[data-filter=resolved]').click()")
        self.evaluate("document.querySelector('.pv-card.done.compact').click()")
        self.wait_for("document.querySelector('.pv-card.done [data-op=show]')")
        self.assertEqual(self.evaluate(names), ["pv-open"])

    def test_show_in_text_scrolls_to_a_resolved_comment_and_flashes_it_briefly(self):
        page = preview.OUT_DIR / "manuscript.html"
        page.write_text(page.read_text().replace("<p>Another paragraph.</p>",
            "<p>Filler.</p>" * 80 + "<p>Another paragraph.</p>"))
        cid = preview.change_comments({"op": "add", "doc": "manuscript", "para": "Introduction ¶82",
            "section": "Introduction", "quote": "Another paragraph.", "comment": "Done"})["comments"][-1]["id"]
        gone = preview.change_comments({"op": "add", "doc": "manuscript", "para": "Introduction ¶1",
            "section": "Introduction", "quote": "Vanished words", "comment": "Gone"})["comments"][-1]["id"]
        for resolved in (cid, gone):
            preview.change_comments({"op": "update", "id": resolved, "status": "done"})
        self.chrome.call("Emulation.setEmulatedMedia", {"features": [
            {"name": "prefers-reduced-motion", "value": "reduce"}]}, self.session)  # scroll at once
        self.open()
        self.evaluate("document.querySelector('[data-side=right]').click()")
        self.evaluate("document.querySelector('[data-filter=resolved]').click()")
        self.wait_for("document.querySelectorAll('.pv-card.done.compact').length === 2")
        highlighted = "[...CSS.highlights.values()].reduce((n, hl) => n + hl.size, 0)"
        self.assertEqual(self.evaluate(highlighted), 0)
        self.assertEqual(self.evaluate("document.querySelector('.pv-scroller').scrollTop"), 0)
        self.evaluate(f"document.querySelector('.pv-card[data-id=\"{cid}\"]').click()")
        self.wait_for(f"document.querySelector('.pv-card[data-id=\"{cid}\"] [data-op=show]')")
        self.evaluate(f"document.querySelector('.pv-card[data-id=\"{cid}\"] [data-op=show]').click()")
        self.wait_for("CSS.highlights.get('pv-done-flash')?.size === 1")
        self.assertEqual(self.evaluate("[...CSS.highlights.get('pv-done-flash')][0].toString()"), "Another paragraph.")
        self.wait_for("document.querySelector('.pv-scroller').scrollTop > 500")
        self.wait_for("CSS.highlights.get('pv-done-flash').size === 0")
        self.assertEqual(self.evaluate(highlighted), 0)
        # Text that is gone: nothing to flash, and the notice says so.
        self.evaluate(f"document.querySelector('.pv-card[data-id=\"{gone}\"]').click()")
        self.wait_for(f"document.querySelector('.pv-card[data-id=\"{gone}\"] [data-op=show]')")
        self.evaluate(f"document.querySelector('.pv-card[data-id=\"{gone}\"] [data-op=show]').click()")
        self.wait_for("document.querySelector('.pv-toast')?.textContent.includes('changed or was removed')")
        self.assertEqual(self.evaluate(highlighted), 0)

    def test_resolved_comments_are_located_for_their_cards_and_lists_but_never_drawn(self):
        page = preview.OUT_DIR / "manuscript.html"
        page.write_text(page.read_text().replace('</section><script>',
            '<table id="tbl:noted"><caption>Table 1. Noted table.</caption><tbody><tr><td>1</td></tr></tbody></table>'
            '<div class="table-note"><p>SD, standard deviation.</p></div></section><script>'))
        stale = {"Introduction ¶9": ("Another paragraph.", "In a paragraph"),
                 "Figure 9 caption": ("External image.", "On a caption"),
                 "Table 9 note": ("SD, standard deviation.", "On a note"),
                 "Introduction ¶1": ("", "General")}
        anchor = {"text": "Another paragraph.", "before": "A word, H2O.",
                  "after": "Figure 1. External image.", "sectionId": "intro"}
        ids = {}
        for para, (quote, text) in stale.items():
            ids[text] = preview.change_comments({"op": "add", "doc": "manuscript", "para": para,
                "section": "Introduction", "quote": quote, "comment": text})["comments"][-1]["id"]
        ids["Rewritten"] = preview.change_comments({"op": "add", "doc": "manuscript", "para": "Introduction ¶2",
            "section": "Introduction", "quote": "Vanished words", "comment": "Rewritten", "anchor": anchor})["comments"][-1]["id"]
        for cid in ids.values():
            preview.change_comments({"op": "update", "id": cid, "status": "done"})
        self.open()
        self.wait_for("document.querySelector('[data-filter=resolved] .n')?.textContent === '5'")
        # Nothing of them is drawn, however they were located.
        self.assertEqual(self.evaluate("[...CSS.highlights.values()].reduce((n, hl) => n + hl.size, 0)"), 0)
        self.assertFalse(self.evaluate("!!document.querySelector('.pv-lost')"))
        # The cards name the paragraph, caption or note the text is in now.
        self.evaluate("document.querySelector('[data-filter=resolved]').click()")
        self.wait_for("document.querySelectorAll('.pv-card.done.compact').length === 5")
        where = lambda text: self.evaluate(
            f"document.querySelector('.pv-card[data-id=\"{ids[text]}\"] .pv-cwhere').textContent")
        self.assertEqual(where("In a paragraph"), "Introduction ¶2")
        self.assertEqual(where("On a caption"), "Figure 1 caption")
        self.assertEqual(where("On a note"), "Introduction ¶3")
        self.assertEqual(where("General"), "Introduction ¶1")
        # Show in text is offered only where there is a quote.
        ops = lambda text: self.evaluate(
            f"document.querySelector('.pv-card[data-id=\"{ids[text]}\"]').click() || "
            "Array.from(document.querySelectorAll('.pv-card.done [data-op]'), b => b.dataset.op)")
        self.assertEqual(ops("General"), ["reopen", "delete"])
        self.assertEqual(ops("In a paragraph"), ["show", "reopen", "delete"])
        # The gallery lists the resolved comments on captions and notes, whatever their old labels said.
        self.open("?view=figures")
        self.wait_for("document.querySelector('[data-filter=resolved] .n')?.textContent === '2'")
        self.evaluate("document.querySelector('[data-filter=resolved]').click()")
        self.wait_for("document.querySelectorAll('.pv-card.done.compact').length === 2")

    def test_show_in_text_flashes_the_paragraph_without_the_highlight_api(self):
        cid = preview.change_comments({"op": "add", "doc": "manuscript", "para": "Introduction ¶2",
            "section": "Introduction", "quote": "Another paragraph.", "comment": "Done"})["comments"][-1]["id"]
        preview.change_comments({"op": "update", "id": cid, "status": "done"})
        self.open()
        self.evaluate("document.querySelector('[data-side=right]').click()")
        self.evaluate("document.querySelector('[data-filter=resolved]').click()")
        self.wait_for("document.querySelector('.pv-card.done.compact')")
        self.evaluate("document.querySelector('.pv-card.done.compact').click()")
        self.wait_for("document.querySelector('.pv-card.done [data-op=show]')")
        self.evaluate("Object.defineProperty(CSS, 'highlights', {value: undefined, configurable: true})")
        self.evaluate("document.querySelector('.pv-card.done [data-op=show]').click()")
        self.wait_for("document.querySelector('[data-label=\"Introduction ¶2\"].pv-flash')")

    def test_resolved_line_in_figures_shows_its_thread_in_place(self):
        preview.change_comments({"op": "add", "doc": "supplementary", "para": "Figure 2 caption",
                                 "section": "Introduction", "quote": "Diagram caption.", "comment": "Caption"})
        cid = preview.load_comments()[0]["id"]
        preview.change_comments({"op": "update", "id": cid, "status": "done"})
        self.open("?view=figures")
        self.evaluate("document.querySelector('[data-filter=resolved]').click()")
        self.wait_for("document.querySelector('.pv-card.done.compact')")
        self.evaluate("document.querySelector('.pv-card.done.compact').click()")
        self.wait_for("document.querySelector('.pv-card.done [data-op=reopen]')")
        self.evaluate("new Promise(resolve => setTimeout(resolve, 300))")
        self.assertEqual(self.evaluate("[location.pathname, location.search]"), ["/manuscript.html", "?view=figures"])


if __name__ == "__main__":
    unittest.main()
