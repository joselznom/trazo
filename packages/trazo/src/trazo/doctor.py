"""Check the tools this project needs: paths, versions, where each was found.

    trazo doctor [--json]

uv and Pandoc are required; Chrome (or Edge, Chromium) is optional, and says what is off without it. Each tool is
looked up as trazo runs it: a project setting (commands.pandoc,
browser.chrome, $TRAZO_CHROME), then PATH, then the data folder and the
machine file that `trazo setup` writes (trazo.machine). Exits 1 when
a required tool is missing, and says what to do for each problem. --json
prints the same report as one JSON object (see `checks`).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from . import machine
from .project import PROJECT

MARKS = {"ok": "✓", "missing": "✗", "note": "!"}


def item(tool: str, status: str, message: str, fix: str = "", **details: str) -> dict[str, str]:
    """One report line: `status` is "ok", "missing" (required, or a project
    setting that does not run) or "note"; found tools add path, version, found."""
    return {"tool": tool, "status": status, "message": message, "fix": fix, **details}


def found(tool: str, name: str, version: str, path: str, where: str) -> dict[str, str]:
    return item(tool, "ok", f"{version or name}: {path} ({where})", path=path, version=version, found=where)


def line(entry: dict[str, str]) -> str:
    fix = entry["fix"]
    return f"  {MARKS[entry['status']]} {entry['message']}" + (f"\n      fix: {fix}" if fix else "")


def setup_command() -> str:
    """How to run trazo setup here: through the project's launcher in a project."""
    return "scripts/trazo setup" if (PROJECT.root / "scripts" / "trazo").is_file() else "trazo setup"


def pandoc_fix() -> str:
    from .tool_setup import PANDOC_VERSION, pandoc_asset
    if known := machine.known_pandoc():
        return f"run {setup_command()}: it records {known} for apps that do not see it on PATH"
    if pandoc_asset():
        return f"run {setup_command()}: it downloads Pandoc {PANDOC_VERSION} into {machine.data_dir()}"
    return "install Pandoc (https://pandoc.org/installing.html), or set commands.pandoc in trazo.toml"


def chrome_fix() -> str:
    from .tool_setup import CHROME_VERSION, chrome_asset
    if chrome_asset():
        return f"run {setup_command()}: it downloads Chrome {CHROME_VERSION} into {machine.data_dir()}"
    return machine.CHROME_FIX


def check_pandoc() -> tuple[list[dict[str, str]], bool, str]:
    """Items, whether Pandoc was found, and its version line."""
    command, where = machine.resolve_pandoc(PROJECT.config["commands"]["pandoc"])
    path = shutil.which(command[0]) or ""
    version = machine.version_line(command) if path else ""
    if path and version:
        shown = " ".join([path, *command[1:]])
        lines = [found("pandoc", "Pandoc", version, shown, where or "PATH")]
        other = machine.recorded("pandoc")
        if where == "PATH" and other and Path(other).resolve() != Path(path).resolve():
            if (other_version := machine.version_line([other])) and other_version != version:
                lines.append(item("pandoc", "note", f"PATH has {version}, but the machine file records {other_version} "
                                        f"({other}); trazo runs the one on PATH, apps opened from the "
                                        "Dock or Finder may run the other",
                                  f"run {setup_command()} to record the one on PATH"))
        return lines, True, version
    if where == "project setting":
        return [item("pandoc", "missing", f"commands.pandoc runs {' '.join(command)}, which " +
                     ("does not run" if path else "is not found"),
                     "fix commands.pandoc in trazo.toml, or remove it to use trazo's Pandoc")], False, ""
    return [item("pandoc", "missing", "Pandoc not found", pandoc_fix())], False, ""


def check_chrome() -> list[dict[str, str]]:
    if os.environ.get("TRAZO_CHROME"):
        path, where = PROJECT.chrome(), "TRAZO_CHROME"
    elif PROJECT.config["browser"]["chrome"] or PROJECT.config["diagrams"]["chrome"]:
        path, where = PROJECT.chrome(), "project setting"
    else:
        path, where = machine.find_chrome()
    if not path:
        return [item("chrome", "note", machine.CHROME_MISSING, chrome_fix())]
    if not Path(path).is_file() and not shutil.which(path):
        return [item("chrome", "note", f"Chrome is set to {path} ({where}), which is not found",
                     "fix browser.chrome in trazo.toml or $TRAZO_CHROME")]
    if os.name == "nt":  # chrome.exe --version opens a browser window instead of printing
        return [found("chrome", "Chrome", "", path, where)]
    if not (version := machine.version_line([path])):
        return [item("chrome", "note", f"Chrome at {path} ({where}) {machine.CHROME_BROKEN}",
                     machine.CHROME_BROKEN_FIX)]
    return [found("chrome", "Chrome", version, path, where)]


def checks() -> dict:
    """The report as data: `ok` (every required tool was found), `tools`
    (one item per report line, in order), the data folder and machine.json
    (None when there is none)."""
    tools = []
    uv, uv_where = machine.resolve_tool("uv")
    uv_version = machine.version_line([uv]) if uv else ""
    if uv_version:
        tools.append(found("uv", "uv", uv_version, uv, uv_where))
    else:
        tools.append(item("uv", "missing", "uv not found: it runs the project's scripts",
                          "ask the agent to set trazo up, or install uv "
                          "(https://docs.astral.sh/uv/getting-started/installation/)"))
    tools.append(found("python", "Python", f"Python {sys.version.split()[0]}", *machine.python()))
    pandoc_lines, pandoc_ok, _ = check_pandoc()
    tools += [*pandoc_lines, *check_chrome()]
    data = machine.read()
    return {"ok": bool(uv_version) and pandoc_ok, "tools": tools,
            "data_folder": str(machine.data_dir()),
            "machine": {"written": data.get("written", "?"), "trazo": data.get("trazo", "?")} if data else None}


def report(result: dict | None = None) -> tuple[list[str], bool]:
    """The report's lines and whether every required tool was found."""
    result = result or checks()
    lines = ["Tools", *(line(entry) for entry in result["tools"]), f"Data folder: {result['data_folder']}"]
    if data := result["machine"]:
        lines.append(f"  machine.json written {data['written']} by trazo {data['trazo']}")
    else:
        lines.append("  no machine.json yet (trazo setup writes it)")
    return lines, result["ok"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="trazo doctor", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)
    result = checks()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1
    lines, ok = report(result)
    print("\n".join(lines))
    print("\nEverything required is found." if ok else "\nFix the items marked with a cross, then run trazo doctor again.")
    return 0 if ok else 1
