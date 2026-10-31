"""Automatic acceptance: `run.py --auto push|tag` decides by itself what to run.

    push   a branch being pushed (pre-push): n1 and n2 for the files the branch changed since it left
           origin/develop (the working branch; main moves only at releases), plus uncommitted and
           untracked ones.
    tag    a release tag being pushed: n1 and n2 for the files changed since the previous release tag
           (passes cached by pushes cost nothing). A skip also blocks it.

push and tag make the data seed (about 100 MB, online) when a selected scenario needs it and it is
missing; a build that fails stops the run with its reason and the command to retry.
Scenarios that passed with nothing they depend on changed are not run again (resultcache.py).
A failed scenario is run once more, and a pass then is reported as such. A scenario that is skipped (no login, a usage limit) proved nothing,
but an agent cannot renew a login: a push goes on and lists it with how to go on, and a release
tag is blocked until it passes (a skip is never cached, so the next run tries it again)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from . import registry, report, versions

MODES = ("push", "tag")
MAKES_SEED = ("push", "tag")   # modes that download the data seed themselves when a selected scenario needs it


def configure(mode: str, environ: dict[str, str] | None = None, tag: str | None = None) -> None:
    """The setting an automatic run implies, set before the fingerprints read it: for a tag run, the
    release being pushed, so that the last release is the one before it (versions.latest_tag)."""
    environ = os.environ if environ is None else environ
    environ.pop(versions.RELEASING, None)
    if mode == "tag" and tag:
        environ[versions.RELEASING] = tag


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)


# Work branches off develop; main is only the fallback for a clone without it (a fork that copied
# main only), since main lags develop until the next release.
BASES = ("origin/develop", "develop", "origin/main", "main")


def base_ref(repo: Path) -> str:
    """The commit where this branch left develop: the merge base of HEAD with the first of BASES that exists."""
    for ref in BASES:
        found = _git(repo, "merge-base", ref, "HEAD")
        if found.returncode == 0 and found.stdout.strip():
            return found.stdout.strip()
    raise registry.CatalogError("neither develop nor main exists to compare this branch with; "
                                "name a base with --changed-from <ref>")


def previous_tag(tag: str, repo: Path) -> str | None:
    """The highest vX.Y.Z release tag below `tag` (which need not exist yet), or None for the first release."""
    def number(name: str):
        found = versions.TAG_PATTERN.fullmatch(name)
        return tuple(map(int, found.groups())) if found else None
    wanted = number(tag)
    if wanted is None:
        raise registry.CatalogError(f"{tag} is not a release tag (vX.Y.Z)")
    below = sorted((n, name) for name in _git(repo, "tag", "--list", "v*").stdout.split()
                   if (n := number(name)) is not None and n < wanted)
    return below[-1][1] if below else None


def tag_changes(tag: str, repo: Path) -> list[str]:
    """Files that differ between the previous release and HEAD (the commit being tagged), or every file for a first release."""
    before = previous_tag(tag, repo)
    args = ["diff", "--name-only", "--no-renames", "-z", before, "HEAD", "--"] if before else ["ls-tree", "-r", "--name-only", "-z", "HEAD"]
    done = _git(repo, *args)
    if done.returncode:
        raise registry.CatalogError(f"git {' '.join(args)}: {done.stderr.strip()}")
    return sorted(n for n in done.stdout.split("\0") if n)


def changes(mode: str, repo: Path, tag: str | None = None, changed_from: str | None = None) -> list[str]:
    if mode == "tag":
        if not tag:
            raise registry.CatalogError("--auto tag needs --tag vX.Y.Z")
        return tag_changes(tag, repo)
    return registry.changed_files(changed_from or base_ref(repo), repo)


def choose(entries: list[registry.Entry], changed: list[str], patterns=()) -> list[registry.Entry]:
    return registry.select(entries, patterns=patterns, changed=changed)


def blocking(result: report.Result, mode: str) -> bool:
    """A result that stops the run: a failure always; a skip (nothing was proven) only for a release tag."""
    return result.status == report.FAILED or (result.status == report.SKIPPED and mode == "tag")


def how_to_proceed(result: report.Result) -> str:
    reason = result.reason.lower()
    if "usage limit" in reason:
        return "wait for the provider's limit to reset, then run it again"
    if "needs the network" in reason:
        return "run it with network access: add `--online` (`make acceptance ACCEPTANCE_ARGS=--online`)"
    if any(word in reason for word in ("login", "keychain", "auth.json")):
        return "renew the login outside the harness, in a normal terminal (an agent never logs in), then run again"
    if "not on path" in reason or "not installed" in reason:
        return "install that CLI and put it on PATH"
    return "read the reason in the report"


def blockers(results: list[report.Result], mode: str) -> list[str]:
    """What stops the run: a line per result, then each distinct way to go on once, with the ids it applies to."""
    return _describe([r for r in results if blocking(r, mode)])


def unproven(results: list[report.Result], mode: str) -> list[str]:
    """The skips that do not stop this run (a push), described like blockers."""
    return _describe([r for r in results if r.status == report.SKIPPED and not blocking(r, mode)])


def _describe(stopped: list[report.Result]) -> list[str]:
    lines = [f"{r.status.upper():8} {r.id}: {(r.reason or 'no reason given').splitlines()[0]}" for r in stopped]
    advice: dict[str, list[str]] = {}
    for r in stopped:
        advice.setdefault(how_to_proceed(r), []).append(r.id)
    return lines + [f"To proceed ({', '.join(ids)}): {text}" for text, ids in advice.items()]
