"""A served page whose script did not load, in Chrome: it reloads itself once while it
starts, keeps the reload note if the script fails again, and never reloads once in use.

On Windows one of a page load's connections now and then fails inside Chrome (status 0)
and that script never runs. Here the server resets the connection instead, which Chrome
reports the same way."""

import contextlib
import socket
import struct
import unittest
from unittest.mock import patch

from _preview_writing import WritingCase

from trazo import preview

NOTE = "Viewer scripts did not load; reload the page."
LATE_SCRIPT = """new Promise(resolve => {
  const script = document.createElement('script');
  script.src = '/__assets/preview/preview-build.js?late';
  script.onload = () => resolve('loaded');
  script.onerror = () => resolve('failed');
  document.head.append(script);
})"""


class ScriptReloadTests(WritingCase):
    @contextlib.contextmanager
    def failing(self, path, times=None):
        """The first `times` requests for `path` (all of them if None) end in a reset
        connection; yields the paths of every GET the server received."""
        seen, original = [], preview.Handler.do_GET

        def do_get(handler):
            seen.append(handler.path)
            if handler.path == path and (times is None or seen.count(path) <= times):
                handler.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                handler.connection.close()
                return
            original(handler)
        with patch.object(preview.Handler, "do_GET", do_get):
            yield seen

    def stays(self):
        """Nothing reloads the page. The guard reloads from the failed script's error
        event, so a reload would have reached the server within this time."""
        self.evaluate("window.samePage = true")
        self.evaluate("new Promise(resolve => setTimeout(resolve, 200))")
        self.assertTrue(self.evaluate("window.samePage === true"))

    def late_script_fails_without_reload(self, page):
        with self.failing("/__assets/preview/preview-build.js?late") as seen:
            self.assertEqual(self.evaluate(LATE_SCRIPT), "failed")
            self.stays()
            self.assertNotIn(page, seen)
        self.assertFalse(self.evaluate("!!document.getElementById('pk-script-note')"))

    def test_a_viewer_script_that_failed_reloads_the_page_once_and_it_starts(self):
        # app.js loads in the head, before there is a body for the note.
        for script in ("/__assets/app/app.js", "/__assets/preview/preview-wordmark.js"):
            with self.subTest(script=script), self.failing(script, times=1) as seen:
                self.open()
                self.assertEqual(seen.count("/manuscript.html"), 2)
                self.assertEqual(seen.count(script), 2)
                self.assertEqual(self.evaluate("performance.getEntriesByType('navigation')[0].type"), "reload")
                self.assertFalse(self.evaluate("!!document.getElementById('pk-script-note')"))

    def test_a_table_editor_script_that_failed_reloads_the_page_once_and_it_starts(self):
        with self.failing("/__assets/table/editor-inline.js", times=1) as seen:
            self.open_table()
            self.assertEqual(seen.count("/tables"), 2)

    def test_a_script_that_fails_again_leaves_the_note_and_no_more_reloads(self):
        with self.failing("/__assets/preview/preview-wordmark.js") as seen:
            self.chrome.call("Page.navigate", {"url": self.url}, self.session)
            self.wait_for("document.getElementById('pk-script-note') && document.readyState === 'complete'"
                          " && performance.getEntriesByType('navigation')[0].type === 'reload'")
            self.stays()
            self.assertEqual(seen.count("/manuscript.html"), 2)
        self.assertEqual(self.evaluate("document.getElementById('pk-script-note').textContent"), NOTE)

    def test_a_script_that_fails_once_the_author_is_writing_does_not_reload(self):
        self.build()
        self.write_in("plain")
        self.type("typed ")
        self.late_script_fails_without_reload("/manuscript.html")
        self.assertIn("typed ", self.evaluate("document.activeElement.textContent"))

    def test_a_script_that_fails_once_a_table_is_edited_does_not_reload(self):
        self.open_table()
        self.evaluate("document.getElementById('te-filter').focus()")
        self.type("res")
        self.late_script_fails_without_reload("/tables")
        self.assertEqual(self.evaluate("document.getElementById('te-filter').value"), "res")


if __name__ == "__main__":
    unittest.main()
