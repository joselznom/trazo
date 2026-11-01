"""Application host shared by documents, tables and diagrams.

Features provide get_route(path, query) and post_route(path, body), returning
a zero-argument producer or None. Existing feature URLs remain public aliases.
All static files go through the same resolver, including figure resources.
"""

from __future__ import annotations

import hashlib
import html
import json
import signal
import threading
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import counts
from .frontmatter import separate_title_page
from .local_server import GuardedHandler, LocalServer
from .portable import address_in_use
from .project import ASSETS_DIR, PROJECT
from .resources import local_file


@lru_cache(maxsize=8)
def _counts(name, page, settings):
    return counts.page_counts(name, page)


def served_counts(name, page):
    """page_counts of a served page, computed once per page text and
    [counts]/[limits] settings (the Submission view reuses them)."""
    config = counts.current_config()
    return _counts(name, page, json.dumps([config["counts"], config["limits"]], sort_keys=True))


def static_file(url: str) -> Path | None:
    """Resolve a local public file; never follow symlinks or hidden paths."""
    from . import preview
    path = unquote(urlparse(url).path).lstrip("/")
    parts = path.split("/")
    if any(p in ("", ".", "..") or p.startswith(".") for p in parts) or "\\" in path or "\0" in path:
        return None
    if path in {f"{name}.html" for name in preview.DOCS}:
        return local_file(preview.OUT_DIR, path)
    elif len(parts) == 3 and parts[0] == "__assets" and parts[1] in ("app", "preview", "diagram", "table"):
        if parts[1] == "preview" and parts[2].startswith("table-editor."):
            return local_file(ASSETS_DIR, "table/" + parts[2].replace("table-editor.", "editor.", 1))
        else:
            return local_file(ASSETS_DIR, parts[1] + "/" + parts[2])
    elif any(path.startswith(PROJECT.relative(key).strip("/") + "/") for key in (
            "figures", "diagrams", "diagram_output", "figure_fonts", "figure_embeds")):
        return local_file(PROJECT.root, path)
    else:
        return None


def editor_page(path: Path) -> bytes:
    config = json.dumps(PROJECT.titles).replace("</", "<\\/")
    return decorate_page(path.read_text(encoding="utf-8").replace(
        "<!-- config -->", f"<script>window.PREVIEW_TITLES = {config};</script>"))


# Latin fonts of the first screen and the paper's italics (theme.css declares
# all of them). A face that arrives after the first layout re-lays out the
# whole paper, about 0.5 s on a long one.
FIRST_FONTS = ("ibm-plex-sans-latin-400-normal", "ibm-plex-sans-latin-500-normal",
               "ibm-plex-sans-latin-600-normal", "ibm-plex-mono-latin-400-normal",
               "source-serif-4-latin-wght-normal", "source-serif-4-latin-wght-italic",
               "ibm-plex-sans-latin-400-italic")


def page_stamp(page: str) -> str:
    """A digest of a served document page and of the viewer scripts that read
    it. The viewer keeps where it found each comment under this stamp
    (sessionStorage), so a change to either must change it."""
    from . import preview
    digest = hashlib.sha256(page.encode())
    for script in ("preview-model.js", *preview.VIEWER_SCRIPTS):
        digest.update(b"\0" + (preview.PREVIEW_ASSETS / script).read_bytes())
    return digest.hexdigest()[:32]


def decorate_page(page, counts=None, stamp=False):
    """A page with the workspace's shared head; `counts` (a document page's,
    from counts.page_counts) becomes window.PREVIEW_COUNTS. `stamp` adds
    <meta name="trazo-page"> with the page_stamp of the decorated page."""
    # builds: document name -> the DOCX its full build writes ("" = no full build).
    builds = {name: PROJECT.config["documents"][name]["docx"] if PROJECT.document_build(name) is not None else ""
              for name in PROJECT.documents}
    # submission_parts: documents the preview shows under its Submission menu.
    config = json.dumps({"kind": PROJECT.config["kind"], "titles": PROJECT.titles, "number_prefixes": PROJECT.number_prefixes, "builds": builds,
                         "submission_parts": PROJECT.submission_parts,
                         "separate_title_page": separate_title_page(PROJECT),
                         "paths": {key: PROJECT.relative(key) for key in (
                             "figures", "diagrams", "diagram_output", "figure_fonts", "figure_embeds")}}).replace("</", "<\\/")
    # Apply stored colours before the first paint in every workspace. Preview
    # HTML also carries this script for standalone serving; move it into head.
    page = page.replace('<script src="/__assets/app/app.js"></script>', '')
    # The first paint, and the snapshot a page change fades into, waits until
    # the page scripts at the end of body have built the shell, and fetches
    # the fonts early so text does not reflow when they arrive.
    shared = (f'<script>window.TRAZO_CONFIG = {config};'
              'window.PREVIEW_TITLES = window.TRAZO_CONFIG.titles;'
              'window.PREVIEW_NUMBER_PREFIXES = window.TRAZO_CONFIG.number_prefixes;'
              + (f'window.PREVIEW_COUNTS = {json.dumps(counts).replace("</", "<" + chr(92) + "/")};'
                 if counts is not None else "") + '</script>'
              '<link rel="expect" href="#pk-shell-ready" blocking="render">'
              + "".join(f'<link rel="preload" href="/__assets/preview/{name}.woff2" as="font" '
                        'type="font/woff2" crossorigin>' for name in FIRST_FONTS) +
              '<script src="/__assets/app/app.js"></script>'
              '<link rel="stylesheet" href="/__assets/app/theme.css">'
              '<link rel="stylesheet" href="/__assets/app/app.css">'
              '<link rel="icon" href="/__assets/app/favicon.svg" type="image/svg+xml">')
    head, end, rest = page.rpartition("</body>")
    if end:
        page = head + '<span id="pk-shell-ready" hidden></span>' + end + rest
    page = page.replace("</head>", shared + "</head>", 1)
    if stamp:
        page = page.replace("</head>", f'<meta name="trazo-page" content="{page_stamp(page)}"></head>', 1)
    return page.encode()


def first_page() -> str:
    from . import preview
    return f"/{next(iter(preview.DOCS))}.html"


def served_page(path: str) -> bytes | None:
    """The HTML served at `path`: an editor page, a document page, or its stub
    while it builds; None when `path` is no page (trazo.page_bundle inlines it)."""
    from . import diagram_editor, preview, table_editor
    pages = {"/tables": table_editor.EDITOR_PAGE, "/diagrams": diagram_editor.EDITOR_PAGE}
    if path.rstrip("/") in pages:
        return editor_page(pages[path.rstrip("/")])
    name = path.lstrip("/").removesuffix(".html")
    if not path.endswith(".html") or name not in preview.DOCS:
        return None
    built = static_file(path)
    if not built:
        return stub_page(name)
    page = built.read_text(encoding="utf-8")
    return decorate_page(page, served_counts(name, page), stamp=True)


def stub_page(name: str) -> bytes:
    """The page of a document that has not built yet: its error or "building...",
    reloading itself once the document builds."""
    from . import preview
    with preview.LOCK:
        error = preview.STATE[name]["error"] or "building..."
    return (
        '<!doctype html><meta charset="utf-8"><title>trazo</title>'
        '<link rel="icon" href="/__assets/app/favicon.svg" type="image/svg+xml">'
        '<link rel="stylesheet" href="/__assets/app/theme.css">'
        '<link rel="stylesheet" href="/__assets/preview/preview.css">'
        f'<div class="pv-banner error" style="display:block;margin:2em">{html.escape(error)}</div>'
        "<script>(async function poll(){try{"
        "const s=await(await fetch('/__status',{cache:'no-store'})).json();"
        f"if(s[{json.dumps(name)}].ok){{(window.TRAZO_VIEW||location).reload();return;}}"
        "}catch(e){}setTimeout(poll,1000)})()</script>"
    ).encode()


def features():
    from . import diagram_editor, page_bundle, preview, preview_updates, review, table_editor, workspace
    return preview, table_editor, diagram_editor, workspace, review, preview_updates, page_bundle


class Handler(GuardedHandler):
    def do_HEAD(self):
        if self.guard(False):
            super().do_HEAD()

    def do_GET(self):
        if not self.guard(False):
            return
        url = urlparse(self.path)
        query = parse_qs(url.query)
        for feature in features():
            route = feature.get_route(url.path, query)
            if route is not None:
                self.reply(route)
                return
        if url.path in ("", "/"):
            self.send_response(302)
            self.send_header("Location", first_page())
            self.end_headers()
            return
        page = served_page(url.path)
        if page is not None:
            self.send_bytes(page, "text/html; charset=utf-8")
            return
        super().do_GET()

    def do_POST(self):
        if not self.guard(True):
            return
        path = urlparse(self.path).path
        for feature in features():
            route = feature.post_route(path, self.json_body)
            if route is not None:
                self.reply(route)
                return
        self.send_json({"error": "not found"}, 404)

    def translate_path(self, path):
        from . import preview
        return str(static_file(path) or preview.OUT_DIR / "__missing__")

    def list_directory(self, path):
        self.send_error(404, "Not found")
        return None


SERVING: list[LocalServer] = []  # the server serve() runs, for shutdown()
STOPPING = threading.Event()  # shutdown() was called, maybe before serve() bound


def shutdown() -> None:
    """Make serve() return, from another thread: Windows cannot send itself
    the SIGTERM serve otherwise stops on."""
    STOPPING.set()
    for server in list(SERVING):
        server.shutdown()


def serve(port=8765, on_bind=None):
    """Serve until SIGTERM, Ctrl-C or shutdown(); on_bind(port) runs once the port is bound."""
    from . import diagram_editor, preview
    stop_event = threading.Event()
    def stop(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    with LocalServer(("127.0.0.1", port), Handler) as server:
        if on_bind:
            on_bind(server.server_port)
        # Start serving before Pandoc finishes; editors remain usable during builds.
        workers = [threading.Thread(target=preview.build_and_watch, args=(stop_event,), daemon=True),
                   threading.Thread(target=diagram_editor.CHECKER.reap, args=(stop_event,), daemon=True)]
        for worker in workers:
            worker.start()
        print(f"[trazo] http://127.0.0.1:{server.server_port}/", flush=True)
        SERVING.append(server)
        if STOPPING.is_set():  # asked before it was registered: serve_forever returns at once
            threading.Thread(target=server.shutdown, daemon=True).start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            SERVING.remove(server)
            stop_event.set()
            preview.DOCX_JOB.stop("cancelled")
            diagram_editor.shutdown()
            for worker in workers:
                worker.join(timeout=5)


def serve_from_terminal(port: int, command: str) -> None:
    """serve(port), failing in one line when the port is taken. Unlike
    preview_start (trazo.mcp), a terminal run does not move to a free
    port: its user typed or bookmarked this one."""
    try:
        serve(port)
    except OSError as exc:
        if not address_in_use(exc):
            raise
        raise SystemExit(f"trazo: port {port} is already in use; stop what is using it, "
                         f"or pick another port: {command} --port N") from None
