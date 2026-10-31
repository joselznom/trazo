"""Live HTML preview of the project's documents for reading and editing.

Builds each document in full with the same Pandoc pipeline as the Word
output (includes, {{v:key}} values, cross-references, citeproc), so figure,
table and reference numbers match. The server writes plain Pandoc HTML;
the viewer scripts (VIEWER_SCRIPTS) build the viewer around it (index, review
sidebar, Figures & Tables view). preview-model.js is shared by the page and
the Figures & Tables view: the other documents are fetched as /<name>.html
and parsed with the same code. Paragraph labels come from it and must not
change, because stored review comments depend on them. Sources are watched
and rebuilt on save; the page reloads itself. Build errors and warnings
appear as a banner instead of stopping the server. In-text references to
figures and tables are links (`--metadata=linkReferences:true`, preview
only) and feed the "Cited in" lists.

Paths and commands come from trazo.toml (see trazo.project); the
defaults are the paper template's layout, used in the notes below.

Review comments made on the page (select text, write a note) are stored in
notes/review/comments.json (undo history in history.json; set
PREVIEW_REVIEW_DIR to use another folder). A comment is `open` (written by
the author), `review` (Claude edited the Markdown and replied; waiting for
the author) or `done` (the author confirmed). From `review` the author
confirms or reopens it with a follow-up; replies form a short thread.
Claude lists the open ones, edits the Markdown sources, and answers each
with `resolve`, which moves it to `review`. Concurrent writers (server and
CLI) are serialized with a file lock; undo reverts only the comment(s) the
last change touched, and is refused (409) if that comment has changed
since.

The Source tab is a plain text editor for the Markdown file behind the
section in view (it follows the scroll while there are no unsaved edits; the
file picker chooses another). Click a paragraph marker to open it at that
paragraph. Saving (Cmd/Ctrl+S) writes
only existing .md files under manuscript/main or manuscript/supplementary,
refuses with 409 if the file changed on disk since it was loaded, and keeps
the previous text in notes/review/file_history.json for "Undo save" (also
refused if the file changed since). The page reloads after the rebuild, but
never while there are unsaved edits. The Diff tab shows the file against
git HEAD (paragraphs aligned, word-level changes; GET /__diff). Substituted
{{v:key}} values and in-text citations show tooltips (the preview build wraps
values in spans; the Word build never does). Export > Full <document> runs that
document's full build (`trazo build --doc NAME` by default) in the background
(POST /__build-docx; state and log tail in /__status).
Every identified table has "Edit caption" (GET/POST /__table-caption),
which saves to its original JSON spec or Markdown paragraph.
Generated values stay locked chips; saves check the on-disk hash and are
undoable through the source format's history. Spec tables (tables/specs/<label>.json, see trazo.table)
also have "Edit table" (window.PREVIEW_TABLE_SPECS), which opens the
structural editor, /tables#<label>, with a return link; its endpoints (/__tables,
/__table..., /__table-comments, /__table-status) are documented in
trazo.table_editor. A change under tables/specs or the table data folder
re-renders the spec tables in-process (no project code runs), unless the
project has its own tables stage (a `tables` step or [commands] entry): a
spec change then reruns it before the rebuild, since it may read the specs.
Endpoints: GET/POST /__source, /__table-caption and /__figure-text,
GET /__diff, GET /__values, GET /__submission (the Submission view's report,
reused until what it reads changes; ?fields=0 leaves out the form fields,
?fresh=S makes it again if its check started over S seconds ago), POST /__locate,
POST /__build-docx and /__cancel-docx, and the table and diagram editors'. The server listens on 127.0.0.1 only
and refuses (403) requests whose Host is not 127.0.0.1:<port> or
localhost:<port>, whose Origin or Sec-Fetch-Site says another site, or (POST)
whose Content-Type is not application/json. Following a link from another
site to a page is allowed (top-level navigation only); iframe loads are
refused, and every response carries X-Frame-Options: DENY and
Content-Security-Policy: frame-ancestors 'none'. There are no CORS headers.
A figure whose image is figures/external/<name>.png and that has
figures/diagrams/<name>.json gets an "Edit diagram" link to the diagram
editor, /diagrams#<name>, with a return link; the Figures & Tables view has an
"All diagrams" link to /diagrams for drafts without an export. The links are
added by the viewer only, never in the Word output, and the list of
diagrams is refreshed at each rebuild. The editor's endpoints (/__diagrams/...)
are documented in trazo.diagram_editor.
Paragraph markers: click opens the source there, Alt-click copies the ¶ label;
lists have a small pencil marker. Figures and tables have Edit caption;
spec tables also have Edit table. The full
build has a cancel link; a build is stopped after PREVIEW_DOCX_TIMEOUT
seconds (default 1200).
The Figures & Tables view is `?view=figures`; the left sidebar then holds
its filters and the right one only the comments on captions and tables.
Articles also have the Submission view (the Checklist of the top bar's
Submission menu), `?view=submission`: the checks of
trazo.submission_check as cards (a row opens where it is fixed: its
section and source line, or the title block or keywords) and the journal
form fields with Copy buttons; the left sidebar lists its groups and the
menu button's badge counts the errors.

The viewer makes no requests outside 127.0.0.1: its fonts (IBM Plex Sans and
Mono, Source Serif 4; SIL OFL) ship with trazo in assets/preview/.

This module holds the preview's paths and state, the routes and the CLI; the
work lives in preview_comments, preview_sources (Source and Diff tabs),
preview_captions, preview_render, preview_watch and preview_full_build, which
read that state from here when they run, and is imported back under its old
names.

    trazo preview serve [--port 8765]  build, watch, and serve on 127.0.0.1
    trazo preview build                build once into .build/preview/
    trazo preview comments [--all]     print open comments (--all: every
                                          state, with threads)
    trazo preview resolve ID "reply"   add an agent reply and set it to review
    trazo preview undo                 revert the last comment change
"""

import argparse
import json
import os
import re
import subprocess  # noqa: F401  (tests patch preview.subprocess.run)
import sys
import threading
import time

from . import preview_export
from .background import CommandJob
from .expand import VALUES_PATH
from .local_server import Conflict, Download, NotFound  # noqa: F401  (callers catch preview.NotFound)
from .local_server import RequestError as CommentError

# Moved to their own modules; imported back because callers and tests use preview.<name>.
from .preview_captions import (  # noqa: F401
    caption_source,
    change_figure_text,
    change_table_caption,
    get_figure_text,
    get_table_caption,
    with_table_note,
)
from .preview_comments import (  # noqa: F401
    PreviewComments,
    PreviewHistory,
    anchor_field,
    change_comments,
    comments_payload,
    comments_stamp,
    load_comments,
    print_comments,
)
from .preview_full_build import (  # noqa: F401
    cancel_docx_build,
    docx_status,
    start_docx_build,
)
from .preview_render import (  # noqa: F401
    diagram_links,
    doc_files,
    render,
    strip_html_comments,
    table_spec_labels,
)
from .preview_sources import (  # noqa: F401
    GitError,
    change_source,
    diff_source,
    get_source,
    git,
    locate_source,
    read_include_from,
    read_source,
    save_file_history,
    sha256_of,
    source_path,
    source_state,
)
from .preview_watch import (  # noqa: F401
    build_and_watch,
    changed_documents,
    regenerate_tables,
    render_spec_tables,
    snapshot,
    watch,
)
from .project import ASSETS_DIR, PROJECT
from .review_storage import REVIEW_DIR, StoreError
from .server import Handler, serve, serve_from_terminal, served_counts  # noqa: F401  # compatibility: preview.Handler remains public

ROOT = PROJECT.root
OUT_DIR = PROJECT.path("build") / "preview"
COMMENTS_PATH = REVIEW_DIR / "comments.json"
HISTORY_PATH = REVIEW_DIR / "history.json"
FILE_HISTORY_PATH = REVIEW_DIR / "file_history.json"
FILE_HISTORY_LIMIT = 50
FILE_HISTORY_BYTES = 20_000_000
# The only files the page may read or write, and only if they already exist.
DOCS = PROJECT.documents  # name -> folder of Markdown sections
SOURCE_ROOTS = tuple(dict.fromkeys(str(folder.relative_to(ROOT)) for name in DOCS
                                  for folder in PROJECT.document_roots(name)))
# Repository paths the page may load (figures only), and trazo's assets.
SERVED_PREFIXES = ("figures/",)
ASSET_URL = "__assets/preview/"
PREVIEW_ASSETS = ASSETS_DIR / "preview"
# The viewer: classic scripts in assets/preview, loaded in this order after
# preview-model.js. They share their top-level names as globals, so a part
# may only use at load time what the parts before it (or it) declare.
VIEWER_SCRIPTS = (
    "preview-page.js", "preview-wordmark.js", "preview-shell.js", "preview-layout.js", "preview-navigation.js",
    "preview-comment-anchors.js", "preview-comment-list.js", "preview-comment-highlights.js",
    "preview-source.js", "preview-diff.js", "preview-build.js", "preview-new-comments.js",
    "preview-captions.js", "preview-caption-editors.js", "preview-export.js", "preview-export-picker.js",
    "preview-figures.js", "preview-lightbox.js", "preview-submission.js", "preview-updates.js", "preview.js",
)
WATCHED = tuple(PROJECT.config["preview"]["watch"])
PANDOC_FLAGS = [
    f"--metadata-file={PROJECT.relative('metadata')}",
    "--standalone",
    f"--bibliography={PROJECT.relative('bibliography')}",
    f"--csl={PROJECT.relative('csl')}",
    "--resource-path=.",
    "--metadata=linkReferences:true",  # "Figure 1" in the text links to the figure (the viewer's "Cited in")
    "--filter=pandoc-crossref",
    "--citeproc",
    "--section-divs",
    "--to=html5",
    f"--css=/{ASSET_URL}preview.css",
]

DOCX_JOB = CommandJob("build", "tail", 40, grace=5, doc="", shown="")
DOCX_TIMEOUT = float(os.environ.get("PREVIEW_DOCX_TIMEOUT") or 1200)  # seconds
STATE = {name: {"version": 0, "ok": False, "error": "", "warnings": [], "built": ""}
         for name in DOCS}
LOCK = threading.Lock()
# The time.time() of the watcher's last check that found every watched file as
# the pages were built from (preview_watch); /__status reports it as `synced`.
# It advances whether those builds succeeded or not: each page's `error`
# (STATE, also set when a build raised) says whether it shows them or keeps
# its last good version.
WATCHER = {"synced": 0.0}

LOCATE_LOW = 0.45  # below this a match is not trusted
DIAGRAM_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
# A spec or data change re-renders the spec tables in-process
# (trazo.table.render_all); a spec change reruns the project's own tables
# stage instead when it has one (preview_watch).
TABLE_SPEC_SOURCES = ("table_specs", "table_data")


def values_info():
    """key -> {text, source} for the value tooltips."""
    payload = json.loads(VALUES_PATH.read_text(encoding="utf-8"))
    return {k: {"text": v.get("text", ""), "source": v.get("source", "")}
            for k, v in payload.get("values", {}).items()}


def build(name):
    error, warnings = render(name, OUT_DIR)
    with LOCK:
        state = STATE[name]
        state.update(ok=not error, error=error, warnings=warnings,
                     built=time.strftime("%H:%M:%S"))
        if not error:  # a failed build keeps the last good page and version
            state["version"] += 1
    status = "error" if error else f"{len(warnings)} warning(s)" if warnings else "ok"
    print(f"[preview] {name}: {status}", flush=True)
    return not error


def status():
    with LOCK:
        docs = {name: dict(state) for name, state in STATE.items()}
    # "server" lets trazo mcp tell its own preview from another one on the port.
    return {**docs, "comments": comments_stamp(), "docx": docx_status(), "synced": WATCHER["synced"],
            "server": {"pid": os.getpid(), "project": str(PROJECT.root)}}


def export_indexes(options):
    from .docx_indexes import INDEXES
    if any(key in options and not isinstance(options[key], bool) for key in INDEXES):
        raise CommentError("export index options must be booleans")
    return [key for key in INDEXES if options.get(key, False)]


def export_request(query):
    first = lambda key, default=None: query.get(key, [default])[0]
    kind = first("kind")
    identifier = query.get("id", []) if kind == "section" else first("id")
    indexes = export_indexes({key: first(key, "0") == "1" for key in ("toc", "lof", "lot")})
    download = Download(*preview_export.export(first("doc"), kind, identifier, first("format"),
                                               query.get("exclude", []), first("refs", "1") != "0", indexes))
    if first("save") != "1":
        return download
    return {"path": preview_export.save(download.data, download.filename, first("doc"))}  # an MCP App view


def submission_report(fields=True, fresh=None):
    """The submission report (trazo.submission_check) with the counts of
    the served first document; `fields` adds the form fields. Reused until
    something it reads changes, or for `fresh` seconds at most
    (trazo.preview_submission); not while the Word build runs, since it
    rewrites what the report reads and its end brings a new report."""
    from . import preview_submission

    def manuscript_counts():
        name = next(iter(DOCS))
        page = OUT_DIR / f"{name}.html"
        with LOCK:
            error = STATE[name]["error"]
        if error:
            raise RuntimeError(f"Build failed: counts may be out of date · {error}")
        if not page.is_file():
            raise RuntimeError("the preview is still building")
        return served_counts(name, page.read_text(encoding="utf-8"))

    def states():
        with LOCK:
            return json.dumps(STATE, sort_keys=True)
    if docx_status()["state"] == "running":
        fresh = None
    return preview_submission.report(fields, manuscript_counts, states, ROOT, (REVIEW_DIR, OUT_DIR), fresh)


def fresh_seconds(value):
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        raise CommentError("fresh must be a number of seconds") from None
    if not 0 <= seconds < float("inf"):
        raise CommentError("fresh must be a number of seconds")
    return seconds


def get_route(path, query):
    first = lambda key: query.get(key, [None])[0]
    routes = {
        "/__status": status, "/__comments": lambda: comments_payload(first("doc")),
        "/__diff": lambda: diff_source(first("file")), "/__values": values_info,
        "/__source": lambda: get_source(first("file")),
        "/__figure-text": lambda: get_figure_text(first("doc"), first("id")),
        "/__table-caption": lambda: get_table_caption(first("doc"), first("id")),
        "/__export": lambda: export_request(query),
        "/__submission": lambda: submission_report(first("fields") != "0", fresh_seconds(first("fresh"))),
        "/__export-formats": lambda: {"formats": ["docx", "png", "pdf", *(
            ["svg"] if preview_export.vector_path(preview_export.image_path(first("src") or "")) else [])]},
    }
    return routes.get(path)


def post_route(path, body):
    route = {"/__comments": change_comments, "/__source": change_source,
             "/__figure-text": change_figure_text,
             "/__table-caption": change_table_caption,
             "/__locate": locate_source,
             "/__build-docx": start_docx_build, "/__cancel-docx": cancel_docx_build}.get(path)
    return (lambda: route(body())) if route is not None else None


def main(argv=None):
    parser = argparse.ArgumentParser(prog="trazo preview", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["serve", "build", "comments", "resolve", "undo"])
    parser.add_argument("target", nargs="*", help="resolve: ID and reply text")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--all", action="store_true",
                        help="comments: list every state, with threads")
    args = parser.parse_args(argv)

    try:
        if args.command == "comments":
            print_comments(args.all)
            return
        if args.command == "undo":
            try:
                label = change_comments({"op": "undo"})
            except Conflict:
                raise
            except CommentError as exc:  # nothing to undo
                print(exc)
                return
            print("undone; next undo: " + (label["undo"] or "nothing"))
            return
        if args.command == "resolve":
            if len(args.target) != 2:
                raise SystemExit('usage: trazo preview resolve ID "reply"')
            change_comments({"op": "reply", "id": args.target[0],
                             "by": "agent", "text": args.target[1]})
            print(f"[{args.target[0]}] review: reply added")
            return
    except (CommentError, StoreError) as exc:
        raise SystemExit(f"error: {exc}")

    if args.command == "build":
        results = [build(name) for name in DOCS]
        raise SystemExit(0 if all(results) else 1)
    serve_from_terminal(args.port, "scripts/trazo preview serve")


if __name__ == "__main__":
    sys.exit(main())
