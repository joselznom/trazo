"""Measure idle preview request rate and server/main-renderer CPU without extra packages."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import subprocess
import time
from pathlib import Path

import common


def cpu_seconds(pid: int) -> float:
    value = subprocess.check_output(["ps", "-p", str(pid), "-o", "time="], text=True, timeout=5).strip()
    days, value = value.split("-") if "-" in value else ("0", value)
    seconds = 0.0
    for component in value.split(":"):
        seconds = seconds * 60 + float(component)
    return seconds + int(days) * 86400


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paper", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--checkout", type=Path, default=common.REPO)
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--seconds", type=float, default=2)
    args = parser.parse_args()
    if args.samples < 20 or not math.isfinite(args.seconds) or args.seconds < 2:
        parser.error("At least 20 idle windows of at least two seconds are required")
    common.SOURCE = args.checkout.resolve() / "packages/trazo/src"
    # Select the measured product before the existing Chrome transport imports it.
    from browser_probe import Browser
    from run import READY

    samples = []
    client = common.MCP(args.paper.resolve())
    try:
        info = client.start()
        started = time.time()
        deadline = time.perf_counter() + 30
        while common.request(client.port, "/__status")[1].get("synced", 0) <= started:
            if time.perf_counter() >= deadline:
                raise TimeoutError("Initial watcher work did not settle")
            time.sleep(.05)
        with Browser() as browser:
            browser.navigate(f"http://127.0.0.1:{client.port}/manuscript.html", READY)
            browser.wait("performance.getEntriesByType('resource').some(e => e.name.endsWith('/__workspace'))")
            browser.evaluate("new Promise(r => setTimeout(r, 2000))")
            machine = common.metadata()
            machine["commit"] = subprocess.check_output(
                ["git", "-C", str(args.checkout), "rev-parse", "HEAD"], text=True, timeout=5).strip()
            machine["chrome"] = browser.version
            for index in range(args.samples):
                before_cpu = cpu_seconds(info["pid"])
                before_task = browser.metrics()["TaskDuration"]
                browser.evaluate("performance.clearResourceTimings()")
                start = time.perf_counter()
                browser.evaluate(f"new Promise(r => setTimeout(r, {args.seconds * 1000}))")
                wall = time.perf_counter() - start
                task = browser.metrics()["TaskDuration"] - before_task
                cpu = cpu_seconds(info["pid"]) - before_cpu
                urls = browser.evaluate("performance.getEntriesByType('resource').map(e => new URL(e.name).pathname)")
                samples.append({"index": index, "wall_seconds": wall,
                                "server_cpu_percent_one_core": 100 * cpu / wall,
                                "renderer_task_percent_one_core": 100 * task / wall,
                                "status_requests_per_second": urls.count("/__status") / wall,
                                "workspace_requests_per_second": urls.count("/__workspace") / wall})
                print(json.dumps(samples[-1]), flush=True)
    finally:
        client.close()
    summary = {}
    for key in samples[0]:
        if key != "index":
            values = sorted(row[key] for row in samples)
            summary[key] = {"p50": statistics.median(values), "p95": values[math.ceil(.95 * len(values)) - 1]}
    common.dump(args.output, {"machine": machine, "samples": samples, "summary": summary,
                              "scope": "20 warm idle windows; ps CPU excludes descendants; Chrome TaskDuration covers the page renderer main thread, not total browser or system energy."})


if __name__ == "__main__":
    main()
