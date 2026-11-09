# Complete preview intermediate files

Measured product commit: `12dd3a6`, based on `2cc61c0`.
Risk: medium (`preview_render.py`). No strict module, template, dependency,
renderer or content-cache change.

## Result and limits

The two Pandoc inputs, `manuscript.md` and `after_body.html` in the preview
build directory, now publish through the existing atomic writer. Interrupted
or failed writes leave the previous complete files. This closes the four
intermediate-file failures in the original campaign; it does not claim a
new protection for the published HTML or recovery, which already worked.
SIGKILL can leave an unused uniquely named `.tmp` file. Power-loss durability,
native subprocess writes and unvisited branches are not covered.

## Regression and injected failures

The real POSIX file-size limit regression failed on the original code with
one test and two failing subcases, one per intermediate. It passes after the
change. No production injection hooks were added. Command:

```sh
uv run --quiet --project packages/trazo python -m unittest discover -s packages/trazo/tests -p test_preview_scratch.py
uv run --quiet --project packages/trazo python scripts/measure/faults.py PAPER OUT --operation preview
```

The final campaign ran 30 injected failures across the reached preview write
boundaries: SIGKILL, ENOSPC and EACCES where meaningful. Zero invariant
violations; every next ordinary operation recovered. Report:
`.build/measure/preview-scratch-final/fault-preview/report.json`.

## Same-machine measurements

Apple M4, 16 GiB, macOS 27.2 arm64; Python 3.12.13, Pandoc 3.9.0.2,
uv 0.11.3, Chrome 156.0.8078.80. Identical input hashes are checked by
`compare.py`. Twenty observations per cell, 560 after the change.
Seconds, p50 / p95. Runs were serial, without simultaneous tests/builds.

| Paper | Operation | Regime | Before | After |
| --- | --- | --- | ---: | ---: |
| Example | diagram_open | cold | 0.067 / 0.070 | 0.068 / 0.084 |
| Example | diagram_open | warm | 0.064 / 0.080 | 0.063 / 0.080 |
| Example | mcp_check | cold | 0.472 / 0.485 | 0.472 / 0.482 |
| Example | mcp_check | warm | 0.471 / 0.478 | 0.474 / 0.487 |
| Example | preview_start | cold | 1.455 / 1.478 | 1.458 / 1.473 |
| Example | preview_start | warm | 0.418 / 0.667 | 0.401 / 0.665 |
| Example | save_visible | cold | 1.065 / 1.099 | 1.065 / 1.083 |
| Example | save_visible | warm | 1.081 / 1.113 | 1.079 / 1.109 |
| Example | table_open | cold | 0.067 / 0.069 | 0.067 / 0.085 |
| Example | table_open | warm | 0.063 / 0.064 | 0.063 / 0.079 |
| Example | word_full | cold | 0.505 / 0.528 | 0.512 / 0.528 |
| Example | word_full | warm | 0.393 / 0.401 | 0.401 / 0.412 |
| Example | word_unchanged | cold | 0.119 / 0.121 | 0.120 / 0.123 |
| Example | word_unchanged | warm | 0.119 / 0.121 | 0.119 / 0.125 |
| Load | diagram_open | cold | 0.102 / 0.118 | 0.101 / 0.118 |
| Load | diagram_open | warm | 0.077 / 0.095 | 0.070 / 0.094 |
| Load | mcp_check | cold | 1.051 / 1.066 | 1.037 / 1.051 |
| Load | mcp_check | warm | 1.048 / 1.062 | 1.035 / 1.058 |
| Load | preview_start | cold | 1.519 / 1.542 | 1.520 / 1.535 |
| Load | preview_start | warm | 0.434 / 0.698 | 0.433 / 0.697 |
| Load | save_visible | cold | 2.332 / 2.366 | 2.351 / 2.367 |
| Load | save_visible | warm | 1.147 / 1.203 | 1.146 / 1.191 |
| Load | table_open | cold | 0.150 / 0.184 | 0.151 / 0.200 |
| Load | table_open | warm | 0.096 / 0.113 | 0.095 / 0.116 |
| Load | word_full | cold | 0.792 / 0.840 | 0.787 / 0.839 |
| Load | word_full | warm | 0.615 / 0.627 | 0.617 / 0.622 |
| Load | word_unchanged | cold | 0.132 / 0.136 | 0.133 / 0.137 |
| Load | word_unchanged | warm | 0.135 / 0.137 | 0.135 / 0.136 |

Raw results: `.build/measure/preview-scratch-final/{example,load}/results.json` and
`comparison-{example,load}.json`. No latency improvement is claimed for this
robustness correction. The three original save-to-visible budget misses remain.

With `BASE` the measurement checkout holding the original generated papers, and
`OUT=.build/measure/preview-scratch-final` in this checkout:

```sh
uv run --quiet --project packages/trazo python scripts/measure/run.py "$BASE/.build/measure/baseline-papers/example" "$OUT/example"
uv run --quiet --project packages/trazo python scripts/measure/run.py "$BASE/.build/measure/final-papers/carga con acentos á" "$OUT/load"
uv run --quiet --project packages/trazo python scripts/measure/compare.py "$BASE/.build/measure/baseline-example/results.json" "$OUT/example/results.json" "$OUT/comparison-example.json"
uv run --quiet --project packages/trazo python scripts/measure/compare.py "$BASE/.build/measure/final-load/results.json" "$OUT/load/results.json" "$OUT/comparison-load.json"
```

Both papers were force-built after restoring inputs. `equivalence.py PAPER OUT
--compare BEFORE` reports `equal: true` for both, including every normalized
DOCX ZIP member, export images and renderer hash. Only ZIP container metadata
and core creation/modification timestamps are ignored. The real diagram export
checks pass. Captures: `word-{example,load}/comparison.json` and companion files
under `OUT`. Renderer SHA256:
`41dd04c3e8a95b79c8a404fb97a51f0b5a25e902f0b100adde44ee06023e0599`.

## Required checks

Actual outputs and argv are in `.build/measure/preview-scratch-final/commands.json`
and its companion logs. On the measured committed product:

| Command | Result |
| --- | --- |
| `make lint` | exit 0, 0.144 s |
| `make test-package` | exit 0, 912 tests in 411.672 s, one existing Windows-only skip |
| `make test-template` | exit 0, both example variants, 40.701 s |
| `make acceptance-auto AUTO=push` | exit 0, 18 fresh passes, 0 failed, 0 skipped, 0 not automated |

Acceptance report: `.build/acceptance/20261106T225113Z/report.md`.
No scenario was cached in that run. Push hooks repeat package, template and
update checks on the exact pushed tip; their logs are retained separately.
`make typecheck` is not applicable: no strict-override module changed.
`make test-update` is not required by the changed paths, but the push hook
runs it. Linux and Windows were not run locally. Manual desktop acceptance
is owner-only and was not run.
