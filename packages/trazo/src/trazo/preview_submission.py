"""The preview's submission report, reused until something it reads changes.

The page asks for GET /__submission after every build and whenever it gets
focus, and a thesis-sized paper takes seconds to check. The checks are
kept, and the form fields once the Submission view asks for them (without
checking again), until one of these changes (files by mtime, size and
inode): a file of the project or of a folder [paths] names outside it
(directly or through a link), a file it read (sources, includes, images,
configured inputs, the Word files and the Word build's records; wherever
they are, as named and as resolved, so a link pointed elsewhere counts), a
document's build state or the configuration. Not followed: an include or
image missing when the report is made (the preview does not rebuild when
it appears outside the project either), and other files of a linked folder
that [paths] does not name (a large data folder is often one).

A missed input is caught by a fresh report (`fresh`, ?fresh= on the
endpoint): the page shows the kept one at once and asks for a fresh one
in the background when the Submission view opens, when "Check again" is
pressed, and once a minute while it is visible. So the list above is about
speed: a change it misses is shown seconds later, not at the next change.
Every report says when its check started (`checked`, seconds since the
epoch), so the page keeps the newest of the answers it gets.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import labels, submission_check
from .build_graph import STATE_NAME
from .docx_freshness import word_inputs

# Folders at the top of a walked folder the report never reads; the uv
# cache alone can hold thousands of files.
UNREAD = {".git", ".venv", ".cache", "node_modules"}
# (project state, files read, their stamps, checks, form fields or None);
# replaced whole, since it is read outside the lock.
_REPORT: tuple[Any, ...] | None = None
# One report at a time: a focus and a rebuild often ask together, and
# several tabs make their minute checks.
_LOCK = threading.Lock()


def stamp(path: str) -> tuple[int, int, int] | None:
    try:
        info = os.stat(path)
    except OSError:
        return None
    return info.st_mtime_ns, info.st_size, info.st_ino


def configured() -> list[Path]:
    """Every path in [paths]."""
    values = submission_check.PROJECT.config["paths"].values()
    return [submission_check.PROJECT.root / value for item in values
            for value in (item if isinstance(item, list) else [item])]


def tree_stamps(tops: list[str], skip: set[str]) -> dict[str, tuple[int, int, int] | None]:
    """path -> stamp of every file under `tops` (real paths; linked folders
    in them are not followed), but UNREAD at a top, __pycache__ and `skip`."""
    stamps, seen = {}, set()
    for top in tops:
        for folder, folders, files in os.walk(top):
            if folder in seen or folder in skip:  # a top inside another one, or left out
                folders[:] = []
                continue
            seen.add(folder)
            folders[:] = [name for name in folders if name != "__pycache__"
                          and not (folder == top and name in UNREAD) and os.path.join(folder, name) not in skip]
            for name in files:
                path = os.path.join(folder, name)
                if path not in skip:
                    stamps[path] = stamp(path)
    return stamps


def read_inputs() -> list[str]:
    """The files the report reads, found when it is made: a change that adds
    one changes a source, the configuration or an include already listed."""
    project = submission_check.PROJECT
    # Configured folders are walked; a folder's own time changes whenever a
    # file in it is replaced, as the report itself may do in the build folder.
    found: set[Path | None] = {path for path in configured() if not path.is_dir()}
    found.add(project.path("build") / STATE_NAME)  # what the Word build last built from
    for name in project.documents:
        found.update(word_inputs(project, name))
        found.add(project.document_docx(name))
    found.update(path for path, _, _ in submission_check.sources())
    named = {os.path.abspath(path) for path in found if path is not None}
    return sorted(named | {os.path.realpath(path) for path in named})


def report(fields: bool, counts: Callable[[], dict[str, Any]], states: Callable[[], str],
           root: Path, skip: tuple[Path, ...], fresh: float | None = None) -> dict[str, Any]:
    """submission_check.report(counts, fields) with when its check started
    ("checked"), its checks and form fields each reused while `states()` (the documents' build state), the
    configuration and the files it reads are unchanged. `fresh`: also make
    it again unless its check started at most that many seconds before this
    call (0: during it, as for a request that waited for a running check).
    `skip`: folders the preview writes itself (review comments, served pages
    that the build state covers)."""
    project = submission_check.PROJECT
    top = os.path.realpath(root)
    # Derived from the sources and configuration, and the report may rewrite
    # them: the label index and the Word build's numbering files.
    derived = [labels.cache_path(project), *(project.path("build") / f"{name}-numbering.json" for name in project.documents)]
    left_out = {os.path.realpath(path) for path in (*skip, *derived)}
    outside = [real for real in map(os.path.realpath, configured())
               if os.path.isdir(real) and not real.startswith(top + os.sep)]
    oldest = None if fresh is None else time.time() - fresh

    def kept() -> tuple[tuple[Any, ...] | None, float, tuple[Any, ...]]:
        """The kept entry if its checks still answer this call, and the
        project's state now with when it was taken."""
        cached, taken = _REPORT, time.time()
        state = (states(), json.dumps(project.config, sort_keys=True, default=str), tree_stamps([top, *outside], left_out))
        if (cached is None or (oldest is not None and cached[3]["checked"] < oldest)
                or cached[0] != state or cached[2] != [stamp(path) for path in cached[1]]):
            return None, taken, state
        return cached, taken, state

    def answer(cached: tuple[Any, ...]) -> dict[str, Any] | None:
        """The report of an entry, or None while its form fields are to make."""
        checks = cached[3]
        if not fields or "fields" in checks:  # a report has no form: its fields are already []
            return checks
        return None if cached[4] is None else {**checks, "fields": cached[4]}
    # Outside the lock, so a page that loads during a long check still gets
    # the kept report at once.
    cached = kept()[0]
    if cached is not None and (result := answer(cached)) is not None:
        return result
    global _REPORT
    with _LOCK:
        # Again after the wait: a check that ran meanwhile may answer it, and
        # a new one starts from the state now.
        cached, checked, state = kept()
        if cached is None:
            with labels.once():  # finding the inputs asks for the label index per document
                read = read_inputs()
            stamps = [stamp(path) for path in read]  # before the check: a change during it shows next time
        # One label index for the checks and fields, read after the stamps.
        with labels.once():
            if cached is None:
                checks = {**submission_check.report(counts), "checked": checked}
                cached = _REPORT = (state, read, stamps, checks, None)
            if (result := answer(cached)) is None:
                from .submission_fields import form_fields
                cached = _REPORT = (*cached[:4], form_fields())
                result = answer(cached)
        return result
