"""A failed preview render must not truncate its last complete intermediate files."""

import errno
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _support  # noqa: F401  (sets TRAZO_ROOT for this process and its child)

from trazo.preview_render import render


@unittest.skipUnless(os.name == "posix" and shutil.which("pandoc"), "needs POSIX file limits and Pandoc")
class PreviewScratchTests(unittest.TestCase):
    def test_file_size_failure_preserves_complete_files_and_next_render_recovers(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            self.assertEqual(render("manuscript", output, for_counts=True)[0], "")
            source, body, page = (output / name for name in ("manuscript.md", "after_body.html", "manuscript.html"))
            before = {p: p.read_bytes() for p in (source, body, page)}
            self.assertGreater(len(before[body]), len(before[source]))
            # Real EFBIG after a partial kernel write: first the Markdown,
            # then the larger script include. No product function is replaced.
            for limit in (1, len(before[source])):
                with self.subTest(limit=limit):
                    result = subprocess.run([sys.executable, "-c", """
import errno, json, resource, signal, sys
from pathlib import Path
from trazo.preview_render import render
assert render("manuscript", Path(sys.argv[1]), for_counts=True)[0] == ""
old = resource.getrlimit(resource.RLIMIT_FSIZE)
signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
resource.setrlimit(resource.RLIMIT_FSIZE, (int(sys.argv[2]), old[1]))
try:
    render("manuscript", Path(sys.argv[1]), for_counts=True)
except OSError as error:
    print(json.dumps({"errno": error.errno}))
else:
    raise AssertionError("the filesystem did not interrupt the render")
finally:
    resource.setrlimit(resource.RLIMIT_FSIZE, old)
""", str(output), str(limit)], capture_output=True, text=True, timeout=20)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(json.loads(result.stdout)["errno"], errno.EFBIG)
                    for path, data in before.items():
                        self.assertEqual(path.read_bytes(), data, path.name)
                    self.assertEqual(render("manuscript", output, for_counts=True)[0], "")
                    for path, data in before.items():
                        self.assertEqual(path.read_bytes(), data, path.name)


if __name__ == "__main__":
    unittest.main()
