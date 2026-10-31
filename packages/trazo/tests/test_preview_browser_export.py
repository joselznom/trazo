"""Viewer regressions in Chrome: section exports and export indexes."""

import shutil
import time
import unittest
import zipfile

import _support
from _preview_browser import PreviewBrowserTestCase

from trazo import preview


class ExportTests(PreviewBrowserTestCase):
    def test_current_section_export_releases_picker_controls_after_failure(self):
        self.open()
        self.evaluate("""(() => {
          const fetch = window.fetch;
          window.fetch = (url, options) => String(url).startsWith('/__export?')
            ? new Promise((resolve, reject) => { window.failExport = () => reject(new TypeError('Connection lost')); })
            : fetch(url, options);
          document.querySelector('.pv-export-toggle').click();
          document.querySelector('.pv-mi[data-act=current]').click();
          document.querySelector('.pv-export-toggle').click();
          document.querySelector('.pv-mi[data-act=choose]').click();
        })()""")
        self.assertTrue(self.evaluate("document.querySelector('.pv-export-chip[data-preset=all]').disabled"))
        self.evaluate("window.failExport()")
        self.wait_for("!document.querySelector('.pv-export-chip[data-preset=all]').disabled")
        self.assertFalse(self.evaluate("document.querySelector('.pv-export-panel').hidden"))

    def test_section_export_picker_replaces_inline_actions_and_handles_keyboard(self):
        page = preview.OUT_DIR / "manuscript.html"
        page.write_text(page.read_text().replace('</section><script>',
            '<section class="level2" id="detail"><h2>Detail</h2><p>Details.</p></section></section>'
            '<section class="level1 unnumbered" id="results"><h1>Results</h1><p>Findings.</p>'
            '<div id="refs"><p>Bibliography at the end of an unnumbered section.</p></div></section><script>'))
        self.open()
        self.assertFalse(self.evaluate("!!document.querySelector('section > .pv-exports')"))
        checked = "Object.fromEntries(Array.from(document.querySelectorAll('.pv-xnode'), e => [e.dataset.id, e.getAttribute('aria-checked')]))"
        self.evaluate("document.querySelector('.pv-export-toggle').click()")
        self.assertFalse(self.evaluate("document.querySelector('.pv-export-menu').hidden"))
        self.assertTrue(self.evaluate("document.activeElement.matches('.pv-docx')"))
        self.evaluate("document.querySelector('.pv-mi[data-act=choose]').click()")
        self.assertTrue(self.evaluate("document.querySelector('.pv-export-menu').hidden"))
        self.assertTrue(self.evaluate("document.activeElement.matches('.pv-export-filter')"))
        self.assertEqual(self.evaluate("Array.from(document.querySelectorAll('.pv-xnode'), e => e.dataset.id)"),
                         ["intro", "detail", "results"])
        self.assertTrue(self.evaluate("document.querySelector('.pv-export-submit').disabled"))
        self.evaluate("document.querySelector('.pv-xnode[data-id=intro]').click()")
        self.assertEqual(self.evaluate(checked), {"intro": "true", "detail": "true", "results": "false"})
        self.assertEqual(self.evaluate("document.querySelector('.pv-export-submit').textContent"), "Export 1 section")
        # Unticking a subsection keeps the rest of its parent.
        self.evaluate("document.querySelector('.pv-xnode[data-id=detail]').click()")
        self.assertEqual(self.evaluate(checked), {"intro": "mixed", "detail": "false", "results": "false"})
        self.assertIn("(part)", self.evaluate("document.querySelector('.pv-export-pills').textContent"))
        self.evaluate("document.querySelector('.pv-xnode[data-id=detail]').focus();"
                      "document.activeElement.dispatchEvent(new KeyboardEvent('keydown', {key:' ', bubbles:true}))")
        self.assertEqual(self.evaluate(checked), {"intro": "true", "detail": "true", "results": "false"})
        self.evaluate("document.querySelector('.pv-export-filter').value = 'res';"
                      "document.querySelector('.pv-export-filter').dispatchEvent(new Event('input'))")
        self.assertEqual(self.evaluate("Array.from(document.querySelectorAll('.pv-xnode:not([hidden])'), e => e.dataset.id)"),
                         ["results"])
        self.evaluate("document.querySelector('.pv-export-chip[data-preset=all]').click()")
        self.assertEqual(self.evaluate(checked), {"intro": "true", "detail": "true", "results": "true"})
        self.assertEqual(self.evaluate("document.querySelector('.pv-export-submit').textContent"), "Export 2 sections")
        self.evaluate("Array.from(document.querySelectorAll('.pv-export-panel button')).find(b => b.textContent === 'Clear').click()")
        self.assertEqual(set(self.evaluate(checked).values()), {"false"})
        self.assertTrue(self.evaluate("document.querySelector('.pv-export-submit').disabled"))
        self.evaluate("document.activeElement.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true}))")
        self.assertTrue(self.evaluate("document.querySelector('.pv-export-panel').hidden"))
        self.assertTrue(self.evaluate("document.activeElement.matches('.pv-export-toggle')"))

    def test_export_indexes_are_per_document_and_offered_only_with_entries(self):
        # The fixture: one heading and two figures, no table.
        options = """(where => [...document.querySelectorAll(where + ' [data-index]')].map(e =>
          [e.dataset.index, e.getAttribute('aria-pressed') === 'true', e.getAttribute('aria-disabled') === 'true']))"""
        summary = "document.querySelector('.pv-index-toggle').textContent"
        self.open()
        self.evaluate("document.querySelector('.pv-export-toggle').click()")
        # The pills are folded under a line that says what the build adds.
        self.assertEqual(self.evaluate(summary), "No indexes")
        self.assertTrue(self.evaluate("document.querySelector('.pv-index-fold').hidden"))
        self.evaluate("document.querySelector('.pv-index-toggle').click()")
        self.assertFalse(self.evaluate("document.querySelector('.pv-index-fold').hidden"))
        self.assertEqual(self.evaluate(options + "('.pv-export-menu')"),
                         [["toc", False, True], ["lof", False, False], ["lot", False, True]])
        self.assertIn("needs at least two headings", self.evaluate(
            "document.querySelector('.pv-export-menu [data-index=toc]').title"))
        # A pill with nothing to list cannot be pressed.
        self.evaluate("document.querySelector('.pv-export-menu [data-index=toc]').click()")
        self.assertEqual(self.evaluate(options + "('.pv-export-menu')")[0], ["toc", False, True])
        self.evaluate("document.querySelector('.pv-export-menu [data-index=lof]').click()")
        self.assertFalse(self.evaluate("document.querySelector('.pv-export-menu').hidden"))
        self.assertEqual(self.evaluate(summary), "With figure list")
        # The picker counts only the chosen sections.
        self.evaluate("document.querySelector('[data-act=choose]').click()")
        self.assertEqual(self.evaluate(options + "('.pv-export-panel')"),
                         [["toc", False, True], ["lof", False, True], ["lot", False, True]])
        self.evaluate("document.querySelector('.pv-xnode[data-id=intro]').click()")
        self.assertEqual(self.evaluate(options + "('.pv-export-panel')"),
                         [["toc", False, True], ["lof", True, False], ["lot", False, True]])
        self.assertEqual(self.evaluate("document.querySelector('.pv-export-panel [data-index=lof]').textContent"), "Figures2")
        self.assertEqual(self.evaluate("JSON.parse(localStorage.getItem('trazo-preview')).exportIndexes"),
                         {"manuscript": {"toc": False, "lof": True, "lot": False}})
        # Only applicable indexes reach the export.
        self.evaluate("""window.exported = null; window.fetch = (url) => {
          window.exported = String(url); return Promise.resolve(new Response(new Blob(['x']), {status: 200})); };
          document.querySelector('.pv-export-submit').click()""")
        self.wait_for("window.exported")
        query = self.evaluate("Object.fromEntries(new URLSearchParams(window.exported.split('?')[1]))")
        self.assertEqual((query["toc"], query["lof"], query["lot"]), ("0", "1", "0"))
        # Each document keeps its own choice.
        self.chrome.call("Page.navigate", {"url": self.url.replace("/manuscript.html", "/supplementary.html")}, self.session)
        self.wait_for("document.querySelector('.pv-bs-label')?.textContent.length > 0 && document.readyState === 'complete'")
        self.evaluate("document.querySelector('.pv-export-toggle').click()")
        self.assertEqual(self.evaluate("document.querySelector('.pv-docx .pv-mi-t').textContent"), "Full supplementary")
        self.assertEqual(self.evaluate(summary), "No indexes")
        self.assertEqual(self.evaluate(options + "('.pv-export-menu')")[1], ["lof", False, False])
        self.open()
        self.evaluate("document.querySelector('.pv-export-toggle').click()")
        self.assertEqual(self.evaluate(summary), "With figure list")
        self.assertEqual(self.evaluate(options + "('.pv-export-menu')")[1], ["lof", True, False])

    @unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
    def test_full_build_offers_authors_only_with_a_separate_title_page(self):
        from unittest.mock import patch

        from trazo import server
        from trazo.project import DEFAULTS, Project, merge
        project = Project(preview.OUT_DIR, merge(DEFAULTS, {"submission": {"title_page": "separate"}}), True)
        patcher = patch.object(server, "PROJECT", project)
        patcher.start()
        self.addCleanup(patcher.stop)
        summary = "document.querySelector('.pv-index-toggle').textContent"
        self.open()
        self.evaluate("document.querySelector('.pv-export-toggle').click();"
                      "document.querySelector('.pv-index-toggle').click()")
        self.assertEqual(self.evaluate(summary), "No authors or indexes")
        self.assertEqual(self.evaluate("[...document.querySelectorAll('.pv-export-menu [data-index]')].map(e => e.dataset.index)"),
                         ["authors", "toc", "lof", "lot"])
        self.evaluate("document.querySelector('.pv-export-menu [data-index=authors]').click();"
                      "document.querySelector('.pv-export-menu [data-index=lof]').click()")
        self.assertEqual(self.evaluate(summary), "With authors and figure list")
        # The section picker never offers it: section exports have no title block.
        self.evaluate("document.querySelector('[data-act=choose]').click()")
        self.assertFalse(self.evaluate("!!document.querySelector('.pv-export-panel [data-index=authors]')"))
        self.evaluate("""window.built = null; window.realFetch = window.realFetch || window.fetch;
          window.fetch = (url, options) => String(url) === '/__build-docx'
            ? (window.built = JSON.parse(options.body), Promise.resolve(new Response('{}', {status: 409})))
            : window.realFetch(url, options);
          document.querySelector('.pv-docx').click()""")
        self.wait_for("window.built")
        self.assertEqual(self.evaluate("window.built"),
                         {"doc": "manuscript", "toc": False, "lof": True, "lot": False, "authors": True})

    def test_section_picker_downloads_one_docx_with_every_selected_heading(self):
        for name, text in {
            "97_picker_first": "# Picker first\n\nFirst chosen text.\n\n## Picker child\n\nChild left out.\n\n"
                               "## Picker kept\n\nKept chosen text.\n",
            "98_picker_omitted": "# Picker omitted\n\nOmitted text.\n",
            "99_picker_last": "# Picker last\n\nLast chosen text $x^2$.\n",
        }.items():
            source = _support.ROOT / f"manuscript/main/{name}.md"
            source.write_text(text)
            self.addCleanup(source.unlink, missing_ok=True)
        self.assertTrue(preview.build("manuscript"), preview.STATE["manuscript"]["error"])
        downloads = preview.OUT_DIR / "downloads"
        downloads.mkdir()
        self.chrome.call("Browser.setDownloadBehavior", {"behavior": "allow",
            "browserContextId": self.context, "downloadPath": str(downloads)})
        self.open()
        self.evaluate("""(() => {
          const fetch = window.fetch;
          window.fetch = (url, options) => String(url).startsWith('/__export?')
            ? new Promise(resolve => { window.resumeExport = () => resolve(fetch(url, options)); })
            : fetch(url, options);
          document.querySelector('.pv-export-toggle').click();
          document.querySelector('.pv-mi[data-act=choose]').click();
          document.querySelector('.pv-xnode[data-id=picker-last]').click();
          document.querySelector('.pv-xnode[data-id=picker-first]').click();
          document.querySelector('.pv-xnode[data-id=picker-child]').click();
          document.querySelector('.pv-export-submit').click();
        })()""")
        self.evaluate("document.querySelector('.pv-export-pill button').click()")
        self.assertEqual(self.evaluate("document.querySelector('.pv-xnode[data-id=picker-first]').getAttribute('aria-checked')"), "mixed")
        self.evaluate("window.resumeExport()")
        deadline = time.monotonic() + 15
        files = []
        while time.monotonic() < deadline:
            files = list(downloads.glob("*.docx"))
            if files:
                break
            time.sleep(0.05)
        self.assertEqual(len(files), 1)
        with zipfile.ZipFile(files[0]) as docx:
            xml = docx.read("word/document.xml")
        self.assertIn(b"First chosen text", xml)
        self.assertIn(b"Kept chosen text", xml)
        self.assertNotIn(b"Child left out", xml)
        self.assertIn(b"Last chosen text", xml)
        self.assertNotIn(b"Omitted text", xml)
        self.wait_for("document.querySelector('.pv-export-panel').hidden")
        self.assertEqual(set(self.evaluate("JSON.parse(localStorage.getItem('trazo-preview')).lastExport.manuscript")),
                         {"picker-first", "picker-kept", "picker-last"})


if __name__ == "__main__":
    unittest.main()
