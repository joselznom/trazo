"""Build the project's Word files (trazo.build_graph), or only the stages
before them: `--prepare=all` runs values, tables, the steps before expand and
the diagrams; `--prepare=inputs` all of them but the diagram export, so it
needs no Chrome (the preview and the checks)."""

import argparse
import sys
import time

from . import build_graph, diagram
from .build_graph import Builder, BuildError, parse_env
from .build_lock import locked
from .project import PROJECT


def add_build_options(parser) -> None:
    from . import docx_indexes
    parser.add_argument("--doc", choices=list(PROJECT.documents), action="append",
                        help="only this document (repeatable; default: every Word file)")
    parser.add_argument("--submission", action="store_true",
                        help="also build the separate submission files and when = \"submission\" steps")
    parser.add_argument("--force", action="store_true", help="run every stage and step, even when fresh")
    parser.add_argument("--env", action="append", metavar="K=V", help="override a [build.env] value")
    parser.add_argument("--authors", action="store_true",
                        help="keep authors and affiliations under the title when [submission] title_page is separate")
    for key, (title, _) in docx_indexes.INDEXES.items():
        parser.add_argument("--" + key, action="store_true", help="include " + title.lower())


def build_command(args) -> int:
    from . import diagram, docx_indexes
    started = time.monotonic()
    try:
        builder = Builder(env=parse_env(args.env), force=args.force, submission=args.submission,
                          authors=args.authors)
        builder.build(args.doc, [key for key in docx_indexes.INDEXES if getattr(args, key)])
    except (BuildError, diagram.DiagramError, OSError, ValueError, RuntimeError) as exc:
        print(f"build: {exc}", file=sys.stderr)
        return 1
    print(f"[build] done in {time.monotonic() - started:.2f} s", flush=True)
    return 0


@locked
def main(argv=None):
    parser = argparse.ArgumentParser(prog="trazo build", description=build_graph.__doc__.split("\n\n")[0])
    parser.add_argument("--prepare", choices=["all", "inputs"],
                        help="all: every stage before expand; inputs: those but the diagram export")
    add_build_options(parser)
    args = parser.parse_args(argv)
    if not args.prepare:
        return build_command(args)
    try:
        builder = build_graph.Builder(env=build_graph.parse_env(args.env), force=args.force,
                                      submission=args.submission)
        builder.prepare(args.prepare == "all")
        return 0
    except (build_graph.BuildError, diagram.DiagramError, OSError, ValueError, RuntimeError) as exc:
        print(f"build: {exc}", file=sys.stderr)
        return 1
