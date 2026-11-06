"""Corrupt source history must preserve evidence and allow the next user action."""

import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import _support
from _preview_browser import PreviewBrowserTestCase

from trazo import preview
from trazo.local_server import LocalServer

FILE = "manuscript/main/01_introduction.md"


class SourceHistoryRecovery(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = LocalServer(("127.0.0.1", 0), preview.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def call(self, route: str, body: dict | None = None) -> tuple[int, dict]:
        port = self.server.server_port
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            headers = {"Host": f"127.0.0.1:{port}"}
            if body is None:
                conn.request("GET", route, headers=headers)
            else:
                headers["Content-Type"] = "application/json"
                conn.request("POST", route, json.dumps(body), headers)
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def test_corrupt_history_is_preserved_reported_and_recovers_on_retry(self) -> None:
        path = _support.ROOT / FILE
        original = path.read_bytes()
        self.addCleanup(path.write_bytes, original)
        corruptions = [b"{truncated", b"\xff\xfe", b"{}", b"[null]",
                       json.dumps([{"file": FILE, "before_text": None, "after_sha": "x"}]).encode(),
                       json.dumps([{"file": FILE, "before_text": "old", "after_sha": "x", "at": "x"}]).encode(),
                       json.dumps([{"file": FILE, "before_text": "old", "after_sha": "x", "kind": "\ud800"}]).encode()]
        for damaged in corruptions:
            for operation in ("save", "read", "latest", "reject"):
                with self.subTest(corrupt=damaged, operation=operation), tempfile.TemporaryDirectory() as folder:
                    root = Path(folder)
                    history = root / "file_history.json"
                    path.write_bytes(original)
                    with patch.object(preview, "FILE_HISTORY_PATH", history), \
                         patch.object(preview, "EDITS_PATH", root / "edits.json"):
                        status, state = self.call(f"/__source?file={FILE}")
                        self.assertEqual(status, 200, state)
                        save = {"file": FILE, "text": state["text"] + "\nA new paragraph.\n",
                                "base_sha256": state["sha256"]}
                        route, body = "/__source", save
                        if operation == "read":
                            route, body = f"/__source?file={FILE}", None
                        elif operation == "latest":
                            route, body = "/__undo?doc=manuscript", None
                        elif operation == "reject":
                            self.assertEqual(self.call("/__edits?doc=manuscript")[0], 200)
                            path.write_bytes(original.replace(b"We studied", b"We followed"))
                            status, edits = self.call("/__edits?doc=manuscript")
                            self.assertEqual(status, 200, edits)
                            self.assertTrue(edits["edits"])
                            route, body = "/__edits", {"op": "reject", "edits": edits["edits"]}
                        before = path.read_bytes()
                        history.write_bytes(damaged)
                        status, error = self.call(route, body)
                        self.assertEqual(status, 500, error)
                        self.assertEqual(path.read_bytes(), before)
                        backups = [p for p in root.iterdir() if p.name not in ("file_history.json", "edits.json")]
                        self.assertEqual(len(backups), 1, error)
                        self.assertEqual(backups[0].read_bytes(), damaged)
                        self.assertIn(str(backups[0]), error["error"])
                        self.assertIn("retry", error["error"].lower())
                        self.assertFalse(history.exists())
                        status, reply = self.call(route, body)
                        self.assertEqual(status, 200, reply)
                        if operation in ("read", "latest"):
                            status, reply = self.call("/__source", save)
                            self.assertEqual(status, 200, reply)
                        status, reply = self.call("/__source", {"op": "undo", "file": FILE})
                        self.assertEqual(status, 200, reply)
                        self.assertEqual(path.read_bytes(), before)
                        self.assertEqual(backups[0].read_bytes(), damaged)

    @unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                     "requires unprivileged POSIX directory permissions")
    def test_failed_quarantine_preserves_history_and_source_until_permissions_return(self) -> None:
        path = _support.ROOT / FILE
        original = path.read_bytes()
        self.addCleanup(path.write_bytes, original)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            history = root / "file_history.json"
            with patch.object(preview, "FILE_HISTORY_PATH", history), \
                 patch.object(preview, "EDITS_PATH", root / "edits.json"):
                status, state = self.call(f"/__source?file={FILE}")
                self.assertEqual(status, 200, state)
                body = {"file": FILE, "text": state["text"] + "\nSaved after recovery.\n",
                        "base_sha256": state["sha256"]}
                damaged = b"{original damaged undo history"
                history.write_bytes(damaged)
                root.chmod(0o555)
                try:
                    status, error = self.call("/__source", body)
                    self.assertEqual(status, 500, error)
                    self.assertEqual(history.read_bytes(), damaged)
                    self.assertEqual(path.read_bytes(), original)
                    self.assertIn("could not be preserved", error["error"])
                finally:
                    root.chmod(0o755)
                status, error = self.call("/__source", body)
                self.assertEqual(status, 500, error)
                self.assertIn("preserved unchanged", error["error"])
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(self.call("/__source", body)[0], 200)
                self.assertEqual(path.read_text(encoding="utf-8"), body["text"])
                self.assertEqual(self.call("/__source", {"op": "undo", "file": FILE})[0], 200)
                self.assertEqual(path.read_bytes(), original)


class UndoErrorBrowserTests(PreviewBrowserTestCase):
    def test_keyboard_undo_shows_corrupt_history_and_retry_advice(self) -> None:
        history = preview.OUT_DIR / "file_history.json"
        with patch.object(preview, "FILE_HISTORY_PATH", history):
            self.open()
            damaged = b'{broken undo history'
            history.write_bytes(damaged)
            self.evaluate("document.body.dispatchEvent(new KeyboardEvent('keydown', "
                          "{key:'z', ctrlKey:true, bubbles:true}))")
            self.wait_for("document.querySelector('.pv-toast')?.textContent.includes('Retry')")
            backups = list(history.parent.glob("file_history.corrupt-*.json"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), damaged)
            self.assertIn(str(backups[0]), self.evaluate("document.querySelector('.pv-toast').textContent"))


if __name__ == "__main__":
    unittest.main()
