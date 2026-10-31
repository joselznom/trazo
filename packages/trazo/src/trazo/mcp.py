"""MCP server for the desktop apps (Claude, ChatGPT): `trazo mcp`.

JSON-RPC 2.0 over stdio, one message per line (the MCP stdio transport).
It answers initialize at once and builds nothing at start. Every tool takes
`project`, the absolute path of the paper folder, because the app decides
the process's working folder.

A client that declares MCP Apps (io.modelcontextprotocol/ui) also gets
the preview's view and its app-only tools (trazo.mcp_app).

trazo reads its project once, on import (trazo.project.PROJECT), so
SUBPROCESS_TOOLS run `python -P -m trazo` in the project folder
(`paper_create` in the new folder's parent; trazo.mcp_calls). The
preview is a child process (trazo.mcp_preview).
"""

from __future__ import annotations

import copy
import functools
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from . import __version__, machine, mcp_app
from .mcp_calls import Calls, ToolError, load
from .mcp_preview import Previews, serve_child
from .mcp_tools import INSTRUCTIONS, TOOLS
from .project import DEFAULTS, Project

PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")

# Tools that run `trazo` in a subprocess (cancellable; see Calls).
SUBPROCESS_TOOLS = ("build", "check", "doctor", "paper_create", "paper_update", "preview_start")


def setup_status(args):
    """The setup report. Stricter than plugin/session-start.sh, which checks
    only the install and its version: `next` is "setup" also without uv or
    Pandoc (unless the project sets commands.pandoc) or machine.json. When
    the install is newer than the plugin, `next` is "plugin": setup would
    install the older trazo."""
    project = load(args, required=False) or Project(Path.cwd(), copy.deepcopy(DEFAULTS), False)
    folder = machine.data_dir()
    installed = machine.installed_version()
    root, plugin = machine.plugin_version()
    uv, uv_where = machine.resolve_tool("uv")
    pandoc_command, pandoc_where = machine.resolve_pandoc(project.config["commands"]["pandoc"])
    pandoc = shutil.which(pandoc_command[0]) or ""
    if os.environ.get("TRAZO_CHROME"):
        chrome, chrome_where = project.chrome(), "TRAZO_CHROME"
    elif project.config["browser"]["chrome"] or project.config["diagrams"]["chrome"]:
        chrome, chrome_where = project.chrome(), "project setting"
    else:
        chrome, chrome_where = machine.find_chrome()
    data = machine.read()
    have, ship = machine.release_key(installed), machine.release_key(plugin)
    if have and ship and have > ship:
        # Both apps share the install: a newer one came from the other app's plugin.
        next_step = "plugin"
    elif not installed or not data or not uv or (not pandoc and pandoc_where != "project setting"):
        next_step = "setup"
    elif plugin and installed != plugin:
        next_step = "update"
    else:
        next_step = "ok"
    return {
        "trazo": {"version": __version__, "python": sys.executable},
        "plugin": {"root": root or None, "version": plugin or None},
        "install": {"folder": str(folder), "installed": bool(installed), "version": installed or None,
                    "matches_plugin": installed == plugin if installed and plugin else None},
        "uv": {"found": bool(uv), "path": uv or None, "where": uv_where or None},
        "pandoc": {"found": bool(pandoc), "path": pandoc or None, "command": pandoc_command,
                   "where": (pandoc_where or "PATH") if pandoc else None},
        "chrome": {"found": bool(chrome), "path": chrome or None, "where": chrome_where if chrome else None},
        "machine": {"written": data.get("written"), "trazo": data.get("trazo")} if data else None,
        "next": next_step,
    }


def doctor_report(args, run):
    """trazo doctor --json in the project folder, or outside any project."""
    project = load(args, required=False) or Project(Path.home(), copy.deepcopy(DEFAULTS), False)
    result = run(project, "doctor", "--json")
    try:
        return json.loads(result.stdout)
    except ValueError:
        raise ToolError(result.stderr.strip() or f"trazo doctor exited with {result.returncode}") from None


def build(args, run):
    project = load(args)
    argv = ["build"] + ["--submission"] * bool(args.get("submission")) + ["--force"] * bool(args.get("force"))
    started = time.time()
    result = run(project, *argv)
    files = [project.document_docx(name) for name in project.documents]
    if args.get("submission"):
        output = project.root / project.config["submission"]["output"]
        files += [output / f"{name}.docx" for name in ("title_page", "declarations", "cover_letter")]
    outputs = [{"path": str(path.relative_to(project.root)), "updated": path.stat().st_mtime >= started - 1}
               for path in dict.fromkeys(files) if path and path.is_file()]
    if result.returncode:
        raise ToolError(f"trazo {' '.join(argv)} failed:\n{result.stderr.strip()}\n{result.stdout.strip()}".strip())
    return {"command": f"trazo {' '.join(argv)}", "log": result.stdout.splitlines(),
            "warnings": result.stderr.splitlines(), "outputs": outputs}


def check(args, run):
    project = load(args)
    result = run(project, "submission", "check", "--json")
    if result.returncode:
        raise ToolError(result.stderr.strip() or f"trazo submission check exited with {result.returncode}")
    return json.loads(result.stdout)


def json_result(result: subprocess.CompletedProcess, command: str):
    """The JSON a `trazo ... --json` subprocess printed, or its error."""
    if result.returncode:
        raise ToolError(result.stderr.strip() or f"trazo {command} exited with {result.returncode}")
    try:
        return json.loads(result.stdout)
    except ValueError:
        raise ToolError(f"trazo {command} printed no JSON:\n{result.stdout.strip()}\n"
                        f"{result.stderr.strip()}".strip()) from None


def paper_create(args, run):
    folder, kind, example, title = (args.get(key) for key in ("folder", "kind", "example", "title"))
    if not isinstance(folder, str) or not Path(folder).is_absolute():
        raise ToolError(f"folder must be an absolute path, not {folder!r}")
    if kind not in ("article", "report"):
        raise ToolError(f"kind must be article or report, not {kind!r}")
    if not isinstance(example, bool):
        raise ToolError("example must be true or false")
    if not isinstance(title, str) or not title.strip():
        raise ToolError("title must be a nonempty string")
    name = args.get("project_name")
    if name is not None and not isinstance(name, str):
        raise ToolError("project_name must be a string")
    folder = Path(folder)
    parent = folder.parent
    while not parent.is_dir() and parent != parent.parent:
        parent = parent.parent
    argv = ["new", str(folder), "--kind", kind, "--title", title, "--json",
            *(["--no-example"] if not example else []), *(["--project-name", name] if name else [])]
    return json_result(run(parent, *argv), "new")


def paper_update(args, run):
    # Update must also reach papers whose old or edited config cannot load.
    value = args.get("project")
    if not isinstance(value, str) or not value:
        raise ToolError("project is required: the absolute path of the paper folder")
    folder = Path(value)
    if not folder.is_absolute():
        raise ToolError(f"project must be an absolute path, not {value!r}")
    return json_result(run(folder, "update", str(folder), "--json"), "update")


def request_id(message: Any) -> Any:
    """The message's id when it is a valid one (string or integer), else None."""
    value = message.get("id") if isinstance(message, dict) else None
    return value if isinstance(value, (str, int)) and not isinstance(value, bool) else None


class Server:
    def __init__(self, stdin, stdout) -> None:
        self.stdin, self.stdout = stdin, stdout
        self.write_lock = threading.Lock()
        self.calls = Calls()
        self.previews = Previews()
        self.views = mcp_app.Views()
        self.ui = False  # the client draws MCP Apps views
        self.tools = {"setup_status": setup_status, "doctor": doctor_report, "build": build, "check": check,
                      "paper_create": paper_create, "paper_update": paper_update,
                      "preview_start": self.start_preview, "preview_stop": self.previews.stop,
                      "preview_request": self.views.request, "preview_panel": self.views.panel}

    def start_preview(self, args, run):
        result = self.previews.start(args, run)
        return self.views.opened(result) if self.ui else result

    def send(self, message: dict[str, Any]) -> None:
        line = json.dumps({"jsonrpc": "2.0", **message}, ensure_ascii=False)
        with self.write_lock:
            self.stdout.write(line + "\n")
            self.stdout.flush()

    def error(self, id_, code: int, message: str) -> None:
        self.send({"id": id_, "error": {"code": code, "message": message}})

    def call_tool(self, id_, name: str, args: dict[str, Any]) -> None:
        try:
            if name in SUBPROCESS_TOOLS:
                result = self.tools[name](args, functools.partial(self.calls.run, id_))
            else:
                result = self.tools[name](args)
        except ToolError as exc:
            reply = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
        except Exception as exc:  # never let a tool take the server down
            reply = {"content": [{"type": "text", "text": f"{name} failed: {exc!r}"}], "isError": True}
        else:
            text = (mcp_app.summary(name, result) if name in mcp_app.APP_TOOL_NAMES
                    else json.dumps(result, ensure_ascii=False, indent=2))
            reply = {"content": [{"type": "text", "text": text}], "structuredContent": result, "isError": False}
        if not self.calls.end(id_):  # a cancelled request gets no reply
            self.send({"id": id_, "result": reply})

    def handle(self, message: Any) -> None:
        if isinstance(message, dict) and "method" not in message and ("result" in message or "error" in message):
            return  # a response; this server sends no requests
        id_ = request_id(message)
        if (not isinstance(message, dict) or message.get("jsonrpc") != "2.0"
                or not isinstance(message.get("method"), str) or ("id" in message and id_ is None)):
            self.error(id_, -32600, "Invalid Request")
            return
        method, params = message["method"], message.get("params", {})
        if "id" not in message:  # a notification: no reply
            if method == "notifications/cancelled" and isinstance(params, dict):
                self.calls.cancel(params.get("requestId"))
            return
        if not isinstance(params, dict):
            self.error(id_, -32602, "params must be an object")
            return
        if method == "initialize":
            asked = params.get("protocolVersion")
            self.ui = mcp_app.supports_ui(params)
            self.send({"id": id_, "result": {
                "protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
                "capabilities": {"tools": {}, **({"resources": {}} if self.ui else {})},
                "serverInfo": {"name": "trazo", "version": __version__},
                "instructions": INSTRUCTIONS}})
        elif method == "ping":
            self.send({"id": id_, "result": {}})
        elif method == "tools/list":
            self.send({"id": id_, "result": {"tools": mcp_app.ui_tools(TOOLS) if self.ui else TOOLS}})
        elif method == "resources/list" and self.ui:
            self.send({"id": id_, "result": {"resources": mcp_app.RESOURCES}})
        elif method == "resources/read" and self.ui:
            try:
                self.send({"id": id_, "result": mcp_app.read_resource(str(params.get("uri")))})
            except ToolError as exc:
                self.error(id_, -32002, str(exc))  # MCP's "resource not found"
        elif method == "tools/call":
            name, args = params.get("name"), params.get("arguments") or {}
            if (not isinstance(name, str) or name not in self.tools
                    or (name in mcp_app.APP_TOOL_NAMES and not self.ui)):
                self.error(id_, -32602, f"Unknown tool: {name}")
            elif not isinstance(args, dict):
                self.error(id_, -32602, "arguments must be an object")
            else:
                self.calls.begin(id_)
                threading.Thread(target=self.call_tool, args=(id_, name, args), daemon=True).start()
        else:
            self.error(id_, -32601, f"Method not found: {method}")

    def run(self) -> None:
        try:
            for line in self.stdin:
                if not line.strip():
                    continue
                try:
                    message = json.loads(line)
                except ValueError:
                    self.error(None, -32700, "Parse error")
                    continue
                try:
                    self.handle(message)
                except Exception as exc:  # a bad message must not stop the server
                    if request_id(message) is not None and "method" in message:
                        self.error(request_id(message), -32603, f"Internal error: {exc!r}")
                    else:
                        print(f"trazo mcp: {exc!r}", file=sys.stderr, flush=True)
        finally:
            self.calls.close()
            self.previews.stop_all()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--serve"] and len(argv) == 2:
        serve_child(int(argv[1]))
        return 0
    if argv:
        print("usage: trazo mcp   (an MCP server on stdin/stdout; see trazo.mcp)", file=sys.stderr)
        return 2
    # Each tool names its project; an inherited root would override it.
    os.environ.pop("TRAZO_ROOT", None)

    def stop(signum, frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    stdin = open(sys.stdin.fileno(), encoding="utf-8", errors="replace", closefd=False)
    stdout = open(sys.stdout.fileno(), "w", encoding="utf-8", newline="\n", closefd=False)
    sys.stdout = sys.stderr  # a stray print must not break the protocol
    Server(stdin, stdout).run()
    return 0
