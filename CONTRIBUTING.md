# Contributing to trazo

How the repository is laid out, how to test a change and how to make a
release. Using trazo is explained in the [README](README.md).

## Layout

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
`scripts/trazo`. Contributor rules, for people and agents, are in [AGENTS.md](AGENTS.md);
coding standards in [CODING_STANDARDS.md](CODING_STANDARDS.md) and the
review guide in [REVIEW.md](REVIEW.md).

## Set up and test

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

## Acceptance harness

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

## Branches

Work happens on `develop`: branch from it and open pull requests against
it. `main` moves only at a release, because both apps install the plugin
from it.

## Quality gate

GitHub Actions runs `make lint` and the three test targets on Linux and
macOS for every pull request and every push to `develop`, `main` or a tag.
With `make hooks`, `git push` runs
`test-package`, `test-template` and `test-update` on a clean checkout of
the tip of each pushed branch or tag (a few minutes), then the acceptance
scenarios for what the branch changed since `develop`, and cancels the push
if one fails. Pushing a `vX.Y.Z` tag also needs n1 and n2 to pass for what
changed since the previous release, skips included. `git push --no-verify`
skips it.

## Try a change in a real paper

To try unreleased changes inside a real project, point its launcher at
your checkout:

```bash
export TRAZO_DEV=../trazo/packages/trazo
scripts/trazo doctor
```

To install the plugin from a local clone instead of GitHub:

```bash
claude plugin marketplace add /path/to/trazo
claude plugin install trazo@trazo
codex plugin marketplace add /path/to/trazo
codex plugin add trazo@trazo
```

To measure trazo on a thesis-sized paper (the stress paper), time its
build, preview start and checks:

```bash
uv run --project packages/trazo python packages/trazo/tests/stress_fixture.py bench /tmp/stress
```

## Release

1. Bump the version in `packages/trazo/pyproject.toml`,
   `packages/trazo/src/trazo/__init__.py`, `.claude-plugin/plugin.json`,
   `.codex-plugin/plugin.json` and `trazo_version` in `copier.yml`.
2. Run `make template-agents` if skills changed and `make
   template-diagrams` if the renderer changed, then `make
   template-variants` (trazo's copy of the rendered template; `make
   test-template` fails while it is stale).
3. In `CHANGELOG.md`, rename `## Unreleased` to `## vX.Y.Z (YYYY-MM-DD)`
   and check it reads well for users (the comment at its top gives the
   shape). Run `make test-linux` and commit on `develop` as
   `chore(release): trazo and template vX.Y.Z`. Do not push yet.
4. The owner runs the manual desktop-app checks on that commit. Agents
   do not run them.
5. Push to `develop`. The pre-push hook tests the commit and runs the n1
   and n2 acceptance scenarios for it (`make acceptance-auto AUTO=push`).
6. Tag `vX.Y.Z` and push the tag. The hook runs n1 and n2 for what changed
   since the previous release; a skip blocks it (`AUTO=tag`).
7. Publish the GitHub release with the changelog section as its notes:
   `python3 scripts/release-notes.py vX.Y.Z > .build/notes.md`, then
   `gh release create vX.Y.Z --title "trazo vX.Y.Z" --notes-file .build/notes.md`.
   Releases are immutable once published.
8. Move `main` to the release; from here users get it:
   `git push origin "vX.Y.Z^{commit}:main"`. This is a fast-forward, the
   only push `main` accepts, and only from the owner.

A fix that cannot wait for what is on `develop`: branch from `main`, make
the fix and the release commit there, push that branch in step 5 instead of
`develop`, finish steps 6 to 8, then merge `main` into `develop` so the
next release still moves `main` forward.
