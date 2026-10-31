"""Run every test against a temporary copy of tests/fixture.

trazo reads the project when it is imported, so test modules import this
module before any trazo module.
"""

import atexit
import os
import shutil
import tempfile
from pathlib import Path

FIXTURE = Path(__file__).resolve().parent / "fixture"
_TMP = Path(tempfile.mkdtemp(prefix="trazo-test-"))
atexit.register(shutil.rmtree, _TMP, True)
ROOT = (_TMP / "project").resolve()
shutil.copytree(FIXTURE, ROOT)
os.environ["TRAZO_ROOT"] = str(ROOT)
os.environ.pop("PREVIEW_REVIEW_DIR", None)


def docx(project, *names):
    """The separate Word files `trazo build --submission` makes for
    `project` (every active one, or `names`), without its other stages."""
    from trazo import build_graph
    builder = build_graph.Builder(project, submission=True)
    if not names and project.config["kind"] == "article":
        builder.remove_inactive_submission_files()
    for name in names or project.submission_documents:
        builder.pandoc_docx(name)
