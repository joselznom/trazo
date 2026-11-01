#!/usr/bin/env python3
"""Print the GitHub release notes for a release: its CHANGELOG.md section and a fixed footer.

    python3 scripts/release-notes.py vX.Y.Z > notes.md
    gh release create vX.Y.Z --title "trazo vX.Y.Z" --notes-file notes.md

Fails when CHANGELOG.md has no `## vX.Y.Z (date)` section or the section is empty, so a release
is never published with the wrong notes. The compare link goes to the previous release tag.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "https://github.com/joselznom/trazo"
TAG = re.compile(r"v(\d+)\.(\d+)\.(\d+)")
# Same steps as the README's "Update" section, for users who read only the release page.
HOW_TO_UPDATE = ("**How to update.** Update trazo in the app's plugin browser (the ChatGPT app also updates it "
                 "when it starts). The next time a paper's preview is open, click the version pill in its top bar, "
                 "then **Update**.")


def section(changelog: str, tag: str) -> str:
    """The body of the `## <tag> (...)` section, without its heading, up to the next `## `."""
    heading = re.compile(rf"^## {re.escape(tag)} \(.+\)\s*$", re.MULTILINE)
    found = heading.search(changelog)
    if not found:
        raise SystemExit(f"CHANGELOG.md has no '## {tag} (date)' section; rename '## Unreleased' first")
    rest = changelog[found.end():]
    following = re.search(r"^## ", rest, re.MULTILINE)
    body = rest[: following.start()] if following else rest
    if not body.strip():
        raise SystemExit(f"the {tag} section of CHANGELOG.md is empty")
    return body.strip("\n")


def previous_tag(tag: str, tags: list[str]) -> str | None:
    """The highest vX.Y.Z tag below `tag` by number, or None for the first release."""
    wanted = TAG.fullmatch(tag)
    if not wanted:
        raise SystemExit(f"{tag} is not a release tag (vX.Y.Z)")
    number = tuple(map(int, wanted.groups()))
    below = [(n, t) for t in tags if (m := TAG.fullmatch(t)) and (n := tuple(map(int, m.groups()))) < number]
    return max(below)[1] if below else None


def notes(changelog: str, tag: str, tags: list[str]) -> str:
    if not TAG.fullmatch(tag):
        raise SystemExit(f"{tag} is not a release tag (vX.Y.Z)")
    parts = [section(changelog, tag), "---", HOW_TO_UPDATE]
    previous = previous_tag(tag, tags)
    if previous:
        parts.append(f"**All changes:** {REPO}/compare/{previous}...{tag}")
    return "\n\n".join(parts) + "\n"


def main(argv: list[str]) -> None:
    if len(argv) != 1:
        raise SystemExit(__doc__)
    tags = subprocess.run(["git", "tag", "--list", "v*"], cwd=ROOT, capture_output=True, text=True,
                          check=True).stdout.split()
    sys.stdout.write(notes((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), argv[0], tags))


if __name__ == "__main__":
    main(sys.argv[1:])
