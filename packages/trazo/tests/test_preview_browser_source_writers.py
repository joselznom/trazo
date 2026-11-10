"""The Source tab in Chrome tells the author when another program wrote a file during a save."""

import json
import shutil
import unittest
from unittest.mock import patch

import _support
from _preview_browser import PreviewBrowserTestCase

from trazo import portable, preview, preview_sources

FILE = "manuscript/main/01_introduction.md"
MARKER = "Text another program saved meanwhile."


class SourceWriterNoticeTests(PreviewBrowserTestCase):
    def test_a_collision_keeps_both_versions_and_a_disk_without_exchange_says_so_once(self):
        source = _support.ROOT / FILE
        self.keep_files(source, preview.FILE_HISTORY_PATH)
        for kept in ("conflicts", "displaced"):
            self.addCleanup(shutil.rmtree, preview.REVIEW_DIR / kept, True)
        page = preview.OUT_DIR / "manuscript.html"
        page.write_text(page.read_text().replace("window.PREVIEW_FILES = [];", "window.PREVIEW_FILES = "
                                                 + json.dumps([{"file": FILE, "title": "Introduction"}]) + ";"))
        original = preview_sources.save_file_history

        def history_then_write(entries):
            original(entries)  # inside the save, after its locked read
            source.write_text(source.read_text() + f"\n{MARKER}\n")
        with patch.object(portable, "filesystem_type", lambda folder: "exfat"):
            self.open()
            self.evaluate("document.querySelector('[data-tab=source]').click()")
            self.wait_for("document.querySelector('.pv-src-text')?.value.includes('Introduction')")
            inexact = "document.querySelector('[data-note=inexact]')"
            self.wait_for(f"!{inexact}.hidden && {inexact}.textContent.includes('cannot swap files atomically')")
            self.evaluate(f"{inexact}.querySelector('button').click()")
            self.wait_for(f"{inexact}.hidden")
            self.evaluate("""(() => {
              const area = document.querySelector('.pv-src-text');
              area.value += '\\nThe browser paragraph.\\n';
              area.dispatchEvent(new Event('input', {bubbles: true}));
            })()""")
            with patch.object(preview_sources, "save_file_history", history_then_write):
                self.evaluate("document.querySelector('.pv-src-bar [data-op=save]').click()")
                self.wait_for("!document.querySelector('.pv-src-conflict').hidden")
            banner = self.evaluate("document.querySelector('.pv-src-conflict span').textContent")
            self.assertIn("Another program changed this file while it was being saved", banner)
            copies = sorted(p.relative_to(_support.ROOT).as_posix()
                            for p in (preview.REVIEW_DIR / "conflicts").rglob("*") if p.is_file())
            self.assertEqual(len(copies), 1)
            self.assertIn(copies[0], banner)
            self.assertIn("The browser paragraph.", (_support.ROOT / copies[0]).read_text())
            self.assertIn(MARKER, source.read_text())
            self.wait_for(f"document.querySelector('[data-note=copies]').textContent.includes({json.dumps(copies[0])})")
            self.assertTrue(self.evaluate("document.querySelector('.pv-src-text').value.includes('The browser paragraph.')"))
            self.open()  # the same tab session: the disk notice stays dismissed
            self.evaluate("document.querySelector('[data-tab=source]').click()")
            self.wait_for("!document.querySelector('[data-note=copies]').hidden")
            self.assertTrue(self.evaluate(f"{inexact}.hidden"))


if __name__ == "__main__":
    unittest.main()
