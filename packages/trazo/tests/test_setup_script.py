"""plugin/scripts/setup.sh: uv's download and check, and trazo's install.

curl, xcode-select and uv are fakes; nothing leaves the machine."""

import hashlib
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

SETUP_SH = Path(__file__).resolve().parents[3] / "plugin" / "scripts" / "setup.sh"
FAKE_UV = """#!/bin/sh
echo "$*${{UV_OFFLINE:+ (offline)}}" >> "{log}"
[ "$1" != "$FAKE_UV_FAIL" ] || {{ echo "error: no network" >&2; exit 2; }}
case "$1" in
    --version) echo "uv 0.13.5 (fake 2026-10-22)" ;;
    python) tool=$(command -v install_name_tool)
            case $tool in /usr/bin/*) ;; *) "$tool" -id lib lib || tool="$tool failed" ;; esac
            echo "install_name_tool: $tool" >> "{log}" ;;
    venv) echo "install_name_tool in venv: $(command -v install_name_tool)" >> "{log}"
          for arg; do last=$arg; done
          /bin/mkdir -p "$last/bin"
          printf '#!/bin/sh\\necho "trazo $* ran"\\n' > "$last/bin/trazo"
          /bin/chmod +x "$last/bin/trazo" ;;
esac
"""


@unittest.skipUnless(platform.system() == "Darwin" and SETUP_SH.is_file(), "setup.sh is macOS only")
class SetupScriptTests(unittest.TestCase):
    """plugin/scripts/setup.sh with a fake curl and xcode-select: a copy whose
    /usr/bin/curl and /usr/bin/xcode-select are the fakes (the real ones are
    called by full path), in a fake plugin folder."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.plugin = self.folder / "plugin root"
        (self.plugin / "packages/trazo").mkdir(parents=True)
        (self.plugin / "packages/trazo/pyproject.toml").write_text("")
        self.data = self.folder / "Application Support" / "trazo"
        self.log = self.folder / "calls.log"
        uv = self.folder / "uv-build" / "uv-aarch64-apple-darwin" / "uv"
        uv.parent.mkdir(parents=True)
        uv.write_text(FAKE_UV.format(log=self.log))
        uv.chmod(0o755)
        self.tarball = self.folder / "uv.tar.gz"
        with tarfile.open(self.tarball, "w:gz") as archive:
            archive.add(uv.parent, arcname=uv.parent.name)
        self.curl = self.folder / "curl"
        self.curl.write_text(f"""#!/bin/sh
echo "curl $*" >> "{self.log}"
[ -z "$FAKE_CURL_EXIT" ] || exit "$FAKE_CURL_EXIT"
while [ $# -gt 0 ]; do [ "$1" = --output ] && out=$2; shift; done
/bin/cp "$FAKE_CURL_FILE" "$out"
""")
        self.curl.chmod(0o755)
        self.xcode_select = self.folder / "xcode-select"
        self.xcode_select.write_text('#!/bin/sh\nexit "${FAKE_XCODE_SELECT_EXIT:-0}"\n')
        self.xcode_select.chmod(0o755)

    def script(self, sha256=None) -> Path:
        text = SETUP_SH.read_text().replace("/usr/bin/curl", str(self.curl))
        text = text.replace("/usr/bin/xcode-select", str(self.xcode_select))
        if sha256:
            text = re.sub(r"sha256=[0-9a-f]{64}", f"sha256={sha256}", text)
        path = self.plugin / "plugin" / "scripts" / "setup.sh"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def run_script(self, script, *args, **env):
        env = {"PATH": "/usr/bin:/bin", "HOME": str(self.folder), "TRAZO_DATA_DIR": str(self.data),
               "FAKE_CURL_FILE": str(self.tarball), **env}
        return subprocess.run(["/bin/sh", str(script), *args], env=env, capture_output=True, text=True, timeout=60)

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_wrong_sha256_installs_nothing(self):
        (self.data / ".setup.old").mkdir(parents=True)  # left by a killed run
        result = self.run_script(self.script())
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("does not have the expected sha256; nothing was installed", result.stderr)
        self.assertEqual(sorted(p.name for p in self.data.iterdir()), [])
        self.assertTrue(self.calls()[0].startswith("curl --fail"))

    def test_a_failed_download_keeps_curls_exit_code(self):
        result = self.run_script(self.script(), FAKE_CURL_EXIT="22")
        self.assertEqual(result.returncode, 22)
        self.assertEqual(list(self.data.iterdir()), [])

    def test_not_the_plugin_folder(self):
        result = self.run_script(self.script(), str(self.folder))
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"setup.sh: {self.folder} is not the plugin folder", result.stderr)

    def test_installs_uv_then_trazo_from_the_plugin_folder(self):
        script = self.script(hashlib.sha256(self.tarball.read_bytes()).hexdigest())
        result = self.run_script(script)  # no argument: the plugin folder is found from the script's path
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(result.stdout.rstrip().endswith("trazo setup ran"), result.stdout)
        self.assertTrue((self.data / "uv" / "uv").is_file())
        calls = self.calls()
        self.assertEqual(sum(c.startswith("curl ") for c in calls), 1)
        self.assertIn("python install --no-bin 3.12", calls)
        self.assertIn("install_name_tool: /usr/bin/install_name_tool", calls)  # the Command Line Tools are there
        self.assertIn(f"pip install --quiet --python {self.data}/venv/bin/python --reinstall-package trazo "
                      f"{self.plugin}/packages/trazo", calls)
        self.assertEqual(sorted(p.name for p in self.data.iterdir()), ["uv", "venv"])
        # Again: uv is current, so nothing is downloaded.
        self.log.unlink()
        result = self.run_script(script)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(any(c.startswith("curl ") for c in self.calls()))

    def test_without_the_command_line_tools_python_installs_with_a_no_op_install_name_tool(self):
        # Apple's stub would open the tools' install dialog in the middle of setup.
        script = self.script(hashlib.sha256(self.tarball.read_bytes()).hexdigest())
        for uv_in_place in (False, True):
            self.log.unlink(missing_ok=True)
            result = self.run_script(script, FAKE_XCODE_SELECT_EXIT="2")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            calls = self.calls()
            self.assertEqual(any(c.startswith("curl ") for c in calls), not uv_in_place)
            tool = next(c for c in calls if c.startswith("install_name_tool: ")).removeprefix("install_name_tool: ")
            self.assertRegex(tool, rf"^{re.escape(str(self.data))}/\.setup\.[^/]+/bin/install_name_tool$")
            self.assertIn("install_name_tool in venv: /usr/bin/install_name_tool", calls)  # only for Python
            self.assertEqual(sorted(p.name for p in self.data.iterdir()), ["uv", "venv"])

    def test_offline_installs_trazo_only_with_uv_in_place(self):
        script = self.script(hashlib.sha256(self.tarball.read_bytes()).hexdigest())
        result = self.run_script(script, "--offline")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("setup.sh: offline: uv 0.13.5 must be downloaded; nothing was changed", result.stderr)
        self.assertEqual((self.calls(), list(self.data.iterdir())), ([], []))
        self.assertEqual(self.run_script(script).returncode, 0)
        self.log.unlink()
        result = self.run_script(script, "--offline")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(result.stdout.rstrip().endswith("trazo setup --offline ran"), result.stdout)
        self.assertIn(f"pip install --quiet --python {self.data}/venv/bin/python --reinstall-package trazo "
                      f"{self.plugin}/packages/trazo (offline)", self.calls())
        # A Python or build backend uv would download: exit 3 offline, uv's code otherwise.
        result = self.run_script(script, "--offline", FAKE_UV_FAIL="python")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertTrue(result.stderr.endswith("setup.sh: offline: uv python failed, probably for a download\n"))
        self.assertEqual(self.run_script(script, FAKE_UV_FAIL="python").returncode, 2)


if __name__ == "__main__":
    unittest.main()
