"""The n1 helpers that carry a verdict: the folder fingerprint, the install's version and the
DevTools-pipe client (against a fake Chrome that answers from a script)."""

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

import _path  # noqa: F401
from acceptance.evidence import Evidence
from acceptance.n1 import chrome, support
from acceptance.sandbox import Sandbox

# Speaks the pipe protocol on descriptors 3 (requests) and 4 (replies). What it answers to a method
# is the first entry of that method's list in replies.json (the last one repeats); "event" entries
# are sent first, the way Chrome interleaves them.
FAKE_CHROME = """#!{python}
import json, os, sys
script = json.load(open({replies!r}))
buffer = b""
while True:
    chunk = os.read(3, 65536)
    if not chunk:
        break
    buffer += chunk
    while b"\\0" in buffer:
        raw, buffer = buffer.split(b"\\0", 1)
        request = json.loads(raw)
        queue = script.get(request["method"], [{{}}])
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        os.write(4, json.dumps({{"method": "Page.loadEventFired", "params": {{}}}}).encode() + b"\\0")
        os.write(4, json.dumps({{"id": request["id"], **reply}}).encode() + b"\\0")
"""


class FingerprintTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, True))
        (self.root / "a").mkdir()
        (self.root / "a" / "file").write_text("one")
        (self.root / "link").symlink_to("a/file")

    def test_rewriting_the_same_bytes_is_no_change(self):
        before = support.fingerprint(self.root)
        (self.root / "a" / "file").write_text("one")
        os.utime(self.root / "a" / "file", (1, 1))
        self.assertEqual(support.fingerprint(self.root), before)

    def test_content_additions_removals_and_a_new_link_target_are_changes(self):
        before = support.fingerprint(self.root)
        (self.root / "a" / "file").write_text("two")
        self.assertNotEqual(support.fingerprint(self.root), before)
        (self.root / "a" / "file").write_text("one")
        (self.root / "extra").write_text("")
        self.assertNotEqual(support.fingerprint(self.root), before)
        (self.root / "extra").unlink()
        (self.root / "link").unlink()
        (self.root / "link").symlink_to("elsewhere")
        self.assertNotEqual(support.fingerprint(self.root), before)


class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.sandbox = Sandbox("unit-n1", Evidence())
        self.addCleanup(self.sandbox.cleanup)

    def fake(self, replies: dict) -> str:
        script = self.sandbox.root / "replies.json"
        script.write_text(json.dumps(replies))
        executable = self.sandbox.root / "fake-chrome"
        executable.write_text(FAKE_CHROME.format(python=sys.executable, replies=str(script)))
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
        return str(executable)

    def test_a_call_returns_its_own_result_and_skips_events(self):
        browser = chrome.Browser(self.sandbox, self.fake({"Target.createTarget": [{"result": {"targetId": "t1"}}]}))
        self.addCleanup(browser.close)
        self.assertEqual(browser.call("Target.createTarget"), {"targetId": "t1"})

    def test_an_error_reply_raises_with_its_message(self):
        browser = chrome.Browser(self.sandbox, self.fake({"Page.navigate": [{"error": {"message": "bad url"}}]}))
        self.addCleanup(browser.close)
        with self.assertRaisesRegex(chrome.ChromeError, "Page.navigate: bad url"):
            browser.call("Page.navigate")

    def test_wait_for_retries_through_errors_until_true(self):
        throws = {"exceptionDetails": {"text": "Execution context was destroyed"}}
        browser = chrome.Browser(self.sandbox, self.fake({"Runtime.evaluate": [
            {"result": throws}, {"result": {"result": {"value": False}}}, {"result": {"result": {"value": True}}}]}))
        self.addCleanup(browser.close)
        chrome.Page(browser, "s").wait_for("ready", timeout=5)

    def test_a_chrome_that_never_answers_times_out(self):
        script = self.sandbox.root / "silent"
        script.write_text(f"#!{sys.executable}\nimport os\nwhile os.read(3, 1):\n    pass\n")
        script.chmod(0o755)
        with self.assertRaisesRegex(chrome.ChromeError, "did not answer Browser.getVersion"):
            chrome.Browser(self.sandbox, str(script), startup_timeout=0.5)


class MachineFileTests(unittest.TestCase):
    def setUp(self):
        self.sandbox = Sandbox("unit-n1-machine", Evidence())
        self.addCleanup(self.sandbox.cleanup)

    def test_the_chrome_setup_recorded_wins(self):
        self.sandbox.data.mkdir(parents=True, exist_ok=True)
        recorded = self.sandbox.root / "my-chrome"
        recorded.write_text("")
        (self.sandbox.data / "machine.json").write_text(json.dumps({"tools": {"chrome": {"path": str(recorded)}}}))
        self.assertEqual(chrome.find_chrome(self.sandbox), str(recorded))

    def test_installed_version_is_the_one_dist_info_folder(self):
        site = self.sandbox.data / "venv" / "lib" / "python3.12" / "site-packages"
        site.mkdir(parents=True)
        self.assertIsNone(support.installed_version(self.sandbox))
        (site / "trazo-0.8.1.dist-info").mkdir()
        self.assertEqual(support.installed_version(self.sandbox), "0.8.1")
        (site / "trazo-0.8.0.dist-info").mkdir()  # a broken install, which the hook also refuses
        self.assertIsNone(support.installed_version(self.sandbox))


if __name__ == "__main__":
    unittest.main()
