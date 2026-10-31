"""trazo command line: `trazo COMMAND [ARGS...]`.

Commands run in the project found from the current folder (the nearest
trazo.toml; see trazo.project). Each command has its own --help.
"""

from __future__ import annotations

import importlib
import sys

from . import __version__

COMMANDS = {
    "abbreviations": ("abbreviations", "check document abbreviation lists and first-use definitions"),
    "submission": ("submission", "check what an article still needs before submission"),
    "new": ("new", "create a paper from the template in an empty folder (no Copier or git)"),
    "update": ("update", "update a paper made by `trazo new` to this trazo's template (run a newer trazo)"),
    "build": ("builds", "build the Word files: values, tables, diagrams, steps, Pandoc"),
    "run": ("build_graph:run_main", "run one [[build.steps]] step (or values, tables, diagrams)"),
    "verify": ("build_graph:verify_main", "clean, build everything, run the checks, test the Word files"),
    "review": ("review", "list, answer and undo comments across the workspace"),
    "preview": ("preview", "live HTML preview with comments, source editor and diff"),
    "diagram": ("diagram", "create, check, render and export declarative diagrams"),
    "table": ("table", "check, render and patch tables from data and JSON specs"),
    "expand": ("expand", "expand !include lines and {{v:key}} values to stdout"),
    "citations": ("citations", "check citation keys against the bibliography"),
    "counts": ("counts", "count main-text words, figures, tables and references"),
    "setup": ("tool_setup", "download Pandoc into the per-user folder; record the machine's tools"),
    "doctor": ("doctor", "check uv, Python, Pandoc and Chrome: paths, versions, what to fix"),
    "agents": ("agents", "copy agent skills to .agents/ and .claude/"),
    "mcp": ("mcp", "MCP server on stdio for the desktop apps (build, check, preview)"),
}


def usage() -> str:
    width = max(map(len, COMMANDS))
    lines = [f"usage: trazo COMMAND [ARGS...]   (trazo {__version__})", "", "commands:"]
    lines += [f"  {name:<{width}}  {about}" for name, (_, about) in COMMANDS.items()]
    lines += [f"  {'version':<{width}}  print the trazo version and project root"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(usage())
        return 0 if argv else 2
    name, rest = argv[0], argv[1:]
    from .project import CONFIG_NAME, LOAD_ERROR, PROJECT
    # Setup and lifecycle commands must work before a paper config loads.
    if LOAD_ERROR is not None and name not in ("mcp", "new", "update", "setup"):
        print(f"trazo: {LOAD_ERROR}", file=sys.stderr)
        return 2
    if name in ("version", "--version"):
        pinned = PROJECT.config["version"] or "unpinned"
        where = (PROJECT.root / CONFIG_NAME if PROJECT.found
                 else f"{PROJECT.root} (no {CONFIG_NAME})")
        print(f"trazo {__version__} (project pins {pinned}; {where})")
        return 0
    if name not in COMMANDS:
        print(f"trazo: unknown command {name!r}\n\n{usage()}", file=sys.stderr)
        return 2
    module, _, function = COMMANDS[name][0].partition(":")
    module = importlib.import_module(f".{module}", __package__)
    try:
        return getattr(module, function or "main")(rest) or 0
    except KeyboardInterrupt:
        # Ctrl+C: the command's finally blocks and with statements have
        # already cleaned up by now; only the traceback is left to hide.
        print("trazo: interrupted", file=sys.stderr)
        return 130
