"""Viewer regressions in Chrome: document zoom, pinch zoom and text size."""

import subprocess
import unittest
from unittest.mock import patch

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)
from _preview_browser import PreviewBrowserTestCase

from trazo import browser, preview


class ZoomTests(PreviewBrowserTestCase):
    def test_document_zoom_scales_content_preserves_reading_position_and_fits_resized_panel(self):
        page = preview.OUT_DIR / "manuscript.html"
        page.write_text(page.read_text().replace('<p>Another paragraph.</p>',
            '<p>Another paragraph.</p>' + '<p>More manuscript text for scrolling.</p>' * 50))
        self.open()
        self.evaluate("document.querySelector('.pv-zoom-reset').click()")
        before = self.evaluate("""(() => {
          const p = document.querySelector('.pv-page p:nth-of-type(15)');
          const s = document.querySelector('.pv-scroller');
          s.scrollTop = p.getBoundingClientRect().top - s.getBoundingClientRect().top + s.scrollTop;
          return {width: document.querySelector('.pv-page').getBoundingClientRect().width,
            top: p.getBoundingClientRect().top, font: p.getBoundingClientRect().height,
            bar: document.querySelector('.pv-topbar').getBoundingClientRect().height};
        })()""")
        self.evaluate("document.querySelector('.pv-zoom-in').click()")
        after = self.evaluate("""(() => {
          const p = document.querySelector('.pv-page p:nth-of-type(15)');
          return {width: document.querySelector('.pv-page').getBoundingClientRect().width,
            top: p.getBoundingClientRect().top, font: p.getBoundingClientRect().height,
            bar: document.querySelector('.pv-topbar').getBoundingClientRect().height};
        })()""")
        self.assertAlmostEqual(after["width"] / before["width"], 1.1, places=2)
        self.assertAlmostEqual(after["font"] / before["font"], 1.1, places=2)
        self.assertAlmostEqual(after["top"], before["top"], delta=2)
        self.assertEqual(after["bar"], before["bar"])
        self.open()
        self.assertEqual(self.evaluate("document.querySelector('.pv-zoom-reset').textContent"), "110%")
        self.evaluate("for (let i = 0; i < 20; i++) document.querySelector('.pv-zoom-in').click()")
        self.assertEqual(self.evaluate("document.querySelector('.pv-zoom-reset').textContent"), "200%")
        self.assertTrue(self.evaluate("document.querySelector('.pv-zoom-in').disabled"))
        self.evaluate("for (let i = 0; i < 20; i++) document.querySelector('.pv-zoom-out').click()")
        self.assertEqual(self.evaluate("document.querySelector('.pv-zoom-reset').textContent"), "50%")
        self.assertTrue(self.evaluate("document.querySelector('.pv-zoom-out').disabled"))
        self.evaluate("document.querySelector('.pv-zoom-fit').click()")
        self.wait_for("document.querySelector('.pv-scroller').scrollWidth <= document.querySelector('.pv-scroller').clientWidth + 1")
        self.assertEqual(self.evaluate("parseFloat(getComputedStyle(document.querySelector('.pv-page')).fontSize)"), 14)
        self.evaluate("document.querySelector('[data-side=right]').click()")
        self.wait_for("Math.abs(document.querySelector('.pv-page').getBoundingClientRect().width - "
                      "Math.min(860, document.querySelector('.pv-scroller').clientWidth - 48)) < 2")

    def test_pinch_zoom_keeps_the_point_under_the_pointer_in_each_view(self):
        page = preview.OUT_DIR / "manuscript.html"
        page.write_text(page.read_text().replace('<p>Another paragraph.</p>',
            '<p>Another paragraph.</p>' + '<p>More manuscript text for scrolling.</p>' * 50))
        self.open()
        self.evaluate("document.querySelector('.pv-zoom-reset').click()")
        pinch = """((deltaY) => {
          const p = document.querySelector('.pv-page p:nth-of-type(20)'), box = p.getBoundingClientRect();
          const x = box.left + box.width / 3, y = box.top + box.height / 2;
          const e = new WheelEvent('wheel', {ctrlKey: true, deltaY, clientX: x, clientY: y, bubbles: true, cancelable: true});
          document.querySelector('.pv-scroller').dispatchEvent(e);
          const after = p.getBoundingClientRect();
          return {prevented: e.defaultPrevented, dx: after.left + after.width / 3 - x, dy: after.top + after.height / 2 - y,
            label: document.querySelector('.pv-zoom-reset').textContent};
        })"""
        self.evaluate("(() => { const p = document.querySelector('.pv-page p:nth-of-type(20)'), s = document.querySelector('.pv-scroller');"
                      " s.scrollTop += p.getBoundingClientRect().top - s.getBoundingClientRect().top - 200; })()")
        for _ in range(5):
            step = self.evaluate(pinch + "(-4)")
            self.assertTrue(step["prevented"])
            self.assertAlmostEqual(step["dx"], 0, delta=2)
            self.assertAlmostEqual(step["dy"], 0, delta=2)
        self.assertEqual(step["label"], "122%")
        # One event (a mouse wheel notch) moves at most about 10 %.
        self.assertEqual(self.evaluate(pinch + "(-5000)")["label"], "135%")
        self.assertEqual([self.evaluate(pinch + "(5000)")["label"] for _ in range(3)], ["122%", "111%", "100%"])
        self.evaluate(pinch + "(-50)")
        self.evaluate("new Promise(resolve => setTimeout(resolve, 400))")
        self.open()
        self.assertEqual(self.evaluate("document.querySelector('.pv-zoom-reset').textContent"), "111%")
        # Figures & Tables keeps its own scale: the cards grow, without Fit width or text size.
        self.open("?view=figures")
        self.wait_for("document.querySelectorAll('.pv-fcard').length === 4")
        self.evaluate("new Promise(resolve => setTimeout(resolve, 500))")
        self.assertEqual(self.evaluate("[document.querySelector('.pv-zoom-reset').textContent,"
                                       " document.querySelector('.pv-zoom-fit').hidden, document.querySelector('.pv-type').hidden]"),
                         ["100%", True, True])
        card = "document.querySelector('.pv-fcard').getBoundingClientRect().width"
        width = self.evaluate(card)
        wheel = """((target) => { const box = target.getBoundingClientRect();
          const e = new WheelEvent('wheel', {ctrlKey: true, deltaY: -20, clientX: box.left + 10, clientY: box.top + 10, bubbles: true, cancelable: true});
          target.dispatchEvent(e); return e.defaultPrevented; })"""
        self.evaluate("(() => { const thumb = document.querySelectorAll('.pv-fcard')[2].querySelector('.pv-thumb'),"
                      " scroller = document.querySelector('.pv-scroller');"
                      " scroller.scrollTop += thumb.getBoundingClientRect().top - scroller.getBoundingClientRect().top - 70; })()")
        for delta in [-20, 20, -20]:
            with self.subTest(gallery_delta=delta):
                step = self.evaluate("""(async (deltaY) => {
                  const thumb = document.querySelectorAll('.pv-fcard')[2].querySelector('.pv-thumb');
                  const before = thumb.getBoundingClientRect();
                  const x = before.left + before.width / 2, y = before.top + before.height / 2;
                  const event = new WheelEvent('wheel', {ctrlKey: true, deltaY, clientX: x, clientY: y, bubbles: true, cancelable: true});
                  thumb.dispatchEvent(event);
                  await new Promise(resolve => setTimeout(resolve, 400));
                  const after = document.querySelectorAll('.pv-fcard')[2].querySelector('.pv-thumb').getBoundingClientRect();
                  return {prevented: event.defaultPrevented, dx: after.left + after.width / 2 - x,
                    dy: after.top + after.height / 2 - y};
                })""" + f"({delta})")
                self.assertTrue(step["prevented"])
                self.assertAlmostEqual(step["dx"], 0, delta=2)
                self.assertAlmostEqual(step["dy"], 0, delta=2)
        self.assertEqual(self.evaluate("document.querySelector('.pv-zoom-reset').textContent"), "111%")
        self.assertGreater(self.evaluate(card), width)
        self.evaluate("document.querySelector('.pv-zoom-in').click()")
        self.assertEqual(self.evaluate("document.querySelector('.pv-zoom-reset').textContent"), "121%")
        self.open("?view=figures")
        self.assertEqual(self.evaluate("document.querySelector('.pv-zoom-reset').textContent"), "121%")
        self.wait_for("document.querySelector('.pv-thumb')")
        self.evaluate("document.querySelector('.pv-fcard [data-a=go]').click()")
        self.assertEqual(self.evaluate("document.querySelector('.pv-zoom-reset').textContent"), "111%")
        # The lightbox leaves the pinch to the browser.
        self.open("?view=figures")
        self.wait_for("document.querySelector('.pv-thumb')")
        self.evaluate("document.querySelector('.pv-thumb').click()")
        self.assertFalse(self.evaluate(wheel + "(document.querySelector('.pv-lb-body'))"))

    def test_text_size_reflows_preserves_position_and_remembers_limits(self):
        page = preview.OUT_DIR / "manuscript.html"
        page.write_text(page.read_text().replace('<p>Another paragraph.</p>',
            '<p>Another paragraph.</p>' + '<p>' + 'More manuscript text for reading. ' * 12 + '</p>'
            + ('<p>' + 'More manuscript text for scrolling. ' * 12 + '</p>') * 40
            + '<table><caption>Table caption.</caption><thead><tr><th>Header</th></tr></thead>'
              '<tbody><tr><td>Cell text</td></tr></tbody></table>').replace(
            'src="figures/external/ordinary.png"',
            'src="data:image/svg+xml,%3Csvg xmlns=\'http://www.w3.org/2000/svg\' width=\'180\' height=\'60\'/%3E"'))
        self.open()
        sizes = """(() => {
          const size = selector => parseFloat(getComputedStyle(document.querySelector(selector)).fontSize);
          return {body: size('.pv-page p'), header: size('.pv-page th'), cell: size('.pv-page td'),
            figure: size('.pv-page figcaption'), table: size('.pv-page caption')};
        })()"""
        self.assertEqual(self.evaluate(sizes),
                         {"body": 14, "header": 11.48, "cell": 11.48, "figure": 12.04, "table": 12.04})
        before = self.evaluate("""(() => {
          const p = document.querySelector('.pv-page p:nth-of-type(15)');
          const s = document.querySelector('.pv-scroller');
          s.scrollTop = p.getBoundingClientRect().top - s.getBoundingClientRect().top + s.scrollTop;
          return {top: p.getBoundingClientRect().top, height: p.getBoundingClientRect().height,
            width: document.querySelector('.pv-page').getBoundingClientRect().width,
            image: document.querySelector('.pv-page img').getBoundingClientRect().width,
            controls: getComputedStyle(document.querySelector('.pv-topbar')).fontSize};
        })()""")
        self.evaluate("document.querySelector('.pv-type summary').click(); document.querySelector('.pv-font-down').click()")
        after = self.evaluate("""(() => {
          const p = document.querySelector('.pv-page p:nth-of-type(15)');
          return {top: p.getBoundingClientRect().top, height: p.getBoundingClientRect().height,
            width: document.querySelector('.pv-page').getBoundingClientRect().width,
            image: document.querySelector('.pv-page img').getBoundingClientRect().width,
            controls: getComputedStyle(document.querySelector('.pv-topbar')).fontSize};
        })()""")
        self.assertAlmostEqual(after["top"], before["top"], delta=2)
        self.assertLess(after["height"], before["height"])
        for key in ("width", "image", "controls"):
            self.assertEqual(after[key], before[key], key)
        self.assertEqual(self.evaluate(sizes)["body"], 13)
        self.evaluate("for (let i = 0; i < 20; i++) document.querySelector('.pv-font-down').click()")
        self.assertEqual(set(self.evaluate(sizes).values()), {10})
        self.assertTrue(self.evaluate("document.querySelector('.pv-font-down').disabled"))
        self.open()
        self.assertEqual(self.evaluate(sizes)["body"], 10)
        self.chrome.call("Page.navigate", {"url": self.url.replace('manuscript', 'supplementary')}, self.session)
        self.wait_for("document.querySelector('.pv-font-value')?.textContent === '10 px'")
        self.assertEqual(self.evaluate("parseFloat(getComputedStyle(document.querySelector('.pv-page p')).fontSize)"), 10)
        self.open()
        self.evaluate("document.querySelector('.pv-type summary').click();"
                      "for (let i = 0; i < 20; i++) document.querySelector('.pv-font-up').click()")
        upper = self.evaluate(sizes)
        for key, expected in {"body": 22, "header": 18.04, "cell": 18.04, "figure": 18.92, "table": 18.92}.items():
            self.assertAlmostEqual(upper[key], expected, places=2)
        self.assertTrue(self.evaluate("document.querySelector('.pv-font-up').disabled"))
        self.evaluate("document.querySelector('.pv-zoom-in').click(); document.querySelector('.pv-zoom-fit').click();"
                      "document.querySelector('[data-side=right]').click()")
        self.wait_for("Math.abs(document.querySelector('.pv-page').getBoundingClientRect().width - "
                      "Math.min(860, document.querySelector('.pv-scroller').clientWidth - 48)) < 2")
        self.assertEqual(self.evaluate(sizes)["body"], 22)
        self.chrome.call("Emulation.setDeviceMetricsOverride", {
            "width": 800, "height": 800, "deviceScaleFactor": 1, "mobile": False}, self.session)
        self.wait_for("document.querySelector('.pv-app-root').classList.contains('pv-narrow')")
        self.assertEqual(self.evaluate(sizes)["body"], 22)
        self.evaluate("document.querySelector('.pv-type summary').click(); document.querySelector('.pv-font-reset').click()")
        self.assertEqual(self.evaluate(sizes)["body"], 14)
        self.evaluate("document.querySelector('.pv-font-reset').dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true}))")
        self.assertTrue(self.evaluate("!document.querySelector('.pv-type').open && document.activeElement.matches('.pv-type summary')"))


class ClassicScrollbarTests(PreviewBrowserTestCase):
    """Windows, Linux and macOS with "always show scroll bars" give the
    scrollbar its own width, which headless Chrome hides by default."""

    @classmethod
    def setUpClass(cls):
        start = subprocess.Popen
        def popen(args, *rest, **options):
            return start([arg for arg in args if arg != "--hide-scrollbars"], *rest, **options)
        with patch.object(browser.subprocess, "Popen", popen):
            super().setUpClass()

    def test_a_fixed_zoom_reloads_at_the_width_beside_the_scrollbar(self):
        # A fixed zoom keeps the width read at load; Fit width would re-fit.
        page = preview.OUT_DIR / "manuscript.html"
        page.write_text(page.read_text().replace(
            '</head>', '<style>.pv-scroller::-webkit-scrollbar { width: 15px; }</style></head>').replace(
            '<p>Another paragraph.</p>',
            '<p>Another paragraph.</p>' + '<p>More manuscript text for scrolling.</p>' * 80))
        self.open()
        self.evaluate("document.querySelector('.pv-zoom-reset').click()")
        self.open()
        bar, expected, actual = self.evaluate("""(() => { const s = document.querySelector('.pv-scroller');
          return [s.offsetWidth - s.clientWidth, Math.min(860, s.clientWidth - 48),
            parseFloat(getComputedStyle(document.querySelector('.pv-page')).width)]; })()""")
        self.assertEqual(bar, 15)
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
