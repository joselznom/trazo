"""Summarize diagnostic spans without adding parent/child or nested browser time."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import dump


def summarize(folder: Path) -> list[dict]:
    run = json.loads((folder / "results.json").read_text())
    result = []
    for sample in run["samples"]:
        totals, counts, functions, spans = {}, {}, {}, []
        for path in (folder / "profiles" / f"{sample['operation']}-{sample['mode']}-0").glob("*.json"):
            process = json.loads(path.read_text())
            for event in process["events"]:
                begin = process["start_perf_counter"] + event["start"]
                end = begin + event["seconds"]
                clipped = max(0, min(end, sample["end_perf_counter"]) - max(begin, sample["start_perf_counter"]))
                if not clipped:
                    continue
                category = event["category"]
                if category == "subprocess":
                    # Labels originate from Python argv lists, not untrusted input or shell strings.
                    executable = event["label"].split("'", 2)[1]
                    program = Path(executable).name
                    category = ("pandoc" if program == "pandoc" else "uv" if program == "uv" else
                                "trazo_child_overlapping" if "'-m', 'trazo'" in event["label"] else "project_script")
                totals[category] = totals.get(category, 0) + clipped
                counts[category] = counts.get(category, 0) + 1
                if category == "python_render_span":
                    spans.append({"document": event["label"], "start_from_operation": begin - sample["start_perf_counter"],
                                  "duration": event["seconds"], "end_from_operation": end - sample["start_perf_counter"]})
            for function in process["functions"]:
                if "/src/trazo/" not in function["file"] or function["file"].endswith("mcp.py"):
                    continue  # MCP's idle stdin iteration is not useful render work.
                key = f"{Path(function['file']).name}:{function['line']}:{function['function']}"
                row = functions.setdefault(key, {"calls": 0, "own_seconds": 0., "cumulative_seconds": 0.})
                for field in row:
                    row[field] += function[field]
        result.append({"operation": sample["operation"], "mode": sample["mode"],
                       "instrumented_seconds": sample["seconds"], "save_ack_seconds": sample.get("save_ack_seconds"),
                       "overlapping_span_seconds": totals, "span_counts": counts, "render_spans": spans,
                       "whole_process_top_python_self": sorted(functions.items(), key=lambda kv: -kv[1]["own_seconds"])[:15],
                       "browser_inclusive_events": sample.get("trace", {}).get("inclusive_seconds", {})})
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile_run", type=Path)
    args = parser.parse_args()
    rows = summarize(args.profile_run)
    dump(args.profile_run / "profile-summary.json", {"warning": "Diagnostic runs, nested spans and concurrent threads overlap; not an additive wall-time partition.", "operations": rows})
    for row in rows:
        print(row["operation"], row["mode"], {key: round(value, 4) for key, value in row["overlapping_span_seconds"].items()})
