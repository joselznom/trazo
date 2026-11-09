"""Experiment: run test modules again and again until a deadline, each pass in a fresh
process as CI does, and keep every pass's verbose output and per-test durations.

    python scripts/windows-flake/loop.py --minutes 35 --out OUT MODULE...
    python scripts/windows-flake/loop.py --minutes 35 --out OUT discover   (the whole package suite)

Run from the repository root. The summary (OUT/summary.json) lists each pass with its
duration and failures; the exit status is 1 when any pass failed.
"""

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--minutes", type=float, required=True)
parser.add_argument("--out", type=Path, required=True)
parser.add_argument("modules", nargs="+")
args = parser.parse_args()
args.out.mkdir(parents=True, exist_ok=True)
deadline = time.monotonic() + args.minutes * 60
passes = []
while time.monotonic() < deadline:
    number = len(passes) + 1
    started = datetime.now(UTC).isoformat(timespec="milliseconds")
    clock = time.monotonic()
    whole = args.modules == ["discover"]
    command = (["discover", "-s", "tests", "-v", "--durations", "0"] if whole
               else ["-v", "--durations", "0", *args.modules])
    proc = subprocess.run([sys.executable, "-m", "unittest", *command],
                          cwd="packages/trazo" if whole else "packages/trazo/tests",
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    text = proc.stdout + proc.stderr
    (args.out / f"pass-{number:03}.log").write_text(text, encoding="utf-8")
    ran = re.search(r"^Ran (\d+) tests? in ([\d.]+)s", text, re.M)
    failures = [f"{kind} {test}" for kind, test in re.findall(r"^(FAIL|ERROR): \S+ \((\S+)\)", text, re.M)]
    passes.append({"pass": number, "started": started, "seconds": round(time.monotonic() - clock, 1),
                   "tests": int(ran.group(1)) if ran else None, "exit": proc.returncode, "failures": failures})
    print(json.dumps(passes[-1]), flush=True)
    (args.out / "summary.json").write_text(json.dumps(passes, indent=1), encoding="utf-8")
failed = [p for p in passes if p["exit"] != 0]
tests = sum(p["tests"] or 0 for p in passes)
print(f"{len(passes)} passes, {tests} tests, {sum(len(p['failures']) for p in failed)} failures in {len(failed)} passes")
sys.exit(1 if failed else 0)
