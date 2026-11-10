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

import _support  # noqa: F401  # selects the disposable paper before importing trazo
from test_table import Restore

from trazo import diagram, preview, source_publish, table
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

    def test_history_failure_does_not_publish_table_or_diagram_text(self) -> None:
        for kind in ("table", "shared", "diagram"):
            path = (table.spec_path("results") if kind == "table" else table.SHARED_PATH
                    if kind == "shared" else diagram.diagram_path("example"))
            history = diagram.HISTORY_PATH if kind == "diagram" else table.HISTORY_PATH
            for error in (errno.ENOSPC, errno.EACCES):
                with self.subTest(kind=kind, error=error), preserve(path, history):
                    before = path.read_bytes()
                    if kind == "diagram":
                        _, state = self.call("/__diagrams/file/example")
                        state["diagram"]["nodes"][0]["text"] = "Saved after retry"
                        endpoint = "/__diagrams/file/example"
                        body = {"diagram": state["diagram"], "base_sha256": state["sha256"]}
                    else:
                        _, state = self.call("/__table?label=results")
                        endpoint = "/__table" + ("/shared" if kind == "shared" else "")
                        body = {"label": "results", "ops": [{"op": "set", "value": "Saved after retry",
                                "path": "maps.method.forest" if kind == "shared" else "title"}],
                                "base_sha256": state["shared"]["sha256"] if kind == "shared" else state["sha256"]}
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


if __name__ == "__main__":
    unittest.main()
