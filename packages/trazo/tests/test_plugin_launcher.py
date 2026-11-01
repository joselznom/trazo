"""plugin/mcp.sh, run with `env` only as the desktop apps do: which server
it starts, and the stand-in server (plugin/mcp-standin.pl) it starts when
trazo cannot serve. On Windows, plugin/mcp.cmd and plugin/mcp-standin.ps1."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo is imported)

import trazo
from trazo import machine, mcp

REPO = Path(__file__).resolve().parents[3]
PLUGIN_FILES = ("plugin/mcp.sh", "plugin/mcp-standin.pl", "plugin/mcp.cmd", "plugin/mcp-standin.ps1",
                ".claude-plugin/plugin.json")
WINDOWS = os.name == "nt"
POWERSHELL = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
NOT_SET_UP = "trazo is not set up; ask the agent to set it up\n"
TOO_OLD = "trazo is too old to run the MCP server; ask the agent to update it\n"


def copy_plugin(root):
    """The files mcp.sh needs, copied into `root` (a plugin root without a venv)."""
    for name in PLUGIN_FILES:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / name, root / name)


def fake_trazo(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('#!/bin/sh\necho "$0 $*"\n')
    path.chmod(0o755)


def install(data, version, server=True, machine_file=True):
    """A per-user install of `version`; `server`: it has trazo/mcp.py (0.6.0 on);
    `machine_file`: machine.json, which `trazo setup` writes last."""
    site = data / "venv" / ("Lib" if WINDOWS else "lib/python3.12") / "site-packages"
    (site / f"trazo-{version}.dist-info").mkdir(parents=True)
    if server:
        (site / "trazo").mkdir()
        (site / "trazo" / "mcp.py").write_text("")
    if machine_file:
        (data / "machine.json").write_text('{"written": "2026-10-25"}')
    if WINDOWS:  # only checked for, never run: the stand-in tests use no server
        (data / "venv" / "Scripts").mkdir(parents=True, exist_ok=True)
        (data / "venv" / "Scripts" / "trazo.exe").write_bytes(b"")
        return data / "venv" / "Scripts" / "trazo.exe"
    fake_trazo(data / "venv" / "bin" / "trazo")
    return data / "venv" / "bin" / "trazo"


class Launcher:
    """plugin/mcp.sh in `root` over pipes, with `env` only."""

    def __init__(self, root, env, args=()):
        if WINDOWS:  # the apps pass their whole environment there; PATH and trazo's own come from `env`
            base = {key: value for key, value in os.environ.items()
                    if key.upper() != "PATH" and not key.upper().startswith("TRAZO_")}
            command = [str(root / "plugin" / "mcp.cmd"), *args]
        else:
            base, command = {"PATH": "/usr/bin:/bin"}, ["/bin/sh", str(root / "plugin" / "mcp.sh"), *args]
        self.process = subprocess.Popen(command, env={**base, **env}, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, encoding="utf-8")

    def send(self, message):
        self.process.stdin.write((message if isinstance(message, str) else json.dumps(message)) + "\n")
        self.process.stdin.flush()

    def reply(self):
        return json.loads(self.process.stdout.readline())

    def rpc(self, id_, method, params=None):
        self.send({"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}})
        return self.reply()

    def setup_status(self):
        result = self.rpc(7, "tools/call", {"name": "setup_status", "arguments": {}})["result"]
        assert result["isError"] is False, result
        assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
        return result["structuredContent"]

    def close(self):
        """Close stdin (EOF); (exit code, the rest of stdout, stderr)."""
        stdout, stderr = self.process.communicate(timeout=30)
        return self.process.returncode, stdout, stderr


@unittest.skipUnless(POWERSHELL.is_file() if WINDOWS else Path("/usr/bin/perl").is_file(),
                     "needs Windows PowerShell, or /bin/sh and /usr/bin/perl")
class LauncherTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.home, self.root, self.data = self.tmp / "home with space", self.tmp / "plugin root", self.tmp / "dätä"
        self.home.mkdir()
        copy_plugin(self.root)

    def launch(self, env, args=()):
        launcher = Launcher(self.root, {"HOME": str(self.home), **env}, args)
        self.addCleanup(lambda: launcher.process.poll() is None and launcher.process.kill())
        return launcher

    def started(self, env=None, args=()):
        """What mcp.sh started: the fake trazo's command line, or the stand-in's stderr."""
        before = sorted(self.tmp.rglob("*"))
        code, stdout, stderr = self.launch(env or {}, args).close()
        self.assertEqual(sorted(self.tmp.rglob("*")), before, "the launcher wrote files")
        self.assertEqual(code, 0)
        return stdout or stderr

    @unittest.skipIf(WINDOWS, "fakes trazo with a shell script; CI starts the real install through mcp.cmd")
    def test_which_server(self):
        data = {"TRAZO_DATA_DIR": str(self.data)}
        self.assertEqual(self.started(), NOT_SET_UP)
        old = install(self.data, "0.5.0", server=False)
        self.assertEqual(self.started(data), TOO_OLD)

        checkout = self.root / "packages" / "trazo" / ".venv" / "bin" / "trazo"
        fake_trazo(checkout)
        self.assertEqual(self.started(), f"{checkout} mcp\n")
        # An install without the MCP server gives way to the checkout's.
        self.assertEqual(self.started(data), f"{checkout} mcp\n")

        # The default folder is trazo.machine.data_dir's for this computer.
        with patch.dict(os.environ, {"HOME": str(self.home), "TRAZO_DATA_DIR": "", "XDG_DATA_HOME": ""}):
            default = install(machine.data_dir(), "0.6.0")
        self.assertEqual(self.started(), f"{default} mcp\n")
        (old.parents[1] / "lib" / "python3.12" / "site-packages" / "trazo").mkdir()
        (old.parents[1] / "lib" / "python3.12" / "site-packages" / "trazo" / "mcp.py").write_text("")
        self.assertEqual(self.started(data), f"{old} mcp\n")

    @unittest.skipIf(WINDOWS, "fakes trazo with a shell script; CI checks mcp.cmd --no-views")
    def test_claude_turns_the_view_off(self):
        # Claude's manifest passes --no-views; Codex's passes nothing.
        checkout = self.root / "packages" / "trazo" / ".venv" / "bin" / "trazo"
        checkout.parent.mkdir(parents=True)
        checkout.write_text('#!/bin/sh\necho "views=${TRAZO_MCP_VIEWS-unset}"\n')
        checkout.chmod(0o755)
        self.assertEqual(self.started(args=["--no-views"]), "views=0\n")
        self.assertEqual(self.started(), "views=unset\n")
        self.assertEqual(self.started({"TRAZO_MCP_VIEWS": "1"}), "views=1\n")

    def test_stand_in_speaks_mcp(self):
        server = self.launch({"TRAZO_DATA_DIR": str(self.data)})
        init = server.rpc(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                            "clientInfo": {"name": "test", "version": "0"}})["result"]
        self.assertEqual(init["protocolVersion"], "2025-06-18")
        self.assertEqual((init["capabilities"], init["serverInfo"]),
                         ({"tools": {}}, {"name": "trazo", "version": trazo.__version__}))
        self.assertIn("trazo:setup", init["instructions"])
        self.assertEqual(server.rpc(2, "initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"],
                         mcp.PROTOCOLS[0])
        server.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        # The same tool as the real server's, so the agent's call carries over.
        [tool] = server.rpc(3, "tools/list")["result"]["tools"]
        [real] = [real for real in mcp.TOOLS if real["name"] == "setup_status"]
        self.assertEqual((tool["name"], tool["inputSchema"], tool["annotations"]),
                         (real["name"], real["inputSchema"], real["annotations"]))

        self.assertEqual(server.rpc("a", "ping"), {"jsonrpc": "2.0", "id": "a", "result": {}})
        self.assertEqual(server.rpc(4, "resources/list")["error"]["code"], -32601)
        self.assertEqual(server.rpc(5, "tools/call", {"name": "build"})["error"]["code"], -32602)
        self.assertEqual(server.rpc(6, "tools/call", {"name": "setup_status", "arguments": "x"})["error"]["code"],
                         -32602)
        server.send({"jsonrpc": "2.0", "id": 8, "method": "ping", "params": "x"})
        self.assertEqual(server.reply()["error"]["code"], -32602)
        for message in ({"jsonrpc": "2.0", "id": None, "method": "ping"}, {"jsonrpc": "2.0", "id": True, "method": "ping"},
                        {"jsonrpc": "2.0", "id": 1.5, "method": "ping"}, {"jsonrpc": "1.0", "id": 9, "method": "ping"}):
            server.send(message)
            self.assertEqual(server.reply()["error"]["code"], -32600, message)
        server.send("not json")
        self.assertEqual(server.reply(), {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
        # Responses and notifications get no reply: the next reply is the ping's.
        server.send({"jsonrpc": "2.0", "id": 99, "result": {}})
        server.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 3}})
        server.send("")
        self.assertEqual(server.rpc(10, "ping"), {"jsonrpc": "2.0", "id": 10, "result": {}})
        self.assertEqual(server.close(), (0, "", NOT_SET_UP))

    def test_stand_in_setup_status(self):
        server = self.launch({"TRAZO_DATA_DIR": str(self.data)})
        status = server.setup_status()
        self.assertEqual({key: status[key] for key in ("plugin", "install", "next")}, {
            "plugin": {"root": str(self.root), "version": trazo.__version__},
            "install": {"folder": str(self.data), "installed": False, "version": None, "matches_plugin": None},
            "next": "setup"})
        self.assertIn("trazo:setup skill", status["message"])
        self.assertIn("trazo or paper work is the go-ahead", status["message"])
        self.assertIn("a new chat loads all the trazo tools", status["message"])
        self.assertEqual(server.close(), (0, "", NOT_SET_UP))

    @unittest.skipIf(WINDOWS, "a POSIX PATH")
    def test_stand_in_reports_an_existing_pandoc(self):
        # The setup skill announces a Pandoc download only when this says none was found.
        known = next((path for path in machine.PANDOC_LOCATIONS if Path(path).is_file()), None)
        # A PATH with only what mcp.sh needs: Linux images have /usr/bin/pandoc.
        tools = self.tmp / "tools"
        tools.mkdir()
        (tools / "dirname").symlink_to(shutil.which("dirname"))
        server = self.launch({"PATH": str(tools)})
        self.assertEqual(server.setup_status()["pandoc"],
                         {"found": bool(known), "path": known, "where": known and "known location"})
        self.assertEqual(server.close(), (0, "", NOT_SET_UP))
        bin_ = self.tmp / "bin dir"
        fake_trazo(bin_ / "pandoc")
        server = self.launch({"PATH": f"{tools}:{bin_}"})
        self.assertEqual(server.setup_status()["pandoc"], {"found": True, "path": str(bin_ / "pandoc"), "where": "PATH"})
        self.assertEqual(server.close(), (0, "", NOT_SET_UP))

    def test_stand_in_setup_status_follows_the_install(self):
        # Only the dist-info folder's name is the version, not a data folder named like one.
        data = self.tmp / "trazo-dev-probe"
        site = data / "venv" / ("Lib" if WINDOWS else "lib/python3.12") / "site-packages"
        install(data, "0.5.0", server=False, machine_file=False)
        server = self.launch({"TRAZO_DATA_DIR": str(data)})
        # The hook's rule: without machine.json trazo is not set up.
        status = server.setup_status()
        self.assertEqual((status["install"], status["next"]), (
            {"folder": str(data), "installed": True, "version": "0.5.0", "matches_plugin": False}, "setup"))

        (data / "machine.json").write_text('{"written": "2026-10-22"}')
        status = server.setup_status()
        self.assertEqual((status["install"]["version"], status["next"]), ("0.5.0", "update"))
        self.assertIn("trazo:setup skill", status["message"])

        # Setup reinstalled trazo, then a download failed before machine.json.
        (data / "machine.json").unlink()
        (site / "trazo-0.5.0.dist-info").rename(site / f"trazo-{trazo.__version__}.dist-info")
        (site / "trazo").mkdir()
        (site / "trazo" / "mcp.py").write_text("")
        status = server.setup_status()
        self.assertEqual((status["install"]["matches_plugin"], status["next"]), (True, "setup"))
        self.assertIn("did not finish", status["message"])
        self.assertIn("downloads only what is missing", status["message"])

        # Setup finished during the chat: only a new one gets the real server.
        (data / "machine.json").write_text('{"written": "2026-10-25"}')
        status = server.setup_status()
        self.assertEqual(status["next"], "restart")
        self.assertIn("new chat", status["message"])

        # Two dist-info folders: no version, so not set up, whatever machine.json says.
        (site / "trazo-0.5.0.dist-info").mkdir()
        status = server.setup_status()
        self.assertEqual((status["install"]["installed"], status["next"]), (False, "setup"))
        self.assertIn("not set up on this computer", status["message"])
        self.assertEqual(server.close(), (0, "", NOT_SET_UP))

    @unittest.skipUnless(WINDOWS, "Windows PATH")
    def test_stand_in_reports_a_pandoc_on_the_windows_path(self):
        folder = self.tmp / "pandoc dir"
        folder.mkdir()
        server = self.launch({"PATH": str(self.tmp)})
        self.assertEqual(server.setup_status()["pandoc"], {"found": False, "path": None, "where": None})
        self.assertEqual(server.close(), (0, "", NOT_SET_UP))
        (folder / "pandoc.exe").write_bytes(b"")
        server = self.launch({"PATH": f"{self.tmp};{folder}"})
        self.assertEqual(server.setup_status()["pandoc"], {"found": True, "path": str(folder / "pandoc.exe"),
                                                           "where": "PATH"})
        self.assertEqual(server.close(), (0, "", NOT_SET_UP))


if __name__ == "__main__":
    unittest.main()
