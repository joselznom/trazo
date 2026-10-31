"""preview_start regenerates the preview's inputs first, as the terminal's
`build --prepare=inputs && preview serve` does (trazo.preview_inputs),
over a real `trazo mcp` session."""

import http.client
import os
import queue
import shutil
import signal
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from test_mcp import Session, answers, free_port, gone, make_project, wait_until

from trazo.build_lock import build_lock, lock_path
from trazo.project import DEFAULTS, Project

# Writes data/values.json from data/n.txt, recording the input as the
# template's scripts/build_values.py does.
VALUES_SCRIPT = """\
import hashlib, json, pathlib
source = pathlib.Path("data/n.txt")
n = source.read_text().strip()
pathlib.Path("data/values.json").write_text(json.dumps({
    "schema_version": 1, "inputs": {"data/n.txt": hashlib.sha256(source.read_bytes()).hexdigest()},
    "values": {"cohort.n": {"value": int(n), "text": n, "source": "test"}}}))
"""

FIGURE_STEP = """
[[build.steps]]
name = "fig1"
run = ["figures/scripts/fig1.py"]
outputs = ["figures/output/png/fig1.png"]
before = "diagrams"
"""


def page(port, name="manuscript"):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request("GET", f"/{name}.html")
        return connection.getresponse().read().decode("utf-8", "replace")
    finally:
        connection.close()


def paper(root):
    """The project at `root` (Project.load would follow the tests' $TRAZO_ROOT)."""
    return Project(root, DEFAULTS, True)


def lock_taken(root):
    """Whether a process holds the build lock of the project at `root`."""
    import fcntl  # POSIX only: imported here so the tests' discovery works on Windows
    path = lock_path(paper(root))
    if not path.exists():
        return False
    with open(path) as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
    return False


@unittest.skipIf(os.name == "nt", "the build lock needs fcntl (POSIX)")
class PreviewInputsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="trazo-mcp-inputs-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.project = make_project(self.tmp)
        (self.project / "scripts").mkdir()
        (self.project / "scripts" / "build_values.py").write_text(VALUES_SCRIPT)
        (self.project / "data" / "n.txt").write_text("4242\n")

    def session(self, env=None, setup=""):
        """A Session whose server runs the Python `setup` first (to shorten a wait)."""
        popen = subprocess.Popen

        def with_setup(argv, **options):
            return popen([*argv[:3], f"{setup}\n{argv[3]}"], **options)
        with patch.object(subprocess, "Popen", with_setup):
            session = Session(self.tmp, free_port(), env)
        self.addCleanup(session.close)
        return session

    def figure_script(self, source):
        script = self.project / "figures" / "scripts" / "fig1.py"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(source)
        with open(self.project / "trazo.toml", "a") as config:
            config.write(FIGURE_STEP)

    def slow_figure(self):
        """A figure script that writes its PID to the returned file, then sleeps."""
        marker = self.tmp / "fig1.pid"
        self.figure_script(f"import os, pathlib, time; pathlib.Path({str(marker)!r}).write_text(str(os.getpid())); "
                           "time.sleep(60)\n")
        return marker

    def started(self, session):
        """preview_start's result, once the page shows the first data value."""
        info = session.tool("preview_start", project=str(self.project))["structuredContent"]
        self.assertTrue(wait_until(lambda: "4242" in page(info["port"])), page(info["port"])[-2000:])
        return info

    def assert_no_preview(self, session):
        self.assertFalse((self.project / ".build" / "preview.json").exists())
        self.assertEqual(session.tool("preview_stop", project=str(self.project))["structuredContent"],
                         {"stopped": False, "reason": "no preview is running for this project"})

    def test_a_reused_preview_shows_the_changed_data_value_when_the_call_returns(self):
        session = self.session()
        first = session.tool("preview_start", project=str(self.project))
        self.assertFalse(first["isError"], first["content"])
        info = first["structuredContent"]
        self.assertTrue(wait_until(lambda: "4242" in page(info["port"])), page(info["port"])[-2000:])

        (self.project / "data" / "n.txt").write_text("5757\n")
        again = session.tool("preview_start", project=str(self.project))
        self.assertFalse(again["isError"], again["content"])
        result = again["structuredContent"]
        self.assertEqual((result["pid"], result["reused"]), (info["pid"], True))
        self.assertNotIn("note", result)
        self.assertIn("5757", page(info["port"]))  # no waiting here: the call waited for the watcher

    def test_a_reused_preview_whose_page_fails_to_build_says_so(self):
        session = self.session()
        info = self.started(session)
        (self.project / "data" / "n.txt").write_text("5757\n")
        section = self.project / "manuscript" / "main" / "01_introduction.md"
        section.write_text(f"{section.read_text()}\n# Introduction\n")  # the preview takes one per file
        again = session.tool("preview_start", project=str(self.project))
        self.assertFalse(again["isError"], again["content"])
        result = again["structuredContent"]
        self.assertTrue(result["reused"])
        self.assertIn("manuscript/main/01_introduction.md has 2 level-1 headings", result["note"])
        self.assertIn("4242", page(info["port"]))  # what the note warns about: the last good page

    def test_a_reused_preview_whose_page_build_raises_says_so(self):
        session = self.session()
        info = self.started(session)
        rendered = self.project / ".build" / "preview" / "manuscript.md"
        # A folder in its place, not a read-only file: root (the Linux test container) writes those.
        rendered.unlink()
        rendered.mkdir()
        (self.project / "data" / "n.txt").write_text("5757\n")
        result = session.tool("preview_start", project=str(self.project))["structuredContent"]
        self.assertTrue(result["reused"])
        self.assertIn("manuscript: IsADirectoryError", result["note"])
        self.assertIn("4242", page(info["port"]))

    def test_a_failing_figure_script_is_a_tool_error_and_starts_no_preview(self):
        self.figure_script("raise SystemExit('fig1: data/missing.csv not found')\n")
        session = self.session()
        result = session.tool("preview_start", project=str(self.project))
        self.assertTrue(result["isError"])
        text = result["content"][0]["text"]
        self.assertIn("no preview was started", text)
        self.assertIn("call preview_start again", text)
        self.assertIn("fig1: data/missing.csv not found", text)
        self.assertNotIn("make", text.lower())
        self.assert_no_preview(session)

    def test_a_cancelled_preparation_is_killed_holds_no_lock_and_starts_no_preview(self):
        marker = self.slow_figure()
        port = free_port()
        session = Session(self.tmp, port)
        self.addCleanup(session.close)
        id_ = session.request("tools/call", {"name": "preview_start", "arguments": {"project": str(self.project)}})
        self.assertTrue(wait_until(lambda: marker.is_file() and marker.read_text()), "the figure script did not start")
        self.assertTrue(lock_taken(self.project))
        session.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": id_}})
        self.assertTrue(wait_until(lambda: gone(int(marker.read_text())), 10), "the figure script still runs")
        self.assertTrue(wait_until(lambda: not lock_taken(self.project), 10), "the build lock is still held")
        self.assertEqual(session.rpc("ping")["result"], {})
        with self.assertRaises(queue.Empty):
            session.reply(timeout=2)
        self.assertFalse((self.project / ".build" / "preview.json").exists())
        self.assertFalse(answers(port))

    def test_a_preparation_outliving_a_killed_server_keeps_the_lock(self):
        marker = self.slow_figure()
        session = self.session()
        session.request("tools/call", {"name": "preview_start", "arguments": {"project": str(self.project)}})
        self.assertTrue(wait_until(lambda: marker.is_file() and marker.read_text()), "the figure script did not start")
        group = os.getpgid(int(marker.read_text()))
        self.addCleanup(lambda: gone(int(marker.read_text())) or os.killpg(group, signal.SIGKILL))
        session.process.kill()  # SIGKILL: the server cannot kill its calls
        session.process.wait(10)
        self.assertFalse(gone(int(marker.read_text())))
        self.assertTrue(lock_taken(self.project), "a competing build would write alongside the figure script")

    def test_another_build_holding_the_lock_is_a_tool_error_until_it_ends(self):
        session = self.session(setup="import trazo.preview_inputs as p; p.LOCK_WAIT = 1")
        held, release = threading.Event(), threading.Event()

        def other_build():
            with build_lock(paper(self.project), log=lambda text: None):
                held.set()
                release.wait(60)
        thread = threading.Thread(target=other_build)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(release.set)
        self.assertTrue(held.wait(10))
        busy = session.tool("preview_start", project=str(self.project))
        self.assertTrue(busy["isError"])
        self.assertIn("another build of this project is running", busy["content"][0]["text"])
        self.assertIn("call preview_start again", busy["content"][0]["text"])
        self.assert_no_preview(session)

        release.set()
        thread.join()
        started = session.tool("preview_start", project=str(self.project))
        self.assertFalse(started["isError"], started["content"])
        self.assertIn("4242", (self.project / "data" / "values.json").read_text())

    def test_simultaneous_calls_wait_for_the_lock_no_longer_than_the_limit(self):
        # Both calls find the lock free; the first to take it runs a figure
        # script that outlasts the wait, so the other must give up in time.
        self.figure_script("import time; time.sleep(60)\n")
        session = self.session(setup="import trazo.preview_inputs as p; p.LOCK_WAIT = 1")
        for _ in range(2):
            session.request("tools/call", {"name": "preview_start", "arguments": {"project": str(self.project)}})
        reply = session.reply(timeout=15)["result"]
        self.assertTrue(reply["isError"])
        self.assertIn("another build of this project is running", reply["content"][0]["text"])
        self.assertIn("call preview_start again", reply["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
