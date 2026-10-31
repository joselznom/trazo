"""Preview: table regeneration in the watcher."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)

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
                        raise KeyboardInterrupt

                with patch.object(preview, "ROOT", Path(folder)), \
                     patch.object(preview.time, "sleep", side_effect=tick), \
                     patch.object(preview, "regenerate_tables", side_effect=AssertionError("ran the generator")), \
                     patch.object(preview, "render_spec_tables") as render, patch.object(preview, "build"):
                    with self.assertRaises(KeyboardInterrupt):
                        preview.watch()
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
                    raise KeyboardInterrupt

            def regenerate():
                text = source.read_text()
                generated.append(text)
                output.write_text(text)
                if len(generated) == 1:
                    source.write_text("second edit")
                return True

            with patch.object(preview, "ROOT", root), \
                 patch.object(preview.time, "sleep", side_effect=tick), \
                 patch.object(preview, "regenerate_tables", side_effect=regenerate), \
                 patch.object(preview, "build"):
                with self.assertRaises(KeyboardInterrupt):
                    preview.watch()
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
                    raise KeyboardInterrupt

            with patch.object(preview, "ROOT", root), \
                 patch.object(preview.time, "sleep", side_effect=tick), \
                 patch.object(preview, "regenerate_tables", return_value=False), \
                 patch.object(preview, "build") as build:
                with self.assertRaises(KeyboardInterrupt):
                    preview.watch()
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
