"""Run Bombadil property-based UI fuzz suites against a throwaway trazo paper.

Each suite starts from a fresh copy of the example paper (plus a hand-edited
table spec so both table editor modes are reachable), serves it via
`trazo preview serve` on a free localhost port, runs Bombadil headlessly, and
checks both Bombadil's property violations and the server log for tracebacks or
5xx errors.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
FUZZ_DIR = Path(__file__).resolve().parent
SPECS_DIR = FUZZ_DIR / "specs"
TRAZO_DIR = REPO_ROOT / "packages" / "trazo"
BOMBADIL_BIN = FUZZ_DIR / "node_modules" / ".bin" / "bombadil"

SERVER_ERROR_PATTERNS = (
    re.compile(r"Traceback \(most recent call last\):"),
    re.compile(r'" 5\d\d '),
)
BG_JOB_EXIT_RE = re.compile(r"^\[(build|export)\]\s+.*:\s+exit\s+(\d+)")


class FuzzError(RuntimeError):
    """Raised when the fuzz runner cannot start or complete a suite."""


@dataclass(frozen=True)
class Suite:
    name: str
    url_path: str
    ready_path: str
    spec_file: str


SUITES: dict[str, Suite] = {
    "preview": Suite("preview", "/manuscript.html", "/manuscript.html", "preview.spec.ts"),
    "diagram": Suite("diagram", "/diagrams#fig2_workflow", "/diagrams", "diagram.spec.ts"),
    "table": Suite("table", "/tables#summary", "/tables", "table.spec.ts"),
}

HAND_TABLE_SPEC: dict[str, Any] = {
    "schema_version": 1,
    "title": "Hand-edited fuzz table",
    "columns": [
        {"id": "c1", "header": "Metric"},
        {"id": "c2", "header": "Baseline"},
        {"id": "c3", "header": "Proposed"},
    ],
    "body": [
        {"id": "b1", "block": "Primary endpoints"},
        {"id": "r1", "cells": {"c1": "Accuracy", "c2": "0.82", "c3": "**0.91**"}},
        {"id": "r2", "cells": {"c1": "Latency (ms)", "c2": "42", "c3": "38"}},
    ],
}


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def seed_hand_table(paper_dir: Path) -> Path:
    spec_path = paper_dir / "tables" / "specs" / "hand_example.json"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(json.dumps(HAND_TABLE_SPEC, indent=2) + "\n", encoding="utf-8")
    return spec_path


def scan_server_log(log_text: str) -> tuple[list[str], list[str]]:
    """Return (fatal_lines, nonzero_bg_job_lines) from a preview server log."""
    fatal: list[str] = []
    bg_exits: list[str] = []
    for line in log_text.splitlines():
        if any(pattern.search(line) for pattern in SERVER_ERROR_PATTERNS):
            fatal.append(line)
        match = BG_JOB_EXIT_RE.match(line)
        if match and int(match.group(2)) != 0:
            bg_exits.append(line)
    return fatal, bg_exits


def run_trazo(args: list[str], cwd: Path) -> None:
    env = {**os.environ, "TRAZO_DEV": str(TRAZO_DIR)}
    cmd = [sys.executable, "-m", "trazo", *args]
    result = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise FuzzError(f"trazo {' '.join(args)} failed (exit {result.returncode}): {detail}")


def prepare_paper(paper_dir: Path) -> None:
    if paper_dir.exists():
        shutil.rmtree(paper_dir)
    run_trazo(["new", str(paper_dir)], cwd=REPO_ROOT)
    seed_hand_table(paper_dir)
    run_trazo(["table", "render", "hand_example"], cwd=paper_dir)
    run_trazo(["build"], cwd=paper_dir)
    run_trazo(["preview", "build"], cwd=paper_dir)


def wait_for_http(url: str, proc: subprocess.Popen[str], timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise FuzzError(f"preview server exited early with code {proc.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = str(exc)
        time.sleep(0.2)
    raise FuzzError(f"preview server did not become ready at {url} within {timeout:g}s ({last_error})")


def summarize_trace(trace_path: Path, cov_path: Path) -> dict[str, Any]:
    if not trace_path.is_file():
        return {"states": 0, "violations": [], "branches_hit": 0, "branches_total": 0, "urls": {}}

    syms: set[int] = set()
    if cov_path.is_file():
        with cov_path.open(encoding="utf-8") as handle:
            syms = {int(json.loads(line)["id"]) for line in handle if line.strip()}

    urls: collections.Counter[str] = collections.Counter()
    violations: list[dict[str, Any]] = []
    hit_branches: set[int] = set()
    states = 0
    with subprocess.Popen(
        [str(BOMBADIL_BIN), "trace", "--json", str(trace_path)],
        cwd=FUZZ_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    ) as proc:
        assert proc.stdout is not None
        for line in proc.stdout:
            record = json.loads(line)
            states += 1
            meta = record["metadata"]
            state = meta["state"]
            urls[str(state["url"])] += 1
            for branch_id in state.get("branches", []):
                hit_branches.add(int(branch_id))
            for violation in meta.get("violations", []):
                violations.append({"state": record["index"], "url": state["url"], "detail": violation})
        proc.wait()

    return {
        "states": states,
        "violations": violations,
        "branches_hit": len(hit_branches),
        "branches_total": len(syms),
        "urls": dict(urls.most_common(10)),
    }


def stop_server(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def run_suite(
    suite: Suite,
    out_dir: Path,
    time_limit: str,
    keep_traces: bool,
    exit_on_violation: bool,
    no_sandbox: bool,
) -> dict[str, Any]:
    suite_dir = out_dir / suite.name
    suite_dir.mkdir(parents=True, exist_ok=True)
    paper_dir = suite_dir / "paper"
    server_log = suite_dir / "server.log"
    bombadil_log = suite_dir / "bombadil.log"
    trace_path = suite_dir / "trace.bin"
    cov_path = suite_dir / "coverage.jsonl"
    spec_path = SPECS_DIR / suite.spec_file

    started = time.monotonic()
    prepare_paper(paper_dir)

    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    start_url = f"{base_url}{suite.url_path}"
    ready_url = f"{base_url}{suite.ready_path}"

    env = {**os.environ, "TRAZO_DEV": str(TRAZO_DIR)}
    with server_log.open("w", encoding="utf-8") as log_handle:
        server = subprocess.Popen(
            [sys.executable, "-m", "trazo", "preview", "serve", "--port", str(port)],
            cwd=paper_dir,
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            wait_for_http(ready_url, server)
            cmd = [
                str(BOMBADIL_BIN),
                "browser",
                "test",
                "--headless",
                "--time-limit",
                time_limit,
                "--output-path",
                str(trace_path),
                "--output-path-overwrite",
                "--coverage-symbols",
                str(cov_path),
            ]
            if exit_on_violation:
                cmd.append("--exit-on-violation")
            if no_sandbox:
                cmd.append("--no-sandbox")
            cmd.extend([start_url, str(spec_path)])

            with bombadil_log.open("w", encoding="utf-8") as b_log:
                result = subprocess.run(cmd, cwd=FUZZ_DIR, stdout=b_log, stderr=subprocess.STDOUT, text=True)
            server_died = server.poll() is not None
        finally:
            stop_server(server)

    summary = summarize_trace(trace_path, cov_path)
    fatal_server_lines, bg_job_exits = scan_server_log(server_log.read_text(encoding="utf-8"))
    passed = (
        result.returncode == 0
        and not summary["violations"]
        and not fatal_server_lines
        and not server_died
    )

    if passed and not keep_traces:
        trace_path.unlink(missing_ok=True)
    trace_kept: str | None = str(trace_path) if trace_path.is_file() else None

    return {
        "suite": suite.name,
        "passed": passed,
        "exit_code": result.returncode,
        "duration_seconds": round(time.monotonic() - started, 1),
        "states": summary["states"],
        "branches_hit": summary["branches_hit"],
        "branches_total": summary["branches_total"],
        "violations": summary["violations"],
        "server_died": server_died,
        "server_fatal_lines": fatal_server_lines,
        "background_job_nonzero_exits": bg_job_exits,
        "urls": summary["urls"],
        "trace": trace_kept,
        "server_log": str(server_log),
        "bombadil_log": str(bombadil_log),
        "spec": str(spec_path),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Bombadil UI fuzz suites against trazo.")
    parser.add_argument(
        "--suite",
        action="append",
        choices=tuple(SUITES),
        help="Suite to run (repeatable; defaults to all suites).",
    )
    parser.add_argument("--time", default="45s", help="Duration per suite (default: 45s).")
    parser.add_argument("--keep-traces", action="store_true", help="Keep trace.bin even when a suite passes.")
    parser.add_argument("--exit-on-violation", action="store_true", help="Stop a suite on the first violation.")
    parser.add_argument("--no-sandbox", action="store_true", help="Pass --no-sandbox to Bombadil's Chrome.")
    parser.add_argument("--output-dir", type=Path, help="Output folder (default: .build/fuzz/<UTC timestamp>).")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not BOMBADIL_BIN.is_file():
        print(
            f"fuzz: Bombadil is not installed at {BOMBADIL_BIN}. Run: cd scripts/fuzz && npm ci",
            file=sys.stderr,
        )
        return 2

    selected = [SUITES[name] for name in (args.suite or list(SUITES))]
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = (args.output_dir or (REPO_ROOT / ".build" / "fuzz" / stamp)).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    for suite in selected:
        print(f"[fuzz] running {suite.name} ({args.time})...", flush=True)
        record = run_suite(
            suite=suite,
            out_dir=out_dir,
            time_limit=args.time,
            keep_traces=args.keep_traces,
            exit_on_violation=args.exit_on_violation,
            no_sandbox=args.no_sandbox,
        )
        results.append(record)
        cov_pct = 100.0 * record["branches_hit"] / max(1, record["branches_total"])
        status = "PASS" if record["passed"] else "FAIL"
        bg_note = f", bg_nonzero={len(record['background_job_nonzero_exits'])}" if record["background_job_nonzero_exits"] else ""
        print(
            f"[fuzz] {suite.name}: {status} "
            f"(exit={record['exit_code']}, states={record['states']}, "
            f"branches={record['branches_hit']}/{record['branches_total']} {cov_pct:.1f}%, "
            f"violations={len(record['violations'])}, server_errors={len(record['server_fatal_lines'])}{bg_note}, "
            f"{record['duration_seconds']}s)",
            flush=True,
        )
        if not record["passed"]:
            if record["trace"]:
                print(
                    f"  inspect: cd scripts/fuzz && npx bombadil browser inspect '{record['trace']}'",
                    flush=True,
                )
            print(f"  logs:    {record['bombadil_log']} | {record['server_log']}", flush=True)

    report_path = out_dir / "report.json"
    report_path.write_text(json.dumps({"results": results}, indent=2) + "\n", encoding="utf-8")
    print(f"[fuzz] report: {report_path}")
    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
