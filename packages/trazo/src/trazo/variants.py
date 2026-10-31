"""The template variants trazo ships, rendered by Copier at release.

`make template-variants` (scripts/template-variants.py) renders the four
variants of template/ (kind article or report, with or without the
example) with placeholder answers and stores them here:

    assets/template/index.json      what each variant holds
    assets/template/blobs/<sha256>  each distinct file content, once

index.json:

    {"format": 1,
     "template": "v0.7.0",        # the template tag
     "src_path": "https://github.com/joselznom/trazo",  # where it comes from
     "copier": "9.19.1",          # the Copier that rendered them
     "placeholders": {"title": "...", "project_name": "..."},
     "skip_if_exists": [...],     # copier.yml _skip_if_exists
     "variants": {"article-example": {
         "kind": "article", "example": true,
         "files": {"README.md": "<sha256 of the stored content>", ...},
         "executable": ["scripts/trazo", ...],
         "dirs": ["data", ...],   # folders Copier creates empty
         "filled": {"README.md": ["title"], ...}}, ...}}

Placeholders are letters and digits only, so every Jinja filter the template
applies to an answer leaves them unchanged and filling one is a plain
replacement by the escaped answer. FILLS says how each file escapes each
answer, as its `.jinja` source does; the generator refuses a placeholder
in a file or for an answer not listed here, so a new use in the template
must be added to FILLS first.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

STORE = Path(__file__).resolve().parent / "assets" / "template"
FORMAT = 1
KINDS = ("article", "report")
PLACEHOLDERS = {"title": "PKTITLEqz7Wx3Vb", "project_name": "pkprojectqz7wx3vb"}
# Line breaks to YAML 1.1 (metadata.yaml), though printable to Python.
LINE_SEPARATORS = "\u2028\u2029"


def _as_is(value: str) -> str:
    return value


def _json(value: str) -> str:
    # `to_json(ensure_ascii=False)` is json.dumps; the stored file keeps its quotes.
    return json.dumps(value, ensure_ascii=False)[1:-1]


def _yaml_json(value: str) -> str:
    # `replace('\\', '\\\\') | to_json(ensure_ascii=False)`
    return _json(value.replace("\\", "\\\\"))


def _quoted(value: str) -> str:
    # `replace('\\', '\\\\') | replace('"', '\\"')` inside "...".
    return value.replace("\\", "\\\\").replace('"', '\\"')


FILLS = {
    "README.md": {"title": _as_is},                       # # {{ title }}
    "pyproject.toml": {"project_name": _as_is,            # name = "{{ project_name }}"
                       "title": _json},                   # description = {{ title | to_json }}
    "manuscript/metadata.yaml": {"title": _yaml_json},    # title:, short-title:
    "manuscript/cover-letter/cover_letter.md": {"title": _quoted},
}


def variant_name(kind: str, example: bool) -> str:
    return f"{kind}-{'example' if example else 'blank'}"


def project_name_for(folder: Path) -> str:
    """copier.yml's default: `_folder_name | lower | regex_replace('[^a-z0-9]+',
    '-') | trim('-') or 'my-paper'`."""
    return re.sub(r"[^a-z0-9]+", "-", Path(folder).name.lower()).strip("-") or "my-paper"


@dataclass
class Store:
    """The stored variants (STORE, or another folder in tests)."""
    root: Path = STORE
    index: dict = field(init=False)

    def __post_init__(self):
        self.root = Path(self.root)
        try:
            self.index = json.loads((self.root / "index.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"trazo's template variants are missing or damaged "
                               f"({self.root / 'index.json'}): {exc}") from exc
        if self.index.get("format") != FORMAT:
            raise RuntimeError(f"{self.root / 'index.json'} has format {self.index.get('format')}, "
                               f"this trazo reads {FORMAT}")

    @property
    def template(self) -> str:
        return self.index["template"]

    def variant(self, kind: str, example: bool) -> dict:
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}, not {kind!r}")
        return self.index["variants"][variant_name(kind, bool(example))]

    def render(self, kind: str, example: bool, values: dict[str, str]) -> dict[str, bytes]:
        """path -> content of a variant with its placeholders filled from
        values (title, project_name)."""
        variant = self.variant(kind, example)
        files = {}
        for path, digest in variant["files"].items():
            data = (self.root / "blobs" / digest).read_bytes()
            if path in variant["filled"]:
                data = fill(path, data.decode("utf-8"), values).encode("utf-8")
            files[path] = data
        return files


def fill(path: str, text: str, values: dict[str, str]) -> str:
    """Replace the placeholders in one file in a single pass, so an answer
    that happens to contain a placeholder is left as it is."""
    rules = FILLS[path]
    lookup = {PLACEHOLDERS[name]: rules[name](value) for name, value in values.items() if name in rules}
    pattern = "|".join(map(re.escape, lookup))
    return re.sub(pattern, lambda m: lookup[m[0]], text)


def check_answers(title: str, project_name: str) -> None:
    """Values a project may hold: one line of printable text each, as the
    files FILLS writes them into need; anything else is refused with a
    readable message."""
    for name, value in (("title", title), ("project_name", project_name)):
        if not isinstance(value, str):
            raise ValueError(f"{name} must be text")
        for ch in value:
            printable = " " <= ch <= "~" or (("\xa0" <= ch <= "\ud7ff" or "\ue000" <= ch <= "\ufffd"
                                              or "\U00010000" <= ch < "\U0010ffff") and ch != "\ufeff")
            if not printable or ch in LINE_SEPARATORS:
                raise ValueError(f"{name} must be one line of printable text; it contains {ch!r}")
