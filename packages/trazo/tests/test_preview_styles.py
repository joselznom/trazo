"""The viewer's stylesheet: preview.css and the parts it imports."""

import re
import unittest

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)

from trazo.project import ASSETS_DIR

STYLES = ASSETS_DIR / "preview"


class StylesheetTests(unittest.TestCase):
    def test_preview_css_imports_every_part(self):
        imported = re.findall(r'@import url\("(preview-[^"]+\.css)"\);', (STYLES / "preview.css").read_text())
        self.assertEqual(sorted(imported), sorted(path.name for path in STYLES.glob("preview-*.css")))

    def test_the_parts_make_no_external_requests(self):
        for path in sorted(STYLES.glob("preview-*.css")):
            with self.subTest(part=path.name):
                self.assertNotRegex(path.read_text(), r"https?://(?!127\.0\.0\.1[:/])")


if __name__ == "__main__":
    unittest.main()
