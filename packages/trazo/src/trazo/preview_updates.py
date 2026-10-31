"""The preview's update notice: GET /__updates compares local versions, and
POST /__update, sent when the user confirms in the preview, updates
trazo and the paper in one step.

Nothing goes over the network. The versions are the plugin's (its
.claude-plugin/plugin.json, found through $TRAZO_PLUGIN_ROOT, which
plugin/mcp.sh sets), the data folder's install (machine.installed_version)
and the paper's template (.trazo/manifest.json) against the one each
trazo ships (assets/template/index.json). The app updates the
plugin itself: when the plugin is older than the install (the other app's
newer plugin installed it) or than the paper, `action` is "plugin" and the
preview only says so.

An update, one at a time:
1. When the preview runs the data folder's trazo and the plugin is
   newer, `setup.sh --offline` reinstalls trazo from the plugin's
   files. Exit 3 means uv, Python or Pandoc must be downloaded, which
   only the setup skill does, in the chat.
2. A paper made by `trazo new` is updated by `trazo update`, run
   with this Python, so it is the trazo just installed.
3. When the running trazo is no longer the installed one, the preview
   restarts as the installed one (RESTART, set by trazo.mcp_preview:
   the same process, port and pipe to the MCP server). The MCP server
   keeps its code until a new chat, but the commands it runs are the new
   trazo.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import __version__, machine, manifest
from .local_server import Conflict
from .project import PROJECT, Project

SETUP_SCRIPT = Path("plugin") / "scripts" / "setup.sh"
PLUGIN_PACKAGE = Path("packages") / "trazo" / "src" / "trazo"
PACKAGE = Path(__file__).resolve().parent
TEMPLATE_INDEX = Path("assets") / "template" / "index.json"
OFFLINE_EXIT = 3
SETUP_TIMEOUT = 600
UPDATE_TIMEOUT = 120
RESTART_DELAY = 0.5  # seconds: time for the reply to reach the page first
LOCK = threading.Lock()
# Set by trazo.mcp_preview.serve_child; None in a preview run from a terminal.
RESTART: Callable[[], None] | None = None


def runs_install() -> bool:
    """True when this process runs the data folder's trazo."""
    return Path(sys.prefix).resolve() == (machine.data_dir() / "venv").resolve()


def release(version: str | None) -> tuple[int, ...] | None:
    """A release version as three numbers ("0.9" is 0.9.0); None if not one."""
    key = machine.release_key(version)
    return (*key, 0, 0, 0)[:3] if key else None


def paper_template(project: Project) -> tuple[int, ...] | None:
    """The template the paper was made or last updated with; None when it
    has no manifest (not made by `trazo new`)."""
    try:
        return manifest.version_key(manifest.read(project.root)["template"])
    except ValueError:
        return None


def shipped_template(package: Path) -> tuple[int, ...] | None:
    """The template a trazo package folder ships (its variants index)."""
    try:
        index = json.loads((package / TEMPLATE_INDEX).read_text(encoding="utf-8"))
        return manifest.version_key(index["template"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def plan(project: Project = PROJECT) -> dict[str, Any]:
    """What the notice offers. `action`: "update" (trazo, the paper or
    both), "plugin" (update the plugin in the app) or None."""
    root, plugin = machine.plugin_version()
    installed = machine.installed_version() if runs_install() else ""
    ship, have, paper = release(plugin), release(installed), paper_template(project)
    in_plugin = shipped_template(Path(root) / PLUGIN_PACKAGE) if root else None
    install = bool(ship and have and ship > have)
    target = plugin if install else installed or __version__
    restart = install or bool(installed and installed != __version__ and RESTART)
    # This package's files on disk: after a reinstall, the new trazo's.
    goal = in_plugin if install else shipped_template(PACKAGE)
    paper_behind = bool(paper and goal and paper < goal)
    if (ship and have and have > ship) or (paper and in_plugin and paper > in_plugin):
        action = "plugin"
    elif install or restart or paper_behind:
        action = "update"
    else:
        action = None
    return {"action": action, "version": target, "install": install, "paper": paper_behind, "restart": restart,
            "versions": {"plugin": plugin or None, "installed": installed or None, "running": __version__,
                         "paper": ".".join(map(str, paper[:3])) if paper else None}}


def last_line(result: subprocess.CompletedProcess[str]) -> str:
    lines = (result.stderr or result.stdout or "").strip().splitlines()
    return lines[-1] if lines else f"exit code {result.returncode}"


def apply(project: Project = PROJECT) -> dict[str, Any]:
    """Run the update `plan` offers; Conflict when there is none or one is running."""
    if not LOCK.acquire(blocking=False):
        raise Conflict("an update is already running")
    try:
        step = plan(project)
        if step["action"] != "update":
            raise Conflict("nothing to update here" if step["action"] is None
                           else "the plugin is older than trazo: update the plugin in the app")
        if step["install"]:
            root, _ = machine.plugin_version()
            result = subprocess.run(["/bin/sh", str(Path(root) / SETUP_SCRIPT), "--offline", root],
                                    capture_output=True, text=True, timeout=SETUP_TIMEOUT)
            if result.returncode:
                return {"ok": False, "download": result.returncode == OFFLINE_EXIT, "error": last_line(result)}
        restart = RESTART if step["restart"] else None
        reply: dict[str, Any] = {"ok": True, "version": step["version"], "needs_merge": [],
                                 "restart": restart is not None, "new_chat": step["restart"]}
        if paper_template(project):
            result = subprocess.run([sys.executable, "-P", "-m", "trazo", "update", "--json", str(project.root)],
                                    cwd=project.root, env={**os.environ, "TRAZO_ROOT": str(project.root)},
                                    capture_output=True, text=True, timeout=UPDATE_TIMEOUT)
            if result.returncode:  # trazo itself may be updated: the restart still runs
                reply.update(ok=False, download=False, error=last_line(result))
            else:
                reply["needs_merge"] = json.loads(result.stdout)["needs_merge"]
        if restart is not None:
            threading.Timer(RESTART_DELAY, restart).start()
        return reply
    finally:
        LOCK.release()


def get_route(path: str, query: dict[str, list[str]]) -> Callable[[], Any] | None:
    return plan if path == "/__updates" else None


def post_route(path: str, body: Callable[[], Any]) -> Callable[[], Any] | None:
    return apply if path == "/__update" else None
