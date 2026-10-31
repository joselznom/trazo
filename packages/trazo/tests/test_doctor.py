"""trazo doctor: tool paths, versions, where they were found, fixes."""

import contextlib
import io
import json
import os
import platform
import sys
import unittest
from unittest.mock import patch

import _support  # noqa: F401  (sets TRAZO_ROOT first)
from test_machine import ToolFolderCase, fake_tool

import trazo
from trazo import doctor, machine, tool_setup
from trazo.project import DEFAULTS, Project, merge


@unittest.skipIf(os.name == "nt", "fake tools are shell scripts")
class DoctorTests(ToolFolderCase):
    def setUp(self):
        super().setUp()
        for patcher in (patch.object(machine, "CHROME_PATHS", ()), patch.object(tool_setup, "CHROME_ASSETS", {}),
                        patch.dict(os.environ, {"TRAZO_CHROME": ""})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.use({})

    def use(self, config, found=True):
        patcher = patch.object(doctor, "PROJECT", Project(self.folder, merge(DEFAULTS, config), found))
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_doctor(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = doctor.main([])
        return code, out.getvalue()

    def test_every_tool_found(self):
        uv = fake_tool(self.bin, "uv", "uv 0.13.5")
        pandoc = fake_tool(self.bin, "pandoc", "pandoc 3.9.0.2")
        chrome = fake_tool(self.bin, "chromium", "Chromium 140.0")
        machine.write()
        code, out = self.run_doctor()
        self.assertEqual(code, 0, out)
        self.assertIn(f"✓ uv 0.13.5: {uv} (PATH)", out)
        self.assertIn(f"✓ Python {sys.version.split()[0]}: {sys.executable} (running Python (may be a uv cache))", out)
        self.assertIn(f"✓ pandoc 3.9.0.2: {pandoc} (PATH)", out)
        self.assertNotIn("pandoc-crossref", out)
        self.assertIn(f"✓ Chromium 140.0: {chrome} (PATH)", out)
        self.assertIn(f"Data folder: {self.data}\n  machine.json written ", out)
        self.assertIn("Everything required is found.", out)

    def test_downloaded_chrome(self):
        fake_tool(self.bin, "uv", "uv 0.13.5")
        fake_tool(self.bin, "pandoc", "pandoc 3.9.0.2")
        chrome = self.install_chrome()
        code, out = self.run_doctor()
        self.assertEqual(code, 0, out)
        self.assertIn(f"✓ Google Chrome for Testing 156.0.0.0: {chrome} (data folder)", out)
        # Without a desktop's libraries (Linux) it is found but does not start: say so, not ✓.
        chrome.write_text("#!/bin/sh\nexit 127\n")
        code, out = self.run_doctor()
        self.assertEqual(code, 0, out)
        self.assertIn(f"! Chrome at {chrome} (data folder) {machine.CHROME_BROKEN}\n"
                      f"      fix: {machine.CHROME_BROKEN_FIX}", out)

    def test_missing_chrome_says_to_download_it_where_trazo_can(self):
        asset = tool_setup.Asset("chrome-headless-shell-test.zip", 1, "0" * 64, "chrome-x/chrome-headless-shell", 1)
        with patch.object(tool_setup, "CHROME_ASSETS", {(platform.system(), machine.machine_name()): asset}):
            self.assertEqual(doctor.check_chrome()[0]["fix"],
                             f"run trazo setup: it downloads Chrome {tool_setup.CHROME_VERSION} into {self.data}")
        self.assertEqual(doctor.check_chrome()[0]["fix"], machine.CHROME_FIX)

    def test_text_and_json_snapshot(self):
        # Fixed expected output, so a change of wording or fields is noticed.
        self.use({})
        uv = fake_tool(self.bin, "uv", "uv 0.13.5")
        pandoc = fake_tool(self.bin, "pandoc", "pandoc 3.9.0.2")
        written = machine.write({})["written"]
        python = sys.version.split()[0]
        code, out = self.run_doctor()
        self.assertEqual(code, 0, out)
        self.assertEqual(out, f"""Tools
  ✓ uv 0.13.5: {uv} (PATH)
  ✓ Python {python}: {sys.executable} (running Python (may be a uv cache))
  ✓ pandoc 3.9.0.2: {pandoc} (PATH)
  ! Chrome, Edge or Chromium not found: diagram export, the diagram editor, preview PNG/PDF exports and `scripts/trazo verify` are off
      fix: install Google Chrome (https://www.google.com/chrome/) or Microsoft Edge (https://www.microsoft.com/edge), or set browser.chrome in trazo.toml
Data folder: {self.data}
  machine.json written {written} by trazo {trazo.__version__}

Everything required is found.
""")
        json_out = io.StringIO()
        with contextlib.redirect_stdout(json_out):
            self.assertEqual(doctor.main(["--json"]), 0)
        self.assertEqual(json.loads(json_out.getvalue()), {
            "ok": True,
            "tools": [
                {"tool": "uv", "status": "ok", "message": f"uv 0.13.5: {uv} (PATH)", "fix": "",
                 "path": str(uv), "version": "uv 0.13.5", "found": "PATH"},
                {"tool": "python", "status": "ok",
                 "message": f"Python {python}: {sys.executable} (running Python (may be a uv cache))", "fix": "",
                 "path": sys.executable, "version": f"Python {python}", "found": "running Python (may be a uv cache)"},
                {"tool": "pandoc", "status": "ok", "message": f"pandoc 3.9.0.2: {pandoc} (PATH)", "fix": "",
                 "path": str(pandoc), "version": "pandoc 3.9.0.2", "found": "PATH"},
                {"tool": "chrome", "status": "note",
                 "message": "Chrome, Edge or Chromium not found: diagram export, the diagram editor, "
                            "preview PNG/PDF exports and `scripts/trazo verify` are off",
                 "fix": "install Google Chrome (https://www.google.com/chrome/) or Microsoft Edge "
                        "(https://www.microsoft.com/edge), or set browser.chrome in trazo.toml"},
            ],
            "data_folder": str(self.data),
            "machine": {"written": written, "trazo": trazo.__version__},
        })

    def test_nothing_found(self):
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        self.assertIn("✗ uv not found", out)
        self.assertIn("✗ Pandoc not found", out)
        if doctor.pandoc_fix().startswith("run trazo setup"):
            self.assertIn(f"fix: run trazo setup: it downloads Pandoc 3.9.0.2 into {self.data}", out)
        self.assertIn("! Chrome, Edge or Chromium not found: diagram export, the diagram editor, "
                      "preview PNG/PDF exports and `scripts/trazo verify` are off\n"
                      "      fix: install Google Chrome (https://www.google.com/chrome/)", out)
        self.assertIn("no machine.json yet (trazo setup writes it)", out)
        self.assertIn("Fix the items marked with a cross", out)

    def test_machine_file_tools(self):
        self.use({})
        tools = self.folder / "tools"
        tools.mkdir()
        uv, pandoc = fake_tool(tools, "uv", "uv 0.13.5"), fake_tool(tools, "pandoc", "pandoc 3.9.0.2")
        machine.write({"uv": {"path": str(uv)}, "pandoc": {"path": str(pandoc)}})
        code, out = self.run_doctor()
        self.assertEqual(code, 0, out)
        self.assertIn(f"uv 0.13.5: {uv} (machine file)", out)
        self.assertIn(f"pandoc 3.9.0.2: {pandoc} (machine file)", out)
        self.assertNotIn("pandoc-crossref", out)

    def test_project_settings(self):
        pandoc = fake_tool(self.folder, "my-pandoc", "pandoc 3.1")
        chrome = fake_tool(self.folder, "my-chrome", "Chrome 1")
        fake_tool(self.bin, "uv", "uv 0.13.5")
        self.use({"commands": {"pandoc": [str(pandoc), "--data-dir=x"]}, "browser": {"chrome": str(chrome)}})
        code, out = self.run_doctor()
        self.assertEqual(code, 0, out)
        self.assertIn(f"pandoc 3.1: {pandoc} --data-dir=x (project setting)", out)
        self.assertIn(f"Chrome 1: {chrome} (project setting)", out)
        self.use({"commands": {"pandoc": ["no-such-pandoc"]}})
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        self.assertIn("✗ commands.pandoc runs no-such-pandoc, which is not found", out)

    def test_outside_a_project_setup_is_not_named_as_scripts_trazo(self):
        self.use({}, found=False)
        fake_tool(self.bin, "uv", "uv 0.13.5")
        fake_tool(self.bin, "pandoc", "pandoc 3.9.0.2")
        code, out = self.run_doctor()
        self.assertEqual(code, 0, out)
        self.assertNotIn("pandoc-crossref", out)
        self.assertNotIn("scripts/trazo setup", out)

    def test_data_folder_tools_without_a_machine_file(self):
        # setup.sh unpacked uv and trazo setup unpacked Pandoc; machine.json is gone.
        self.use({})
        (self.data / "uv").mkdir(parents=True)
        uv = fake_tool(self.data / "uv", "uv", "uv 0.13.5")
        pandoc = self.install_pandoc("pandoc 3.9.0.2")
        code, out = self.run_doctor()
        self.assertEqual(code, 0, out)
        self.assertIn(f"✓ uv 0.13.5: {uv} (data folder)", out)
        self.assertIn(f"✓ pandoc 3.9.0.2: {pandoc} (data folder)", out)
        self.assertIn("no machine.json yet", out)

    def test_path_and_machine_file_pandoc_differ(self):
        self.use({})
        fake_tool(self.bin, "uv", "uv 0.13.5")
        fake_tool(self.bin, "pandoc", "pandoc 3.12.1")
        tools = self.folder / "tools"
        tools.mkdir()
        recorded = fake_tool(tools, "pandoc", "pandoc 3.9.0.2")
        machine.write({"pandoc": {"path": str(recorded)}})
        (self.folder / "scripts").mkdir()
        (self.folder / "scripts/trazo").write_text("")
        code, out = self.run_doctor()
        self.assertEqual(code, 0, out)
        self.assertIn(f"! PATH has pandoc 3.12.1, but the machine file records pandoc 3.9.0.2 ({recorded})", out)
        self.assertIn("fix: run scripts/trazo setup to record the one on PATH", out)


if __name__ == "__main__":
    unittest.main()
