"""trazo mcp's processes: the preview's port, reuse and stop, and the
subprocesses a session ends or cancels. Each test runs its own server."""

import json
import os
import queue
import shutil
import signal
import socket
import tempfile
import time
import unittest
from pathlib import Path

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo is imported)
from test_mcp import Session, answers, free_port, gone, make_project, wait_until

# Windows has no SIGKILL: os.kill ends a process with any other signal.
KILL = getattr(signal, "SIGKILL", signal.SIGTERM)


class McpLifetimeTests(unittest.TestCase):
    """Each test runs its own server."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="trazo-mcp-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def session(self, port=None, env=None):
        session = Session(self.tmp, port or free_port(), env)
        self.addCleanup(session.close)
        return session

    def start(self, session, project):
        result = session.tool("preview_start", project=str(project))
        self.assertFalse(result["isError"], result["content"])
        return result["structuredContent"]

    def test_port_fallback(self):
        with socket.socket() as held:
            held.bind(("127.0.0.1", 0))
            held.listen()
            port = held.getsockname()[1]
            project = make_project(self.tmp)
            info = self.start(self.session(port), project)
            self.assertNotEqual(info["port"], port)
            self.assertEqual(info["url"], f"http://127.0.0.1:{info['port']}")
            self.assertEqual(json.loads((project / ".build" / "preview.json").read_text())["port"], info["port"])

    def test_stale_or_foreign_preview_json_is_not_reused(self):
        first, second = make_project(self.tmp, "first"), make_project(self.tmp, "second")
        session = self.session()
        record = first / ".build" / "preview.json"
        record.parent.mkdir(parents=True, exist_ok=True)
        dead_port = free_port()
        record.write_text(json.dumps({"url": f"http://127.0.0.1:{dead_port}", "port": dead_port,
                                      "pid": os.getpid(), "project": str(first)}))
        info = self.start(session, first)
        self.assertEqual(info["reused"], False)
        self.assertNotEqual(info["pid"], os.getpid())
        session.tool("preview_stop", project=str(first))

        other = self.start(session, second)
        record.write_text(json.dumps({**other, "project": str(first)}))
        info = self.start(session, first)
        self.assertEqual(info["reused"], False)
        self.assertNotEqual(info["pid"], other["pid"])
        self.assertEqual(info["project"], str(first))

    def test_preview_stop_after_the_preview_died(self):
        project = make_project(self.tmp)
        session = self.session()
        info = self.start(session, project)
        os.kill(info["pid"], KILL)
        self.assertTrue(wait_until(lambda: gone(info["pid"]) or not answers(info["port"])))
        stop = session.tool("preview_stop", project=str(project))["structuredContent"]
        self.assertEqual((stop["stopped"], stop["pid"]), (False, info["pid"]))
        self.assertIn("already exited", stop["reason"])
        self.assertFalse((project / ".build" / "preview.json").exists())

    def test_preview_stops_with_the_server(self):
        for end in ("eof", "sigterm", "sigkill"):
            with self.subTest(end=end):
                project = make_project(self.tmp, end)
                session = self.session()
                info = self.start(session, project)
                if end == "eof":
                    session.process.stdin.close()
                else:
                    session.process.send_signal(signal.SIGTERM if end == "sigterm" else KILL)
                session.process.wait(30)
                self.assertTrue(wait_until(lambda: gone(info["pid"])), "preview still running")
                self.assertFalse(answers(info["port"]))
                self.assertTrue(wait_until(lambda: not (project / ".build" / "preview.json").exists()))

    def test_two_sessions_share_a_preview(self):
        project = make_project(self.tmp)
        first = self.session()
        info = self.start(first, project)
        other = self.session()
        again = self.start(other, project)
        self.assertEqual((again["pid"], again["reused"]), (info["pid"], True))
        # The user sees one preview per paper: any chat closes it.
        stop = other.tool("preview_stop", project=str(project))["structuredContent"]
        self.assertEqual(stop, {"stopped": True, "pid": info["pid"], "started_here": False})
        # The first session reaps its child later; until then it is a zombie, not gone.
        self.assertFalse(answers(info["port"]))
        self.assertFalse((project / ".build" / "preview.json").exists())
        # The first chat closes the preview that replaced its own.
        fresh = self.start(other, project)
        self.assertNotEqual(fresh["pid"], info["pid"])
        stop = first.tool("preview_stop", project=str(project))["structuredContent"]
        self.assertEqual(stop, {"stopped": True, "pid": fresh["pid"], "started_here": False})
        # The chat that started it starts a new one.
        self.assertEqual(self.start(first, project)["reused"], False)

    def test_proxy_in_the_environment_is_bypassed(self):
        proxy = "http://127.0.0.1:9"
        session = self.session(env={"HTTP_PROXY": proxy, "http_proxy": proxy, "NO_PROXY": None, "no_proxy": None})
        started = time.monotonic()
        info = self.start(session, make_project(self.tmp))
        self.assertLess(time.monotonic() - started, 15)
        self.assertEqual(self.start(session, Path(info["project"]))["reused"], True)

    def test_project_python_files_do_not_shadow_the_standard_library(self):
        project = make_project(self.tmp)
        (project / "random.py").write_text("raise SystemExit('the project random.py was imported')\n")
        session = self.session()
        report = session.tool("check", project=str(project))
        self.assertFalse(report["isError"], report["content"])
        self.start(session, project)

    def test_non_utf8_build_output(self):
        project = make_project(self.tmp, docx="import sys; sys.stdout.buffer.write(b'bad \\xff byte\\n')")
        result = self.session().tool("build", project=str(project))
        self.assertFalse(result["isError"], result["content"])
        self.assertIn("bad � byte", result["structuredContent"]["log"])

    def slow_build(self, session):
        """Start a build whose commands.docx waits; its pid once it runs."""
        marker = self.tmp / "docx.pid"
        source = (f"import os, pathlib, time; pathlib.Path({str(marker)!r}).write_text(str(os.getpid())); "
                  "time.sleep(60)")
        project = make_project(self.tmp, docx=source)
        docx = project / "output" / "manuscript.docx"
        docx.parent.mkdir(exist_ok=True)
        docx.write_bytes(b"last good")
        id_ = session.request("tools/call", {"name": "build", "arguments": {"project": str(project)}})
        self.assertTrue(wait_until(lambda: marker.is_file() and marker.read_text()), "commands.docx did not start")
        return id_, int(marker.read_text()), docx

    def test_build_is_killed_when_the_server_exits(self):
        session = self.session()
        _, pid, docx = self.slow_build(session)
        session.process.stdin.close()
        session.process.wait(30)
        self.assertTrue(wait_until(lambda: gone(pid), 10), "commands.docx still running")
        self.assertEqual(docx.read_bytes(), b"last good")

    def test_cancelled_build_is_killed_without_a_reply(self):
        session = self.session()
        id_, pid, _ = self.slow_build(session)
        session.send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                      "params": {"requestId": id_, "reason": "test"}})
        self.assertTrue(wait_until(lambda: gone(pid), 10), "commands.docx still running")
        self.assertEqual(session.rpc("ping")["result"], {})
        with self.assertRaises(queue.Empty):
            session.reply(timeout=2)


if __name__ == "__main__":
    unittest.main()
