"""Process and artifact ownership for the workflow measurements (stdlib only)."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import platform
import queue
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "packages/trazo/src"
SCRIPTS = Path(__file__).resolve().parent


def environment(root: Path, profile: Path | None = None) -> dict[str, str]:
    env = {**os.environ, "TRAZO_ROOT": str(root), "PYTHONPATH": str(SOURCE)}
    if profile:
        env.update(PYTHONPATH=os.pathsep.join((str(SCRIPTS / "instrument"), str(SOURCE))),
                   TRAZO_MEASURE_PROFILE=str(profile))
    return env


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Malformed Unicode is evidence in fuzz results, including lone surrogates.
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", errors="backslashreplace")


def command(root: Path, *args: str, timeout: float = 180,
            profile: Path | None = None) -> subprocess.CompletedProcess[str]:
    with subprocess.Popen([sys.executable, "-P", "-m", "trazo", *args], cwd=root,
                          env=environment(root, profile), stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, start_new_session=True) as proc:
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            raise
    result = subprocess.CompletedProcess(proc.args, proc.returncode, out, err)
    if result.returncode:
        raise RuntimeError(f"{args}: exit {result.returncode}\n{out}\n{err}")
    return result


def request(port: int, path: str, body: dict | None = None) -> tuple[int, Any]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        conn.request("POST" if body is not None else "GET", path,
                     json.dumps(body) if body is not None else None,
                     {"Content-Type": "application/json"} if body is not None else {})
        reply = conn.getresponse()
        data = reply.read()
        try:
            data = json.loads(data)
        except ValueError:
            data = data.decode("utf-8", "replace")
        return reply.status, data
    finally:
        conn.close()


class MCP:
    """A real initialized stdio client, including cancellation-safe teardown."""

    def __init__(self, root: Path, profile: Path | None = None) -> None:
        self.root, self.sequence, self.port = root, 0, None
        self.log: list[str] = []
        self.answers: queue.Queue = queue.Queue()
        self.proc = subprocess.Popen([sys.executable, "-P", "-m", "trazo", "mcp"],
                                     env=environment(root, profile), cwd=root, text=True,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, start_new_session=True)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        self.errors = threading.Thread(target=self._errors, daemon=True)
        self.errors.start()
        try:
            self.rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                    "clientInfo": {"name": "trazo-measure", "version": "1"}}, timeout=15)
            self.notify("notifications/initialized", {})
        except BaseException:
            self.close()
            raise

    def _read(self) -> None:
        for line in self.proc.stdout:
            try:
                self.answers.put(json.loads(line))
            except ValueError:
                self.answers.put({"error": {"message": line}})
        self.answers.put({"error": {"message": "MCP closed stdout"}})

    def _errors(self) -> None:
        self.log.extend(self.proc.stderr)

    def notify(self, method: str, params: dict) -> None:
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method, "params": params}) + "\n")
        self.proc.stdin.flush()

    def rpc(self, method: str, params: dict, timeout: float = 180) -> dict:
        self.sequence += 1
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.sequence,
                                         "method": method, "params": params}) + "\n")
        self.proc.stdin.flush()
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            try:
                reply = self.answers.get(timeout=max(.01, deadline - time.perf_counter()))
            except queue.Empty:
                self.notify("notifications/cancelled", {"requestId": self.sequence})
                raise TimeoutError(f"MCP {method}: no response within {timeout}s") from None
            if "error" in reply:
                raise RuntimeError(reply["error"])
            if reply.get("id") == self.sequence:
                return reply["result"]
        raise TimeoutError(method)

    def tool(self, name: str, **args: Any) -> dict:
        result = self.rpc("tools/call", {"name": name, "arguments": {"project": str(self.root), **args}})
        if result.get("isError"):
            raise RuntimeError(result)
        return result["structuredContent"]

    def start(self) -> dict:
        result = self.tool("preview_start")
        self.port = int(result["port"])
        return result

    def close(self) -> None:
        try:
            if self.port and self.proc.poll() is None:
                self.tool("preview_stop")
        finally:
            self.proc.stdin.close()
            try:
                self.proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
                self.proc.wait(timeout=5)
            self.reader.join(timeout=2)
            self.errors.join(timeout=2)
            self.proc.stdout.close()
            self.proc.stderr.close()


def metadata() -> dict:
    def output(argv: list[str]) -> str:
        return subprocess.run(argv, capture_output=True, text=True, timeout=15).stdout.strip()
    return {"platform": platform.platform(), "machine": platform.machine(),
            "cpu": output(["sysctl", "-n", "machdep.cpu.brand_string"]) if sys.platform == "darwin" else platform.processor(),
            "memory_bytes": output(["sysctl", "-n", "hw.memsize"]) if sys.platform == "darwin" else "see platform",
            "python": sys.version, "pandoc": output(["pandoc", "--version"]).splitlines()[0],
            "uv": output(["uv", "--version"]), "commit": output(["git", "rev-parse", "HEAD"]),
            "renderer_sha256": hashlib.sha256((SOURCE / "trazo/assets/diagram/renderer.js").read_bytes()).hexdigest(),
            "clock": str(time.get_clock_info("perf_counter")), "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
