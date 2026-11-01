"""The live preview the trazo MCP server (trazo.mcp) starts: a child
process (`trazo mcp --serve PORT`) that stops itself when its stdin
closes, that is when the server exits. It binds 127.0.0.1:8765, else a
free port, then writes <build>/preview.json (url, port, pid, project) and
removes it when it stops; /__status names its pid and project, so a
session reuses only a preview really serving the project, also another
session's, and stops another session's with SIGTERM (on Windows, which
has no SIGTERM to send, by ending its process tree and removing its
preview.json). preview.json also holds the id the server gave the child in
$TRAZO_PREVIEW_ID, which tells the server its own preview: on Windows the
pid Popen sees is the venv launcher's, not the interpreter's."""

from __future__ import annotations

import functools
import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from . import preview_inputs
from .mcp_calls import ToolError, load, project_env, trazo_argv
from .portable import address_in_use, kill_group, pid_alive
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
            preview_id = secrets.token_hex(8)
            with open(log_path, "wb") as log:
                child = subprocess.Popen(trazo_argv("mcp", "--serve", str(PREVIEW_PORT)), cwd=project.root,
                                         env={**project_env(project), PREVIEW_ID_ENV: preview_id},
                                         stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT)
            child.preview_id = preview_id  # type: ignore[attr-defined]
            self.children[project.root] = (child, info_path(project))
            deadline = time.monotonic() + START_TIMEOUT
            while True:
                info = self.running(project)
                if info and started_by(child, info):
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
            recorded = read_info(info_path(project)) or {}
            exited = None
            # On Windows its launcher outlives the interpreter for a moment.
            if entry is not None and (entry[0].poll() is not None
                                      or started_by(entry[0], recorded) and not pid_alive(recorded["pid"])):
                exited = entry[0]
            stopped = self.stop_child(project.root)
            # Another session may have stopped this one's preview and started its own.
            info = self.running(project)
            if not info:
                if exited is not None:
                    return {"stopped": False, "pid": preview_pid(exited, recorded), "reason": f"the preview had "
                            f"already exited (code {exited.returncode}); see .build/preview.log"}
                if stopped is not None:
                    return {"stopped": True, "pid": stopped, "started_here": True}
                return {"stopped": False, "reason": "no preview is running for this project"}
        # Another session's: running() just matched its pid to /__status, so the
        # signal reaches that preview. It stops on SIGTERM and removes preview.json;
        # its session starts a new one on its next preview_start.
        if os.name == "nt":  # no SIGTERM there: end its tree, its builds and Chrome too
            kill_group(info["pid"])
        else:
            try:
                os.kill(info["pid"], signal.SIGTERM)
            except ProcessLookupError:
                pass
        # It stops answering first and removes preview.json last.
        deadline = time.monotonic() + START_TIMEOUT
        if os.name == "nt":  # ended at once: nothing removes preview.json but this
            while pid_alive(info["pid"]) and time.monotonic() < deadline:
                time.sleep(0.1)
            if not pid_alive(info["pid"]) and (read_info(info_path(project)) or {}).get("pid") == info["pid"]:
                info_path(project).unlink(missing_ok=True)
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
        recorded = read_info(path) or {}  # it removes preview.json when it stops
        if child.poll() is None:
            child.stdin.close()  # the child stops itself (see serve_child)
            try:
                child.wait(10)
            except subprocess.TimeoutExpired:
                kill_tree(child)
                child.wait()
        if started_by(child, read_info(path) or {}):  # the child could not remove it
            path.unlink(missing_ok=True)
        return preview_pid(child, recorded)

    def stop_all(self) -> None:
        with self.lock:
            for root in list(self.children):
                self.stop_child(root)


PREVIEW_ID_ENV = "TRAZO_PREVIEW_ID"
RESTART_PORT: list[int] = []  # Windows: the port restart() hands to the next preview


def started_by(child: subprocess.Popen, info: dict[str, Any]) -> bool:
    """Whether preview.json's preview is `child`, also after a restart in place."""
    return bool(info.get("id")) and info.get("id") == getattr(child, "preview_id", None)


def preview_pid(child: subprocess.Popen, info: dict[str, Any]) -> int:
    """The preview's own pid, as preview.json records it: on Windows not the launcher's."""
    return info["pid"] if started_by(child, info) else child.pid


def kill_tree(child: subprocess.Popen) -> None:
    """Kill the preview; on Windows also the interpreter its launcher started."""
    if os.name == "nt":
        kill_group(child.pid)
    else:
        child.kill()


def restart(port: int) -> None:
    """Run this preview again as the installed trazo (trazo.preview_updates):
    the same pid, so preview.json and the MCP server's Previews stay right, and
    the same stdin pipe; the listening socket is not inherited, so the port is
    free to bind again. Windows has no exec: there this preview stops serving
    first, then serve_child runs the new one, with the same stdin and
    $TRAZO_PREVIEW_ID, and waits for it."""
    sys.stdout.flush()
    sys.stderr.flush()
    if os.name == "nt":
        RESTART_PORT.append(port)
        stop_serving()
        return
    os.execv(sys.executable, trazo_argv("mcp", "--serve", str(port)))


def stop_serving() -> None:
    """End this preview's server.serve() from another thread: SIGTERM, which
    serve handles; on Windows, where os.kill would end the process at once
    and skip its cleanup, by shutting the server down."""
    if os.name == "nt":
        from . import server
        server.shutdown()
    else:
        os.kill(os.getpid(), signal.SIGTERM)


def serve_child(port: int) -> None:
    """The preview, run by preview_start in the project folder (see the
    module docstring). It stops on SIGTERM, which server.serve handles, or
    when its stdin closes."""
    from . import preview_updates, server
    from .project import PROJECT
    path = info_path(PROJECT)

    def record(bound: int) -> None:
        write_json(path, {"url": f"http://127.0.0.1:{bound}", "port": bound, "pid": os.getpid(),
                          "id": os.environ.get(PREVIEW_ID_ENV, ""), "project": str(PROJECT.root)}, indent=2)
        preview_updates.RESTART = functools.partial(restart, bound)

    def watch_stdin():
        sys.stdin.buffer.read()
        stop_serving()
    threading.Thread(target=watch_stdin, daemon=True).start()
    try:
        try:
            server.serve(port, on_bind=record)
        except OSError as exc:
            if not address_in_use(exc) or not port:
                raise
            server.serve(0, on_bind=record)
    finally:
        if (read_info(path) or {}).get("pid") == os.getpid():
            path.unlink(missing_ok=True)
    if RESTART_PORT:  # Windows: restart() stopped serving to free the port
        sys.exit(subprocess.call(trazo_argv("mcp", "--serve", str(RESTART_PORT[0]))))
