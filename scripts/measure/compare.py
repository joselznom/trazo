"""Compare independent same-machine runs without mistaking small changes for wins."""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from pathlib import Path

from common import dump


def quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return statistics.median(ordered) if q == .5 else ordered[math.ceil(q * len(ordered)) - 1]


def compare(before: dict, after: dict) -> list[dict]:
    if before["diagnostic_only"] or after["diagnostic_only"]:
        raise ValueError("Profile/smoke runs cannot prove a performance improvement")
    for key in ("platform", "machine", "cpu", "memory_bytes", "python", "pandoc", "uv", "chrome"):
        if before["machine"].get(key) != after["machine"].get(key):
            raise ValueError(f"Machine/toolchain changed: {key}")
    if before["fixture"]["inputs"] != after["fixture"]["inputs"]:
        raise ValueError("Fixture inputs differ")
    rng, result = random.Random(20261106), []
    groups = {(s["operation"], s["mode"]) for s in before["samples"]}
    for operation, mode in sorted(groups):
        a, b = [[s["seconds"] for s in run["samples"] if s["operation"] == operation and s["mode"] == mode]
                for run in (before, after)]
        if min(len(a), len(b)) < 20:
            raise ValueError(f"Too few samples: {operation}/{mode}")
        row = {"operation": operation, "mode": mode}
        for q, label in ((.5, "p50"), (.95, "p95")):
            delta = sorted(quantile(rng.choices(a, k=len(a)), q) - quantile(rng.choices(b, k=len(b)), q)
                           for _ in range(2000))
            old, new = quantile(a, q), quantile(b, q)
            interval = [delta[49], delta[1949]]
            row[label] = {"before": old, "after": new, "improvement_fraction": (old - new) / old,
                          "bootstrap_delta_95_interval": interval,
                          "improvement_exceeds_noise_and_5_percent": interval[0] > 0 and new <= .95 * old}
        result.append(row)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    results = compare(json.loads(args.before.read_text()), json.loads(args.after.read_text()))
    dump(args.output, results)
    print(json.dumps(results, indent=2))
