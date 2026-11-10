# User workflow measurements

Developer tooling, Python standard library only. Run from the repository root with
the checkout's Python. Nothing is installed into a paper and no product code is
instrumented on disk. The fault harness supports POSIX process groups (macOS/Linux).

## Generate and measure

```sh
uv run --quiet --project packages/trazo python scripts/measure/fixture.py .build/measure/papers/example
uv run --quiet --project packages/trazo python scripts/measure/fixture.py '.build/measure/papers/load with áccents' --load
uv run --quiet --project packages/trazo python scripts/measure/run.py .build/measure/papers/example .build/measure/example
uv run --quiet --project packages/trazo python scripts/measure/run.py '.build/measure/papers/load with áccents' .build/measure/load
```

Use new output folders. Fixture generation refuses to overwrite existing papers.
The load fixture preserves the example's data/figure/value scripts, adds 160 coherent
synthetic paragraphs, nine table specs and ten distinct 1000 x 650 raster plots,
and cites 80 extra synthetic references. About 11,000 words, 12 figures and ten
tables represent a long methods paper with extensive validation, not a thesis or a
maximum-size stress test. One real declarative diagram exercises the editor and
export stamps. The manifest records actual sizes, seed and input hashes. Synthetic
references are not research evidence. Table variants share the example's source
data; the dataset is deliberately small, and large-data analysis is not measured.

The default is 20 samples per operation and regime. All raw samples are retained;
p50 is the median and p95 is the nearest-rank observation (19th of 20). No samples
are silently discarded. `--smoke --samples 1` is diagnostic and cannot be a baseline.
Machine, Python, Pandoc, uv, Chrome, commit and renderer hash accompany the output.
Run the suites serially on the same machine without tests/builds running alongside.

Validate the measurement oracles with:

```sh
uv run --quiet --project packages/trazo python -m unittest discover -s scripts/measure/tests
uv run --quiet --project packages/trazo ruff check --config packages/trazo/pyproject.toml scripts/measure
```

The crash-boundary test catches delayed SIGKILL delivery from a worker thread on
macOS. The Word-normalization test allows only the declared timestamp differences
and rejects changed document text. After a candidate fix, `compare.py BEFORE.json
AFTER.json OUTPUT.json` checks machine/input identity and computes fixed-seed
bootstrap intervals. An improvement must exceed both the interval's noise and 5%.
With 20 observations, p95 remains a sparse empirical tail estimate.

| Operation | Start | End |
| --- | --- | --- |
| `preview_start` | Send the real MCP tool request | Main manuscript has content and status, fonts ready and two animation frames elapsed |
| `save_visible` | Browser POST to the actual source-save endpoint | A unique new paragraph is visible after the real watcher/reload pipeline |
| `table_open` | Navigate/reload the table editor | Table cells exist, fonts ready and two frames elapsed |
| `diagram_open` | Navigate/reload the diagram editor | Diagram SVG nodes exist, fonts ready and two frames elapsed |
| `word_full` | MCP `build(force=true)` | Successful reply after both Word files are built |
| `word_unchanged` | MCP `build` with primed outputs | Successful unchanged-build reply |
| `mcp_check` | MCP `check` | Structured submission-check reply |

Cold means a new initialized MCP connection, and a fresh Chrome process/profile
for browser operations. Preview start and full build also discard the disposable
paper's `.build` cache. The other operations retain required build outputs. Warm
means one retained session after an unmeasured warm-up. Build/check tools still
spawn Python for each request, as the product does. Unchanged builds are primed
in both regimes; a cold unchanged build is a fresh MCP process, not a missing DOCX.
Chrome/MCP installation and initialization, server setup for editor/save operations,
the existing project `.venv`, uv's package cache and the OS file cache are outside
the timed interval. **This is application cold start, not physical-disk cold start.**
The browser milestone is a conservative paint opportunity, not pixel recognition
or proof that every offscreen image has decoded. Warm editor samples reload the
actual document; a surviving old DOM cannot satisfy the milestone.

## Idle polling cost

```sh
uv run --quiet --project packages/trazo python scripts/measure/idle.py PAPER OUTPUT.json --checkout PRODUCT_CHECKOUT
```

This complementary measurement uses 20 settled idle windows of two seconds each.
It reports server CPU from `ps`, Chrome main-renderer task time and request rates,
with p50/p95 and raw samples. CPU percentages are fractions of one core, not of
all machine cores. Server descendants and total browser/system energy are outside
this scope. Run before and after serially on the same load paper. It waits for
initial watcher work and browser reloads to settle before opening the windows.

## Separate profiling

```sh
uv run --quiet --project packages/trazo python scripts/measure/run.py .build/measure/papers/example .build/measure/profile-example --smoke --samples 1 --profile
```

Repeat for the load paper. These timings are not compared to uninstrumented samples.
The opt-in `sitecustomize.py` exists only on the child processes' `PYTHONPATH`.
It records cProfile files plus `perf_counter` spans for subprocess waits, filesystem
APIs, watcher scans and preview renders. Python 3.12 allows one cProfile monitoring
tool per interpreter, so the preview gives it to the render/watcher thread; child
CLI processes profile their main thread. HTTP workers have filesystem/subprocess
spans but not separate cProfiles. Spans nest and processes overlap: do not sum them
as an exclusive wall-time partition. Filesystem API wall time includes the page
cache and is not a measurement of physical disk service time.

Chrome traces are DevTools `Tracing` JSON, accompanied by inclusive event totals
and performance metrics. They include JS, layout and paint work. Inclusive trace
durations also overlap. Raw traces open in Chrome's trace viewer or Perfetto.
`analyze_profiles.py PROFILE_RUN` clips recorded spans to each timed operation and
writes `profile-summary.json`; whole-process cProfile totals remain separately labeled.

## Failure contract and fault injection

The proposed contract, checked at the filesystem/HTTP/MCP boundaries:

1. An acknowledged save preserves its complete new text. A replaced user version
   remains recoverable in history. Conflicting writers must get a conflict or keep
   both versions, never an acknowledged overwrite with no recovery copy.
2. Published user files, JSON state and DOCX are complete old or new artifacts.
   Temporary scratch files may remain after SIGKILL but must never be trusted as
   successful outputs. Atomic rename is not power-loss durability without fsync.
3. Every bounded operation has a product deadline, kills its descendants on expiry
   and exposes a specific failure. The harness's deadline does not satisfy this.
4. Failed generation keeps the last good visible artifact and exposes the error in
   browser/MCP status. A console message alone is insufficient for a browser user.
5. After a transient fault is removed, the next normal operation works without
   deleting caches, repairing history or restarting the host by hand. Persistent
   missing tools, permissions and a genuinely full disk must be reported until the
   environment is repaired; the app cannot manufacture writable storage.
6. A build sees a coherent snapshot and a subsequent build detects an intervening
   source edit. A build that cannot guarantee freshness must not advertise it.

```sh
uv run --quiet --project packages/trazo python scripts/measure/inventory.py .build/measure/audit
uv run --quiet --project packages/trazo python scripts/measure/faults.py .build/measure/papers/example .build/measure/write-faults
uv run --quiet --project packages/trazo python scripts/measure/scenarios.py .build/measure/papers/example .build/measure/scenarios
```

`faults.py` first records reached Python open/write/replace boundaries on successful
source, table, diagram, Word, preview and diagram-export operations. Each injection
runs in its own copy. SIGKILL occurs before open, halfway through a write, or either
side of replacement. ENOSPC occurs at open/write/rename; EACCES at open/rename.
This models returned OS errors without filling the owner's volume. A separate
scenario uses real directory permissions. Every case retains its site, result,
stderr and recovery result. Failed copies are retained. `--operation`, `--fault`
and `--site` replay an exact case in a new output folder. Exit 1 means an invariant
failed, as expected while auditing unfixed code. One success after an injected
failure is accepted and listed as such: a source, table or diagram save that
published, then could not set the replaced file aside, and answered with a `warning`. It counts only when
the file holds the complete new text and the old text is in undo history.

The static AST inventory covers source-level candidates, including all 19 broad
Python handlers. It is not proof of runtime coverage. The dynamic write campaign
does not intercept writes inside external executables, OS writes below Python's
file API, unvisited branches, setup/update operations, or every browser edit mode.
Those gaps must be reported, never counted as passes. The independent-writer
scenario pauses the real HTTP save before replacement, then writes from a separate
process. Fixed-seed fuzzing exercises 100 malformed HTTP payloads; it is a bounded
sample, not a proof over all corrupt input.

## Word and export equivalence

```sh
uv run --quiet --project packages/trazo python scripts/measure/equivalence.py .build/measure/papers/example .build/measure/word-before
# Run the candidate build on identical inputs.
uv run --quiet --project packages/trazo python scripts/measure/equivalence.py .build/measure/papers/example .build/measure/word-after --compare .build/measure/word-before
```

Repeat for the load paper. All ZIP members are compared, ignoring ZIP container
timestamps/order/compression and only the `created`/`modified` timestamps in
`docProps/core.xml`. Document XML, styles, relationships, embedded media and other
properties are preserved in the comparison. Copies of the DOCX, per-member hashes,
export hashes, renderer hash and the real `diagram check --all --exports --json`
output are retained. A missing or corrupt ZIP fails instead of normalizing away
the problem.
