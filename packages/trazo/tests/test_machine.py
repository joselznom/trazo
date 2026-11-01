"""The per-user data folder, machine.json and tool detection."""

import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

import _support  # noqa: F401  (sets TRAZO_ROOT first)

from trazo import build_graph, machine, pandoc_run, tool_setup
from trazo.project import DEFAULTS, Project, merge


def fake_tool(folder: Path, name: str, line: str) -> Path:
    path = folder / name
    path.write_text(f"#!/bin/sh\necho '{line}'\n")
    path.chmod(0o755)
    return path


class DataFolderTests(unittest.TestCase):
    def test_platform_folders_and_override(self):
        home = Path("/home/someone")
        with patch.dict(os.environ, {"TRAZO_DATA_DIR": "", "XDG_DATA_HOME": "", "LOCALAPPDATA": r"C:\Users\a\AppData\Local"}), \
                patch.object(Path, "home", return_value=home):
            with patch.object(sys, "platform", "darwin"):
                self.assertEqual(machine.data_dir(), home / "Library/Application Support/trazo")
            with patch.object(sys, "platform", "linux"):
                self.assertEqual(machine.data_dir(), home / ".local/share/trazo")
            with patch.object(sys, "platform", "win32"):
                self.assertEqual(machine.data_dir(), Path(r"C:\Users\a\AppData\Local") / "trazo")
        with patch.dict(os.environ, {"TRAZO_DATA_DIR": "/tmp/elsewhere"}):
            self.assertEqual(machine.data_dir(), Path("/tmp/elsewhere"))
            self.assertEqual(machine.machine_path(), Path("/tmp/elsewhere/machine.json"))

    def test_windows_chrome_and_edge_locations(self):
        if os.name == "nt":  # the real folders: a child's environment cannot replace them reliably there
            folders = [os.environ[name] for name in ("ProgramFiles", "ProgramFiles(x86)")]
            self.assertEqual(list(machine.CHROME_PATHS[3:]), [str(Path(folder) / app) for app in (
                r"Google\Chrome\Application\chrome.exe", r"Microsoft\Edge\Application\msedge.exe") for folder in folders])
            return
        env = {**os.environ, "ProgramFiles": "/pf", "ProgramFiles(x86)": "/pf86"}
        out = subprocess.run([sys.executable, "-c", "from trazo.project import CHROME_PATHS; print('\\n'.join(CHROME_PATHS))"],
                             env=env, capture_output=True, text=True, check=True).stdout.splitlines()
        self.assertEqual(out[3:], [str(Path("/pf") / r"Google\Chrome\Application\chrome.exe"),
                                   str(Path("/pf86") / r"Google\Chrome\Application\chrome.exe"),
                                   str(Path("/pf") / r"Microsoft\Edge\Application\msedge.exe"),
                                   str(Path("/pf86") / r"Microsoft\Edge\Application\msedge.exe")])


class ToolFolderCase(unittest.TestCase):
    """A data folder and a PATH of one empty folder, both temporary."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.data = self.folder / "data"
        self.bin = self.folder / "bin"
        self.bin.mkdir()
        for patcher in (patch.dict(os.environ, {"TRAZO_DATA_DIR": str(self.data), "PATH": str(self.bin)}),
                        patch.object(machine, "PANDOC_LOCATIONS", ())):  # this Mac's Homebrew
            patcher.start()
            self.addCleanup(patcher.stop)

    def install_pandoc(self, line="pandoc 3.9.0.2 (data folder)") -> Path:
        """A fake Pandoc where `trazo setup` unpacks it, for this platform."""
        asset = tool_setup.Asset("pandoc-test.zip", 1, "0" * 64, "pandoc-x/bin/pandoc", 1)
        patcher = patch.object(tool_setup, "PANDOC_ASSETS", {(platform.system(), machine.machine_name()): asset})
        patcher.start()
        self.addCleanup(patcher.stop)
        binary = tool_setup.pandoc_binary(asset)
        binary.parent.mkdir(parents=True)
        return fake_tool(binary.parent, "pandoc", line)


    def install_chrome(self, line="Google Chrome for Testing 156.0.0.0") -> Path:
        """A fake headless Chrome where `trazo setup` unpacks it, for this platform."""
        asset = tool_setup.Asset("chrome-headless-shell-test.zip", 1, "0" * 64, "chrome-x/chrome-headless-shell", 1)
        patcher = patch.object(tool_setup, "CHROME_ASSETS", {(platform.system(), machine.machine_name()): asset})
        patcher.start()
        self.addCleanup(patcher.stop)
        binary = tool_setup.chrome_binary(asset)
        binary.parent.mkdir(parents=True)
        return fake_tool(binary.parent, "chrome-headless-shell", line)


@unittest.skipIf(os.name == "nt", "fake tools are shell scripts")
class MachineFileTests(ToolFolderCase):
    def test_records_paths_versions_and_where(self):
        fake_tool(self.bin, "pandoc", "pandoc 3.9.0.2")
        (self.data / "uv").mkdir(parents=True)
        uv = fake_tool(self.data / "uv", "uv", "uv 0.13.5 (abc 2026-10-22)")
        chrome = fake_tool(self.bin, "chromium", "Chromium 140.0")
        with patch.object(machine, "CHROME_PATHS", ()):
            data = machine.write()
        on_disk = json.loads(machine.machine_path().read_text())
        self.assertEqual(on_disk, data)
        self.assertEqual(data["trazo"], machine.__version__)
        tools = data["tools"]
        self.assertEqual(sorted(tools), sorted(machine.TOOLS))
        self.assertEqual(tools["uv"], {"path": str(uv), "version": "uv 0.13.5 (abc 2026-10-22)",
                                       "found": "data folder"})
        self.assertEqual(tools["pandoc"], {"path": str(self.bin / "pandoc"), "version": "pandoc 3.9.0.2",
                                           "found": "PATH"})
        self.assertEqual(tools["chrome"], {"path": str(chrome), "version": "Chromium 140.0", "found": "PATH"})
        self.assertEqual(tools["python"]["path"], sys.executable)
        self.assertTrue(tools["python"]["version"].startswith("Python 3."))
        self.assertEqual(machine.recorded("pandoc"), str(self.bin / "pandoc"))

    def test_missing_tools_are_empty(self):
        with patch.object(machine, "CHROME_PATHS", ()):
            tools = machine.write()["tools"]
        for tool in ("uv", "pandoc", "chrome"):
            self.assertEqual(tools[tool], {"path": "", "version": "", "found": ""})
        self.assertEqual(machine.recorded("pandoc"), "")

    def test_known_chrome_location_comes_first(self):
        chrome = fake_tool(self.folder, "Google Chrome", "Google Chrome 141.0")
        fake_tool(self.bin, "chromium", "Chromium 140.0")
        with patch.object(machine, "CHROME_PATHS", (str(chrome),)):
            self.assertEqual(machine.find_chrome(), (str(chrome), "known location"))

    def test_downloaded_chrome_is_the_last_resort(self):
        downloaded = self.install_chrome()
        with patch.object(machine, "CHROME_PATHS", ()), patch.dict(os.environ, {"TRAZO_CHROME": ""}):
            self.assertEqual(machine.find_chrome(), (str(downloaded), "data folder"))
            self.assertEqual(machine.write()["tools"]["chrome"],
                             {"path": str(downloaded), "version": "Google Chrome for Testing 156.0.0.0",
                              "found": "data folder"})
            self.assertEqual(Project(self.folder, merge(DEFAULTS, {}), True).chrome(), str(downloaded))
            installed = fake_tool(self.bin, "chromium", "Chromium 140.0")
            self.assertEqual(machine.find_chrome(), (str(installed), "PATH"))
            self.assertEqual(Project(self.folder, merge(DEFAULTS, {}), True).chrome(), str(installed))
            configured = merge(DEFAULTS, {"browser": {"chrome": "/opt/chrome"}})
            self.assertEqual(Project(self.folder, configured, True).chrome(), "/opt/chrome")

    def test_unreadable_or_stale_machine_file(self):
        self.assertEqual(machine.read(), {})
        self.data.mkdir()
        machine.machine_path().write_text("not json")
        self.assertEqual(machine.read(), {})
        machine.machine_path().write_text(json.dumps({"tools": {"pandoc": {"path": str(self.folder / "gone")}}}))
        self.assertEqual(machine.recorded("pandoc"), "")  # recorded, but no longer there

    def test_a_malformed_machine_file_counts_as_missing_with_one_warning(self):
        self.data.mkdir()
        project = Project(self.folder, merge(DEFAULTS, {}), True)
        for tools in (["x"], None, {"pandoc": "x"}, {"pandoc": {"path": 3}}):
            with self.subTest(tools=tools):
                machine._warned.clear()
                machine.machine_path().write_text(json.dumps({"tools": tools}))
                err = io.StringIO()
                with redirect_stderr(err):
                    self.assertEqual(machine.recorded("pandoc"), "")
                    self.assertEqual(project.command("pandoc"), ["pandoc"])  # no crash in builds
                    self.assertEqual(machine.resolve_tool("uv"), ("", ""))
                warning = f"trazo: {machine.machine_path()} is malformed and was ignored"
                self.assertEqual(err.getvalue().count(warning), 0 if tools == {"pandoc": {"path": 3}} else 1)

    def test_python_of_the_data_folder_venv(self):
        venv = self.data / "venv"
        with patch.object(sys, "prefix", str(venv)):
            self.assertEqual(machine.python(), (str(venv / "bin/python"), "data folder"))
        self.assertEqual(machine.python(), (sys.executable, "running Python (may be a uv cache)"))

    def test_rosetta_reports_the_hardware(self):
        def sysctl(output):
            return patch.object(machine.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output, ""))
        try:
            for translated, expected in (("1\n", "arm64"), ("0\n", "x86_64"), ("", "x86_64")):
                machine.machine_name.cache_clear()
                with self.subTest(translated=translated), patch.object(sys, "platform", "darwin"), \
                        patch.object(machine.platform, "machine", return_value="x86_64"), sysctl(translated) as run:
                    self.assertEqual(machine.machine_name(), expected)
                    self.assertEqual(run.call_args.args[0][0], "/usr/sbin/sysctl")
            machine.machine_name.cache_clear()
            with patch.object(sys, "platform", "darwin"), patch.object(machine.platform, "machine", return_value="x86_64"), \
                    sysctl("1\n"):
                self.assertEqual(tool_setup.pandoc_asset("Darwin").name, "pandoc-3.9.0.2-arm64-macOS.zip")
        finally:
            machine.machine_name.cache_clear()

    def test_uv_order_path_then_data_folder_then_machine_file(self):
        recorded = fake_tool(self.folder, "uv", "uv recorded")
        machine.write({"uv": {"path": str(recorded)}})
        self.assertEqual(machine.resolve_tool("uv"), (str(recorded), "machine file"))
        (self.data / "uv").mkdir()
        in_data = fake_tool(self.data / "uv", "uv", "uv data")
        self.assertEqual(machine.resolve_tool("uv"), (str(in_data), "data folder"))
        on_path = fake_tool(self.bin, "uv", "uv path")
        self.assertEqual(machine.resolve_tool("uv"), (str(on_path), "PATH"))

    def test_homebrew_pandoc_off_path_is_recorded(self):
        homebrew = fake_tool(self.folder, "pandoc", "pandoc 3.9.0.2 (homebrew)")
        with patch.object(machine, "PANDOC_LOCATIONS", ("/no/such/pandoc", str(homebrew))), \
                patch.object(machine, "CHROME_PATHS", ()):
            tools = machine.write()["tools"]
        self.assertEqual(tools["pandoc"], {"path": str(homebrew), "version": "pandoc 3.9.0.2 (homebrew)",
                                           "found": "known location"})

    def test_version_line_of_a_failing_tool_is_empty(self):
        tool = self.bin / "broken"
        tool.write_text("#!/bin/sh\necho oops\nexit 3\n")
        tool.chmod(0o755)
        self.assertEqual(machine.version_line([str(tool)]), "")
        self.assertEqual(machine.version_line([str(self.bin / "missing")]), "")


@unittest.skipIf(os.name == "nt", "fake tools are shell scripts")
class PandocResolutionTests(ToolFolderCase):
    def setUp(self):
        super().setUp()
        self.recorded = fake_tool(self.folder, "pandoc", "pandoc 3.9.0.2 (machine file)")
        machine.write({"pandoc": {"path": str(self.recorded), "version": "pandoc 3.9.0.2", "found": "data folder"}})

    def project(self, commands=None):
        return Project(self.folder, merge(DEFAULTS, {"commands": commands} if commands else {}), True)

    def test_order_project_setting_then_path_then_machine_file(self):
        self.assertEqual(machine.resolve_pandoc(["/opt/pandoc", "--data-dir=x"]),
                         (["/opt/pandoc", "--data-dir=x"], "project setting"))
        self.assertEqual(machine.resolve_pandoc(["pandoc"]), ([str(self.recorded)], "machine file"))
        fake_tool(self.bin, "pandoc", "pandoc on PATH")
        self.assertEqual(machine.resolve_pandoc(["pandoc"]), (["pandoc"], "PATH"))

    def test_nothing_found_keeps_the_default(self):
        machine.machine_path().unlink()
        self.assertEqual(machine.resolve_pandoc(["pandoc"]), (["pandoc"], ""))

    def test_the_data_folder_pandoc_without_a_machine_file(self):
        machine.machine_path().unlink()
        installed = self.install_pandoc()
        self.assertEqual(machine.resolve_pandoc(["pandoc"]), ([str(installed)], "data folder"))

    def test_projects_and_builds_use_the_resolved_pandoc(self):
        self.assertEqual(self.project().command("pandoc"), [str(self.recorded)])
        self.assertEqual(self.project({"pandoc": ["my-pandoc"]}).command("pandoc"), ["my-pandoc"])
        builder = build_graph.Builder(self.project())
        # The hash has the configured command (PATH differs between a terminal
        # and an app) and the version line of the Pandoc that runs.
        self.assertEqual(builder.pandoc(self.folder / "a.md", self.folder / "n.json", self.folder / "a.docx")[0],
                         "pandoc")
        self.assertEqual(builder.versions()["pandoc"], "pandoc 3.9.0.2 (machine file)")
        with patch.object(pandoc_run, "PROJECT", self.project()):
            result = pandoc_run.run(["--version"], text=True, cwd=self.folder)
        self.assertEqual(result.args[0], str(self.recorded))
        self.assertEqual(result.stdout, "pandoc 3.9.0.2 (machine file)\n")


if __name__ == "__main__":
    unittest.main()
