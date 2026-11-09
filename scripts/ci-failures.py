#!/usr/bin/env python3
"""List the tests that failed in each attempt of a GitHub Actions run.

    python3 scripts/ci-failures.py <run id or URL>

`gh run view --log-failed` shows only the last attempt, so a test that failed and passed on a
re-run leaves no trace there. Needs `gh`, logged in; run it inside the repository.
"""

from __future__ import annotations

import io
import json
import re
import subprocess
import sys
import zipfile

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
FAILED_TEST = re.compile(r"(?:ERROR|FAIL): \S+ \(([\w.]+)")
RUN = re.compile(r"(?:^|/runs/)(\d+)")


def gh(path: str) -> bytes:
    answer = subprocess.run(["gh", "api", f"repos/{{owner}}/{{repo}}/actions/runs/{path}"], capture_output=True)
    if answer.returncode:
        raise SystemExit(f"gh api {path}: {answer.stderr.decode('utf-8', 'replace').strip()}")
    return answer.stdout


def failures(logs: zipfile.ZipFile) -> dict[str, list[str]]:
    """The failed tests of each failed job. The top-level files are the jobs' whole logs."""
    found = {}
    for name in logs.namelist():
        if "/" in name or not name.endswith(".txt"):
            continue
        text = ANSI.sub("", logs.read(name).decode("utf-8", "replace"))
        if "##[error]" in text:
            found[name.split("_", 1)[1].removesuffix(".txt")] = sorted(set(FAILED_TEST.findall(text)))
    return found


def main() -> None:
    wanted = RUN.search(sys.argv[1]) if len(sys.argv) == 2 else None
    if not wanted:
        raise SystemExit(__doc__)
    run = wanted.group(1)
    details = json.loads(gh(run))
    print(f"{details['name']} on {details['head_branch']} at {details['head_sha'][:7]}")
    for attempt in range(1, details["run_attempt"] + 1):
        print(f"attempt {attempt}: {json.loads(gh(f'{run}/attempts/{attempt}'))['conclusion'] or 'running'}")
        for job, tests in failures(zipfile.ZipFile(io.BytesIO(gh(f"{run}/attempts/{attempt}/logs")))).items():
            print(f"  {job}")
            for test in tests or ["no test failed: read the job's log"]:
                print(f"    {test}")


if __name__ == "__main__":
    main()
