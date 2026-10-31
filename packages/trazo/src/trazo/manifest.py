"""`.trazo/manifest.json`: what the template gave a project.

`trazo new` writes it and `trazo update` reads and rewrites it. It records
the template version, the answers of the variant and, for each
template-owned file, the sha256 of its content as
the template made it, so an update can tell files the author changed from
files it may replace:

    {"format": 1,
     "template": "v1.0.0",
     "answers": {"example": true, "kind": "article",
                 "project_name": "my-paper", "title": "Paper title"},
     "files": {"scripts/trazo": "<sha256>", "README.md": "<sha256>", ...},
     "project": ["data/example.csv", "manuscript/metadata.yaml", ...]}

`project` lists the project-owned files the template created: the
author's from then on, never replaced (PROJECT_OWNED and copier.yml's
`_skip_if_exists`). Hashes are of the bytes on disk, except that text
(UTF-8 without NUL) counts `\r\n` as `\n`, so a checkout that converts line
ends changes nothing, and trazo.toml's `version = "..."` value is left
out: updates move it in place, so it does not count as an author's change. Hashes cover content only, not the
executable bit.

The skill copies in the template's skill_targets (`.agents/skills/`,
`.claude/skills/`) are in neither list: `trazo agents` owns them, and
`trazo update` regenerates them as it does.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .project import DEFAULTS
from .review_storage import write_atomic

PATH = Path(".trazo") / "manifest.json"
FORMAT = 1
ANSWERS = ("example", "kind", "project_name", "title")
# Content the author writes: folders (ending in /) and files.
PROJECT_OWNED = (
    "manuscript/", "data/", "notes/", "inputs/", "bibliography/references.bib",
    "figures/scripts/", "figures/diagrams/", "figures/external/",
    "tables/scripts/generate_tables.py", "tables/specs/",
    "scripts/build_values.py",
)
# Template-owned files inside a PROJECT_OWNED folder.
TEMPLATE_OWNED = ("figures/scripts/_style.py",)
# The template's skill_targets, where its skill copies are.
SKILL_COPIES = tuple(f"{target}/" for target in DEFAULTS["paths"]["skill_targets"])
VERSION_LINE = re.compile(rb'^version = "[^"\n]*"', re.M)


def project_owned(path: str, skip_if_exists: list[str]) -> bool:
    if path in TEMPLATE_OWNED:
        return False
    return path in skip_if_exists or any(
        path.startswith(owned) if owned.endswith("/") else path == owned for owned in PROJECT_OWNED)


def is_text(data: bytes) -> bool:
    if b"\0" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def file_hash(path: str, data: bytes) -> str:
    if is_text(data):
        data = data.replace(b"\r\n", b"\n")
    if path == "trazo.toml":
        data = VERSION_LINE.sub(b'version = ""', data, count=1)
    return hashlib.sha256(data).hexdigest()


def build(template: str, answers: dict, files: dict[str, bytes], skip_if_exists: list[str]) -> dict:
    """The manifest of a project made from `files` (path -> content),
    without the skill copies."""
    files = {path: data for path, data in files.items() if not path.startswith(SKILL_COPIES)}
    owned = {path: file_hash(path, data) for path, data in sorted(files.items())
             if not project_owned(path, skip_if_exists)}
    return {"format": FORMAT, "template": template,
            "answers": {key: answers[key] for key in ANSWERS},
            "files": owned,
            "project": sorted(path for path in files if path not in owned)}


def version_key(tag: str) -> tuple[int, ...]:
    """`vX.Y.Z` (or `vX.Y.Z-N-gHASH`, as `git describe` gives) as numbers."""
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:-(\d+)-g[0-9a-f]+)?", str(tag))
    if not match:
        raise ValueError(f"cannot read the template version {tag!r} (expected vX.Y.Z)")
    return tuple(int(part or 0) for part in match.groups())


def read(folder: Path) -> dict:
    path = Path(folder) / PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"{path} is missing: this project was not made by `trazo new`") from None
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("format") != FORMAT or not all(
            isinstance(data.get(key), kind) for key, kind in
            (("template", str), ("answers", dict), ("files", dict), ("project", list))) or not all(
            isinstance(value, str) for value in data["files"].values()):
        raise ValueError(f"{path} is not a trazo manifest (format {FORMAT})")
    return data


def write(folder: Path, manifest: dict) -> None:
    write_atomic(Path(folder) / PATH, (json.dumps(manifest, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))
