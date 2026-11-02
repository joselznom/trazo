"""What the browser offers that the MCP App view must offer
too: exports, the host's theme and the diagram editor's fonts, in Chrome as a host draws the view (see _mcp_app_view)."""

import base64
import json
from pathlib import Path
from unittest.mock import patch

import _support
from _mcp_app_view import McpAppViewTestCase

from trazo import preview_export

DOCX = b"PK\x03\x04 a Word file"
EXPORT = (DOCX, "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "manuscript-intro.docx")
READY = "d?.querySelector('.pv-bs-label')?.textContent.length > 0"


class ViewParityTests(McpAppViewTestCase):
    def export(self):
        # preview_export.export runs Pandoc; the view's part starts with its bytes.
        patcher = patch.object(preview_export, "export", lambda *args: EXPORT)
        patcher.start()
        self.addCleanup(patcher.stop)
        saved = Path(_support.ROOT) / "output" / EXPORT[2]  # beside the manuscript's Word file
        self.keep_files(saved)
        saved.unlink(missing_ok=True)
        return saved

    def test_an_export_is_saved_in_the_paper(self):
        saved = self.export()
        self.host(sandbox=False)
        self.wait_frame(READY)
        self.frame("w.downloadExport('manuscript', 'section', ['intro'], 'docx')")
        self.wait_frame("v.document.getElementById('trazo-view-toast')?.textContent"
                        ".includes('Saved in the paper: output/manuscript-intro.docx')")
        self.assertEqual(saved.read_bytes(), DOCX)
        self.assertEqual(self.evaluate("window.downloads"), [])

    def test_an_export_goes_to_a_host_that_downloads(self):
        saved = self.export()
        self.host(sandbox=False, capabilities={"downloadFile": {}})
        self.wait_frame(READY)
        self.frame("w.downloadExport('manuscript', 'section', ['intro'], 'docx')")
        self.wait_for("window.downloads.length === 1")
        content, = self.evaluate("window.downloads")[0]["contents"]
        self.assertEqual(content["type"], "resource")
        self.assertEqual(content["resource"]["uri"], "file:///manuscript-intro.docx")
        self.assertEqual(content["resource"]["mimeType"], EXPORT[1])
        self.assertEqual(base64.b64decode(content["resource"]["blob"]), DOCX)
        self.assertFalse(saved.exists())

    def test_an_export_the_host_refuses_is_saved_in_the_paper(self):
        saved = self.export()
        self.host(sandbox=False, capabilities={"downloadFile": {}})
        self.wait_frame(READY)
        self.evaluate("window.downloadResult = {isError: true}")
        self.frame("w.downloadExport('manuscript', 'section', ['intro'], 'docx')")
        self.wait_frame("v.document.getElementById('trazo-view-toast')?.textContent.includes('Saved in the paper: ')")
        self.assertEqual(len(self.evaluate("window.downloads")), 1)
        self.assertEqual(saved.read_bytes(), DOCX)

    def test_the_automatic_theme_follows_the_host(self):
        self.host(sandbox=False, context={"theme": "dark"})
        self.wait_frame(READY)
        self.assertEqual(self.frame("d.documentElement.dataset.theme"), "dark")
        self.evaluate("sendContext({theme: 'light'})")
        self.wait_frame("d.documentElement.dataset.theme === 'light'")
        # A theme the reader chose wins over the host's.
        self.frame("w.Trazo.savePrefs({theme: 'dark'})")
        self.evaluate("sendContext({theme: 'light'})")
        self.wait_frame("d.documentElement.dataset.theme === 'dark'")

    def test_the_diagram_editor_has_its_figure_fonts(self):
        tokens = json.loads((Path(_support.ROOT) / "figures" / "style" / "tokens.json").read_text())
        family = tokens["fonts"]["family"]
        self.host(sandbox=False)
        self.wait_frame(READY)
        self.frame("v.TRAZO_VIEW.assign('/diagrams#example')")
        self.wait_frame("v.TRAZO_VIEW.path === '/diagrams' && d?.querySelector('#paper svg')")
        self.wait_frame(f"[...d.fonts].some(f => f.family.replace(/\"/g, '') === {json.dumps(family)}"
                        " && f.status === 'loaded')")
        # The editor's own @font-face rules would point at URLs the CSP blocks.
        self.assertEqual(self.frame("d.getElementById('pk-figure-fonts')?.textContent ?? ''"), "")
