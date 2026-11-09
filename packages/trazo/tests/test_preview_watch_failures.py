"""Preview: a watcher pass that fails for an unexpected reason shows it and recovers."""

import http.client
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)
from test_mcp import Session, free_port, make_project, wait_until

from trazo.build_lock import lock_path
from trazo.project import DEFAULTS, Project


def get(port: int, path: str) -> bytes:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        assert response.status == 200, response.read()
        return response.read()
    finally:
        connection.close()


def status(port: int) -> dict:
    return json.loads(get(port, "/__status"))


@unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
@unittest.skipIf(os.name == "nt" or os.geteuid() == 0, "file modes must deny access")
class WatcherFailureTests(unittest.TestCase):
    def project(self) -> Path:
        # Entered before the session, so the session closes first.
        return make_project(Path(self.enterContext(tempfile.TemporaryDirectory())).resolve())

    def start(self, root: Path) -> int:
        session = Session(root.parent, free_port())
        self.addCleanup(session.close)
        start = session.tool("preview_start", project=str(root))
        self.assertFalse(start["isError"], start["content"])
        return start["structuredContent"]["port"]

    def test_a_failed_pass_is_reported_retried_and_keeps_its_changes(self) -> None:
        root = self.project()
        port = self.start(root)
        self.assertTrue(wait_until(lambda: status(port)["manuscript"]["ok"]))
        lock = lock_path(Project(root, DEFAULTS, True))
        lock.chmod(0o400)  # the tables stage cannot open the build lock: not an error it expects
        spec = root / "tables/specs/results.json"
        section = root / "manuscript/main/01_introduction.md"
        changed = time.time()
        spec.write_text(spec.read_text() + " ")
        section.write_text(section.read_text() + "\nWrittenWhileTheLockFails.\n")
        self.assertTrue(wait_until(lambda: status(port)["synced"] > changed))
        failure = status(port)["manuscript"]
        self.assertFalse(failure["ok"], "a failed watcher pass was reported as a fresh page")
        self.assertIn("PermissionError", failure["error"])
        # The pass is retried while it keeps failing (each retry stamps `built` again).
        self.assertTrue(wait_until(lambda: status(port)["manuscript"]["built"] != failure["built"]))
        lock.chmod(0o644)  # fixed without touching a source: the retry notices
        self.assertTrue(wait_until(lambda: status(port)["manuscript"]["ok"]
                                   and b"WrittenWhileTheLockFails" in get(port, "/manuscript.html")),
                        "the edit made during the failure never reached the page")
        log = lambda: (root / ".build/preview.log").read_text(encoding="utf-8")  # noqa: E731
        self.assertTrue(wait_until(lambda: "watcher recovered" in log()), log())
        self.assertEqual(log().count("watcher error"), 1, "a lasting failure was printed on every retry")

    def test_a_page_unreadable_at_start_does_not_stop_the_watcher(self) -> None:
        root = self.project()
        section = root / "manuscript/main/01_introduction.md"
        section.chmod(0)
        port = self.start(root)
        self.assertTrue(wait_until(lambda: "PermissionError" in status(port)["manuscript"]["error"]),
                        "the first build's failure is not in the status")
        section.chmod(0o644)
        section.write_text(section.read_text() + "\nReadableAgain.\n")
        self.assertTrue(wait_until(lambda: status(port)["manuscript"]["ok"]
                                   and b"ReadableAgain" in get(port, "/manuscript.html")),
                        "the watcher did not rebuild after the file became readable")


if __name__ == "__main__":
    unittest.main()
