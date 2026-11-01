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

- In the ChatGPT app, opening the preview shows it once, in the app's
  browser. Before, some accounts also got a full-screen copy of it.

## v1.1.0 (2026-11-01)

trazo now runs on Linux, Windows and Intel Macs, and the preview is more reliable.

### New

- The plugin now works on Linux (x64 and ARM) in Claude Code and Codex:
  setup downloads uv, Python and Pandoc into `~/.local/share/trazo`
  (`$XDG_DATA_HOME/trazo` when that is set), and a headless Chrome on x64
  when no Chrome, Edge or Chromium is installed. The session hook and the
  MCP server's launcher now find a Linux install too (#5).
- trazo runs on Windows 10 and 11 (x64; not Windows on Arm) in the Claude
  and ChatGPT desktop apps. Setup installs into `%LOCALAPPDATA%\trazo`,
  with no admin rights, and uses the preinstalled Edge for diagrams and
  PNG and PDF exports. Papers get `scripts/trazo.cmd`, so their commands
  run from PowerShell and cmd too, and a `.gitattributes` that keeps LF line
  endings in a clone on Windows; update a paper with the plugin's
  `paper_update` to get them (#7).
- trazo is now tested on Intel Macs as well as Apple silicon: setup, the
  Word build, diagram export and the preview (#4).
- When setup finishes in a paper's folder and that paper was made with an
  earlier trazo, the agent suggests updating it (#10).

### Fixed

- In the Claude app, `/trazo:open` shows the preview only in the Browser
  pane. Before, it also showed an "Open full screen" card that could not load
  the paper ("IPC not available for MCP tool call") (#8).
- The preview starts at once on a Mac whose network is slow to answer name
  lookups; before, it could give up after 20 seconds with an empty message (#3).
- On a busy computer, moving between pages of the preview no longer fades
  into a blank page before the paper appears (#3).
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
