"""GET /__bundle: a served page with what it loads inlined (trazo.page_bundle)."""

import base64
import json
import re
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

import _support

from trazo import preview
from trazo.local_server import LocalServer
from trazo.project import ASSETS_DIR

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


class PageBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = Path(tempfile.mkdtemp(prefix="trazo-bundle-"))
        cls.addClassCleanup(shutil.rmtree, cls.out, True)
        image = Path(_support.ROOT) / "figures" / "external" / "tiny.png"
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(PNG)
        cls.addClassCleanup(image.unlink)
        # Pandoc's own markup for the stylesheet; a figure with a relative image.
        (cls.out / "manuscript.html").write_text(
            '<!doctype html><html><head><meta charset="utf-8"><title>Paper</title>'
            '<link rel="stylesheet" href="/__assets/preview/preview.css" /></head><body>'
            '<figure><img src="figures/external/tiny.png" alt="x"></figure>'
            '<img src="https://example.org/remote.png">'
            '<script src="/__assets/preview/preview-model.js"></script></body></html>', encoding="utf-8")
        patcher = patch.object(preview, "OUT_DIR", cls.out)
        patcher.start()
        cls.addClassCleanup(patcher.stop)
        server = LocalServer(("127.0.0.1", 0), preview.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        cls.addClassCleanup(server.server_close)
        cls.addClassCleanup(server.shutdown)
        cls.base = f"http://127.0.0.1:{server.server_port}"

    def get(self, page):
        with urllib.request.urlopen(f"{self.base}/__bundle?page={page}", timeout=10) as reply:
            return json.load(reply)

    def test_a_document_page_comes_back_with_everything_local_inlined(self):
        bundle = self.get("/")
        self.assertEqual(bundle["path"], "/manuscript.html")
        html = bundle["html"]
        self.assertIn("<title>Paper</title>", html)
        self.assertNotRegex(html, r'<script src="/')
        self.assertNotIn('rel="stylesheet"', html)
        self.assertNotIn('rel="preload"', html)
        self.assertNotIn('rel="icon"', html)
        self.assertNotIn("@import", html)
        self.assertNotIn("@font-face", html)
        self.assertIn((ASSETS_DIR / "preview" / "preview-model.js").read_text(encoding="utf-8"), html)
        # The image keeps its path, which the viewer reads to find diagrams and exports.
        self.assertIn(f'<img data-trazo-src="figures/external/tiny.png" src="data:image/png;base64,'
                      f'{base64.b64encode(PNG).decode()}" alt="x">', html)
        self.assertIn('src="https://example.org/remote.png"', html)  # not local: left as it was
        # The page's head, as the browser gets it, comes along.
        self.assertIn("window.TRAZO_CONFIG", html)
        self.assertIn('<meta name="trazo-page"', html)

    def test_fonts_come_back_for_font_face(self):
        fonts = self.get("/manuscript.html")["fonts"]
        css = (ASSETS_DIR / "app" / "theme.css").read_text()
        self.assertEqual(len(fonts), len(re.findall(r"@font-face", css)))
        sans = next(f for f in fonts if f["family"] == "IBM Plex Sans" and f["descriptors"]["weight"] == "400"
                    and f["descriptors"]["style"] == "normal")
        self.assertEqual(base64.b64decode(sans["data"])[:4], b"wOF2")
        self.assertEqual(set(sans["descriptors"]), {"style", "weight", "display", "unicodeRange"})

    def test_the_diagram_editor_keeps_the_renderer_byte_for_byte(self):
        # The renderer's sha256 is in every diagram export stamp.
        html = self.get("/diagrams")["html"]
        self.assertIn("<script>" + (ASSETS_DIR / "diagram" / "renderer.js").read_text(encoding="utf-8") + "</script>",
                      html)

    def test_the_diagram_editor_gets_the_figure_fonts(self):
        # The editor declares them by URL at run time, which a view's CSP blocks.
        tokens = json.loads((Path(_support.ROOT) / "figures" / "style" / "tokens.json").read_text())["fonts"]
        fonts = [f for f in self.get("/diagrams")["fonts"] if f["family"] == tokens["family"]]
        self.assertEqual(sorted(f["descriptors"]["weight"] for f in fonts),
                         sorted(str(f["weight"]) for f in tokens["files"] if f["family"] == tokens["family"]))
        self.assertEqual(base64.b64decode(fonts[0]["data"])[:4], b"\x00\x01\x00\x00")  # TrueType
        self.assertFalse(any(f["family"] == tokens["family"] for f in self.get("/")["fonts"]))

    def test_figure_fonts_come_only_from_the_fonts_folder(self):
        tokens_path = Path(_support.ROOT) / "figures" / "style" / "tokens.json"
        original = tokens_path.read_text()
        self.addCleanup(tokens_path.write_text, original)
        tokens = json.loads(original)
        outside = Path(tempfile.mkdtemp(prefix="trazo-outside-"))
        self.addCleanup(shutil.rmtree, outside, True)
        (outside / "private.ttf").write_bytes(b"private")
        tokens["fonts"]["files"] = [{"family": "Escape", "weight": 400, "file": f} for f in
                                    ("../../../outside.ttf", str(outside / "private.ttf"), "../style/tokens.json")]
        tokens_path.write_text(json.dumps(tokens))
        self.assertFalse(any(f["family"] == "Escape" for f in self.get("/diagrams")["fonts"]))

    def test_a_path_that_is_no_page_is_not_found(self):
        for page in ("/nope.html", "/__status", "/__assets/preview/preview.js"):
            with self.subTest(page=page), self.assertRaises(urllib.error.HTTPError) as caught:
                self.get(page)
            self.assertEqual(caught.exception.code, 404)
            caught.exception.close()


if __name__ == "__main__":
    unittest.main()
