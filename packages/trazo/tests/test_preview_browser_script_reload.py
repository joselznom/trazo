"""A served page whose script or stylesheet did not load, in Chrome: it reloads itself
once while it starts, keeps the reload note if the file fails again, and never reloads
once in use.

On Windows one of a page load's connections now and then fails inside Chrome (status 0)
and that script never runs, or that stylesheet never applies. Here the server resets the
connection instead, which Chrome reports the same way."""

import contextlib
import json
import socket
import struct
import unittest
from unittest.mock import patch

from _preview_writing import WritingCase

from trazo import preview

NOTE = "Viewer scripts did not load; reload the page."
# A trazo script and a trazo stylesheet added once the page is in use.
LATE_FILES = (("script", {"src": "/__assets/preview/preview-build.js?late"}),
              ("link", {"rel": "stylesheet", "href": "/__assets/preview/preview-views.css?late"}))
LATE_FILE = """((tag, props) => new Promise(resolve => document.head.append(Object.assign(
  document.createElement(tag), props, {onload: () => resolve('loaded'), onerror: () => resolve('failed')}))))"""
# An @import of preview.css: the stylesheet Windows CI lost once.
SHELL_CSS = "/__assets/preview/preview-shell.css"
# Every trazo script and stylesheet of this page load answered.
ALL_LOADED = ("!performance.getEntriesByType('resource').some(e => e.responseStatus === 0"
              " && new URL(e.name).pathname.startsWith('/__assets/'))")


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
        """Nothing reloads the page. The guard reloads from the failed file's error
        event, so a reload would have reached the server within this time."""
        self.evaluate("window.samePage = true")
        self.evaluate("new Promise(resolve => setTimeout(resolve, 200))")
        self.assertTrue(self.evaluate("window.samePage === true"))

    def late_files_fail_without_reload(self, page):
        for tag, props in LATE_FILES:
            path = props.get("src") or props["href"]
            with self.subTest(late=path), self.failing(path) as seen:
                self.assertEqual(self.evaluate(f"{LATE_FILE}('{tag}', {json.dumps(props)})"), "failed")
                self.stays()
                self.assertNotIn(page, seen)
            self.assertFalse(self.evaluate("!!document.getElementById('pk-script-note')"))

    def test_a_viewer_file_that_failed_reloads_the_page_once_and_it_starts(self):
        # app.js loads in the head, before there is a body for the note.
        for path in ("/__assets/app/app.js", "/__assets/preview/preview-wordmark.js", SHELL_CSS):
            with self.subTest(path=path), self.failing(path, times=1) as seen:
                self.open()
                self.assertEqual(seen.count("/manuscript.html"), 2)
                self.assertEqual(seen.count(path), 2)
                self.assertEqual(self.evaluate("performance.getEntriesByType('navigation')[0].type"), "reload")
                self.assertTrue(self.evaluate(ALL_LOADED))
                self.assertFalse(self.evaluate("!!document.getElementById('pk-script-note')"))

    def test_a_table_editor_file_that_failed_reloads_the_page_once_and_it_starts(self):
        for path in ("/__assets/table/editor-inline.js", "/__assets/table/editor-inline.css"):
            # The same URL with a fragment again would stay on the page.
            self.chrome.call("Page.navigate", {"url": "about:blank"}, self.session)
            with self.subTest(path=path), self.failing(path, times=1) as seen:
                self.open_table()
                self.assertEqual(seen.count("/tables"), 2)
                self.assertTrue(self.evaluate(ALL_LOADED))

    def test_a_file_that_fails_again_leaves_the_note_and_no_more_reloads(self):
        for path in ("/__assets/preview/preview-wordmark.js", SHELL_CSS):
            with self.subTest(path=path), self.failing(path) as seen:
                self.chrome.call("Page.navigate", {"url": self.url}, self.session)
                self.wait_for("document.getElementById('pk-script-note') && document.readyState === 'complete'"
                              " && performance.getEntriesByType('navigation')[0].type === 'reload'")
                self.stays()
                self.assertEqual(seen.count("/manuscript.html"), 2)
                self.assertEqual(self.evaluate("document.getElementById('pk-script-note').textContent"), NOTE)
            # The next subtest starts a new count, as a page that loaded would.
            self.evaluate("sessionStorage.clear()")

    def test_a_file_that_fails_once_the_author_is_writing_does_not_reload(self):
        self.build()
        self.write_in("plain")
        self.type("typed ")
        self.late_files_fail_without_reload("/manuscript.html")
        self.assertIn("typed ", self.evaluate("document.activeElement.textContent"))

    def test_a_file_that_fails_once_a_table_is_edited_does_not_reload(self):
        self.open_table()
        self.evaluate("document.getElementById('te-filter').focus()")
        self.type("res")
        self.late_files_fail_without_reload("/tables")
        self.assertEqual(self.evaluate("document.getElementById('te-filter').value"), "res")


if __name__ == "__main__":
    unittest.main()
