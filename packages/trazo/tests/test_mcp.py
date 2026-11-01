"""trazo mcp over pipes, one server for all tests (plugin/mcp.sh is in
test_plugin_launcher.py, the manifests and the SessionStart hook in
test_plugin.py, servers of their own in test_mcp_lifetime.py).

No test binds the real preview port: each server runs with PREVIEW_PORT
set to a port the test picked."""

import http.client
import json
import os
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import _support

import trazo
from trazo import portable


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def answers(port):
    try:
        socket.create_connection(("127.0.0.1", port), timeout=2).close()
    except OSError:
        return False
    return True


def gone(pid):
    return not portable.pid_alive(pid)  # os.kill(pid, 0) would end it on Windows


def wait_until(condition, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return condition()


def make_project(parent, name="paper", docx=None):
    """A copy of the fixture; `docx` replaces commands.docx with a Python source."""
    root = parent / name
    shutil.copytree(_support.FIXTURE, root)
    if docx is not None:
        config = root / "trazo.toml"
        line = "docx = " + json.dumps(["python3", "-c", docx])
        config.write_text("\n".join(line if row.startswith("docx = ") else row
                                    for row in config.read_text().splitlines()) + "\n")
    return root


class Session:
    """trazo mcp in a subprocess started outside the project, as the app
    does, with the preview port set to `port`; `env` values of None unset."""

    def __init__(self, cwd, port, env=None):
        environ = {k: v for k, v in os.environ.items() if k != "TRAZO_ROOT"}
        for key, value in (env or {}).items():
            environ.pop(key, None)
            if value is not None:
                environ[key] = value
        code = f"import sys, trazo.mcp as m, trazo.mcp_preview as p; p.PREVIEW_PORT = {port}; sys.exit(m.main([]))"
        self.process = subprocess.Popen([sys.executable, "-P", "-c", code], cwd=cwd, env=environ,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, encoding="utf-8")
        self.replies = queue.Queue()
        self.next_id = 0
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()

    def read(self):
        for line in self.process.stdout:
            self.replies.put(json.loads(line))

    def send(self, message):
        self.process.stdin.write((message if isinstance(message, str) else json.dumps(message)) + "\n")
        self.process.stdin.flush()

    def request(self, method, params=None):
        self.next_id += 1
        self.send({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params or {}})
        return self.next_id

    def reply(self, timeout=60):
        return self.replies.get(timeout=timeout)

    def rpc(self, method, params=None):
        id_ = self.request(method, params)
        reply = self.reply()
        assert (reply["jsonrpc"], reply["id"]) == ("2.0", id_), reply
        return reply

    def tool(self, name, **arguments):
        result = self.rpc("tools/call", {"name": name, "arguments": arguments})["result"]
        if not result["isError"]:
            assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
        return result

    def close(self):
        if not self.process.stdin.closed:
            self.process.stdin.close()
        self.process.wait(30)
        self.reader.join(10)
        self.process.stdout.close()


class McpServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="trazo-mcp-")).resolve()
        cls.project = make_project(cls.tmp)
        cls.data = cls.tmp / "data"
        (cls.data / "venv" / "bin").mkdir(parents=True)
        (cls.data / "venv" / "bin" / "trazo").touch()
        cls.port = free_port()
        cls.session = Session(cls.tmp, cls.port, {"TRAZO_DATA_DIR": str(cls.data)})
        # initialize is the first message a fresh server reads.
        cls.init = cls.session.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                                  "clientInfo": {"name": "test", "version": "0"}})

    @classmethod
    def tearDownClass(cls):
        cls.session.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_session(self):
        init = self.init["result"]
        self.assertEqual(init["protocolVersion"], "2025-06-18")
        self.assertEqual(init["serverInfo"], {"name": "trazo", "version": trazo.__version__})
        self.session.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual(self.session.rpc("ping")["result"], {})

        tools = {tool["name"]: tool for tool in self.session.rpc("tools/list")["result"]["tools"]}
        self.assertEqual(set(tools), {"setup_status", "doctor", "build", "check", "paper_create", "paper_update",
                                      "preview_start", "preview_stop"})
        self.assertIn("project", tools["build"]["inputSchema"]["properties"])
        self.assertTrue(all(tool["inputSchema"]["additionalProperties"] is False for tool in tools.values()))
        # Codex asks before a tool that is not read-only, always before a destructive one.
        hinted = {hint: {name for name, tool in tools.items() if tool["annotations"][hint]}
                  for hint in ("readOnlyHint", "destructiveHint")}
        self.assertEqual(hinted, {"readOnlyHint": {"setup_status", "doctor", "check"},
                                  "destructiveHint": {"paper_update"}})

        status = self.session.tool("setup_status", project=str(self.project))["structuredContent"]
        self.assertEqual(status["trazo"]["version"], trazo.__version__)
        self.assertEqual(set(status), {"trazo", "plugin", "install", "uv", "pandoc", "chrome", "machine", "next"})

        start = self.session.tool("preview_start", project=str(self.project))
        self.assertFalse(start["isError"], start["content"])
        info = start["structuredContent"]
        self.assertEqual((info["project"], info["port"], info["reused"]), (str(self.project), self.port, False))
        self.assertEqual(info["url"], f"http://127.0.0.1:{self.port}")
        recorded = json.loads((self.project / ".build" / "preview.json").read_text())
        self.assertEqual(recorded, {k: info[k] for k in ("url", "port", "pid", "ppid", "project")})
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request("GET", "/__status")
        reply = connection.getresponse()
        self.assertEqual(reply.status, 200)
        served = json.load(reply)
        connection.close()
        self.assertIn("manuscript", served)
        self.assertEqual(served["server"], {"pid": info["pid"], "project": str(self.project)})
        again = self.session.tool("preview_start", project=str(self.project))["structuredContent"]
        self.assertEqual((again["port"], again["pid"], again["reused"]), (info["port"], info["pid"], True))

        stop = self.session.tool("preview_stop", project=str(self.project))["structuredContent"]
        self.assertEqual(stop, {"stopped": True, "pid": info["pid"], "started_here": True})
        self.assertFalse((self.project / ".build" / "preview.json").exists())
        self.assertFalse(answers(self.port))

    def test_errors(self):
        session = self.session
        self.assertEqual(session.rpc("nope")["error"]["code"], -32601)
        self.assertEqual(session.rpc("tools/call", {"name": "nope"})["error"]["code"], -32602)
        self.assertEqual(session.rpc("tools/call", {"name": ["x"]})["error"]["code"], -32602)
        self.assertEqual(session.rpc("tools/call", {"name": "check", "arguments": "x"})["error"]["code"], -32602)
        result = session.tool("check", project="relative/path")
        self.assertTrue(result["isError"])
        self.assertIn("absolute", result["content"][0]["text"])
        result = session.tool("preview_start", project=str(self.tmp))
        self.assertTrue(result["isError"])
        self.assertIn("trazo.toml", result["content"][0]["text"])
        self.assertEqual(session.rpc("ping")["result"], {})

    def test_bad_messages_keep_the_server_running(self):
        session = self.session
        for params in ("x", [1], 3):
            session.send({"jsonrpc": "2.0", "id": "bad", "method": "tools/call", "params": params})
            self.assertEqual(session.reply()["error"]["code"], -32602)
        session.send({"jsonrpc": "2.0", "id": None, "method": "ping"})
        self.assertEqual(session.reply(), {"jsonrpc": "2.0", "id": None,
                                           "error": {"code": -32600, "message": "Invalid Request"}})
        session.send({"jsonrpc": "2.0", "id": True, "method": "ping"})
        self.assertEqual(session.reply()["error"]["code"], -32600)
        session.send("not json")
        self.assertEqual(session.reply()["error"]["code"], -32700)
        # Responses and notifications get no reply: the next reply is the ping's.
        session.send({"jsonrpc": "2.0", "id": 99, "result": {}})
        session.send({"jsonrpc": "2.0", "id": 98, "error": {"code": -1, "message": "x"}})
        session.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": "x"})
        # An unhashable requestId raises inside handle(); the server logs it and goes on.
        session.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": [1]}})
        self.assertEqual(session.rpc("ping")["result"], {})

    def test_check_returns_the_report(self):
        report = self.session.tool("check", project=str(self.project))
        self.assertFalse(report["isError"], report["content"])
        self.assertEqual(report["structuredContent"]["kind"], "article")

    def test_build(self):
        result = self.session.tool("build", project=str(self.project))
        self.assertFalse(result["isError"], result["content"])
        build = result["structuredContent"]
        self.assertEqual(build["command"], "trazo build")
        self.assertIn("docx", build["log"])
        self.assertIsInstance(build["outputs"], list)

    def test_paper_create_and_update(self):
        folder = self.tmp / "papers" / "New paper"
        result = self.session.tool("paper_create", folder=str(folder), kind="article", example=False,
                                   title="A new paper")
        self.assertFalse(result["isError"], result["content"])
        made = result["structuredContent"]
        self.assertEqual((made["folder"], made["project_name"], made["example"]), (str(folder), "new-paper", False))
        self.assertEqual(made["files"], len([p for p in folder.rglob("*") if p.is_file() and p.name != "manifest.json"]))
        again = self.session.tool("paper_create", folder=str(folder), kind="article", example=False, title="X")
        self.assertTrue(again["isError"])
        self.assertIn("not empty", again["content"][0]["text"])
        bad = self.session.tool("paper_create", folder="relative", kind="article", example=False, title="X")
        self.assertTrue(bad["isError"])
        nested = self.session.tool("paper_create", folder=str(folder / "notes" / "inner"), kind="article",
                                   example=False, title="X")
        self.assertTrue(nested["isError"])
        self.assertIn("is inside the paper", nested["content"][0]["text"])
        self.assertFalse((folder / "notes" / "inner").exists())
        title = 'Ünïcode "quoted" title: C:\\papers'
        quoted = self.session.tool("paper_create", folder=str(self.tmp / "papers" / "Quoted"), kind="report",
                                   example=True, title=title)
        self.assertFalse(quoted["isError"], quoted["content"])
        self.assertEqual(quoted["structuredContent"]["title"], title)
        self.assertEqual(json.loads((self.tmp / "papers/Quoted/.trazo/manifest.json").read_text(
            encoding="utf-8"))["answers"]["title"], title)

        update = self.session.tool("paper_update", project=str(folder))
        self.assertFalse(update["isError"], update["content"])
        self.assertTrue(update["structuredContent"]["up_to_date"])

        old = self.tmp / "old-paper"
        shutil.copytree(folder, old)
        shutil.rmtree(old / ".trazo")
        update = self.session.tool("paper_update", project=str(old))
        self.assertTrue(update["isError"])
        self.assertIn("not made by `trazo new`", update["content"][0]["text"])

    def test_setup_status_without_project(self):
        result = self.session.tool("setup_status")
        self.assertFalse(result["isError"], result["content"])
        status = result["structuredContent"]
        # venv/bin/trazo without a dist-info folder is not an install.
        self.assertEqual(status["install"], {"folder": str(self.data), "installed": False, "version": None,
                                             "matches_plugin": None})
        self.assertEqual((status["machine"], status["next"]), (None, "setup"))
        self.assertEqual(status["trazo"]["version"], trazo.__version__)

    def test_doctor(self):
        for args in ({}, {"project": str(self.project)}, {"project": str(self.tmp)}):  # tmp is no paper
            result = self.session.tool("doctor", **args)
            self.assertFalse(result["isError"], result["content"])
            report = result["structuredContent"]
            self.assertEqual(set(report), {"ok", "tools", "data_folder", "machine"})
            self.assertIsInstance(report["ok"], bool)
            self.assertEqual(report["data_folder"], str(self.data))
            self.assertEqual([item["tool"] for item in report["tools"]][:2], ["uv", "python"])
            for item in report["tools"]:
                self.assertIn(item["status"], ("ok", "missing", "note"))
                self.assertLessEqual({"tool", "status", "message", "fix"}, set(item))
                if item["status"] == "ok":
                    self.assertLessEqual({"path", "version", "found"}, set(item))


if __name__ == "__main__":
    unittest.main()
