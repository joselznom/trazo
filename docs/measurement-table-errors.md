# Visible table failures in the preview

When a built-in table render fails, the preview now displays the error and keeps
its last good page. Further prose saves cannot hide that error. Fixing the table
rebuilds the page, including prose written while generation was failing.

Product commit: `e0dab4a`. Base: `2cc61c0`. Risk: **medium** (`preview_watch.py`).
Rollback: revert this PR, restoring the previous log-only table failure behavior.
No new cache, dependency, template or diagram renderer change.

## Same-machine measurements

Apple M4, 16 GiB, macOS 27.2 arm64; Python 3.12.13, Pandoc 3.9.0.2,
uv 0.11.3, Chrome 156.0.8078.80. Identical paper hashes verified by `compare.py`.
Twenty samples per operation and regime, 560 observations after the fix.
Seconds, p50 / p95. No speed improvement is claimed for this robustness change.

| Paper | Operation | Regime | Before | After |
| --- | --- | --- | ---: | ---: |
| Example | diagram_open | cold | 0.067 / 0.070 | 0.068 / 0.085 |
| Example | diagram_open | warm | 0.064 / 0.080 | 0.063 / 0.081 |
| Example | mcp_check | cold | 0.472 / 0.485 | 0.473 / 0.479 |
| Example | mcp_check | warm | 0.471 / 0.478 | 0.475 / 0.481 |
| Example | preview_start | cold | 1.455 / 1.478 | 1.464 / 1.484 |
| Example | preview_start | warm | 0.418 / 0.667 | 0.515 / 0.662 |
| Example | save_visible | cold | 1.065 / 1.099 | 1.067 / 1.082 |
| Example | save_visible | warm | 1.081 / 1.113 | 1.081 / 1.127 |
| Example | table_open | cold | 0.067 / 0.069 | 0.067 / 0.085 |
| Example | table_open | warm | 0.063 / 0.064 | 0.063 / 0.067 |
| Example | word_full | cold | 0.505 / 0.528 | 0.515 / 0.524 |
| Example | word_full | warm | 0.393 / 0.401 | 0.403 / 0.408 |
| Example | word_unchanged | cold | 0.119 / 0.121 | 0.120 / 0.125 |
| Example | word_unchanged | warm | 0.119 / 0.121 | 0.120 / 0.123 |
| Load | diagram_open | cold | 0.102 / 0.118 | 0.100 / 0.118 |
| Load | diagram_open | warm | 0.077 / 0.095 | 0.063 / 0.094 |
| Load | mcp_check | cold | 1.051 / 1.066 | 1.043 / 1.067 |
| Load | mcp_check | warm | 1.048 / 1.062 | 1.037 / 1.062 |
| Load | preview_start | cold | 1.519 / 1.542 | 1.522 / 1.541 |
| Load | preview_start | warm | 0.434 / 0.698 | 0.442 / 0.708 |
| Load | save_visible | cold | 2.332 / 2.366 | 2.348 / 2.382 |
| Load | save_visible | warm | 1.147 / 1.203 | 1.149 / 1.177 |
| Load | table_open | cold | 0.150 / 0.184 | 0.151 / 0.200 |
| Load | table_open | warm | 0.096 / 0.113 | 0.095 / 0.115 |
| Load | word_full | cold | 0.792 / 0.840 | 0.795 / 0.859 |
| Load | word_full | warm | 0.615 / 0.627 | 0.616 / 0.626 |
| Load | word_unchanged | cold | 0.132 / 0.136 | 0.134 / 0.137 |
| Load | word_unchanged | warm | 0.135 / 0.137 | 0.136 / 0.137 |

Raw samples and fixed-seed bootstrap comparisons:
`.build/measure/preview-table-final/{example,load}/results.json` and
`comparison-{example,load}.json`. The three save-to-visible budget misses remain.

The runs were serial, without concurrent tests/builds. The commands below use
`BASE` for the measurement checkout, which contains the original papers and
baseline captures, and `OUT=.build/measure/preview-table-final` in this checkout:

```sh
uv run --quiet --project packages/trazo python scripts/measure/run.py "$BASE/.build/measure/baseline-papers/example" "$OUT/example"
uv run --quiet --project packages/trazo python scripts/measure/run.py "$BASE/.build/measure/final-papers/carga con acentos á" "$OUT/load"
uv run --quiet --project packages/trazo python scripts/measure/compare.py "$BASE/.build/measure/baseline-example/results.json" "$OUT/example/results.json" "$OUT/comparison-example.json"
uv run --quiet --project packages/trazo python scripts/measure/compare.py "$BASE/.build/measure/final-load/results.json" "$OUT/load/results.json" "$OUT/comparison-load.json"
```

After restoring inputs, both papers were force-built again. `equivalence.py PAPER
OUT --compare BEFORE` returned `equal: true` for each: normalized DOCX ZIP members,
export images and renderer hash are identical. Only ZIP container metadata and
core created/modified timestamps are normalized. The real diagram export checks
pass. Evidence: `word-{example,load}/comparison.json` and companion captures under
`OUT`. Renderer SHA256 remains
`41dd04c3e8a95b79c8a404fb97a51f0b5a25e902f0b100adde44ee06023e0599`.

## Failure proof and checks

The real MCP/HTTP regression fails before the fix: corrupt table data leaves the
preview status successful and stale. The same test passes after the fix, including
a prose edit while broken and recovery after restoring data. The existing
render-during-data-write regression returns the render result so it continues to
exercise the successful rendering path.

```sh
uv run --quiet --project packages/trazo python -m unittest discover -s packages/trazo/tests -p test_preview_tables.py
uv run --quiet --project packages/trazo python scripts/measure/scenarios.py "$BASE/.build/measure/papers/example" "$OUT/faults" --scenario corrupt_table
make lint
make test-package
make test-template
make acceptance-auto AUTO=push
```

- Before: 6 tests, one failure (`preview-table-validation/regression-before.log`).
- Focused preview/table suite: 137 tests pass in 49.258 s
  (`preview-table-validation/focused-final.log`).
- `corrupt_table`: zero violations, visible table error, recovery successful.
- `make lint`: exit 0, 0.062 s.
- `make test-package`: 912 tests in 417.594 s, `OK (skipped=1)`.
  The skip is the existing Windows-only case.
- `make test-template`: exit 0, 40.320 s, article/report, example and blank variants.
- Full commands, exits, timings and actual output: `OUT/commands.json` and logs.
- `make typecheck`: not applicable, no strict-override module changed.
- `make test-update`: not directly applicable; the push hook runs it.
- Acceptance: `.build/acceptance/20261106T220350Z/report.md`, **18 passed,
  0 failed, 0 skipped, 0 not automated**. All 18 fresh, none cached.

Linux and Windows were not run locally. This change does not add deadlines to
custom table generators or solve independent external writers. Last good pages
remain visible while their status reports the failure; they are not marked fresh.
