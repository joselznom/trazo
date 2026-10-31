"""A small JSON-RPC client for `codex app-server` over stdio.

The app-server answers what `codex exec` cannot show without a model: which
MCP tools a plugin gives (`mcpServerStatus/list`), the state of its hooks
(`hooks/list`) and config writes (`config/batchWrite`). One client is one
app-server process, started through the sandbox so cleanup stops it.
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from typing import Any

from ..sandbox import Sandbox


class CodexError(Exception):
    """A Codex CLI or app-server call failed or answered something unexpected."""


class AppServer:
    """    with AppServer(sb, argv) as server:     # argv: the codex command and its global options
        server.request("hooks/list", {"cwds": [str(sb.work)]})
    """

    def __init__(self, sandbox: Sandbox, argv: list[str], env: dict[str, str], timeout: float = 30) -> None:
        self.timeout, self.next_id = timeout, 0
        self.replies: queue.Queue[dict] = queue.Queue()
        self.stderr_path = sandbox.tmp / f"app-server-{time.monotonic_ns()}.err"
        self._stderr = self.stderr_path.open("w")
        self.process = sandbox.spawn([*argv, "app-server"], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=self._stderr, text=True, encoding="utf-8")
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        for line in self.process.stdout:
            try:
                self.replies.put(json.loads(line))
            except ValueError:
                continue  # not a protocol line

    def _send(self, message: dict) -> None:
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def request(self, method: str, params: dict | None = None, timeout: float | None = None) -> Any:
        """One request; notifications the server sends in between are skipped."""
        timeout = timeout or self.timeout
        self.next_id += 1
        self._send({"id": self.next_id, "method": method, "params": params or {}})
        deadline = time.monotonic() + timeout
        while True:
            try:
                reply = self.replies.get(timeout=max(deadline - time.monotonic(), 0.01))
            except queue.Empty:
                raise CodexError(f"no reply to {method} in {timeout}s; stderr: {self._stderr_tail()!r}") from None
            if reply.get("id") != self.next_id:
                continue
            if "error" in reply:
                raise CodexError(f"{method}: {reply['error']}")
            return reply["result"]

    def initialize(self) -> dict:
        result = self.request("initialize", {"clientInfo": {"name": "acceptance", "title": "acceptance", "version": "0"}})
        self._send({"method": "initialized"})
        return result

    def _stderr_tail(self) -> str:
        self._stderr.flush()
        return self.stderr_path.read_text(errors="replace")[-500:]

    def __enter__(self) -> AppServer:
        try:
            self.initialize()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        if not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            self.process.wait(5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(5)
        self._stderr.close()


def hooks_of(listing: dict, plugin_id: str) -> list[dict]:
    """The hooks `hooks/list` reports for one plugin."""
    return [hook for entry in listing["data"] for hook in entry["hooks"] if hook.get("pluginId") == plugin_id]


def trust_edit(hooks: list[dict]) -> dict:
    """The `config/batchWrite` params that trust `hooks` as they are now. A hook's
    `currentHash` is `<hash>/<content hash>`: the two values config.toml stores. The key
    holds dots (`hooks/hooks.json`), so the whole `hooks.state` table is written, not a dotted path."""
    state = {}
    for hook in hooks:
        trusted_hash, content_hash = hook["currentHash"].split("/")
        state[hook["key"]] = {"trusted_hash": trusted_hash, "trusted_content_hash": content_hash}
    return {"edits": [{"keyPath": "hooks.state", "mergeStrategy": "upsert", "value": state}]}



def approve_edit(plugin_id: str, server_name: str) -> dict:
    """The `config/batchWrite` params that approve every tool of one plugin-provided MCP server
    (`plugins.<plugin>.mcp_servers.<server>.default_tools_approval_mode`, Codex 0.164.0). That is the
    ChatGPT app's "approve this server's tools" at its narrowest: no other server, no sandbox change.
    The plugin id holds an `@`, so the whole `plugins` table is written, as `trust_edit` does for hooks."""
    value = {plugin_id: {"mcp_servers": {server_name: {"default_tools_approval_mode": "approve"}}}}
    return {"edits": [{"keyPath": "plugins", "mergeStrategy": "upsert", "value": value}]}
