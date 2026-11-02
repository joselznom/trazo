"""trazo.updates: the latest release from GitHub (the network is mocked),
and plugin_update against fake Claude and Codex config folders whose CLIs
are scripts that do what the real ones do to those folders."""

import io
import json
import os
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo is imported)
from test_machine import ToolFolderCase

from trazo import updates

FAKE_CLAUDE = """
import json, os, sys
from pathlib import Path
config = Path(os.environ["CLAUDE_CONFIG_DIR"])
with open(os.environ["CLI_LOG"], "a") as log:
    log.write(json.dumps([str(config), os.getcwd(), *sys.argv[1:]]) + "\\n")
if os.environ.get("CLI_FAIL"):
    sys.exit("error: " + os.environ["CLI_FAIL"])
if sys.argv[1:3] == ["plugin", "update"]:
    listing = config / "plugins" / "installed_plugins.json"
    data = json.loads(listing.read_text())
    for entry in data["plugins"][sys.argv[3]]:
        new = config / "plugins" / "cache" / "trazo" / "trazo" / "1.1.0"
        (new / ".claude-plugin").mkdir(parents=True, exist_ok=True)
        (new / ".claude-plugin" / "plugin.json").write_text('{"version": "1.1.0"}')
        entry.update(installPath=str(new), version="1.1.0")
    listing.write_text(json.dumps(data))
"""

FAKE_CODEX = """
import json, os, sys
from pathlib import Path
config = Path(os.environ["CODEX_HOME"])
with open(os.environ["CLI_LOG"], "a") as log:
    log.write(json.dumps([str(config), os.getcwd(), *sys.argv[1:]]) + "\\n")
if sys.argv[1:3] == ["plugin", "add"]:
    new = config / "plugins" / "cache" / "trazo" / "trazo" / "1.1.0"
    (new / ".codex-plugin").mkdir(parents=True, exist_ok=True)
    (new / ".codex-plugin" / "plugin.json").write_text('{"version": "1.1.0"}')
    print("Installed plugin trazo@trazo")
    print(f"Installed plugin root: {new}")
"""


def reply(body: bytes) -> io.BytesIO:
    return io.BytesIO(body)  # what urlopen's reply offers here: read() and `with`


class LatestReleaseTests(ToolFolderCase):
    def plugin(self, manifest: dict) -> str:
        root = self.folder / "plugin"
        (root / ".claude-plugin").mkdir(parents=True)
        (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "trazo", **manifest}))
        return str(root)

    def test_reads_main_of_the_repository_the_plugin_names(self):
        root = self.plugin({"version": "1.0.0", "repository": "https://github.com/someone/trazo"})
        with patch.object(updates.urllib.request, "urlopen", return_value=reply(b'{"version": "1.2.0"}')) as get:
            self.assertEqual(updates.latest_release(root), "1.2.0")
        self.assertEqual(get.call_args.args[0],
                         "https://raw.githubusercontent.com/someone/trazo/main/.claude-plugin/plugin.json")

    def test_unknown_without_a_github_repository(self):
        for manifest in ({}, {"repository": "https://gitlab.com/someone/trazo"}, {"repository": 3}):
            with patch.object(updates.urllib.request, "urlopen") as get:
                self.assertEqual(updates.latest_release(self.plugin(manifest)), "")
            get.assert_not_called()
            (self.folder / "plugin" / ".claude-plugin" / "plugin.json").unlink()
            (self.folder / "plugin" / ".claude-plugin").rmdir()

    def test_unknown_offline_or_on_a_bad_reply(self):
        root = self.plugin({"repository": "https://github.com/someone/trazo.git"})
        for outcome in (urllib.error.URLError("offline"), TimeoutError(), reply(b"<html>"),
                        reply(b'{"version": "1.2.0rc1"}'), reply(b"[]")):
            mock = {"side_effect": outcome} if isinstance(outcome, Exception) else {"return_value": outcome}
            with patch.object(updates.urllib.request, "urlopen", **mock):
                self.assertEqual(updates.latest_release(root), "", outcome)


@unittest.skipIf(os.name == "nt", "the fake CLIs are shell scripts")
class PluginUpdateTests(ToolFolderCase):
    def setUp(self):
        super().setUp()
        self.log = self.folder / "cli.log"
        patcher = patch.dict(os.environ, {"CLI_LOG": str(self.log), "CLI_FAIL": "", "CLAUDE_PLUGIN_ROOT": ""})
        patcher.start()
        self.addCleanup(patcher.stop)

    def cli(self, folder: Path, name: str, source: str) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{name}.py").write_text(source)
        path = folder / name
        path.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{folder / name}.py" "$@"\n')
        path.chmod(0o755)
        return path

    def app_plugin(self, config: Path, manifest_folder: str) -> Path:
        root = config / "plugins" / "cache" / "trazo" / "trazo" / "1.0.0"
        (root / manifest_folder).mkdir(parents=True)
        (root / ".claude-plugin").mkdir(exist_ok=True)
        (root / ".claude-plugin" / "plugin.json").write_text('{"version": "1.0.0"}')
        os.environ["TRAZO_PLUGIN_ROOT"] = str(root)
        return root

    def claude(self, scope: dict | None = None) -> Path:
        config = self.folder / "claude config"
        root = self.app_plugin(config, ".claude-plugin")
        entry = {"scope": "user", "installPath": str(root), "version": "1.0.0", **(scope or {})}
        (config / "plugins" / "installed_plugins.json").write_text(
            json.dumps({"version": 2, "plugins": {"trazo@trazo": [entry]}}))
        return config

    def calls(self) -> list[list[str]]:
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_claude(self):
        config = self.claude()
        self.cli(self.bin, "claude", FAKE_CLAUDE)
        self.assertEqual(updates.plugin_update({}), {"ok": True, "app": "claude", "from": "1.0.0",
                                                     "version": "1.1.0", "new_chat": True})
        home = str(Path.home())
        self.assertEqual(self.calls(), [[str(config), home, "plugin", "marketplace", "update", "trazo"],
                                        [str(config), home, "plugin", "update", "trazo@trazo", "--scope", "user"]])

    def test_claude_project_scope_runs_in_that_project(self):
        project = self.folder / "a project"
        project.mkdir()
        self.claude({"scope": "project", "projectPath": str(project)})
        self.cli(self.bin, "claude", FAKE_CLAUDE)
        self.assertTrue(updates.plugin_update({})["ok"])
        self.assertEqual(self.calls()[1][1:], [str(project.resolve()), "plugin", "update", "trazo@trazo",
                                               "--scope", "project"])

    def test_codex(self):
        config = self.folder / "codex home"
        self.app_plugin(config, ".codex-plugin")
        (config / "config.toml").write_text('[marketplaces.trazo]\nsource_type = "git"\n')
        self.cli(self.bin, "codex", FAKE_CODEX)
        self.assertEqual(updates.plugin_update({}), {"ok": True, "app": "codex", "from": "1.0.0",
                                                     "version": "1.1.0", "new_chat": True})
        home = str(Path.home())
        self.assertEqual(self.calls(), [[str(config), home, "plugin", "marketplace", "upgrade", "trazo"],
                                        [str(config), home, "plugin", "add", "trazo@trazo"]])

    def test_the_app_bundle_cli_when_none_is_on_path(self):
        # A desktop app's PATH has no `claude`: the newest copy the Claude app keeps is used.
        self.claude()
        bundles = self.folder / "bundles"
        for version in ("2.1.9", "2.1.10"):
            self.cli(bundles / version / "abc", "claude", FAKE_CLAUDE.replace("1.1.0", version))
        with patch.dict(updates.CLI_LOCATIONS, {"claude": (str(bundles / "*" / "*" / "claude"),)}):
            self.assertEqual(updates.plugin_update({})["version"], "2.1.10")

    def test_no_cli(self):
        self.claude()
        with patch.dict(updates.CLI_LOCATIONS, {"claude": ()}):
            self.assertEqual(updates.plugin_update({}), {"ok": False, "app": "claude", "from": "1.0.0",
                                                         "error": "the claude command was not found"})

    def test_a_failing_cli(self):
        self.claude()
        self.cli(self.bin, "claude", FAKE_CLAUDE)
        os.environ["CLI_FAIL"] = "cannot reach github.com"
        result = updates.plugin_update({})
        self.assertEqual((result["ok"], result["app"], result["error"]), (False, "claude", "error: cannot reach github.com"))
        self.assertEqual(len(self.calls()), 1)  # it stops at the first step

    def test_a_plugin_no_app_installed(self):
        # A checkout of this repository, or a folder marketplace's copy outside an app's cache.
        root = self.folder / "checkout"
        (root / ".claude-plugin").mkdir(parents=True)
        (root / ".claude-plugin" / "plugin.json").write_text('{"version": "1.0.0"}')
        with patch.dict(os.environ, {"TRAZO_PLUGIN_ROOT": str(root)}):
            result = updates.plugin_update({})
        self.assertEqual((result["ok"], result["app"]), (False, None))
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
