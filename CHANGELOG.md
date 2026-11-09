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

- Write directly on the preview's text, with no Markdown in sight. Click a
  paragraph, a list item or a heading and type: leaving it saves your words
  to the Markdown, Esc discards what you typed since the last save and
  "Saved" appears by the build status. Citations, numbers from your data,
  formulas, code and images stay locked. Enter breaks a paragraph or opens a
  new one, Backspace at its start joins the one before, and blank lines in a
  paste make paragraphs. Select text and press ⌘B or ⌘I, or B or I in the
  selection bar, to toggle bold and italic. `@` opens a list of your references,
  figures, tables and values. If trazo cannot place a change exactly in the
  Markdown, it saves nothing, keeps your text and asks what to do (#20,
  #21, #22, #23, #24, #25).
- The preview marks what changed in your text since you last reviewed it,
  like tracked changes: inserted words in green, deleted ones struck
  through, new paragraphs with a bar. Click a mark to accept or reject it,
  or use **Edits** above the page to step through them or take them all at
  once. Resolve on a comment the agent answered accepts its edits, and the
  new Reject undoes them and reopens the comment. Your own saves from the
  preview are accepted as you make them. Works without git (#16).
- Tables typed by hand: **+ New table** in the table editor makes a table
  that is not computed from your data, empty or pasted from Excel, Word or
  Sheets, and adds it to the section you choose. Type in its cells, add rows
  and columns, and format text; it goes to Word like any table (#17).
- **Make editable** on a Markdown table turns it into a table typed by hand,
  with its caption, note and cross-references. A table that would lose
  something says why and keeps **Edit source** (#18).

### Changed

- Saved changes appear sooner in a visible preview, especially in longer papers.
- ⌘Z in the preview, outside the text, undoes your latest change in the
  document, a comment or a saved paragraph, where before it undid only
  comments (#25).
- Deleting a review comment now removes it for good, after a warning, in
  the preview and in the table and diagram editors. A comment shown in full
  has a trash button, shown when you point at it. Under Resolved, **Select**
  next to the filter lets you tick several comments, or **All**, and delete
  them together. The Deleted list is gone: comments left in it show as
  resolved, so you can delete them too. The agent can delete resolved
  comments when you ask it to (`trazo review delete`) (#14, #40).
- A second click on a resolved comment folds it back to one line (#40).
- The table editor speaks plainly: double-click a header or a row label to
  rename it, change decimals with − and +, add marks (a, *, †) and bold
  cells from a format bar, and ask the agent for new columns, rows or
  numbers with one click. Numbers from your data stay locked, and a cell
  tells you where its number comes from. A label or format that other
  tables or the text also use asks whether to change it in this table only
  or everywhere. Technical settings move under **Advanced** (#15).

### Fixed

- A corrupt manuscript undo history is preserved in a separate file and shown as
  an error. Retrying resumes saving and undoing new changes without repairing
  the old history by hand.
- Broken table data or specs now show an error in the preview and keep the last
  good page. Fixing them refreshes the page, including manuscript edits made meanwhile.
- A build, check or paper update run by the agent that hangs now stops after
  20 minutes with an error, instead of waiting forever.
- A failed table or diagram save keeps the last saved text and its undo history,
  including when a diagram autosave is interrupted.
- The table editor no longer shows "null" under a table without a note (#15).
- A save from the preview no longer overwrites a change that an agent, an
  editor or a sync app makes to the same file while it is saved, on a Mac's
  APFS disk or Linux's ext4, XFS, Btrfs or tmpfs: that version stays in the
  file, your text is copied to `notes/review/conflicts/`, and the Source tab
  shows the conflict and where the copy is. On other disks the Source tab says
  once that a write at the instant of saving can still be lost.

### Removed

- The preview's Diff tab, which compared a file with the last git commit:
  the marks in the text replace it (`git diff` still shows it) (#16).

## v1.2.0 (2026-11-02)

trazo now keeps itself up to date: the agent checks for a new version and installs it for you.

### Changed

- The agent now checks for a new trazo each time you ask it for trazo or
  paper work, and installs it: the plugin first, through the app (the
  Claude app never updated it by itself), then, in a new chat, trazo and
  your paper. It tells you only what changes for you. Ask it to *"update
  trazo"* to check at once (#13).

### Removed

- The version pill in the preview's top bar: it could miss a new release,
  and it could not update the plugin (#13).

### Fixed

- In the ChatGPT app, opening the preview shows it once, in the app's
  browser. Before, some accounts also got a full-screen copy of it (#12).

### Upgrade notes

- In the Claude app, update the trazo plugin by hand this one time: earlier
  versions cannot check for updates. Choose **+** next to the prompt box ›
  **Plugins**, uninstall **trazo**, then add it again from **Browse
  plugins**, and start a new chat; your papers stay as they are. With the
  Claude Code CLI installed, `claude plugin marketplace update trazo` and
  then `claude plugin update trazo@trazo` in a terminal do the same. The
  ChatGPT app updates the plugin when it starts.

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
