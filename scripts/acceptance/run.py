#!/usr/bin/env python3
"""Run the acceptance scenarios (see scripts/acceptance/README.md).

    uv run --quiet --project packages/trazo python scripts/acceptance/run.py --list
    ... run.py --level n1 [--scenario 'n1-port*'] [--changed-from main] [--online] [--keep] [--no-cache]
    ... run.py --auto push|tag [--tag vX.Y.Z]     (what pre-push runs)

Exit status: 0 when nothing failed (skipped and not automated scenarios are
listed, never counted as passed), 1 on a failure, 2 on a usage or catalog error.
With --auto tag a skipped scenario also gives 1: nothing was proven. A push
lists its skips under "Not proven" and gives 0.
"""

from __future__ import annotations

import argparse
import platform
import shlex
import signal
import subprocess
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from acceptance import auto, guard, limits, registry, report, resultcache, versions  # noqa: E402
from acceptance import seed as seeds
from acceptance.context import Context, Shared  # noqa: E402
from acceptance.evidence import Evidence  # noqa: E402
from acceptance.sandbox import Sandbox, minimal_env  # noqa: E402

BUILD = versions.REPO / ".build" / "acceptance"


def stop_on_terminate() -> None:
    """SIGTERM stops like Ctrl-C: the scenario's `finally`
    runs the sandbox cleanup, which ends every process group the harness started. Only the first signal
    counts, so a second one cannot cut the cleanup short."""
    def handler(signum, frame):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, handler)


def parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--list", action="store_true", help="list the selected catalog entries and exit")
    parser.add_argument("--level", action="append", choices=registry.LEVELS, default=[], help="only this level (repeatable)")
    parser.add_argument("--scenario", action="append", default=[], metavar="GLOB", help="only ids matching this glob (repeatable)")
    parser.add_argument("--changed-from", metavar="REF", help="only scenarios whose areas match files changed since REF, uncommitted and untracked included")
    parser.add_argument("--online", action="store_true", help="allow downloads: make the data seed if missing; run scenarios that need the network")
    parser.add_argument("--keep", action="store_true", help="keep each scenario's sandbox and print where it is")
    parser.add_argument("--no-cache", action="store_true", help="run every selected scenario even if it passed before and nothing it depends on changed")
    parser.add_argument("--auto", choices=auto.MODES, help="choose by the files changed since develop and judge for a gate: "
                        "push (pre-push) or tag (a release tag: the scenarios since the previous release pass, skips included)")
    parser.add_argument("--tag", metavar="vX.Y.Z", help="with --auto tag: the release being pushed (compared with the previous release tag)")
    return parser.parse_args(argv)


def git_state() -> tuple[str, bool]:
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=versions.REPO, capture_output=True, text=True).stdout.strip()
    return git("rev-parse", "--short=12", "HEAD"), bool(git("status", "--porcelain"))


def listing(entries: list[registry.Entry]) -> str:
    rows = [f"{e.id:34} {'automated' if e.id in registry.IMPLEMENTATIONS else 'not automated':14} {e.source:34} {e.title}"
            for e in entries]
    return "\n".join(rows + [f"{len(entries)} scenarios, {sum(e.id in registry.IMPLEMENTATIONS for e in entries)} automated"])


def blocked(needs: tuple[str, ...], args: argparse.Namespace, seed: seeds.Seed | None) -> str:
    """Why a scenario cannot run here, or ""."""
    if "darwin" in needs and platform.system() != "Darwin":
        return f"needs macOS (this is {platform.system()})"
    if "online" in needs and not args.online:
        return "needs the network: run with --online"
    if "seed" in needs and seed is None:
        return "needs the data seed (uv, Python and the install): run with --online once to make it"
    return ""


def make_seed(args: argparse.Namespace, shared: Shared, wanted: bool, log: Evidence) -> seeds.Seed | None:
    """The cached seed; made first when a selected scenario needs it and the run may download:
    --online, or an automatic push or tag run (nobody is there to run --online by hand)."""
    found = seeds.find(shared.cache)
    if found or not (wanted and (args.online or args.auto in auto.MAKES_SEED)):
        return found
    print("Making the data seed with the real setup.sh (downloads about 100 MB; once per pinned versions)...",
          file=sys.stderr, flush=True)
    try:
        plugin = versions.checkout_plugin(shared.cache, 1)
        return seeds.build(shared.cache, plugin.path, minimal_env, lambda argv, done: log.command(
            argv, done.returncode, done.stdout + done.stderr, 0))
    except (versions.VersionError, guard.GuardError, subprocess.TimeoutExpired) as error:
        # A block must carry the real reason (the head and the tail of setup's output) and the command to try again.
        lines = (str(error) or type(error).__name__).splitlines()
        reason = lines if len(lines) <= 6 else [lines[0], *lines[-4:]]
        retry = f"make acceptance-auto AUTO={args.auto}" if args.auto else "run it again"
        raise versions.VersionError("\n".join([*reason, f"To retry once the cause is fixed (network, guard): {retry}"])) from error


def execute(entry: registry.Entry, args: argparse.Namespace, shared: Shared, seed: seeds.Seed | None) -> report.Result:
    result = report.Result(entry.id, entry.level, entry.title, report.NOT_AUTOMATED)
    implementation = registry.IMPLEMENTATIONS.get(entry.id)
    if implementation is None:
        return result
    if reason := blocked(implementation.needs, args, seed):
        result.status, result.reason = report.SKIPPED, reason
        return result
    evidence = Evidence()
    started = datetime.now(UTC)
    sandbox = None
    try:
        sandbox = Sandbox(entry.id, evidence, keep=args.keep, seed=seed)
        context = Context(entry.id, sandbox, evidence, shared)
        implementation.function(context)
        result.status = report.PASSED
    except registry.Skip as skip:
        result.status, result.reason = report.SKIPPED, str(skip)
    except versions.FirstRelease as first:  # nothing to update from: never a pass, and unlike a skip never blocks a tag
        result.status, result.reason = report.NOT_APPLICABLE, str(first)
    except AssertionError as failure:
        result.status, result.reason, result.traceback = report.FAILED, str(failure) or "assertion failed", traceback.format_exc()
    except Exception as failure:  # a scenario's bug or an unexpected state is a failure, not a crash of the run
        result.status, result.reason, result.traceback = report.FAILED, f"{type(failure).__name__}: {failure}", traceback.format_exc()
    finally:
        if sandbox is not None:
            sandbox.cleanup()
        result.seconds = (datetime.now(UTC) - started).total_seconds()
        result.evidence = evidence.items
    if result.status == report.FAILED and (message := limits.usage_limit(result.reason)):
        # A provider refusing the session says nothing about the code under test.
        result.status, result.reason, result.traceback = report.SKIPPED, f"usage limit reached ({message}); wait for it to reset", ""
    return result


def with_retry(entry: registry.Entry, args: argparse.Namespace, shared: Shared, seed: seeds.Seed | None) -> report.Result:
    """execute(), and in an automatic run one more attempt for a failed scenario (they are cheap and
    can flake); a pass then says so."""
    result = execute(entry, args, shared, seed)
    if not (args.auto and result.status == report.FAILED):
        return result
    first = result
    result = execute(entry, args, shared, seed)
    summary = (first.reason or "failed").splitlines()[0]
    noted = {"kind": "note", "text": f"first attempt failed: {first.reason}\n{first.traceback}".strip()}
    result.evidence = [noted, *first.evidence, *result.evidence]
    if result.status == report.PASSED:
        result.retried, result.reason = True, f"passed on retry; the first attempt failed: {summary}"
    elif result.status == report.FAILED:
        result.reason = f"{result.reason}\n(failed on retry too; the first attempt: {summary})"
    return result


def cached_result(entry: registry.Entry, hit: dict) -> report.Result:
    result = report.Result(entry.id, entry.level, entry.title, report.PASSED, f"cached pass from {hit.get('passed_at', '?')}", cached=True)
    result.evidence = [{"kind": "note", "text": f"Nothing this scenario depends on changed since it passed at commit {hit.get('commit', '?')} "
                                                 f"(report {hit.get('report', '?')}, {hit.get('seconds', '?')}s). --no-cache runs it again."}]
    return result


def lookup_plan(chosen: list[registry.Entry], args: argparse.Namespace, shared: Shared) -> dict[str, tuple[str, dict | None]]:
    """For each scenario worth caching: its fingerprint and the stored pass, if any and wanted."""
    cacheable = [e for e in chosen if e.id in registry.IMPLEMENTATIONS]
    if not cacheable:
        return {}
    prints = resultcache.Fingerprints(shared.repo)
    plan = {}
    for entry in cacheable:
        fingerprint = prints.of(entry)
        plan[entry.id] = (fingerprint, None if args.no_cache else resultcache.lookup(shared.cache, entry.id, fingerprint))
    return plan


def run_selected(chosen: list[registry.Entry], args: argparse.Namespace, shared: Shared, meta: dict, folder: Path) -> int:
    results: list[report.Result] = []
    try:
        plan = lookup_plan(chosen, args, shared)
        todo = [e for e in chosen if not plan.get(e.id, ("", None))[1]]
        needs_seed = any("seed" in registry.IMPLEMENTATIONS[e.id].needs for e in todo if e.id in registry.IMPLEMENTATIONS)
        seed_log = Evidence()
        seed = make_seed(args, shared, needs_seed, seed_log)
        meta["seed"] = {"folder": str(seed.root) if seed else None, "pins": seed.pins if seed else None,
                        "made_in_this_run": bool(seed_log.items), "setup_run": seed_log.items}
        for entry in chosen:
            fingerprint, hit = plan.get(entry.id, ("", None))
            result = cached_result(entry, hit) if hit else with_retry(entry, args, shared, seed)
            if fingerprint and not hit and result.status == report.PASSED:
                resultcache.store(shared.cache, entry.id, fingerprint, {
                    "commit": meta["commit"], "seconds": round(result.seconds, 1), "report": str(folder / "report.md")})
            results.append(result)
            print(f"{result.label.upper():22} {entry.id}" + (f"  ({result.reason.splitlines()[0]})" if result.reason else ""), flush=True)
    except (KeyboardInterrupt, versions.VersionError) as error:
        print(f"run.py: stopped: {error or 'interrupted'}", file=sys.stderr)
        return 1 if results else 2
    finally:
        report.write(folder, meta, results)
        print(f"\n{report.summary(results)}\nReport: {folder / 'report.md'}")
    if args.auto and (stops := auto.blockers(results, args.auto)):
        print("\nBlocked:\n" + "\n".join(stops))
    if args.auto and (skips := auto.unproven(results, args.auto)):
        print("\nNot proven (skipped; a release tag needs them to pass):\n" + "\n".join(skips))
    failed = report.counts(results)[report.FAILED]
    return 1 if failed or (args.auto and any(auto.blocking(r, args.auto) for r in results)) else 0


def main(argv: list[str]) -> int:
    args = parse(argv)
    if args.auto:
        auto.configure(args.auto, tag=args.tag)
        args.online = args.online or args.auto in ("push", "tag")   # pre-push may download the seed it lacks
    try:
        entries = registry.load_catalog()
        if args.auto:
            changed = auto.changes(args.auto, versions.REPO, args.tag, args.changed_from)
            chosen = auto.choose(entries, changed, args.scenario)
        else:
            changed = registry.changed_files(args.changed_from, versions.REPO) if args.changed_from else None
            chosen = registry.select(entries, args.level, args.scenario, changed)
        if chosen or args.list:
            registry.discover(entries)  # imports every level: skipped when nothing runs, to stay instant
    except registry.CatalogError as error:
        print(f"run.py: {error}", file=sys.stderr)
        return 2
    if args.list:
        print(listing(chosen))
        return 0
    if not chosen:
        print("No scenario selected." if not args.auto else f"Nothing to run: no scenario depends on the {len(changed)} changed file(s).")
        return 0

    started = datetime.now(UTC)
    commit, dirty = git_state()
    meta = {"command": "run.py " + shlex.join(argv), "commit": commit, "dirty": dirty,
            "started": started.isoformat(timespec="seconds"), "online": args.online,
            "changed_from": args.changed_from, "changed_files": changed, "auto": args.auto}
    folder = BUILD / started.strftime("%Y%m%dT%H%M%SZ")
    try:
        shared = Shared(versions.cache_dir(), args.online)
    except guard.GuardError as error:
        print(f"run.py: {error}", file=sys.stderr)
        return 2
    return run_selected(chosen, args, shared, meta, folder)


if __name__ == "__main__":
    stop_on_terminate()
    sys.exit(main(sys.argv[1:]))
