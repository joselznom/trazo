"""Preview: the watcher builds a write from the preview at once."""

import shutil
import threading
import time
import unittest
from unittest.mock import patch

import _support

from trazo import preview, preview_sources


@unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
class WatcherWakeTests(unittest.TestCase):
    def test_a_write_from_the_preview_is_built_and_synced_long_before_the_interval(self):
        source = _support.ROOT / "manuscript/main/01_introduction.md"
        text = source.read_text(encoding="utf-8")
        self.addCleanup(source.write_text, text, encoding="utf-8")
        state = {name: dict(value) for name, value in preview.STATE.items()}
        watcher = {**preview.WATCHER, "rebuild": set(), "wake": None}
        self.enterContext(patch.object(preview, "STATE", state))
        self.enterContext(patch.object(preview, "WATCHER", watcher))
        signal = preview.Signal()
        thread = threading.Thread(target=preview.watch, kwargs={"interval": 30, "stop_event": signal}, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(signal.set)
        deadline = time.monotonic() + 10
        while watcher["wake"] is None and time.monotonic() < deadline:
            time.sleep(0.01)
        before = state["manuscript"]["version"]

        source.write_text(text + "\nOne more sentence.\n", encoding="utf-8")
        preview_sources.mark_written()
        # With a 30 s interval, only the wake and the pass right after the build make it in time.
        while time.monotonic() < deadline and not (
                state["manuscript"]["version"] > before and watcher["synced"] > watcher["written"]):
            time.sleep(0.01)
        self.assertGreater(state["manuscript"]["version"], before, state["manuscript"]["error"])
        self.assertGreater(watcher["synced"], watcher["written"])


if __name__ == "__main__":
    unittest.main()
