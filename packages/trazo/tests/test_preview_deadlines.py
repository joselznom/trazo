"""Preview: a hung tables command or page Pandoc ends with an error, Pandoc
that could not finish is retried while text Pandoc rejects is not, another
build's lock never blocks the watcher or a table save for long, and nothing
the preview started outlives it."""

import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import _hang
import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)
from test_mcp import Session, free_port, gone, wait_until
from test_preview_watch_failures import PreviewCase, get, status

from trazo import frontmatter, preview, preview_render
from trazo.build_lock import build_lock
from trazo.project import DEFAULTS, Project


@unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
class PreviewDeadlineTests(PreviewCase):
    def setUp(self) -> None:
        self.root = self.project()
        self.pids = self.root.parent / "hang.pids"
        self.addCleanup(_hang.end, self.pids)

    def gone(self) -> bool:
        return all(map(gone, _hang.recorded(self.pids)))

    def pandoc(self, mode: str) -> None:
        _hang.set_command(self.root, "pandoc", _hang.command(self.pids, shutil.which("pandoc"), mode))

    def start_with(self, env: dict[str, str]) -> int:
        with patch.dict(os.environ, env):  # the MCP server and the preview it starts inherit them
            session = Session(self.root.parent, free_port())
        self.addCleanup(session.close)
        start = session.tool("preview_start", project=str(self.root))
        self.assertFalse(start["isError"], start["content"])
        self.session = session
        return start["structuredContent"]["port"]

    def test_a_hung_tables_command_is_stopped_and_shown(self) -> None:
        flag = self.root.parent / "hang-tables"
        child = "import time; time.sleep(3600)"
        script = ("import os, pathlib, subprocess, sys, time\n"
                  f"if pathlib.Path({str(flag)!r}).exists():\n"
                  f"    child = subprocess.Popen([sys.executable, '-c', {child!r}])\n"
                  f"    open({str(self.pids)!r}, 'a').write(f'{{os.getpid()}} {{child.pid}}\\n')\n"
                  "    time.sleep(3600)\n")
        _hang.set_command(self.root, "tables", [sys.executable, "-c", script])
        port = self.start_with({"PREVIEW_TABLES_TIMEOUT": "2"})
        self.assertTrue(wait_until(lambda: status(port)["manuscript"]["ok"]))
        flag.touch()  # from now on the command hangs
        spec = self.root / "tables/specs/results.json"
        section = self.root / "manuscript/main/01_introduction.md"
        spec.write_text(spec.read_text() + " ")
        self.assertTrue(wait_until(lambda: "did not finish within 2 s" in status(port)["manuscript"]["error"]),
                        status(port))
        self.assertIn("PREVIEW_TABLES_TIMEOUT", status(port)["manuscript"]["error"])
        self.assertTrue(wait_until(self.gone), "the tables command or its child is still running")
        flag.unlink()
        section.write_text(section.read_text() + "\nWrittenAfterTheHang.\n")
        spec.write_text(spec.read_text() + " ")  # the next spec change runs the command again
        self.assertTrue(wait_until(lambda: status(port)["manuscript"]["ok"]
                                   and b"WrittenAfterTheHang" in get(port, "/manuscript.html")), status(port))

    def test_another_build_holding_the_lock_is_waited_out_quietly(self) -> None:
        port = self.start_with({})
        self.assertTrue(wait_until(lambda: status(port)["manuscript"]["ok"]))
        spec = self.root / "tables/specs/results.json"
        section = self.root / "manuscript/main/01_introduction.md"
        log = lambda: (self.root / ".build/preview.log").read_text(encoding="utf-8")  # noqa: E731
        with build_lock(Project(self.root, DEFAULTS, True), lambda text: None):
            spec.write_text(spec.read_text() + " ")
            section.write_text(section.read_text() + "\nWrittenDuringAnotherBuild.\n")
            self.assertTrue(wait_until(lambda: "tables: waiting for another build" in log()), log())
            # The text keeps following during the other build; only the tables wait.
            self.assertTrue(wait_until(lambda: b"WrittenDuringAnotherBuild" in get(port, "/manuscript.html")))
            self.assertFalse(wait_until(lambda: status(port)["manuscript"]["error"], timeout=4),
                             "another build's lock was shown as a failure")
            regenerated = log().count("tables: regenerated")
        released = time.monotonic()
        self.assertTrue(wait_until(lambda: log().count("tables: regenerated") > regenerated, timeout=30), log())
        self.assertLess(time.monotonic() - released, 3, "the tables waited for a backoff after the lock was released")
        self.assertTrue(status(port)["manuscript"]["ok"], status(port))

    def test_a_tables_command_that_fails_after_a_lock_wait_runs_once(self) -> None:
        runs = self.root.parent / "tables.runs"
        flag = self.root.parent / "fail-tables"
        script = ("import pathlib, sys\n"
                  f"if pathlib.Path({str(flag)!r}).exists():\n"
                  f"    open({str(runs)!r}, 'a').write('run\\n')\n"
                  "    sys.exit('the tables command failed')\n")
        _hang.set_command(self.root, "tables", [sys.executable, "-c", script])
        port = self.start_with({})
        self.assertTrue(wait_until(lambda: status(port)["manuscript"]["ok"]))
        flag.touch()
        spec = self.root / "tables/specs/results.json"
        log = lambda: (self.root / ".build/preview.log").read_text(encoding="utf-8")  # noqa: E731
        with build_lock(Project(self.root, DEFAULTS, True), lambda text: None):
            spec.write_text(spec.read_text() + " ")
            self.assertTrue(wait_until(lambda: "tables: waiting for another build" in log()), log())
        self.assertTrue(wait_until(lambda: "the tables command failed" in status(port)["manuscript"]["error"]),
                        status(port))
        time.sleep(3)  # several ticks: a table error waits for the next spec change
        self.assertEqual(runs.read_text().count("run"), 1, "the failing tables command ran on every tick")

    def test_a_table_save_waits_for_another_build_only_so_long(self) -> None:
        port = self.start_with({})
        self.assertTrue(wait_until(lambda: status(port)["manuscript"]["ok"]))
        spec = self.root / "tables/specs/results.json"
        before = spec.read_bytes()
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("GET", "/__table?label=results")
        sha = json.loads(connection.getresponse().read())["sha256"]
        connection.close()
        reply: dict = {}

        def save() -> None:
            save_connection = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
            body = json.dumps({"label": "results", "base_sha256": sha,
                               "ops": [{"op": "set", "path": "title", "value": "Saved during a build"}]})
            save_connection.request("POST", "/__table", body, {"Content-Type": "application/json",
                                                                "Origin": f"http://127.0.0.1:{port}"})
            response = save_connection.getresponse()
            reply.update(status=response.status, body=json.loads(response.read()))
        with build_lock(Project(self.root, DEFAULTS, True), lambda text: None):
            worker = threading.Thread(target=save, daemon=True)
            worker.start()
            worker.join(40)
            self.assertFalse(worker.is_alive(), "the save waited for the other build without end")
        self.assertEqual(reply["status"], 503, reply)
        self.assertIn("another build of this project was still running after 20 s", reply["body"]["error"])
        self.assertEqual(spec.read_bytes(), before, "a save that could not render wrote its spec")

    def test_a_hung_page_pandoc_ends_with_the_preview(self) -> None:
        self.pandoc("page")
        self.start_with({})
        self.assertTrue(wait_until(lambda: _hang.recorded(self.pids)), "the page build did not start Pandoc")
        stop = self.session.tool("preview_stop", project=str(self.root))
        self.assertFalse(stop["isError"], stop["content"])
        self.assertTrue(wait_until(self.gone, timeout=20), "the page's Pandoc outlived the preview")

    @unittest.skipIf(os.name == "nt", "a command that ignores SIGTERM")
    def test_the_watcher_starts_nothing_once_the_preview_is_stopping(self) -> None:
        # The page build's `pandoc --help` probe ignores SIGTERM, so the shutdown has
        # to wait for it; the page's own Pandoc must not start meanwhile.
        self.pandoc("stubborn-help+page")
        self.start_with({})
        self.assertTrue(wait_until(lambda: _hang.recorded(self.pids)), "the probe did not start")
        stop = self.session.tool("preview_stop", project=str(self.root))
        self.assertFalse(stop["isError"], stop["content"])
        self.assertTrue(wait_until(self.gone, timeout=20), "a command the watcher started outlived the preview")
        self.assertEqual(len(_hang.recorded(self.pids)), 2, "the watcher started Pandoc while the preview stopped")

    def test_pandoc_that_ran_out_of_time_is_retried_without_an_edit(self) -> None:
        self.pandoc("once-page")  # the first page build hangs, the next ones work
        port = self.start_with({"TRAZO_PANDOC_TIMEOUT": "2"})
        self.assertTrue(wait_until(lambda: status(port)["manuscript"]["ok"], timeout=30), status(port))
        self.assertTrue(wait_until(self.gone), "the Pandoc that ran out of time is still running")

    def test_text_pandoc_rejects_is_not_built_again_until_it_changes(self) -> None:
        self.pandoc("fail-page")
        port = self.start_with({})
        self.assertTrue(wait_until(lambda: all("rejected" in status(port)[name]["error"]
                                               for name in ("manuscript", "supplementary"))), status(port))
        calls = len(_hang.recorded(self.pids))
        time.sleep(4)  # longer than the first retries of a failure Pandoc could not finish
        self.assertEqual(len(_hang.recorded(self.pids)), calls, "Pandoc ran again on text that did not change")


@unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
class PageMetadataTests(unittest.TestCase):
    def test_a_metadata_read_that_ran_out_of_time_is_retried_not_shown_as_a_text_error(self) -> None:
        pids = Path(self.enterContext(tempfile.TemporaryDirectory())) / "hang.pids"
        self.addCleanup(_hang.end, pids)
        command = _hang.command(pids, shutil.which("pandoc"), "meta")
        with patch.object(frontmatter, "TIMEOUT", 1), \
                patch.dict(preview.PROJECT.config["commands"], {"pandoc": command}), \
                self.assertRaisesRegex(preview_render.PandocUnavailable, "did not finish within 1 s"):
            preview.build("manuscript")  # the watcher retries what this raises (PageFailed)

    @unittest.skipIf(os.name == "nt", "a signal")
    def test_a_metadata_read_killed_by_a_signal_is_retried_not_shown_as_a_text_error(self) -> None:
        pids = Path(self.enterContext(tempfile.TemporaryDirectory())) / "hang.pids"
        command = _hang.command(pids, shutil.which("pandoc"), "killed-meta")
        with patch.dict(preview.PROJECT.config["commands"], {"pandoc": command}), \
                self.assertRaisesRegex(preview_render.PandocUnavailable, "signal 9"):
            preview.build("manuscript")


if __name__ == "__main__":
    unittest.main()
