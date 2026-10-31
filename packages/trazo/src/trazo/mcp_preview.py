"""The live preview the trazo MCP server (trazo.mcp) starts: a child
process (`trazo mcp --serve PORT`) that stops itself when its stdin
closes, that is when the server exits. It binds 127.0.0.1:8765, else a
free port, then writes <build>/preview.json (url, port, pid, project) and
removes it when it stops; /__status names its pid and project, so a
session reuses only a preview really serving the project, also another
session's, and stops another session's with SIGTERM."""

from __future__ import annotations

import errno
import functools
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from . import preview_inputs
from .mcp_calls import ToolError, load, project_env, trazo_argv
from .preview_inputs import preview_status
from .project import Project
from .review_storage import write_json

PREVIEW_PORT = 8765
PREVIEW_FILE = "preview.json"
START_TIMEOUT = 20.0


def read_info(path: Path) -> dict[str, Any] | None:
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return info if isinstance(info, dict) else None


def info_path(project: Project) -> Path:
    return project.path("build") / PREVIEW_FILE


class Previews:
    """Preview child processes started by this server, by project root,
    with the preview.json each one writes."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.children: dict[Path, tuple[subprocess.Popen, Path]] = {}

    @staticmethod
    def running(project: Project) -> dict[str, Any] | None:
        """The preview in preview.json when it answers as this project's, with that pid."""
        info = read_info(info_path(project))
        try:
            expected = {"pid": int(info["pid"]), "project": str(project.root)}
            status = preview_status(int(info["port"]))
        except (TypeError, KeyError, ValueError):
            return None
        if info.get("project") == expected["project"] and status and status.get("server") == expected:
            return info
        return None

    def start(self, args, run):
        return preview_inputs.start(load(args), run, self.serve)

    def serve(self, project: Project) -> dict[str, Any]:
        with self.lock:
            info = self.running(project)
            if info:
                return {**info, "reused": True}
            self.stop_child(project.root)
            log_path = project.path("build") / "preview.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "wb") as log:
                child = subprocess.Popen(trazo_argv("mcp", "--serve", str(PREVIEW_PORT)), cwd=project.root,
                                         env=project_env(project), stdin=subprocess.PIPE, stdout=log,
                                         stderr=subprocess.STDOUT)
            self.children[project.root] = (child, info_path(project))
            deadline = time.monotonic() + START_TIMEOUT
            while True:
                info = self.running(project)
                if info and info["pid"] == child.pid:
                    return {**info, "reused": False}
                if info:  # another session's preview of this project started first
                    self.stop_child(project.root)
                    return {**info, "reused": True}
                if child.poll() is not None or time.monotonic() > deadline:
                    self.stop_child(project.root)
                    tail = log_path.read_text(encoding="utf-8", errors="replace").strip()[-2000:]
                    raise ToolError(f"the preview did not start:\n{tail}")
                time.sleep(0.1)

    def stop(self, args):
        project = load(args)
        with self.lock:
            entry = self.children.get(project.root)
            exited = entry[0] if entry is not None and entry[0].poll() is not None else None
            stopped = self.stop_child(project.root)
            # Another session may have stopped this one's preview and started its own.
            info = self.running(project)
            if not info:
                if exited is not None:
                    return {"stopped": False, "pid": exited.pid, "reason": f"the preview had already exited "
                            f"(code {exited.returncode}); see .build/preview.log"}
                if stopped is not None:
                    return {"stopped": True, "pid": stopped, "started_here": True}
                return {"stopped": False, "reason": "no preview is running for this project"}
        # Another session's: running() just matched its pid to /__status, so the
        # signal reaches that preview. It stops on SIGTERM and removes preview.json;
        # its session starts a new one on its next preview_start.
        try:
            os.kill(info["pid"], signal.SIGTERM)
        except ProcessLookupError:
            pass
        # It stops answering first and removes preview.json last.
        deadline = time.monotonic() + START_TIMEOUT
        while ((self.running(project) or (read_info(info_path(project)) or {}).get("pid") == info["pid"])
               and time.monotonic() < deadline):
            time.sleep(0.1)
        if self.running(project):
            raise ToolError(f"the preview (pid {info['pid']}) did not stop")
        return {"stopped": True, "pid": info["pid"], "started_here": False}

    def stop_child(self, root: Path) -> int | None:
        """Stop this server's preview of `root`; its pid, or None."""
        child, path = self.children.pop(root, (None, None))
        if child is None:
            return None
        if child.poll() is None:
            child.stdin.close()  # the child stops itself (see serve_child)
            try:
                child.wait(10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        if (read_info(path) or {}).get("pid") == child.pid:  # the child could not remove it
            path.unlink(missing_ok=True)
        return child.pid

    def stop_all(self) -> None:
        with self.lock:
            for root in list(self.children):
                self.stop_child(root)


def restart(port: int) -> None:
    """Run this preview again as the installed trazo (trazo.preview_updates):
    the same pid, so preview.json and the MCP server's Previews stay right, and
    the same stdin pipe; the listening socket is not inherited, so the port is
    free to bind again."""
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(sys.executable, trazo_argv("mcp", "--serve", str(port)))


def serve_child(port: int) -> None:
    """The preview, run by preview_start in the project folder (see the
    module docstring). It stops on SIGTERM, which server.serve handles, or
    when its stdin closes."""
    from . import preview_updates, server
    from .project import PROJECT
    path = info_path(PROJECT)

    def record(bound: int) -> None:
        write_json(path, {"url": f"http://127.0.0.1:{bound}", "port": bound, "pid": os.getpid(),
                          "project": str(PROJECT.root)}, indent=2)
        preview_updates.RESTART = functools.partial(restart, bound)

    def watch_stdin():
        sys.stdin.buffer.read()
        os.kill(os.getpid(), signal.SIGTERM)
    threading.Thread(target=watch_stdin, daemon=True).start()
    try:
        try:
            server.serve(port, on_bind=record)
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE or not port:
                raise
            server.serve(0, on_bind=record)
    finally:
        if (read_info(path) or {}).get("pid") == os.getpid():
            path.unlink(missing_ok=True)
