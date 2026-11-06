# Bounded agent commands

An agent build, check, paper creation/update or preview input preparation that
hangs now stops with an explicit error after 20 minutes by default. The call's
process group is stopped, available command output is kept, and the same MCP
session can serve the next request. `TRAZO_MCP_TIMEOUT` sets a positive finite
number of seconds. Invalid values fail before launching the command.

Product commit: `3f77f64`. Comparison base: `2cc61c0`. Risk: **high**, as the
MCP lifecycle and error contract change. Rollback: revert this PR, which restores
unbounded agent subprocess calls. No new cache, dependency or renderer change.

## Measurements

Same Apple M4 / 16 GiB / macOS 27.2 arm64 as the baseline. Python 3.12.13,
Pandoc 3.9.0.2, uv 0.11.3 and Chrome 156.0.8078.80. Identical fixture input hashes
verified by `compare.py`. Twenty samples per operation and regime, 560 after
observations. Seconds, p50 / p95. No normal-operation speed improvement is claimed.

| Paper | Operation | Regime | Before | After |
| --- | --- | --- | ---: | ---: |
| Example | diagram_open | cold | 0.067 / 0.070 | 0.067 / 0.083 |
| Example | diagram_open | warm | 0.064 / 0.080 | 0.072 / 0.080 |
| Example | mcp_check | cold | 0.472 / 0.485 | 0.454 / 0.463 |
| Example | mcp_check | warm | 0.471 / 0.478 | 0.451 / 0.458 |
| Example | preview_start | cold | 1.455 / 1.478 | 1.449 / 1.478 |
| Example | preview_start | warm | 0.418 / 0.667 | 0.515 / 0.658 |
| Example | save_visible | cold | 1.065 / 1.099 | 1.073 / 1.095 |
| Example | save_visible | warm | 1.081 / 1.113 | 1.092 / 1.129 |
| Example | table_open | cold | 0.067 / 0.069 | 0.075 / 0.085 |
| Example | table_open | warm | 0.063 / 0.064 | 0.063 / 0.080 |
| Example | word_full | cold | 0.505 / 0.528 | 0.494 / 0.509 |
| Example | word_full | warm | 0.393 / 0.401 | 0.393 / 0.399 |
| Example | word_unchanged | cold | 0.119 / 0.121 | 0.119 / 0.121 |
| Example | word_unchanged | warm | 0.119 / 0.121 | 0.119 / 0.120 |
| Load | diagram_open | cold | 0.102 / 0.118 | 0.100 / 0.117 |
| Load | diagram_open | warm | 0.077 / 0.095 | 0.064 / 0.080 |
| Load | mcp_check | cold | 1.051 / 1.066 | 1.082 / 1.277 |
| Load | mcp_check | warm | 1.048 / 1.062 | 1.082 / 1.115 |
| Load | preview_start | cold | 1.519 / 1.542 | 1.524 / 1.538 |
| Load | preview_start | warm | 0.434 / 0.698 | 0.431 / 0.697 |
| Load | save_visible | cold | 2.332 / 2.366 | 2.350 / 2.366 |
| Load | save_visible | warm | 1.147 / 1.203 | 1.162 / 1.211 |
| Load | table_open | cold | 0.150 / 0.184 | 0.151 / 0.200 |
| Load | table_open | warm | 0.096 / 0.113 | 0.080 / 0.113 |
| Load | word_full | cold | 0.792 / 0.840 | 0.801 / 0.830 |
| Load | word_full | warm | 0.615 / 0.627 | 0.684 / 0.791 |
| Load | word_unchanged | cold | 0.132 / 0.136 | 0.176 / 0.185 |
| Load | word_unchanged | warm | 0.135 / 0.137 | 0.159 / 0.174 |

The same 25 of 28 p95 budgets pass. Warm source saves on both papers and cold
source saves on the load paper remain above budget. Load Word/check timings moved
up while some unrelated editor timings moved down; the full raw samples and
bootstrap intervals are retained. These movements are not treated as speed wins.

A second serial control compared the unchanged base with the candidate immediately
on the same load paper (20 samples per cell on each side). It did not reproduce the
large Word timing increase:

| Operation | Regime | Base control | Candidate control |
| --- | --- | ---: | ---: |
| word_full | cold | 0.761 / 0.783 | 0.771 / 0.785 |
| word_full | warm | 0.595 / 0.611 | 0.600 / 0.618 |
| word_unchanged | cold | 0.131 / 0.133 | 0.132 / 0.134 |
| word_unchanged | warm | 0.129 / 0.132 | 0.130 / 0.135 |

The control differences are under 2% in both quantiles. Some bootstrap intervals
exclude zero, but none meets the declared 5% practical threshold. This is evidence
against attributing the earlier large movement to the code change, not proof of
zero overhead. The original observations remain in the table above. Complete
commands, samples and comparisons: `.build/measure/deadline-word-control/`.


Commands (serial, with no concurrent test/build workload):

```sh
uv run --quiet --project packages/trazo python scripts/measure/run.py .build/measure/baseline-papers/example .build/measure/deadline-after/example
uv run --quiet --project packages/trazo python scripts/measure/run.py '.build/measure/final-papers/carga con acentos á' .build/measure/deadline-after/load
uv run --quiet --project packages/trazo python scripts/measure/compare.py .build/measure/baseline-example/results.json .build/measure/deadline-after/example/results.json .build/measure/deadline-after/comparison-example.json
uv run --quiet --project packages/trazo python scripts/measure/compare.py .build/measure/final-load/results.json .build/measure/deadline-after/load/results.json .build/measure/deadline-after/comparison-load.json
```

Both papers were force-built again from restored inputs. `equivalence.py PAPER OUT
--compare BEFORE` returned `equal: true` for both: all normalized DOCX ZIP members,
diagram exports and renderer hashes match. Captures and real `diagram check --all
--exports --json` results: `.build/measure/deadline-after/word-{example,load}/`.
Before: `.build/measure/word-before-{example,load}/`. Only ZIP container metadata
and core created/modified timestamps are normalized. No content difference.
Renderer SHA256:
`41dd04c3e8a95b79c8a404fb97a51f0b5a25e902f0b100adde44ee06023e0599`.

## Failure proof and validation

The real MCP regression first failed on the old product because no product
deadline fired. Extending it with command output also failed before that output
was retained. Logs: `.build/measure/deadline-validation/regression-before.log`
and `output-regression-before.log` in that folder.

```sh
uv run --quiet --project packages/trazo python -m unittest discover -s packages/trazo/tests -p test_mcp_lifetime.py
TRAZO_MCP_TIMEOUT=1 uv run --quiet --project packages/trazo python scripts/measure/scenarios.py .build/measure/papers/example .build/measure/deadline-faults-final --scenario pandoc_hang
make lint
make test-package
make test-template
make acceptance-auto AUTO=push
```

- Focused lifecycle suite: 13 tests pass, 28.475 s. A hung command and a detached
  child retaining its pipes both produce a bounded error, retain output and the
  previous valid Word file, and allow a ping and successful retry on the same session.
- Final hang injection: zero violations; actual product timeout at 1 s, no
  supervisor cancellation, old DOCX preserved, MCP alive and retry successful.
  `.build/measure/deadline-faults-final/report.json`.
- `make lint`: exit 0, 0.080 s.
- `make test-package`: **914 tests, OK, one Windows-only skip**, 422.497 test seconds.
- `make test-template`: exit 0, 40.179 s.
- Full logs and command records: `.build/measure/deadline-validation-final/`.
- `make typecheck`: not run; no strict-override module changed.
- `make test-update`: not directly applicable; the push hook runs it.
- Acceptance: `.build/acceptance/20261106T212346Z/report.md`, **18 passed, 0 failed,
  0 skipped, 0 not automated**. All 18 fresh, none cached.

## Limits

Standalone CLI commands and the already-running preview watcher remain outside
this deadline. Deliberately detached sessions are not owned by the killed group.
The pipe drain waits at most five seconds; on Windows a daemon pipe reader may
remain while an escaped descendant retains the handle. Windows process-tree
termination has its existing separate 30-second bound. Linux and Windows behavior
was reviewed but not executed locally. Native executable writes and power-loss
durability were not tested. The pre-existing cancel/reaped-PID race is unchanged.
