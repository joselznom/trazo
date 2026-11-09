"""Preview: a watcher pass that fails for an unexpected reason shows it and recovers."""

import contextlib
import http.client
import io
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)
from test_mcp import Session, free_port, make_project, wait_until

from trazo import preview, preview_watch
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


class PreviewCase(unittest.TestCase):
    """A copy of the fixture paper and a preview started on it through MCP."""

    def project(self) -> Path:
        # Entered before the session, so the session closes first.
        return make_project(Path(self.enterContext(tempfile.TemporaryDirectory())).resolve())

    def start(self, root: Path) -> int:
        session = Session(root.parent, free_port())
        self.addCleanup(session.close)
        start = session.tool("preview_start", project=str(root))
        self.assertFalse(start["isError"], start["content"])
        return start["structuredContent"]["port"]


@unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
class TableOutputTests(PreviewCase):
    def test_a_hand_edit_in_another_encoding_is_noted_until_the_next_render_rebuilds_it(self) -> None:
        root = self.project()
        config = root / "trazo.toml"  # trazo renders the spec tables itself: their outputs are checked
        config.write_text("\n".join(row for row in config.read_text().splitlines()
                                    if not row.startswith("tables = ")) + "\n")
        port = self.start(root)
        self.assertTrue(wait_until(lambda: status(port)["manuscript"]["ok"]))
        output, spec = root / "tables/output/md/results.md", root / "tables/specs/results.json"
        output.write_bytes(output.read_bytes() + "Café, saved as Latin-1.\n".encode("latin-1"))
        outputs = lambda: json.loads(get(port, "/__workspace"))["outputs"]  # noqa: E731
        self.assertTrue(wait_until(lambda: outputs() == ["tables/output/md/results.md"]))
        settled = time.time()  # the hand edit's pass is over: the render below is a pass of its own
        self.assertTrue(wait_until(lambda: status(port)["synced"] > settled))
        spec.write_text(spec.read_text() + " ")  # the next table render
        self.assertTrue(wait_until(lambda: outputs() == []), "the banner outlived the render that rebuilt the output")
        self.assertNotIn(b"Latin-1", output.read_bytes())
        self.assertEqual(status(port)["manuscript"]["error"], "")


@unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
@unittest.skipIf(os.name == "nt" or os.geteuid() == 0, "file modes must deny access")
class WatcherFailureTests(PreviewCase):

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
        section.write_text(section.read_text() + "\nReadableAgain.\n")
        section.chmod(0)
        port = self.start(root)
        self.assertTrue(wait_until(lambda: "PermissionError" in status(port)["manuscript"]["error"]),
                        "the first build's failure is not in the status")
        section.chmod(0o644)  # fixed without touching the text: the retry notices (backoff at most 30 s)
        self.assertTrue(wait_until(lambda: status(port)["manuscript"]["ok"]
                                   and b"ReadableAgain" in get(port, "/manuscript.html"), timeout=45),
                        "the watcher did not rebuild after the file became readable")
        log = (root / ".build/preview.log").read_text(encoding="utf-8")
        self.assertEqual(log.count("manuscript: error PermissionError"), 1, log)


class WatcherBackoffTests(unittest.TestCase):
    def test_outputs_a_failed_table_step_wrote_do_not_skip_the_backoff(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            spec, output = root / "tables/specs/example.json", root / "tables/output/md/example.md"
            spec.parent.mkdir(parents=True)
            output.parent.mkdir(parents=True)
            spec.write_text("original")
            runs, polls = [], 0

            def tick(_):  # the watcher's wait between passes; True stops it
                nonlocal polls
                polls += 1
                if polls == 1:
                    spec.write_text("edited")
                return polls > 6

            def regenerate():  # writes its outputs, then fails in a way it does not expect
                runs.append(len(runs))
                output.write_text(f"run {len(runs)}")
                raise UnicodeDecodeError("utf-8", b"\xf1", 0, 1, "invalid continuation byte")

            with patch.object(preview, "ROOT", root), patch.object(preview, "build"), \
                    patch.object(preview, "regenerate_tables", side_effect=regenerate), \
                    patch.dict(preview.STATE, clear=True), \
                    patch.dict(preview.WATCHER, {"synced": 0.0, "rebuild": set(), "outputs": []}), \
                    patch.object(preview_watch.time, "monotonic", return_value=1000.0), \
                    contextlib.redirect_stdout(io.StringIO()):  # the clock stands still: no backoff ends
                preview.watch(stop_event=Mock(**{"wait.side_effect": tick}))
            self.assertEqual(len(runs), 1, "the outputs of the failed run were taken as a new change")


if __name__ == "__main__":
    unittest.main()
