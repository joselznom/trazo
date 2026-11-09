"""Preview: table regeneration in the watcher."""

import http.client
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)
from test_mcp import Session, free_port, make_project, wait_until

from trazo import preview, preview_watch


class PreviewTableTests(unittest.TestCase):
    def test_only_a_projects_own_tables_stage_reruns_on_a_spec_change(self):
        project = preview.PROJECT
        self.assertTrue(preview_watch.own_tables_stage(project))  # the fixture's [commands] tables
        with patch.dict(project.config["commands"], {"tables": []}), patch.dict(project.config["build"], {"steps": []}):
            self.assertFalse(preview_watch.own_tables_stage(project))
            with patch.dict(project.config["build"], {"steps": [{"name": "tables"}]}):
                self.assertTrue(preview_watch.own_tables_stage(project))
            # Without one, a spec change re-renders the spec tables in-process.
            with tempfile.TemporaryDirectory() as folder:
                spec = Path(folder) / "tables/specs/example.json"
                spec.parent.mkdir(parents=True)
                spec.write_text("{}")
                polls = 0

                def tick(_):
                    nonlocal polls
                    polls += 1
                    if polls == 1:
                        spec.write_text('{"title": "Edited."}')
                    elif polls > 2:
                        return True  # stop the watcher

                with patch.object(preview, "ROOT", Path(folder)), \
                     patch.object(preview, "regenerate_tables", side_effect=AssertionError("ran the generator")), \
                     patch.object(preview, "render_spec_tables") as render, patch.object(preview, "build"):
                    # Through stop_event, not a patched time.sleep: another thread\'s sleep would count as a pass.
                    preview.watch(stop_event=Mock(**{"wait.side_effect": tick}))
                render.assert_called_once()

    def test_edit_during_table_regeneration_is_rebuilt_on_next_poll(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "tables/specs/example.json"
            output = root / "tables/output/md/example.md"
            source.parent.mkdir(parents=True)
            output.parent.mkdir(parents=True)
            source.write_text("original")
            generated = []
            polls = 0

            def tick(_):
                nonlocal polls
                polls += 1
                if polls == 1:
                    source.write_text("first edit")
                if polls > 3:
                    return True  # stop the watcher

            def regenerate():
                text = source.read_text()
                generated.append(text)
                output.write_text(text)
                if len(generated) == 1:
                    source.write_text("second edit")
                return True

            with patch.object(preview, "ROOT", root), \
                 patch.object(preview, "regenerate_tables", side_effect=regenerate), \
                 patch.object(preview, "build"):
                # Through stop_event, not a patched time.sleep: another thread\'s sleep would count as a pass.
                preview.watch(stop_event=Mock(**{"wait.side_effect": tick}))
            self.assertEqual(generated, ["first edit", "second edit"])
            self.assertEqual(output.read_text(), "second edit")

    def test_failed_table_build_is_not_hidden_by_a_manuscript_edit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "tables/specs/example.json"
            manuscript = root / "manuscript/main/section.md"
            source.parent.mkdir(parents=True)
            manuscript.parent.mkdir(parents=True)
            source.write_text("original")
            manuscript.write_text("original")
            polls = 0

            def tick(_):
                nonlocal polls
                polls += 1
                if polls == 1:
                    source.write_text("invalid table text")
                elif polls == 2:
                    manuscript.write_text("revised manuscript")
                else:
                    return True  # stop the watcher

            with patch.object(preview, "ROOT", root), \
                 patch.object(preview, "regenerate_tables", return_value=False), \
                 patch.object(preview, "build") as build:
                # Through stop_event, not a patched time.sleep: another thread\'s sleep would count as a pass.
                preview.watch(stop_event=Mock(**{"wait.side_effect": tick}))
                build.assert_not_called()

    def test_tables_command_comes_from_the_project(self):
        self.assertTrue(preview.regenerate_tables())

    def test_an_empty_tables_command_runs_the_built_in_stage(self):
        from trazo import build_graph
        with patch.dict(preview.PROJECT.config["commands"], {"tables": []}):
            with patch.object(build_graph.Builder, "tables") as tables:
                self.assertTrue(preview.regenerate_tables())
            tables.assert_called_once_with()
            failure = build_graph.BuildError("tables: generate failed (exit 1)")
            with patch.object(build_graph.Builder, "tables", side_effect=failure), \
                    patch.dict(preview.STATE, {"manuscript": {}}):
                self.assertFalse(preview.regenerate_tables())
                self.assertIn("generate failed", preview.STATE["manuscript"]["error"])


@unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
class PreviewTableFailureTests(unittest.TestCase):
    def test_corrupt_table_keeps_last_page_and_error_until_data_are_fixed(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = make_project(Path(folder).resolve())
            config = root / "trazo.toml"
            config.write_text("\n".join(row for row in config.read_text().splitlines()
                                        if not row.startswith("tables = ")) + "\n")
            source = root / "manuscript/main/01_introduction.md"
            source.write_text(source.read_text() + "\n!include ../../tables/output/md/results.md\n")
            data = root / "tables/output/data/results.json"
            original = json.loads(data.read_text())
            port = free_port()
            session = Session(root.parent, port)
            try:
                start = session.tool("preview_start", project=str(root))
                self.assertFalse(start["isError"], start["content"])
                port = start["structuredContent"]["port"]

                def get(path: str) -> bytes:
                    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                    try:
                        connection.request("GET", path)
                        response = connection.getresponse()
                        self.assertEqual(response.status, 200)
                        return response.read()
                    finally:
                        connection.close()

                def status() -> dict:
                    return json.loads(get("/__status"))

                self.assertTrue(wait_until(lambda: status()["manuscript"]["ok"]))
                initial = status()["manuscript"]["version"]
                page = get("/manuscript.html")
                self.assertIn(b"0.812", page)
                changed_at = time.time()
                data.write_text("{corrupt")
                self.assertTrue(wait_until(lambda: status()["synced"] > changed_at))
                failure = status()["manuscript"]
                self.assertFalse(failure["ok"], "corrupt table data were reported as a successful preview")
                self.assertIn("results", failure["error"])
                self.assertIn("JSON", failure["error"])
                self.assertEqual(failure["version"], initial)
                self.assertEqual(get("/manuscript.html"), page)

                changed_at = time.time()
                source.write_text(source.read_text() + "\nManuscriptEditedDuringTableFailure.\n")
                self.assertTrue(wait_until(lambda: status()["synced"] > changed_at))
                self.assertFalse(status()["manuscript"]["ok"], "a prose edit hid the table error")
                self.assertEqual(get("/manuscript.html"), page)

                original["records"][0]["value"]["est"] = .813
                data.write_text(json.dumps(original))
                self.assertTrue(wait_until(lambda: status()["manuscript"]["ok"]
                                           and status()["manuscript"]["version"] > initial))
                recovered = get("/manuscript.html")
                self.assertIn(b"ManuscriptEditedDuringTableFailure", recovered)
                self.assertIn(b"0.813", recovered)
                self.assertEqual(status()["manuscript"]["error"], "")
            finally:
                session.close()
