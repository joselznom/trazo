# AGENTS.md

Instructions for AI agents working on the trazo suite.

## Layout

- `packages/trazo/`: the tools, a Python package (stdlib only, Python
  3.12+). Code in `src/trazo/`, browser assets in `src/trazo/assets/`,
  shared agent skills in `src/trazo/skills/`, tests in `tests/` (they
  run against a copy of `tests/fixture/`).
- `template/`: the Copier project scaffold. Files ending in `.jinja` are
  rendered (`project_name`, `title`, `example`, `trazo_version`);
  others are copied as they are. `{#` in a `.jinja` file starts a Jinja
  comment, so wrap Markdown in `{% raw %}`.
- `example: false` starts a project without the example paper: its own
  files are excluded in `copier.yml` (`_exclude`), and shared files that
  mention it have an `{% if example %}` variant. Keep both working;
  `make test-template` builds both.
- `copier.yml`: template questions and settings.

## Rules

- Projects run trazo through `scripts/trazo` (the per-user
  install); never make a project import trazo or add it to its
  `uv.lock`.
- Every path or command a project may change belongs in
  `trazo.project.DEFAULTS` and is documented there.
- Keep `assets/diagram/renderer.js` byte-stable unless a change is needed.
  Its sha256 is part of every diagram export stamp, so a change makes all
  projects' exports stale.
- Generated files in `template/` (`.agents/skills`, `.claude/skills`,
  `figures/external`, `templates/reference_universal.docx`) come from
  `make template-agents`, `make template-diagrams` and
  `make template-reference`; trazo's rendered copy of the template,
  `packages/trazo/src/trazo/assets/template`, from
  `make template-variants` (rerun after any `template/` change). Change
  Word styles in `scripts/build-reference-docx.py`, never in Word.
- The logo files in `brand/` and the preview's favicon and top-bar
  wordmark, `packages/trazo/src/trazo/assets/app/favicon.svg` and
  `assets/preview/preview-wordmark.js`, come from
  `scripts/build-logo.py`; the plugin manifests point at `brand/`.
- Keep documentation short, practical, and in English.

## Done means green

- Every change: `make lint` (it also lints `scripts/acceptance`),
  `make test-package` and `make test-template`. Report the exact commands
  and results before claiming a change works.
- Acceptance harness (`scripts/acceptance/`): it runs by itself, chosen by
  the files a change touches, as the `areas` of
  `scripts/acceptance/catalog.toml` say. Passes are cached by content, so a
  scenario whose files did not change is not run again.
  - **Push:** `pre-push` runs `make acceptance-auto AUTO=push` (n1 and n2
    for the files this branch changed since main) on each pushed tip after
    the tests. With nothing matching it ends at once. A missing data seed
    (fresh clone, changed `setup.sh`) is downloaded by the run itself, once,
    about 100 MB. A failure blocks the push: read the `report.md` it names
    and fix the cause; never push with `--no-verify`. A skip (no login, a
    usage limit) is listed, not blocking; an agent never logs in, so tell
    the owner.
  - Pushing a `vX.Y.Z` tag also needs n1 and n2 passing for what changed
    since the previous release (a skip blocks here). The owner checks the
    desktop apps by hand before a release; agents do not run manual app
    checks.
  - A failed scenario is retried once and a pass then says "on retry";
    `--no-cache` forces a run. n2 uses the owner's real CLI logins.
  - Edits to `scripts/acceptance/`: `make test-acceptance`.
  - Paste the path of the `report.md` that `make acceptance*` prints and its
    summary line. A skipped, cached or "not automated" scenario is not a
    fresh pass: say which ones were.
  - The harness reads the owner's Claude login (one read-only keychain
    item) and a temporary copy of Codex's `auth.json`. Never run `claude`
    or `codex` login, logout or `setup-token`, and never write the keychain.
- A change to a module listed under `[[tool.mypy.overrides]]` in
  `packages/trazo/pyproject.toml`: `make typecheck` reports nothing for
  it. The other modules' errors are known and do not block yet.
- A change to `template/`, `copier.yml` or trazo's `new`, `update` or
  `variants`: also `make test-update`.
- Every commit: the hooks in `.githooks/` run (`make hooks` once per
  clone). `pre-commit` runs `scripts/check-file-size.sh`: files stay under
  ~5,000 tokens, and files already over it may change but not grow; then
  `ruff check` on the staged files of `packages/trazo`.
  `commit-msg` enforces Conventional Commits without agent attribution.
- Every push: `pre-push` runs `test-package`, `test-template` and
  `test-update` on a clean checkout of the tip of each pushed branch or
  tag (not of every commit below it). GitHub Actions runs only by hand,
  so this hook is the quality gate.
- Every change reaches `main` through a pull request, except docs-only
  changes and version bumps. Release steps are in CONTRIBUTING.md.

## Risk

The paths a change touches set its minimum tier; the agent may raise the
tier (a public signature, a contract, CI, release or dependency change is
high), never lower it. The tier decides how the owner reviews the PR: low
reads the evidence, medium reads the diff, high reads the diff and takes
the quiz. `S/` is
`packages/trazo/src/trazo/`. A file named below wins over a folder
that contains it: generated files stay low inside `template/`, and
`renderer.js` stays high inside `S/assets/`.

- High:
  - the Word build: `S/build_graph.py`, `S/builds.py`, `S/pandoc_*.py`,
    `S/uv_sync.py`, `S/crossref.py`, `S/numbering.py`, `S/expand.py`,
    `S/citations.py`, `S/frontmatter.py`, `S/abbreviations.py`,
    `S/docx_indexes.py`;
  - what papers depend on: `S/project.py`, `S/cli.py`,
    `S/assets/diagram/renderer.js`;
  - creating and updating papers: `S/new.py`, `S/update.py`,
    `S/variants.py`, `S/manifest.py`, `copier.yml`, `template/`,
    `scripts/update-test.sh`, `scripts/template-variants.py`,
    `scripts/template_snapshot.py`;
  - the agent: `S/skills/`, `S/agents.py`, `S/mcp*.py`, `plugin/`,
    `.claude-plugin/`, `.codex-plugin/`, `.codex-mcp.json`, `hooks/`;
  - setup: `S/tool_setup.py`, `S/doctor.py`, `S/machine.py`,
    `S/dependencies.py`, `S/browser.py`;
  - release and the quality gate: `Makefile`, `.githooks/`,
    `scripts/check-file-size.sh`, `scripts/ci-*.sh`, `.github/`, `.gitattributes`,
    `packages/trazo/pyproject.toml`, `packages/trazo/uv.lock`,
    `S/__init__.py`.
- Low: the editors and the preview's look (`S/diagram_editor.py`,
  `S/table_editor.py`, `S/assets/` except `renderer.js`), the other
  `scripts/`, docs (`README.md`, `packages/trazo/README.md`, `docs/`,
  `CHANGELOG.md`, and the root `AGENTS.md`, `CLAUDE.md`,
  `CODING_STANDARDS.md` and `REVIEW.md`), and generated files
  (`S/assets/template/`, `template/.agents/skills/`,
  `template/.claude/skills/`, `template/figures/external/`,
  `template/templates/reference_universal.docx`).
- Everything else is medium.

## Planning

- Plan and track work on the Kanvas board; follow
  `.agents/skills/kanvas/SKILL.md`. The board (`kanvas.json`) and the
  skill sit in the main checkout and are git-ignored, so all worktrees
  share one live board. From a worktree in `.claude/worktrees/`, read the
  skill at `../../../.agents/skills/kanvas/SKILL.md` and run `kanvas`
  (the per-user install) where it says `kanvas.py`.

## Where to look

- Coding standards: `CODING_STANDARDS.md`. Read it before writing or
  reviewing code.
- Review guide: `REVIEW.md`.
- Acceptance harness: `scripts/acceptance/README.md` (levels, scenario
  catalog, credentials). Technical plugin checks: `docs/plugin.md`.
  Agents run the scenarios; the owner runs manual app checks before a release.
- Changes and upgrade notes per release: `CHANGELOG.md`.
