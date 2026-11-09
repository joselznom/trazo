"""A program that ignores trazo's lock must never lose text to a browser save.

The other program writes inside the save's window, after its locked read and
before its publication, by wrapping a step the save takes there (writing its
undo entry); nothing in trazo knows about the test.
"""

import hashlib
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from unittest.mock import patch

import _support

from trazo import edit_diff, portable, preview, preview_blocks, preview_edit_review, preview_sources, source_publish
from trazo.local_server import LocalServer

FILE = "manuscript/main/01_introduction.md"
MARKER = "Text another program saved meanwhile."
NO_EXCHANGE = "exfat"  # a filesystem type without atomic exchange


def exact(path: Path) -> str:
    """A file's text with its line endings (read_text turns Windows' CRLF into LF)."""
    return path.read_bytes().decode("utf-8")


class IndependentWriters(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = LocalServer(("127.0.0.1", 0), preview.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self) -> None:
        self.path = _support.ROOT / FILE
        self.addCleanup(self.path.write_bytes, self.path.read_bytes())
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        for name, value in (("FILE_HISTORY_PATH", folder / "file_history.json"), ("EDITS_PATH", folder / "edits.json")):
            patcher = patch.object(preview, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for kept in ("conflicts", "displaced"):
            self.addCleanup(shutil.rmtree, preview.REVIEW_DIR / kept, True)
        self.addCleanup(lambda: [p.unlink() for p in self.path.parent.glob(self.path.name + ".*.swap")])

    def call(self, route: str, body: dict | None = None) -> tuple[int, dict]:
        port = self.server.server_port
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            headers = {"Host": f"127.0.0.1:{port}", "Content-Type": "application/json"}
            conn.request("GET" if body is None else "POST", route, None if body is None else json.dumps(body), headers)
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def load(self) -> dict:
        status, state = self.call(f"/__source?file={FILE}")
        self.assertEqual(status, 200, state)
        return state

    def save(self, state: dict, text: str) -> tuple[int, dict]:
        return self.call("/__source", {"file": FILE, "text": text, "base_sha256": state["sha256"]})

    def append(self, atomic: bool) -> None:
        new = self.path.read_text(encoding="utf-8") + f"\n{MARKER}\n"
        if atomic:
            tmp = self.path.with_name("agent.tmp")
            tmp.write_text(new, encoding="utf-8")
            os.replace(tmp, self.path)
        else:
            self.path.write_text(new, encoding="utf-8")

    def writing_in_the_window(self, atomic: bool, step: str = "save_file_history") -> AbstractContextManager:
        """The other program writes once, right after the save's `step`: writing
        its undo entry, or (for an undo) reading the history."""
        original = getattr(preview_sources, step)
        done = []

        def step_then_write(*args: object) -> object:
            result = original(*args)
            if not done:
                done.append(True)
                self.append(atomic)
            return result
        return patch.object(preview_sources, step, step_then_write)

    def filesystems(self) -> list[str | None]:
        """None: the real one, when it can swap files atomically; else one that cannot."""
        return ([None] if portable.can_exchange(self.path.parent) else []) + [NO_EXCHANGE]

    def as_filesystem(self, kind: str | None) -> AbstractContextManager:
        return nullcontext() if kind is None else patch.object(portable, "filesystem_type", lambda folder: kind)

    def test_write_between_locked_read_and_publication_stays_on_disk(self) -> None:
        for kind in self.filesystems():
            for atomic in (False, True):
                with self.subTest(filesystem=kind or "native", atomic=atomic), self.as_filesystem(kind):
                    before = self.path.read_bytes()
                    try:
                        state = self.load()
                        browser = state["text"] + "\nThe browser's paragraph.\n"
                        with self.writing_in_the_window(atomic):
                            status, body = self.save(state, browser)
                        self.assertEqual(status, 409, body)
                        self.assertIn(MARKER, self.path.read_text(encoding="utf-8"))
                        kept = [exact(_support.ROOT / p) for p in body["kept"]]
                        self.assertIn(browser, kept)
                        state = self.load()
                        self.assertFalse(state["can_undo"])  # the save that did not happen has no undo
                        self.assertEqual(state["exact"], kind is None)
                        self.assertEqual(sorted(state["conflicts"]), sorted(body["conflicts"]))
                    finally:  # the next case starts from the fixture, whatever this one did
                        self.path.write_bytes(before)
                        shutil.rmtree(preview.REVIEW_DIR / "conflicts", ignore_errors=True)

    def test_undo_collides_like_a_save(self) -> None:
        state = self.load()
        status, saved = self.save(state, state["text"] + "\nA paragraph to undo.\n")
        self.assertEqual(status, 200, saved)
        with self.writing_in_the_window(atomic=True, step="read_file_history"):
            status, body = self.call("/__source", {"op": "undo", "file": FILE})
        self.assertEqual(status, 409, body)
        self.assertIn(MARKER, self.path.read_text(encoding="utf-8"))
        self.assertIn(state["text"], [exact(_support.ROOT / p) for p in body["kept"]])

    def test_a_save_answers_the_hash_of_what_it_wrote(self) -> None:
        state = self.load()
        original = preview_edit_review.absorb

        def absorb_then_write(*args: object) -> None:
            original(*args)  # after the save published its file
            self.append(atomic=False)
        with patch.object(preview_edit_review, "absorb", absorb_then_write):
            status, saved = self.save(state, state["text"] + "\nFirst paragraph.\n")
        self.assertEqual(status, 200, saved)
        status, body = self.save(saved, state["text"] + "\nFirst paragraph.\nSecond paragraph.\n")
        self.assertEqual(status, 409, body)
        self.assertIn(MARKER, self.path.read_text(encoding="utf-8"))

    def test_late_write_through_an_open_file_is_kept(self) -> None:
        if not portable.can_exchange(self.path.parent):
            self.skipTest("no atomic exchange on this filesystem")
        state = self.load()
        fd = os.open(self.path, os.O_RDWR)  # another program opened the file before the save
        self.addCleanup(os.close, fd)
        status, body = self.save(state, state["text"] + "\nThe browser's paragraph.\n")
        self.assertEqual(status, 200, body)
        os.pwrite(fd, (state["text"] + f"\n{MARKER}\n").encode(), 0)
        state = self.load()
        self.assertTrue(any(MARKER in (_support.ROOT / p).read_text(encoding="utf-8") for p in state["conflicts"]), state)

    def test_a_killed_save_leaves_no_unique_text_behind(self) -> None:
        unique = self.path.with_name(self.path.name + ".killed.swap")
        unique.write_bytes(b"Only copy of a version a killed save displaced.\n")
        duplicate = self.path.with_name(self.path.name + ".dup.swap")
        duplicate.write_bytes(self.path.read_bytes())
        # A save killed after recording its undo entry: the history names the
        # new text by its hash only, so its .swap is the one copy of that text.
        pending = "Text of a save killed before it was published.\n"
        unsent = self.path.with_name(self.path.name + ".pending.swap")
        unsent.write_bytes(pending.encode())
        preview.FILE_HISTORY_PATH.write_text(json.dumps([{
            "file": FILE, "before_text": exact(self.path),
            "after_sha": hashlib.sha256(pending.encode()).hexdigest()}]), encoding="utf-8")
        state = self.load()
        self.assertFalse(unique.exists() or unsent.exists())
        kept = sorted(exact(_support.ROOT / p) for p in state["conflicts"])
        self.assertEqual(kept, sorted(["Only copy of a version a killed save displaced.\n", pending]))
        # A known copy may still be open in another program: it stays for the grace window.
        self.assertTrue(duplicate.exists())
        with patch.object(source_publish, "DISPLACED_GRACE", -1):  # the window has passed
            self.load()
        self.assertFalse(duplicate.exists())

    def test_trouble_after_publishing_is_reported_by_every_writer(self) -> None:
        if not portable.can_exchange(self.path.parent):
            self.skipTest("no atomic exchange on this filesystem")
        blocker = preview.REVIEW_DIR / "displaced"
        blocker.write_text("a file where the folder should be")
        self.addCleanup(blocker.unlink, missing_ok=True)
        swaps = lambda: list(self.path.parent.glob(self.path.name + ".*.swap"))  # noqa: E731
        state = self.load()
        browser = state["text"] + "\nThe browser's paragraph.\n"
        status, body = self.save(state, browser)
        self.assertEqual((status, exact(self.path)), (200, browser), body)
        self.assertTrue(body["warning"] and body["can_undo"], body)
        # The replaced file stays beside the source, where a program holding it open still writes.
        self.assertEqual(len(swaps()), 1)
        self.load()
        self.assertEqual([exact(p) for p in swaps()], [state["text"]])
        status, undone = self.call("/__source", {"op": "undo", "file": FILE})
        self.assertEqual((status, undone["text"]), (200, state["text"]), undone)
        self.assertTrue(undone["warning"], undone)
        key = preview_blocks.editable(state["text"])[0].key
        status, block = self.call("/__block", {"file": FILE, "h": key, "markdown": "Typed on the page."})
        self.assertEqual(status, 200, block)
        self.assertTrue(block["warning"], block)
        self.call("/__edits?doc=manuscript")  # takes the baseline
        self.path.write_text(self.path.read_text(encoding="utf-8").replace("Typed on the page.", "We followed"), encoding="utf-8")
        status, listed = self.call("/__edits?doc=manuscript")
        status, rejected = self.call("/__edits", {"op": "reject", "edits": listed["edits"]})
        self.assertEqual((status, rejected["done"]), (200, len(listed["edits"])), rejected)
        self.assertTrue(rejected["warning"], rejected)

    def test_a_disk_without_exchange_says_its_check_is_not_exact(self) -> None:
        with self.as_filesystem(NO_EXCHANGE):
            state = self.load()
            status, body = self.save(state, state["text"] + "\nThe browser's paragraph.\n")
        self.assertEqual(status, 200, body)
        self.assertIs(body["exact"], False)
        self.assertIs(state["exact"], False)

    def test_writing_on_the_page_collides_like_a_save(self) -> None:
        text = self.path.read_text(encoding="utf-8")
        key = preview_blocks.editable(text)[0].key
        typed = "A paragraph typed on the page."
        with self.writing_in_the_window(atomic=True):
            status, body = self.call("/__block", {"file": FILE, "h": key, "markdown": typed})
        self.assertEqual(status, 409, body)
        self.assertIn(MARKER, self.path.read_text(encoding="utf-8"))
        self.assertTrue(any(typed in (_support.ROOT / p).read_text(encoding="utf-8") for p in body["kept"]), body)

    def test_rejecting_an_edit_leaves_a_write_made_meanwhile(self) -> None:
        status, _ = self.call("/__edits?doc=manuscript")  # takes the baseline
        self.assertEqual(status, 200)
        self.path.write_text(self.path.read_text(encoding="utf-8").replace("We studied", "We followed"), encoding="utf-8")
        status, listed = self.call("/__edits?doc=manuscript")
        self.assertTrue(listed["edits"], listed)
        original = edit_diff.reject

        def reject_then_write(*args: object) -> str:
            result = original(*args)  # inside the rejection's window, after its locked read
            self.append(atomic=True)
            return result
        with patch.object(edit_diff, "reject", reject_then_write):
            status, body = self.call("/__edits", {"op": "reject", "edits": listed["edits"]})
        self.assertEqual(status, 200, body)
        self.assertIn(MARKER, self.path.read_text(encoding="utf-8"))
        self.assertEqual((body["done"], len(body["missed"])), (0, len(listed["edits"])), body)
        self.assertFalse(self.load()["can_undo"])



@unittest.skipUnless(sys.platform == "darwin" or sys.platform.startswith("linux"), "reads statfs")
class FilesystemTypes(unittest.TestCase):
    def test_the_type_comes_from_the_right_field_and_an_allowed_one_swaps(self) -> None:
        # A misread field would send every disk to the degraded path, and the
        # tests above would still pass there.
        self.assertEqual(portable.filesystem_type("/dev" if sys.platform == "darwin" else "/proc"),
                         "devfs" if sys.platform == "darwin" else "0x9fa0")  # PROC_SUPER_MAGIC
        with tempfile.TemporaryDirectory(dir=_support.ROOT) as folder:
            a, b = Path(folder, "a"), Path(folder, "b")
            a.write_text("a")
            b.write_text("b")
            if portable.filesystem_type(folder) not in portable.EXCHANGE_FILESYSTEMS:
                self.skipTest(f"{portable.filesystem_type(folder)!r} has no atomic exchange")
            portable.exchange(a, b)
            self.assertEqual((a.read_text(), b.read_text()), ("b", "a"))


if __name__ == "__main__":
    unittest.main()
