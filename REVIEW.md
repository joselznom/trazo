# Review guide

<!-- source: https://claude.dev/blog/solving-code-review-at-anthropic/ and https://github.com/mattpocock/skills/tree/main/skills/engineering/code-review -->
<!-- What matters in this repo when reviewing a change. Written by someone who knows the code. -->

## Two axes

Review the diff on two separate axes and report them under separate headings, without merging or reranking findings across them:

- **Spec**: does the change do what was asked? Report missing or partial requirements, behaviour nobody asked for (scope creep), and requirements that look implemented but wrong. Quote the request or spec for each finding.
- **Standards**: does the change follow `CODING_STANDARDS.md` and this repo's conventions?

Code can pass one axis and fail the other.

## What a bug looks like here

<!-- Seed it from the repo's `fix:` commits: what actually broke, not what could break. -->

- **A stale Word file.** The build's freshness check missed an input, so a change did not rebuild the DOCX: a deleted section or declaration, `trazo.toml`, an image whose caption (the alt text) contains brackets, the Pandoc or pandoc-crossref version (d6a311b, f66983e).
- **A failed step that looks done.** A run that failed left its outputs or its record behind, and the next build trusted them (f66983e). Counts and pages from a failed build replaced the last good ones (d48799a).
- **Wrong content in Word without an error.** Form-only citations counted in the reference numbering, a definition list half parsed, includes expanded wrongly (d6a311b, d48799a, 0a7c23e). Nothing fails; the document is just wrong.
- **An update that changes a user's paper.** Demo content added to existing projects on update (d48799a); a new Word style that projects with their own `reference_universal.docx` lack (CHANGELOG v0.3.1).
- **Works on macOS, breaks on Linux.** Timestamps too coarse for the watcher, Chrome refusing to start as root (CHANGELOG v0.1.2), headless Chrome left running (9d8b5d1).
- **Two writers, one file.** `steps.json` overwritten by a concurrent run (f66983e); review comments and history are written under a lock for the same reason.
- **The preview server and its clients.** A tab closed mid-reply printed tracebacks (a2fd5c1); reset connections under load (1f0023d).

## Always check

- Every new input of a Word build reaches its digest in `build_graph.py`, and a failed step removes what it wrote.
- A path or command a project may change is in `trazo.project.DEFAULTS`.
- A change under `template/`: `make template-variants` was rerun, both `example` answers and both kinds (article, report) still build, and CHANGELOG has an upgrade note when an existing project must do something.
- State that runs or the preview share (build records, review comments and history) is written with `write_atomic`, and `steps.json` is re-read before one key changes; a new shared state file follows the same pattern. Ordinary output files need neither.
- `assets/diagram/renderer.js` is unchanged unless the change needs it (its hash stamps every export).
- Skills and `template/AGENTS.md` are copied into every paper and read by Claude and Codex: an instruction must be true in a generated project, not only in this repo.
- Behaviour that depends on the OS (file watching, Chrome, paths) was run on Linux too (`make test-linux`) or the PR says it was not.

## Paths that don't need review

- Generated files (`packages/trazo/src/trazo/assets/template/`, `template/.agents/skills/`, `template/.claude/skills/`, `template/figures/external/`, `template/templates/reference_universal.docx`): check that the make target that writes them was run; `make test-template` fails when they are stale.

## Rules

- Every finding cites file and line, backed by evidence.
- Tag each finding `Important`, `Nit` or `Pre-existing`. A pre-existing bug is reported, not blamed on this change.
- Keep nits few. Style belongs to the linter.
- Flag the class of bug once, so the author fixes every instance; never repeat a finding already posted.
- Three rounds at most. After the third, review only the new diff and ask the author to reconsider the approach instead of patching.
- The summary opens with the number of findings per axis and says plainly when there is nothing to fix.
