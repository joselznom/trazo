"""trazo setup: the pinned Pandoc zip, its download, check and unpacking.

The zips are fakes served from a local http.server; nothing leaves the machine."""

import contextlib
import hashlib
import http.server
import io
import json
import os
import platform
import shutil
import stat
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from unittest.mock import patch

import _support  # noqa: F401  (sets TRAZO_ROOT first)

from trazo import cli, machine, tool_setup
from trazo.tool_setup import Asset

PANDOC_SCRIPT = b"#!/bin/sh\necho 'pandoc 3.9.0.2'\necho 'Features: +server +lua'\n"
CHROME_SCRIPT = b"#!/bin/sh\necho 'Google Chrome for Testing 156.0.0.0'\n"


def make_zip(entries) -> bytes:
    """entries: (name, data, mode) with mode None for a directory."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data, mode in entries:
            info = zipfile.ZipInfo(name)
            info.external_attr = (mode or (stat.S_IFDIR | 0o755)) << 16
            archive.writestr(info, data)
    return buffer.getvalue()


PANDOC_ZIP = make_zip([
    ("pandoc-x/", b"", None),
    ("pandoc-x/bin/pandoc", PANDOC_SCRIPT, stat.S_IFREG | 0o755),
    ("pandoc-x/bin/pandoc-lua", b"pandoc", stat.S_IFLNK | 0o755),
    ("pandoc-x/share/man/pandoc.1", b"manual", stat.S_IFREG | 0o644),
])


CHROME_ZIP = make_zip([
    ("chrome-x/", b"", None),
    ("chrome-x/chrome-headless-shell", CHROME_SCRIPT, stat.S_IFREG | 0o755),
    ("chrome-x/icudtl.dat", b"data", stat.S_IFREG | 0o644),
])


def asset_for(data: bytes, sha256: str | None = None) -> Asset:
    return Asset("pandoc-test.zip", len(data), sha256 or hashlib.sha256(data).hexdigest(),
                 "pandoc-x/bin/pandoc", 1000)


def chrome_asset_for(data: bytes, sha256: str | None = None) -> Asset:
    return Asset("chrome-headless-shell-test.zip", len(data), sha256 or hashlib.sha256(data).hexdigest(),
                 "chrome-x/chrome-headless-shell", 1000)


class Server:
    """Serves {path: bytes} on 127.0.0.1 and counts the requests for files. A bare `/` is not counted:
    setup's URLs always name a version and a file, and apps that watch for local servers (T3 Code does)
    send `GET /` to any port that opens, so counting it made the tests fail at random."""

    def __init__(self, files):
        self.files, self.requests = files, []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != "/":
                    outer.requests.append(self.path)
                data = outer.files.get(self.path)
                self.send_response(200 if data is not None else 404)
                self.send_header("Content-Length", str(len(data or b"")))
                self.end_headers()
                self.wfile.write(data or b"")

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = tool_setup.main(argv)
    return code, out.getvalue(), err.getvalue()


class AssetTests(unittest.TestCase):
    def test_platform_to_asset(self):
        self.assertEqual(tool_setup.pandoc_asset("Darwin", "arm64").name, "pandoc-3.9.0.2-arm64-macOS.zip")
        self.assertEqual(tool_setup.pandoc_asset("Darwin", "x86_64").name, "pandoc-3.9.0.2-x86_64-macOS.zip")
        for name in ("AMD64", "x86_64"):
            self.assertEqual(tool_setup.pandoc_asset("Windows", name).name, "pandoc-3.9.0.2-windows-x86_64.zip")
        for system, name in (("Linux", "x86_64"), ("Windows", "ARM64"), ("Darwin", "ppc")):
            self.assertIsNone(tool_setup.pandoc_asset(system, name))

    def test_pins_are_consistent(self):
        for asset in tool_setup.PANDOC_ASSETS.values():
            self.assertIn(tool_setup.PANDOC_VERSION, asset.name)
            self.assertRegex(asset.sha256, "^[0-9a-f]{64}$")
            self.assertTrue(asset.binary.endswith(("/pandoc", "/pandoc.exe")))

    def test_chrome_platform_to_asset_and_url(self):
        base = "https://storage.googleapis.com/chrome-for-testing-public/" + tool_setup.CHROME_VERSION
        arm, intel = tool_setup.chrome_asset("Darwin", "arm64"), tool_setup.chrome_asset("Darwin", "x86_64")
        self.assertEqual(tool_setup.chrome_url(arm), f"{base}/mac-arm64/chrome-headless-shell-mac-arm64.zip")
        self.assertEqual(tool_setup.chrome_url(intel), f"{base}/mac-x64/chrome-headless-shell-mac-x64.zip")
        for system, name in (("Linux", "x86_64"), ("Windows", "AMD64"), ("Darwin", "ppc")):
            self.assertIsNone(tool_setup.chrome_asset(system, name))

    def test_chrome_pins_are_consistent(self):
        for asset in tool_setup.CHROME_ASSETS.values():
            stem = asset.name.removesuffix(".zip")
            self.assertRegex(asset.sha256, "^[0-9a-f]{64}$")
            self.assertEqual(asset.binary, f"{stem}/chrome-headless-shell")
            self.assertGreater(asset.unpacked, asset.size)


@unittest.skipIf(os.name == "nt", "the fake Pandoc is a shell script")
class SetupTests(unittest.TestCase):
    def setUp(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, True)
        self.data, self.bin = folder / "data", folder / "bin"
        self.bin.mkdir()
        for patcher in (patch.dict(os.environ, {"TRAZO_DATA_DIR": str(self.data), "PATH": str(self.bin)}),
                        patch.object(machine, "CHROME_PATHS", ()), patch.object(machine, "PANDOC_LOCATIONS", ())):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.server = Server({f"/{tool_setup.PANDOC_VERSION}/pandoc-test.zip": PANDOC_ZIP,
                              f"/{tool_setup.CHROME_VERSION}/test/chrome-headless-shell-test.zip": CHROME_ZIP})
        self.addCleanup(self.server.close)
        self.use(asset_for(PANDOC_ZIP))
        self.use_chrome(None)  # the Pandoc tests download no Chrome

    def use(self, asset):
        for patcher in (patch.object(tool_setup, "PANDOC_ASSETS", {(platform.system(), machine.machine_name()): asset}),
                        patch.object(tool_setup, "PANDOC_URL", self.server.url + "/{version}/{name}")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def use_chrome(self, asset):
        for patcher in (patch.object(tool_setup, "CHROME_ASSETS",
                                     {(platform.system(), machine.machine_name()): asset} if asset else {}),
                        patch.object(tool_setup, "CHROME_URL", self.server.url + "/{version}/{platform}/{name}")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def files(self):
        return sorted(str(p.relative_to(self.data)) for p in self.data.rglob("*")) if self.data.exists() else []

    def test_plan_prints_url_size_and_destination_and_changes_nothing(self):
        code, out, _ = run(["--plan"])
        self.assertEqual(code, 0)
        self.assertIn(f"{self.server.url}/{tool_setup.PANDOC_VERSION}/pandoc-test.zip", out)
        self.assertIn(f"{len(PANDOC_ZIP) / 1e6:.1f} MB, sha256 {hashlib.sha256(PANDOC_ZIP).hexdigest()}", out)
        self.assertIn(f"unpacked into {self.data / 'pandoc' / tool_setup.PANDOC_VERSION}", out)
        self.assertIn(str(self.data / "machine.json"), out)
        self.assertFalse(self.data.exists())
        self.assertEqual(self.server.requests, [])

    def test_setup_downloads_unpacks_and_records_pandoc(self):
        code, out, err = run([])
        self.assertEqual(code, 0, out + err)
        binary = self.data / "pandoc" / tool_setup.PANDOC_VERSION / "pandoc-x" / "bin" / "pandoc"
        self.assertTrue(os.access(binary, os.X_OK))
        link = binary.with_name("pandoc-lua")
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), "pandoc")
        self.assertEqual(stat.S_IMODE((binary.parent.parent / "share/man/pandoc.1").stat().st_mode), 0o644)
        tools = json.loads((self.data / "machine.json").read_text())["tools"]
        self.assertEqual(tools["pandoc"], {"path": str(binary), "version": "pandoc 3.9.0.2", "found": "data folder"})
        self.assertIn(f"pandoc: pandoc 3.9.0.2 ({binary}, data folder)", out)
        if platform.system() == "Darwin":
            self.assertIn("quarantine:", out)
        self.assertIn(f"  {machine.CHROME_MISSING}.\n  To turn them on: {machine.CHROME_FIX}.", out)
        # Only the folder and machine.json: the zip and temporary folders are gone.
        self.assertEqual([f for f in self.files() if "pandoc-x" not in f],
                         ["machine.json", "pandoc", f"pandoc/{tool_setup.PANDOC_VERSION}"])
        # A second run downloads nothing and refreshes machine.json.
        (self.data / "machine.json").unlink()
        code, out, _ = run([])
        self.assertEqual(code, 0)
        self.assertEqual(len(self.server.requests), 1)
        self.assertTrue((self.data / "machine.json").is_file())
        self.assertIn("nothing to download", run(["--plan"])[1])

    def test_checksum_failure_deletes_the_download(self):
        self.use(asset_for(PANDOC_ZIP, "0" * 64))
        code, out, err = run([])
        self.assertEqual(code, 1)
        self.assertIn("is not the expected " + "0" * 64, err)
        self.assertIn("the download was deleted", err)
        self.assertEqual(self.files(), ["pandoc"])  # no zip, no unpacked files, no machine.json

    def test_a_short_download_says_so(self):
        self.use(asset_for(PANDOC_ZIP)._replace(size=len(PANDOC_ZIP) + 10))
        code, _, err = run([])
        self.assertEqual(code, 1)
        self.assertIn(f"got {len(PANDOC_ZIP)} of {len(PANDOC_ZIP) + 10} bytes; check the connection and run again", err)
        self.assertEqual(self.files(), ["pandoc"])

    def test_leftovers_of_an_interrupted_run_are_removed(self):
        # A killed run leaves its temporary folder, or a folder without the binary.
        leftover = self.data / "pandoc" / ".setup-abc123"
        leftover.mkdir(parents=True)
        (leftover / "pandoc-test.zip").write_bytes(b"part")
        partial = self.data / "pandoc" / tool_setup.PANDOC_VERSION / "pandoc-x"
        partial.mkdir(parents=True)
        (partial / "half").write_bytes(b"x")
        code, out, err = run([])
        self.assertEqual(code, 0, out + err)
        self.assertEqual([f for f in self.files() if "pandoc-x" not in f],
                         ["machine.json", "pandoc", f"pandoc/{tool_setup.PANDOC_VERSION}"])
        self.assertFalse((partial / "half").exists())

    def test_ctrl_c_during_the_download_exits_130_and_leaves_nothing(self):
        class Interrupted(io.BytesIO):
            def read(self, size=-1):
                if self.tell():
                    raise KeyboardInterrupt
                return super().read(1)

        out, err = io.StringIO(), io.StringIO()
        with patch("urllib.request.urlopen", return_value=Interrupted(PANDOC_ZIP)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["setup"])
        self.assertEqual(code, 130)
        self.assertEqual(err.getvalue(), "trazo: interrupted\n")
        self.assertEqual(self.files(), ["pandoc"])  # the partial zip and its temporary folder are gone

    def test_homebrew_pandoc_off_path_is_used(self):
        homebrew = self.bin.parent / "homebrew" / "pandoc"
        homebrew.parent.mkdir()
        homebrew.write_bytes(PANDOC_SCRIPT)
        homebrew.chmod(0o755)
        with patch.object(machine, "PANDOC_LOCATIONS", (str(homebrew),)):
            self.assertIn(f"Pandoc: {homebrew} (known location); nothing to download", run(["--plan"])[1])
            code, out, err = run([])
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.server.requests, [])
        self.assertEqual(json.loads((self.data / "machine.json").read_text())["tools"]["pandoc"],
                         {"path": str(homebrew), "version": "pandoc 3.9.0.2", "found": "known location"})

    def test_missing_asset_fails_clearly(self):
        self.server.files.clear()
        code, _, err = run([])
        self.assertEqual(code, 1)
        self.assertIn("cannot download", err)
        self.assertIn("404", err)
        self.assertEqual(self.files(), ["pandoc"])

    def test_pandoc_on_path_is_not_downloaded(self):
        (self.bin / "pandoc").write_bytes(PANDOC_SCRIPT)
        (self.bin / "pandoc").chmod(0o755)
        self.assertIn(f"Pandoc: {self.bin / 'pandoc'} (PATH); nothing to download", run(["--plan"])[1])
        code, out, _ = run([])
        self.assertEqual(code, 0)
        self.assertEqual(self.server.requests, [])
        self.assertEqual(json.loads((self.data / "machine.json").read_text())["tools"]["pandoc"]["found"], "PATH")

    def test_offline_downloads_nothing(self):
        # The preview's update: a Pandoc to download means setup in the chat, with consent.
        code, _, err = run(["--offline"])
        self.assertEqual((code, self.server.requests, self.files()), (3, [], []))
        self.assertIn(f"trazo setup: offline: Pandoc {tool_setup.PANDOC_VERSION} must be downloaded", err)
        (self.bin / "pandoc").write_bytes(PANDOC_SCRIPT)
        (self.bin / "pandoc").chmod(0o755)
        self.assertEqual(run(["--offline"])[0], 0)

    def chrome_binary(self):
        return self.data / "chrome" / tool_setup.CHROME_VERSION / "chrome-x" / "chrome-headless-shell"

    def test_chrome_is_downloaded_when_none_is_installed(self):
        self.use_chrome(chrome_asset_for(CHROME_ZIP))
        plan = run(["--plan"])[1]
        self.assertIn(f"{self.server.url}/{tool_setup.CHROME_VERSION}/test/chrome-headless-shell-test.zip", plan)
        self.assertIn(f"sha256 {hashlib.sha256(CHROME_ZIP).hexdigest()}", plan)
        self.assertIn(f"unpacked into {self.data / 'chrome' / tool_setup.CHROME_VERSION}", plan)
        code, out, err = run([])
        self.assertEqual(code, 0, out + err)
        self.assertTrue(os.access(self.chrome_binary(), os.X_OK))
        self.assertEqual(stat.S_IMODE(self.chrome_binary().with_name("icudtl.dat").stat().st_mode), 0o644)
        self.assertEqual(json.loads((self.data / "machine.json").read_text())["tools"]["chrome"],
                         {"path": str(self.chrome_binary()), "version": "Google Chrome for Testing 156.0.0.0",
                          "found": "data folder"})
        self.assertNotIn(machine.CHROME_MISSING, out)
        self.assertEqual(len(self.server.requests), 2)  # Pandoc and Chrome
        # A second run downloads nothing and the plan says so.
        code, out, _ = run([])
        self.assertEqual((code, len(self.server.requests)), (0, 2))
        self.assertIn(f"Chrome: {self.chrome_binary()} (data folder); nothing to download", run(["--plan"])[1])

    def test_an_installed_chrome_is_not_downloaded(self):
        self.use_chrome(chrome_asset_for(CHROME_ZIP))
        (self.bin / "chromium").write_bytes(CHROME_SCRIPT)
        (self.bin / "chromium").chmod(0o755)
        self.assertIn(f"Chrome: {self.bin / 'chromium'} (PATH); nothing to download", run(["--plan"])[1])
        with contextlib.suppress(urllib.error.HTTPError):  # what a port prober sends; it is not a download
            urllib.request.urlopen(self.server.url + "/", timeout=5).close()
        code, out, err = run([])
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.server.requests, [f"/{tool_setup.PANDOC_VERSION}/pandoc-test.zip"])
        self.assertFalse((self.data / "chrome").exists())
        self.assertEqual(json.loads((self.data / "machine.json").read_text())["tools"]["chrome"]["found"], "PATH")

    def test_a_failed_chrome_download_does_not_fail_setup(self):
        # Chrome is optional: Pandoc and machine.json are done, and the user is told what is off.
        self.use_chrome(chrome_asset_for(CHROME_ZIP, "0" * 64))
        code, out, err = run([])
        self.assertEqual(code, 0, out + err)
        self.assertIn("trazo setup: could not download Chrome", err)
        self.assertIn("is not the expected " + "0" * 64, err)
        self.assertIn(f"  {machine.CHROME_MISSING}.", out)
        # No zip, no temporary folder, no half-unpacked Chrome.
        self.assertEqual([f for f in self.files() if "pandoc" not in f], ["chrome", "machine.json"])
        self.assertEqual(json.loads((self.data / "machine.json").read_text())["tools"]["chrome"]["path"], "")

    def test_offline_skips_chrome_without_failing(self):
        # Chrome is optional, so the preview's update does not stop for it.
        self.use_chrome(chrome_asset_for(CHROME_ZIP))
        (self.bin / "pandoc").write_bytes(PANDOC_SCRIPT)
        (self.bin / "pandoc").chmod(0o755)
        code, out, err = run(["--offline"])
        self.assertEqual((code, self.server.requests), (0, []), out + err)
        self.assertIn(f"  {machine.CHROME_MISSING}.", out)

    def test_older_versions_are_removed_once_the_new_one_is_unpacked(self):
        self.use_chrome(chrome_asset_for(CHROME_ZIP))
        for tool in ("pandoc", "chrome"):
            (self.data / tool / "0.0.1").mkdir(parents=True)
            (self.data / tool / "0.0.1" / "old").write_bytes(b"x")
        (self.data / "chrome" / "notes").mkdir()  # not a version: left alone
        code, out, err = run([])
        self.assertEqual(code, 0, out + err)
        self.assertEqual(sorted(p.name for p in (self.data / "pandoc").iterdir()), [tool_setup.PANDOC_VERSION])
        self.assertEqual(sorted(p.name for p in (self.data / "chrome").iterdir()), [tool_setup.CHROME_VERSION, "notes"])
        self.assertIn(f"Removed {self.data / 'pandoc' / '0.0.1'}", out)

    def test_older_versions_stay_when_the_download_fails(self):
        (self.data / "pandoc" / "0.0.1").mkdir(parents=True)
        self.use(asset_for(PANDOC_ZIP, "0" * 64))
        self.assertEqual(run([])[0], 1)
        self.assertTrue((self.data / "pandoc" / "0.0.1").is_dir())

    def test_a_platform_without_a_chrome_download_says_so(self):
        self.assertIn("downloads Chrome only for macOS", run(["--plan"])[1])

    def test_unsupported_platform_without_pandoc_fails(self):
        with patch.object(tool_setup, "PANDOC_ASSETS", {}):
            self.assertIn("downloads Pandoc only for macOS and Windows", run(["--plan"])[1])
            code, _, err = run([])
        self.assertEqual(code, 1)
        self.assertIn("no Pandoc found", err)

    def test_unpack_refuses_paths_and_links_outside_the_folder(self):
        link = stat.S_IFLNK | 0o777
        for entries in ([("../evil", b"x", stat.S_IFREG | 0o644)],
                        [("a/link", b"../../evil", link)],
                        [("a/link", b"/etc", link)],
                        # Each link stays inside when made; together L points outside.
                        [("d1/d2/L", b"x/../..", link), ("d1/d2/x", b"../..", link)],
                        [("d1/d2/x", b"../..", link)]):
            archive = self.bin / "bad.zip"
            archive.write_bytes(make_zip(entries))
            with self.subTest(entries=entries), self.assertRaises(tool_setup.SetupError):
                tool_setup.unpack(archive, self.data / "out")
        self.assertFalse((self.data / "evil").exists())
        self.assertFalse((self.data.parent / "evil").exists())


if __name__ == "__main__":
    unittest.main()
