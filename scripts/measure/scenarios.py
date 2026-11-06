"""Environmental, concurrent edit and deterministic malformed-input experiments."""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import signal
import socket
import subprocess
import sys
import time
import zipfile
from collections.abc import Callable
from pathlib import Path

from common import MCP, SCRIPTS, command, dump, environment, request
from faults import clone, launch

NAMES = ("pandoc_missing", "pandoc_hang", "pandoc_invalid", "port_busy", "edit_during_build",
         "readonly_directory", "corrupt_history", "corrupt_table", "concurrent_writers", "fuzz")
SOURCE_FILE = "manuscript/main/01_introduction.md"


def wait(check: Callable[[], bool], timeout: float = 15) -> None:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if check():
            return
        time.sleep(.02)
    raise TimeoutError("Scenario barrier not reached")


def configure(root: Path, mode: str) -> str:
    config = root / "trazo.toml"
    old = config.read_text()
    argv = ([str(root / "missing-pandoc")] if mode == "missing" else
            [sys.executable, str(SCRIPTS / "pandoc_fault.py"), shutil.which("pandoc"), mode, str(root)])
    config.write_text(old + "\n[commands]\npandoc = " + json.dumps(argv, ensure_ascii=False) + "\n")
    return old


def pandoc_case(root: Path, name: str) -> dict:
    mode = name.removeprefix("pandoc_")
    before = (root / "output/manuscript.docx").read_bytes()
    old = configure(root, mode)
    client = MCP(root)
    failure, response = None, None
    try:
        try:
            response = client.rpc("tools/call", {"name": "build", "arguments": {"project": str(root), "force": True}},
                                  timeout=3 if mode == "hang" else 30)
        except TimeoutError as exc:
            failure = str(exc)
        current = (root / "output/manuscript.docx").read_bytes()
        intact = current == before
        valid = zipfile.is_zipfile(root / "output/manuscript.docx")
        alive = client.rpc("ping", {}) == {}
        (root / "trazo.toml").write_text(old)
        recovery = client.tool("build")
        problems = []
        if failure:
            problems.append("no product timeout within 3s; only the supervisor cancelled the tool")
        if response and not response.get("isError"):
            problems.append("invalid dependency output was reported as success")
        if not intact and not valid:
            problems.append("last good Word file was replaced with invalid bytes")
        return {"violations": problems, "response": response, "supervisor": failure,
                "old_docx_preserved": intact, "valid_docx": valid, "mcp_alive": alive,
                "recovered": bool(recovery), "deadline_seconds": 3 if mode == "hang" else 30}
    finally:
        (root / "trazo.toml").write_text(old)
        client.close()


def edit_during_build(root: Path) -> dict:
    old = configure(root, "race")
    proc = subprocess.Popen([sys.executable, "-m", "trazo", "build", "--force"], env=environment(root),
                            cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        wait(lambda: (root / "race.ready").exists() or proc.poll() is not None)
        if proc.poll() is not None:
            raise RuntimeError("Pandoc barrier was not reached: " + str(proc.communicate()))
        source = root / SOURCE_FILE
        marker = "ConcurrentAgentRevision survives the next build"
        source.write_text(source.read_text() + f"\n\n{marker}.\n")
        (root / "race.release").touch()
        stdout, stderr = proc.communicate(timeout=30)
        first_code = proc.returncode
        second = command(root, "build")
        with zipfile.ZipFile(root / "output/manuscript.docx") as archive:
            updated = "ConcurrentAgentRevision" in archive.read("word/document.xml").decode()
        return {"violations": [] if updated and first_code == 0 else ["concurrent edit did not reach the next Word build"],
                "first_exit": first_code, "first_log": stdout + stderr, "second_log": second.stdout,
                "next_build_contains_agent_text": updated,
                "limitation": "The first build may represent the snapshot before the edit; only a subsequent build is verified here."}
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
        (root / "trazo.toml").write_text(old)


def concurrent_writers(root: Path) -> dict:
    reference = root.parent / "race-reference"
    clone(root, reference)
    result = launch(reference, "source", root.parent / "race-reference.json")
    if result["returncode"] or result["result"]["status"] != 200:
        raise RuntimeError(result)
    sites = [json.loads(line) for line in (root.parent / "race-reference.sites.jsonl").read_text().splitlines()]
    site = next(s["site"] for s in sites if s["path"] == SOURCE_FILE and s["site"].startswith("before_replace"))
    output = root.parent / "race.json"
    argv = [sys.executable, str(SCRIPTS / "fault_worker.py"), str(root), "source", str(output), "--fault", "race", "--site", site]
    with subprocess.Popen(argv, env=environment(root), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, start_new_session=True) as proc:
        try:
            wait(lambda: output.with_suffix(".sites.ready").exists() or proc.poll() is not None)
            if proc.poll() is not None:
                raise RuntimeError(proc.communicate())
            source = root / SOURCE_FILE
            marker = "Independently saved agent text must survive"
            source.write_text(source.read_text() + f"\n\n{marker}.\n")
            output.with_suffix(".sites.release").touch()
            stdout, stderr = proc.communicate(timeout=15)
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)
    retained = marker in source.read_text() or any(marker in p.read_text() for p in (root / "notes/review").glob("*history.json"))
    response = json.loads(output.read_text())
    return {"violations": [] if retained else ["HTTP save acknowledged success after overwriting the independent writer; its text is absent from history"],
            "retained": retained, "response": response, "site": site, "log": stdout + stderr}


def browser_cases(root: Path, name: str) -> dict:
    blocker = None
    if name == "port_busy":
        blocker = socket.socket()
        try:
            blocker.bind(("127.0.0.1", 8765))
            blocker.listen()
        except OSError:
            blocker.close()
            blocker = None  # An existing listener is sufficient; never stop it.
    client = MCP(root)
    try:
        info = client.start()
        port = int(info["port"])
        wait(lambda: request(port, "/__status")[1].get("manuscript", {}).get("version", 0) > 0)
        source = root / SOURCE_FILE
        original = source.read_bytes()
        state = request(port, f"/__source?file={SOURCE_FILE}")[1]
        payload = {"file": SOURCE_FILE, "base_sha256": state["sha256"], "text": state["text"] + "\nBrowser revision.\n"}
        if name == "port_busy":
            return {"violations": [] if port != 8765 else ["occupied port was not avoided"],
                    "port": port, "recovered": True}
        if name == "readonly_directory":
            folder = source.parent
            permissions = folder.stat().st_mode & 0o777
            folder.chmod(0o555)
            try:
                status, body = request(port, "/__source", payload)
                preserved = source.read_bytes() == original
            finally:
                folder.chmod(permissions)
            recovered = request(port, "/__source", payload)[0] == 200
            return {"violations": [] if status >= 400 and preserved and recovered else ["read-only save contract failed"],
                    "status": status, "body": body, "preserved": preserved, "recovered": recovered}
        if name == "corrupt_history":
            history = root / "notes/review/file_history.json"
            history.parent.mkdir(parents=True, exist_ok=True)
            history.write_text('[{"before_text":')
            status, body = request(port, "/__source", payload)
            retry_status, retry = request(port, "/__source", payload)
            return {"violations": ["valid saves remain blocked until the history is manually repaired"] if retry_status >= 400 else [],
                    "status": status, "body": body, "retry_status": retry_status,
                    "source_preserved": source.read_bytes() == original, "recovered": retry_status == 200}
        if name == "corrupt_table":
            target = root / "tables/output/data/summary.json"
            previous = target.read_bytes()
            current = request(port, "/__status")[1]["manuscript"]["version"]
            changed_at = time.time()
            target.write_text("{corrupt")
            def processed() -> bool:
                status = request(port, "/__status")[1]
                return (status["manuscript"]["version"] > current or not status["manuscript"]["ok"]
                        or status.get("synced", 0) > changed_at)
            wait(processed, timeout=20)
            shown = request(port, "/__status")[1]
            table = request(port, "/__table?label=summary")
            restored_at = time.time()
            target.write_bytes(previous)
            wait(lambda: request(port, "/__status")[1].get("synced", 0) > restored_at, timeout=20)
            problems = ["preview reports ok while table data are corrupt and the last generated table is still shown"] if shown["manuscript"]["ok"] else []
            return {"violations": problems, "preview_status": shown, "table_response": table,
                    "recovered": request(port, "/__status")[1]["manuscript"]["ok"]}
        rng = random.Random(20261106)
        atoms = [None, True, False, 0, -1, "á / 東京", [], {}, "\ud800"]
        responses, failures = [], []
        table_before = (root / "tables/specs/summary.json").read_bytes()
        diagram_before = (root / "figures/diagrams/fig2_workflow.json").read_bytes()
        table_state = request(port, "/__table?label=summary")[1]
        diagram_state = request(port, "/__diagrams/file/fig2_workflow")[1]
        for i in range(100):
            endpoint = rng.choice(["/__source", "/__table", "/__diagrams/file/fig2_workflow"])
            value = {rng.choice(["text", "file", "base_sha256", "ops", "diagram", "label"]): rng.choice(atoms)
                     for _ in range(rng.randrange(1, 5))}
            if i % 2 == 0:
                if endpoint == "/__source":
                    value = {"file": SOURCE_FILE, "base_sha256": state["sha256"],
                             "text": rng.choice([None, [], {}, 12, True, "\ud800"])}
                elif endpoint == "/__table":
                    value = {"label": "summary", "base_sha256": table_state["sha256"],
                             "ops": [{"op": "set", "path": "schema_version", "value": rng.choice([-1, None, "broken"])}]}
                else:
                    diagram = json.loads(diagram_before)
                    diagram["nodes"] = rng.choice([None, True, 12, "broken"])
                    value = {"base_sha256": diagram_state["sha256"], "diagram": diagram}
            status, body = request(port, endpoint, value)
            responses.append({"index": i, "endpoint": endpoint, "status": status, "body": body, "input": value})
            if status < 400 or request(port, "/__status")[0] != 200:
                failures.append(f"malformed request {i} was accepted or killed the server")
        if source.read_bytes() != original:
            failures.append("malformed requests changed user text")
        if (root / "tables/specs/summary.json").read_bytes() != table_before:
            failures.append("malformed requests changed the table spec")
        if (root / "figures/diagrams/fig2_workflow.json").read_bytes() != diagram_before:
            failures.append("malformed requests changed the diagram spec")
        return {"violations": failures, "seed": 20261106, "cases": responses, "source_preserved": source.read_bytes() == original}
    finally:
        client.close()
        if blocker:
            blocker.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paper", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--scenario", choices=NAMES, action="append")
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    results = []
    for name in args.scenario or NAMES:
        root = out / name
        clone(args.paper.resolve(), root)
        if name.startswith("pandoc_"):
            result = pandoc_case(root, name)
        elif name == "edit_during_build":
            result = edit_during_build(root)
        elif name == "concurrent_writers":
            result = concurrent_writers(root)
        else:
            result = browser_cases(root, name)
        results.append({"name": name, **result})
        dump(out / "report.json", results)
        print(f"{name}: {'FAIL ' + '; '.join(result['violations']) if result['violations'] else 'PASS'}", flush=True)
    return int(any(r["violations"] for r in results))


if __name__ == "__main__":
    raise SystemExit(main())
