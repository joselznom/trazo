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

### New

- The plugin now works on Linux (x64 and ARM) in Claude Code and Codex:
  setup downloads uv, Python and Pandoc into `~/.local/share/trazo`
  (`$XDG_DATA_HOME/trazo` when that is set), and a headless Chrome on x64
  when no Chrome, Edge or Chromium is installed. The session hook and the
  MCP server's launcher now find a Linux install too (#5).
- trazo is now tested on Intel Macs as well as Apple silicon: setup, the
  Word build, diagram export and the preview (#4).

### Fixed

- In the Claude app, `/trazo:open` shows the preview only in the Browser
  pane. Before, it also showed an "Open full screen" card that could not load
  the paper ("IPC not available for MCP tool call").
- The preview starts at once on a Mac whose network is slow to answer name
  lookups; before, it could give up after 20 seconds with an empty message.
- On a busy computer, moving between pages of the preview no longer fades
  into a blank page before the paper appears.
- Setup and the tools check say when the Chrome they found does not start
  (on Linux, a computer without a desktop's libraries) instead of showing
  it as ready (#5).

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
