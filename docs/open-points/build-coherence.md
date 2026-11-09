# Word build coherence during source edits

Evidence: `.build/open-points/build-storage/` in the `t3-investigate-open-workflows` worktree (local, git-ignored). Prototype worktrees (`wt/`, `wt-*`) were not retained; their changes are the `*.diff` files there, all against b775566 and never integrated. Back to the [decision summary](../open-points.md).

Head b775566, macOS arm64 (APFS), Pandoc 3.9.0.2. `S` below is
`.build/open-points/build-storage`.
Other agents shared the machine: every timing is indicative, never a budget.
Prototype worktree: `S/wt` (detached at b775566), full diff `S/prototype.diff`.
Paths in the product are relative to `packages/trazo/src/trazo/`.

## A. Word build coherence when sources change during a build

### When each input is read, hashed and recorded (code reading, confirmed by `S/window-times-load.txt`)

Order inside one document's Word build (`Builder.docx` -> `pandoc_docx`):

| t (load fixture, ms) | Event | Reads |
| --- | --- | --- |
| 0 to 36 | `numbering.metadata_file` -> `labels.index` | every document's sources (a full `expand_files` per document), writes `<build>/<doc>-numbering.json` |
| 38.2 | `Builder.expand` digest (hash H_e) | sources + includes, values, `_shared.json`, metadata, trazo.toml |
| 38.5 to 44.9 | `expand_files` | the same files again, one by one; `frontmatter.render` reads metadata; values are loaded lazily |
| 45.4 | record `expand:<doc>` = H_e | |
| 73.2 | docx digest (hash H_d) | expanded md, numbering, images, step outputs, diagram PNGs, metadata, bibliography, CSL, reference doc |
| 74.1 to 159.7 | Pandoc stage 1 | expanded md, `--metadata-file` (metadata.yaml, numbering) |
| 188.3 to 312.4 | Pandoc stage 2 | bibliography and CSL (citeproc), images, reference doc |
| 344.1 | `write_atomic(output)`, then record `docx:<doc>` = H_d | |

Steps, the values and tables generators (`Builder.script`) and the diagram
export (`Builder.diagrams`) are different: they hash again **after** the run
and record that post-run hash (`build_graph.py:392`, `:497`).
`tables-render` records its pre-run hash (`:456`, `:470`).

### A1. Step and diagram records are hashed after the run: a change made during the run is never rebuilt

- **Status:** still open, fixable defect (new finding; contradicts "the next build detects the change").
- **Reproduction:** `uv run --quiet --project packages/trazo python S/tools/race_cases.py S/papers/example S/race`
  (case `c6`): a declared step (`S/tools/slow_step.py`) reads `data/slow_input.txt`
  (STEPV1), pauses on a file barrier, the input is changed to STEPV2, the step
  writes "Step output: STEPV1". Result in `S/race/results.json`: first build exit 0,
  then two further `trazo build` runs print `[build] up to date`; the DOCX keeps
  STEPV1 while the input is STEPV2, indefinitely (until another edit).
- **Root cause, confidence high:** `script()` records `self.digest(run, hashed(), extra)`
  computed after `run_python`; `diagrams()` evaluates `digest()` after `cmd_export`.
  The record describes inputs newer than the outputs. The post-run hash exists for
  inputs a script records itself (values), but it is applied to every input.
  The diagram editor saves under `review_lock` only, not the build lock, so a
  browser diagram save during an export hits this path (code reading).
- **Recommended solution:** take a per-file snapshot (content hash plus mtime, size,
  inode) of the inputs known before the run; after the run compare the snapshot on
  the files present in both sets, excluding the run's own outputs; on a difference
  raise `BuildError("<step>: <files> changed while it ran; build again ...")` and write
  no record (the next build reruns it). Same in `diagrams()` (exclude
  `diagram_output`) and `render_tables()`. Prototyped in `S/wt` (`Builder.snapshot`,
  `Builder.unchanged`).
- **Rejected alternatives:** record the pre-run digest without checking (fixes
  detection, but this build still publishes stale step output, see A3); rerun the
  step automatically (a figure script may take minutes; the user reruns).
- **Checkable invariant:** a step record never names an input content that its
  outputs were not computed from (for inputs known before the run).
- **Portability and risks:** pure Python stat/hash; `st_ino` is the file index on
  Windows. A step whose folder input contains its own output is excluded by the
  `written` set; a step that edits its own declared input on purpose now fails
  (that step was already never fresh-correct).
- **Test that fails before the fix:** `S/wt/packages/trazo/tests/test_build_coherence.py`
  `StepInputs.test_an_input_edited_while_its_step_runs_is_built_again`. On b775566:
  `AssertionError: '1' != '2'` (`S/baseline-tests/b775566-run.txt`).
- **Verification needed:** `make lint`, `make test-package`, `make test-template`,
  acceptance n1/n2 for the build area; values scripts that record new inputs on the
  first run (only intersecting files are compared, so no false failure).
- **Measured cost:** one extra hash of the step inputs, same order as the existing digest.
- **Proposed PR:** "fix: never record step inputs newer than the run", **High**
  (`build_graph.py` is the Word build).
- **Not verified:** the diagram-export instance was not run (needs Chrome); it is the
  same code pattern. Values scripts' own recorded hashes were not audited.

### A2. A published DOCX can mix versions, and its stamp can describe a state it does not contain

- **Status:** still open, fixable. Reproduced on b775566.
- **Reproduction:** same command, cases in `S/race/results.json`. A Pandoc shim on
  `[commands] pandoc` (`S/tools/pandoc_pause.py`, a file barrier before stage 1 or 2;
  `machine.resolve_pandoc` runs a project setting as written) pauses a
  `trazo build --force` (forced only so the paused build has work; any build with work
  behaves the same). Edits are atomic renames, as an editor or the browser save does.

  | Case | Edit during the pause | First DOCX | Next build, edit kept | Next build, edit undone (v1 restored) |
  | --- | --- | --- | --- | --- |
  | c1 control | section text, stage 1 | v1 (expanded earlier) | rebuilt, v2 | n/a |
  | c2/c2r | bibliography title, stage 1 | text v1 + bibliography **v2** | rebuilt | **manuscript not rebuilt**, DOCX still shows BIBV2 |
  | c3/c3r | metadata.yaml title, stage 1 | Pandoc metadata **v2**, stamp v1 | rebuilt | **not rebuilt**, DOCX still METAV2 |
  | c4/c4r | figure PNG, stage 2 | embeds **v2**, stamp v1 | rebuilt | **not rebuilt**, DOCX embeds v2 |
  | c5/c5r | 02_methods and 03_results together, between their reads in expand (window widened in-process by `S/tools/inproc_build.py`) | methods **v1** + results **v2** | rebuilt | **`[build] up to date`**, DOCX has v2 results |

  Natural window widths, load fixture (`S/window-times-load.txt`): docx digest to
  Pandoc stage 2 start 115 ms, digest to record 271 ms per manuscript build; expand
  digest to reads 0.3 to 6.7 ms; numbering read to expand digest about 38 ms. The c5
  window was widened artificially; the others are the real build with a delayed Pandoc.
- **Root cause, confidence high:** inputs are hashed once (H_e, H_d) and read later by
  `expand_files` and by Pandoc; the hash is recorded after publication without
  checking that the files still hold the hashed bytes. Reads of several files are
  sequential, so one build can combine different moments. A writer that ignores the
  build lock (browser source, table spec and diagram saves take `review_lock` only;
  agents; editors) is enough.
- **Answer to question 1:** yes on both counts. A DOCX can mix main text v1 with a
  bibliography, metadata or figure v2, or one section v1 with another v2. The stamp can
  record v1 while the DOCX contains v2; reverting to v1 then leaves the v2 DOCX "fresh".
  If the edit stays, the next build does rebuild (the known statement holds only then).
- **Recommended solution, candidate (b):** verify before publication with a bounded
  retry. In `pandoc_docx`: snapshot (content hash, mtime, size, inode) of the expand
  inputs plus every document's sources (numbering reads them) before `expand`; add the
  Word-stage inputs when known; build into the existing temporary folder; snapshot again
  just before `write_atomic(output)`; on any difference, discard the result, forget
  `expand:<doc>`, log "changed during the build; building again", and retry once
  (`ATTEMPTS = 2`); after that raise `BuildError("... changed during the build 2 times;
  the earlier Word file is kept ...")`. Prototype in `S/wt` (`pandoc_docx`,
  `pandoc_docx_once`, `watched`, `InputsChanged`). With the prototype the same race run
  (`TRAZO_SRC=S/wt/packages/trazo/src ... race_cases.py ... S/race-prototype`) gives:
  every first DOCX is a single v2 state, the next build is `up to date`, every undo
  triggers a rebuild, c6 fails explicitly then catches up (`S/race-prototype/results.json`).
- **Candidates compared:**

  | Candidate | Invariant it guarantees | Does not guarantee | Measured cost (load manuscript, 40 files, 395 kB) |
  | --- | --- | --- | --- |
  | (a) staging copy before Pandoc | each recorded hash equals the bytes read, for the files trazo stages | one cross-file state (copies are sequential); files Pandoc finds by itself (`--resource-path`, data dir, paths named inside metadata) | copy 5.3 ms median (`S/hash-cost-load.json`), plus rewriting every path Pandoc and after-docx steps use |
  | (b) re-hash before publish, max 2 attempts, explicit failure | published DOCX and record describe one on-disk state of all watched inputs that held still from attempt start to publication; the stamp never names a state the DOCX lacks; a changing state is never published (old DOCX kept) | A-B-A with restored mtime and inode; files outside the watched set; a writer's own multi-file edit that straddles the start (needs writer cooperation) | prototype snapshots 5.6 ms median per manuscript attempt (`S/snapshot-cost-load-final.json`), about 2% of the 270 ms Word stage; plain extra hash pass 1.7 ms |
  | (c) record pre-read hashes only | an edit still on disk at the next build is rebuilt ("progress") | no mix prevention, no stamp truth (c2r to c5r are counterexamples); this is today's docx/expand behaviour | zero |

- **Rejected alternatives:** (a) for the reasons in the table and because it still needs
  (b) for cross-file coherence; (c) alone because the revert lie is reproduced; a
  writer-side build lock for browser saves (would block saves for a whole build, and
  agents and editors still ignore it).
- **Checkable invariants:** after `trazo build` exits 0, for each built document, every
  watched input hashes to what `steps.json` records; undoing any edit made during the
  build makes the next build rebuild that document.
- **Portability and risks:** stat and hashing are portable; `st_ino` on Windows needs
  Python 3.12 (required). A `touch` or an identical rewrite counts as a change (one
  retry). Owner decision: strict failure after 2 attempts (recommended for deliverables)
  versus "publish but do not record". With the table editor saving 600 ms after the last
  change (`assets/table/editor.js:15`) and a 0.3 s window per attempt on the load
  fixture, two consecutive collisions are possible on large papers while someone types;
  the preview Word job would then report the error. Command-built documents
  (`[documents] build`) keep mtime freshness (`docx_freshness.py`) and are not covered.
- **Tests that fail before the fix:** `S/wt/packages/trazo/tests/test_build_coherence.py`
  `WordInputs.test_a_word_file_is_published_only_for_inputs_that_held_still` (b775566:
  `'[build] docx: manuscript' not found in '[build] docx: supplementary ...'`) and
  `test_inputs_that_keep_changing_fail_and_keep_the_earlier_word_file` (b775566: exit 0
  instead of 1). They use a Pandoc shim that edits the bibliography, no timing.
- **Verification needed:** full gates (`make lint`, `make test-package`,
  `make test-template`), acceptance n1/n2 (build area), Linux CI. `build_graph.py` is
  already over the file-size limit (36 kB); the prototype grows it by 4.5 kB, which the
  pre-commit hook refuses: the PR must move `snapshot`, `unchanged`, `watched` and the
  existing `expand_includes`, `image_targets`, `recorded_inputs` into a new module so
  `build_graph.py` does not grow.
- **Measured impact:** see table; hashing runs at about 3 GB/s here, so 100 MB of figures
  add about 33 ms per pass.
- **Proposed PRs:** PR1 = A1 + A2 in one "fix: publish a Word file only for inputs that
  held still" (High: Word build), or A1 first if the owner wants the smaller change.
  Update `docs/measurement-boundaries.md` ("Single-build snapshot coherence remains
  unproven") in the same PR.
- **Not verified:** Linux/Windows; real browser saves (the edits were file renames, the
  same system call the save uses); large binary figures; prototype mypy (build_graph is
  not in the strict list; error count unchanged at 17).
