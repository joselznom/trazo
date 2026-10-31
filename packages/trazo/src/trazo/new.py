"""Create a paper from the template, without Copier, git or a network.

    trazo new FOLDER [--kind article|report] [--no-example]
                        [--title TITLE] [--project-name NAME] [--json]

The result is byte for byte what `copier copy` writes for the same answers
(the template variants ship in trazo; see trazo.variants), plus
`.trazo/manifest.json` for `trazo update`. FOLDER must be empty or missing and not inside another paper (a
folder with trazo.toml). The paper is written to a hidden sibling
folder and renamed into place, so a create that fails or is killed leaves
no partial paper. copier.yml has no `_tasks` (scripts/template-variants.py
refuses them), so there is nothing to run after the copy.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import sys
from pathlib import Path

from . import manifest, variants


def make_executable(path: Path) -> None:
    """Add all three x bits, as Copier 9.19 does for an executable file."""
    mode = path.stat().st_mode
    path.chmod(mode | 0o111)


def write_files(folder: Path, files: dict[str, bytes], executable: list[str], dirs: list[str]) -> None:
    """Write a variant's files and its empty folders."""
    for path in dirs:
        (folder / path).mkdir(parents=True, exist_ok=True)
    for path, data in files.items():
        target = folder / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        if path in executable:
            make_executable(target)


def create(folder, *, kind: str, example: bool, title: str, project_name: str | None = None,
           store: variants.Store | None = None) -> dict:
    """paper_create, with the store replaceable (tests use an older one)."""
    store = store or variants.Store()
    folder = Path(folder).expanduser().resolve()
    if not isinstance(example, bool):
        raise ValueError("example must be true or false")
    if project_name is None:
        project_name = variants.project_name_for(folder)
    variants.check_answers(title, project_name)
    variant = store.variant(kind, example)
    if folder.exists() and not folder.is_dir():
        raise ValueError(f"{folder} is a file; choose an empty or new folder")
    if folder.is_dir() and any(folder.iterdir()):
        raise ValueError(f"{folder} is not empty; choose an empty or new folder")
    inside = next((parent for parent in folder.parents if (parent / "trazo.toml").is_file()), None)
    if inside is not None:
        raise ValueError(f"{folder} is inside the paper {inside}; choose a folder outside it")
    values = {"title": title, "project_name": project_name}
    saved = {"example": example, "kind": kind, **values}
    files = store.render(kind, example, values)
    folder.parent.mkdir(parents=True, exist_ok=True)
    staging = folder.parent / f".{folder.name}.trazo-new-{secrets.token_hex(4)}"
    staging.mkdir()
    try:
        write_files(staging, files, variant["executable"], variant["dirs"])
        manifest.write(staging, manifest.build(store.template, saved, files, store.index["skip_if_exists"]))
        os.replace(staging, folder)  # an empty folder is replaced
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {"folder": str(folder), "template": store.template, "kind": kind, "example": example,
            "title": title, "project_name": project_name, "files": len(files)}


def paper_create(folder, *, kind: str, example: bool, title: str, project_name: str | None = None) -> dict:
    """Create a paper in `folder` (empty or missing) from the template
    variant for `kind` ("article" or "report") and `example`. project_name
    defaults to the folder name as copier.yml derives it. Returns what was
    made; raises ValueError with a readable message for bad input."""
    return create(folder, kind=kind, example=example, title=title, project_name=project_name)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="trazo new", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder")
    parser.add_argument("--kind", choices=variants.KINDS, default="article")
    parser.add_argument("--no-example", dest="example", action="store_false",
                        help="empty sections instead of the example paper")
    parser.add_argument("--title", default="Paper title")
    parser.add_argument("--project-name", help="default: from the folder name")
    parser.add_argument("--json", action="store_true", help="print what was made as JSON")
    args = parser.parse_args(argv)
    try:
        made = paper_create(args.folder, kind=args.kind, example=args.example, title=args.title,
                            project_name=args.project_name)
    except ValueError as exc:
        print(f"trazo new: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(made, ensure_ascii=False))
        return 0
    print(f"Created {made['folder']}: {made['kind']}, {'example' if made['example'] else 'blank'}, "
          f"template {made['template']}, {made['files']} files")
    return 0
