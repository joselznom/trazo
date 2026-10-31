"""The per-user data folder and its machine file.

The data folder holds what `trazo setup` installs (Pandoc, and Chrome when none
is installed) and what the
plugin's setup script installs (uv, Python, trazo's own environment):

    macOS    ~/Library/Application Support/trazo
    Windows  %LOCALAPPDATA%\\trazo
    Linux    $XDG_DATA_HOME/trazo (~/.local/share/trazo)

$TRAZO_DATA_DIR replaces it (tests, or a second setup); make it an
absolute path, since the plugin's shell scripts do not expand `~`. machine.json in
it records where uv, Python, Pandoc and Chrome (or Edge, Chromium) were
found and the first line of their version output:

    {"trazo": "0.5.0", "written": "2026-10-22T10:00:00+00:00",
     "tools": {"pandoc": {"path": "/.../pandoc", "version": "pandoc 3.9.0.2",
                          "found": "PATH"}, ...}}

`found` is "PATH", "data folder", "known location" or, for Python run
from elsewhere than the data folder's venv, "running Python (may be a uv
cache)"; a tool not found has empty strings. A file that is not shaped
like this counts as missing, with one warning. Machine paths are not
project settings: a project overrides them with commands.pandoc and
browser.chrome (trazo.project).
"""

from __future__ import annotations

import functools
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .project import CHROME_COMMANDS, CHROME_PATHS
from .review_storage import write_json

NAME = "trazo"
DATA_ENV = "TRAZO_DATA_DIR"
MACHINE_FILE = "machine.json"
TOOLS = ("uv", "python", "pandoc", "chrome")
DEFAULT_PANDOC = ["pandoc"]
# Where Homebrew puts Pandoc; a GUI-launched app's PATH lacks these folders.
PANDOC_LOCATIONS = ("/opt/homebrew/bin/pandoc", "/usr/local/bin/pandoc")
CHROME_MISSING = ("Chrome, Edge or Chromium not found: diagram export, the diagram editor, "
                  "preview PNG/PDF exports and `scripts/trazo verify` are off")
CHROME_FIX = ("install Google Chrome (https://www.google.com/chrome/) or Microsoft Edge "
              "(https://www.microsoft.com/edge), or set browser.chrome in trazo.toml")
_warned: set[str] = set()


def data_dir() -> Path:
    if os.environ.get(DATA_ENV):
        return Path(os.environ[DATA_ENV]).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / NAME
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / NAME
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / NAME


def machine_path() -> Path:
    return data_dir() / MACHINE_FILE


def read() -> dict[str, Any]:
    """machine.json, or {} when it is missing, unreadable or malformed
    (a malformed file is reported once on stderr)."""
    path = machine_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    tools = data.get("tools", {}) if isinstance(data, dict) else None
    if not isinstance(tools, dict) or not all(isinstance(entry, dict) for entry in tools.values()):
        if str(path) not in _warned:
            _warned.add(str(path))
            print(f"trazo: {path} is malformed and was ignored; trazo setup writes it again",
                  file=sys.stderr, flush=True)
        return {}
    return data


def recorded(tool: str) -> str:
    """The path machine.json records for a tool, if that file still exists."""
    path = read().get("tools", {}).get(tool, {}).get("path", "")
    return path if isinstance(path, str) and path and Path(path).is_file() else ""


@functools.cache
def machine_name() -> str:
    """platform.machine(), except "arm64" for an x86_64 process under Rosetta
    on Apple silicon, so downloads match the hardware."""
    name = platform.machine()
    if sys.platform == "darwin" and name == "x86_64":
        try:
            result = subprocess.run(["/usr/sbin/sysctl", "-n", "sysctl.proc_translated"],
                                    capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return name
        if result.stdout.strip() == "1":
            return "arm64"
    return name


def run_options() -> dict[str, Any]:
    """No console window for subprocesses on Windows."""
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def version_line(command: list[str]) -> str:
    """The first line `command --version` prints, or "" when it cannot run."""
    try:
        result = subprocess.run([*command, "--version"], capture_output=True, timeout=30, **run_options())
    except (OSError, subprocess.SubprocessError):
        return ""
    lines = (result.stdout or result.stderr).decode("utf-8", errors="replace").strip().splitlines()
    return lines[0].strip() if result.returncode == 0 and lines else ""


def executable(name: str) -> str:
    return f"{name}.exe" if os.name == "nt" else name


def find_chrome() -> tuple[str, str]:
    """(path, where) of Chrome, Edge or Chromium at a known location or on PATH,
    else of the headless Chrome `trazo setup` unpacked into the data folder."""
    for candidate in CHROME_PATHS:
        if Path(candidate).exists():
            return candidate, "known location"
    for command in CHROME_COMMANDS:
        if found := shutil.which(command):
            return found, "PATH"
    return (path, "data folder") if (path := installed_chrome()) else ("", "")


def data_uv() -> str:
    """The uv the plugin's setup script unpacked into the data folder, if any."""
    path = data_dir() / "uv" / executable("uv")
    return str(path) if path.is_file() else ""


def uv_environment() -> dict[str, str]:
    """Settings that keep the data folder's uv inside the data folder (its
    Python, cache and tools), the same ones plugin/scripts/setup.sh exports;
    a project's `uv sync` then reuses the Python setup installed."""
    data = data_dir()
    return {DATA_ENV: str(data), "UV_CACHE_DIR": str(data / "cache"),
            "UV_PYTHON_INSTALL_DIR": str(data / "python"), "UV_PYTHON_BIN_DIR": str(data / "bin"),
            "UV_TOOL_DIR": str(data / "tools"), "UV_TOOL_BIN_DIR": str(data / "bin"),
            "UV_MANAGED_PYTHON": "1"}


def python() -> tuple[str, str]:
    """(path, where) of the Python trazo runs on: the data folder's venv
    when trazo runs from it, else sys.executable."""
    venv = data_dir() / "venv"
    if Path(sys.prefix).resolve() == venv.resolve():
        return str(venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")), "data folder"
    return sys.executable, "running Python (may be a uv cache)"


def installed_version() -> str:
    """The trazo version installed in the data folder's venv, read from
    its trazo-VERSION.dist-info folder name (no Python is started, the
    same rule plugin/session-start.sh follows); "" when there is none or
    more than one (a broken install)."""
    venv = data_dir() / "venv"
    if not (venv / ("Scripts/trazo.exe" if os.name == "nt" else "bin/trazo")).is_file():
        return ""
    pattern = "Lib/site-packages/trazo-*.dist-info" if os.name == "nt" else "lib/python*/site-packages/trazo-*.dist-info"
    found = [path.name for path in venv.glob(pattern) if path.is_dir()]
    return found[0].removeprefix("trazo-").removesuffix(".dist-info") if len(found) == 1 else ""


def plugin_version() -> tuple[str, str]:
    """(plugin root, version in its .claude-plugin/plugin.json); "" for what is unknown."""
    root = os.environ.get("TRAZO_PLUGIN_ROOT") or os.environ.get("CLAUDE_PLUGIN_ROOT") or ""
    if not root:
        return "", ""
    try:
        version = json.loads((Path(root) / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError, TypeError):
        return root, ""
    return root, version if isinstance(version, str) else ""


def release_key(version: str | None) -> tuple[int, ...] | None:
    """A release version ("0.10.1": digits and dots) as numbers to order it;
    None for anything else (a pre-release, a dev build), which is not
    ordered. plugin/session-start.sh's `newer` follows the same rule."""
    if not isinstance(version, str) or not re.fullmatch(r"\d+(\.\d+)*", version):
        return None
    return tuple(int(part) for part in version.split("."))


def installed_pandoc() -> str:
    """The Pandoc `trazo setup` unpacked into the data folder, if any."""
    from .tool_setup import pandoc_asset, pandoc_binary
    asset = pandoc_asset()
    path = pandoc_binary(asset) if asset else None
    return str(path) if path and path.is_file() else ""


def installed_chrome() -> str:
    """The headless Chrome `trazo setup` unpacked into the data folder, if any."""
    from .tool_setup import chrome_asset, chrome_binary
    asset = chrome_asset()
    path = chrome_binary(asset) if asset else None
    return str(path) if path and path.is_file() else ""


def known_pandoc() -> str:
    """A Pandoc at a fixed location off a GUI app's PATH (Homebrew), if any."""
    return next((path for path in PANDOC_LOCATIONS if Path(path).is_file()), "") if os.name != "nt" else ""


def detect() -> dict[str, dict[str, str]]:
    """Each tool's path, version line and where it was found."""
    tools: dict[str, dict[str, str]] = {}
    candidates = {
        "uv": [(data_uv(), "data folder"), (shutil.which("uv") or "", "PATH")],
        "python": [python()],
        "pandoc": [(shutil.which("pandoc") or "", "PATH"), (known_pandoc(), "known location"),
                   (installed_pandoc(), "data folder")],
        "chrome": [find_chrome()],
    }
    for tool, found in candidates.items():
        path, where = next(((p, w) for p, w in found if p), ("", ""))
        if tool == "python":
            version = f"Python {platform.python_version()}"
        elif tool == "chrome" and os.name == "nt":
            version = ""  # chrome.exe --version opens a browser window instead of printing
        else:
            version = version_line([path]) if path else ""
        tools[tool] = {"path": path, "version": version, "found": where if path else ""}
    return tools


def write(tools: dict[str, dict[str, str]] | None = None) -> dict[str, Any]:
    """Detect the tools (unless given) and write machine.json."""
    data = {"trazo": __version__, "written": datetime.now(UTC).isoformat(timespec="seconds"),
            "tools": tools if tools is not None else detect()}
    write_json(machine_path(), data, indent=2)
    return data


def resolve_tool(tool: str) -> tuple[str, str]:
    """(full path, where) of uv or Pandoc: PATH, then the data folder, then
    the path machine.json records; ("", "") when none is found."""
    fixed = {"uv": data_uv, "pandoc": installed_pandoc}[tool]
    for path, where in ((shutil.which(tool) or "", "PATH"), (fixed(), "data folder"),
                        (recorded(tool), "machine file")):
        if path:
            return path, where
    return "", ""


def resolve_pandoc(configured: list[str]) -> tuple[list[str], str]:
    """The Pandoc command to run and where it came from.

    A project's own commands.pandoc runs as written ("project setting");
    the default ["pandoc"] runs from PATH ("PATH") and, when PATH has none,
    as the full path of the Pandoc `trazo setup` unpacked ("data folder")
    or that machine.json records ("machine file"). Where is "" when no
    Pandoc is found; the command is then the default, so running it fails
    as before."""
    if list(configured) != DEFAULT_PANDOC:
        return list(configured), "project setting"
    path, where = resolve_tool("pandoc")
    if where == "PATH":
        return list(configured), where
    return ([path], where) if path else (list(configured), "")
