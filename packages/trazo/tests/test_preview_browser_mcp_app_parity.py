"""What the browser offers that the MCP App view must offer
too: exports, the host's theme, the diagram editor's fonts and the update
notice, in Chrome as a host draws the view (see _mcp_app_view)."""

import base64
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _support
from _mcp_app_view import McpAppViewTestCase
from test_preview_updates import FAKE_SETUP

from trazo import preview_export, preview_updates

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


@unittest.skipIf(os.name == "nt", "the fake setup.sh is a shell script")
class ViewUpdateTests(McpAppViewTestCase):
    # The setup of test_preview_browser_updates.UpdateNoticeTests: a fake plugin root and a data
    # folder whose venv is this test's own Python.
    def setUp(self):
        super().setUp()
        tmp = Path(tempfile.mkdtemp(prefix="trazo-updates-")).resolve()
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "data").mkdir()
        (tmp / "data" / "venv").symlink_to(sys.prefix)
        plugin = tmp / "plugin"
        (plugin / ".claude-plugin").mkdir(parents=True)
        script = plugin / preview_updates.SETUP_SCRIPT
        script.parent.mkdir(parents=True)
        script.write_text(FAKE_SETUP.format(log=tmp / "setup.log"))
        (plugin / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "trazo", "version": "99.0.0"}))
        index = plugin / preview_updates.PLUGIN_PACKAGE / preview_updates.TEMPLATE_INDEX
        index.parent.mkdir(parents=True, exist_ok=True)
        index.write_text(json.dumps({"template": "v99.0.0"}))
        restart = lambda: setattr(preview_updates, "__version__", "99.0.0")  # noqa: E731
        for patcher in (patch.dict(os.environ, {"TRAZO_DATA_DIR": str(tmp / "data"),
                                                "TRAZO_PLUGIN_ROOT": str(plugin), "CLAUDE_PLUGIN_ROOT": ""}),
                        patch.object(preview_updates, "RESTART", restart),
                        patch.object(preview_updates, "__version__", preview_updates.__version__)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_page_reloads_in_the_view_after_an_update(self):
        pill, pop = "d.querySelector('.pv-update-pill')", "d.querySelector('.pv-update-pop')"
        self.host(sandbox=False)
        self.wait_frame(f"{READY} && !{pill}.hidden")
        self.frame("w.notReloaded = true")
        self.frame(f"{pill}.click()")
        self.frame(f"[...{pop}.querySelectorAll('button')].find(b => b.textContent === 'Update').click()")
        self.wait_frame(f"!w.notReloaded && {pop} && !{pop}.hidden")
        self.assertEqual(self.frame(f"{pop}.innerText"), "Updated to 99.0.0.\nNew tools load in a new chat.")
        self.assertEqual(self.frame("v.TRAZO_VIEW.path"), "/manuscript.html")
