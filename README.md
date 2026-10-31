# trazo

Write a scientific paper or report in plain text (Markdown), with help
from an AI agent, and get a Word document ready to share with coauthors
or submit to a journal. trazo is for researchers who write in Word today
and want references, figure and table numbers, and numbers computed from
their data to stay correct without fixing them by hand.

You get:

- a Word file with formatted citations, numbered figures and tables, and
  cross-references that renumber themselves;
- a live preview in the browser to read the paper, leave review comments
  for the agent, edit the text and see what changed;
- figures made by scripts, a diagram editor, tables computed from your
  data, and numbers in the text that come from the data;
- skills that teach the agent how to write, cite, make figures and tables,
  and review the paper.

It runs on macOS, as a plugin for the Claude desktop app or the ChatGPT
desktop app.

## Install (once per computer)

Both apps need git to add the plugin. A Mac that has never had developer
tools lacks it: open Terminal, run `xcode-select --install` and accept
Apple's dialog to install the Command Line Tools. This is the one step that
needs a terminal.

**Claude app**

1. Install the [Claude desktop app](https://claude.com/download) and sign in.
2. In the Code tab, in a local session, choose **+** next to the prompt
   box > **Plugins** > **Browse plugins** > **Add plugin** > **Add
   marketplace**, and enter `joselznom/trazo` (or the path of a local
   clone).
3. In the same browser, select `trazo` and add it. The app warns that the
   plugin runs a local server on this computer.
4. Start a new chat and ask the agent to "set up trazo". Setup downloads
   uv, Python, trazo and Pandoc into `~/Library/Application Support/trazo`
   and changes nothing else. Diagrams and the PNG/PDF exports need a
   Chromium-based browser: setup uses the Google Chrome, Edge or Chromium
   already on the Mac, and downloads a headless Chrome (about 110 MB) into
   the same folder when there is none.

**ChatGPT app** (it uses the same folder: after setup in one app, the
other finds it)

1. **Plugins** > **Add** > **Add a marketplace**, source
   `https://github.com/joselznom/trazo.git`, Git ref `main`.
2. Install `trazo` from that marketplace.
3. Start a new chat and ask the agent to "set up trazo". Allow the setup
   command to use the network and write outside the folder when the app
   asks. If it asks to trust trazo's hook, that hook only checks, offline,
   whether setup is needed.

**From a terminal** (a local clone)

```bash
claude plugin marketplace add /path/to/trazo
claude plugin install trazo@trazo
codex plugin marketplace add /path/to/trazo
codex plugin add trazo@trazo
```

## Create a paper

Ask the agent, for example: "create a new paper called 'My study' in
Documents, as an article with the example". It asks three things:

| Question | Answer |
|---|---|
| Kind | Article (for a journal: submission files and checklist) or report. |
| Example paper | Yes for sample text, data, a figure, a diagram, a table and a citation; no for empty sections. |
| Title | The paper's title. It can be provisional; change it later in `manuscript/metadata.yaml`. |

Open the new folder in a session and ask the agent to build the Word files
or open the preview (`/trazo:open` in Claude). Then read `README.md` in the
new folder: it is the user guide (writing, references, figures, tables,
the preview, working with the agent, a journal's format).

To keep versions, ask the agent to make the folder a git repository; the
preview's Diff tab compares with the last saved version.

## Read and review the paper

Ask the agent to open the preview, or type `/trazo:open` in Claude. In the
Claude app it opens in the Browser pane; the toolbar's **Browser** button
shows the pane if it is hidden. The "trazo preview (plugin)" launch entry
in `.claude/launch.json` uses port 8765. If that port is busy, `/trazo:open`
opens the other address returned by the preview instead.

In the ChatGPT app, the preview opens in the built-in browser. Leave
comments with **Annotate**, then send a message asking the agent to address
the browser comments. When MCP Apps are available for your account, the
preview also opens full screen from the tool call and in a **Paper
preview** tab beside the conversation. The browser works without MCP Apps.

Use `/trazo:comments` to ask the agent to address review comments, and
`/trazo:close` (`$trazo:close` in ChatGPT) to stop the preview. A second
session on the same folder reuses it; either session can close it. When
the session ends, the preview and any running build stop, but the last
good Word files in `output/` stay.

## Update

Update the plugin in the app's plugin browser. The next time the preview
is open, a pill with the new version appears in its top bar: click it,
then **Update**. That reinstalls trazo from the plugin's files (no
download) and brings the template's changes into the paper. A file you
changed that the template also changed gets a `<file>.new` beside it; ask
the agent to merge it. You can also ask the agent to "update the paper".

If the update needs a download (a new uv or Pandoc), the pill says to ask
the agent to set up trazo. If it says "Plugin", the other app installed a
newer trazo: update the plugin in this app too.

## Problems

| Message | Fix |
|---|---|
| The agent has no trazo tools | Ask it to set up trazo, then start a new chat |
| `trazo is not set up on this computer` | Ask the agent to set up trazo |
| `this paper pins trazo vX, but trazo vY is set up here` | Ask the agent to update the paper, or update the plugin and ask it to set up trazo |
| `Chrome not found` | Ask the agent to set up trazo: it downloads a headless Chrome (or install [Google Chrome](https://www.google.com/chrome/)) |
| `macOS sandbox blocks Chrome's access to LaunchServices` | Run the command from a terminal, or allow the agent to launch GUI processes |

In a terminal, `scripts/trazo doctor` inside a paper folder checks the
tools.

## For maintainers

The repository has two parts, versioned together with tags `vX.Y.Z`:

- `packages/trazo/`: the tools (preview, diagram and table editors,
  values, checks, agent skills), a Python package with no dependencies.
  See [its README](packages/trazo/README.md).
- `template/`: the project scaffold, rendered by
  [Copier](https://copier.readthedocs.io) (`copier.yml`) into the variants
  that `trazo new` and `trazo update` use.

The repository root is also the plugin (`.claude-plugin/`,
`.codex-plugin/`); [docs/plugin.md](docs/plugin.md) explains its technical
parts and the acceptance scenarios. Projects never vendor trazo: each
pins a release in `trazo.toml` and runs the per-user install through
`scripts/trazo`. Contributor rules are in [AGENTS.md](AGENTS.md).

You need [uv](https://docs.astral.sh/uv/), Make and Pandoc. `make
test-linux` also needs Docker.

```bash
git clone https://github.com/joselznom/trazo.git
cd trazo
make hooks           # once per clone: check each commit, test it before it is pushed
make lint            # ruff on trazo and on the acceptance harness
make test-package    # trazo unit tests
make test-template   # make each template variant with trazo new, verify
make test-update     # update between trazo template versions, verify
make test-linux      # the same CI steps in a Debian container (Docker)
make test-acceptance # unit tests of the acceptance harness
make acceptance      # plugin acceptance, offline n1 and the real CLIs n2
make acceptance-auto # what pre-push runs: chosen by changed files, cached
```

The acceptance harness (`scripts/acceptance/`) proves that the plugin
installs, updates and works without the desktop apps. With `make hooks`,
`git push` runs the scenarios that the changed files call for
(`make acceptance-auto`).
A scenario that passed is not run again until a file it depends on changes,
so a repeat costs seconds; the first run needs the data seed, which the
push downloads. Level n1 drives the plugin offline; n2 drives the real
`claude` and `codex` CLIs with cheap models. A skipped scenario (no login, a
usage limit) is listed and blocks only a release tag. By hand:
`make acceptance` (n1, n2), `--no-cache` to force a run; it prints a
`report.md` under `.build/acceptance/`. Level n2 uses your Claude and Codex
logins, read-only. The desktop apps and what an agent does with the plugin
are checked by the owner before a release. Agents do not run manual app
checks. See [the harness README](scripts/acceptance/README.md).

GitHub Actions runs only by hand. With `make hooks`, `git push` runs
`test-package`, `test-template` and `test-update` on a clean checkout of
the tip of each pushed branch or tag (a few minutes), then the acceptance
scenarios for what the branch changed since main, and cancels the push if
one fails. Pushing a `vX.Y.Z` tag also needs n1 and n2 to pass for what
changed since the previous release, skips included. `git push --no-verify`
skips it.

To try unreleased changes inside a real project, point its launcher at
your checkout:

```bash
export TRAZO_DEV=../trazo/packages/trazo
scripts/trazo doctor
```

To measure trazo on a thesis-sized paper (the stress paper), time its
build, preview start and checks:

```bash
uv run --project packages/trazo python packages/trazo/tests/stress_fixture.py bench /tmp/stress
```

### Release

1. Bump the version in `packages/trazo/pyproject.toml`,
   `packages/trazo/src/trazo/__init__.py`, `.claude-plugin/plugin.json`,
   `.codex-plugin/plugin.json` and `trazo_version` in `copier.yml`.
2. Run `make template-agents` if skills changed and `make
   template-diagrams` if the renderer changed, then `make
   template-variants` (trazo's copy of the rendered template; `make
   test-template` fails while it is stale).
3. Add a CHANGELOG entry, run `make test-linux` and commit as
   `chore(release): trazo and template vX.Y.Z`. Do not push yet.
4. The owner runs the manual desktop-app checks on that commit. Agents
   do not run them.
5. Push to `main`. The pre-push hook tests the commit and runs the n1 and
   n2 acceptance scenarios for it (`make acceptance-auto AUTO=push`).
6. Tag `vX.Y.Z` and push the tag. The hook runs n1 and n2 for what changed
   since the previous release; a skip blocks it (`AUTO=tag`).
7. Publish the GitHub release with the changelog section as its notes:
   `gh release create vX.Y.Z --title vX.Y.Z --notes-file <section>`.

## Licence

trazo is free and open source under the [MIT licence](LICENSE). The project
template in `template/` is under [MIT No Attribution](LICENSE-template), so
papers made from it need no licence notice, except the bundled IEEE citation
style (`bibliography/csl/ieee.csl`), which is CC BY-SA 3.0 and keeps its
licence and authors in the file. Bundled fonts keep their SIL Open Font
License texts.
