"""GET /__bundle?page=PATH: a served page with what it loads inlined, for
the preview's MCP App view.

A view reaches the preview only through a tool call, one request at a time,
and its default CSP blocks stylesheets and fonts from data: URLs. So the
page comes back in one reply, as JSON: `path` (the page; "/" is the first
document), `html` with every local script, stylesheet (its @imports
included) and image inlined, the preload and icon links dropped, and
`fonts`, the stylesheets' @font-face rules (family, descriptors, base64
data) for the view to load with FontFace. An inlined image keeps its path
in data-trazo-src, which the viewer reads to find diagrams and exports.
The diagram editor's page also gets the figure fonts (figure_tokens), which
the editor declares by URL at run time. Scripts are inlined byte for
byte, so renderer.js stays the file its export stamps hash. Anything not
local stays as it was.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from .local_server import NotFound
from .resources import local_file as public_file

SCRIPT = re.compile(r'<script src="([^"]+)"></script>')
STYLESHEET = re.compile(r'<link rel="stylesheet" href="([^"]+)"\s*/?>')
DROPPED = re.compile(r'<link rel="(?:preload|icon)"[^>]*>')
IMAGE = re.compile(r'(<img\b[^>]*?\ssrc=")([^"]+)(")')
IMPORT = re.compile(r"""@import\s+url\(\s*["']?([^"')]+)["']?\s*\)\s*;""")
FONT_FACE = re.compile(r"@font-face\s*\{([^}]*)\}")
CSS_URL = re.compile(r"""url\(\s*["']?([^"')]+)["']?\s*\)""")
DESCRIPTOR = re.compile(r"(font-family|font-style|font-weight|font-display|unicode-range)\s*:\s*([^;]+)")
FONT_DESCRIPTORS = {"font-style": "style", "font-weight": "weight", "font-display": "display",
                    "unicode-range": "unicodeRange"}
ORIGIN = "http://page.invalid"


def get_route(path: str, query: dict[str, list[str]]) -> Callable[[], dict[str, Any]] | None:
    if path == "/__bundle":
        return lambda: bundle((query.get("page") or ["/"])[0])
    return None


def post_route(path: str, body: Callable[[], Any]) -> None:
    return None


def local_file(ref: str, base: str) -> Path | None:
    """The public file `ref` names, relative to the URL path `base`."""
    from .server import static_file
    if ref.startswith(("data:", "blob:", "#")):
        return None
    target = urlparse(urljoin(ORIGIN + base, ref))
    return static_file(target.path) if f"{target.scheme}://{target.netloc}" == ORIGIN else None


def data_url(path: Path) -> str:
    kind = "font/woff2" if path.suffix == ".woff2" else mimetypes.guess_type(path.name)[0]
    return f"data:{kind or 'application/octet-stream'};base64,{base64.b64encode(path.read_bytes()).decode()}"


def bundle(page: str) -> dict[str, Any]:
    from .server import first_page, served_page
    path = urlparse(page).path
    path = first_page() if path in ("", "/") else path
    served = served_page(path)
    if served is None:
        raise NotFound(f"no page at {path}")
    fonts: list[dict[str, Any]] = []

    def script(match: re.Match[str]) -> str:
        file = local_file(match[1], path)
        if file is None:
            return match[0]
        return "<script>" + re.sub(r"</(script)", r"<\\/\1", file.read_text(encoding="utf-8"), flags=re.I) + "</script>"

    def stylesheet(match: re.Match[str]) -> str:
        file = local_file(match[1], path)
        if file is None:
            return match[0]
        css = inline_css(file, urlparse(urljoin(ORIGIN + path, match[1])).path, fonts, set())
        return "<style>" + re.sub(r"</(style)", r"<\\/\1", css, flags=re.I) + "</style>"

    def image(match: re.Match[str]) -> str:
        file = local_file(match[2], path)
        if file is None:
            return match[0]
        return f'{match[1].removesuffix("src=\"")}data-trazo-src="{match[2]}" src="{data_url(file)}{match[3]}'

    html = served.decode("utf-8")
    html = SCRIPT.sub(script, html)
    html = STYLESHEET.sub(stylesheet, html)
    html = DROPPED.sub("", html)
    html = IMAGE.sub(image, html)
    if path.rstrip("/") == "/diagrams":
        fonts += figure_fonts()
    return {"path": path, "html": html, "fonts": fonts}


def figure_fonts() -> list[dict[str, Any]]:
    """The fonts figure_tokens names, as the diagram renderer's fontFaceCss declares them."""
    from .project import PROJECT
    try:
        files = json.loads(PROJECT.path("figure_tokens").read_text(encoding="utf-8"))["fonts"]["files"]
    except (OSError, ValueError, KeyError, TypeError):
        return []  # the editor reports a bad or missing tokens file itself
    fonts = []
    for entry in files if isinstance(files, list) else []:
        # Only a file inside figure_fonts: the tokens file is the paper's, the bundle goes to the host.
        file = public_file(PROJECT.path("figure_fonts"), str(entry.get("file", ""))) if isinstance(entry, dict) else None
        if file is not None and isinstance(entry, dict):
            fonts.append({"family": str(entry.get("family", "")), "descriptors": {"weight": str(entry.get("weight", 400))},
                          "data": base64.b64encode(file.read_bytes()).decode()})
    return fonts


def inline_css(file: Path, url: str, fonts: list[dict[str, Any]], seen: set[Path]) -> str:
    """The stylesheet at `url` with its @imports inlined, its @font-face rules
    moved to `fonts` and its other local url()s as data: URLs."""
    if file in seen:
        return ""
    seen.add(file)

    def imported(match: re.Match[str]) -> str:
        target = local_file(match[1], url)
        if target is None:
            return match[0]
        return inline_css(target, urlparse(urljoin(ORIGIN + url, match[1])).path, fonts, seen)

    def font_face(match: re.Match[str]) -> str:
        source = CSS_URL.search(match[1])
        target = local_file(source[1], url) if source else None
        if target is None:
            return match[0]
        descriptors = {key: value.strip() for key, value in DESCRIPTOR.findall(match[1])}
        fonts.append({"family": descriptors.pop("font-family", "").strip("\"'"),
                      "descriptors": {FONT_DESCRIPTORS[key]: value for key, value in descriptors.items()},
                      "data": base64.b64encode(target.read_bytes()).decode()})
        return ""

    def other(match: re.Match[str]) -> str:
        target = local_file(match[1], url)
        return match[0] if target is None else f'url("{data_url(target)}")'

    css = IMPORT.sub(imported, file.read_text(encoding="utf-8"))
    css = FONT_FACE.sub(font_face, css)
    return CSS_URL.sub(other, css)
