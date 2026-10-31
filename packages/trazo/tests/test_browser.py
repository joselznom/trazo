"""Browser transport failures, using a real pipe peer instead of crashing Chrome."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)

from trazo import browser, diagram

PEER = r'''
import json, os, sys, time
from pathlib import Path

Path(__file__).with_suffix('.started').touch()
if MODE == 'startup-error':
    sys.stderr.write('x' * 100000 + '\nprofile could not be opened\n')
    sys.stderr.flush()
    sys.exit(23)
pending = b''
while True:
    chunk = os.read(3, 65536)
    if not chunk:
        break
    pending += chunk
    while b'\0' in pending:
        raw, pending = pending.split(b'\0', 1)
        request = json.loads(raw)
        method = request['method']
        result = {}
        if method == 'Target.createTarget':
            result = {'targetId': 'page'}
        elif method == 'Target.attachToTarget':
            result = {'sessionId': 'session'}
        elif method == 'Runtime.evaluate':
            if request['params']['expression'].startswith('DiagramRenderer.renderForExport'):
                result = {'exceptionDetails': {'text': 'original renderer failure'}}
            else:
                result = {'result': {'value': True}}
        elif method == 'Target.closeTarget' and MODE == 'render-error':
            sys.stderr.write('cleanup failed\n')
            sys.exit(7)
        os.write(4, json.dumps({'id': request['id'], 'result': result}).encode() + b'\0')
        if method == 'Browser.close':
            if MODE == 'hang-close':
                time.sleep(600)
            sys.exit(0)
'''


class BrowserTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="trazo-browser-test-")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def peer(self, mode):
        path = self.root / "peer"
        path.write_text(f"#!{sys.executable}\nMODE = {mode!r}\n" + PEER)
        path.chmod(0o755)
        patcher = patch.object(browser, "CHROME", str(path))
        patcher.start()
        self.addCleanup(patcher.stop)
        return path

    def test_failed_launch_releases_profile_and_pipe_descriptors(self):
        executable = self.root / "not-executable"
        executable.touch()
        profile = self.root / "profile"
        profile.mkdir()
        before = set(os.listdir("/dev/fd"))
        try:
            with patch.object(browser, "CHROME", str(executable)), \
                    patch.object(browser.tempfile, "mkdtemp", return_value=str(profile)):
                with self.assertRaises((OSError, browser.BrowserError)):
                    with browser.Chrome(timeout=2) as chrome:
                        chrome.call("Browser.getVersion")
            self.assertFalse(profile.exists(), "a failed launch left its temporary profile")
            self.assertEqual(len(os.listdir("/dev/fd")), len(before))
        finally:
            # Also clean up descriptors leaked by the unfixed implementation.
            for fd in set(os.listdir("/dev/fd")) - before:
                try:
                    os.close(int(fd))
                except OSError:
                    pass

    def test_startup_error_includes_exit_status_and_bounded_stderr(self):
        self.peer("startup-error")
        with self.assertRaises(browser.BrowserError) as caught:
            with browser.Chrome(timeout=2) as chrome:
                chrome.call("Browser.getVersion")
        message = str(caught.exception)
        self.assertIn("23", message)
        self.assertIn("profile could not be opened", message)
        self.assertLess(len(message), 10000)

    def test_cleanup_preserves_the_original_render_error(self):
        self.peer("render-error")
        payload = {"diagram": {"canvas": {"width_mm": 10, "height_mm": 10}},
                   "tokens": {"fonts": {"files": []}}}
        with browser.Chrome(timeout=2) as chrome:
            with self.assertRaisesRegex(browser.BrowserError, "original renderer failure"):
                diagram.chrome_render(chrome, payload)

    def test_close_reaps_an_unresponsive_process_and_is_idempotent(self):
        self.peer("hang-close")
        chrome = browser.Chrome(timeout=2)

        def reap():
            if chrome.proc.poll() is None:
                chrome.proc.kill()
            chrome.proc.wait(timeout=5)

        self.addCleanup(reap)
        chrome.close()
        self.assertIsNotNone(chrome.proc.returncode, "close returned before reaping Chrome")
        self.assertFalse(Path(chrome.profile).exists())
        chrome.close()

    @unittest.skipUnless(sys.platform == "darwin" and shutil.which("sandbox-exec"),
                         "needs the macOS sandbox")
    def test_denied_launchservices_is_reported_before_starting_a_browser(self):
        peer = self.peer("normal")
        code = """
from trazo.browser import Chrome, BrowserError
try:
    with Chrome(timeout=2) as chrome:
        chrome.call('Browser.getVersion')
except BrowserError as exc:
    print(exc)
    raise SystemExit(7)
"""
        result = subprocess.run([
            "sandbox-exec", "-p",
            '(version 1)(allow default)(deny mach-lookup '
            '(global-name "com.apple.coreservices.launchservicesd"))',
            sys.executable, "-c", code],
            env={**os.environ, "TRAZO_CHROME": str(peer)},
            capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        self.assertIn("macOS sandbox", result.stdout)
        self.assertIn("LaunchServices", result.stdout)
        self.assertFalse(peer.with_suffix(".started").exists())


if __name__ == "__main__":
    unittest.main()
