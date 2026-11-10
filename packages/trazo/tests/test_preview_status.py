"""A preview page's held /__status request, through the real HTTP handler.

The page asks `/__status?since=<digest>&wait=<seconds>` between its workspace
scans (assets/app/app.js); the server answers when the state the page shows
changes, when the wait ends, or when the server closes.
"""

import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)

from trazo import preview, preview_sources, preview_status, preview_watch
from trazo.local_server import LocalServer

DOC = next(iter(preview.DOCS))


class HeldStatus(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="trazo-status-")
        self.addCleanup(folder.cleanup)
        for name, value in {
            "OUT_DIR": Path(folder.name),
            "COMMENTS_PATH": Path(folder.name) / "comments.json",
            "STATE": {name: dict(state) for name, state in preview.STATE.items()},
            "WATCHER": {"synced": 1.0, "written": 0.0, "rebuild": set(), "outputs": []},
            # Pandoc is not what this tests: a build that renders nothing still lands.
            "render": lambda name, out: ("", []),
        }.items():
            patcher = patch.object(preview, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        # Long enough that an answer at the end of the hold, or at a recheck,
        # cannot pass for one at a change.
        for name in ("HOLD", "RECHECK"):
            patcher = patch.object(preview_status, name, 30.0)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.server = LocalServer(("127.0.0.1", 0), preview.Handler)
        self.server.daemon_threads = False  # closing waits for the requests in flight
        self.addCleanup(self.server.server_close)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.shutdown)

    def status(self, query=""):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=60)
        try:
            connection.request("GET", "/__status" + query)
            reply = connection.getresponse()
            return reply.status, json.loads(reply.read())
        finally:
            connection.close()

    def held(self, since):
        """(thread, answers) of a status request held on `since`, still waiting."""
        answers = []
        thread = threading.Thread(target=lambda: answers.append(self.status(f"?since={since}&wait=30")), daemon=True)
        thread.start()
        time.sleep(0.3)
        self.assertTrue(thread.is_alive(), f"answered without waiting: {answers}")
        return thread, answers

    def test_a_change_the_page_shows_answers_a_held_request_at_once(self):
        changes = {
            "a build": (lambda: self.assertTrue(preview.build(DOC)),
                        lambda reply: reply[DOC]["version"] == 1 and not reply["building"]),
            "a save from the page": (preview_sources.mark_written, lambda reply: reply["building"]),
            "the watcher catching up with that save": (lambda: preview_watch.synced(time.time()),
                                                       lambda reply: not reply["building"]),
        }
        for change, (make, shown) in changes.items():
            with self.subTest(change):
                _, before = self.status()
                waiting, answers = self.held(before["digest"])
                make()
                waiting.join(5)
                self.assertFalse(waiting.is_alive(), "still held after the change")
                [(code, reply)] = answers
                self.assertEqual(code, 200)
                self.assertTrue(shown(reply), reply)
                self.assertNotEqual(reply["digest"], before["digest"])

    def test_without_a_change_a_held_request_answers_when_its_wait_ends(self):
        _, before = self.status()
        start = time.monotonic()
        code, reply = self.status(f"?since={before['digest']}&wait=0.6")
        self.assertGreaterEqual(time.monotonic() - start, 0.6)
        self.assertEqual((code, reply["digest"], reply[DOC]), (200, before["digest"], before[DOC]))

    def test_a_request_for_another_state_is_answered_at_once(self):
        start = time.monotonic()
        code, reply = self.status("?since=not-the-current-digest&wait=30")
        self.assertLess(time.monotonic() - start, 5)
        self.assertEqual(code, 200)
        self.assertIn(DOC, reply)

    def test_a_wait_that_is_not_a_number_of_seconds_is_refused(self):
        for wait in ("soon", "-1", "nan", "inf"):
            with self.subTest(wait):
                code, reply = self.status(f"?since=x&wait={wait}")
                self.assertEqual((code, reply["error"]), (400, "wait must be a number of seconds"))

    def test_closing_the_server_answers_its_held_requests(self):
        _, before = self.status()
        waiting, answers = self.held(before["digest"])
        start = time.monotonic()
        self.server.shutdown()
        self.server.server_close()
        self.assertLess(time.monotonic() - start, 5)  # not the 30 s hold
        waiting.join(5)
        self.assertEqual([code for code, _ in answers], [200])


if __name__ == "__main__":
    unittest.main()
