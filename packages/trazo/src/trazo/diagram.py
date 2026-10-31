"""Create, check, render and export declarative diagrams (figures/diagrams/*.json).

The JSON source (schema_version 1, millimetres at print size, style tokens
from figures/style/tokens.json) is rendered by trazo's assets/diagram/
renderer.js in headless Chrome (TRAZO_CHROME or [diagrams] chrome in
trazo.toml override where Chrome is found). One Chrome run renders, measures the text
for lint and writes the exports; lint never estimates text widths in Python.

    trazo diagram new NAME [--width 183|120|89] [--height MM]
    trazo diagram check NAME|--all [--json] [--exports]
    trazo diagram render NAME [--png PATH] [--dpi N]
    trazo diagram export NAME|--all [--force]
    trazo diagram describe NAME
    trazo diagram patch NAME OPS.json        (OPS.json may be "-" for stdin)
    trazo diagram comments [NAME] [--all]
    trazo diagram resolve ID "reply"
    trazo diagram undo

NAME is a file in figures/diagrams/ (without .json) or, for check, render,
describe and patch, a path to a .json file. A node with "panel" has x/y
relative to the panel's top-left corner and must stay inside its content
area (below the label header). Groups may nest. Edge ends are anchors
`id.left|right|top|bottom|center`, optionally `@fraction` along the side
(0 = left or top end, default 0.5). Panels, nodes, edges and raw may set
optional `locked` and `hidden` booleans. Locked elements stay in the figure;
the editor will not move, resize or edit them (a locked panel or group locks
its contents). Hidden elements are left out of the drawing, and so are the
contents of a hidden group or panel and any edge that ends on a hidden
element. `check` accepts both flags. Text is a string or a list of blocks
{"style", "t" | "runs", "space_before_pt", "align"}; runs may set bold,
italic, sub, sup and color; "\\n" breaks a line. `export` writes
figures/external/NAME.{svg,pdf,png} (PNG at 600 dpi). Each export carries a
stamp: the sha256 of the JSON (its canonical form, so formatting alone
does not count), tokens.json, renderer.js, the {{v:key}}
values used (named formats, {{v:key|format}}, are an error in diagrams)
and every embedded file; `check --exports` fails when an export
is missing or its stamp differs. `check` exits non-zero on any error;
`export` refuses to write when `check` has errors unless --force is given.
A diagram with ``meta.draft`` true is still linted, but ``check --exports``
warns instead of failing and ``export`` skips it unless ``--force``. Use
that while the manuscript still points at a hand-authored figure of the
same name.

Patch operations (a JSON list, applied all-or-nothing, then validated):
    {"op": "set", "id": ID, "props": {"fill": "signal", "text.1.t": "New"}}
        dotted keys reach into lists and objects; null deletes the key;
        the id "@canvas" targets the canvas
    {"op": "add", "into": "nodes|edges|panels|raw", "item": {...}, "before": ID}
    {"op": "remove", "id": ID, "cascade": true}
        cascade also removes edges attached to ID and drops it from groups
    {"op": "move", "id": ID, "dx": 2, "dy": -1}  or  {"op": "move", "id": ID, "x": 10, "y": 4}

`new`, `patch` and `resolve` record history in notes/review/
diagram_history.json; `undo` reverts the last entry and refuses if the file
or comment changed since. Comments (made in the editor) live in
notes/review/diagram_comments.json with the preview states: open (author),
review (the agent replied), done (only the author marks it). Set
PREVIEW_REVIEW_DIR to use another review folder.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import re
import struct
import sys
import tempfile
import time
import zlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .browser import CHROME, CHROME_TIMEOUT, BrowserError, Chrome  # noqa: F401  # public re-exports
from .build_lock import exclusive
from .dependencies import render_revision
from .expand import PLACEHOLDER, ValuesError, load_values
from .project import ASSETS_DIR, PROJECT
from .resources import local_file
from .review_comments import CommentStore
from .review_history import History
from .review_storage import REVIEW_DIR, StoreError, review_lock, write_atomic
from .svg_export import DPI, FONT_DIR

ROOT = PROJECT.root
DIAGRAM_DIR = PROJECT.path("diagrams")
EXTERNAL_DIR = PROJECT.path("diagram_output")
TOKENS_PATH = PROJECT.path("figure_tokens")
RENDERER_PATH = ASSETS_DIR / "diagram" / "renderer.js"
RENDER_DIR = PROJECT.path("build") / "diagrams"
COMMENTS_PATH = REVIEW_DIR / "diagram_comments.json"
HISTORY_PATH = REVIEW_DIR / "diagram_history.json"
STAMP_KEY = "diagram-source-sha256"
RENDER_STAMP_KEY = "diagram-render-sha256"
RENDER_DPI = 300
TOLERANCE_MM = 0.05

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
ANCHOR_RE = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9_-]*)\.(left|right|top|bottom|center)(?:@([0-9]*\.?[0-9]+))?$")
EMBED_RE = re.compile(r"^" + re.escape(PROJECT.relative("figure_embeds").rstrip("/")) + r"/[A-Za-z0-9_.-]+\.svg$")
IMAGE_RE = re.compile(r"^" + re.escape(PROJECT.relative("figures").rstrip("/")) + r"/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.(?:png|jpe?g|svg)$")

TOP_KEYS = {"schema_version", "canvas", "panels", "nodes", "edges", "raw", "meta"}
CANVAS_KEYS = {"width_mm", "height_mm", "grid"}
FLAGS = {"locked", "hidden"}
PANEL_KEYS = {"id", "x", "y", "w", "h", "label", "title", "embed", "fit"} | FLAGS
NODE_COMMON = {"id", "kind", "panel"} | FLAGS
NODE_KEYS = {
    "box": {"x", "y", "w", "h", "text", "fill", "stroke", "padding", "align", "valign", "radius"},
    "pill": {"x", "y", "w", "h", "text", "fill", "stroke", "padding", "align", "valign"},
    "text": {"x", "y", "w", "h", "text", "fill", "stroke", "padding", "align", "valign"},
    "image": {"x", "y", "w", "h", "src", "fit"},
    "group": {"children"},
}
EDGE_KEYS = {"id", "from", "to", "route", "waypoints", "stroke", "label", "gap"} | FLAGS
RAW_KEYS = {"id", "svg", "note"} | FLAGS
BLOCK_KEYS = {"style", "t", "runs", "space_before_pt", "align"}
RUN_KEYS = {"t", "bold", "italic", "sub", "sup", "color"}
COLLECTIONS = ("panels", "nodes", "edges", "raw")
MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml"}

Issue = dict[str, Any]


DiagramError = BrowserError  # compatibility for CLI and callers of the original transport


def issue(level: str, code: str, element: str | None, message: str) -> Issue:
    return {"level": level, "code": code, "id": element, "message": message}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------- files


def diagram_path(name: str, allow_path: bool = True) -> Path:
    """Resolve NAME to figures/diagrams/NAME.json, or accept a .json path."""
    if name.endswith(".json"):
        if not allow_path:
            raise DiagramError(f"expected a diagram name in {rel(DIAGRAM_DIR)}, not a path")
        return Path(name).resolve()
    if not NAME_RE.match(name):
        raise DiagramError(f"invalid diagram name {name!r} (use lowercase letters, digits, - and _)")
    return DIAGRAM_DIR / f"{name}.json"


def all_diagrams() -> list[Path]:
    return sorted(DIAGRAM_DIR.glob("*.json"))


def read_diagram(path: Path) -> tuple[dict[str, Any], bytes]:
    if not path.exists():
        raise DiagramError(f"diagram not found: {rel(path)}")
    data = path.read_bytes()
    try:
        diagram = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise DiagramError(f"{rel(path)} is not valid JSON: {exc}") from exc
    if not isinstance(diagram, dict):
        raise DiagramError(f"{rel(path)}: the top level must be a JSON object")
    return diagram, data


def dump_diagram(diagram: dict[str, Any]) -> bytes:
    return (json.dumps(diagram, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def load_tokens() -> dict[str, Any]:
    return json.loads(TOKENS_PATH.read_text(encoding="utf-8"))


def color_names(tokens: dict[str, Any]) -> set[str]:
    names = set(tokens["roles"])
    for group in ("contenders", "datasets", "outcomes"):
        names.update(tokens.get(group, {}))
    return names


# ---------------------------------------------------- validation


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class Validator:
    """Schema checks for schema_version 1; collects every problem found."""

    def __init__(self, diagram: dict[str, Any], tokens: dict[str, Any]) -> None:
        self.diagram = diagram
        self.colors = color_names(tokens)
        self.strokes = set(tokens["strokes"])
        self.styles = set(tokens["text_styles"])
        self.issues: list[Issue] = []

    def error(self, element: str | None, message: str, code: str = "schema") -> None:
        self.issues.append(issue("error", code, element, message))

    def keys(self, obj: dict[str, Any], allowed: set[str], where: str, element: str | None) -> None:
        for key in obj:
            if key not in allowed:
                self.error(element, f"{where}: unknown property {key!r}")

    def number(self, obj: dict[str, Any], key: str, where: str, element: str | None,
               required: bool = True, positive: bool = False, minimum: float | None = None) -> None:
        if key not in obj:
            if required:
                self.error(element, f"{where}: {key!r} is required")
            return
        value = obj[key]
        if not is_number(value):
            self.error(element, f"{where}: {key!r} must be a number")
        elif positive and value <= 0:
            self.error(element, f"{where}: {key!r} must be greater than 0")
        elif minimum is not None and value < minimum:
            self.error(element, f"{where}: {key!r} must be at least {minimum}")

    def choice(self, obj: dict[str, Any], key: str, options: tuple[str, ...], where: str,
               element: str | None) -> None:
        if key in obj and obj[key] not in options:
            self.error(element, f"{where}: {key!r} must be one of {', '.join(options)}")

    def token(self, value: Any, kind: str, where: str, element: str | None) -> None:
        known = {"colour": self.colors, "stroke": self.strokes, "text style": self.styles}[kind]
        if not isinstance(value, str) or value not in known:
            self.error(element, f"{where}: unknown {kind} token {value!r}", code="unknown-token")

    def text(self, value: Any, where: str, element: str | None) -> None:
        if isinstance(value, str):
            return
        if not isinstance(value, list):
            self.error(element, f"{where}: text must be a string or a list of blocks")
            return
        for i, block in enumerate(value):
            at = f"{where}[{i}]"
            if not isinstance(block, dict):
                self.error(element, f"{at}: a text block must be an object")
                continue
            self.keys(block, BLOCK_KEYS, at, element)
            if "style" in block:
                self.token(block["style"], "text style", at, element)
            if ("t" in block) == ("runs" in block):
                self.error(element, f"{at}: give exactly one of 't' or 'runs'")
            if "t" in block and not isinstance(block["t"], str):
                self.error(element, f"{at}: 't' must be a string")
            self.number(block, "space_before_pt", at, element, required=False, minimum=0)
            self.choice(block, "align", ("left", "center", "right"), at, element)
            runs = block.get("runs", [])
            if not isinstance(runs, list):
                self.error(element, f"{at}: 'runs' must be a list")
                continue
            for j, run in enumerate(runs):
                rat = f"{at}.runs[{j}]"
                if not isinstance(run, dict):
                    self.error(element, f"{rat}: a run must be an object")
                    continue
                self.keys(run, RUN_KEYS, rat, element)
                if not isinstance(run.get("t"), str):
                    self.error(element, f"{rat}: 't' (string) is required")
                for flag in ("bold", "italic", "sub", "sup"):
                    if flag in run and not isinstance(run[flag], bool):
                        self.error(element, f"{rat}: {flag!r} must be true or false")
                if run.get("sub") and run.get("sup"):
                    self.error(element, f"{rat}: a run cannot be both sub and sup")
                if "color" in run:
                    self.token(run["color"], "colour", rat, element)

    def flags(self, obj: dict[str, Any], where: str, element: str | None) -> None:
        for key in ("locked", "hidden"):
            if key in obj and not isinstance(obj[key], bool):
                self.error(element, f"{where}: {key!r} must be true or false")

    def padding(self, node: dict[str, Any], where: str, element: str) -> None:
        if "padding" not in node:
            return
        value = node["padding"]
        values = value if isinstance(value, list) else [value]
        if (isinstance(value, list) and len(value) != 4) or not all(
                is_number(v) and v >= 0 for v in values):
            self.error(element, f"{where}: 'padding' must be a number >= 0 or a list of 4")

    def groups(self, nodes: dict[str, dict[str, Any]]) -> None:
        """A node belongs to at most one group, and groups may nest but not cycle."""
        parent: dict[str, str] = {}
        for nid, node in nodes.items():
            if node.get("kind") != "group" or not isinstance(node.get("children"), list):
                continue
            for child in node["children"]:
                if not isinstance(child, str) or child not in nodes or child == nid:
                    continue
                if child in parent and parent[child] != nid:
                    self.error(nid, f"node {child!r} is in more than one group "
                                    f"({parent[child]!r} and {nid!r})")
                else:
                    parent[child] = nid
        reported: set[str] = set()
        for start in parent:
            seen = [start]
            current = start
            while current in parent:
                current = parent[current]
                if current in seen:
                    cycle = seen[seen.index(current):]
                    if not reported & set(cycle):
                        self.error(current, f"groups form a cycle: {' -> '.join(cycle + [current])}")
                        reported.update(cycle)
                    break
                seen.append(current)

    def run(self) -> list[Issue]:
        d = self.diagram
        self.keys(d, TOP_KEYS, "diagram", None)
        if d.get("schema_version") != 1:
            self.error(None, "schema_version must be 1")
        canvas = d.get("canvas")
        if not isinstance(canvas, dict):
            self.error(None, "canvas (object) is required")
        else:
            self.keys(canvas, CANVAS_KEYS, "canvas", None)
            self.number(canvas, "width_mm", "canvas", None, positive=True)
            self.number(canvas, "height_mm", "canvas", None, positive=True)
            self.number(canvas, "grid", "canvas", None, required=False, positive=True)
        if "meta" in d and not isinstance(d["meta"], dict):
            self.error(None, "meta must be an object")
        ids: dict[str, str] = {}
        for collection in COLLECTIONS:
            items = d.get(collection, [])
            if not isinstance(items, list):
                self.error(None, f"{collection} must be a list")
                d[collection] = items = []
            for index, item in enumerate(items):
                where = f"{collection}[{index}]"
                if not isinstance(item, dict):
                    self.error(None, f"{where} must be an object")
                    continue
                element = item.get("id")
                if not isinstance(element, str) or not ID_RE.match(element):
                    self.error(None, f"{where}: 'id' must match {ID_RE.pattern} (got {element!r})")
                    continue
                if element in ids:
                    self.error(element, f"duplicate id {element!r} ({ids[element]} and {collection})")
                ids[element] = collection
        valid = lambda item: (isinstance(item, dict) and isinstance(item.get("id"), str)
                              and item["id"] in ids)  # noqa: E731
        panels = {p["id"] for p in d.get("panels", []) if valid(p)}
        nodes = {n["id"]: n for n in d.get("nodes", []) if valid(n)}
        for panel in d.get("panels", []):
            if not valid(panel):
                continue
            pid = panel["id"]
            where = f"panel {pid!r}"
            self.keys(panel, PANEL_KEYS, where, pid)
            self.flags(panel, where, pid)
            for key in ("x", "y"):
                self.number(panel, key, where, pid)
            for key in ("w", "h"):
                self.number(panel, key, where, pid, positive=True)
            for key in ("label", "title"):
                if key in panel and not isinstance(panel[key], str):
                    self.error(pid, f"{where}: {key!r} must be a string")
            if "embed" in panel and not (isinstance(panel["embed"], str)
                                         and EMBED_RE.match(panel["embed"])):
                self.error(pid, f"{where}: 'embed' must be figures/output/svg/<stem>.svg")
            self.choice(panel, "fit", ("contain", "none"), where, pid)
        for node in d.get("nodes", []):
            if not valid(node):
                continue
            nid = node["id"]
            kind = node.get("kind")
            where = f"node {nid!r}"
            if not isinstance(kind, str) or kind not in NODE_KEYS:
                self.error(nid, f"{where}: 'kind' must be one of {', '.join(NODE_KEYS)}")
                continue
            self.keys(node, NODE_COMMON | NODE_KEYS[kind], where, nid)
            self.flags(node, where, nid)
            if "panel" in node and (not isinstance(node["panel"], str) or node["panel"] not in panels):
                self.error(nid, f"{where}: unknown panel {node['panel']!r}", code="unknown-id")
            if kind == "group":
                children = node.get("children")
                if not isinstance(children, list) or not children:
                    self.error(nid, f"{where}: 'children' must be a non-empty list of node ids")
                    continue
                for child in children:
                    if not isinstance(child, str) or child == nid or child not in nodes:
                        self.error(nid, f"{where}: unknown child {child!r}", code="unknown-id")
                continue
            self.number(node, "x", where, nid)
            self.number(node, "y", where, nid)
            sized = kind != "text"
            self.number(node, "w", where, nid, required=sized, positive=True)
            self.number(node, "h", where, nid, required=sized, positive=True)
            if "fill" in node:
                self.token(node["fill"], "colour", where, nid)
            if "stroke" in node and node["stroke"] is not None:
                self.token(node["stroke"], "stroke", where, nid)
            if "text" in node:
                self.text(node["text"], f"{where}.text", nid)
            self.padding(node, where, nid)
            self.number(node, "radius", where, nid, required=False, minimum=0)
            self.choice(node, "align", ("left", "center", "right"), where, nid)
            self.choice(node, "valign", ("top", "middle", "bottom"), where, nid)
            if kind == "image":
                if not (isinstance(node.get("src"), str) and IMAGE_RE.match(node["src"])):
                    self.error(nid, f"{where}: 'src' must be a .png, .jpg or .svg path under figures/")
                self.choice(node, "fit", ("contain", "fill"), where, nid)
        self.groups(nodes)
        targets = set(nodes) | panels
        for edge in d.get("edges", []):
            if not valid(edge):
                continue
            eid = edge["id"]
            where = f"edge {eid!r}"
            self.keys(edge, EDGE_KEYS, where, eid)
            self.flags(edge, where, eid)
            for end in ("from", "to"):
                match = ANCHOR_RE.match(edge.get(end) or "") if isinstance(edge.get(end), str) else None
                if not match:
                    self.error(eid, f"{where}: {end!r} must be 'id.left|right|top|bottom|center' "
                                    "with an optional '@fraction'")
                elif match.group(1) not in targets:
                    self.error(eid, f"{where}: {end!r} refers to unknown node {match.group(1)!r}",
                               code="unknown-id")
                elif match.group(3) is not None and not 0 <= float(match.group(3)) <= 1:
                    self.error(eid, f"{where}: {end!r} anchor fraction must be between 0 and 1")
            self.choice(edge, "route", ("straight", "orthogonal"), where, eid)
            if "stroke" in edge:
                self.token(edge["stroke"], "stroke", where, eid)
            waypoints = edge.get("waypoints", [])
            if not isinstance(waypoints, list) or not all(
                    isinstance(p, list) and len(p) == 2 and all(is_number(v) for v in p)
                    for p in waypoints):
                self.error(eid, f"{where}: 'waypoints' must be a list of [x, y]")
            if edge.get("label") is not None:
                self.text(edge["label"], f"{where}.label", eid)
            if "gap" in edge and not (isinstance(edge["gap"], list) and len(edge["gap"]) == 2
                                      and all(is_number(v) and v >= 0 for v in edge["gap"])):
                self.error(eid, f"{where}: 'gap' must be [start_mm, end_mm]")
        for raw in d.get("raw", []):
            if not valid(raw):
                continue
            self.keys(raw, RAW_KEYS, f"raw {raw['id']!r}", raw["id"])
            self.flags(raw, f"raw {raw['id']!r}", raw["id"])
            if not isinstance(raw.get("svg"), str):
                self.error(raw["id"], f"raw {raw['id']!r}: 'svg' (string) is required")
        return self.issues


def validate(diagram: dict[str, Any], tokens: dict[str, Any]) -> list[Issue]:
    return Validator(diagram, tokens).run()


# ---------------------------------------------------- values and assets


def iter_strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from iter_strings(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key in ("t", "runs", "text", "label", "title"):
                yield from iter_strings(item)


def value_placeholders(diagram: dict[str, Any]) -> list[tuple[str, str]]:
    """(key, format) of every {{v:key}} or {{v:key|format}} in the diagram's text."""
    found: set[tuple[str, str]] = set()
    for collection in ("panels", "nodes", "edges"):
        for item in diagram.get(collection, []):
            for text in iter_strings({k: v for k, v in item.items()
                                      if k in ("text", "label", "title")}):
                found.update(PLACEHOLDER.findall(text))
    return sorted(found)


def value_keys(diagram: dict[str, Any]) -> list[str]:
    return sorted({key for key, _ in value_placeholders(diagram)})


def format_problems(diagram: dict[str, Any]) -> list[Issue]:
    """Named formats ({{v:key|format}}) belong to prose and tables, not diagrams."""
    return [issue("error", "value-format", None,
                  f"{{{{v:{key}|{name}}}}}: diagrams do not apply value formats; "
                  f"use {{{{v:{key}}}}} (its text from the values file)")
            for key, name in value_placeholders(diagram) if name]


def resolve_values(keys: list[str]) -> tuple[dict[str, str], list[Issue]]:
    """Look up {{v:key}} values; unknown keys and a stale values file are errors."""
    if not keys:
        return {}, []
    try:
        values = load_values()
    except ValuesError as exc:
        return {}, [issue("error", "values", None, str(exc))]
    found = {k: values[k] for k in keys if k in values}
    problems = [issue("error", "unknown-value", None, f"unknown value key {{{{v:{k}}}}}")
                for k in keys if k not in values]
    return found, problems


def read_assets(diagram: dict[str, Any]) -> tuple[dict[str, str | None], dict[str, str | None],
                                                  dict[str, str | None]]:
    """Embed SVG text and image data URIs keyed by path, plus their sha256 (None if missing)."""
    embeds: dict[str, str | None] = {}
    images: dict[str, str | None] = {}
    hashes: dict[str, str | None] = {}
    for panel in diagram.get("panels", []):
        path = panel.get("embed")
        if path:
            file = local_file(ROOT, path)
            data = file.read_bytes() if file is not None else None
            embeds[path] = data.decode("utf-8", "replace") if data is not None else None
            hashes[path] = sha256_bytes(data) if data is not None else None
    for node in diagram.get("nodes", []):
        path = node.get("src") if node.get("kind") == "image" else None
        if path:
            file = local_file(ROOT, path)
            data = file.read_bytes() if file is not None else None
            mime = MIME.get(Path(path).suffix.lower(), "application/octet-stream")
            images[path] = (f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
                            if data is not None else None)
            hashes[path] = sha256_bytes(data) if data is not None else None
    return embeds, images, hashes


def source_stamp(source: bytes, values: dict[str, str], hashes: dict[str, str | None]) -> str:
    """sha256 over everything an export depends on (see the module docstring)."""
    manifest = {
        "diagram": sha256_bytes(source),
        "tokens": sha256_bytes(TOKENS_PATH.read_bytes()),
        "renderer": sha256_bytes(RENDERER_PATH.read_bytes()),
        "values": values,
        "assets": hashes,
    }
    return sha256_bytes(json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode("utf-8"))


# ---------------------------------------------------- headless Chrome



def font_faces(tokens: dict[str, Any]) -> str:
    return "\n".join(
        f"@font-face {{ font-family: '{f['family']}'; font-weight: {f['weight']}; "
        f"src: url('{(FONT_DIR / f['file']).as_uri()}'); }}"
        for f in tokens["fonts"]["files"])


def render_page(width_mm: float, height_mm: float, tokens: dict[str, Any]) -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
{font_faces(tokens)}
@page {{ size: {width_mm}mm {height_mm}mm; margin: 0; }}
html, body {{ margin: 0; padding: 0; background: #fff; }}
svg {{ display: block; }}
</style><script src="{RENDERER_PATH.as_uri()}"></script></head><body></body></html>"""


def chrome_render(chrome: Chrome, payload: dict[str, Any], pdf: bool = False,
                  png_dpi: float | None = None) -> dict[str, Any]:
    """Render in a fresh tab; return svgText, geometry, issues and optional pdf/png bytes."""
    canvas = payload["diagram"]["canvas"]
    width, height = canvas["width_mm"], canvas["height_mm"]
    with tempfile.TemporaryDirectory(prefix="diagram-page-") as tmp:
        page = Path(tmp) / "page.html"
        page.write_text(render_page(width, height, payload["tokens"]), encoding="utf-8")
        with chrome.target() as session:
            chrome.call("Page.navigate", {"url": page.as_uri()}, session)
            deadline = time.monotonic() + chrome.timeout
            while True:
                state = chrome.call("Runtime.evaluate", {
                    "expression": "document.readyState === 'complete' && !!window.DiagramRenderer",
                    "returnByValue": True}, session)
                if state.get("result", {}).get("value"):
                    break
                if time.monotonic() > deadline:
                    raise DiagramError("the renderer page did not load")
                time.sleep(0.02)
            evaluated = chrome.call("Runtime.evaluate", {
                "expression": f"DiagramRenderer.renderForExport({json.dumps(payload)})",
                "awaitPromise": True, "returnByValue": True}, session)
            if "exceptionDetails" in evaluated:
                details = evaluated["exceptionDetails"]
                text = details.get("exception", {}).get("description") or details.get("text")
                raise DiagramError(f"renderer error: {text}")
            result = evaluated["result"]["value"]
            if pdf:
                printed = chrome.call("Page.printToPDF", {
                    "printBackground": True, "preferCSSPageSize": True}, session)
                result["pdf"] = base64.b64decode(printed["data"])
            if png_dpi:
                chrome.call("Emulation.setDeviceMetricsOverride", {
                    "width": round(width / 25.4 * 96), "height": round(height / 25.4 * 96),
                    "deviceScaleFactor": png_dpi / 96, "mobile": False}, session)
                shot = chrome.call("Page.captureScreenshot", {"format": "png"}, session)
                result["png"] = base64.b64decode(shot["data"])
    return result


# ---------------------------------------------------- lint


def overlap(a: list[float], b: list[float]) -> float:
    w = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    h = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return w * h if w > TOLERANCE_MM and h > TOLERANCE_MM else 0.0


def contains(outer: list[float], inner: list[float]) -> bool:
    t = TOLERANCE_MM
    return (inner[0] >= outer[0] - t and inner[1] >= outer[1] - t
            and inner[0] + inner[2] <= outer[0] + outer[2] + t
            and inner[1] + inner[3] <= outer[1] + outer[3] + t)


def segment_hits_box(p: list[float], q: list[float], box: list[float]) -> bool:
    """True when segment p-q passes through the interior of box (Liang-Barsky)."""
    t = TOLERANCE_MM
    x0, y0, x1, y1 = box[0] + t, box[1] + t, box[0] + box[2] - t, box[1] + box[3] - t
    if x1 <= x0 or y1 <= y0:
        return False
    dx, dy = q[0] - p[0], q[1] - p[1]
    lo, hi = 0.0, 1.0
    for denom, num in ((-dx, p[0] - x0), (dx, x1 - p[0]), (-dy, p[1] - y0), (dy, y1 - p[1])):
        if denom == 0:
            if num < 0:
                return False
        else:
            r = num / denom
            if denom < 0:
                lo = max(lo, r)
            else:
                hi = min(hi, r)
    return lo < hi


def concealed_ids(diagram: dict[str, Any]) -> set[str]:
    """Ids omitted from the drawing: hidden, inside a hidden group or panel, or an edge into one."""
    hidden: set[str] = set()
    nodes = [n for n in diagram.get("nodes", []) if isinstance(n, dict)]
    for collection in COLLECTIONS:
        for item in diagram.get(collection, []):
            if isinstance(item, dict) and item.get("hidden") is True and isinstance(item.get("id"), str):
                hidden.add(item["id"])
    changed = True
    while changed:
        changed = False
        for node in nodes:
            nid = node.get("id")
            if not isinstance(nid, str):
                continue
            if node.get("kind") == "group" and nid in hidden:
                for child in node.get("children") or []:
                    if isinstance(child, str) and child not in hidden:
                        hidden.add(child)
                        changed = True
            panel = node.get("panel")
            if isinstance(panel, str) and panel in hidden and nid not in hidden:
                hidden.add(nid)
                changed = True
    for edge in diagram.get("edges", []):
        if not isinstance(edge, dict) or not isinstance(edge.get("id"), str):
            continue
        ends = []
        for key in ("from", "to"):
            spec = edge.get(key)
            if isinstance(spec, str) and "." in spec:
                ends.append(spec.split(".", 1)[0])
        if edge.get("hidden") is True or any(end in hidden for end in ends):
            hidden.add(edge["id"])
    return hidden


def lint(diagram: dict[str, Any], geometry: dict[str, Any], tokens: dict[str, Any]) -> list[Issue]:
    """Layout checks over the geometry measured by the renderer."""
    found: list[Issue] = []
    hidden = concealed_ids(diagram)
    minimum = tokens["lint"]["min_font_pt"]
    tolerance = tokens["lint"]["embed_scale_tolerance"]
    canvas = [0.0, 0.0, geometry["canvas"]["width_mm"], geometry["canvas"]["height_mm"]]
    panels = {p["id"]: p["content"] for p in geometry["panels"]}
    nodes = geometry["nodes"]
    solid = [n for n in nodes if n["kind"] != "group"]
    for node in solid:
        nid = node["id"]
        if nid in hidden:
            continue
        text = node.get("text")
        if text and text["overflow_x"] > TOLERANCE_MM:
            found.append(issue("error", "text-overflow", nid,
                               f"text is {text['overflow_x']:.2f} mm wider than node {nid!r}"))
        if text and text["overflow_y"] > TOLERANCE_MM:
            found.append(issue("error", "text-overflow", nid,
                               f"text is {text['overflow_y']:.2f} mm taller than node {nid!r}"))
        small = [s for s in node.get("fonts_pt", []) if s < minimum - 1e-6]
        if small:
            found.append(issue("error", "font-size", nid,
                               f"font size {min(small):.2f} pt in {nid!r} is under {minimum} pt"))
        if not contains(canvas, node["box"]):
            found.append(issue("error", "outside-canvas", nid, f"node {nid!r} extends past the canvas"))
        panel = panels.get(node.get("panel"))
        if panel and not contains(panel, node["box"]):
            found.append(issue("error", "outside-panel", nid,
                               f"node {nid!r} extends past the content area of panel "
                               f"{node['panel']!r}"))
    for i, a in enumerate(solid):
        if a["id"] in hidden:
            continue
        for b in solid[i + 1:]:
            if b["id"] in hidden:
                continue
            if overlap(a["box"], b["box"]) and not (contains(a["box"], b["box"])
                                                    or contains(b["box"], a["box"])):
                found.append(issue("error", "overlap", a["id"],
                                   f"nodes {a['id']!r} and {b['id']!r} overlap"))
    for edge in geometry["edges"]:
        if edge["id"] in hidden:
            continue
        points = edge["points"]
        ends = {edge.get("from_id"), edge.get("to_id")}
        for node in solid:
            if node["id"] in ends or node["id"] in hidden:
                continue
            if any(segment_hits_box(points[k], points[k + 1], node["box"])
                   for k in range(len(points) - 1)):
                found.append(issue("error", "edge-crosses-node", edge["id"],
                                   f"edge {edge['id']!r} crosses node {node['id']!r}"))
        label = edge.get("label")
        if label:
            small = [s for s in label["fonts_pt"] if s < minimum - 1e-6]
            if small:
                found.append(issue("error", "font-size", edge["id"],
                                   f"label font {min(small):.2f} pt is under {minimum} pt"))
            if not contains(canvas, label["box"]):
                found.append(issue("error", "outside-canvas", edge["id"],
                                   f"label of edge {edge['id']!r} extends past the canvas"))
            for node in solid:
                if node["id"] not in ends and node["id"] not in hidden and overlap(label["box"], node["box"]):
                    found.append(issue("error", "label-overlap", edge["id"],
                                       f"label of edge {edge['id']!r} overlaps node {node['id']!r}"))
    for panel in geometry["panels"]:
        if panel["id"] in hidden:
            continue
        embed = panel.get("embed")
        if embed and not embed["missing"] and abs(embed["scale"] - 1) > tolerance:
            found.append(issue("warning", "embed-scaled", panel["id"],
                               f"embed {embed['path']} is scaled to {embed['scale'] * 100:.1f}% "
                               "(its font sizes change); render the chart at the panel size"))
    for raw in diagram.get("raw", []):
        if raw.get("id") in hidden:
            continue
        found.append(issue("warning", "raw", raw.get("id"),
                           f"raw SVG {raw.get('id')!r} bypasses the schema and lint"))
    return found


# ---------------------------------------------------- render pipeline


class Rendered:
    """Result of validating and (when the schema is valid) rendering one diagram."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.name = path.stem
        self.issues: list[Issue] = []
        self.diagram: dict[str, Any] = {}
        self.source = b""
        self.stamp = ""
        self.revision = ""
        self.accepted_stamps: tuple[str, ...] = ()
        self.output: dict[str, Any] = {}

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i["level"] == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i["level"] == "warning"]


def render_payload(diagram: dict[str, Any], source: bytes,
                   tokens: dict[str, Any]) -> tuple[dict[str, Any], list[Issue], str]:
    """The renderer input {diagram, tokens, values, embeds, images}, value issues and stamp.

    The stamp hashes the canonical serialisation (dump_diagram) of the
    diagram, not the raw file bytes, so re-saving the same content with
    different JSON formatting (escapes, 1.0 vs 1) does not make exports stale.
    """
    payload, problems, stamps = render_payload_stamps(diagram, source, tokens)
    return payload, problems, stamps[0]


def render_payload_stamps(diagram: dict[str, Any], source: bytes,
                          tokens: dict[str, Any]) -> tuple[dict[str, Any], list[Issue], tuple[str, ...]]:
    """As render_payload, with every stamp an export may carry: the canonical
    one first (written by export), then the raw-bytes stamp of older exports."""
    values, problems = resolve_values(value_keys(diagram))
    problems = format_problems(diagram) + problems
    embeds, images, hashes = read_assets(diagram)
    payload = {"diagram": diagram, "tokens": tokens, "values": values,
               "embeds": embeds, "images": images}
    canonical = source_stamp(dump_diagram(diagram), values, hashes)
    legacy = source_stamp(source, values, hashes)
    return payload, problems, (canonical, legacy) if legacy != canonical else (canonical,)


def process(path: Path, chrome: Chrome, pdf: bool = False,
            png_dpi: float | None = None) -> Rendered:
    """Validate, render and lint one diagram in a single Chrome tab."""
    result = Rendered(path)
    tokens = load_tokens()
    result.diagram, result.source = read_diagram(path)
    result.issues = validate(result.diagram, tokens)
    if result.errors:
        return result
    payload, problems, result.accepted_stamps = render_payload_stamps(result.diagram, result.source, tokens)
    result.stamp = result.accepted_stamps[0]
    result.revision = render_revision(result.stamp, tokens)
    result.issues += problems
    result.output = chrome_render(chrome, payload, pdf=pdf, png_dpi=png_dpi)
    seen = {(i["code"], i["message"]) for i in result.issues}
    for item in result.output["issues"]:
        if (item["code"], item["message"]) not in seen:
            result.issues.append(item)
            seen.add((item["code"], item["message"]))
    result.issues += lint(result.diagram, result.output["geometry"], tokens)
    return result


# ---------------------------------------------------- stamps


def stamp_svg(svg: str, stamp: str, source: str, revision: str = "") -> bytes:
    svg = svg.replace("<svg ", f'<svg data-{STAMP_KEY}="{stamp}" ', 1)
    if revision:
        svg = svg.replace("<svg ", f'<svg data-{RENDER_STAMP_KEY}="{revision}" ', 1)
    header = f"<!-- Generated by trazo diagram from {source}; do not edit. -->\n"
    return (header + svg + "\n").encode("utf-8")


def png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def stamp_png(png: bytes, stamp: str, revision: str = "") -> bytes:
    if png[:8] != b"\x89PNG\r\n\x1a\n" or png[12:16] != b"IHDR":
        raise DiagramError("Chrome returned an invalid PNG")
    end = 8 + 8 + struct.unpack(">I", png[8:12])[0] + 4
    stamps = png_chunk(b"tEXt", f"{STAMP_KEY}\0{stamp}".encode("ascii"))
    if revision:
        stamps += png_chunk(b"tEXt", f"{RENDER_STAMP_KEY}\0{revision}".encode("ascii"))
    return png[:end] + stamps + png[end:]


def stamp_pdf(pdf: bytes, stamp: str, title: str, revision: str = "") -> bytes:
    """Append an incremental update whose Info dictionary carries the stamp."""
    starts = re.findall(rb"startxref\s+(\d+)\s+%%EOF", pdf)
    trailer_at = pdf.rfind(b"trailer")
    size = re.search(rb"/Size\s+(\d+)", pdf[trailer_at:]) if trailer_at >= 0 else None
    root = re.search(rb"/Root\s+(\d+\s+\d+\s+R)", pdf[trailer_at:]) if trailer_at >= 0 else None
    if not (starts and size and root):
        raise DiagramError("cannot stamp the PDF (unexpected structure from Chrome)")
    number = int(size.group(1))
    body = pdf if pdf.endswith(b"\n") else pdf + b"\n"
    keywords = f"{STAMP_KEY}:{stamp}" + (f" {RENDER_STAMP_KEY}:{revision}" if revision else "")
    obj = (f"{number} 0 obj\n<< /Title ({title}) /Creator (trazo diagram) "
           f"/Keywords ({keywords}) >>\nendobj\n").encode("ascii")
    xref_at = len(body) + len(obj)
    tail = (f"xref\n{number} 1\n{len(body):010d} 00000 n \ntrailer\n"
            f"<< /Size {number + 1} /Root {root.group(1).decode()} /Info {number} 0 R "
            f"/Prev {int(starts[-1])} >>\nstartxref\n{xref_at}\n%%EOF\n").encode("ascii")
    return body + obj + tail


def read_stamp(path: Path, key: str = STAMP_KEY) -> str | None:
    if not path.exists():
        return None
    data = path.read_bytes()
    if path.suffix == ".svg":
        match = re.search(rf'data-{key}="([0-9a-f]{{64}})"'.encode(), data)
        return match.group(1).decode() if match else None
    if path.suffix == ".pdf":
        found = re.findall(rf"{key}:([0-9a-f]{{64}})".encode(), data)
        return found[-1].decode() if found else None
    at = 8
    while at + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[at:at + 8])
        if kind == b"tEXt":
            name, _, value = data[at + 8:at + 8 + length].partition(b"\0")
            if name.decode("latin-1") == key:
                return value.decode("ascii")
        if kind == b"IDAT":
            return None
        at += 12 + length
    return None


def export_paths(name: str) -> list[Path]:
    return [EXTERNAL_DIR / f"{name}.{ext}" for ext in ("svg", "pdf", "png")]


def is_draft(diagram: dict[str, Any]) -> bool:
    """True when the manuscript figure of this name must not be replaced."""
    meta = diagram.get("meta")
    return isinstance(meta, dict) and meta.get("draft") is True


def export_state(path, diagram=None, source=None, tokens=None):
    """Freshness without Chrome; old source-only stamps remain accepted."""
    if diagram is None:
        diagram, source = read_diagram(path)
    if is_draft(diagram):
        return "draft"
    tokens = tokens or load_tokens()
    if validate(diagram, tokens):
        return "error"
    _, problems, stamps = render_payload_stamps(diagram, source, tokens)
    if any(p["level"] == "error" for p in problems):
        return "error"
    revision = render_revision(stamps[0], tokens)
    paths = export_paths(path.stem)
    if any(not p.is_file() for p in paths):
        return "missing"
    for file in paths:
        if read_stamp(file) not in stamps:
            return "stale"
        full = read_stamp(file, RENDER_STAMP_KEY)
        if full is not None and full != revision:
            return "stale"
    return "current"


def stale_exports(result: Rendered) -> list[Issue]:
    if is_draft(result.diagram):
        return [issue("warning", "draft", None,
                      f"{result.name} is a draft and is not exported; "
                      "the manuscript figure is unchanged")]
    problems = []
    for path in export_paths(result.name):
        stamp = read_stamp(path)
        full = read_stamp(path, RENDER_STAMP_KEY)
        if (stamp not in (result.accepted_stamps or (result.stamp,))
                or (full is not None and result.revision and full != result.revision)):
            state = "missing" if not path.exists() else "unstamped" if stamp is None else "stale"
            problems.append(issue("error", "stale-export", None,
                                  f"{rel(path)} is {state}; run `trazo diagram export {result.name}`"))
    return problems


# ---------------------------------------------------- history and comments


HISTORY = History(HISTORY_PATH, ("file", "comments"), DiagramError)


def load_history() -> list[dict[str, Any]]:
    return HISTORY.load()


def save_history(history: list[dict[str, Any]]) -> None:
    HISTORY.save(history)


def write_diagram(path: Path, data: bytes, label: str) -> None:
    """Write a diagram atomically and record the previous text for undo."""
    HISTORY.write_file(path, data, label)


COMMENTS = CommentStore(
    COMMENTS_PATH, "diagram", HISTORY,
    targets=lambda request: {"diagram": request.get("diagram")})  # the editor checks targets


def revert_comments(entry: dict[str, Any]) -> None:
    """Undo a "comments" history entry; refuse if a comment changed since."""
    COMMENTS.revert(entry, DiagramError)


def undo() -> str:
    """Revert the last history entry; refuse (and drop it) if its target changed since."""
    return HISTORY.undo(revert_comments)


def resolve_comment(comment_id: str, reply: str) -> None:
    COMMENTS.resolve(comment_id, reply, DiagramError)


def comment_place(c: dict[str, Any]) -> str:
    return c.get("target") or ("point " + json.dumps(c.get("point")) if c.get("point") else "-")


def print_comments(diagram: str | None, show_all: bool) -> None:
    COMMENTS.print(diagram, show_all, comment_place)


# ---------------------------------------------------- patch


def find_item(diagram: dict[str, Any], element: str) -> tuple[str, dict[str, Any]]:
    for collection in COLLECTIONS:
        for item in diagram.get(collection, []):
            if isinstance(item, dict) and item.get("id") == element:
                return collection, item
    raise DiagramError(f"no element with id {element!r}")


def leaf_nodes(diagram: dict[str, Any], item: dict[str, Any]) -> list[dict[str, Any]]:
    """The item itself, or every non-group node a (possibly nested) group contains."""
    leaves: list[dict[str, Any]] = []
    seen: set[str] = set()
    stack = [item]
    while stack:
        node = stack.pop()
        if node.get("id") in seen:
            continue
        seen.add(node.get("id"))
        if node.get("kind") != "group":
            leaves.append(node)
            continue
        for child in reversed(node.get("children", [])):
            stack.append(find_item(diagram, child)[1])
    return leaves


def set_path(target: Any, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    for part in parts[:-1]:
        if isinstance(target, list) and part.isdigit() and int(part) < len(target):
            target = target[int(part)]
        elif isinstance(target, dict):
            target = target.setdefault(part, {})
        else:
            raise DiagramError(f"cannot follow {dotted!r} at {part!r}")
    last = parts[-1]
    if isinstance(target, list):
        if not last.isdigit() or int(last) >= len(target):
            raise DiagramError(f"index {last!r} out of range in {dotted!r}")
        if value is None:
            target.pop(int(last))
        else:
            target[int(last)] = value
    elif isinstance(target, dict):
        if value is None:
            target.pop(last, None)
        else:
            target[last] = value
    else:
        raise DiagramError(f"cannot set {dotted!r}")


def apply_op(diagram: dict[str, Any], op: dict[str, Any]) -> str:
    kind = op.get("op")
    if kind == "set":
        props = op.get("props")
        if not isinstance(props, dict) or not props:
            raise DiagramError("set needs a non-empty 'props' object")
        target = diagram.setdefault("canvas", {}) if op.get("id") == "@canvas" \
            else find_item(diagram, str(op.get("id")))[1]
        for key, value in props.items():
            if key == "id":
                raise DiagramError("ids are stable; remove and add the element to rename it")
            set_path(target, key, value)
        return f"set {op.get('id')}: {', '.join(props)}"
    if kind == "add":
        into = op.get("into")
        item = op.get("item")
        if into not in COLLECTIONS or not isinstance(item, dict):
            raise DiagramError(f"add needs 'into' ({', '.join(COLLECTIONS)}) and an 'item' object")
        items = diagram.setdefault(into, [])
        at = len(items)
        if op.get("before") is not None:
            match = [i for i, x in enumerate(items) if x.get("id") == op["before"]]
            if not match:
                raise DiagramError(f"add: no {into} item with id {op['before']!r}")
            at = match[0]
        items.insert(at, item)
        return f"add {item.get('id')}"
    if kind == "remove":
        element = str(op.get("id"))
        collection, item = find_item(diagram, element)
        diagram[collection].remove(item)
        if op.get("cascade"):
            prefix = f"{element}."
            diagram["edges"] = [e for e in diagram.get("edges", [])
                                if not (str(e.get("from", "")).startswith(prefix)
                                        or str(e.get("to", "")).startswith(prefix))]
            gone = {element}
            while gone:
                for node in diagram.get("nodes", []):
                    if node.get("kind") == "group":
                        node["children"] = [c for c in node.get("children", []) if c not in gone]
                gone = {n.get("id") for n in diagram.get("nodes", [])
                        if n.get("kind") == "group" and not n.get("children")}
                diagram["nodes"] = [n for n in diagram.get("nodes", []) if n.get("id") not in gone]
            if collection == "panels":
                for node in diagram.get("nodes", []):
                    if node.get("panel") == element:
                        node.pop("panel")
        return f"remove {element}"
    if kind == "move":
        element = str(op.get("id"))
        collection, item = find_item(diagram, element)
        absolute = "x" in op or "y" in op
        relative = "dx" in op or "dy" in op
        if absolute == relative:
            raise DiagramError("move needs either x/y or dx/dy")
        values = [op.get(k, 0) for k in (("x", "y") if absolute else ("dx", "dy"))]
        if not all(is_number(v) for v in values):
            raise DiagramError("move values must be numbers")
        if collection == "edges":
            if absolute:
                raise DiagramError("edges move only by dx/dy (their waypoints shift)")
            item["waypoints"] = [[p[0] + values[0], p[1] + values[1]]
                                 for p in item.get("waypoints", [])]
            return f"move {element}"
        if collection == "raw":
            raise DiagramError("raw fragments cannot be moved")
        members = leaf_nodes(diagram, item)
        if not members:
            raise DiagramError(f"group {element!r} has no nodes to move")
        if absolute:
            ox = min(m.get("x", 0) for m in members)
            oy = min(m.get("y", 0) for m in members)
            dx = values[0] - ox if "x" in op else 0
            dy = values[1] - oy if "y" in op else 0
        else:
            dx, dy = values
        for member in members:
            member["x"] = round(member.get("x", 0) + dx, 4)
            member["y"] = round(member.get("y", 0) + dy, 4)
        return f"move {element}"
    raise DiagramError(f"unknown op {kind!r} (use set, add, remove or move)")


# ---------------------------------------------------- commands


def format_issue(item: Issue) -> str:
    where = f" [{item['id']}]" if item.get("id") else ""
    return f"  {item['level']}: {item['code']}{where}: {item['message']}"


def report(result: Rendered) -> None:
    for item in result.issues:
        print(format_issue(item))
    status = "FAIL" if result.errors else "ok"
    print(f"{result.name}: {status} ({len(result.errors)} error(s), "
          f"{len(result.warnings)} warning(s))")


def cmd_new(name: str, width: float, height: float) -> None:
    if not all(is_number(value) and value > 0 for value in (width, height)):
        raise DiagramError("canvas width and height must be positive and finite")
    path = diagram_path(name, allow_path=False)
    if path.exists():
        raise DiagramError(f"{rel(path)} already exists")
    diagram = {"schema_version": 1,
               "canvas": {"width_mm": width, "height_mm": height, "grid": 1},
               "panels": [], "nodes": [], "edges": [], "raw": []}
    write_diagram(path, dump_diagram(diagram), f"new {name}")
    print(f"created {rel(path)} ({width:g} x {height:g} mm)")


def check_payload(result: Rendered) -> dict[str, Any]:
    """One diagram's entry in `check --json`."""
    return {"name": result.name, "file": rel(result.path), "ok": not result.errors,
            "errors": len(result.errors), "warnings": len(result.warnings),
            "issues": result.issues}


def cmd_check(names: list[str], as_json: bool, exports: bool) -> int:
    paths = [diagram_path(n) for n in names] if names else all_diagrams()
    results: list[Rendered] = []
    with Chrome() as chrome:
        for path in paths:
            try:
                result = process(path, chrome)
            except DiagramError as exc:
                result = Rendered(path)
                result.issues = [issue("error", "load", None, str(exc))]
            if exports and result.stamp:
                result.issues += stale_exports(result)
            results.append(result)
    if as_json:
        payload = [check_payload(r) for r in results]
        print(json.dumps(payload[0] if len(names) == 1 else payload, indent=2, ensure_ascii=False))
    else:
        if not results:
            print(f"no diagrams in {rel(DIAGRAM_DIR)}")
        for result in results:
            report(result)
    return 1 if any(r.errors for r in results) else 0


def cmd_render(name: str, png: str | None, dpi: float) -> int:
    path = diagram_path(name)
    out = Path(png) if png else RENDER_DIR / f"{path.stem}.png"
    with Chrome() as chrome:
        result = process(path, chrome, png_dpi=dpi)
    report(result)
    if "png" not in result.output:
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(out, result.output["png"])
    print(f"wrote {rel(out)} ({dpi:g} dpi)")
    return 0


@exclusive  # a build exports the same files
def cmd_export(names: list[str], force: bool, stale: bool = False) -> int:
    paths = [diagram_path(n, allow_path=False) for n in names] if names else all_diagrams()
    if stale:
        paths = [p for p in paths if export_state(p) not in ("current", "draft")]
    if not paths:
        return 0  # no browser needed for a blank project or a no-op build
    failed = False
    with Chrome() as chrome:
        for path in paths:
            result = process(path, chrome, pdf=True, png_dpi=DPI)
            report(result)
            if is_draft(result.diagram) and not force:
                print(f"{result.name}: draft, not exported (manuscript figure unchanged)")
                continue
            if "svgText" not in result.output or (result.errors and not force):
                print(f"{result.name}: not exported (fix the errors or use --force)")
                failed = True
                continue
            svg_path, pdf_path, png_path = export_paths(result.name)
            with review_lock():
                current, source = read_diagram(path)
                tokens = load_tokens()
                _, _, stamp = render_payload(current, source, tokens)
                if render_revision(stamp, tokens) != result.revision:
                    raise DiagramError(f"{result.name} changed during export; retry")
                write_atomic(svg_path, stamp_svg(result.output["svgText"], result.stamp, rel(path), result.revision))
                write_atomic(pdf_path, stamp_pdf(result.output["pdf"], result.stamp, result.name, result.revision))
                write_atomic(png_path, stamp_png(result.output["png"], result.stamp, result.revision))
            print(f"exported {rel(svg_path)}, {pdf_path.name} and {png_path.name}")
    return 1 if failed else 0


def describe_text(blocks: Any, lines: list[dict[str, Any]]) -> str:
    rendered = " / ".join(line["text"] for line in lines)
    return f'"{rendered}"' if rendered else "(no text)"


def cmd_describe(name: str) -> int:
    path = diagram_path(name)
    with Chrome() as chrome:
        result = process(path, chrome)
    d = result.diagram
    if not result.output:
        report(result)
        return 1
    geometry = result.output["geometry"]
    mm = lambda v: f"{v:.2f}"  # noqa: E731
    box = lambda b: f"x {mm(b[0])} y {mm(b[1])} w {mm(b[2])} h {mm(b[3])}"  # noqa: E731
    canvas = d["canvas"]
    print(f"{result.name}: canvas {canvas['width_mm']} x {canvas['height_mm']} mm, "
          f"{len(d.get('panels', []))} panel(s), {len(d.get('nodes', []))} node(s), "
          f"{len(d.get('edges', []))} edge(s), {len(d.get('raw', []))} raw")
    source = {n["id"]: n for n in d.get("nodes", [])}
    for panel in geometry["panels"]:
        spec = next(p for p in d["panels"] if p["id"] == panel["id"])
        label = " ".join(x for x in (spec.get("label"), spec.get("title")) if x)
        print(f"panel {panel['id']}: {box(panel['box'])}" + (f'  "{label}"' if label else ""))
        if panel["embed"]:
            e = panel["embed"]
            state = "MISSING" if e["missing"] else f"scale {e['scale'] * 100:.1f}%"
            print(f"    embed {e['path']} ({state})")
    for node in geometry["nodes"]:
        spec = source[node["id"]]
        extras = [f"{k} {spec[k]}" for k in ("fill", "stroke", "panel") if spec.get(k)]
        print(f"{node['kind']:5} {node['id']}: {box(node['box'])}"
              + (f"  ({', '.join(extras)})" if extras else ""))
        if node["kind"] == "group":
            print(f"    children: {', '.join(node['children'])}")
        if node.get("text"):
            lines = node["text"]["lines"]
            print(f"    text, {len(lines)} line(s), {', '.join(f'{s:g}' for s in node['fonts_pt'])} pt: "
                  f"{describe_text(spec.get('text'), lines)}")
    for edge in geometry["edges"]:
        spec = next(e for e in d["edges"] if e["id"] == edge["id"])
        route = " -> ".join(f"({mm(x)}, {mm(y)})" for x, y in edge["points"])
        print(f"edge  {edge['id']}: {spec['from']} -> {spec['to']} "
              f"[{spec.get('route', 'orthogonal')}, {spec.get('stroke', 'arrow')}] {route}")
    for raw in d.get("raw", []):
        print(f"raw   {raw['id']}: {len(raw.get('svg', ''))} characters of SVG")
    if result.issues:
        print("issues:")
        for item in result.issues:
            print(format_issue(item))
    return 0


def cmd_patch(name: str, ops_file: str) -> int:
    path = diagram_path(name)
    text = sys.stdin.read() if ops_file == "-" else Path(ops_file).read_text(encoding="utf-8")
    try:
        ops = json.loads(text)
    except ValueError as exc:
        raise DiagramError(f"operations are not valid JSON: {exc}") from exc
    if isinstance(ops, dict):
        ops = [ops]
    if not isinstance(ops, list) or not all(isinstance(o, dict) for o in ops):
        raise DiagramError("operations must be a JSON object or a list of objects")
    with review_lock():
        diagram, before = read_diagram(path)
        summary = [apply_op(diagram, op) for op in ops]
        problems = validate(diagram, load_tokens())
        if problems:
            for item in problems:
                print(format_issue(item))
            hint = (" (to remove a node with its edges, add \"cascade\": true)"
                    if any(p["code"] == "unknown-id" for p in problems) else "")
            raise DiagramError(f"patch not applied: the result is not a valid diagram{hint}")
        data = dump_diagram(diagram)
        if data == before:
            print("no change")
            return 0
        write_atomic(path, data)
        HISTORY.record(HISTORY.file_entry(
            path, before, data, f"patch {path.stem}: {'; '.join(summary)}"[:300]))
    print(f"patched {rel(path)}: {'; '.join(summary)}")
    print(f"next: trazo diagram check {name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="trazo diagram",
        description=__doc__.splitlines()[0],
        epilog="\n".join(__doc__.splitlines()[1:]),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("new", help="create an empty diagram")
    p.add_argument("name")
    p.add_argument("--width", type=float, default=183, choices=[183, 120, 89],
                   help="canvas width in mm (default 183)")
    p.add_argument("--height", type=float, default=60, help="canvas height in mm (default 60)")
    p = sub.add_parser("check", help="validate and lint (non-zero exit on error)")
    p.add_argument("names", nargs="*", metavar="NAME")
    p.add_argument("--all", action="store_true", help="check every diagram")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--exports", action="store_true",
                   help="also fail when figures/external exports are missing or stale")
    p = sub.add_parser("render", help="write a quick PNG to inspect")
    p.add_argument("name")
    p.add_argument("--png", help=f"output path (default {rel(RENDER_DIR)}/NAME.png)")
    p.add_argument("--dpi", type=float, default=RENDER_DPI, help=f"default {RENDER_DPI}")
    p = sub.add_parser("export", help="write figures/external/NAME.{svg,pdf,png}")
    p.add_argument("names", nargs="*", metavar="NAME")
    p.add_argument("--all", action="store_true", help="export every diagram")
    p.add_argument("--force", action="store_true", help="export even when check has errors")
    p.add_argument("--stale", action="store_true", help="only export missing or stale non-draft diagrams")
    p = sub.add_parser("describe", help="text summary of the layout in mm")
    p.add_argument("name")
    p = sub.add_parser("patch", help="apply set/add/remove/move operations by id")
    p.add_argument("name")
    p.add_argument("ops", help='JSON file with the operations, or "-" for stdin')
    p = sub.add_parser("comments", help="list open diagram comments")
    p.add_argument("name", nargs="?", help="only this diagram")
    p.add_argument("--all", action="store_true", help="every state, with threads")
    p = sub.add_parser("resolve", help="reply to a comment and set it to review")
    p.add_argument("id")
    p.add_argument("reply")
    sub.add_parser("undo", help="revert the last diagram or comment change")
    args = parser.parse_args(argv)

    try:
        if args.command == "new":
            cmd_new(args.name, args.width, args.height)
        elif args.command == "check":
            if bool(args.names) == args.all:
                parser.error("check needs NAME(s) or --all")
            return cmd_check(args.names, args.json, args.exports)
        elif args.command == "render":
            return cmd_render(args.name, args.png, args.dpi)
        elif args.command == "export":
            if bool(args.names) == args.all:
                parser.error("export needs NAME(s) or --all")
            return cmd_export(args.names, args.force, args.stale)
        elif args.command == "describe":
            return cmd_describe(args.name)
        elif args.command == "patch":
            return cmd_patch(args.name, args.ops)
        elif args.command == "comments":
            print_comments(args.name, args.all)
        elif args.command == "resolve":
            resolve_comment(args.id, args.reply)
            print(f"[{args.id}] review: reply added")
        elif args.command == "undo":
            print(undo())
    except (DiagramError, StoreError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
