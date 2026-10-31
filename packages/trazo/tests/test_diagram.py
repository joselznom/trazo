"""Diagram checks, editor paths and the shared review folder."""

import contextlib
import io
import unittest
from pathlib import Path

import _support

from trazo import diagram, diagram_editor
from trazo.project import ASSETS_DIR


class EditorPathTests(unittest.TestCase):
    def test_editor_page_and_assets_come_from_the_package(self):
        self.assertEqual(diagram_editor.EDITOR_PAGE, ASSETS_DIR / "diagram" / "editor.html")
        page = diagram_editor.EDITOR_PAGE.read_text(encoding="utf-8")
        for name in ("editor.css", "icons.js", "renderer.js", "editor.js", "comments.js"):
            self.assertIn(f"/__assets/diagram/{name}", page)
            self.assertEqual(diagram_editor.static_file(f"__assets/diagram/{name}"),
                             ASSETS_DIR / "diagram" / name)

    def test_project_files_are_served_read_only(self):
        self.assertEqual(diagram_editor.static_file("figures/fonts/OFL.txt"),
                         _support.ROOT / "figures/fonts/OFL.txt")
        for bad in ("__assets/diagram/../renderer.js", "__assets/other/x.js",
                    "trazo.toml", "figures/diagrams/example.json", "figures/.hidden.png"):
            with self.subTest(bad=bad):
                self.assertIsNone(diagram_editor.static_file(bad))

    def test_diagram_files_are_in_the_project(self):
        self.assertEqual(diagram_editor.diagram_file("example"),
                         _support.ROOT / "figures/diagrams/example.json")
        self.assertEqual(diagram.RENDERER_PATH, ASSETS_DIR / "diagram" / "renderer.js")


class ValidationTests(unittest.TestCase):
    def test_new_rejects_invalid_heights_without_creating_a_file(self):
        for index, height in enumerate(("0", "-1", "nan", "inf")):
            name = f"invalid-height-{index}"
            path = diagram.diagram_path(name)
            with self.subTest(height=height), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(diagram.main(["new", name, "--height", height]), 1)
                self.assertFalse(path.exists())

    def test_fixture_diagram_is_valid(self):
        data, _ = diagram.read_diagram(diagram.diagram_path("example"))
        self.assertEqual(diagram.validate(data, diagram.load_tokens()), [])

    def test_unknown_colour_is_an_error(self):
        data, _ = diagram.read_diagram(diagram.diagram_path("example"))
        data["nodes"][0]["fill"] = "no-such-colour"
        codes = [i["code"] for i in diagram.validate(data, diagram.load_tokens())]
        self.assertTrue(codes)


@unittest.skipUnless(diagram.CHROME and Path(diagram.CHROME).exists(), "needs Chrome")
class ChromeTests(unittest.TestCase):
    def test_check_and_export(self):
        self.assertEqual(diagram.main(["check", "example"]), 0)
        self.assertEqual(diagram.main(["export", "example"]), 0)
        for suffix in ("svg", "pdf", "png"):
            self.assertTrue((_support.ROOT / f"figures/external/example.{suffix}").is_file())
        self.assertEqual(diagram.main(["check", "--all", "--exports"]), 0)
        # Font bytes belong to the render revision even when JSON/tokens stay identical.
        font = _support.ROOT / "figures/fonts/BarlowSemiCondensed-Regular.ttf"
        original = font.read_bytes()
        try:
            font.write_bytes(original + b"\0")
            self.assertEqual(diagram.export_state(diagram.diagram_path("example")), "stale")
        finally:
            font.write_bytes(original)
        self.assertEqual(diagram.export_state(diagram.diagram_path("example")), "current")


if __name__ == "__main__":
    unittest.main()
