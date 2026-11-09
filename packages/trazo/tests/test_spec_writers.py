"""A program that ignores trazo's lock must never lose a table or diagram spec to an editor save.

The other program writes inside the save's window, after its locked read and
before its publication, by wrapping a step the save takes there (writing its
undo history; for an undo, hashing the file it read); nothing in trazo knows
about the test.
"""

import http.client
import json
import os
import shutil
import sys
import threading
import unittest
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

import _support  # selects the disposable paper before importing trazo
from test_table import Restore

from trazo import diagram, portable, preview, review_history, table
from trazo.local_server import LocalServer

MARKER = "spec-key-another-program-wrote"
NO_EXCHANGE = "exfat"


class SpecWriters(Restore):
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
        super().setUp()
        self.diagram = diagram.diagram_path("example")
        self.addCleanup(self.diagram.write_bytes, self.diagram.read_bytes())
        self.addCleanup(diagram.HISTORY_PATH.unlink, missing_ok=True)
        for kept in ("conflicts", "displaced"):
            self.addCleanup(shutil.rmtree, preview.REVIEW_DIR / kept, True)
        for folder in (self.diagram.parent, table.spec_path("results").parent):
            self.addCleanup(lambda f=folder: [p.unlink() for p in f.glob("*.swap")])

    def call(self, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        try:
            conn.request("POST" if body is not None else "GET", path,
                         json.dumps(body) if body is not None else None, {"Content-Type": "application/json"})
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def write(self, path: Path, atomic: bool) -> None:
        """Another program's edit: valid JSON with one more top-level key."""
        new = path.read_text(encoding="utf-8").replace("{", '{"' + MARKER + '": 1, ', 1)
        if atomic:
            tmp = path.with_name("agent.tmp")
            tmp.write_text(new, encoding="utf-8")
            os.replace(tmp, path)
        else:
            path.write_text(new, encoding="utf-8")

    def writing_in_the_window(self, path: Path, atomic: bool, undo: bool = False) -> AbstractContextManager:
        done = []
        if undo:  # right after the undo's locked read of the file (revert_file), not an earlier read
            read = Path.read_bytes

            def read_then_write(self_path: Path) -> bytes:
                data = read(self_path)
                if not done and self_path == path and sys._getframe(1).f_code.co_name == "revert_file":
                    done.append(True)
                    self.write(path, atomic)
                return data
            return patch.object(Path, "read_bytes", read_then_write)
        save = review_history.History.save

        def history_then_write(history: review_history.History, entries: list) -> None:
            save(history, entries)
            if not done:
                done.append(True)
                self.write(path, atomic)
        return patch.object(review_history.History, "save", history_then_write)

    def as_filesystem(self, kind: str | None) -> AbstractContextManager:
        return nullcontext() if kind is None else patch.object(portable, "filesystem_type", lambda folder: kind)

    def filesystems(self) -> list[str | None]:
        return ([None] if portable.can_exchange(self.diagram.parent) else []) + [NO_EXCHANGE]

    def kept(self) -> list[str]:
        folder = preview.REVIEW_DIR / "conflicts"
        return [p.read_text(encoding="utf-8") for p in folder.rglob("*") if p.is_file()] if folder.is_dir() else []

    def save_table(self) -> tuple[int, Any]:
        _, state = self.call("/__table?label=results")
        return self.call("/__table", {"label": "results", "base_sha256": state["sha256"],
                                      "ops": [{"op": "set", "path": "title", "value": "Title typed in the editor."}]})

    def save_diagram(self) -> tuple[int, Any]:
        _, state = self.call("/__diagrams/file/example")
        state["diagram"]["nodes"][0]["text"] = "Text typed in the editor"
        return self.call("/__diagrams/file/example", {"diagram": state["diagram"], "base_sha256": state["sha256"]})

    def test_a_write_during_an_editor_save_stays_on_disk(self) -> None:
        cases = ((table.spec_path("results"), self.save_table, "Title typed in the editor."),
                 (self.diagram, self.save_diagram, "Text typed in the editor"))
        for kind in self.filesystems():
            for path, save, typed in cases:
                for atomic in (False, True):
                    with self.subTest(filesystem=kind or "native", file=path.name, atomic=atomic), \
                         self.as_filesystem(kind):
                        before = path.read_bytes()
                        with self.writing_in_the_window(path, atomic):
                            status, body = save()
                        self.assertEqual(status, 409, body)
                        self.assertIn(MARKER, path.read_text(encoding="utf-8"))
                        self.assertTrue(any(typed in k for k in self.kept()), body)
                        if path == self.diagram:  # what the editor's Keep mine and Load theirs use
                            self.assertEqual(body["text"], path.read_bytes().decode("utf-8"))
                        path.write_bytes(before)
                        shutil.rmtree(preview.REVIEW_DIR / "conflicts")

    def test_a_collision_records_no_undo(self) -> None:
        path, before = table.spec_path("results"), table.spec_path("results").read_bytes()
        with self.writing_in_the_window(path, atomic=True):
            self.assertEqual(self.save_table()[0], 409)
        path.write_bytes(before)
        status, body = self.call("/__table/undo", {"label": "results"})
        self.assertEqual(status, 400, body)  # nothing to undo
        self.assertEqual(path.read_bytes(), before)

    def test_an_undo_collides_like_a_save(self) -> None:
        path = table.spec_path("results")
        status, saved = self.save_table()
        self.assertEqual(status, 200, saved)
        with self.writing_in_the_window(path, atomic=True, undo=True):
            status, body = self.call("/__table/undo", {"label": "results"})
        self.assertEqual(status, 409, body)
        self.assertIn("while the undo was being written", body["error"])  # not the check before it
        self.assertIn(MARKER, path.read_text(encoding="utf-8"))

    def test_a_diagram_save_answers_the_hash_of_what_it_wrote(self) -> None:
        record = review_history.History.publish

        def publish_then_write(*args: Any, **kwargs: Any) -> Any:
            result = record(*args, **kwargs)
            self.write(self.diagram, atomic=False)  # right after the save published
            return result
        _, state = self.call("/__diagrams/file/example")
        edited = state["diagram"]
        edited["nodes"][0]["text"] = "Text typed in the editor"
        with patch.object(review_history.History, "publish", publish_then_write):
            status, saved = self.call("/__diagrams/file/example", {"diagram": edited, "base_sha256": state["sha256"]})
        self.assertEqual(status, 200, saved)
        edited["nodes"][0]["text"] = "A second edit in the editor"  # the editor keeps its own diagram
        status, body = self.call("/__diagrams/file/example", {"diagram": edited, "base_sha256": saved["sha256"]})
        self.assertEqual(status, 409, body)
        self.assertIn(MARKER, self.diagram.read_text(encoding="utf-8"))

    def test_a_late_write_through_an_open_file_is_kept(self) -> None:
        if not portable.can_exchange(self.diagram.parent):
            self.skipTest("no atomic exchange on this filesystem")
        for save, path, read in ((self.save_table, table.spec_path("results"), "/__table?label=results"),
                                 (self.save_diagram, self.diagram, "/__diagrams/file/example")):
            with self.subTest(file=path.name):
                before = path.read_text(encoding="utf-8")
                fd = os.open(path, os.O_RDWR)  # another program opened the spec before the save
                self.addCleanup(os.close, fd)
                status, body = save()
                self.assertEqual(status, 200, body)
                os.pwrite(fd, before.replace("{", '{"' + MARKER + '": 1, ', 1).encode(), 0)
                self.assertEqual(self.call(read)[0], 200)  # the editor's next read keeps it
                self.assertTrue(any(MARKER in k for k in self.kept()), path)

    def test_a_late_write_to_the_shared_spec_is_kept_by_the_next_table_read(self) -> None:
        if not portable.can_exchange(self.diagram.parent):
            self.skipTest("no atomic exchange on this filesystem")
        shared = table.SHARED_PATH
        _, state = self.call("/__table?label=results")
        fd = os.open(shared, os.O_RDWR)  # another program opened _shared.json before the save
        self.addCleanup(os.close, fd)
        before = shared.read_text(encoding="utf-8")
        status, body = self.call("/__table/shared", {"label": "results", "base_sha256": state["shared"]["sha256"],
                                                     "ops": [{"op": "set", "path": "maps.method.forest", "value": "Forest"}]})
        self.assertEqual(status, 200, body)
        os.pwrite(fd, before.replace("{", '{"' + MARKER + '": 1, ', 1).encode(), 0)
        self.assertEqual(self.call("/__table?label=results")[0], 200)
        self.assertTrue(any(MARKER in k for k in self.kept()))

    def test_trouble_after_publishing_is_reported_by_both_editors(self) -> None:
        if not portable.can_exchange(self.diagram.parent):
            self.skipTest("no atomic exchange on this filesystem")
        blocker = preview.REVIEW_DIR / "displaced"
        blocker.write_text("a file where the folder should be")
        self.addCleanup(blocker.unlink, missing_ok=True)
        for save, path in ((self.save_table, table.spec_path("results")), (self.save_diagram, self.diagram)):
            with self.subTest(file=path.name):
                before = path.read_bytes()
                status, body = save()
                self.assertEqual(status, 200, body)
                self.assertTrue(body.get("warning"), body)
                # The replaced file stays beside the spec, where a program holding it open still writes.
                self.assertEqual([p.read_bytes() for p in path.parent.glob(path.name + ".*.swap")], [before])
        status, undone = self.call("/__table/undo", {"label": "results"})
        self.assertEqual(status, 200, undone)
        self.assertIn("could not set the replaced version aside", undone["undone"])
        self.assertTrue(undone["warning"], undone)
        # A spec table's caption saves and undoes through the table editor: its reply says so too.
        state = preview.get_table_caption("supplementary", "tbl:cohorts")
        saved = preview.change_table_caption({"doc": "supplementary", "id": "tbl:cohorts", "base_sha256": state["sha256"],
                                              "changes": {"title": "A caption typed in the preview."}})
        self.assertTrue(saved.get("warning"), saved)
        undone = preview.change_table_caption({"op": "undo", "doc": "supplementary", "id": "tbl:cohorts"})
        self.assertTrue(undone.get("warning"), undone)

    def test_copies_of_a_file_outside_the_project_stay_in_the_review_folder(self) -> None:
        with TemporaryDirectory() as folder:
            source = Path(folder).resolve() / "outside.json"
            source.write_text("{}", encoding="utf-8")
            history = review_history.History(Path(folder) / "history.json")
            with self.writing_in_the_window(source, atomic=True), self.assertRaises(RuntimeError) as caught:
                history.write_file(source, b'{"saved": 1}', "save")
            kept = caught.exception.extra["kept"]  # type: ignore[attr-defined]
            self.assertTrue(kept)
            for copy in kept:
                self.assertTrue((_support.ROOT / copy).resolve().is_relative_to(preview.REVIEW_DIR.resolve()), copy)
            self.assertIn(MARKER, source.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
