"""Whether a newer trazo is out, and the plugin's update through the app's own CLI.

The latest release is the version in `.claude-plugin/plugin.json` on the
`main` branch of the repository the plugin's own plugin.json names
(`repository`, a github.com URL): `main` moves only at a release, and it
is what both apps install. setup_status asks every time it runs and waits
TIMEOUT at most; a plugin.json without a github.com repository (a test's) is
not checked, and an unanswered request leaves the version unknown.

An app installs the plugin into <config>/plugins/cache/<marketplace>/<plugin>/<version>.
Claude's config folder holds plugins/installed_plugins.json, Codex's
config.toml; plugin_update runs that app's CLI on that folder:

    claude plugin marketplace update M; claude plugin update P@M --scope S
    codex plugin marketplace upgrade M; codex plugin add P@M

Third-party marketplaces do not update by themselves in the Claude app, so
without this a Claude user stays on the release first installed. The CLI is
the one on PATH, else one of CLI_LOCATIONS: an app's own copy, since a
desktop app's PATH is short. The running chat keeps the old plugin (its
MCP server, skills and hook): the new one loads in a new chat.
"""

from __future__ import annotations

import glob
import http.client
import json
import os
import re
import shutil
import subprocess
import threading
import urllib.request
from pathlib import Path
from typing import Any, NamedTuple

from . import machine

LATEST_URL = "https://raw.githubusercontent.com/{repository}/main/.claude-plugin/plugin.json"
GITHUB = re.compile(r"https://github\.com/([\w.-]+/[\w-]+(?:\.[\w-]+)*?)(?:\.git)?/?")
TIMEOUT = 3  # seconds setup_status waits, name lookup included
CLI_TIMEOUT = 300
# Where an app keeps a CLI it does not put on PATH; globs, newest version first.
CLI_LOCATIONS: dict[str, tuple[str, ...]] = {
    "claude": ("~/.local/bin/claude", "~/.claude/local/claude",
               "~/Library/Application Support/Claude/claude-code/*/*/claude.app/Contents/MacOS/claude"),
    "codex": ("~/.local/bin/codex", "/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex"),
}
CONFIG_ENV = {"claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME"}
MANIFESTS = {"claude": ".claude-plugin", "codex": ".codex-plugin"}


class Install(NamedTuple):
    app: str  # "claude" or "codex"
    config: Path
    marketplace: str
    plugin: str


def latest_release(root: str) -> str:
    """The latest release's version; "" when unknown."""
    repository = machine.plugin_manifest(root).get("repository")
    match = GITHUB.fullmatch(repository) if isinstance(repository, str) else None
    if not match:
        return ""
    url, found = LATEST_URL.format(repository=match[1]), []

    def fetch() -> None:
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT) as reply:
                found.append(json.loads(reply.read(1 << 16))["version"])
        except (OSError, http.client.HTTPException, ValueError, KeyError, TypeError):
            pass
    # urlopen's timeout leaves out the name lookup, which can hang far longer on a bad network.
    thread = threading.Thread(target=fetch, daemon=True)
    thread.start()
    thread.join(TIMEOUT)
    version = found[0] if found else ""
    return version if machine.release_key(version) else ""


def app_install(root: str) -> Install | None:
    """Which app installed the plugin at root, from its cache's layout; None
    for a checkout or a copy outside an app's cache."""
    path = Path(root)
    if len(path.parts) < 6 or path.parents[2].name != "cache" or path.parents[3].name != "plugins":
        return None
    config = path.parents[4]
    if (config / "plugins" / "installed_plugins.json").is_file():
        app = "claude"
    elif (config / "config.toml").is_file():
        app = "codex"
    else:
        return None
    return Install(app, config, path.parents[1].name, path.parent.name)


def find_cli(app: str) -> str:
    if on_path := shutil.which(app):
        return on_path
    for pattern in CLI_LOCATIONS[app]:
        found = [path for path in glob.glob(os.path.expanduser(pattern)) if os.access(path, os.X_OK)]
        if found:  # the newest: compare the version folders in the paths
            return max(found, key=lambda path: [machine.release_key(part) or (-1,) for part in Path(path).parts])
    return ""


def claude_entries(install: Install, match: dict[str, Any]) -> list[dict[str, Any]]:
    """The installed_plugins.json entries of this plugin whose keys match `match`."""
    try:
        listing = json.loads((install.config / "plugins" / "installed_plugins.json").read_text(encoding="utf-8"))
        entries = listing["plugins"][f"{install.plugin}@{install.marketplace}"]
        return [entry for entry in entries if all(entry.get(k) == v for k, v in match.items())]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return []


def last_line(result: subprocess.CompletedProcess[str]) -> str:
    lines = (result.stderr or result.stdout or "").strip().splitlines()
    return lines[-1] if lines else f"exit code {result.returncode}"


def plugin_update(args: dict[str, Any]) -> dict[str, Any]:
    """Update the plugin with the CLI of the app that installed it. `ok`,
    `app` ("claude", "codex" or None), `from` (this plugin's version) and
    then `version`, or `error`."""
    root, version = machine.plugin_version()
    install = app_install(root) if root else None
    reply: dict[str, Any] = {"ok": False, "app": install.app if install else None, "from": version or None}
    if install is None:
        return {**reply, "error": "this plugin was not installed by the Claude or ChatGPT app"}
    cli = find_cli(install.app)
    if not cli:
        return {**reply, "error": f"the {install.app} command was not found"}
    ref = f"{install.plugin}@{install.marketplace}"
    cwd = Path.home()
    if install.app == "claude":
        # The scope it was installed in (a project's runs in that project), found again after the update.
        # Scopes at one version share the cache folder: with several, which one this chat runs is unknown.
        installs = claude_entries(install, {"installPath": root}) or [{"scope": "user"}]
        if len(installs) > 1:
            return {**reply, "error": "trazo is installed in more than one Claude scope"}
        scope: dict[str, Any] = {key: installs[0][key] for key in ("scope", "projectPath") if key in installs[0]}
        cwd = Path(scope.get("projectPath") or cwd)
        steps = [["plugin", "marketplace", "update", install.marketplace],
                 ["plugin", "update", ref, "--scope", scope["scope"]]]
    else:
        scope = {}
        steps = [["plugin", "marketplace", "upgrade", install.marketplace], ["plugin", "add", ref]]
    env = {**os.environ, CONFIG_ENV[install.app]: str(install.config)}
    for step in steps:
        try:
            result = subprocess.run([cli, *step], cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                                    text=True, timeout=CLI_TIMEOUT, **machine.no_window())
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {**reply, "error": f"{install.app} {' '.join(step)}: {exc}"}
        if result.returncode:
            return {**reply, "error": last_line(result)}
    if install.app == "claude":
        new_root = next(iter(claude_entries(install, scope)), {}).get("installPath", "")
    else:
        found = re.search(r"Installed plugin root: (.+)", result.stdout)
        new_root = found[1].strip() if found else ""
    new = machine.plugin_manifest(new_root, MANIFESTS[install.app]).get("version") if new_root else None
    return {**reply, "ok": True, "version": new if isinstance(new, str) else None}
