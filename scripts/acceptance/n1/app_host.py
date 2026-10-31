"""A stand-in for the desktop app, for n1-force-quit-build: the process that
owns the MCP server's pipes. Killing it closes them, as a force-quit does,
which the harness itself cannot do because it must keep its own ends open.

    app_host.py PLUGIN_ROOT PAPER STATE_FILE

Starts plugin/mcp.sh, runs preview_start on PAPER, writes STATE_FILE (the
server's pid and the preview_start result), sends `build` without waiting for
its reply and then idles until it is killed."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

REPLY_TIMEOUT = 60


def main(plugin: str, paper: str, state_file: str) -> None:
    server = subprocess.Popen(["/bin/sh", f"{plugin}/plugin/mcp.sh"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=open(f"{state_file}.stderr", "w"), text=True)
    next_id = 0

    def send(method: str, params: dict, notification: bool = False) -> int:
        nonlocal next_id
        message = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            next_id += 1
            message["id"] = next_id
        server.stdin.write(json.dumps(message) + "\n")
        server.stdin.flush()
        return next_id

    def answer(id_: int) -> dict:
        deadline = time.monotonic() + REPLY_TIMEOUT
        while time.monotonic() < deadline:
            reply = json.loads(server.stdout.readline())
            if reply.get("id") == id_:
                return reply["result"]
        raise SystemExit(f"no reply to request {id_}")

    answer(send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                               "clientInfo": {"name": "app-host", "version": "0"}}))
    send("notifications/initialized", {}, notification=True)
    started = answer(send("tools/call", {"name": "preview_start", "arguments": {"project": paper}}))
    scratch = Path(f"{state_file}.tmp")
    scratch.write_text(json.dumps({"server": server.pid, "preview": started["structuredContent"]}))
    scratch.rename(state_file)  # complete when it exists
    send("tools/call", {"name": "build", "arguments": {"project": paper}})
    time.sleep(600)


if __name__ == "__main__":
    main(*sys.argv[1:4])
