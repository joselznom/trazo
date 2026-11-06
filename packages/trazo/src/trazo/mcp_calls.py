"""How the trazo MCP server (trazo.mcp) runs a tool call: the
project it names, and `trazo` subprocesses in the project folder, each
in its own process group, killed on notifications/cancelled or when the
server exits. Each subprocess call has a finite deadline (1200 seconds by
default; TRAZO_MCP_TIMEOUT sets positive finite seconds)."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from . import portable
from .project import CONFIG_NAME, ConfigError, Project


class ToolError(Exception):
    """A tool failed; the message goes back to the agent as a tool error."""


def load(args: dict[str, Any], required: bool = True) -> Project | None:
    value = args.get("project")
    if not value:
        if required:
            raise ToolError("project is required: the absolute path of the paper folder")
        return None
    if not isinstance(value, str):
        raise ToolError("project must be a string: the absolute path of the paper folder")
    root = Path(value)
    if not root.is_absolute():
        raise ToolError(f"project must be an absolute path, not {value!r}")
    if not (root / CONFIG_NAME).is_file():
        if not required:
            return None
        raise ToolError(f"{root} has no {CONFIG_NAME}")
    try:
        return Project.load(root.resolve())
    except ConfigError as exc:
        raise ToolError(str(exc)) from exc


def trazo_argv(*argv: str) -> list[str]:
    # -P: the project folder is the cwd and must not shadow the standard library.
    return [sys.executable, "-P", "-m", "trazo", *argv]


def project_env(project: Project) -> dict[str, str]:
    return {**os.environ, "TRAZO_ROOT": str(project.root)}


class Calls:
    """Tool calls in progress, by request id, and the subprocess each one
    runs, in its own process group."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.cancelled: dict[Any, bool] = {}
        self.processes: dict[Any, subprocess.Popen] = {}
        self.closed = False

    def begin(self, key) -> None:
        with self.lock:
            self.cancelled[key] = False

    def end(self, key) -> bool:
        """Forget the call; True when it was cancelled (send no reply)."""
        with self.lock:
            return self.cancelled.pop(key, False)

    def cancel(self, key) -> None:
        with self.lock:
            if key not in self.cancelled:
                return
            self.cancelled[key] = True
            process = self.processes.get(key)
        if process:
            portable.kill_group(process.pid)

    def run(self, key, where: Project | Path, *argv: str, env=None) -> subprocess.CompletedProcess:
        """`trazo ARGV` for the call `key`, with `env` added, in the project
        folder, or in the folder `where` (outside any project: paper_create)."""
        try:
            timeout = float(os.environ.get("TRAZO_MCP_TIMEOUT", "1200"))
        except ValueError:
            raise ToolError("TRAZO_MCP_TIMEOUT must be a positive finite number of seconds") from None
        if not 0 < timeout < float("inf"):
            raise ToolError("TRAZO_MCP_TIMEOUT must be a positive finite number of seconds")
        cwd, base = (where.root, project_env(where)) if isinstance(where, Project) else (where, os.environ)
        with self.lock:
            if self.closed or self.cancelled.get(key):
                raise ToolError("cancelled")
            process = subprocess.Popen(trazo_argv(*argv), cwd=cwd, env={**base, **(env or {})},
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       encoding="utf-8", errors="replace", **portable.group_options())
            self.processes[key] = process
        try:
            try:
                stdout, stderr = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                portable.kill_group(process.pid)
                try:
                    stdout, stderr = process.communicate(timeout=5)
                except subprocess.TimeoutExpired as exc:
                    # A descendant that detached itself may still hold a pipe open.
                    stdout, stderr = exc.stdout, exc.stderr
                    process.kill()
                    process.wait(timeout=5)
                finally:
                    if os.name != "nt":  # Windows reader threads own these streams until EOF.
                        process.stdout.close()
                        process.stderr.close()
                output = [value.decode("utf-8", "replace") if isinstance(value, bytes) else value or ""
                          for value in (stderr, stdout)]
                message = (f"trazo {' '.join(argv)} timed out after {timeout:g} s; "
                           "the call was stopped. Fix the blocked command and retry, or increase "
                           "TRAZO_MCP_TIMEOUT for a deliberately longer operation")
                raise ToolError((message + "\n" + "\n".join(output)).strip()) from None
        finally:
            with self.lock:
                self.processes.pop(key, None)
        return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)

    def close(self) -> None:
        with self.lock:
            self.closed = True
            processes = list(self.processes.values())
        for process in processes:
            portable.kill_group(process.pid)
