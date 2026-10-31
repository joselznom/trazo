"""The viewer's scripts: the parts in preview.VIEWER_SCRIPTS."""

import re
import unittest

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)

from trazo import preview
from trazo.project import ASSETS_DIR

SCRIPTS = ASSETS_DIR / "preview"


class ViewerScriptTests(unittest.TestCase):
    def test_every_part_is_loaded_once(self):
        parts = sorted(path.name for path in SCRIPTS.glob("preview*.js") if path.name != "preview-model.js")
        self.assertEqual(sorted(preview.VIEWER_SCRIPTS), parts)

    def test_no_two_top_level_functions_share_a_name(self):
        # The parts share one global scope: a second declaration silently replaces the first.
        names = [name for script in preview.VIEWER_SCRIPTS
                 for name in re.findall(r"(?m)^  (?:async )?function\*? (\w+)", (SCRIPTS / script).read_text())]
        self.assertEqual(sorted({name for name in names if names.count(name) > 1}), [])

    def test_the_parts_make_no_external_requests(self):
        for name in preview.VIEWER_SCRIPTS:
            with self.subTest(part=name):
                self.assertNotRegex((SCRIPTS / name).read_text(), r"https?://(?!127\.0\.0\.1[:/])")


if __name__ == "__main__":
    unittest.main()
