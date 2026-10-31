"""What several n1 scenarios share: papers, previews, waiting, and a
fingerprint of a folder to prove that a step left it alone."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import time
from collections.abc import Callable
from pathlib import Path

from ..drive import McpClient
from ..sandbox import Sandbox


def wait_until(condition: Callable[[], object], timeout: float = 15, interval: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return bool(condition())


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def http_get(url: str, timeout: float = 5) -> tuple[int, str]:
    """(status, body) of a GET to a 127.0.0.1 URL, never through a proxy; (0, "") when nothing answers."""
    host, _, port = url.removeprefix("http://").partition(":")
    port, _, path = port.partition("/")
    connection = http.client.HTTPConnection(host, int(port), timeout=timeout)
    try:
        connection.request("GET", "/" + path)
        reply = connection.getresponse()
        return reply.status, reply.read().decode("utf-8", errors="replace")
    except (OSError, http.client.HTTPException):
        return 0, ""
    finally:
        connection.close()


def answers(port: int) -> bool:
    return http_get(f"http://127.0.0.1:{port}/__status")[0] == 200


def serving(info: dict) -> bool:
    """True when the URL in `info` (a preview_start result or preview.json) is that preview of that paper."""
    status, body = http_get(info["url"] + "/__status")
    try:
        return status == 200 and json.loads(body)["server"] == {"pid": info["pid"], "project": info["project"]}
    except (ValueError, KeyError, TypeError):
        return False


def new_paper(mcp: McpClient, folder: Path) -> Path:
    """A paper made the way the agent makes one: the paper_create tool."""
    made = mcp.call("paper_create", folder=str(folder), kind="article", example=False, title="Acceptance paper")
    assert not made.is_error, made.text
    assert (folder / "trazo.toml").is_file(), f"paper_create wrote no trazo.toml in {folder}"
    return folder


def preview_record(paper: Path) -> dict:
    """The paper's .build/preview.json, which is how an agent finds the preview's URL."""
    return json.loads((paper / ".build" / "preview.json").read_text(encoding="utf-8"))


def mcp_session(sandbox: Sandbox, plugin: Path, cwd: Path | None = None) -> McpClient:
    """A new chat's server: started in the sandbox's work folder, as an app starts one."""
    mcp = McpClient(sandbox, plugin, cwd=cwd or sandbox.work)
    mcp.initialize()
    return mcp


def fingerprint(folder: Path) -> dict[str, str]:
    """Every file under `folder` as relative path -> a digest of its content (a link's target for
    a link). Equal fingerprints before and after a step mean it added, removed and changed
    nothing; the modification time is left out because uv rewrites identical files when it
    finds a Python already installed."""
    found = {}
    for here, dirs, files in os.walk(folder):
        for name in dirs + files:
            path = Path(here, name)
            if path.is_symlink():
                found[str(path.relative_to(folder))] = f"link {os.readlink(path)}"
            elif path.is_file():
                found[str(path.relative_to(folder))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def installed_version(sandbox: Sandbox) -> str | None:
    """The trazo version in the data folder's venv, read like the hook reads it (the dist-info folder)."""
    names = [p.name for p in (sandbox.data / "venv").glob("lib/python*/site-packages/trazo-*.dist-info")]
    return names[0].removeprefix("trazo-").removesuffix(".dist-info") if len(names) == 1 else None
