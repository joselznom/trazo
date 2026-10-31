"""The scenario catalog (catalog.toml), the implementations registered
against it, and the selection of which to run."""

from __future__ import annotations

import fnmatch
import importlib
import re
import subprocess
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
CATALOG = HERE / "catalog.toml"
LEVELS = ("n1", "n2")
# What a scenario may declare it needs; the runner skips it, with a reason, when it is missing.
NEEDS = ("darwin", "seed", "online")


class Skip(Exception):
    """The scenario cannot run here; the message says why and what to do."""


class CatalogError(Exception):
    pass


@dataclass(frozen=True)
class Entry:
    id: str
    level: str
    title: str
    source: str
    areas: tuple[str, ...]
    # Runs the checkout's real plugin, so its fingerprint also covers the whole checkout (resultcache.py).
    uses_checkout: bool = False


@dataclass(frozen=True)
class Implementation:
    function: Callable
    needs: tuple[str, ...]


IMPLEMENTATIONS: dict[str, Implementation] = {}


def scenario(id: str, needs: Iterable[str] = ()) -> Callable[[Callable], Callable]:
    """Register `function(ctx)` as the implementation of catalog entry `id`.
    It passes when it returns, fails on an exception (an assert is the usual way)
    and is skipped when it raises Skip(reason). `needs` is a subset of NEEDS."""
    needs = tuple(needs)
    if unknown := set(needs) - set(NEEDS):
        raise CatalogError(f"{id}: unknown needs {sorted(unknown)}; choose from {NEEDS}")

    def register(function: Callable) -> Callable:
        if id in IMPLEMENTATIONS:
            raise CatalogError(f"{id} is implemented twice")
        IMPLEMENTATIONS[id] = Implementation(function, needs)
        return function
    return register


def load_catalog(path: Path = CATALOG) -> list[Entry]:
    """The entries. An entry's areas are the catalog's `checkout_areas` (for a scenario that runs the
    checkout's plugin) plus its own `areas`: a scenario that forgets a plugin input still hears about its change."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    checkout_areas = tuple(data.get("checkout_areas", ()))
    entries, seen = [], set()
    for row in data.get("scenario", []):
        try:
            id_, level = row["id"], row["level"]
            uses_checkout = row.get("checkout", True)
            extra = tuple(row.get("areas", ()))
            entry = Entry(id_, level, row["title"], row["source"],
                          tuple(dict.fromkeys((checkout_areas if uses_checkout else ()) + extra)), bool(uses_checkout))
        except KeyError as exc:
            raise CatalogError(f"{path.name}: {row.get('id', row)} lacks {exc}") from exc
        if entry.level not in LEVELS or not entry.id.startswith(f"{entry.level}-"):
            raise CatalogError(f"{entry.id}: level must be one of {LEVELS} and the id must start with it")
        if entry.id in seen:
            raise CatalogError(f"{entry.id} appears twice in {path.name}")
        if not entry.areas or not all(isinstance(a, str) for a in entry.areas):
            raise CatalogError(f"{entry.id}: areas must be a non-empty list of path globs")
        seen.add(entry.id)
        entries.append(entry)
    return entries


def discover(entries: list[Entry]) -> None:
    """Import n1 and n2 (each package imports its modules), then check that every
    implementation names a catalog entry: a typo would otherwise hide a scenario."""
    for level in LEVELS:
        importlib.import_module(f"{__package__}.{level}")
    unknown = set(IMPLEMENTATIONS) - {e.id for e in entries}
    if unknown:
        raise CatalogError(f"implemented but not in catalog.toml: {', '.join(sorted(unknown))}")


def import_modules(package: str, folder: Path) -> None:
    """For n1/__init__.py and the like: import every module of the package."""
    for path in sorted(folder.glob("*.py")):
        if path.stem != "__init__":
            importlib.import_module(f"{package}.{path.stem}")


def glob_regex(pattern: str) -> re.Pattern:
    """`**` crosses folders, `*` and `?` do not; a pattern ending in `/**` matches what is under it."""
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out))


def touches(entry: Entry, changed: Iterable[str]) -> bool:
    patterns = [glob_regex(area) for area in entry.areas]
    return any(p.fullmatch(path) for path in changed for p in patterns)


def changed_files(ref: str, repo: Path) -> list[str]:
    """Files that differ from `ref`: committed since it, uncommitted, and untracked (not ignored).
    A rename counts as a deletion and an addition (--no-renames): its old path leaving an area is a change too."""
    def lines(*args: str) -> list[str]:
        done = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
        if done.returncode:
            raise CatalogError(f"git {' '.join(args)}: {done.stderr.strip()}")
        return [n for n in done.stdout.split("\0") if n]
    return sorted(set(lines("diff", "--name-only", "--no-renames", "-z", ref, "--")) | set(lines("ls-files", "-z", "--others", "--exclude-standard")))


def select(entries: list[Entry], levels: Iterable[str] = (), patterns: Iterable[str] = (),
           changed: list[str] | None = None) -> list[Entry]:
    """Entries matching every filter given: level, an id glob (any of `patterns`), changed files."""
    levels, patterns = tuple(levels), tuple(patterns)
    chosen = []
    for entry in entries:
        if levels and entry.level not in levels:
            continue
        if patterns and not any(fnmatch.fnmatchcase(entry.id, p) for p in patterns):
            continue
        if changed is not None and not touches(entry, changed):
            continue
        chosen.append(entry)
    return chosen
