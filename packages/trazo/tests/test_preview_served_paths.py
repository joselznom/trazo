"""Preview: the paths and assets the server serves."""

import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _support

from trazo import preview
from trazo.project import ASSETS_DIR


class ServedPathTests(unittest.TestCase):
    def translate(self, url):
        handler = preview.Handler.__new__(preview.Handler)
        return Path(handler.translate_path(url))

    def test_assets_come_from_the_package(self):
        self.assertEqual(self.translate("/__assets/preview/preview.js"),
                         ASSETS_DIR / "preview" / "preview.js")
        self.assertTrue((ASSETS_DIR / "preview" / "preview.css").is_file())

    def test_the_document_model_script_is_served(self):
        self.assertEqual(self.translate("/__assets/preview/preview-model.js"),
                         ASSETS_DIR / "preview" / "preview-model.js")

    def test_bundled_fonts_are_served_as_woff2(self):
        css = (ASSETS_DIR / "app" / "theme.css").read_text()
        files = set(re.findall(r'url\("../preview/([^"/]+\.woff2)"\)', css))
        self.assertTrue(files)
        handler = preview.Handler.__new__(preview.Handler)
        for name in sorted(files):
            with self.subTest(font=name):
                path = self.translate(f"/__assets/preview/{name}")
                self.assertEqual(path, ASSETS_DIR / "preview" / name)
                self.assertEqual(path.read_bytes()[:4], b"wOF2")
                self.assertEqual(handler.guess_type(str(path)), "font/woff2")
        for licence in ("OFL-IBM-Plex-Sans", "OFL-IBM-Plex-Mono", "OFL-Source-Serif-4"):
            self.assertTrue((ASSETS_DIR / "preview" / f"{licence}.txt").is_file())

    def test_pages_paint_once_their_shell_and_fonts_are_ready(self):
        from trazo import server
        page = server.decorate_page("<html><head></head><body><p>Text</p>"
                                    '<script src="/x.js"></script></body></html>').decode()
        head = page[:page.index("</head>")]
        self.assertIn('<link rel="expect" href="#pk-shell-ready" blocking="render">', head)
        # The marker comes after the page scripts, so the first paint has the shell.
        self.assertTrue(page.endswith('<script src="/x.js"></script>'
                                      '<span id="pk-shell-ready" hidden></span></body></html>'))
        css = (ASSETS_DIR / "app" / "theme.css").read_text()
        preloads = re.findall(r'<link rel="preload" href="/__assets/preview/([^"]+)" as="font"', head)
        self.assertEqual(len(preloads), len(server.FIRST_FONTS))
        for name in preloads:
            with self.subTest(font=name):
                self.assertIn(f'url("../preview/{name}")', css)

    def test_pages_preload_the_italic_faces_of_the_manuscript(self):
        # Italic text in the body (serif) and in captions and tables (sans) is
        # laid out with the first layout; a face fetched later re-lays out the
        # whole paper when it arrives.
        from trazo import server
        page = server.decorate_page("<html><head></head><body></body></html>").decode()
        preloads = re.findall(r'<link rel="preload" href="/__assets/preview/([^"]+)" as="font"', page)
        self.assertIn("source-serif-4-latin-wght-italic.woff2", preloads)
        self.assertIn("ibm-plex-sans-latin-400-italic.woff2", preloads)

    def test_pages_name_the_trazo_favicon(self):
        from trazo import server
        page = server.decorate_page("<html><head></head><body></body></html>").decode()
        link = re.search(r'<link rel="icon" href="(/[^"]+)" type="image/svg\+xml">', page[:page.index("</head>")])
        path = self.translate(link.group(1))
        self.assertEqual(path, ASSETS_DIR / "app" / "favicon.svg")
        self.assertTrue(path.read_text().startswith("<svg "))

    def test_the_page_stamp_changes_with_the_page_and_with_the_viewer_scripts(self):
        # The viewer keeps where it found each comment under this stamp.
        from trazo import server

        def stamp(page):
            return re.search(r'<meta name="trazo-page" content="(\w+)">',
                             server.decorate_page(page, stamp=True).decode())[1]
        page = "<html><head></head><body><p>Text</p></body></html>"
        before = stamp(page)
        self.assertEqual(stamp(page), before)
        self.assertNotEqual(stamp(page.replace("Text", "Text.")), before)
        with tempfile.TemporaryDirectory() as folder:  # a trazo update with other viewer code
            shutil.copytree(preview.PREVIEW_ASSETS, folder, dirs_exist_ok=True)
            script = Path(folder) / preview.VIEWER_SCRIPTS[-1]
            script.write_text(script.read_text() + "\n")
            with patch.object(preview, "PREVIEW_ASSETS", Path(folder)):
                self.assertNotEqual(stamp(page), before)

    def test_the_viewer_makes_no_external_requests(self):
        for name in ("preview.js", "preview.css", "preview-model.js"):
            text = (ASSETS_DIR / "preview" / name).read_text()
            self.assertNotRegex(text, r"https?://(?!127\.0\.0\.1[:/])", name)

    def test_figures_come_from_the_project(self):
        self.assertEqual(self.translate("/figures/diagrams/example.json"),
                         _support.ROOT / "figures/diagrams/example.json")

    def test_other_paths_are_not_served(self):
        for url in ("/trazo.toml", "/__assets/preview/../../project.py",
                    "/figures/../trazo.toml", "/__assets/preview/x/y.js",
                    "/manuscript/main/01_introduction.md"):
            with self.subTest(url=url):
                self.assertEqual(self.translate(url).name, "__missing__")
