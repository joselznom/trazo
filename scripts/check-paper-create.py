#!/usr/bin/env python3
"""`trazo new` makes what `copier copy` makes: same files, bytes, modes and
empty folders, for the four variants,
titles that need escaping or wrapping, and umask 022 and 027. Run by `make test-template` with
trazo's Python (needs `uvx copier`); nothing here runs git.

    python scripts/check-paper-create.py
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from trazo import manifest, new, variants
from template_snapshot import COPIER, snapshot

HOSTILE = 'A "quoted" title: C:\\papers 😀'
DEFAULT_TITLE = "Paper title"  # copier.yml's, and `trazo new --title`'s
CASES = [
    ("article", True, None),
    ("article", False, HOSTILE),
    ("report", True, HOSTILE),
    ("report", False, None),
    ("article", True, "'Yes': a #long title, with " + "many words " * 12 + "& <tags> {x}"),
]
UMASKS = {1: 0o027}  # case number -> umask; the others run under 022


def tree(root: Path) -> dict[str, tuple]:
    """path -> (kind, mode, bytes) for every file and folder under root."""
    found = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        mode = stat.S_IMODE(path.lstat().st_mode)
        found[rel] = ("dir", mode, b"") if path.is_dir() else ("file", mode, path.read_bytes())
    return found


def compare(expected: Path, actual: Path) -> list[str]:
    want, got = tree(expected), tree(actual)
    problems = [f"missing {p}" for p in sorted(set(want) - set(got))]
    problems += [f"extra {p}" for p in sorted(set(got) - set(want))]
    for path in sorted(set(want) & set(got)):
        if want[path][:2] != got[path][:2]:
            problems.append(f"{path}: {want[path][:2]} != {got[path][:2]}")
        elif want[path][2] != got[path][2]:
            problems.append(f"{path}: content differs")
    return problems


def main() -> int:
    failures = 0
    with tempfile.TemporaryDirectory(prefix="trazo-new-") as folder:
        work = Path(folder)
        source = snapshot(work / "template")
        for number, (kind, example, title) in enumerate(CASES):
            umask = UMASKS.get(number, 0o022)
            # A folder name that exercises copier.yml's project_name rule.
            reference = work / f"copier-{number}" / "My Paper (Draft) 2"
            created = work / f"new-{number}" / "My Paper (Draft) 2"
            command = ["uvx", "--from", COPIER, "copier", "copy", "--defaults", "--quiet", "--vcs-ref=HEAD",
                       "-d", f"kind={kind}", "-d", f"example={str(example).lower()}"]
            if title is not None:
                command += ["-d", f"title={title}"]
            previous = os.umask(umask)  # this script runs no threads
            try:
                subprocess.run([*command, str(source), str(reference)], check=True, stdout=subprocess.DEVNULL)
                new.create(created, kind=kind, example=example, title=title or DEFAULT_TITLE)
            finally:
                os.umask(previous)
            assert manifest.read(created)["template"] == variants.Store().template
            (created / manifest.PATH).unlink()
            (created / manifest.PATH.parent).rmdir()
            problems = compare(reference, created)
            label = f"{variants.variant_name(kind, example)}, umask {umask:03o}, title {title or DEFAULT_TITLE!r}"
            if problems:
                failures += 1
                print(f"FAIL {label}:", *problems[:20], sep="\n  ", file=sys.stderr)
            else:
                print(f"trazo new == copier copy: {label} ({len(tree(created))} entries)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
