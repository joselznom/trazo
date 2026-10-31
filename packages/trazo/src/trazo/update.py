"""Update a paper made by `trazo new` to the template this trazo ships.

    trazo update [--json] [FOLDER]

The template comes with trazo, so this updates to the release of the
trazo that runs it: the plugin's, which `scripts/trazo` runs too. A
trazo at the project's own template reports "already at"; updating the
plugin (then its setup) brings a newer one. An older trazo is refused.

No Copier, git or network: `.trazo/manifest.json` says what the
template gave the project, and for each template-owned file:

- the author did not change it (its hash is the manifest's): replaced
  with the new template's content;
- the author changed it and the template did not: left as it is;
- both changed it: the new content is written to `<file>.new` for the
  agent to merge into `<file>` (then delete the `.new` file);
- new in the template: added, unless a file of the author's is already
  there (then `<file>.new`); deleted by the author: not brought back;
- gone from the template: removed if unchanged, else kept.

Project-owned files (manifest.PROJECT_OWNED, `_skip_if_exists`) are never
touched; a project-owned file new in the template is added when missing.
trazo.toml's `version` moves to the new release in place even when
the author changed the file. The template's skill copies
(manifest.SKILL_COPIES) are never the manifest's: once the files are done,
the update regenerates the copies in the paper's
[paths] skill_targets as `trazo agents` does, from this trazo's
skills and the paper's own, and removes marked copies of a skill neither
has. In a template target the paper dropped, it removes only those and
leaves the rest, with a note. When the paper's trazo.toml or skills
folder does not load, the copies are left as they are, with a note. An unmerged
`<file>.new` left by an earlier update is kept when it already holds the
new content, else replaced; both are reported. Every write goes through a
temporary file. Only content is compared: a file whose executable bit
alone changed counts as unchanged.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import agents, manifest, variants
from .new import make_executable
from .project import CONFIG_NAME, DEFAULTS, ConfigError, Project, merge
from .review_storage import write_atomic

NEW_SUFFIX = ".new"


@dataclass
class UpdateReport:
    folder: str
    template_from: str
    template_to: str
    replaced: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    needs_merge: list[str] = field(default_factory=list)   # <path>.new written beside each
    skipped: list[dict] = field(default_factory=list)      # {"path": ..., "reason": ...}
    notes: list[str] = field(default_factory=list)
    up_to_date: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


def _set_version(path: Path, new_content: bytes) -> bool:
    """Give trazo.toml the new template's `version` line; True if changed."""
    match = manifest.VERSION_LINE.search(new_content)
    data = path.read_bytes()
    if not match or not manifest.VERSION_LINE.search(data):
        return False
    moved = manifest.VERSION_LINE.sub(lambda _: match[0], data, count=1)
    if moved == data:
        return False
    write_atomic(path, moved)
    return True


def _write(target: Path, data: bytes, executable: bool) -> None:
    write_atomic(target, data)
    if executable:
        make_executable(target)


def _write_new(folder: Path, path: str, data: bytes, executable: bool, report: UpdateReport) -> None:
    """Write `<path>.new` for the agent to merge; report an unmerged one."""
    target = folder / (path + NEW_SUFFIX)
    report.needs_merge.append(path)
    if target.is_file():
        if target.read_bytes() == data:
            report.notes.append(f"{path}{NEW_SUFFIX} from an earlier update is still unmerged")
            return
        report.notes.append(f"{path}{NEW_SUFFIX} from an earlier update was unmerged; replaced it")
    _write(target, data, executable)


def _paper(folder: Path) -> Project | None:
    """The paper's Project from its own trazo.toml (not $TRAZO_ROOT's,
    which Project.load prefers); None when it does not load."""
    try:
        settings = tomllib.loads((folder / CONFIG_NAME).read_text(encoding="utf-8"))
        return Project(folder, merge(DEFAULTS, settings), True)
    except (OSError, ValueError, ConfigError):
        return None


def _skill_copies(project: Project | None, folder: Path, report: UpdateReport) -> None:
    """Regenerate the skill copies as `trazo agents` does, for the
    paper's [paths] and with this trazo's skills: a hand edit to a copy
    is replaced, and a marked copy of a skill neither has is removed, in
    the template's targets the paper dropped too."""
    try:
        if project is None:
            raise agents.SkillError("trazo.toml does not load")
        skills = agents.canonical_skills(project)
    except agents.SkillError as exc:
        report.notes.append(f"the skill copies were left as they are ({exc}): fix it, then run "
                            "`scripts/trazo agents`")
        return
    if agents.check(skills, project):
        report.removed += [Path(os.path.relpath(copy, folder)).as_posix() for copy in agents.sync(skills, project)]
        targets = ", ".join(project.config["paths"]["skill_targets"])
        report.notes.append(f"regenerated the skill copies in {targets}, as `trazo agents` does")
    for dropped in agents.dropped_targets(project):
        if dropped.is_dir() and any(agents.is_managed(path) for path in dropped.iterdir()):
            report.notes.append(f"{os.path.relpath(dropped, folder)} is not among [paths] skill_targets, so its "
                                "skill copies were not updated; delete it if no agent reads it")


def _check_versions(folder: Path, old: dict, store: variants.Store) -> bool:
    """True when the project is already at this trazo's template; raise
    on a downgrade."""
    have, ship = manifest.version_key(old["template"]), manifest.version_key(store.template)
    if ship < have:
        raise ValueError(f"{folder} is at template {old['template']}, newer than this trazo's "
                         f"{store.template}; run a trazo of {old['template']} or later")
    return ship == have


def update(folder: str | Path, store: variants.Store | None = None) -> UpdateReport:
    """paper_update, with the store replaceable (tests use an older one)."""
    store = store or variants.Store()
    folder = Path(folder).expanduser().resolve()
    old = manifest.read(folder)
    saved = old["answers"]
    try:
        variant = store.variant(saved["kind"], saved["example"])
        files = store.render(saved["kind"], saved["example"],
                             {"title": saved["title"], "project_name": saved["project_name"]})
    except (KeyError, TypeError) as exc:
        raise ValueError(f"{folder / manifest.PATH} has incomplete answers: {exc}") from exc
    report = UpdateReport(str(folder), old["template"], store.template)
    if _check_versions(folder, old, store):
        report.up_to_date = True
        report.notes.append(
            f"already at template {store.template}; to update, run this with a newer trazo "
            "(update the plugin, then run its setup)")
        return report
    skip = store.index["skip_if_exists"]
    built = manifest.build(store.template, saved, files, skip)
    known = set(old["files"]) | set(old["project"])
    for path in variant["dirs"]:
        (folder / path).mkdir(parents=True, exist_ok=True)
    for path, data in files.items():
        if path.startswith(manifest.SKILL_COPIES):
            continue  # _skill_copies
        target = folder / path
        executable = path in variant["executable"]
        new_hash = built["files"].get(path)
        old_hash = old["files"].get(path)
        if not target.exists():
            if path not in known:
                _write(target, data, executable)
                report.added.append(path)
            elif new_hash is not None:
                report.skipped.append({"path": path, "reason": "deleted in the project; not restored"})
            continue
        if new_hash is None:
            continue  # project-owned: the author's
        current = manifest.file_hash(path, target.read_bytes())
        if current == new_hash:
            if path == "trazo.toml" and _set_version(target, data):
                report.replaced.append(path)
        elif old_hash is not None and current == old_hash:
            _write(target, data, executable)
            report.replaced.append(path)
        elif old_hash is not None and new_hash == old_hash:
            if path == "trazo.toml":
                _set_version(target, data)
            report.skipped.append({"path": path, "reason": "changed in the project; the template did not change it"})
        else:
            if path == "trazo.toml":
                _set_version(target, data)
            _write_new(folder, path, data, executable, report)

    for path, old_hash in old["files"].items():
        target = folder / path
        if path in files or path.startswith(manifest.SKILL_COPIES) or not target.is_file():
            continue
        if manifest.file_hash(path, target.read_bytes()) == old_hash:
            target.unlink()
            report.removed.append(path)
        else:
            report.skipped.append({"path": path, "reason": "removed from the template; changed in the project, kept"})

    project = _paper(folder)
    _skill_copies(project, folder, report)
    manifest.write(folder, built)
    return report


def paper_update(folder: str | Path) -> dict:
    """Update the paper in `folder` to this trazo's template. Returns the
    report as a dict: replaced, added, removed, needs_merge (a `<file>.new`
    beside each, to merge into the file and then delete), skipped
    ({path, reason}) and notes. Raises ValueError with a readable message
    when the folder has no usable manifest or this trazo's template is
    older than the project's."""
    return update(folder).as_dict()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trazo update", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", nargs="?", help="default: this project")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)
    if args.folder is None:
        start = Path(os.environ.get("TRAZO_ROOT") or Path.cwd()).expanduser().resolve()
        args.folder = next((folder for folder in (start, *start.parents)
                            if (folder / manifest.PATH).is_file()), start)
    try:
        report = update(args.folder)
    except ValueError as exc:
        print(f"trazo update: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report.as_dict(), indent=2, ensure_ascii=False))
        return 0
    if report.up_to_date:
        print(f"{report.folder}: {report.notes[0]}")
        return 0
    print(f"Updated {report.folder}: template {report.template_from} -> {report.template_to}")
    for note in report.notes:
        print(f"  note: {note}")
    for name in ("replaced", "added", "removed"):
        for path in getattr(report, name):
            print(f"  {name:<11} {path}")
    for entry in report.skipped:
        print(f"  {'kept':<11} {entry['path']} ({entry['reason']})")
    for path in report.needs_merge:
        print(f"  {'merge':<11} {path}{NEW_SUFFIX} -> {path}")
    if report.needs_merge:
        print("Merge each .new file into its file, keeping the project's changes, then delete it.")
    return 0
