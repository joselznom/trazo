"""A failed editor save must preserve the last text and its usable undo history."""

import errno
import http.client
import json
import os
import threading
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

import _support  # selects the disposable paper before importing trazo
from test_table import Restore

from trazo import diagram, preview, review_history, source_publish, table
from trazo.local_server import LocalServer
from trazo.review_history import History


@contextmanager
def preserve(*paths: Path) -> Iterator[None]:
    saved = {path: path.read_bytes() if path.exists() else None for path in paths}
    try:
        yield
    finally:
        for path, data in saved.items():
            if data is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(data)


class SaveHistoryTests(Restore):
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

    def call(self, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        try:
            conn.request("POST" if body is not None else "GET", path,
                         json.dumps(body) if body is not None else None,
                         {"Content-Type": "application/json"})
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    @contextmanager
    def refuse_replace(self, destination: Path, error: int) -> Iterator[None]:
        """The system refuses to publish `destination`: its rename, or the atomic
        exchange that publishes an existing file where the filesystem has one."""
        replace, exchange = os.replace, source_publish.exchange

        def refuse(source: Any, target: Any, *args: Any, **kwargs: Any) -> None:
            if Path(target) == destination:
                raise OSError(error, os.strerror(error), str(target))
            replace(source, target, *args, **kwargs)

        def refuse_exchange(source: Any, target: Any) -> None:
            if Path(target) == destination:
                raise OSError(error, os.strerror(error), str(target))
            exchange(source, target)

        with patch("os.replace", side_effect=refuse), patch.object(source_publish, "exchange", refuse_exchange):
            yield

    def edit(self, kind: str, text: str) -> tuple[str, dict[str, Any]]:
        """The endpoint and body of an editor save that sets `text` in the fixture's
        "results" table (its spec, or the shared spec) or "example" diagram."""
        if kind == "diagram":
            _, state = self.call("/__diagrams/file/example")
            state["diagram"]["nodes"][0]["text"] = text
            return "/__diagrams/file/example", {"diagram": state["diagram"], "base_sha256": state["sha256"]}
        _, state = self.call("/__table?label=results")
        return "/__table" + ("/shared" if kind == "shared" else ""), {
            "label": "results", "ops": [{"op": "set", "value": text,
                                         "path": "maps.method.forest" if kind == "shared" else "title"}],
            "base_sha256": state["shared"]["sha256"] if kind == "shared" else state["sha256"]}

    def test_history_failure_does_not_publish_table_or_diagram_text(self) -> None:
        for kind in ("table", "shared", "diagram"):
            path = (table.spec_path("results") if kind == "table" else table.SHARED_PATH
                    if kind == "shared" else diagram.diagram_path("example"))
            history = diagram.HISTORY_PATH if kind == "diagram" else table.HISTORY_PATH
            for error in (errno.ENOSPC, errno.EACCES):
                with self.subTest(kind=kind, error=error), preserve(path, history):
                    before = path.read_bytes()
                    endpoint, body = self.edit(kind, "Saved after retry")
                    with self.refuse_replace(history, error):
                        status, result = self.call(endpoint, body)
                    self.assertEqual(status, 500, result)
                    self.assertIn(os.strerror(error), result["error"])
                    self.assertEqual(path.read_bytes(), before, "failed save replaced the user's last text")
                    status, result = self.call(endpoint, body)
                    self.assertEqual(status, 200, result)
                    self.assertNotEqual(path.read_bytes(), before)
                    if kind == "diagram":
                        diagram.undo()
                    else:
                        self.assertEqual(self.call("/__table/undo", {"label": "results"})[0], 200)
                    self.assertEqual(path.read_bytes(), before)

    def test_unreadable_pending_file_does_not_block_other_saves(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source, other = root / "source.txt", root / "other.txt"
            history = History(root / "history.json")
            source.write_bytes(b"Original text")
            history.write_file(source, b"First save", "First")
            with self.refuse_replace(source, errno.ENOSPC), self.assertRaises(OSError):
                history.write_file(source, b"Failed save", "Failed")
            read = Path.read_bytes

            def unreadable(path: Path) -> bytes:
                if path == source:
                    raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(path))
                return read(path)

            with patch.object(Path, "read_bytes", unreadable):
                history.write_file(other, b"Independent text", "Independent")
            self.assertEqual(other.read_bytes(), b"Independent text")
            self.assertEqual(source.read_bytes(), b"First save")
            history.undo()
            history.undo()
            self.assertFalse(other.exists())
            self.assertEqual(source.read_bytes(), b"Original text")

    def test_successful_autosaves_keep_one_undo_group(self) -> None:
        path, history = diagram.diagram_path("example"), diagram.HISTORY_PATH
        with preserve(path, history):
            original = path.read_bytes()
            for text in ("First autosave", "Second autosave"):
                _, state = self.call("/__diagrams/file/example")
                state["diagram"]["nodes"][0]["text"] = text
                status, saved = self.call("/__diagrams/file/example", {
                    "diagram": state["diagram"], "base_sha256": state["sha256"],
                    "autosave": True, "client": "group-regression"})
                self.assertEqual(status, 200, saved)
            self.assertEqual(saved["history"], "merged")
            diagram.undo()
            self.assertEqual(path.read_bytes(), original)

    def test_failed_autosave_preserves_the_last_save_and_grouped_undo(self) -> None:
        path, history = diagram.diagram_path("example"), diagram.HISTORY_PATH
        for destination in (history, path):
            with self.subTest(destination=destination.name), preserve(path, history):
                original = path.read_bytes()
                _, state = self.call("/__diagrams/file/example")
                state["diagram"]["nodes"][0]["text"] = "First autosave"
                body = {"diagram": state["diagram"], "base_sha256": state["sha256"],
                        "autosave": True, "client": "history-regression"}
                status, saved = self.call("/__diagrams/file/example", body)
                self.assertEqual(status, 200, saved)
                previous = path.read_bytes()
                body["base_sha256"] = saved["sha256"]
                body["diagram"]["nodes"][0]["text"] = "Second autosave"
                with self.refuse_replace(destination, errno.ENOSPC):
                    status, result = self.call("/__diagrams/file/example", body)
                self.assertEqual(status, 500, result)
                self.assertIn(os.strerror(errno.ENOSPC), result["error"])
                self.assertEqual(path.read_bytes(), previous, "failed autosave destroyed the last saved version")
                diagram.undo()
                self.assertEqual(path.read_bytes(), original, "failed autosave broke the previous undo group")

    def test_a_failed_save_at_the_history_limits_keeps_the_oldest_undo(self) -> None:
        for limit in ("HISTORY_LIMIT", "HISTORY_BYTES"):
            with self.subTest(limit=limit), TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                source, history = root / "source.txt", History(root / "history.json")
                source.write_text("0")
                for n in range(1, 4):
                    history.write_file(source, str(n).encode(), f"save {n}")
                # The store is full: three entries, or exactly their size.
                full = 3 if limit == "HISTORY_LIMIT" else sum(len(json.dumps(e, ensure_ascii=False))
                                                             for e in history.load())
                with patch.object(review_history, limit, full):
                    with self.refuse_replace(source, errno.ENOSPC), self.assertRaises(OSError):
                        history.write_file(source, b"failed", "failed save")
                    for _ in range(3):
                        history.undo()
                self.assertEqual(source.read_text(), "0", "the failed save cost the oldest undo")

    def test_a_failed_source_save_keeps_every_undo(self) -> None:
        name = "manuscript/main/01_introduction.md"
        path = _support.ROOT / name
        with TemporaryDirectory() as directory, preserve(path), \
                patch.object(preview, "FILE_HISTORY_PATH", Path(directory) / "file_history.json"), \
                patch.object(preview, "EDITS_PATH", Path(directory) / "edits.json"):
            original = path.read_bytes()

            def save(text: str) -> tuple[int, Any]:
                _, state = self.call(f"/__source?file={name}")
                return self.call("/__source", {"file": name, "text": state["text"] + text,
                                               "base_sha256": state["sha256"]})
            for n in range(1, 4):
                self.assertEqual(save(f"\nSave {n}.\n")[0], 200)
            with patch.object(preview, "FILE_HISTORY_LIMIT", 3):
                with self.refuse_replace(path, errno.ENOSPC):
                    status, result = save("\nNever saved.\n")
                self.assertEqual(status, 500, result)
                status, state = self.call(f"/__source?file={name}")
                self.assertTrue(state["can_undo"], "undo looks unavailable after a failed save")
                for _ in range(3):
                    status, result = self.call("/__source", {"op": "undo", "file": name})
                    self.assertEqual(status, 200, result)
            self.assertEqual(path.read_bytes(), original)

    def test_a_damaged_table_or_diagram_history_is_set_aside_once(self) -> None:
        damaged = b'[{"kind": "file", "label": "cut sho'
        for kind in ("table", "diagram"):
            path = diagram.diagram_path("example") if kind == "diagram" else table.spec_path("results")
            history = diagram.HISTORY_PATH if kind == "diagram" else table.HISTORY_PATH
            page = "/__diagrams/file/example" if kind == "diagram" else "/__table?label=results"

            def save() -> tuple[int, Any]:
                """The editor's page, then its save: the first that fails, or the save."""
                status, result = self.call(page)
                if status != 200:  # the table editor's page reads the history; the diagram's does not
                    return status, result
                return self.call(*self.edit(kind, "Saved over a damaged history"))
            with self.subTest(kind=kind), preserve(path, history):
                history.parent.mkdir(parents=True, exist_ok=True)
                history.write_bytes(damaged)
                status, result = save()
                kept = list(history.parent.glob(f"{history.stem}.corrupt-*{history.suffix}"))
                self.addCleanup(lambda found=kept: [p.unlink(missing_ok=True) for p in found])
                self.assertEqual(status, 500, result)
                self.assertEqual(len(kept), 1, "the damaged history was not set aside")
                self.assertIn(str(kept[0]), result["error"])
                self.assertEqual(kept[0].read_bytes(), damaged)
                status, result = save()
                self.assertEqual(status, 200, result)
                if kind == "diagram":
                    diagram.undo()
                else:
                    self.assertEqual(self.call("/__table/undo", {"label": "results"})[0], 200)
                self.assertNotIn(b"Saved over a damaged history", path.read_bytes())


if __name__ == "__main__":
    unittest.main()
