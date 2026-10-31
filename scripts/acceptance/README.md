# Acceptance harness

Proves that the plugin installs, updates and works for a user, without the
desktop apps. Python standard library only; dev tooling, not part of trazo.

| Level | Drives | Needs |
| --- | --- | --- |
| n1 | the plugin like an app does: MCP over stdio through `plugin/mcp.sh`, the SessionStart hook, the real `setup.sh`, headless Chrome | the data seed (made once; automatic runs make it themselves); no login |
| n2 | the real `claude` and `codex` CLIs in a throwaway config: install, update, slash commands, MCP state, hook note | `claude` and `codex` on `PATH` and their logins (see Credentials); cheap model calls |

The owner checks the desktop apps and agent behavior by hand before a release.
Agents run the scenarios, not the manual app checks. The scenario coverage is
listed in [docs/plugin.md](../../docs/plugin.md).

## Run

```bash
RUN="uv run --quiet --project packages/trazo python scripts/acceptance/run.py"
$RUN --list                          # the catalog; "not automated" has no implementation yet
$RUN --level n1 --level n2           # --level repeats; without --online, scenarios that need a missing seed are skipped, with the reason
$RUN --online --scenario 'n1-*'      # first time: makes the data seed (real setup.sh, downloads)
$RUN --changed-from main             # scenarios whose `areas` match files changed since main
$RUN --no-cache --level n1           # passes are cached by content; this runs them again
$RUN --scenario n1-sandbox --keep    # keep the sandbox (its path is in the report)
```

## Automatic runs

`run.py --auto push|tag` (`make acceptance-auto AUTO=push|tag`) chooses by itself, from the
files changed since the merge base with `origin/main` (else `main`), plus uncommitted and untracked
ones, and judges as a gate. Nothing selected ends in a second, with no seed, Chrome or model.

| Mode | Run by | Does |
| --- | --- | --- |
| `push` | `.githooks/pre-push`, for each pushed tip | n1 and n2 for the changed files; makes the seed when it is missing |
| `tag` | `.githooks/pre-push`, for a pushed `vX.Y.Z` tag (`--tag`) | n1 and n2 for the files changed since the previous release tag, mostly cached passes, and a skip blocks; the tag being pushed already exists locally, so "the last release" these runs update from and hash is the one before it (`ACCEPTANCE_RELEASING`); makes the seed when it is missing |

- **Cache.** A scenario that passed is not run again while nothing it depends on changed. The fingerprint
  hashes the content of the files matching its `areas` (tracked, uncommitted and untracked, never what
  `.gitignore` ignores), the harness code (`scripts/acceptance/` except its tests and docs), the
  highest release tag and the data seed's key (`resultcache.py`). Passes only, in `<cache>/results/`: a
  failure or a skip is never stored. The report says "cached"; `--no-cache` forces a run. The CLIs'
  versions are not in it.
- **What selects and what caches.** `catalog.toml` gives every scenario a shared list of areas: all of
  `packages/trazo/src/trazo/`, the package's `pyproject.toml` and `uv.lock`, `copier.yml`, `plugin/`,
  `hooks/`, and the plugin manifests. They install and run the checkout's real plugin, so a change anywhere in
  it can break them, and their fingerprint also hashes the whole checkout (the content of the plugin folder
  `versions.py` builds), so an input the catalog forgot still misses the cache. It leaves out `docs/`, the
  top-level Markdown files and the harness's own Markdown files, which nothing reads. A scenario that never touches
  the plugin says `checkout = false`. Every diff behind a selection or a fingerprint uses `--no-renames`,
  so a file moved out of an area counts as a change.
- **Skips block a release only.** A skipped scenario (no login, a usage limit) proved nothing, but an
  agent cannot renew a login: a push goes on, and the run lists it under "Not proven" with how to
  go on, on stderr. A skip is never cached, so the next
  run tries it again, and a `tag` run is blocked by it like a failure. A provider's usage-limit or quota message is a skip with
  that reason, never a pass.
- **Retry.** A failed scenario runs once more, and a pass is reported "passed on retry".

The Makefile wraps these: `make acceptance` (n1 and n2), `make acceptance-auto`, `make test-acceptance`.
`CHANGED_FROM=<ref>` becomes `--changed-from`; `ACCEPTANCE_ARGS` adds other options
(`make acceptance ACCEPTANCE_ARGS=--online` the first time).

Filters combine (all must match); `--scenario` is repeatable and takes globs
on ids. `--changed-from REF` uses `git diff --name-only REF` (committed and
uncommitted changes) plus untracked files.

Every selected scenario ends as passed, failed, skipped (with a reason),
not applicable or not automated (neither counted as passed). Not applicable
is a scenario with nothing to check, such as an update from the last release
in the `tag` run of a repository's first release; it never blocks. Exit
status 1 on any failure.
The report is in `.build/acceptance/<UTC timestamp>/report.md` and `.json`:
command line, commit, per scenario status, duration and evidence.

Unit tests of the harness (no scenario runs, no model is called): `make test-acceptance`.

## Add a scenario

See [scenarios.md](scenarios.md): the catalog row, the function, and what a scenario gets
(sandbox, plugin folders, MCP client, evidence).

## The cache and the data seed

The seed, the plugin folders and the cached passes live in `.build/acceptance/cache/` of the clone's
main checkout (`git rev-parse --git-common-dir`), one cache for every worktree and for pre-push's
clean checkout, so the seed (uv, Python and the install, a few hundred MB of downloads) is made once.
`TRAZO_ACCEPTANCE_CACHE=<folder>` puts it elsewhere. Entries are keyed by what setup downloads, by
commit or by the checkout's content, so sharing is safe. Each worktree still writes its reports and
traces under its own `.build/acceptance/`.

The cache follows the guard's rules (see Safety): a `TRAZO_ACCEPTANCE_CACHE`
that is, contains or lies inside the real home, `~/Library/Application Support/trazo`,
`~/.claude*` or `~/.codex` is refused before anything is written, and the seed's
`setup.sh` run is checked like a scenario's environment first. Runs share the
cache safely: building a seed and creating or pruning plugin folders happen under
an `flock` (`<cache>/seed-<key>.lock`, `<cache>/.plugins.lock`), the seed's
`.complete` is checked again under its lock, and pruning keeps any checkout
folder used within the last day (another worktree's run may be reading it) and
the most recent six.

The seed is made once, by `--online` or by an automatic `push` or `tag` run that selects a scenario
that needs it (one line on stderr says so; about 100 MB). It is the real `sh plugin/scripts/setup.sh` into
`<cache>/seed-<key>/`, keyed by what setup downloads (uv in
`setup.sh`, Pandoc and Chrome in `tool_setup.py`, the CPU). If the build fails (no network, a guard
refusal), the run stops with the reason and `make acceptance-auto AUTO=<mode>` to try again. A scenario gets a
copy-on-write clone (`cp -c -R`) without `venv/` and `machine.json`, which
hold absolute paths; `Sandbox.install()` rebuilds them offline. The seed
holds only what setup downloaded on this computer: a Pandoc or Chrome that is
already installed is not copied, so on a Mac that has both the seed is small
(uv and Python).

## n2 and the hosts

`hosts/` has one driver per CLI, used by n2. Both run in the sandbox's
throwaway `CLAUDE_CONFIG_DIR` and `CODEX_HOME` and skip, with the reason, when
the CLI or its login is missing.

- `hosts/claude.py`: `Claude(sb)` installs and updates the plugin from a folder
  marketplace (`marketplace_add`, `install_from`, `update`, `plugins`) and
  `run_session` runs `claude -p` with stream-json events (`claude_events.py`).
- `hosts/codex.py`: the same for `codex plugin`, plus hook trust, the MCP
  server's state through `codex app-server` (`codex_appserver.py`) and
  `session` for a model run (`codex_events.py` reads its JSONL and rollout).
- `hosts/trace.py`: `Trace`, what one session did, the same for both hosts:
  trazo's MCP tool calls (`trazo.<tool>`), the hook's notes, the MCP tools it
  had, cost and duration.
- n2 (`n2/`) registers the install, update, slash command, connection time,
  resume and stand-in scenarios, which need no model or only a cheap one: Haiku
  on Claude and `gpt-6-luna` at `low` effort on Codex (`hosts/codex.py`
  `MODEL`, `EFFORT`). A Claude session that outlives its timeout raises
  `TimeoutExpired`; a Codex one that fails raises `SessionFailed` with its partial `Trace`.
Setting (default in brackets):

| Variable | Effect |
| --- | --- |
| `TRAZO_ACCEPTANCE_CACHE` | the seed, plugin and result cache [the clone's main checkout, `.build/acceptance/cache`] |

Codex sessions are kept in `.build/acceptance/sessions/`.

### Credentials

The harness never writes, resets or deletes the macOS keychain, and never
runs `claude /login`, `claude logout`, `claude setup-token`, `codex login` or
`codex logout`.

- Claude: the only keychain command is one read of the item `Claude Code-credentials`
  (with the `-<hash>` suffix of a custom config dir), through
  `keychain_read_argv`, which refuses any other name. The OAuth access token goes into
  `CLAUDE_CODE_OAUTH_TOKEN` of the child process only, is redacted from every
  record, from the parsed `Trace`, the saved JSONL, the debug log and error messages. A token about to expire skips
  the scenario instead of being refreshed.
- Codex: a temporary copy of `~/.codex/auth.json` (mode 600) is in the sandbox
  only while a model run lasts, then deleted. Its access, refresh and id tokens are
  redacted from the evidence, the `Trace`, the saved JSONL, the rollout logs under
  `$CODEX_HOME/sessions` (rewritten in place after every run, so `--keep` retains
  them clean) and error messages (a timeout's command line included). A login that lives only in the
  keychain is not read: the scenario skips. A token about to expire skips too,
  so Codex never refreshes inside the sandbox and leaves the real login stale.

## A flaky update until the fix is released

`n1-preview-update` updates from the last release (a tag), whose preview still runs that release's
viewer. Before the fix, the viewer reloaded itself when the paper's rebuild changed the build
version, and a reload served while the old server restarted (`os.execv`, 0.5 s after the reply)
left a page with no scripts. The viewer now skips that reload while an update runs
(`updateBusy` in `preview-updates.js`), but the old page of this scenario is still the last
release's, so the scenario fails about 1 run in 30 (more under load) until a tag includes the
fix. The failure message prints the page's state (`loads`, `loadErrors`, `viewerLoaded`).
To check the fix before then, run the scenario with `ctx.last_release("<commit with the fix>")`.

## Safety

`guard.py` runs when a sandbox is made and before every spawn. It refuses
when `HOME`, `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `TRAZO_DATA_DIR` or `TMPDIR`
is missing from the sandbox or resolves to, contains or lies inside the
account's real home (read from the account database, not `$HOME`),
`~/Library/Application Support/trazo`, `~/.claude*`, `~/.claude.json`,
`~/.codex` or Claude Code's `~/Library/Caches/claude-cli-nodejs`, and when any
environment value mentions one of those paths. Sandboxes live under the system
temporary folder and are removed unless `--keep`.

What the guard cannot see: a scenario that passes the real home as a command
argument (not in the environment), or a program that finds the real home in
another way (the macOS keychain, for instance). The n2 drivers check what the CLIs read outside `CLAUDE_CONFIG_DIR` and
`CODEX_HOME` before a model run (see Credentials).
