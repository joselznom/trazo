#!/usr/bin/env python3
"""Run a package's unit tests in parallel, one process per test module.

    python scripts/test-package.py PACKAGE [--jobs N] [--shard I/N]

`make test-package` runs it on packages/trazo. Each tests/test*.py runs as
`python -m unittest discover -s tests -p <file>` inside PACKAGE, JOBS of them at a time
(below; --jobs N for another number). The tests are the ones a single
`python -m unittest discover -s tests` runs: both ways are counted first, and the run stops
when they differ. A module that passes prints one line with its time; one that fails, its
whole unittest report.

--shard I/N runs the I-th of N parts of the modules, for CI to run the parts on N machines:
the parts 1/N to N/N hold every module once.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Run inside PACKAGE: the tests unittest discovers in all of tests/, and in each of its test*.py.
COUNT = """
import json, unittest
from pathlib import Path
discover = lambda **pattern: unittest.TestLoader().discover("tests", **pattern).countTestCases()
files = {path.name: discover(pattern=path.name) for path in sorted(Path("tests").glob("test*.py"))}
print("\\n" + json.dumps({"whole": discover(), "files": files}))
"""
RAN = re.compile(r"^Ran (\d+) tests? in ", re.MULTILINE)
SKIPPED = re.compile(r"^(?:OK|FAILED) \(.*?skipped=(\d+)", re.MULTILINE)
# A module at a time for every two processors, less one. Tests that wait on a deadline (a page
# in Chrome, a process to stop) fail now and then when the processors are busy: with 10 at a
# time on a 10-processor Mac, and with 2 at a time on GitHub's 3- and 4-processor runners.
JOBS = max(1, (os.cpu_count() or 1) // 2 - 1)


def discovered(package: Path) -> dict[str, int]:
    """The number of tests in each tests/test*.py that has any, as unittest discovers them."""
    answer = subprocess.run([sys.executable, "-c", COUNT], cwd=package, capture_output=True,
                            encoding="utf-8", errors="replace")
    if answer.returncode:
        raise SystemExit(f"test-package: could not list the tests of {package}:\n{answer.stdout}{answer.stderr}")
    counts = json.loads(answer.stdout.splitlines()[-1])
    held = sum(counts["files"].values())
    if held != counts["whole"]:
        raise SystemExit(f"test-package: unittest discover finds {counts['whole']} tests and the test*.py files "
                         f"of {package / 'tests'} hold {held}: move the others there, so every test runs.")
    return {name: tests for name, tests in counts["files"].items() if tests}


def shard(value: str) -> tuple[int, int]:
    """--shard I/N as (I, N)."""
    part = re.fullmatch(r"(\d+)/(\d+)", value)
    if not part or not 1 <= int(part.group(1)) <= int(part.group(2)):
        raise argparse.ArgumentTypeError(f"{value!r} is not I/N with I from 1 to N")
    return int(part.group(1)), int(part.group(2))


def run_module(package: Path, name: str) -> tuple[str, int, str, float]:
    """One module's exit status, unittest report and seconds."""
    start = time.monotonic()
    done = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", name], cwd=package,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", errors="replace")
    return name, done.returncode, done.stdout, time.monotonic() - start


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a package's unit tests in parallel, one process per module.")
    parser.add_argument("package", type=Path, help="the folder that holds tests/")
    parser.add_argument("--jobs", type=int, default=JOBS, help=f"modules run at a time (here {JOBS})")
    parser.add_argument("--shard", type=shard, default=(1, 1), metavar="I/N", help="run the I-th of N parts")
    args = parser.parse_args()
    package, jobs, (part, parts) = args.package.resolve(), max(1, args.jobs), args.shard
    expected = discovered(package)
    # By name, in turns: the long browser modules sit together in the alphabet.
    expected = {name: expected[name] for name in sorted(expected)[part - 1::parts]}
    start = time.monotonic()
    ran = skipped = 0
    failed: list[str] = []
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for future in as_completed([pool.submit(run_module, package, name) for name in expected]):
            name, status, report, seconds = future.result()
            # The last lines of unittest's report; a test's own output comes before them.
            tests = int((RAN.findall(report) or ["0"])[-1])
            ran += tests
            skipped += int((SKIPPED.findall(report) or ["0"])[-1])
            if status == 0 and tests == expected[name]:
                print(f"ok      {name}: {tests} tests in {seconds:.1f} s", flush=True)
            else:
                failed.append(name)
                print(f"FAILED  {name}: exit status {status}, {tests} of {expected[name]} tests ran\n{report}",
                      flush=True)
    print(f"\nRan {ran} tests in {len(expected)} modules in {time.monotonic() - start:.1f} s, "
          f"{jobs} at a time (skipped={skipped})" + (f", part {part} of {parts}" if parts > 1 else ""))
    if failed:
        print(f"\nFAILED (modules={len(failed)}): {', '.join(sorted(failed))}")
        return 1
    print("\nOK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
