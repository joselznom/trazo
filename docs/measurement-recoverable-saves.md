# Recoverable table and diagram saves

A table or diagram save now journals its undo entry before replacing the source.
An interrupted publication is reconciled with the file's hash on the next history
read. A failed grouped autosave restores the preceding group. An unreadable pending
file retains its evidence without blocking saves to other files.

Product commit: `6e92b9e`. Comparison base: `2cc61c0`. Risk: **high**, raised from
the paths' medium minimum because this changes crash recovery of user text.
Rollback: revert this PR; newly stored pending metadata is compatible with older
history readers, but the original publication-order failure returns.

## Measurements

Same Apple M4 / 16 GiB / macOS 27.2 arm64 as the baseline. Python 3.12.13,
Pandoc 3.9.0.2, uv 0.11.3 and Chrome 156.0.8078.80. Identical input hashes verified
by `compare.py`. Twenty observations per operation and regime, 560 after samples.
Seconds, p50 / p95. No speed improvement is claimed for this storage correction.
The source-save measurements do not directly time table/diagram save latency.

| Paper | Operation | Regime | Before | After |
| --- | --- | --- | ---: | ---: |
| Example | diagram_open | cold | 0.067 / 0.070 | 0.067 / 0.085 |
| Example | diagram_open | warm | 0.064 / 0.080 | 0.063 / 0.078 |
| Example | mcp_check | cold | 0.472 / 0.485 | 0.472 / 0.481 |
| Example | mcp_check | warm | 0.471 / 0.478 | 0.478 / 0.493 |
| Example | preview_start | cold | 1.455 / 1.478 | 1.458 / 1.476 |
| Example | preview_start | warm | 0.418 / 0.667 | 0.506 / 0.665 |
| Example | save_visible | cold | 1.065 / 1.099 | 1.065 / 1.083 |
| Example | save_visible | warm | 1.081 / 1.113 | 1.080 / 1.112 |
| Example | table_open | cold | 0.067 / 0.069 | 0.067 / 0.084 |
| Example | table_open | warm | 0.063 / 0.064 | 0.063 / 0.066 |
| Example | word_full | cold | 0.505 / 0.528 | 0.522 / 0.537 |
| Example | word_full | warm | 0.393 / 0.401 | 0.403 / 0.413 |
| Example | word_unchanged | cold | 0.119 / 0.121 | 0.122 / 0.124 |
| Example | word_unchanged | warm | 0.119 / 0.121 | 0.122 / 0.126 |
| Load | diagram_open | cold | 0.102 / 0.118 | 0.109 / 0.118 |
| Load | diagram_open | warm | 0.077 / 0.095 | 0.063 / 0.093 |
| Load | mcp_check | cold | 1.051 / 1.066 | 1.044 / 1.059 |
| Load | mcp_check | warm | 1.048 / 1.062 | 1.055 / 1.078 |
| Load | preview_start | cold | 1.519 / 1.542 | 1.522 / 1.549 |
| Load | preview_start | warm | 0.434 / 0.698 | 0.435 / 0.700 |
| Load | save_visible | cold | 2.332 / 2.366 | 2.345 / 2.366 |
| Load | save_visible | warm | 1.147 / 1.203 | 1.146 / 1.177 |
| Load | table_open | cold | 0.150 / 0.184 | 0.151 / 0.168 |
| Load | table_open | warm | 0.096 / 0.113 | 0.095 / 0.128 |
| Load | word_full | cold | 0.792 / 0.840 | 0.804 / 0.865 |
| Load | word_full | warm | 0.615 / 0.627 | 0.624 / 0.636 |
| Load | word_unchanged | cold | 0.132 / 0.136 | 0.134 / 0.138 |
| Load | word_unchanged | warm | 0.135 / 0.137 | 0.136 / 0.137 |

The three failing baseline budget cells remain: warm source saves on both papers
and cold source saves on the load paper. The other 25 cells remain within budget.
Several medians/tails moved, including small Word and unchanged-build slowdowns;
they do not change these budget conclusions. Bootstrap comparisons are retained,
not used to claim an optimization of an unrelated operation.

Commands (run serially, without validation running alongside):

```sh
uv run --quiet --project packages/trazo python scripts/measure/run.py .build/measure/baseline-papers/example .build/measure/history-after/example
uv run --quiet --project packages/trazo python scripts/measure/run.py '.build/measure/final-papers/carga con acentos á' .build/measure/history-after/load
uv run --quiet --project packages/trazo python scripts/measure/compare.py .build/measure/baseline-example/results.json .build/measure/history-after/example/results.json .build/measure/history-after/comparison-example.json
uv run --quiet --project packages/trazo python scripts/measure/compare.py .build/measure/final-load/results.json .build/measure/history-after/load/results.json .build/measure/history-after/comparison-load.json
```

Both forced builds completed. `equivalence.py PAPER OUT --compare BEFORE` returned
`equal: true` for the example and load, comparing every normalized DOCX ZIP member,
diagram export hashes and renderer hash. Captures and real `diagram check --all
--exports --json` results: `.build/measure/history-after/word-{example,load}/`.
Before captures: `.build/measure/word-before-{example,load}/`. Only ZIP container
metadata and core created/modified timestamps are normalized. No content difference.
Renderer SHA256 remains
`41dd04c3e8a95b79c8a404fb97a51f0b5a25e902f0b100adde44ee06023e0599`.

## Failure proof and validation

The HTTP-save regression failed before the fix in seven subcases, twice. A second
regression reproduced an unreadable pending file blocking another file's save.
Logs: `.build/measure/history-regression-{before,repeat}.log` and
`.build/measure/history-validation/pending-read-before.log`.

```sh
uv run --quiet --project packages/trazo python -m unittest discover -s packages/trazo/tests -p test_save_history.py
uv run --quiet --project packages/trazo python scripts/measure/faults.py .build/measure/papers/example .build/measure/history-faults-final --operation table --operation diagram
make lint
make test-package
make test-template
make typecheck
make acceptance-auto AUTO=push
```

- Focused regression: 4 tests pass (0.571 s).
- Fault campaign: **56 injections, zero invariant failures**, all triggers reached,
  all retries successful. SIGKILL, ENOSPC and EACCES at reached Python boundaries.
  Report: `.build/measure/history-faults-final/report.md`.
- `make lint`: exit 0.
- `make test-package`: **915 tests, OK, one Windows-only skip**, 415.564 test seconds.
- `make test-template`: exit 0, 40.664 s.
- `make typecheck`: exit 2, **148 errors in 37 other files**; no error in changed
  strict module `review_history`. The whole-package check is not green.
- Full logs and command results: `.build/measure/history-validation-final/`.
- `make test-update`: not directly applicable to this diff; the push hook runs it.
- Linux/Windows execution, native executable writes and power-loss durability were
  not tested. Cached acceptance is not a fresh pass.

Acceptance: `.build/acceptance/20261106T204101Z/report.md`, **18 passed, 0 failed,
0 skipped, 0 not automated**. All 18 ran fresh; none cached.

## Limits

The existing 200-entry / 5 MB retention limit may evict oldest undo entries for an
attempt that subsequently fails. Pending entries survive unreadability, but cannot
recover bytes never persisted. This PR does not close the independent-writer race,
comment/history transactions, corrupt source history, silent table generation,
subprocess deadlines or partial preview scratch writes. Those are tracked separately.
No dependency, cache, template or renderer change.
