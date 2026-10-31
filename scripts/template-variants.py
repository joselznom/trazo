#!/usr/bin/env python3
"""Render the four template variants with Copier and store them in trazo.

    python3 scripts/template-variants.py          # rewrite the stored variants
    python3 scripts/template-variants.py --check  # fail if they are stale
    python3 scripts/template-variants.py --from REPO TAG OUT
        # a store of another commit, for tests (make test-update)

Each variant (kind article/report, example yes/no) is rendered from a
commit of this checkout with its uncommitted changes (template_snapshot.py)
with the placeholder answers of trazo.variants,
then stored as index.json plus one blob per distinct content (the layout
is described in packages/trazo/src/trazo/variants.py). Copier is
pinned (template_snapshot.COPIER): index.json names the version.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "packages/trazo/src"))

from trazo import variants  # noqa: E402
from template_snapshot import COPIER, snapshot  # noqa: E402

MAX_BLOB = 256 * 1024  # the repository's limit for a tracked file
# Where the template comes from, the URL copier.yml's header copies from.
SRC_PATH = "https://github.com/joselznom/trazo"


def render(source: Path, ref: str, kind: str, example: bool, target: Path) -> None:
    # project_name comes from the folder name, as for a user: passing it
    # with -d would make copier.yml's new_project false (no example
    # abbreviations). The placeholder is already in its derived form.
    assert variants.project_name_for(target) == variants.PLACEHOLDERS["project_name"]
    command = ["uvx", "--from", COPIER, "copier", "copy", "--defaults", "--quiet", f"--vcs-ref={ref}",
               "-d", f"kind={kind}", "-d", f"example={str(example).lower()}",
               "-d", f"title={variants.PLACEHOLDERS['title']}", str(source), str(target)]
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL)


def skip_if_exists(source: Path) -> list[str]:
    """copier.yml `_skip_if_exists`: a plain list of paths. trazo.manifest
    compares paths exactly, so quoted entries and globs are refused."""
    block = re.search(r"^_skip_if_exists:\n((?:\s+-.*\n|#.*\n)+)",
                      (source / "copier.yml").read_text(encoding="utf-8"), re.M)
    entries = re.findall(r"^\s+-\s*(.*?)\s*$", block[1], re.M)
    bad = [entry for entry in entries if not re.fullmatch(r"[A-Za-z0-9_./-]+", entry)]
    if bad:
        sys.exit(f"copier.yml _skip_if_exists: {', '.join(bad)}: only plain unquoted paths "
                 "(no quotes or globs); trazo.manifest compares paths exactly")
    return entries


def refuse_tasks(source: Path) -> None:
    """trazo new copies files only; tasks would make it differ from Copier."""
    found = re.findall(r"^(_tasks|_migrations):", (source / "copier.yml").read_text(encoding="utf-8"), re.M)
    if found:
        sys.exit(f"copier.yml has {', '.join(found)}: run them in trazo.new too, then lift this check")


def setting(text: str, key: str) -> str:
    return re.search(rf'^{key} = "([^"]*)"', text, re.M)[1]


def build(work: Path, source: Path | None = None, ref: str = "HEAD") -> tuple[dict, dict[str, bytes]]:
    """Render source at ref (default: a snapshot of this checkout). The
    template version is trazo.toml's, or ref when source is given."""
    if source is None:
        source = snapshot(work / "template")
    refuse_tasks(source)
    index = {"format": variants.FORMAT, "template": "", "src_path": SRC_PATH,
             "copier": COPIER.split("==")[1], "placeholders": variants.PLACEHOLDERS,
             "skip_if_exists": skip_if_exists(source), "variants": {}}
    blobs: dict[str, bytes] = {}
    for kind in variants.KINDS:
        for example in (True, False):
            name = variants.variant_name(kind, example)
            root = work / name / variants.PLACEHOLDERS["project_name"]
            render(source, ref, kind, example, root)
            toml = (root / "trazo.toml").read_text(encoding="utf-8")
            index["template"] = setting(toml, "version") if ref == "HEAD" else ref
            files, executable, dirs, filled = {}, [], [], {}
            for path in sorted(root.rglob("*")):
                rel = path.relative_to(root).as_posix()
                if path.is_symlink():
                    sys.exit(f"{name}: {rel} is a symlink; trazo stores regular files only")
                if path.is_dir():
                    if not any(path.iterdir()):
                        dirs.append(rel)
                    continue
                data = path.read_bytes()
                if len(data) > MAX_BLOB:
                    sys.exit(f"{name}: {rel} is over {MAX_BLOB // 1024} KiB")
                digest = hashlib.sha256(data).hexdigest()
                blobs[digest] = data
                files[rel] = digest
                if os.access(path, os.X_OK):
                    executable.append(rel)
                names = [n for n, value in variants.PLACEHOLDERS.items() if value.encode() in data]
                if names:
                    unknown = [n for n in names if n not in variants.FILLS.get(rel, {})]
                    if unknown:
                        sys.exit(f"{name}: {rel} uses {', '.join(unknown)}; add its escaping "
                                 "to trazo.variants.FILLS")
                    filled[rel] = names
            index["variants"][name] = {"kind": kind, "example": example, "files": files,
                                       "executable": executable, "dirs": dirs, "filled": filled}
    return index, blobs


def index_text(index: dict) -> str:
    return json.dumps(index, indent=1, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    check = argv == ["--check"]
    other = len(argv) == 4 and argv[0] == "--from"
    if argv and not (check or other):
        print(__doc__, file=sys.stderr)
        return 2
    store = Path(argv[3]).resolve() if other else variants.STORE
    texts: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="trazo-variants-") as folder:
        if other:
            index, blobs = build(Path(folder), Path(argv[1]).resolve(), argv[2])
        else:
            index, blobs = build(Path(folder) / "head")
    text = index_text(index)
    texts["index.json"] = text
    if check:
        stale = []
        for name, expected in sorted(texts.items()):
            current = store / name
            if not current.is_file() or current.read_text(encoding="utf-8") != expected:
                stale.append(name)
        stored = {p.name for p in (store / "blobs").glob("*")} if (store / "blobs").is_dir() else set()
        stale += sorted(f"blobs/{d}" for d in stored ^ set(blobs))
        stale += sorted(f"blobs/{d}" for d in set(blobs) & stored
                        if (store / "blobs" / d).read_bytes() != blobs[d])
        if stale:
            print("Stored template variants are stale; run `make template-variants`:",
                  *stale[:20], sep="\n  ", file=sys.stderr)
            return 1
        print(f"Template variants up to date ({len(blobs)} blobs, {index['template']})")
        return 0
    shutil.rmtree(store, ignore_errors=True)
    (store / "blobs").mkdir(parents=True)
    for digest, data in blobs.items():
        (store / "blobs" / digest).write_bytes(data)
    for name, content in texts.items():
        (store / name).parent.mkdir(parents=True, exist_ok=True)
        (store / name).write_text(content, encoding="utf-8")
    size = sum(map(len, blobs.values())) + sum(len(t.encode()) for t in texts.values())
    print(f"Wrote {store}: {len(blobs)} blobs, {size / 1024:.0f} KiB")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
