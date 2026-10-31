# Changelog

<!--
Written for users: each section becomes the notes of its GitHub release
(scripts/release-notes.py). A pull request that changes what users see adds
its line under "Unreleased"; a release renames that heading to
"## vX.Y.Z (YYYY-MM-DD)". Shape of a section:

  One sentence: what this release brings.

  ### New / ### Changed / ### Fixed / ### Removed   (only those with entries)
  - What changed for the user, in the product's words (#PR).

  ### Upgrade notes   (only when the user must do more than update)

Internal changes (tests, CI, refactoring) stay out.
-->

## Unreleased

### Fixed

- The preview starts at once on a Mac whose network is slow to answer name
  lookups; before, it could give up after 20 seconds with an empty message.

## v1.0.0 (2026-10-31)

First public release. trazo turns a paper written in Markdown, with help
from an AI agent, into a Word document ready for coauthors or a journal:

- formatted citations, numbered figures and tables, and cross-references
  that renumber themselves;
- figures made by scripts, a diagram editor, tables computed from data,
  and numbers in the text that come from the data;
- a live preview in the browser to read the paper, leave review comments
  for the agent, edit the text and see what changed;
- skills that teach the agent to write, cite, make figures and tables, and
  review the paper;
- a plugin for the Claude and ChatGPT desktop apps on macOS, with a
  one-step setup that downloads everything it needs.
