"""Shared workspace state and catalog of document objects and their sources."""

import json
from html.parser import HTMLParser
from urllib.parse import quote, unquote, urlparse

from .dependencies import digest, fingerprint, snapshot
from .project import ASSETS_DIR, PROJECT


def diagram_for(source):
    from . import diagram
    parsed = urlparse(source)
    if parsed.scheme or parsed.netloc:
        return None
    path = (PROJECT.root / unquote(parsed.path).lstrip("/")).resolve()
    if path.parent == diagram.EXTERNAL_DIR.resolve() and diagram.NAME_RE.fullmatch(path.stem):
        file = diagram.DIAGRAM_DIR / (path.stem + ".json")
        if file.is_file() and file.resolve() == file.absolute():
            return path.stem
    return None


def diagram_links():
    from . import diagram
    names, sources = [], {}
    for path in diagram.all_diagrams():
        if not diagram.NAME_RE.fullmatch(path.stem) or path.is_symlink():
            continue
        for output in diagram.export_paths(path.stem):
            sources[diagram.rel(output)] = path.stem
        if (diagram.EXTERNAL_DIR / (path.stem + ".png")).is_file():
            names.append(path.stem)
    return {"names": names, "sources": sources}


class Objects(HTMLParser):
    def __init__(self, doc):
        super().__init__()
        self.doc, self.items, self.current = doc, [], None
        self.stack, self.caption_depth = [], None
        self.sources, self.composites = {}, set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        identifier = attrs.get("id", "")
        item = None
        hidden = any(t == "header" or a.get("id") == "refs" for t, a, _ in self.stack)
        nested = any(t == "figure" and a.get("id", "").startswith("fig:")
                     for t, a, _ in self.stack)
        if not hidden and not nested and ((tag == "figure" and identifier.startswith("fig:"))
                                         or (tag == "table" and identifier.startswith("tbl:"))):
            self.current = {"doc": self.doc, "id": identifier,
                            "kind": "figure" if tag == "figure" else "table", "src": "", "caption": ""}
            self.items.append(self.current)
            item = self.current
            self.sources[identifier] = []
            if tag == "figure" and "subfigures" in attrs.get("class", "").split():
                self.composites.add(identifier)
        elif self.current is not None:
            if tag == "img":
                source = attrs.get("src", "")
                self.sources[self.current["id"]].append(source)
                if not self.current["src"]:
                    self.current["src"] = source
            elif (tag == "caption" and self.current["kind"] == "table") or (
                    tag == "figcaption" and self.stack and self.stack[-1][2] is self.current):
                self.caption_depth = len(self.stack)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input",
                       "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append((tag, attrs, item))

    def handle_data(self, data):
        if self.caption_depth is not None and self.current is not None:
            self.current["caption"] += data

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                if self.caption_depth is not None and self.caption_depth >= index:
                    self.caption_depth = None
                self.current = next((item for _, _, item in reversed(self.stack) if item), None)
                break


def catalog():
    from . import diagram_editor, preview
    objects, uses = [], {}
    for doc in preview.DOCS:
        parser = Objects(doc)
        file = preview.OUT_DIR / (doc + ".html")
        if file.is_file():
            parser.feed(file.read_text(encoding="utf-8"))
        for item in parser.items:
            item["caption"] = " ".join(item["caption"].split())
            item["diagram"] = None if item["id"] in parser.composites else diagram_for(item["src"])
            item["href"] = f"/{quote(doc)}.html#{quote(item['id'])}"
            for name in {diagram_for(source) for source in parser.sources[item["id"]]} - {None}:
                uses.setdefault(name, []).append(item)
        objects.extend(parser.items)
    diagrams = []
    for path in diagram_editor.diagram_paths():
        state = diagram_editor.diagram_state(path)
        state["uses"] = uses.get(path.stem, [])
        state["href"] = "/diagrams#" + quote(path.stem)
        diagrams.append(state)
    return {"documents": PROJECT.titles, "objects": objects, "diagrams": diagrams,
            "links": diagram_links()}


def status():
    from . import diagram_editor, preview, review, table_editor
    with preview.LOCK:
        docs = {name: dict(s) for name, s in preview.STATE.items()}
    diagrams = diagram_editor.status(None)
    tables = table_editor.status()
    revision = digest(json.dumps({"documents": docs, "diagrams": diagrams["diagrams"],
                                  "tables": tables}, sort_keys=True).encode())
    inputs = digest(json.dumps({str(p): stamp for p, stamp in snapshot().items()
                               if not p.is_relative_to(ASSETS_DIR)}, sort_keys=True).encode())
    return {"documents": docs, "comments": preview.comments_stamp(), "docx": preview.docx_status(),
            "diagrams": diagrams, "tables": tables, "catalog": revision, "inputs": inputs,
            "review": review.revision(),
            "assets": fingerprint(p for p in ASSETS_DIR.rglob("*") if p.is_file())}


def get_route(path, query):
    return {"/__workspace": status, "/__catalog": catalog}.get(path)


def post_route(path, body):
    return None
