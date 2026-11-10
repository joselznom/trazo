"""`uv sync` of a project's Python environment (.venv), which the build runs
when uv.lock changes or .venv is missing (trazo.build_graph).

A sandbox (the ChatGPT app's) may let commands write only inside the
workspace; uv then cannot open its own cache, so the sync runs again with
the cache in the project's git-ignored .cache/uv.

Each run has a deadline: 1800 seconds, enough for a first sync of large
wheels on a slow link, or TIMEOUT_ENV's (positive finite seconds)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from . import processes
from .machine import uv_environment

TIMEOUT_ENV = "TRAZO_UV_TIMEOUT"


def timeout_seconds() -> float:
    try:
        value = float(os.environ.get(TIMEOUT_ENV) or 1800)
    except ValueError:
        value = float("nan")
    if not 0 < value < float("inf"):
        raise ValueError(f"{TIMEOUT_ENV} must be a positive finite number of seconds")
    return value


def sync(uv: str, where: str, root: Path, locked: bool) -> int:
    """Run `uv sync` (`--locked` when the project has uv.lock) in root with
    the uv found `where` (trazo.machine.resolve_tool); its exit code. Raises
    processes.Timeout (an OSError) when it does not finish in time."""
    env = {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}
    if where == "data folder":  # setup's uv: its Python and cache stay in the data folder
        env.update(uv_environment())
    command = [uv, "sync", "--quiet", *(["--locked"] if locked else [])]
    timeout = timeout_seconds()
    try:
        result = processes.run(command, cwd=root, env=env, text=True, timeout=timeout)
        if result.returncode and "Failed to initialize cache" in result.stderr:
            print("uv sync: uv's cache is not writable here; using .cache/uv", file=sys.stderr)
            env["UV_CACHE_DIR"] = str(root / ".cache" / "uv")
            result = processes.run(command, cwd=root, env=env, text=True, timeout=timeout)
    except processes.Timeout as exc:
        exc.hint = f"uv sync; check the network, or set {TIMEOUT_ENV} for a longer sync"
        raise
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode
