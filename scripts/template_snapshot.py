"""A committed copy of this checkout, uncommitted changes included, for Copier.

Copier renders a dirty template from a copy that drops tracked files
matching a .gitignore (template/.gitignore ignores output/, so the
.gitkeep files under figures/output and tables/output vanish). A commit
of the working tree renders as a release tag does.

    python3 scripts/template_snapshot.py TARGET   # for shell scripts
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# The Copier the stored template variants are rendered and checked with
# (`uvx --from COPIER copier`): another version may write other bytes.
COPIER = "copier==9.19.1"


def git(*args: str, cwd: Path = REPO, **options) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "user.name=trazo", "-c", "user.email=trazo@localhost",
                           "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false",
                           "-c", "core.hooksPath=/dev/null", *args], cwd=cwd, check=True, **options)


def snapshot(target: Path) -> Path:
    """Clone this checkout's HEAD into target, apply the working tree's
    changes and untracked files, commit, and return target."""
    head = git("rev-parse", "HEAD", capture_output=True, text=True).stdout.strip()
    git("clone", "--quiet", "--shared", "--no-checkout", str(REPO), str(target))
    git("checkout", "--quiet", "--detach", head, cwd=target)
    patch = git("diff", "--binary", "HEAD", capture_output=True).stdout
    if patch:
        git("apply", "--whitespace=nowarn", "-", cwd=target, input=patch)
    untracked = git("ls-files", "--others", "--exclude-standard", "-z", capture_output=True).stdout
    for name in filter(None, untracked.decode().split("\0")):
        source, copy = REPO / name, target / name
        if name.endswith("/") or not source.is_file():
            continue  # a nested repository (a worktree under .claude/, say)
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_bytes(source.read_bytes())
        copy.chmod(source.stat().st_mode)
    git("add", "-A", cwd=target)
    git("commit", "--quiet", "--allow-empty", "-m", "Snapshot of the working tree", cwd=target)
    return target


if __name__ == "__main__":
    snapshot(Path(sys.argv[1]))
