"""Time the stress paper: create, build, preview and checks (stress_fixture.py bench).

Each step runs this checkout's trazo in a subprocess with TRAZO_ROOT
set to the paper, as scripts/trazo does, and is timed by the wall clock.
The preview steps start `trazo preview serve` on a free port and time:
the port answering, every page built (/__status), the review list
(/__review), and finding the source of the longest commented paragraph
(POST /__locate, what a click on a paragraph does) from a file that does
not hold it, so the search goes through every chapter. Results go to stdout
and DIR/stress-timings.json.
"""

import http.client
import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

TIMINGS = "stress-timings.json"


def environment(root: Path) -> dict[str, str]:
    return {**os.environ, "TRAZO_ROOT": str(root)}


def trazo(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "trazo", *args], cwd=root, env=environment(root),
                          capture_output=True, text=True)


def request(port: int, method: str, path: str, body: dict | None = None) -> tuple[int, bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=600)
    try:
        payload = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else {}
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def wait(condition: Callable[[], bool], timeout: float = 1800) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if condition():
                return
        except OSError:
            pass
        time.sleep(0.05)
    raise TimeoutError("the preview did not get ready in time")


def longest_paragraph(root: Path, expected: dict) -> tuple[str, str]:
    """The text and file of the longest commented paragraph of the manuscript."""
    files = {q["id"]: q["file"] for q in expected["comments"]["quotes"] if q["doc"] == "manuscript"}
    comments = json.loads((root / "notes/review/comments.json").read_text(encoding="utf-8"))
    comment = max((c for c in comments if c["id"] in files), key=lambda c: len(c["anchor"]["text"]))
    return comment["anchor"]["text"], files[comment["id"]]


def preview(root: Path, timings: dict[str, float], expected: dict) -> None:
    port = free_port()
    start = time.monotonic()
    server = subprocess.Popen([sys.executable, "-m", "trazo", "preview", "serve", "--port", str(port)],
                              cwd=root, env=environment(root), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait(lambda: request(port, "GET", "/__status")[0] == 200)
        timings["preview: port answers"] = time.monotonic() - start

        def built() -> bool:
            status = json.loads(request(port, "GET", "/__status")[1])
            return all(state["version"] > 0 for name, state in status.items()
                       if isinstance(state, dict) and "version" in state)
        wait(built)
        timings["preview: every page built"] = time.monotonic() - start
        text, file = longest_paragraph(root, expected)
        for label, method, path, body in [
                ("preview: manuscript page", "GET", "/manuscript.html", None),
                ("preview: review list", "GET", "/__review", None),
                # The key points match no chapter paragraph, so /__locate also
                # searches every sibling file (a chapter would match too well).
                ("preview: locate a paragraph", "POST", "/__locate",
                 {"file": "manuscript/main/00b_key_points.md", "text": text})]:
            began = time.monotonic()
            status, answer = request(port, method, path, body)
            timings[label] = time.monotonic() - began
            if status != 200:
                raise RuntimeError(f"{method} {path}: HTTP {status}")
        found = json.loads(answer)["file"]
        if found != file:
            raise RuntimeError(f"/__locate found the paragraph in {found}, not {file}")
    finally:
        server.terminate()
        server.wait(timeout=30)


def bench(root: Path, create: Callable[[], dict], as_json: bool) -> int:
    """Create the paper and time each step; a failed check is reported, not fatal."""
    root = Path(root).resolve()
    timings: dict[str, float] = {}
    failed: list[str] = []

    def timed(label: str, *args: str, check: bool = False) -> None:
        began = time.monotonic()
        result = trazo(root, *args)
        timings[label] = time.monotonic() - began
        if result.returncode and check:
            failed.append(label)
        elif result.returncode:
            raise SystemExit(f"trazo {' '.join(args)} failed:\n{result.stdout}{result.stderr}")

    began = time.monotonic()
    expected = create()
    timings["create the paper"] = time.monotonic() - began
    timed("build (cold)", "build", "--submission")
    timed("build (nothing changed)", "build", "--submission")
    chapter = root / "manuscript/main/01_chapter.md"
    chapter.write_text(chapter.read_text(encoding="utf-8") + "\nOne more sentence.\n", encoding="utf-8")
    timed("build (one chapter edited)", "build", "--submission")
    timed("preview build", "preview", "build")
    for label, args in [("counts", ["counts"]), ("abbreviations check", ["abbreviations", "check"]),
                        ("citations", ["citations"]), ("submission check", ["submission", "check", "--strict"])]:
        timed(label, *args, check=True)
    preview(root, timings, expected)
    report = {"folder": str(root), "scale": expected["scale"], "failed": failed,
              "seconds": {k: round(v, 3) for k, v in timings.items()}}
    (root / TIMINGS).write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    if as_json:
        print(json.dumps(report, indent=1))
        return 0
    width = max(map(len, timings))
    for label, seconds in timings.items():
        print(f"{label:<{width}}  {seconds:8.2f} s" + ("  (failed)" if label in failed else ""))
    print(f"(also in {root / TIMINGS})")
    return 0
