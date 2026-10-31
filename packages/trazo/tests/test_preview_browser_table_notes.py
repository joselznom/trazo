"""Viewer regressions in Chrome: table notes laid out under their tables."""

import unittest

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)
from _preview_browser import PreviewBrowserTestCase

from trazo import preview


class TableNoteTests(PreviewBrowserTestCase):
    def test_table_note_reads_under_its_table_in_the_page_cards_and_viewer(self):
        page = preview.OUT_DIR / "manuscript.html"
        page.write_text(page.read_text().replace('</section><script>',
            '<table id="tbl:noted" style="width:60%"><caption>Table 1. Noted table.</caption><tbody><tr><td>1</td></tr></tbody></table>'
            '<div class="table-note" data-custom-style="Table Note"><p>SD, standard deviation.</p><p><em>p</em> &lt; 0.05.</p></div>'
            '</section><script>'))
        self.open()
        # The note follows the table's scroll wrapper, smaller than the caption.
        self.assertTrue(self.evaluate("document.getElementById('tbl:noted').closest('.pv-tscroll').nextElementSibling.matches('.table-note')"))
        size = "parseFloat(getComputedStyle(document.querySelector({!r})).fontSize)"
        self.assertLess(self.evaluate(size.format(".pv-page .table-note")), self.evaluate(size.format("#tbl\\:noted caption")))
        # It is as wide as its table, which stops short of the text width.
        edges = lambda table, note: self.evaluate(
            f"[{table!r}, {note!r}].flatMap((s) => {{ const r = document.querySelector(s).getBoundingClientRect(); return [r.left, r.right]; }}).map(Math.round)")
        left, right, note_left, note_right = edges("#tbl\\:noted", ".pv-page .table-note")
        self.assertEqual((left, right), (note_left, note_right))
        self.assertLess(right, self.evaluate("Math.round(document.querySelector('.pv-page').getBoundingClientRect().right)") - 40)
        self.open("?view=figures")
        card = '.pv-fcard[data-key="manuscript|tbl:noted"]'
        self.wait_for(f"document.querySelector('{card} .pv-fnote')")
        self.assertEqual(self.evaluate(f"document.querySelector('{card} .pv-fnote').title"), "SD, standard deviation. p < 0.05.")
        self.evaluate(f"document.querySelector('{card} .pv-thumb').click()")
        self.assertEqual(self.evaluate("document.querySelector('.pv-lb-note em').textContent"), "p")
        left, right, note_left, note_right = edges(".pv-lb-body table", ".pv-lb-note")
        self.assertEqual((left, right), (note_left, note_right))

    def assert_note_matches_table(self, elements):
        """The note has the edges of the visible table: the table itself, or the box that scrolls it."""
        rects = self.evaluate(f"({elements}).map((e) => {{ const r = e.getBoundingClientRect(); return [r.left, r.right]; }})")
        (left, right), (_, box_right), (note_left, note_right) = rects
        self.assertAlmostEqual(note_left, left, delta=0.5, msg=rects)
        self.assertAlmostEqual(note_right, min(right, box_right), delta=0.5, msg=rects)
        return right - left, box_right - left  # the table's width, and the room it has

    def test_table_note_follows_the_visible_width_of_a_table_that_needs_more_room(self):
        page = preview.OUT_DIR / "manuscript.html"
        # All 60% wide by Pandoc: one fits, one needs more room, one is wider than the text.
        tables = {"short": "1", "grown": "x" * 60, "scrolling": "x" * 220}
        page.write_text(page.read_text().replace('</section><script>', "".join(
            f'<table id="tbl:{name}" style="width:60%"><caption>Table {n}. {name}.</caption><tbody><tr><td>{cell}</td></tr></tbody></table>'
            '<div class="table-note"><p>SD, standard deviation.</p></div>' for n, (name, cell) in enumerate(tables.items(), 1))
            + '</section><script>'))
        in_page = lambda name: (f'(() => {{ const t = document.getElementById("tbl:{name}"), box = t.closest(".pv-tscroll");'
                                ' return [t, box, box.nextElementSibling]; })()')
        self.open()
        measured = {name: self.assert_note_matches_table(in_page(name)) for name in tables}
        # The cases differ as intended.
        self.assertLess(measured["short"][0], measured["short"][1] * 0.65)
        grown, room = measured["grown"]
        self.assertTrue(room * 0.65 < grown < room - 10, measured)
        self.assertGreaterEqual(measured["scrolling"][0], measured["scrolling"][1] + 10)
        # Narrower window and bigger text change the room and the table; the note follows.
        self.chrome.call("Emulation.setDeviceMetricsOverride", {
            "width": 800, "height": 800, "deviceScaleFactor": 1, "mobile": False}, self.session)
        frames = "new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
        self.evaluate(frames)
        for name in tables:
            self.assert_note_matches_table(in_page(name))
        for _ in range(4):
            self.evaluate("document.querySelector('.pv-font-up').click()")
        self.evaluate(frames)
        for name in tables:
            self.assert_note_matches_table(in_page(name))
        self.chrome.call("Emulation.setDeviceMetricsOverride", {
            "width": 1280, "height": 800, "deviceScaleFactor": 1, "mobile": False}, self.session)
        # The enlarged viewer has the same rule, in its own box.
        self.open("?view=figures")
        in_viewer = '[document.querySelector(".pv-lb-body table"), document.querySelector(".pv-lb-body .tscroll"), document.querySelector(".pv-lb-note")]'
        for name in tables:
            card = f'.pv-fcard[data-key="manuscript|tbl:{name}"]'
            self.wait_for(f"document.querySelector('{card} .pv-thumb')")
            self.evaluate(f"document.querySelector('{card} .pv-thumb').click()")
            self.wait_for("document.querySelector('.pv-lb-note')")
            measured[name] = self.assert_note_matches_table(in_viewer)
            self.evaluate("document.querySelector('[data-lb=close]').click()")
        self.assertLess(measured["short"][0], measured["short"][1] * 0.65)
        self.assertGreaterEqual(measured["scrolling"][0], measured["scrolling"][1] + 10)


if __name__ == "__main__":
    unittest.main()
