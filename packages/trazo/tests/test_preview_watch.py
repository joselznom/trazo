"""Preview: the watcher builds a write from the preview at once."""

import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import _support

from trazo import preview, preview_edit_review, preview_sources
from trazo.build_lock import Busy


@unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
class WatcherWakeTests(unittest.TestCase):
    def watching(self, interval, deadline):
        """Start a watcher on copies of the preview's state; (STATE, WATCHER)
        once it has read the sources, or at `deadline` (time.monotonic)."""
        state = {name: dict(value) for name, value in preview.STATE.items()}
        watcher = {**preview.WATCHER, "rebuild": set(), "wake": None}
        self.enterContext(patch.object(preview, "STATE", state))
        self.enterContext(patch.object(preview, "WATCHER", watcher))
        signal = preview.Signal()
        thread = threading.Thread(target=preview.watch, kwargs={"interval": interval, "stop_event": signal}, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(signal.set)
        while watcher["wake"] is None and time.monotonic() < deadline:
            time.sleep(0.01)
        return state, watcher

    def test_a_write_from_the_preview_is_built_and_synced_long_before_the_interval(self):
        source = _support.ROOT / "manuscript/main/01_introduction.md"
        text = source.read_text(encoding="utf-8")
        self.addCleanup(source.write_text, text, encoding="utf-8")
        deadline = time.monotonic() + 10
        state, watcher = self.watching(30, deadline)
        before = state["manuscript"]["version"]

        source.write_text(text + "\nOne more sentence.\n", encoding="utf-8")
        preview_sources.mark_written()
        # With a 30 s interval, only the wake and the pass right after the build make it in time.
        while time.monotonic() < deadline and not (
                state["manuscript"]["version"] > before and watcher["synced"] > watcher["written"]):
            time.sleep(0.01)
        self.assertGreater(state["manuscript"]["version"], before, state["manuscript"]["error"])
        self.assertGreater(watcher["synced"], watcher["written"])

    def test_a_save_built_before_it_is_accepted_is_built_again(self):
        # A pass that reads the file after the save published it and before it was
        # accepted (absorb) builds the save as another program's pending edit.
        source = _support.ROOT / "manuscript/main/01_introduction.md"
        text = source.read_text(encoding="utf-8")
        self.addCleanup(source.write_text, text, encoding="utf-8")
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        self.enterContext(patch.object(preview, "FILE_HISTORY_PATH", folder / "file_history.json"))
        self.enterContext(patch.object(preview, "EDITS_PATH", folder / "edits.json"))
        preview_edit_review.ensure()  # nothing pending before the save
        deadline = time.monotonic() + 20
        state, watcher = self.watching(30, deadline)
        absorb, reader, read = preview_edit_review.absorb, preview_edit_review.reader, threading.Event()

        def reading():  # a page build reads the file and the baseline through it
            marked, seen = reader()

            def read_marked(path):
                found = marked(path)
                if Path(path).resolve() == source.resolve():
                    read.set()
                return found
            return read_marked, seen

        def read_first(*args):  # the file is published: a pass reads it before it is accepted
            watcher["wake"]()
            self.assertTrue(read.wait(10), "no pass read the saved file")
            return absorb(*args)
        self.enterContext(patch.object(preview_edit_review, "reader", reading))
        with patch.object(preview_edit_review, "absorb", read_first):
            preview_sources.change_source({"file": "manuscript/main/01_introduction.md", "text": text.replace("patients", "participants"),
                                           "base_sha256": preview_sources.sha256_of(source.read_bytes())})
        # A page holds its reload until `synced` passes `written`, then shows that build.
        while time.monotonic() < deadline and not watcher["synced"] > watcher["written"]:
            time.sleep(0.01)
        self.assertGreater(watcher["synced"], watcher["written"])
        page = (preview.OUT_DIR / "manuscript.html").read_text(encoding="utf-8")
        paragraph = next(line for line in page.splitlines() if "participants" in line)
        self.assertNotIn("pv-edit-", paragraph)  # not shown as another program's edit

    def test_tables_waiting_for_another_build_keep_the_interval(self):
        spec = _support.ROOT / "tables/specs/results.json"
        text = spec.read_text(encoding="utf-8")
        self.addCleanup(spec.write_text, text, encoding="utf-8")
        tries = []

        def locked():  # another build holds the project's lock the whole time
            tries.append(time.monotonic())
            raise Busy("held")
        self.enterContext(patch.object(preview, "render_spec_tables", locked))
        self.enterContext(patch.object(preview, "regenerate_tables", locked))
        deadline = time.monotonic() + 30
        self.watching(0.2, deadline)  # it has read the sources
        spec.write_text(text + " ", encoding="utf-8")
        while not tries and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(tries, "the tables stage never ran")
        time.sleep(1)
        # One try per interval (0.2 s), not a pass after another without a wait.
        self.assertLess(len([t for t in tries if t > tries[0] + 0.05]), 10)

if __name__ == "__main__":
    unittest.main()
