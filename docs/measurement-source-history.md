# Recovery from corrupt manuscript undo history

A damaged undo store now produces an actionable error before the manuscript is
changed. Its exact bytes are kept under a unique `file_history.corrupt-*.json`
name. The next ordinary action starts new history without manual repair. Old
entries in a corrupt store are not claimed to be recovered. Keyboard undo shows
the error and backup path instead of silently dropping the failure.

Product commit: `09e4f45`. Base: `2cc61c0`. Risk: **high** by judgment, because
this changes recovery of authoritative manuscript history. Rollback: revert this
PR; preserved corrupt backup files remain available. No new content cache,
dependency, template or renderer change.

## Same-machine measurements

Apple M4, 16 GiB, macOS 27.2 arm64; Python 3.12.13, Pandoc 3.9.0.2,
uv 0.11.3, Chrome 156.0.8078.80. Identical input hashes are checked by
`compare.py`. Twenty observations per cell, 560 after the change.
Seconds, p50 / p95. Runs were serial, without simultaneous tests/builds.

| Paper | Operation | Regime | Before | After |
| --- | --- | --- | ---: | ---: |
| Example | diagram_open | cold | 0.067 / 0.070 | 0.067 / 0.084 |
| Example | diagram_open | warm | 0.064 / 0.080 | 0.071 / 0.079 |
| Example | mcp_check | cold | 0.472 / 0.485 | 0.475 / 0.481 |
| Example | mcp_check | warm | 0.471 / 0.478 | 0.470 / 0.482 |
| Example | preview_start | cold | 1.455 / 1.478 | 1.467 / 1.497 |
| Example | preview_start | warm | 0.418 / 0.667 | 0.507 / 0.665 |
| Example | save_visible | cold | 1.065 / 1.099 | 1.065 / 1.098 |
| Example | save_visible | warm | 1.081 / 1.113 | 1.080 / 1.111 |
| Example | table_open | cold | 0.067 / 0.069 | 0.068 / 0.100 |
| Example | table_open | warm | 0.063 / 0.064 | 0.063 / 0.064 |
| Example | word_full | cold | 0.505 / 0.528 | 0.514 / 0.528 |
| Example | word_full | warm | 0.393 / 0.401 | 0.396 / 0.407 |
| Example | word_unchanged | cold | 0.119 / 0.121 | 0.120 / 0.121 |
| Example | word_unchanged | warm | 0.119 / 0.121 | 0.120 / 0.123 |
| Load | diagram_open | cold | 0.102 / 0.118 | 0.101 / 0.117 |
| Load | diagram_open | warm | 0.077 / 0.095 | 0.064 / 0.079 |
| Load | mcp_check | cold | 1.051 / 1.066 | 1.037 / 1.059 |
| Load | mcp_check | warm | 1.048 / 1.062 | 1.032 / 1.070 |
| Load | preview_start | cold | 1.519 / 1.542 | 1.530 / 1.543 |
| Load | preview_start | warm | 0.434 / 0.698 | 0.431 / 0.699 |
| Load | save_visible | cold | 2.332 / 2.366 | 2.348 / 2.379 |
| Load | save_visible | warm | 1.147 / 1.203 | 1.192 / 1.218 |
| Load | table_open | cold | 0.150 / 0.184 | 0.151 / 0.201 |
| Load | table_open | warm | 0.096 / 0.113 | 0.095 / 0.098 |
| Load | word_full | cold | 0.792 / 0.840 | 0.790 / 0.807 |
| Load | word_full | warm | 0.615 / 0.627 | 0.610 / 0.630 |
| Load | word_unchanged | cold | 0.132 / 0.136 | 0.133 / 0.136 |
| Load | word_unchanged | warm | 0.135 / 0.137 | 0.134 / 0.136 |

Raw results: `.build/measure/source-history-final/{example,load}/results.json` and
`comparison-{example,load}.json`. No latency improvement is claimed for this
robustness correction. The three original save-to-visible budget misses remain.

With `BASE` the measurement checkout holding the original generated papers, and
`OUT=.build/measure/source-history-final` in this checkout:

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

## Failure proof and checks

The HTTP regression covers save, source read, latest undo and rejecting a tracked
edit, with malformed JSON, invalid shapes and lone Unicode surrogates. It checks
unchanged manuscript bytes, exact quarantined bytes, a useful error, successful
retry and working undo. A real read-only-directory case keeps the corrupt store
intact when quarantine fails, then recovers after permission is restored. A real
Chrome keyboard-undo test checks that the author sees the failure and retry advice.

```sh
uv run --quiet --project packages/trazo python -m unittest discover -s packages/trazo/tests -p test_preview_source_history.py
uv run --quiet --project packages/trazo python scripts/measure/scenarios.py "$BASE/.build/measure/papers/example" "$OUT/fault-corrupt_history" --scenario corrupt_history
make lint
make test-package
make test-template
make acceptance-auto AUTO=push
```

- First negative control: two tests, 25 failing subcases on old code.
- Review negative control: three tests, five failures for the surrogate and
  hidden keyboard error. Final focused run: three tests pass in 4.163 s.
- Evidence: `.build/measure/source-history-validation/` logs, including
  `regression-all-before.log`, `review-regression-before.log` and `review-green-1.log`.
- `corrupt_history`: first request HTTP 500 with backup/retry advice, second HTTP
  200, `source_preserved: true`, `recovered: true`, zero violations. The corrected
  measurement oracle samples preservation immediately after the failed request,
  before the successful retry. Its independent rerun is retained in
  `OUT/fault-corrected-oracle/{command.json,run.log,report.json}`. The original
  post-retry diagnostic is superseded; the HTTP regression also checks bytes at
  the actual failure boundary.
- `make lint`: exit 0. `make test-package`: **914 tests**, 418.804 s,
  `OK (skipped=1)`, the existing Windows-only case.
- `make test-template`: exit 0, 42.365 s; article/report, example and blank variants.
- Every command, exit and actual output is in `OUT/commands.json` and its logs.
- `make typecheck`: not applicable, no strict-override module changed.
- `make test-update`: not directly applicable; the push hook runs it.
- Acceptance: `.build/acceptance/20261106T222416Z/report.md`, **18 passed,
  0 failed, 0 skipped, 0 not automated**. All 18 fresh; none cached.

Linux and Windows were not executed locally. This does not recover previously
lost history entries, guarantee power-loss durability or serialize independent
external writers. The damaged bytes remain available for separate recovery.
