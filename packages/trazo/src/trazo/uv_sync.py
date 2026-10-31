"""`uv sync` of a project's Python environment (.venv), which the build runs
when uv.lock changes or .venv is missing (trazo.build_graph).

A sandbox (the ChatGPT app's) may let commands write only inside the
workspace; uv then cannot open its own cache, so the sync runs again with
the cache in the project's git-ignored .cache/uv."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .machine import uv_environment


def sync(uv: str, where: str, root: Path, locked: bool) -> int:
    """Run `uv sync` (`--locked` when the project has uv.lock) in root with
    the uv found `where` (trazo.machine.resolve_tool); its exit code."""
    env = {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}
    if where == "data folder":  # setup's uv: its Python and cache stay in the data folder
        env.update(uv_environment())
    command = [uv, "sync", "--quiet", *(["--locked"] if locked else [])]
    result = subprocess.run(command, cwd=root, env=env, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    if result.returncode and "Failed to initialize cache" in result.stderr:
        print("uv sync: uv's cache is not writable here; using .cache/uv", file=sys.stderr)
        env["UV_CACHE_DIR"] = str(root / ".cache" / "uv")
        result = subprocess.run(command, cwd=root, env=env, stderr=subprocess.PIPE, encoding="utf-8",
                                errors="replace")
    sys.stderr.write(result.stderr)
    return result.returncode
