"""Measure seven end-to-end user operations, retaining every observation."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import statistics
import time
from pathlib import Path

from browser_probe import Browser
from common import MCP, dump, metadata, request

OPS = ("preview_start", "save_visible", "table_open", "diagram_open", "word_full", "word_unchanged", "mcp_check")
READY = "document.querySelector('[data-label]') && document.querySelector('.pv-bs-label')?.textContent.length > 0"


def cold_outputs(root: Path) -> None:
    if not (root / "benchmark-fixture.json").is_file():
        raise ValueError("Only generated benchmark papers may have their build cache removed")
    shutil.rmtree(root / ".build", ignore_errors=True)


class Session:
    def __init__(self, root: Path, operation: str, profile: Path | None) -> None:
        self.root, self.operation = root, operation
        self.browser = Browser() if operation in OPS[:4] else None
        try:
            self.client = MCP(root, profile)
        except BaseException:
            if self.browser:
                self.browser.close()
            raise
        self.source = "manuscript/main/01_introduction.md"
        self.original = (root / self.source).read_text(encoding="utf-8")
        if self.browser and operation != "preview_start":
            try:
                self.client.start()
                self.browser.navigate(self.url(), READY)
            except BaseException:
                self.close()
                raise

    def url(self, path: str = "/manuscript.html") -> str:
        return f"http://127.0.0.1:{self.client.port}{path}"

    def run(self, index: int) -> dict:
        start = time.perf_counter()
        detail = {}
        if self.operation == "preview_start":
            detail["preview"] = self.client.start()
            detail["mcp_seconds"] = time.perf_counter() - start
            self.browser.navigate(self.url(), READY)
        elif self.operation == "save_visible":
            status, source = request(self.client.port, f"/__source?file={self.source}")
            if status != 200:
                raise RuntimeError(source)
            marker = f"Benchmark saved revision {index} at {time.perf_counter_ns()}"
            payload = {"file": self.source, "base_sha256": source["sha256"],
                       "text": self.original + f"\n\n{marker}.\n"}
            # Fetch goes through the browser and the real save endpoint. No direct product calls.
            start = time.perf_counter()
            response = self.browser.evaluate(f"fetch('/__source', {{method:'POST',headers:{{'Content-Type':'application/json'}},"
                                             f"body:JSON.stringify({json.dumps(payload)})}}).then(async r => ({{status:r.status,body:await r.text()}}))")
            detail["save_ack_seconds"] = time.perf_counter() - start
            if response["status"] != 200:
                raise RuntimeError(response)
            self.browser.wait(f"document.body?.innerText.includes({json.dumps(marker)})")
            detail["status"] = request(self.client.port, "/__status")[1]
        elif self.operation == "table_open":
            self.browser.navigate(self.url("/tables#summary"), "document.querySelector('#te-page tbody td')")
        elif self.operation == "diagram_open":
            self.browser.navigate(self.url("/diagrams#fig2_workflow"), "document.querySelector('#paper svg [data-id]')")
        elif self.operation in ("word_full", "word_unchanged"):
            result = self.client.tool("build", force=self.operation == "word_full")
            detail["log"] = result["log"]
        else:
            result = self.client.tool("check")
            detail["reported_errors"] = result.get("errors")
        elapsed = time.perf_counter() - start
        if self.browser:
            detail["browser_metrics"] = self.browser.metrics()
        return {"seconds": elapsed, "start_perf_counter": start, "end_perf_counter": start + elapsed, **detail}

    def close(self) -> None:
        if self.browser:
            self.browser.close()
        self.client.close()
        if self.operation == "save_visible":
            (self.root / self.source).write_text(self.original, encoding="utf-8")


def summarize(samples: list[dict]) -> list[dict]:
    result = []
    for operation in OPS:
        for mode in ("cold", "warm"):
            values = sorted(s["seconds"] for s in samples if s["operation"] == operation and s["mode"] == mode)
            if values:
                result.append({"operation": operation, "mode": mode, "n": len(values),
                               "p50": statistics.median(values), "p95": values[math.ceil(.95 * len(values)) - 1],
                               "min": values[0], "max": values[-1], "stdev": statistics.stdev(values) if len(values) > 1 else 0})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paper", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--operation", choices=OPS, action="append")
    parser.add_argument("--smoke", action="store_true", help="Diagnostic only, never a baseline")
    parser.add_argument("--profile", action="store_true", help="Separate instrumented run, not comparable with baseline")
    args = parser.parse_args()
    if args.samples < 20 and not args.smoke:
        parser.error("At least 20 samples are required; --smoke labels a diagnostic run")
    root, out = args.paper.resolve(), args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    report = {"machine": metadata(), "fixture": json.loads((root / "benchmark-fixture.json").read_text()),
              "diagnostic_only": args.smoke or args.profile, "samples": [], "summary": []}
    dump(out / "results.json", report)
    for op in args.operation or OPS:
        for mode in ("cold", "warm"):
            session = None
            try:
                for index in range(args.samples):
                    if session is None:
                        if mode == "cold" and op in ("word_full", "preview_start"):
                            cold_outputs(root)
                        profile = out / "profiles" / f"{op}-{mode}-{index}" if args.profile else None
                        session = Session(root, op, profile)
                        if mode == "warm" or op == "word_unchanged":
                            if op == "word_unchanged":
                                session.client.tool("build")
                            else:
                                session.run(-1)
                    trace = out / "traces" / f"{op}-{mode}-{index}.json"
                    if args.profile and session.browser:
                        session.browser.trace_start()
                    sample = {"operation": op, "mode": mode, "index": index, **session.run(index)}
                    if args.profile and session.browser:
                        sample["trace"] = session.browser.trace_end(trace)
                    report["samples"].append(sample)
                    report["summary"] = summarize(report["samples"])
                    if session.browser:
                        report["machine"]["chrome"] = session.browser.version
                    dump(out / "results.json", report)
                    print(f"{root.name} {op} {mode} {index + 1}/{args.samples}: {sample['seconds']:.3f}s", flush=True)
                    if mode == "cold":
                        session.close()
                        session = None
                print(json.dumps(report["summary"][-1]), flush=True)
            finally:
                if session:
                    session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
