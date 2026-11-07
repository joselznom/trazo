# Measured user workflows: final evidence, 2026-11-07

The empirical budget result is **27/28**, compared with **25/28** at the
baseline. The storage campaign improves from 22 violations in 189 injections to
zero in 197 injections. Both independent-writer races remain failures. The broad
no-loss, no-hang and no-silent-error contracts are therefore not universally met.

Six root-cause product changes are separate PRs against `t3/measure-user-workflows`.
No PR was merged. The combined checkout `6ee0bae` is a local verification assembly,
not an additional product PR. Its `packages/trazo/src/trazo` Git tree is
`a15861a0d4b7326eb36dd5308fbe6017976d9d24`.

## Measurements and budgets

The [phase 1 baseline](measurement-baseline.md) retains the seven operation
boundaries, justified fixtures, top five costs, original inventory, reproduced
failures and prioritization. The [harness](../scripts/measure/README.md) defines
cold/warm and failure invariants. No product code changed in phase 1.

Both papers are generated from the template's example with seed 20261106. The
example has 308 main-text words (691 raw), two figures, one table and one citation.
The load has 10,636 main-text words (11,121 raw), 12 figures, ten tables and 81
citations, including ten distinct PNGs and one real declarative diagram. Table
variants share the small example dataset: large-data analysis and complex diagram
layout are not covered. The load path includes spaces and an accented character.

Machine: Apple M4, 16 GiB, macOS 27.2 arm64; Python 3.12.13, Pandoc 3.9.0.2,
uv 0.11.3, Chrome 156.0.8078.80. Twenty uninstrumented observations per operation,
paper and regime; 560 baseline and 560 combined. OS caches were not flushed.
Each correction also has its own 560-sample run. Final archive: **4,480 selected
observations**, not counting diagnostics, exploratory runs or the extra control.
All samples remain available; no outliers were discarded.

Seconds, p50 / p95 on committed combined product `6ee0bae`:

| Operation | Example cold | Example warm | Load cold | Load warm | p95 budget |
| --- | ---: | ---: | ---: | ---: | --- |
| preview_start | 1.455 / 1.473 | 0.509 / 0.666 | 1.522 / 1.546 | 0.432 / 0.699 | cold 2 s; warm 1 s |
| save_visible | 1.117 / 1.134 | 0.577 / 0.858 | 1.849 / 1.877 **miss** | 0.877 / 0.929 | cold 1.5 s; warm 1 s |
| table_open | 0.067 / 0.069 | 0.063 / 0.065 | 0.150 / 0.182 | 0.095 / 0.111 | cold 0.25 s; warm 0.25 s |
| diagram_open | 0.067 / 0.085 | 0.064 / 0.077 | 0.101 / 0.119 | 0.063 / 0.080 | cold 0.25 s; warm 0.25 s |
| word_full | 0.508 / 0.522 | 0.401 / 0.410 | 0.781 / 0.799 | 0.604 / 0.615 | example 1 s; load 2 s |
| word_unchanged | 0.120 / 0.122 | 0.117 / 0.123 | 0.131 / 0.135 | 0.133 / 0.135 | cold 0.25 s; warm 0.25 s |
| mcp_check | 0.469 / 0.478 | 0.470 / 0.477 | 1.039 / 1.077 | 1.034 / 1.079 | example 0.75 s; load 1.5 s |

Remaining budget misses:

- `save_visible`, load cold: p95 1.877 s against 1.5 s.

The remaining cold-save delay includes initial preview work still in flight,
watcher detection, Pandoc rendering and browser replacement. Faster status polling
cannot remove all those phases. Separate diagnostic profiles preserve those costs;
Word, unchanged builds, editor opening and the typical MCP check already meet
their budgets, so no speculative cache or unrelated optimization was added.

The isolated polling correction passes both improvement gates for warm example
saves and cold load saves. Warm load saves pass the median gate; the isolated
p95 interval crosses zero (-0.015 to 0.304 s), although the combined run's interval
is positive (0.249 to 0.304 s). Both observations are retained. The warm-load p95
improvement is therefore not consistent across the two runs and is not promoted
to a robust isolated-code claim. Cold example saves do not improve.

The standalone preview now makes about four idle requests per second, including
approximately one full workspace request. That **does not meet the proposed phase 1
no-increase-in-idle-requests criterion**. The explicit tradeoff is faster visible
changes in exchange for cheap status requests. MCP-hosted views retain the old
cadence. Warm idle measurements, 20 two-second windows per side:

| Warm idle metric | Before p50 / p95 | After p50 / p95 |
| --- | ---: | ---: |
| workspace_requests_per_second | 0.998 / 0.999 | 0.999 / 1.000 |
| status_requests_per_second | 0.000 / 0.000 | 2.996 / 2.999 |
| server_cpu_percent_one_core | 3.246 / 4.991 | 3.245 / 5.491 |
| renderer_task_percent_one_core | 0.137 / 0.429 | 0.580 / 1.402 |

The CPU percentages describe one core. Server CPU excludes descendants; browser
TaskDuration describes the page main thread. They do not measure system energy.

## Corrections and regression proof

| Change | PR / branch | Measured product | Tier | Failure-first evidence and affected proof |
| --- | --- | --- | --- | --- |
| Recoverable table and diagram saves | [#33](https://github.com/joselznom/trazo/pull/33) | `6e92b9e` | High by concurrency judgment | Seven failing HTTP subcases before; grouped autosave and pending-read controls; 56 injections pass. |
| Finite agent-command deadline and pipe cleanup | [#34](https://github.com/joselznom/trazo/pull/34) | `6edd945` | High, MCP lifecycle | Hung-command and captured-output regressions fail before; 13 lifecycle tests pass; detached pipe cannot hold reply indefinitely. |
| Visible table-generation failures | [#35](https://github.com/joselznom/trazo/pull/35) | `e0dab4a` | Medium, watcher | Six-test negative control has one failure; 137 focused tests pass; corrupt table reports failure and recovers. |
| Damaged source-history recovery | [#36](https://github.com/joselznom/trazo/pull/36) | `09e4f45` | High by authoritative-history judgment | 25 initial failing subcases, five follow-up failures, three final tests pass; backup bytes and source preserved. |
| Complete preview intermediate files | [#37](https://github.com/joselznom/trazo/pull/37) | `12dd3a6` | Medium, render path | Real file-size limit fails both intermediate writes before; 30 injections pass after. Published HTML already survived before. |
| Measured visible-preview polling | `t3/perf-preview-status` | `b6005a5` | Medium by shared-polling judgment | Real Chrome reserved-name regression fails in two views before the correction; final committed benchmark and idle measurements recorded. |

The priority remains user impact versus risk. General independent-writer
protection was not silently replaced with a weaker promise: the
[design disposition](measurement-writer-limits.md) records why an incomplete
hard-link or platform-specific exchange protocol was deferred. Scratch-file
atomicity is lower impact because the visible page already survived failures.

## Output identity and bounded fault evidence

Every correction and the combined checkout force-build both papers from restored
inputs. All normalized DOCX ZIP members are equal to baseline, including document
XML, styles, relationships and embedded images. Only ZIP container metadata and
core created/modified timestamps are normalized. All export hashes match and the
real diagram export checks pass. There are no content differences to explain.
Renderer SHA256 stays
`41dd04c3e8a95b79c8a404fb97a51f0b5a25e902f0b100adde44ee06023e0599`.
No package dependency, project import of trazo or new content cache was added.

The combined campaign at `4e2c3b0` ran **197 injections at 85 reached Python
write/publication boundaries**, zero affected violations, all triggers hit and
all next normal operations recovered after removal of the fault. The later
`6ee0bae` change only narrows an optional PIPE object before close. The campaign
was not repeated after that typing correction; final package tests and lifecycle
regressions cover it. Per operation: source 30, table 35, diagram 21, Word 46,
preview 30 and export 35. SIGKILL, ENOSPC and EACCES are distinct cases.

The environmental run exits **1** because `concurrent_writers` and
`concurrent_atomic_writers` still lose the external text after a successful HTTP
save. Their `limitation` labels do not remove `violations` or turn them green.
Pandoc absent/invalid/hung, occupied port, read-only folder, corrupt source history,
corrupt table data and 100 seeded malformed HTTP payloads pass the checked
invariants. A source edit during build is detected by the next build; coherence
of the first build remains unproven.

The [boundary audit](measurement-boundaries.md) gives the disposition of every
one of the 19 broad handlers. Three silent-error findings remain: the outer
watcher, table drift checks and Chrome cleanup. Standalone CLI/watcher subprocesses
and some shutdown/lock waits remain unbounded. Native executable writes, unvisited
branches, setup/update fault injection, every browser editing mode, power loss,
Linux and Windows execution are not covered. A SIGKILL may leave unused temporary
files. Damaged-history quarantine preserves bytes, not the lost meaning of old
undo entries. History's existing retention limit remains.

## Actual validation and evidence

The [validation record](measurement-final-evidence.md) lists exact commands,
outputs, report paths and skipped steps. Final combined `make lint`,
`make test-package` (924 tests, one Windows-only skip) and `make test-template`
pass. `make typecheck` exits 2 with the original 148 errors in 37 files; the two
new optional-pipe errors found in the intermediate run were corrected. No error
remains in the changed strict `review_history` module.

Final acceptance: `.build/acceptance/20261107T000019Z/report.md`
in the combined checkout, **12 passed, 0 failed, 6 skipped, 0 not automated**. The owner's Claude login expired
or was about to expire during final validation. Earlier fresh passes are retained,
but do not replace skipped final-tip checks. No agent renewed that login.

[Raw observations, deltas, fixture hashes, artifact manifests and inventory](measurements/2026-11-07/README.md)
are versioned with this report. Large traces, cProfiles, DOCX binaries and full
command logs remain in the named local `.build` folders. The report is evidence
for these measured workflows and limits, not a universal reliability guarantee.
