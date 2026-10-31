#!/usr/bin/env python3
"""A paper made by `trazo new` in a folder that is not a git repository
builds its Word file and serves the preview; the Diff tab says history is
off. Run by `make test-template` with trazo's Python and TRAZO_DEV.

    TRAZO_DEV=packages/trazo python scripts/check-no-git.py
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from trazo import new


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def get(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def main() -> int:
    assert os.environ.get("TRAZO_DEV"), "set TRAZO_DEV to this checkout's packages/trazo"
    with tempfile.TemporaryDirectory(prefix="trazo-no-git-") as folder:
        project = Path(folder) / "paper"
        new.paper_create(project, kind="article", example=True, title="A paper without git")
        probe = subprocess.run(["git", "-C", str(project), "rev-parse"], capture_output=True)
        assert probe.returncode != 0, f"{project} is inside a git repository"
        env = {**os.environ, "GIT_CEILING_DIRECTORIES": folder}

        def run(*command: str) -> None:
            result = subprocess.run(command, cwd=project, env=env, capture_output=True, text=True)
            assert result.returncode == 0, f"{' '.join(command)}:\n{result.stdout}\n{result.stderr}"

        run("uv", "sync", "--quiet")
        run("scripts/trazo", "build")
        assert (project / "output/manuscript.docx").stat().st_size > 0
        print(f"trazo build without git: {project / 'output/manuscript.docx'}")

        port = free_port()
        server = subprocess.Popen(["scripts/trazo", "preview", "serve", "--port", str(port)],
                                  cwd=project, env=env, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL)
        try:
            base = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + 120
            while True:
                try:
                    status, _ = get(base + "/")
                    if status == 200:
                        break
                except OSError:
                    pass
                assert server.poll() is None, "the preview exited"
                assert time.monotonic() < deadline, "the preview did not answer"
                time.sleep(0.5)
            status, body = get(base + "/__diff?file=manuscript/main/01_introduction.md")
            error = json.loads(body).get("error", "")
            assert status >= 400 and error.startswith("History is off"), (status, body)
            print(f"trazo preview without git: {base} answers; Diff tab: {error}")
        finally:
            server.terminate()
            server.wait(timeout=30)
    return 0


if __name__ == "__main__":
    sys.exit(main())
