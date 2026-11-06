# Workflow baseline and failure audit, 2026-11-06

Product revision: `ee13ca0a8c835749b9575a784171ce7a7007dde4`.
No product, template or renderer changes were made during this investigation.
The [harness and methodology](../scripts/measure/README.md) and
[machine-readable baseline](measurement-baseline.json) are the durable deliverables.
Raw samples, profiles, traces, fault snapshots and logs are in `.build/measure/`.
This is phase 1 evidence. Product corrections and their before/after PR evidence
have not been performed.

Coverage is explicit: 81 dynamically reached Python write/publication boundaries
were injected, not every branch of the static inventory. Native executable writes,
power-loss durability, setup/update mutations, all browser editing modes and
Linux/Windows behavior remain unproven. Do not interpret the passing cases as a
universal guarantee of no data loss.

## Workload and machine

Apple M4, 16 GiB RAM, macOS 27.2 arm64; Python 3.12.13, Pandoc 3.9.0.2,
uv 0.11.3, Chrome 156.0.8078.80. Runs were serial, without the test suites running
alongside the baseline. The OS file cache was not flushed. Cold means application
cold start under the operation-specific rules in the harness README.

Both papers come from `trazo new` with the example enabled. The fixed seed is
20261106. The load paper retains the real example's analysis scripts and adds
coherent synthetic prose, distinct raster plots and table specs. Its size models
a long methods paper with extensive validation. Table variants share the small
example dataset, so this is not a benchmark of large-data analysis or complex
diagram layout. Paths with spaces and accents were used throughout the load run.

| Paper | Main-text words, `trazo counts` | Raw Markdown words | Figures | Tables | Cited references |
| --- | ---: | ---: | ---: | ---: | ---: |
| Example | 308 | 691 | 2 | 1 | 1 |
| Load | 10,636 | 11,121 | 12 | 10 | 81 |

Both have a 66-word abstract, 25 supplementary words and one declarative diagram.
The load adds ten distinct 1000 x 650 PNGs. Evidence:
`.build/measure/counts-{example,load}.txt` and each paper's `benchmark-fixture.json`.
The earlier load experiment that reused one image is excluded from this baseline.

## Baseline and proposed budgets

Seconds, **p50 / p95**, 20 observations in every cell, 560 total. p95 uses the
nearest rank; with 20 observations it is an empirical tail estimate, not a service
level guarantee. Timings below are uninstrumented. Profiles were collected separately.

| Operation | Example cold | Example warm | Load cold | Load warm | Proposed p95 budget |
| --- | ---: | ---: | ---: | ---: | --- |
| MCP preview start to rendered manuscript | 1.455 / 1.478 | 0.418 / 0.667 | 1.519 / 1.542 | 0.434 / 0.698 | Cold 2 s; warm 1 s |
| Source save to visible new paragraph | 1.065 / 1.099 | 1.081 / 1.113 | 2.332 / 2.366 | 1.147 / 1.203 | Cold 1.5 s; warm 1 s |
| Open table editor | 0.067 / 0.069 | 0.063 / 0.064 | 0.150 / 0.184 | 0.096 / 0.113 | 0.25 s |
| Open diagram editor | 0.067 / 0.070 | 0.064 / 0.080 | 0.102 / 0.118 | 0.077 / 0.095 | 0.25 s |
| Full Word build, both documents | 0.505 / 0.528 | 0.393 / 0.401 | 0.792 / 0.840 | 0.615 / 0.627 | Example 1 s; load 2 s |
| Word build without changes | 0.119 / 0.121 | 0.119 / 0.121 | 0.132 / 0.136 | 0.135 / 0.137 | 0.25 s |
| MCP submission check | 0.472 / 0.485 | 0.471 / 0.478 | 1.051 / 1.066 | 1.048 / 1.062 | Example 0.75 s; load 1.5 s |

The budgets allow responsive editors, a subsecond normal save and a bounded extra
wait when saving immediately after opening. **25 of 28 cells meet them.** The
exceptions are warm saves in both papers and cold saves in the load paper.
Word, unchanged builds, editor opening and MCP checks already meet these budgets;
their profiles do not justify adding a cache now.

Raw baseline: `.build/measure/baseline-example/results.json` and
`.build/measure/final-load/results.json`. One browser milestone requires real
document replacement, content, ready fonts and two animation frames. It cannot
be satisfied by the old DOM; it is not proof of every offscreen image decoding.

## Five dominant time consumers

These are diagnostic observations, not an additive partition. cProfile overhead
can move a render past the next polling tick. Parent/child waits, concurrent HTTP
workers and nested Chrome events overlap. Full evidence is in
`.build/measure/profile-load/profile-summary.json`, its `profiles/` and `traces/`.

| Consumer | Measured evidence | Implication |
| --- | --- | --- |
| File watching and browser status polling, including an initial build still in flight | Watcher interval is 0.5 s (`preview_watch.py:121`); visible-tab status interval is 1 s (`assets/app/app.js:102`). In the instrumented cold save, the affected new render starts 1.076 s after saving. In the warm sample it finishes at 0.961 s, while the new paragraph is visible at 2.340 s. The trace includes the intervening navigation and paint. | Explains the quantized save delay. This is the performance correction to investigate first. The residual also includes reload/network/render work; it is not all idle time. |
| Pandoc subprocesses | Load full build: 9 launches, 0.529 s of leaf subprocess wait in a 1.129 s instrumented operation. MCP check: 10 launches, 0.796 s in 1.484 s. | Largest external work. Count metadata, label-index reads and both conversion stages before proposing changes. |
| Python cross-reference traversal | `crossref.apply`: 0.305 s cumulative over three calls during the load check. Full Word build: 0.103 s over two calls. `equations`, `blocks_in` and reference walking dominate the package's non-idle self time. | A measured CPU cost, but both operations meet budget. No speculative refactor or caching is warranted. |
| Browser parsing, JavaScript and layout | Cold load preview trace: ParseHTML 61 ms, EvaluateScript 61 ms and Layout 51 ms. Cold save trace: Layout 140 ms, EvaluateScript 76 ms. These inclusive trace totals overlap. | Browser work is visible but much smaller than the save delay. Editor traces are smaller still. |
| Project script processes and environment check | Full cold load build: three project scripts take 0.129 s combined; `uv sync --locked` takes 0.011 s. | Preserving the example scripts matters. uv is not the bottleneck on this installed environment. |

Filesystem API spans total 17 ms for the instrumented full Word build and 15 ms
for the MCP check. They include page-cache hits and omit native Pandoc I/O; they
are not physical disk latency measurements. Concurrent preview requests make
their summed spans overlap. cProfile files preserve the Python breakdown;
Chrome traces preserve JS/layout/paint detail instead of attributing all time to
one Python function.

## Robustness inventory and reproduced failures

Run `inventory.py .build/measure/audit` to regenerate the inventory. It records
117 disk-mutation call sites (including directory/cleanup operations), 34
subprocess-related sites, 46 concurrency operations, 55 shared-module and 25
shared-instance state candidates, two bind/server sites and seven browser-storage
sites. Constants and shared-state candidates still need caller-level interpretation;
the inventory is not a claim that every candidate is a race.

Each row includes source location, surrounding error handling, local timeout and
atomicity. Atomic single-file writes do not make the source/history pair a
transaction. Build locking is unbounded by default; preview preparation explicitly
sets a 20-second lock wait. MCP tool subprocess communication has no deadline.
Preview Word/export jobs instead have an owning timer, and Chrome has transport
and shutdown deadlines. HTTP/MCP error boundaries usually expose exceptions.

All **19 broad handlers** were reviewed in `audit/inventory.md`. Four need attention:
`preview_watch.py:20` and `:187` only print failures; `preview_edit_review.py:247`
omits broken tables from drift checks; `diagram_editor.py:336` suppresses cleanup
failures. Two others deliberately degrade optional matching (`preview_locate.py:30`,
`preview_sources.py:182`). The remaining 13 re-raise after cleanup or produce an
HTTP, MCP, job or preview error. The silent stale-table scenario confirms the first
watcher problem; the other three are audit findings, not newly reproduced failures.

The final write campaign is `.build/measure/faults-final/report.md` and `.json`:
**189 injections at 81 reached boundaries, 22 invariant failures**. All triggers
were hit. Every next operation succeeded after removing the injected fault, which
does not restore versions already lost. Counts by operation: source 30/0 failures,
table 35/9, diagram 21/9, Word 46/0, preview 22/4, diagram export 35/0.
SIGKILL delivery boundaries have an independent harness regression test.

| Failure | Evidence / reproduction command suffix | User impact |
| --- | --- | --- |
| Table and diagram files change before their old text is safely recorded | `faults.py SEED NEW_OUT --operation table --fault full`, or `--operation diagram --fault full`; e.g. `table-005-full`, `diagram-005-full` | ENOSPC/EACCES during history persistence leaves the new file but no recoverable previous version. SIGKILL after replacement has the same effect. |
| Independent writer overwritten by browser save | `scenarios.py SEED NEW_OUT --scenario concurrent_writers` | A real HTTP save is paused before replacement, a separate process saves new text, then the HTTP request returns 200 after overwriting it. Neither file nor history retains that text. |
| Hung Pandoc has no MCP product deadline | `scenarios.py SEED NEW_OUT --scenario pandoc_hang` | The experiment cancels at 3 s; this is a harness deadline. `mcp_calls.py:93`, `frontmatter.py:25` and the build call chain confirm missing product bounds. MCP ping, cancellation and the next valid build work. |
| Corrupt table data leave a successful preview status | `scenarios.py SEED NEW_OUT --scenario corrupt_table` | Old generated table stays visible, status says OK; the table endpoint reports the error. Restoring valid data lets the watcher recover. |
| Corrupt source undo history blocks valid saves | `scenarios.py SEED NEW_OUT --scenario corrupt_history` | HTTP 500 is visible and source text survives, but the retry still fails until the store is repaired. |
| Preview intermediate files can remain truncated | `faults.py SEED NEW_OUT --operation preview --fault kill` | `.build/preview/manuscript.md` or `after_body.html` can be partial. The published HTML remains intact and the next render recovers. These are scratch-file failures, not observed loss of manuscript text. |

Prefix every command above with `uv run --quiet --project packages/trazo python
scripts/measure/`. The seed used was `.build/measure/papers/example`; choose a new
output directory. `--site 'EXACT SITE FROM report.json'` isolates one boundary.

Passing environmental controls: Pandoc absent, invalid Pandoc DOCX output, port
occupied, source edited during compilation (the next build includes it), an actual
read-only directory, and 100 fixed-seed malformed HTTP inputs. The final fuzz run
includes valid outer envelopes with corrupt source/table/diagram contents, checks
that user files stay byte-identical and that the server keeps answering.
Results are in `scenarios/report.json` and `fuzz-final/report.json`. ENOSPC is
injected at file APIs; the owner's physical volume was not filled.

## Correction order, impact and risk

One root cause per PR, based on `t3/measure-user-workflows`, as requested.
The proposed invariants and their limits are in the harness README.

| Order | Correction | Tier and reason | Required proof before landing |
| --- | --- | --- | --- |
| 1 | Persist recoverable history before replacing edited table/diagram text, including sibling CLI/history paths | Medium: `table.py`, `diagram.py`, `review_history.py`; diagram editor itself is low. No public signature change is needed. | A failing real-boundary regression first; rerun the history-write and SIGKILL cases, normal save/undo, and strict mypy for `review_history`. |
| 2 | Establish a supported protocol for concurrent browser and agent writes | High: raises the tier because it changes the concurrency contract. `preview_sources.py` alone is medium. | Preserve both independent versions or return conflict in deterministic interleavings. An extra hash check alone leaves a smaller race and does not prove the invariant. Arbitrary noncooperating writes require an explicit contract decision. |
| 3 | Bound build/MCP subprocess lifetimes and kill descendants on timeout | High: MCP, Word build, metadata and uv execution paths are high tier. | Failing hang/cancellation regressions, retained last-good DOCX and successful next call, including lock release. |
| 4 | Expose failed table regeneration/watcher failures in preview status | Medium: `preview_watch.py` and associated state. | Corrupt-data regression first; last good page retained, error visible, recovery when input becomes valid. |
| 5 | Reduce save-to-visible polling delay without a new content cache | Low if confined to `assets/app/app.js`; medium if watcher code must change. | Same-machine 20-sample before/after, improvement exceeding noise, no regression in idle request load or other operations. |
| 6 | Recover from corrupt history while retaining the damaged store for diagnosis | High by judgment: automatic repair affects authoritative user history. | Failing corrupt-store regression, preserved bytes and bounded recovery without claiming recovered information that no longer exists. |
| 7 | Make retained preview scratch files atomic or explicitly disposable | Medium: `preview_render.py`. Low user impact because published HTML already survives. | The four scratch failures and the old-page preservation invariant. |

Do not optimize Word or introduce a cache just because there is measurable work.
Those budgets already pass, and a cache would add invalidation risk.

## Verification actually run

- `make lint`: passed.
- `make test-package`: passed, 911 tests in 419.934 s, one skip:
  `test_plugin_launcher.LauncherTests.test_stand_in_reports_a_pandoc_on_the_windows_path`
  requires Windows PATH behavior.
- `make test-template`: passed, including generated variant checks and all template builds.
- `uv run --quiet --project packages/trazo ruff check --config packages/trazo/pyproject.toml scripts/measure`: passed.
- `uv run --quiet --project packages/trazo python -m unittest discover -s scripts/measure/tests`:
  passed, two independent evidence-oracle tests.
- `make acceptance-auto AUTO=push`: **18 passed, 0 failed, 0 skipped, 0 not automated**,
  all fresh, none cached. Report:
  `.build/acceptance/20261106T191709Z/report.md`. Selection covers the existing branch
  changes since develop as well as this work; those earlier changes were not edited.
- Forced rebuild and `equivalence.py --compare` for both papers: normalized DOCX
  members, all exports and renderer matched. Both real `diagram check --all --exports
  --json` runs passed. This establishes baseline repeatability, not a product-fix
  before/after claim. Artifacts: `word-before-{example,load}/` and
  `word-repeat-{example,load}/` under `.build/measure/`.

Not run: `make test-update` (no template/new/update/variants edits), `make typecheck`
(no package module edits), `make test-acceptance` (acceptance harness unchanged),
`make test-linux`, Windows execution and manual desktop-app checks. No PR has been
published and no performance improvement is claimed yet. The failures above,
three unmet budget cells and the explicitly uncovered branches remain open.
