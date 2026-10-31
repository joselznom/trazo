"""`uv sync` of a project's .venv where uv cannot write its own cache (the ChatGPT app's sandbox)."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)

from trazo.uv_sync import sync

# A uv that fails as uv 0.11 does in the sandbox unless its cache is the project's .cache/uv.
FAKE_UV = """#!/bin/sh
echo "${UV_CACHE_DIR:-default}" >> uv.log
case "$UV_CACHE_DIR" in */.cache/uv) exit 0 ;; esac
echo "error: Failed to initialize cache at \\`$HOME/.cache/uv\\`" >&2
exit 2
"""


@unittest.skipIf(os.name == "nt", "the fake uv is a shell script")
class UvSync(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="trazo-uv-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.uv = self.root / "uv"
        self.uv.write_text(FAKE_UV)
        self.uv.chmod(0o755)

    def test_the_papers_cache_is_used_when_uv_cannot_write_its_own(self):
        environ = {key: value for key, value in os.environ.items() if key != "UV_CACHE_DIR"}
        with patch.dict(os.environ, environ, clear=True):
            self.assertEqual(sync(str(self.uv), "PATH", self.root, False), 0)
        self.assertEqual((self.root / "uv.log").read_text().splitlines(),
                         ["default", str(self.root / ".cache" / "uv")])

    def test_another_failure_is_not_retried(self):
        self.uv.write_text('#!/bin/sh\necho x >> uv.log\necho "error: no lock" >&2\nexit 2\n')
        self.assertEqual(sync(str(self.uv), "PATH", self.root, True), 2)
        self.assertEqual((self.root / "uv.log").read_text(), "x\n")


if __name__ == "__main__":
    unittest.main()
