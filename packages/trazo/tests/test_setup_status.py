"""setup_status in process, against fake data folders and a fake plugin
root (test_mcp.py calls it through the server)."""

import os
import shutil
import unittest
from unittest.mock import patch

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo is imported)
from test_machine import ToolFolderCase, fake_tool

import trazo
from trazo import machine, mcp
from trazo.project import DEFAULTS, Project, merge


@unittest.skipIf(os.name == "nt", "fake tools are shell scripts")
class SetupStatusTests(ToolFolderCase):
    """setup_status against fake data folders and a fake plugin root."""

    def setUp(self):
        super().setUp()
        self.plugin = self.folder / "plugin root"
        (self.plugin / ".claude-plugin").mkdir(parents=True)
        (self.plugin / ".claude-plugin" / "plugin.json").write_text('{"name": "trazo", "version": "0.5.0"}')
        for patcher in (patch.dict(os.environ, {"TRAZO_PLUGIN_ROOT": str(self.plugin), "CLAUDE_PLUGIN_ROOT": "",
                                                "TRAZO_CHROME": ""}),
                        patch.object(machine, "CHROME_PATHS", ())):
            patcher.start()
            self.addCleanup(patcher.stop)

    def install(self, version):
        """A venv with trazo VERSION, as plugin/scripts/setup.sh leaves it."""
        venv = self.data / "venv"
        shutil.rmtree(venv / "lib", ignore_errors=True)
        (venv / "lib" / "python3.12" / "site-packages" / f"trazo-{version}.dist-info").mkdir(parents=True)
        if not (venv / "bin" / "trazo").exists():
            (venv / "bin").mkdir(parents=True)
            fake_tool(venv / "bin", "trazo", "trazo")

    def test_not_set_up(self):
        status = mcp.setup_status({})
        self.assertEqual(status["install"], {"folder": str(self.data), "installed": False, "version": None,
                                             "matches_plugin": None})
        self.assertEqual(status["plugin"], {"root": str(self.plugin), "version": "0.5.0"})
        self.assertEqual(status["uv"], {"found": False, "path": None, "where": None})
        self.assertFalse(status["pandoc"]["found"])
        self.assertEqual(status["chrome"], {"found": False, "path": None, "where": None})
        self.assertEqual((status["machine"], status["next"]), (None, "setup"))

    def test_a_folder_that_is_not_a_paper(self):
        # The chat's folder before a paper exists: the report, not an error.
        self.assertEqual(mcp.setup_status({"project": str(self.folder)}), mcp.setup_status({}))
        with self.assertRaises(mcp.ToolError):
            mcp.setup_status({"project": "relative/folder"})

    def test_update_then_ok(self):
        self.install("0.4.0")
        (self.data / "uv").mkdir()
        uv = fake_tool(self.data / "uv", "uv", "uv 0.13.5")
        pandoc = self.install_pandoc()
        chrome = fake_tool(self.bin, "chromium", "Chromium 140")
        written = machine.write({})["written"]
        status = mcp.setup_status({})
        self.assertEqual(status["install"], {"folder": str(self.data), "installed": True, "version": "0.4.0",
                                             "matches_plugin": False})
        self.assertEqual(status["uv"], {"found": True, "path": str(uv), "where": "data folder"})
        self.assertEqual(status["pandoc"], {"found": True, "path": str(pandoc), "command": [str(pandoc)],
                                            "where": "data folder"})
        self.assertEqual(status["chrome"], {"found": True, "path": str(chrome), "where": "PATH"})
        self.assertEqual(status["machine"], {"written": written, "trazo": trazo.__version__})
        self.assertEqual(status["next"], "update")

        self.install("0.5.0")
        status = mcp.setup_status({})
        self.assertEqual((status["install"]["version"], status["install"]["matches_plugin"]), ("0.5.0", True))
        self.assertEqual(status["next"], "ok")

        # Missing Pandoc means setup again, whatever the versions.
        shutil.rmtree(pandoc.parent)
        self.assertEqual(mcp.setup_status({})["next"], "setup")

    def test_an_older_plugin_says_plugin(self):
        # Another app's newer plugin installed 0.10.0; setup from this 0.5.0 plugin would downgrade it.
        self.install("0.10.0")
        # Also when the other app's setup stopped half way.
        self.assertEqual(mcp.setup_status({})["next"], "plugin")
        (self.data / "uv").mkdir()
        fake_tool(self.data / "uv", "uv", "uv 0.13.5")
        self.install_pandoc()
        machine.write({})
        status = mcp.setup_status({})
        self.assertEqual((status["install"]["matches_plugin"], status["next"]), (False, "plugin"))
        # A version it cannot order is offered setup, as before.
        self.install("0.5.0rc1")
        self.assertEqual(mcp.setup_status({})["next"], "update")

    def test_release_key(self):
        self.assertEqual(machine.release_key("0.10.1"), (0, 10, 1))
        self.assertLess(machine.release_key("0.9.9"), machine.release_key("0.10"))
        for version in ("", "0.10.0rc1", "v0.10.0", "0.10.0.dev3", "1..2", None):
            self.assertIsNone(machine.release_key(version), version)

    def test_two_dist_info_folders_are_broken(self):
        self.install("0.5.0")
        (self.data / "venv" / "lib" / "python3.12" / "site-packages" / "trazo-0.10.0.dist-info").mkdir()
        self.assertEqual(machine.installed_version(), "")
        self.assertEqual(mcp.setup_status({})["next"], "setup")

    def test_uv_missing(self):
        self.install("0.5.0")
        self.install_pandoc()
        machine.write({})
        status = mcp.setup_status({})
        self.assertEqual((status["install"]["matches_plugin"], status["uv"]["found"]), (True, False))
        self.assertEqual(status["next"], "setup")

    def test_a_broken_project_pandoc_is_not_next(self):
        self.install("0.5.0")
        (self.data / "uv").mkdir()
        fake_tool(self.data / "uv", "uv", "uv 0.13.5")
        self.install_pandoc()
        machine.write({})
        project = Project(self.folder, merge(DEFAULTS, {"commands": {"pandoc": ["no-such-pandoc"]}}), True)
        with patch.object(mcp, "load", return_value=project):
            status = mcp.setup_status({})
        self.assertEqual(status["pandoc"], {"found": False, "path": None, "command": ["no-such-pandoc"],
                                            "where": None})
        self.assertEqual(status["next"], "ok")

    def test_unknown_plugin_version(self):
        self.install("0.4.0")
        (self.data / "uv").mkdir()
        fake_tool(self.data / "uv", "uv", "uv 0.13.5")
        fake_tool(self.bin, "pandoc", "pandoc 3.9.0.2")
        machine.write({})
        with patch.dict(os.environ, {"TRAZO_PLUGIN_ROOT": ""}):
            status = mcp.setup_status({})
        self.assertEqual(status["plugin"], {"root": None, "version": None})
        self.assertIsNone(status["install"]["matches_plugin"])
        self.assertEqual(status["pandoc"]["where"], "PATH")
        self.assertEqual(status["next"], "ok")


if __name__ == "__main__":
    unittest.main()
