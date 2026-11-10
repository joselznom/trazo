# The plugin in detail

How the Claude Code and Codex plugin works. The install steps are in the [README](../README.md).

The repository root is also a Claude Code plugin (`.claude-plugin/`) and
a Codex plugin for the ChatGPT app (`.codex-plugin/`, `.codex-mcp.json`): the
trazo skills, the `setup`, `update`, `open` and `close` skills and an MCP
server (`trazo mcp`) with the tools `setup_status`,
`doctor`, `build`, `check`, `paper_create`, `paper_update`, `plugin_update`,
`preview_start` and `preview_stop`. Before setup, the
server also runs from a clone's development venv: run `make test-package`
(or `cd packages/trazo && uv sync`) once to create it.

Both manifests start the server with one command, `plugin/mcp`, so the
same plugin works everywhere: on macOS and Linux that file runs
`plugin/mcp.sh`; on Windows a file without an extension cannot run, so
both apps look the name up through `PATHEXT` and run `plugin/mcp.cmd`.
Setup is `plugin/scripts/setup.sh` on macOS and Linux and
`plugin/scripts/setup.ps1` (Windows PowerShell 5.1) on Windows; both
install into the per-user data folder only.

A `SessionStart` hook (`hooks/hooks.json`, `plugin/session-start.sh`;
new sessions, `/clear` and compaction, not resume or fork) checks offline,
on macOS and Linux, whether setup is done and the installed trazo is the plugin's
version. If not, it gives the agent a factual note (not shown to the user)
that the trazo tools need the setup skill, so the agent can bring it up
when the user asks for paper work. On Windows the hook does nothing (or
fails quietly where no Git Bash runs it); the stand-in server below tells
the agent instead.

`claude plugin validate .` checks both manifests (it warns that the root
`CLAUDE.md` is not loaded by plugins). Without the per-user install or
the clone's venv, or when neither provides the MCP server, the launcher
starts a stand-in server (`plugin/mcp-standin.pl`, run by the system Perl
on macOS; `plugin/mcp-standin.ps1`, run by Windows PowerShell, on Windows)
whose only tool, `setup_status`, tells the agent to run the setup skill.
The app connects to it, so a new chat after setup gets the real server.

For a client that declares MCP Apps views, the server also offers the
preview as a full-screen view (`trazo.mcp_app`). Both manifests turn it
off (`plugin/mcp --no-views`), so the preview opens in the app's browser
only: the Claude app draws the view but refuses the tool calls it makes,
and the ChatGPT app draws it for some accounts only, as a second window
beside the browser.

Updates start from the agent. The server's instructions ask it to call
`setup_status` with the paper before the first trazo work in a chat, and
`setup_status` asks GitHub every time for the latest release (the version
in `.claude-plugin/plugin.json` on `main` of the plugin's `repository`;
3 s timeout, unknown when offline). When the plugin is older, the update
skill calls `plugin_update`, which runs the app's own CLI on the app's
config folder: `claude plugin marketplace update` and `claude plugin
update`, or `codex plugin marketplace upgrade` and `codex plugin add`. The
Claude app does not update third-party marketplaces by itself. The next
chat loads the new plugin; there the hook asks for setup, which reinstalls
trazo, and `setup_status` says `"next": "paper"` until `paper_update` runs.

MCP subprocess tools (build, check, doctor, paper creation/update and preview input
preparation) have a 1200-second deadline. Set `TRAZO_MCP_TIMEOUT` to a positive,
finite number of seconds in the MCP server's environment for a different limit.
On expiry the server stops the call's process group (on Windows its job object,
which also holds what nested calls start), returns a tool error with the
available command output and remains available for the next request. A call
whose descendant keeps the output open after it exits returns its real result
after five seconds, with a warning, instead of waiting for the deadline.
Deliberately detached processes are outside the call's group and keep their own
lifetime. Standalone CLI builds and the preview have deadlines of their own
(`docs/measurement-boundaries.md`, "Scope of the deadlines").

## Checks by hand

These checks are covered fully or partly by the
[acceptance harness](../scripts/acceptance/README.md). Each item names its
scenarios. Run them with `make acceptance` and read the `report.md` it
prints. Before a release, the owner checks the remaining app behavior
by hand. Agents run the scenarios, not the manual app checks.

- In a project made from the template, type `/`: note what the template's
  own `.claude/skills` and the plugin's `/trazo:*` copies look like
  together: three `/trazo:*` commands and the project's `comments` copy
  in Claude. Record what ChatGPT's selector shows.
  Claude's list: `n2-slash-commands`. ChatGPT's selector: by hand.

- With 8765 busy (`nc -lk 127.0.0.1 8765`; a listener on all addresses
  does not block 127.0.0.1), `preview_start` returns another port; the
  agent must read the URL from `.build/preview.json`.
  `n1-port-busy` covers the tool; the agent opening the returned URL: by hand.

- Read the connection time from the "Successfully connected … in N ms"
  line under `~/Library/Caches/claude-cli-nodejs/<folder>/mcp-logs-plugin-trazo-trazo/`.
  `n2-mcp-connection-time`.

- Force-quit the app during a `build` with the preview running:
  `.build/preview.json` is gone, the build stopped, the old DOCX kept.
  `n1-force-quit-build`.

- Resume a session before setup (reopen it from the sidebar, or
  `claude --resume`): the hook's note is not added again.
  `n2-resume-no-hook-note`.

- With an older trazo installed, the note says "update" and
  the setup skill reinstalls trazo without downloading uv or Pandoc again.
  `n1-old-install-update`, with the last release as the older install.

- After setup and a new chat, `setup_status` returns `"next": "ok"`.
  `n1-setup-status-ok`.

- With a newer trazo installed than the plugin (the other app's), the
  note asks for a plugin update and `setup_status` returns
  `"next": "plugin"`; the setup skill does not reinstall.
  `n1-install-newer-than-plugin` covers the hook and the tool; the agent
  running the update skill: by hand.

- With a release newer than the plugin on GitHub, the first trazo request
  in a chat updates the plugin through the app's CLI (`plugin_update`),
  and the next chat reinstalls trazo and updates the paper, with only
  plain words to the user. `n2-update-claude` covers `plugin_update` with
  the claude CLI; the agent's flow in both apps: by hand.

- Two sessions on one folder: the second `preview_start` reuses the first.
  `n1-two-sessions-share-preview`.

- Rename `packages/trazo/.venv` for a moment: `/mcp` shows trazo
  connected with one tool, `setup_status` returns `"next": "setup"`, and
  after renaming it back a new chat has all the tools without a reconnect.
  `n2-no-venv-standin` and `n2-codex-tools-by-state` (Codex) start from an
  empty data folder instead of renaming the venv.

- In each app's browser (the Claude Browser pane and the ChatGPT browser),
  write on the preview's text: click a paragraph and type, Enter, Backspace
  at its start, ⌘B, `@`, the right-click menu and a paste from Word. If a
  browser fails, turn writing off there before a release. By hand.

