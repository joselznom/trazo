"""How the trazo MCP server (trazo.mcp) runs a tool call: the
project it names, and `trazo` subprocesses in the project folder, each
in its own process group, killed on notifications/cancelled or when the
server exits."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

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


def kill_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


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
            kill_group(process)

    def run(self, key, where: Project | Path, *argv: str, env=None) -> subprocess.CompletedProcess:
        """`trazo ARGV` for the call `key`, with `env` added, in the project
        folder, or in the folder `where` (outside any project: paper_create)."""
        cwd, base = (where.root, project_env(where)) if isinstance(where, Project) else (where, os.environ)
        with self.lock:
            if self.closed or self.cancelled.get(key):
                raise ToolError("cancelled")
            process = subprocess.Popen(trazo_argv(*argv), cwd=cwd, env={**base, **(env or {})},
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       encoding="utf-8", errors="replace", start_new_session=True)
            self.processes[key] = process
        try:
            stdout, stderr = process.communicate()
        finally:
            with self.lock:
                self.processes.pop(key, None)
        return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)

    def close(self) -> None:
        with self.lock:
            self.closed = True
            processes = list(self.processes.values())
        for process in processes:
            kill_group(process)
