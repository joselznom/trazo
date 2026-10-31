"""What preview_start (trazo.mcp) adds around the preview, so that it
shows the paper as the data and scripts make it now.

First it runs `trazo build --prepare=inputs`, as a terminal does before
`trazo preview serve`: values, tables and the steps before expand (the
figure scripts), no diagram export, so no Chrome. It is hash-based: with
nothing changed it runs no project script. It runs as the call's subprocess
(trazo.mcp.Calls), so a cancelled call or an exiting server kill it. A
failed preparation is a tool error and no preview starts; a project whose
commands run Make gets the build's own refusal.

That subprocess takes the project's build lock itself, so the lock lasts
as long as the build writes, also when the server dies first. While another
build holds the lock (the build tool, scripts/trazo, the preview's Word
export, another preview_start), it waits up to LOCK_WAIT seconds
($TRAZO_BUILD_LOCK_WAIT, trazo.build_lock), then the call fails
saying so, rather than running into the app's tool-call timeout.

A reused preview (also another chat's) re-renders changed outputs once its
watcher (trazo.preview_watch) sees them. The call returns when the
preview's /__status reports `synced` (trazo.preview.WATCHER) after the
preparation ended, with a note naming each page whose build then failed
(the page keeps its last good version); after SETTLE_WAIT seconds it
returns with a note instead (the page reloads itself once the render
lands). A preview whose status has no `synced` (an older trazo) is not
waited for. preview_status reads that status (also trazo.mcp's check of
a preview to reuse).
"""

from __future__ import annotations

import http.client
import json
import subprocess
import time
from collections.abc import Callable
from typing import Any

from .build_lock import BUSY, WAIT_ENV
from .project import Project

LOCK_WAIT = 20.0  # seconds
SETTLE_WAIT = 30.0  # seconds
POLL = 0.25
LOG_TAIL = 4000


def preview_status(port: int) -> dict[str, Any] | None:
    """GET /__status from 127.0.0.1:PORT, never through a proxy; None when nothing answers."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        connection.request("GET", "/__status")
        reply = connection.getresponse()
        data = json.loads(reply.read()) if reply.status == 200 else None
    except (OSError, ValueError, http.client.HTTPException):
        return None
    finally:
        connection.close()
    return data if isinstance(data, dict) else None


def start(project: Project, run: Callable[..., subprocess.CompletedProcess[str]],
          serve: Callable[[Project], dict[str, Any]]) -> dict[str, Any]:
    """The preview_start result: prepare, then `serve` (trazo.mcp's
    Previews.serve, which starts or reuses the preview), then settle."""
    prepared = prepare(project, run)
    return settle(project, serve(project), prepared)


def prepare(project: Project, run: Callable[..., subprocess.CompletedProcess[str]]) -> float:
    """Run `trazo build --prepare=inputs` through `run` (the call's
    cancellable trazo.mcp.Calls.run); the time.time() it ended."""
    from .mcp import ToolError  # mcp imports this module
    result = run(project, "build", "--prepare=inputs", env={WAIT_ENV: str(LOCK_WAIT)})
    if result.returncode == BUSY:
        raise ToolError(f"another build of this project is running (the build tool, scripts/trazo, the "
                        f"preview's Word export or another preview_start) and was still running after "
                        f"{LOCK_WAIT:g} s, so no preview was started; call preview_start again when it ends")
    if result.returncode:
        # stderr last: it ends with the error, which the tail must keep.
        log = f"{result.stdout.strip()}\n{result.stderr.strip()}".strip()
        raise ToolError(f"values, tables and figures could not be regenerated (trazo build --prepare=inputs "
                        f"exited with {result.returncode}), so no preview was started; fix what the log below "
                        f"reports, then call preview_start again:\n{log[-LOG_TAIL:]}")
    return time.time()


def settle(project: Project, info: dict[str, Any], prepared: float) -> dict[str, Any]:
    """`info`, the preview_start result, once a reused preview has rendered
    what existed at `prepared`, with a note when a page failed to build or
    SETTLE_WAIT runs out."""
    if not info["reused"]:
        return info  # a new preview builds from the prepared inputs
    deadline = time.monotonic() + SETTLE_WAIT
    while time.monotonic() < deadline:
        status = preview_status(int(info["port"]))
        if status is not None:
            synced = status.get("synced")
            if not isinstance(synced, (int, float)) or synced >= prepared:
                return with_failures(project, info, status)
        time.sleep(POLL)
    return {**info, "note": f"the preview had not yet shown the regenerated values, tables and figures after "
                            f"{SETTLE_WAIT:.0f} s; its page reloads itself once it does"}


def with_failures(project: Project, info: dict[str, Any], status: dict[str, Any]) -> dict[str, Any]:
    """`info` with a note naming each page whose last build failed, and the
    first line of its error."""
    failed = []
    for name in project.documents:
        state = status.get(name)
        error = state.get("error") if isinstance(state, dict) else None
        if isinstance(error, str) and error.strip():
            failed.append(f"{name}: {error.strip().splitlines()[0]}")
    if not failed:
        return info
    return {**info, "note": f"the preview could not render the current sources, so its page still shows the last "
                            f"version that rendered ({'; '.join(failed)}); once that is fixed the page reloads "
                            "itself"}
