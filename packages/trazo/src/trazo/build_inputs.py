"""The files a build stage reads: the sources a document includes, the
images its text names (and those Pandoc could not find), and the inputs a
generated file records; and
snapshots that tell whether those files held still while the stage ran.

A snapshot keeps each file's content hash and identity (mtime, size,
inode). Two equal snapshots, one before a run and one after, mean every
read in between saw the bytes the snapshot hashed, so a record made from
it describes what the outputs contain. The identity also catches an edit
that was undone meanwhile, unless its writer restored the mtime and the
inode as well.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .project import Project

# Word builds of one document whose inputs changed before giving up: the
# owner chose to fail and keep the earlier Word file rather than publish one
# that no record describes.
ATTEMPTS = 2

Snapshot = dict[Path, tuple[str, int, int, int] | None]

TARGET = re.compile(r"\s*(?:<([^>\n]+)>|([^\s)]+))")


MISSING_RESOURCE = re.compile(r"Could not fetch resource '?(.+?)'?(?::\s|$)", re.M)


def missing_resources(stderr: str) -> str | None:
    """The error for images Pandoc could not find (it only warns, and writes
    the image's description), or None."""
    missing = [urllib.parse.unquote(name) for name in MISSING_RESOURCE.findall(stderr)]
    if not missing:
        return None
    return (f"Pandoc could not find {', '.join(dict.fromkeys(missing))}: build it first "
            "(a [[build.steps]] entry with it in outputs) or fix the image path")


def image_targets(markdown: str) -> list[str]:
    """Local targets of Markdown images, `![alt](target "title")`; the alt
    text may hold brackets ([Title.]{.caption-title}) and nested images."""
    found: list[str] = []
    start = 0
    while (start := markdown.find("![", start)) != -1:
        depth, end = 0, start + 1
        while end < len(markdown):
            char = markdown[end]
            if char == "\\":
                end += 1
            elif char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
                if not depth:
                    break
            end += 1
        start += 2
        if depth or markdown[end + 1:end + 2] != "(":
            continue
        if (match := TARGET.match(markdown, end + 2)) and "://" not in (target := match[1] or match[2]):
            found.append(target)
    return found


def expand_includes(paths: list[Path]) -> list[Path]:
    """The sources and every file they `!include`, recursively (missing ones too)."""
    from .expand import include_lines, resolve_include
    found: list[Path] = []
    pending = list(paths)
    while pending:
        path = pending.pop(0)
        if path in found:
            continue
        found.append(path)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for _, target in include_lines(text):
            if target is not None:
                try:
                    pending.append(resolve_include(target, path))
                except FileNotFoundError:
                    pass
    return found


def recorded_inputs(paths: Iterable[Path]) -> list[str]:
    """The inputs a generated JSON file records ({"inputs": {path: sha256}})."""
    found: list[str] = []
    for path in paths:
        try:
            found += [name for name in json.loads(path.read_text(encoding="utf-8")).get("inputs", {})
                      if not Path(name).is_absolute()]
        except (OSError, ValueError, AttributeError):
            pass
    return found


def expand_inputs(project: Project, name: str, documents: Iterable[str] = ()) -> list[Path]:
    """What expanding document `name` reads, besides the labels of the
    others; with the sources of `documents` too."""
    sources = [path for doc in dict.fromkeys([name, *documents]) for path in project.document_files(doc)]
    return [*expand_includes(sources), project.path("values"), project.path("table_specs") / "_shared.json",
            project.path("metadata"), project.root / "trazo.toml"]


def watched(project: Project, name: str) -> list[Path]:
    """The project files a Word build of `name` reads before Pandoc: its
    expand inputs and every document's sources (the numbering reads the
    labels of the documents it cites; hashing all of them is cheaper than
    finding which, at the cost of a retry when another one changes)."""
    return expand_inputs(project, name, project.documents)


def snapshot(paths: Iterable[Path]) -> Snapshot:
    """Each file's sha256, mtime, size and inode; None when it cannot be read.
    The stat comes first, so a write during the read changes the next one."""
    found: Snapshot = {}
    for path in sorted(set(paths)):
        try:
            info = path.stat()
            found[path] = (hashlib.sha256(path.read_bytes()).hexdigest(), info.st_mtime_ns,
                           info.st_size, info.st_ino)
        except OSError:
            found[path] = None
    return found


def changes(before: Snapshot, paths: Iterable[Path] = (),
            written: Iterable[Path] = ()) -> tuple[Snapshot, list[Path]]:
    """A new snapshot of `before`'s files and `paths`, and the files of
    `before` that changed since, except `written` (the run's own outputs)."""
    after = snapshot([*before, *paths])
    skip = set(written)
    return after, [path for path, value in before.items() if path not in skip and after[path] != value]
