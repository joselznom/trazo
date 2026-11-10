"""How often an idle preview page asks the server, in Chrome."""

import unittest
from unittest.mock import patch

from _preview_browser import PreviewBrowserTestCase

from trazo import preview

ASKED = ("performance.getEntriesByType('resource').map(e => new URL(e.name).pathname)"
         ".filter(path => path === '/__workspace' || path === '/__status').length")


class IdleTests(PreviewBrowserTestCase):
    def test_an_idle_page_asks_about_twice_a_second_and_still_reloads_at_a_build(self):
        self.open()
        self.wait_for("performance.getEntriesByType('resource')"
                      ".filter(e => new URL(e.name).pathname === '/__workspace').length >= 2")
        self.evaluate("performance.clearResourceTimings()")
        self.evaluate("new Promise(resolve => setTimeout(resolve, 3000))")
        # One workspace scan and one held status a second; polling the status
        # every 250 ms between scans made 12.
        self.assertIn(self.evaluate(ASKED), range(4, 8))
        origin = self.evaluate("performance.timeOrigin")
        with patch.object(preview, "render", lambda name, out: ("", [])):  # Pandoc is not what this tests
            self.assertTrue(preview.build("manuscript"))
        self.wait_for(f"performance.timeOrigin !== {origin} && document.readyState === 'complete'")


if __name__ == "__main__":
    unittest.main()
