"""report.md and report.json for one run, in .build/acceptance/<UTC timestamp>/."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

PASSED, FAILED, SKIPPED, NOT_AUTOMATED = "passed", "failed", "skipped", "not automated"
NOT_APPLICABLE = "not applicable"


@dataclass
class Result:
    id: str
    level: str
    title: str
    status: str
    reason: str = ""       # why skipped, the failure's message, or the note of a cached or retried pass
    seconds: float = 0.0
    evidence: list[dict] = field(default_factory=list)
    traceback: str = ""
    cached: bool = False   # passed in an earlier run and nothing it depends on changed (resultcache.py)
    retried: bool = False  # failed once and passed on the automatic retry

    @property
    def label(self) -> str:
        return self.status + (" (cached)" if self.cached else " on retry" if self.retried else "")


def counts(results: list[Result]) -> dict[str, int]:
    return {status: sum(r.status == status for r in results) for status in (PASSED, FAILED, SKIPPED, NOT_AUTOMATED, NOT_APPLICABLE)}


def summary(results: list[Result]) -> str:
    # Not applicable only happens in a first release's tag run, so it is left out when there is none.
    line = ", ".join(f"{n} {status}" for status, n in counts(results).items() if n or status != NOT_APPLICABLE)
    extra = [f"{n} {what}" for what, n in (("cached", sum(r.cached for r in results)),
                                           ("passed on retry", sum(r.retried for r in results))) if n]
    return line + (f" ({', '.join(extra)})" if extra else "")


def _evidence_lines(item: dict) -> list[str]:
    if item["kind"] == "command":
        head = f"- `{' '.join(item['argv'])}` -> exit {item['exit']} in {item['seconds']}s"
        return [head] + (["", "  ```", *("  " + line for line in item["output"].splitlines()), "  ```"] if item["output"] else [])
    if item["kind"] == "note":
        return [f"- {item['text']}"]
    if item["kind"] == "path":
        return [f"- {item['label']}: `{item['path']}`"]
    return [f"- {item['label']}: `{item['value']}`"]


def markdown(meta: dict, results: list[Result]) -> str:
    lines = ["# Acceptance report", "", f"- Command: `{meta['command']}`",
             f"- Commit: {meta['commit']}{' (uncommitted changes)' if meta['dirty'] else ''}",
             f"- Started: {meta['started']}", f"- Result: {summary(results)}", "",
             "| Scenario | Status | Seconds | Note |", "| --- | --- | --- | --- |"]
    for r in results:
        lines.append(f"| {r.id} | {r.label} | {r.seconds:.1f} | {r.reason.splitlines()[0] if r.reason else ''} |")
    for r in results:
        if r.status == NOT_AUTOMATED or not (r.evidence or r.reason or r.traceback):
            continue
        lines += ["", f"## {r.id}: {r.label}", "", r.title, ""]
        if r.reason:
            lines += [f"Reason: {r.reason}", ""]
        for item in r.evidence:
            lines += _evidence_lines(item)
        if r.traceback:
            lines += ["", "```", r.traceback.rstrip(), "```"]
    return "\n".join(lines) + "\n"


def write(folder: Path, meta: dict, results: list[Result]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "report.md").write_text(markdown(meta, results), encoding="utf-8")
    document = {**meta, "counts": counts(results), "results": [asdict(r) for r in results]}
    (folder / "report.json").write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")
