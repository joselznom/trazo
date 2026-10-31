"""template/scripts/trazo runs the per-user install, or a checkout with
TRAZO_DEV through uv (fakes on PATH)."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "template/scripts/trazo"
# What the script runs besides uv: a PATH of only these has no uv.
TOOLS = ("sh", "dirname", "uname", "sed", "head", "tr", "cat", "mkdir", "env")


def executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + text)
    path.chmod(0o755)


@unittest.skipIf(os.name == "nt", "a POSIX shell script")
class ScriptsTrazoTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.project = self.tmp / "paper"
        (self.project / "scripts").mkdir(parents=True)
        shutil.copy(SCRIPT, self.project / "scripts/trazo")
        (self.project / "trazo.toml").write_text(
            'version = "v0.5.0"\n')
        self.data = self.tmp / "Application Support/trazo"  # as on macOS: a space
        bin_dir = self.tmp / "bin"
        executable(bin_dir / "uv", 'echo "uv $*"\n')
        (self.tmp / "no-uv").mkdir()
        for tool in TOOLS:
            os.symlink(shutil.which(tool), self.tmp / "no-uv" / tool)
        self.env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(self.tmp / "home"),
                    "TRAZO_DATA_DIR": str(self.data)}

    def install(self, version: str, data: Path | None = None) -> None:
        venv = (data or self.data) / "venv"
        executable(venv / "bin/trazo", 'echo "installed $*"\n')
        for info in venv.glob("lib/python*/site-packages/trazo-*.dist-info"):
            info.rmdir()
        (venv / f"lib/python3.12/site-packages/trazo-{version}.dist-info").mkdir(parents=True)

    def run_script(self, *command, code=0, **env) -> str:
        result = subprocess.run(list(command or [str(self.project / "scripts/trazo"), "version"]),
                                cwd=self.tmp, env={**self.env, **env}, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, code, result.stderr)
        return (result.stdout + result.stderr if code else result.stdout).strip()

    def test_the_per_user_install_runs_and_says_when_it_is_another_release(self):
        def run():
            result = subprocess.run([str(self.project / "scripts/trazo"), "version"], cwd=self.tmp, env=self.env,
                                    capture_output=True, text=True, timeout=30)
            return result.returncode, result.stdout.strip(), result.stderr.strip()

        self.install("0.5.0")
        (self.project / "trazo.toml").write_text('version = "v0.5.0"\n[limtis]\n')  # the command reports it
        self.assertEqual(run(), (0, "installed version", ""))
        self.install("0.6.0")
        self.assertEqual(run(), (0, "installed version", "scripts/trazo: this paper pins trazo v0.5.0, but "
                         "trazo 0.6.0 is set up here: update the older one (the paper with the plugin's paper_update, "
                         "or the plugin and its setup)"))
        (self.data / "venv/lib/python3.13/site-packages/trazo-0.5.0.dist-info").mkdir(parents=True)
        self.assertIn("but another trazo is set up here", run()[2])  # two installs: no version

    def test_without_the_install_it_says_to_set_trazo_up(self):
        self.assertEqual(self.run_script(code=127), "scripts/trazo: trazo is not set up on this computer: "
                         "set it up (the plugin's setup skill)")
        (self.project / "trazo.toml").write_text("")
        self.install("0.5.0")
        self.assertEqual(self.run_script(code=2), "scripts/trazo: trazo.toml has no version")

    def test_the_default_data_folder_follows_machine_data_dir(self):
        del self.env["TRAZO_DATA_DIR"]
        uname = self.tmp / "bin/uname"
        home = Path(self.env["HOME"])
        executable(uname, "echo Linux\n")
        self.install("0.5.0", self.tmp / "xdg/trazo")
        self.assertEqual(self.run_script(XDG_DATA_HOME=str(self.tmp / "xdg")), "installed version")
        self.run_script(code=127)  # not in the default folder
        self.install("0.5.0", home / ".local/share/trazo")
        self.assertEqual(self.run_script(), "installed version")
        self.assertEqual(self.run_script(XDG_DATA_HOME=""), "installed version")
        executable(uname, "echo Darwin\n")
        self.run_script(code=127, XDG_DATA_HOME=str(self.tmp / "xdg"))  # macOS ignores XDG_DATA_HOME
        self.install("0.5.0", home / "Library/Application Support/trazo")
        self.assertEqual(self.run_script(), "installed version")

    def test_trazo_dev_comes_first(self):
        self.install("0.5.0")
        self.assertEqual(self.run_script(TRAZO_DEV="/src/trazo"),
                         "uv run --quiet --project /src/trazo trazo version")

    def test_uv_from_the_data_folder_when_path_has_none(self):
        # As trazo.machine.resolve_tool: PATH has no uv (only the system's
        # tools), the data folder's runs the checkout with its cache kept there.
        executable(self.data / "uv/uv", 'echo "data uv $* $UV_CACHE_DIR $UV_MANAGED_PYTHON"\n')
        path = str(self.tmp / "no-uv")
        self.assertEqual(self.run_script(PATH=path, TRAZO_DEV="/src/trazo"),
                         f"data uv run --quiet --project /src/trazo trazo version {self.data / 'cache'} 1")
        self.assertEqual(self.run_script(TRAZO_DEV="/src/trazo"),
                         "uv run --quiet --project /src/trazo trazo version")  # PATH's uv comes first

    def test_no_uv_at_all_names_setup(self):
        self.assertEqual(self.run_script(code=127, PATH=str(self.tmp / "no-uv"), TRAZO_DEV="/src/trazo"),
                         "scripts/trazo: uv not found: set up trazo (the plugin's setup skill) or install uv")


if __name__ == "__main__":
    unittest.main()
