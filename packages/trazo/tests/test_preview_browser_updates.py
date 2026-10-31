"""Viewer regressions in Chrome: the update notice's pill and dialog
(preview-updates.js), against a fake plugin root and a data folder whose
venv is this test's own Python (see test_preview_updates.py)."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)
from _preview_browser import PreviewBrowserTestCase
from test_preview_updates import FAKE_SETUP

from trazo import preview, preview_updates

# The viewer script of a page that has not run yet has neither of these.
POLLED = "typeof loaded !== 'undefined' && loaded !== null"
PILL = "document.querySelector('.pv-update-pill')"
POP = "document.querySelector('.pv-update-pop')"


@unittest.skipIf(os.name == "nt", "the fake setup.sh is a shell script")
class UpdateNoticeTests(PreviewBrowserTestCase):
    def setUp(self):
        super().setUp()
        tmp = Path(tempfile.mkdtemp(prefix="trazo-updates-")).resolve()
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "data").mkdir()
        (tmp / "data" / "venv").symlink_to(sys.prefix)
        self.plugin = tmp / "plugin"
        (self.plugin / ".claude-plugin").mkdir(parents=True)
        script = self.plugin / preview_updates.SETUP_SCRIPT
        script.parent.mkdir(parents=True)
        script.write_text(FAKE_SETUP.format(log=tmp / "setup.log"))
        for patcher in (patch.dict(os.environ, {"TRAZO_DATA_DIR": str(tmp / "data"),
                                                "TRAZO_PLUGIN_ROOT": str(self.plugin), "CLAUDE_PLUGIN_ROOT": ""}),
                        patch.object(preview_updates, "RESTART", None)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def set_plugin(self, version):
        (self.plugin / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "trazo",
                                                                                "version": version}))
        index = self.plugin / preview_updates.PLUGIN_PACKAGE / preview_updates.TEMPLATE_INDEX
        index.parent.mkdir(parents=True, exist_ok=True)
        index.write_text(json.dumps({"template": f"v{version}"}))

    def test_nothing_shows_when_current(self):
        self.set_plugin(preview_updates.__version__)
        self.open()
        self.evaluate("fetch('/__updates').then(r => r.json())")
        self.assertTrue(self.evaluate(f"{PILL}.hidden && {POP}.hidden"))

    def test_update_from_the_pill(self):
        self.set_plugin("99.0.0")
        self.open()
        self.wait_for(f"!{PILL}.hidden")
        self.assertEqual(self.evaluate(f"{PILL}.textContent"), "99.0.0")
        # The pill sits in the top bar, before the word count.
        self.assertTrue(self.evaluate(f"{PILL}.nextElementSibling.classList.contains('pv-words')"))
        self.evaluate(f"{PILL}.click()")
        self.wait_for(f"!{POP}.hidden")
        self.assertEqual(self.evaluate(f"{POP}.innerText"), "trazo 99.0.0\nUpdates trazo.\nLater\nUpdate")
        self.evaluate(f"[...{POP}.querySelectorAll('button')].find(b => b.textContent === 'Update').click()")
        self.wait_for(f"{POP}.innerText.startsWith('Updated to 99.0.0.')")
        self.assertIn("New tools load in a new chat.", self.evaluate(f"{POP}.innerText"))
        self.evaluate("document.body.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))")
        self.assertTrue(self.evaluate(f"{POP}.hidden"))

    def test_the_page_reloads_after_a_restart(self):
        # The restart runs the new trazo in place; here it only changes the running version.
        self.set_plugin("99.0.0")
        restart = lambda: setattr(preview_updates, "__version__", "99.0.0")  # noqa: E731
        with patch.object(preview_updates, "RESTART", restart), \
                patch.object(preview_updates, "__version__", preview_updates.__version__):
            self.open()
            self.wait_for(f"!{PILL}.hidden")
            self.evaluate("window.notReloaded = true")
            self.evaluate(f"{PILL}.click()")
            self.evaluate(f"[...{POP}.querySelectorAll('button')].find(b => b.textContent === 'Update').click()")
            self.wait_for(f"!window.notReloaded && {POP} && !{POP}.hidden")
        self.assertEqual(self.evaluate(f"{POP}.innerText"), "Updated to 99.0.0.\nNew tools load in a new chat.")
        self.assertEqual(self.evaluate(f"{PILL}.className"), "pv-update-pill done")
        # Under the pill, also once the word count has moved it.
        self.wait_for(f"Math.abs({POP}.getBoundingClientRect().left - {PILL}.getBoundingClientRect().left) < 1")
        self.assertIsNone(self.evaluate("sessionStorage.getItem('trazo-updated')"))

    def bump_build(self):
        """What a rebuild of the paper does for the viewer: a new build version."""
        with preview.LOCK:
            preview.STATE["manuscript"]["version"] += 1

    def open_with_update_ready(self):
        self.set_plugin("99.0.0")
        self.open()
        self.wait_for(f"!{PILL}.hidden && {POLLED}")  # the live reload's first poll has run
        self.evaluate(f"{PILL}.click()")
        self.evaluate("window.notReloaded = true")

    def click_update(self):
        self.evaluate(f"[...{POP}.querySelectorAll('button')].find(b => b.textContent === 'Update').click()")

    def test_a_rebuild_does_not_reload_the_page_while_updating(self):
        # The update rewrites the paper and the old server is about to restart: a live reload served
        # now meets the exec and leaves a page with no scripts. The restart never comes here, so the
        # update stays in progress.
        with patch.object(preview_updates, "RESTART", lambda: None):
            self.open_with_update_ready()
            self.click_update()
            self.wait_for(f"{POP}.innerText.startsWith('Updating')")
            self.bump_build()
            # The poll that saw the new version has already decided about the reload.
            self.wait_for(f"typeof versions !== 'undefined' && versions.manuscript === {preview.STATE['manuscript']['version']}")
            self.assertTrue(self.evaluate("window.notReloaded"))

    def test_the_result_survives_a_reload_after_the_restart(self):
        # The restarted server builds the pages again, so the version moves under the fresh page.
        restart = lambda: setattr(preview_updates, "__version__", "99.0.0")  # noqa: E731
        with patch.object(preview_updates, "RESTART", restart), \
                patch.object(preview_updates, "__version__", preview_updates.__version__):
            self.open_with_update_ready()
            self.click_update()
            self.wait_for(f"!window.notReloaded && {POLLED} && {POP} && !{POP}.hidden")
            self.evaluate("window.notReloaded = true")
            self.bump_build()
            self.wait_for(f"!window.notReloaded && {POLLED} && {POP}.innerText.startsWith('Updated to 99.0.0.')")
            self.assertFalse(self.evaluate(f"{POP}.hidden"))

    def test_a_download_is_left_to_the_chat(self):
        self.set_plugin("99.0.0")
        with patch.dict(os.environ, {"FAKE_SETUP_EXIT": "3"}):
            self.open()
            self.wait_for(f"!{PILL}.hidden")
            self.evaluate(f"{PILL}.click()")
            self.evaluate(f"[...{POP}.querySelectorAll('button')].find(b => b.textContent === 'Update').click()")
            self.wait_for(f"{POP}.innerText.startsWith('Needs a download.')")
        self.assertEqual(self.evaluate(f"{POP}.innerText"), "Needs a download.\nAsk the agent to set up trazo.")

    def test_an_older_plugin_has_no_button(self):
        self.set_plugin("0.1.0")
        self.open()
        self.wait_for(f"!{PILL}.hidden")
        self.assertEqual(self.evaluate(f"{PILL}.textContent"), "Plugin")
        self.evaluate(f"{PILL}.click()")
        self.assertEqual(self.evaluate(f"{POP}.innerText"),
                         "Plugin out of date.\nUpdate trazo in the app's plugin settings.")
        self.assertEqual(self.evaluate(f"{POP}.querySelectorAll('button').length"), 0)


if __name__ == "__main__":
    unittest.main()
