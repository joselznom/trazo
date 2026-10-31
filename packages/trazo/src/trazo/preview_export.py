"""Independent exports selected after the complete document is numbered.

Pandoc reads the original expanded Markdown, runs crossref and citeproc
once, then the selected AST is written to DOCX/Markdown or rendered in
Chrome. Equations, captions, media and citation numbers stay intact.
"""

import base64
import copy
import itertools
import json
import mimetypes
import re
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import docx_indexes, pandoc_run
from .browser import Chrome
from .expand import expand_files
from .local_server import NotFound, RequestError
from .numbering import metadata_file
from .pandoc_options import mathml_flag
from .project import PROJECT
from .resources import local_file

MIMES = {"docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
         "md": "text/markdown; charset=utf-8", "svg": "image/svg+xml",
         "png": "image/png", "pdf": "application/pdf"}


def pandoc(data, *flags):
    try:
        result = pandoc_run.run(list(flags), input=data, timeout=120,
                                command=PROJECT.command("pandoc"), cwd=PROJECT.root)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RequestError(f"could not export with Pandoc: {exc}") from exc
    if result.returncode:
        raise RequestError(result.stderr.decode("utf-8", errors="replace").strip() or "Pandoc export failed")
    return result.stdout


def walk(value):
    if isinstance(value, dict):
        yield value
        yield from walk(value.get("c"))
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)


def siblings(value):
    """Every list of AST nodes, so a block can be found with its neighbours."""
    if isinstance(value, list):
        if any(isinstance(item, dict) and "t" in item for item in value):
            yield value
        for item in value:
            yield from siblings(item)
    elif isinstance(value, dict):
        yield from siblings(value.get("c"))


def is_note(block):
    """A table note: the ::: {.table-note} block that follows a table."""
    return isinstance(block, dict) and block.get("t") == "Div" and "table-note" in block["c"][0][1]


def document(doc):
    if doc not in PROJECT.documents:
        raise RequestError("unknown document")
    source = expand_files(PROJECT.document_files(doc), document=doc)
    return json.loads(pandoc(source.encode(), "--from=markdown", "--to=json",
        f"--metadata-file={PROJECT.relative('metadata')}",
        f"--metadata-file={metadata_file(doc, PROJECT.path('build'))}",
        f"--bibliography={PROJECT.relative('bibliography')}", f"--csl={PROJECT.relative('csl')}",
        "--filter=pandoc-crossref", "--citeproc"))


def select(ast, kind, identifier, exclude=(), references=True):
    """Select sections in document order, or one identified figure/table.

    A section brings its subsections; an excluded section leaves them out.
    Each block follows the nearest listed heading above it, so a subsection
    inside an excluded one can be listed again to bring it back.
    """
    blocks = ast["blocks"]
    if kind == "section":
        identifiers = [identifier] if isinstance(identifier, str) else identifier
        if not isinstance(identifiers, list) or not identifiers or any(
                not isinstance(value, str) or not value for value in [*identifiers, *exclude]):
            raise RequestError("choose at least one section")
        wanted, dropped = set(identifiers), set(exclude)
        if wanted & dropped:
            raise RequestError("a section cannot be both included and excluded")
        found, chosen, open_ = set(), [], []  # open_: (depth, included) per listed heading
        for b in blocks:
            if b["t"] == "Header":
                depth, attrs = b["c"][:2]
                while open_ and open_[-1][0] >= depth:
                    open_.pop()
                if attrs[0] in wanted | dropped:
                    found.add(attrs[0])
                    open_.append((depth, attrs[0] in wanted))
            if open_ and open_[-1][1]:
                chosen.append(b)
        if found != wanted | dropped:
            raise NotFound("section no longer exists; reload the viewer")
        # The references Div is document-level material, even if Pandoc
        # placed it after the final section without its own heading.
        chosen = [b for b in chosen if not (b["t"] == "Div" and b["c"][0][0] == "refs")]
    elif kind == "figure":
        chosen = [b for b in walk(blocks) if b.get("t") == "Figure" and b["c"][0][0] == identifier]
        if len(chosen) != 1:
            raise NotFound("figure no longer exists; reload the viewer")
    elif kind == "table":
        # A table brings its note.
        found = [group[i:i + 2] for group in siblings(blocks) for i, b in enumerate(group)
                 if isinstance(b, dict) and b.get("t") == "Table" and b["c"][0][0] == identifier]
        if len(found) != 1:
            raise NotFound("table no longer exists; reload the viewer")
        chosen = [b for i, b in enumerate(found[0]) if i == 0 or is_note(b)]
    else:
        raise RequestError("choose a section, figure or table")
    cites = {c["citationId"] for n in walk(chosen) if n.get("t") == "Cite" for c in n["c"][0]}
    refs = [n for n in walk(blocks) if n.get("t") == "Div" and n["c"][0][0] == "refs"]
    if cites and refs and references:
        ref = copy.deepcopy(refs[0])
        ref["c"][1] = [b for b in ref["c"][1] if b.get("t") != "Div" or b["c"][0][0] in {"ref-" + c for c in cites}]
        chosen = [*chosen, ref]
    return {**ast, "meta": {k: v for k, v in ast.get("meta", {}).items() if k in ("lang",)}, "blocks": chosen}


def image_path(source):
    parsed = urlparse(source)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise RequestError("image exports require a local figure file")
    relative = unquote(parsed.path).removeprefix("./")
    allowed = any(relative.startswith(PROJECT.relative(key).rstrip("/") + "/") for key in ("figures", "diagram_output"))
    path = local_file(PROJECT.root, relative)
    if not allowed or path is None:
        raise RequestError("the image must exist inside figures/")
    return path


def vector_path(path):
    candidates = [path] if path.suffix.lower() == ".svg" else [path.with_suffix(".svg")]
    if path.parent.name == "png":
        candidates.append(path.parent.parent / "svg" / (path.stem + ".svg"))
    return next((p for p in candidates if p.is_file() and p.suffix.lower() == ".svg"), None)


def standalone_svg(path, seen=()):
    """Original vector tree with local media/fonts embedded recursively."""
    path = path.resolve()
    if path in seen:
        raise RequestError("cyclic SVG resource")
    try:
        root = ET.fromstring(path.read_text(encoding="utf-8"))
        box = [float(n) for n in re.split(r"[ ,]+", root.attrib["viewBox"].strip())]
        if len(box) != 4 or box[2] <= 0 or box[3] <= 0:
            raise ValueError("invalid viewBox")
    except (OSError, ET.ParseError, KeyError, ValueError) as exc:
        raise RequestError(f"the vector source needs a valid viewBox: {exc}") from exc
    # Inline local resources so the downloaded SVG has no dependence on
    # the project's directory layout. Network resources are not fetched.
    def embedded(source):
        if source.startswith(("data:", "#")):
            return source
        parsed = urlparse(source)
        if parsed.scheme or parsed.netloc:
            raise RequestError("SVG export needs local embedded resources")
        resource = (path.parent / unquote(parsed.path)).resolve()
        if not resource.is_relative_to(PROJECT.root.resolve()) or not resource.is_file():
            raise RequestError("SVG resource is missing or outside the project")
        mime = mimetypes.guess_type(resource.name)[0] or "application/octet-stream"
        data = resource.read_bytes()
        if resource.suffix.lower() == ".svg":
            child = standalone_svg(resource, (*seen, path))
            data = ET.tostring(child, encoding="utf-8")
        return f"data:{mime};base64,{base64.b64encode(data).decode()}"
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] in ("script", "foreignObject"):
            raise RequestError("SVG source contains unsupported scripts or foreign content")
        for key, value in list(node.attrib.items()):
            if key.rsplit("}", 1)[-1] == "href":
                node.set(key, embedded(value))
            elif "url(" in value:
                node.set(key, re.sub(r"url\([\"']?([^)'\"]+)[\"']?\)", lambda m: "url('" + embedded(m[1]) + "')", value))
        if node.tag.rsplit("}", 1)[-1] == "style" and node.text:
            if "@import" in node.text:
                raise RequestError("SVG styles must embed their fonts; @import is unsupported")
            node.text = re.sub(r"url\([\"']?([^)'\"]+)[\"']?\)", lambda m: "url('" + embedded(m[1]) + "')", node.text)
    return root


def caption_svg(path, caption):
    """Embed the original vector tree and a visible, selectable caption."""
    root = standalone_svg(path)
    box = [float(n) for n in re.split(r"[ ,]+", root.attrib["viewBox"].strip())]
    width, height = 1000, 1000 * box[3] / box[2]
    caption_body = '<div style="box-sizing:border-box;width:1000px;padding:16px;font:18px/1.4 serif;overflow-wrap:anywhere">' + caption + '</div>'
    caption_height = chrome_image(caption_body, "height")
    root.attrib.update(x="0", y="0", width=str(width), height=str(height))
    outer = ET.Element("{http://www.w3.org/2000/svg}svg", {
        "viewBox": f"0 0 {width} {height + caption_height}", "width": str(width),
        "height": str(height + caption_height)})
    ET.SubElement(outer, "{http://www.w3.org/2000/svg}rect", {"width": "100%", "height": "100%", "fill": "white"})
    outer.append(root)
    foreign = ET.SubElement(outer, "{http://www.w3.org/2000/svg}foreignObject", {
        "x": "0", "y": str(height), "width": str(width), "height": str(caption_height)})
    try:
        foreign.append(ET.fromstring(caption_body.replace('<div ', '<div xmlns="http://www.w3.org/1999/xhtml" ', 1)))
    except ET.ParseError as exc:
        raise RequestError(f"caption cannot be embedded as standalone SVG: {exc}") from exc
    return ET.tostring(outer, encoding="utf-8", xml_declaration=True)


def rendered_figure(ast, fmt):
    figure = ast["blocks"][0]
    if fmt == "svg":
        images = [n for n in walk(figure["c"][2]) if n.get("t") == "Image"]
        if len(images) != 1:
            raise RequestError("SVG export requires a single-image figure; use PNG, PDF or DOCX for multiple panels")
        vector = vector_path(image_path(images[0]["c"][2][0]))
        if not vector:
            raise RequestError("SVG is unavailable: this figure has no vector source")
        content = {**ast, "blocks": figure["c"][1][1]}
    else:
        # Embed bytes so Chrome cannot race an image regeneration on disk.
        content = copy.deepcopy(ast)
        for n in walk(content["blocks"]):
            if n.get("t") == "Image":
                path = image_path(n["c"][2][0])
                ext = path.suffix.lower()
                mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".svg": "image/svg+xml"}.get(ext)
                if not mime:
                    raise RequestError("PNG/PDF export needs a PNG, JPEG, WebP or SVG source")
                data = ET.tostring(standalone_svg(path), encoding="utf-8") if ext == ".svg" else path.read_bytes()
                n["c"][2][0] = f"data:{mime};base64,{base64.b64encode(data).decode()}"
                n["c"][0][2] = []
    body = pandoc(json.dumps(content).encode(), "--from=json", "--to=html5",
                  mathml_flag(tuple(PROJECT.command("pandoc")))).decode()
    return caption_svg(vector, body) if fmt == "svg" else chrome_image(body, fmt)


def chrome_image(body, fmt):
    with tempfile.TemporaryDirectory(prefix="trazo-export-") as folder:
        page = Path(folder) / "figure.html"
        page.write_text('<!doctype html><meta charset="utf-8"><style>'
            'html,body{margin:0;padding:0;background:white}body{width:1000px}'
            'figure{margin:0;padding:16px}img{max-width:100%;height:auto}'
            'figcaption{font:18px/1.4 serif;margin-top:16px}p{margin:0}'
            '</style><main>' + body + '</main>', encoding="utf-8")
        with Chrome() as chrome:
            with chrome.target() as session:
                chrome.call("Emulation.setDeviceMetricsOverride", {"width": 1000, "height": 800,
                    "deviceScaleFactor": 2, "mobile": False}, session)
                chrome.call("Page.navigate", {"url": page.as_uri()}, session)
                deadline = time.monotonic() + 30
                while True:
                    ready = chrome.call("Runtime.evaluate", {"expression":
                        "document.readyState === 'complete' && [...document.images].every(i => i.complete && i.naturalWidth > 0)",
                        "returnByValue": True}, session)["result"].get("value")
                    if ready:
                        break
                    if time.monotonic() > deadline:
                        raise RequestError("the figure did not finish rendering")
                    time.sleep(0.02)
                height = chrome.call("Runtime.evaluate", {"expression": "Math.ceil(document.querySelector('main').getBoundingClientRect().height)",
                    "returnByValue": True}, session)["result"]["value"]
                if not 0 < height <= 20000:
                    raise RequestError("the figure is too large to export")
                if fmt == "height":
                    return height
                if fmt == "png":
                    result = chrome.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True,
                        "clip": {"x": 0, "y": 0, "width": 1000, "height": height, "scale": 1}}, session)
                else:
                    result = chrome.call("Page.printToPDF", {"printBackground": True, "paperWidth": 1000 / 96,
                        "paperHeight": height / 96, "marginTop": 0, "marginBottom": 0,
                        "marginLeft": 0, "marginRight": 0}, session)
                return base64.b64decode(result["data"])


def save(data: bytes, filename: str, doc: str) -> str:
    """Keep an export for an MCP App view, which cannot download (decision
    011): beside the document's Word file when that folder is in the paper,
    else in <build>/exports. A file that exists is never replaced, nor a link
    followed: the name gets " (2)", " (3)"... Returns its path in the paper."""
    root = PROJECT.root.resolve()
    docx = PROJECT.document_docx(doc)
    folder = docx.parent if docx else PROJECT.path("build") / "exports"
    if not folder.resolve().is_relative_to(root):
        folder = PROJECT.path("build") / "exports"
    if not folder.resolve().is_relative_to(root):
        raise RequestError("the paper's build folder is outside the paper; download the export from the browser")
    folder.mkdir(parents=True, exist_ok=True)
    name = Path(filename)
    for n in itertools.count(1):
        target = folder / (name.name if n == 1 else f"{name.stem} ({n}){name.suffix}")
        try:
            with open(target, "xb") as out:  # O_EXCL: fails on anything there, a link too
                out.write(data)
        except FileExistsError:
            continue
        return target.resolve().relative_to(root).as_posix()
    raise AssertionError("unreachable")


def export(doc, kind, identifier, fmt, exclude=(), references=True, indexes=()):
    allowed = {"section": ("docx",), "table": ("docx", "md"),
               "figure": ("docx", "png", "pdf", "svg")}
    if kind not in allowed or fmt not in allowed[kind] or (
            kind != "section" and not isinstance(identifier, str)):
        raise RequestError("unsupported export format for this object")
    ast = select(document(doc), kind, identifier, exclude, references)
    # Prepare every selected diagram before reading any of its image/vector bytes.
    from . import diagram
    names = set()
    for node in walk(ast["blocks"]):
        if node.get("t") == "Image":
            source = urlparse(node["c"][2][0])
            if source.scheme or source.netloc:
                continue
            path = (PROJECT.root / unquote(source.path)).resolve()
            if path.parent == diagram.EXTERNAL_DIR.resolve() and diagram.NAME_RE.fullmatch(path.stem):
                if (diagram.DIAGRAM_DIR / (path.stem + ".json")).is_file():
                    names.add(path.stem)
    if names and diagram.cmd_export(sorted(names), False, stale=True):
        raise RequestError("diagram export failed; fix its check issues before downloading")
    if kind == "figure" and fmt in ("png", "pdf", "svg"):
        data = rendered_figure(ast, fmt)
    else:
        flags = ["--from=json", "--to=" + ("markdown" if fmt == "md" else fmt), "--resource-path=."]
        if fmt == "docx" and PROJECT.path("reference_doc").is_file():
            flags.append(f"--reference-doc={PROJECT.path('reference_doc')}")
        data = pandoc(json.dumps(ast).encode(), *flags)
        if fmt == "docx" and kind == "section":
            data = docx_indexes.apply(data, indexes)
    if isinstance(identifier, list):
        identifier = identifier[0] if len(identifier) == 1 else "sections"
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "-", f"{doc}-{identifier}").strip("-")[:180]
    return data, MIMES[fmt], stem + "." + fmt
