"""Enumerate reached writes, kill at each boundary, and assert disk/recovery invariants."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from common import SCRIPTS, dump, environment

USER_FILES = {"source": "manuscript/main/01_introduction.md", "table": "tables/specs/summary.json",
              "diagram": "figures/diagrams/fig2_workflow.json"}


def clone(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns(".venv", ".git", "__pycache__"))
    if (source / ".venv").is_dir():
        (destination / ".venv").symlink_to(source / ".venv", target_is_directory=True)


def launch(root: Path, operation: str, output: Path, fault: str = "none", site: str = "",
           timeout: float = 20) -> dict:
    argv = [sys.executable, str(SCRIPTS / "fault_worker.py"), str(root), operation, str(output),
            "--fault", fault, "--site", site]
    output.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    with subprocess.Popen(argv, env=environment(root), cwd=root, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, start_new_session=True) as proc:
        timed_out = False
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(proc.pid, signal.SIGKILL)
            out, err = proc.communicate(timeout=5)
    output.with_suffix(".log").write_text(out + err, encoding="utf-8")
    return {"returncode": proc.returncode, "supervisor_timeout": timed_out,
            "injection_hit": output.with_suffix(".sites.hit").exists(),
            "seconds": time.perf_counter() - started,
            "result": json.loads(output.read_text()) if output.exists() else None,
            "log": str(output.with_suffix(".log"))}


def files(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes()
            for folder in ("manuscript", "tables", "figures/diagrams", "notes", "output", ".build")
            for path in (root / folder).rglob("*")
            if path.is_file() and not path.name.endswith((".tmp", ".lock")) and "__pycache__" not in str(path)}


def violations(root: Path, operation: str, before: dict[str, bytes], after: dict[str, bytes]) -> list[str]:
    problems = []
    actual = files(root)
    user_file = USER_FILES.get(operation)
    if user_file and actual.get(user_file) != before.get(user_file):
        history = []
        for path in (root / "notes/review").glob("*history.json"):
            try:
                history.extend(json.loads(path.read_text()))
            except (ValueError, TypeError):
                pass
        old_text = before[user_file].decode("utf-8")
        if not any(e.get("before") == old_text or e.get("before_text") == old_text
                   for e in history if isinstance(e, dict)):
            problems.append("previous user text is absent from both the file and undo history")
    for name, data in actual.items():
        if data in (before.get(name), after.get(name)):
            continue
        if name.endswith(".json"):
            try:
                json.loads(data)
            except ValueError:
                problems.append(f"invalid published JSON: {name}")
        elif name.endswith(".docx"):
            try:
                with zipfile.ZipFile(root / name) as archive:
                    if archive.testzip() or "word/document.xml" not in archive.namelist():
                        problems.append(f"invalid published DOCX: {name}")
            except zipfile.BadZipFile:
                problems.append(f"truncated published DOCX: {name}")
        elif name.endswith((".md", ".html")):
            problems.append(f"published text is neither the complete old nor new version: {name}")
        elif name.endswith(".svg"):
            try:
                ET.fromstring(data)
            except ET.ParseError:
                problems.append(f"invalid published SVG: {name}")
        elif name.endswith(".png"):
            import struct
            import zlib
            offset, complete = 8, False
            while offset + 12 <= len(data) and data.startswith(b"\x89PNG\r\n\x1a\n"):
                length = struct.unpack(">I", data[offset:offset + 4])[0]
                end = offset + length + 12
                if end > len(data):
                    break
                chunk = data[offset + 4:end - 4]
                if zlib.crc32(chunk) != struct.unpack(">I", data[end - 4:end])[0]:
                    break
                offset = end
                if chunk[:4] == b"IEND":
                    complete = offset == len(data)
                    break
            if not complete:
                problems.append(f"invalid published PNG: {name}")
        elif name.endswith(".pdf") and not (data.startswith(b"%PDF-") and data.rstrip().endswith(b"%%EOF")):
            problems.append(f"invalid published PDF: {name}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paper", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--operation", action="append", choices=(*USER_FILES, "build", "preview", "export"))
    parser.add_argument("--fault", action="append", choices=("kill", "full", "readonly"))
    parser.add_argument("--site", help="Reproduce one exact site from report.json")
    args = parser.parse_args()
    seed, out = args.paper.resolve(), args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    cases = []
    for operation in args.operation or (*USER_FILES, "build", "preview", "export"):
        normal = out / f"{operation}-reference"
        clone(seed, normal)
        before = files(normal)
        probe = launch(normal, operation, out / f"{operation}-reference.json")
        if probe["returncode"] or (probe["result"] or {}).get("status", 0) not in (0, 200):
            raise RuntimeError(f"The control did not succeed: {probe}")
        after = files(normal)
        journal = out / f"{operation}-reference.sites.jsonl"
        sites = [json.loads(line) for line in journal.read_text().splitlines()]
        for index, site in enumerate(sites):
            if args.site and site["site"] != args.site:
                continue
            for fault in args.fault or ("kill", "full", "readonly"):
                stage = site["site"].split(" | ", 1)[0]
                if stage == "after_replace" and fault != "kill":
                    continue  # A successful rename cannot subsequently return ENOSPC/EACCES.
                if stage == "write" and fault == "readonly":
                    continue  # Directory permissions are checked at open/rename, not an existing fd's write.
                name = f"{operation}-{index:03d}-{fault}"
                root = out / name
                clone(seed, root)
                result = launch(root, operation, out / (name + ".json"), fault, site["site"])
                problems = violations(root, operation, before, after)
                response = result["result"] or {}
                # A kill is proved by -SIGKILL; handled failures prove the trigger with its hit counter.
                triggered = result["injection_hit"] and (result["returncode"] == -signal.SIGKILL if fault == "kill" else True)
                if not triggered:
                    problems.append("injection did not reach its target")
                if result["supervisor_timeout"]:
                    problems.append("operation exceeded supervisor deadline")
                if fault != "kill" and response.get("status") in (0, 200) and operation != "preview":
                    problems.append("injected write failure was reported as success")
                if operation == "preview" and fault != "kill" and response.get("state", {}).get("ok"):
                    problems.append("injected render failure left the preview status successful")
                recovery = launch(root, operation, out / (name + "-recovery.json"))
                recovered = recovery["returncode"] == 0 and (recovery["result"] or {}).get("status", 0) in (0, 200)
                if not recovered:
                    problems.append("next operation failed after the injected fault was removed")
                cases.append({"name": name, "operation": operation, "fault": fault, **site,
                              "triggered": triggered, "outcome": result, "recovered": recovered,
                              "violations": problems})
                dump(out / "report.json", {"cases": cases, "failures": sum(bool(c["violations"]) for c in cases)})
                print(f"{name}: {'FAIL ' + '; '.join(problems) if problems else 'PASS'}", flush=True)
                # Keep failing snapshots; successful disposable copies have no evidence beyond the logs.
                if not problems:
                    shutil.rmtree(root)
        shutil.rmtree(normal)
    lines = ["# Write fault results", "", f"{len(cases)} injections, {sum(bool(c['violations']) for c in cases)} invariant failures.", "",
             "Only dynamically reached Python write/publication boundaries are covered. External executables and unvisited sites are not covered.", "",
             "| Case | Site | Triggered | Recovered | Violations |", "| --- | --- | --- | --- | --- |"]
    lines += [f"| {c['name']} | {c['site'].replace('|', '/')} | {c['triggered']} | {c['recovered']} | {'; '.join(c['violations']) or 'none'} |" for c in cases]
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Report: {out / 'report.md'}", flush=True)
    return int(any(c["violations"] for c in cases))


if __name__ == "__main__":
    raise SystemExit(main())
