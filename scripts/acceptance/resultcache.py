"""Passes that need not run again: a scenario that passed is remembered under a fingerprint of
everything it depends on, and runs again only when that changes.

The fingerprint hashes the content of every file matching the scenario's catalog `areas` (tracked,
uncommitted and untracked, never what .gitignore ignores), the harness's own code, the highest
release tag (the "last release" the scenarios install from) and the data seed's key. A scenario
that runs the checkout's plugin (`uses_checkout`) also hashes the whole checkout, which is the content of the plugin
folder versions.checkout_plugin builds, so a plugin input the catalog forgot still misses the cache;
only files nothing reads at run time (CHECKOUT_SKIP: `docs/`, the top-level Markdown files and the
acceptance harness's Markdown files) are left out of that hash.
Only passes are stored: a failure or a skip is never cached, so a flaky or blocked scenario is tried
again. The CLIs' versions are not in the fingerprint; `--no-cache` forces a run after updating one.

Entries live in the shared cache (`<cache>/results/<id>/<fingerprint>.json`), so worktrees and
pre-push's clean checkout share them. Their content is fixed by the key, so sharing is safe."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from collections.abc import Iterable, Mapping
from pathlib import Path

from . import registry, versions
from . import seed as seeds

FORMAT = 4  # bumped when the fingerprint's inputs change, so older passes are not trusted
KEEP_PER_SCENARIO = 10   # alternating branches leave several fingerprints; older ones are dropped
# The harness code except what cannot change a verdict: its tests and docs.
HARNESS = "scripts/acceptance/"
HARNESS_SKIP = re.compile(r"scripts/acceptance/(tests/.*|.*\.md)")
# Files no n1 or n2 scenario executes or reads, so a docs-only change does not rerun them (n2 spends the
# owner's real CLI logins). Only these: skills, template/, the package's assets and its README (the
# package build reads it) stay in the checkout hash, and so does any file we are unsure about.
CHECKOUT_SKIP = re.compile(
    r"docs/.*|(README|CHANGELOG|AGENTS|CLAUDE|CODING_STANDARDS|REVIEW)\.md"
    r"|scripts/acceptance/.*\.md")


class FileHashes:
    """The checkout's files and a content hash of each, computed once for a whole run."""

    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self.names = versions.checkout_files(repo)
        self._hashes: dict[str, bytes] = {}

    def of(self, name: str) -> bytes:
        if name not in self._hashes:
            path = self.repo / name
            if path.is_symlink():
                body = b"link " + os.readlink(path).encode()
            else:
                body = (b"x " if os.access(path, os.X_OK) else b"- ") + hashlib.sha256(path.read_bytes()).digest()
            self._hashes[name] = body
        return self._hashes[name]

    def digest(self, names: Iterable[str], head: str) -> str:
        digest = hashlib.sha256(head.encode() + b"\0")
        for name in sorted(names):
            digest.update(name.encode() + b"\0" + self.of(name))
        return digest.hexdigest()

    def harness(self) -> list[str]:
        return [n for n in self.names if n.startswith(HARNESS) and not HARNESS_SKIP.fullmatch(n)]


def seed_key(repo: Path) -> str:
    """What the data seed is keyed by (the versions setup downloads), or "" when this tree has no setup."""
    try:
        return seeds.key(seeds.pins(repo))
    except (OSError, versions.VersionError):
        return ""


def latest_tag_commit(repo: Path) -> str:
    try:
        tag = versions.latest_tag(repo)
        return versions.git("rev-parse", f"{tag}^{{commit}}", repo=repo, text=True).stdout.strip()
    except (versions.VersionError, subprocess.CalledProcessError):
        return ""


class Fingerprints:
    def __init__(self, repo: Path) -> None:
        self.files = FileHashes(repo)
        self.tag = latest_tag_commit(repo)
        self.seed = seed_key(repo)

    def of(self, entry: registry.Entry) -> str:
        patterns = [registry.glob_regex(area) for area in entry.areas]
        matching = [n for n in self.files.names if any(p.fullmatch(n) for p in patterns)]
        # The checkout's plugin folder is every file versions.checkout_files lists, less what nothing reads.
        checkout = [n for n in self.files.names if not CHECKOUT_SKIP.fullmatch(n)]
        names = sorted(set(checkout if entry.uses_checkout else matching) | set(self.files.harness()))
        head = f"{FORMAT} {entry.id} {self.tag} {self.seed}"
        return self.files.digest(names, head)


def _folder(cache: Path, entry_id: str) -> Path:
    return cache / "results" / entry_id


def lookup(cache: Path, entry_id: str, fingerprint: str) -> dict | None:
    """The stored pass, or None."""
    path = _folder(cache, entry_id) / f"{fingerprint[:40]}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("fingerprint") != fingerprint or data.get("id") != entry_id:
        return None
    os.utime(path)
    return data


def store(cache: Path, entry_id: str, fingerprint: str, info: Mapping[str, object]) -> None:
    """Remember a pass. Written to a temporary file and renamed, so a reader never sees half of it."""
    versions.check_cache(cache)
    folder = _folder(cache, entry_id)
    folder.mkdir(parents=True, exist_ok=True)
    record = {"id": entry_id, "fingerprint": fingerprint, "passed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **info}
    handle, temp = tempfile.mkstemp(prefix=".tmp-", dir=folder)
    with os.fdopen(handle, "w", encoding="utf-8") as out:
        json.dump(record, out, indent=1)
    os.replace(temp, folder / f"{fingerprint[:40]}.json")
    entries = sorted((p for p in folder.glob("*.json")), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in entries[KEEP_PER_SCENARIO:]:
        old.unlink(missing_ok=True)
