"""Drive the plugin the way the desktop apps do: its MCP server over stdio
through plugin/mcp.sh, and its SessionStart hook."""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .sandbox import Sandbox

PROTOCOL = "2025-06-18"


@dataclass
class ToolResult:
    is_error: bool
    data: Any      # structuredContent when the tool returned some, else the text
    text: str


class McpClient:
    """plugin/mcp.sh in a sandbox, over pipes. Each call is added to the evidence.

        mcp = McpClient(sb, sb.plugin(folder), cwd=paper)
        mcp.initialize()                      # mcp.connect_seconds: spawn to initialize reply
        mcp.tools()                           # names; the stand-in server lists one
        mcp.call("setup_status").data["next"]
        code, stderr = mcp.close()            # closes stdin, as the app does at the end
    """

    def __init__(self, sandbox: Sandbox, plugin_root: Path, cwd: Path | None = None,
                 env: Mapping[str, str] | None = None) -> None:
        self.sandbox = sandbox
        self.started = time.monotonic()
        self.process = sandbox.spawn(["/bin/sh", plugin_root / "plugin/mcp.sh"], env=env, cwd=cwd,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, encoding="utf-8")
        self.replies: queue.Queue[dict] = queue.Queue()
        self.stderr: list[str] = []
        self.connect_seconds: float | None = None
        self.next_id = 0
        threading.Thread(target=self._read_replies, daemon=True).start()
        threading.Thread(target=lambda: [self.stderr.append(line) for line in self.process.stderr], daemon=True).start()

    def _read_replies(self) -> None:
        for line in self.process.stdout:
            try:
                self.replies.put(json.loads(line))
            except ValueError:  # a server printing non-JSON to stdout is a finding of its own
                self.replies.put({"jsonrpc": "2.0", "id": None, "error": {"code": -1, "message": f"not JSON: {line!r}"}})

    def send(self, message: dict | str) -> None:
        self.process.stdin.write((message if isinstance(message, str) else json.dumps(message)) + "\n")
        self.process.stdin.flush()

    def rpc(self, method: str, params: dict | None = None, timeout: float = 60) -> dict:
        self.next_id += 1
        self.send({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params or {}})
        try:
            reply = self.replies.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError(f"no reply to {method} in {timeout}s; stderr: {''.join(self.stderr)[-500:]!r}") from None
        if reply.get("id") != self.next_id:
            raise AssertionError(f"reply {reply} does not answer request {self.next_id} ({method})")
        return reply

    def initialize(self) -> dict:
        reply = self.rpc("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                        "clientInfo": {"name": "acceptance", "version": "0"}})
        self.connect_seconds = time.monotonic() - self.started
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.sandbox.evidence.note(f"mcp initialize: {reply['result']['serverInfo']} in {self.connect_seconds:.2f}s")
        return reply["result"]

    def tools(self) -> list[str]:
        names = [tool["name"] for tool in self.rpc("tools/list")["result"]["tools"]]
        self.sandbox.evidence.note(f"mcp tools: {', '.join(names)}")
        return names

    def call(self, name: str, timeout: float = 120, **arguments: Any) -> ToolResult:
        reply = self.rpc("tools/call", {"name": name, "arguments": arguments}, timeout)
        if "error" in reply:
            raise AssertionError(f"{name}: protocol error {reply['error']}")
        result = reply["result"]
        text = "".join(part.get("text", "") for part in result.get("content", []))
        found = ToolResult(bool(result.get("isError")), result.get("structuredContent", text), text)
        self.sandbox.evidence.data(f"tool {name}({arguments or ''})", {"is_error": found.is_error, "result": found.data})
        return found

    def close(self, timeout: float = 30) -> tuple[int, str]:
        """Close stdin (the app ending the session); (exit code, stderr)."""
        if not self.process.stdin.closed:
            self.process.stdin.close()
        code = self.process.wait(timeout)
        time.sleep(0.1)  # let the reader threads drain
        return code, "".join(self.stderr)


@dataclass
class HookResult:
    code: int
    stdout: str
    context: str | None   # hookSpecificOutput.additionalContext; None when the hook said nothing


def run_hook(sandbox: Sandbox, plugin_root: Path, cwd: Path | None = None, source: str = "startup",
             env: Mapping[str, str] | None = None) -> HookResult:
    """hooks/hooks.json's SessionStart command, as the app runs it."""
    done = sandbox.run(["/bin/sh", plugin_root / "plugin/session-start.sh"], env=env, cwd=cwd, timeout=10,
                       input=json.dumps({"hook_event_name": "SessionStart", "source": source}))
    context = None
    if done.stdout.strip():
        context = json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"]
    return HookResult(done.returncode, done.stdout, context)
