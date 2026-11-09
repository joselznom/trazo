# Bounded agent commands

An agent build, check, paper creation/update or preview input preparation that
hangs now stops with an explicit error after 20 minutes by default. The call's
process group is stopped, available command output is kept, and the same MCP
session can serve the next request. `TRAZO_MCP_TIMEOUT` sets a positive finite
number of seconds. Invalid values fail before launching the command.

Measured product commit: `6edd945` (including the optional-pipe narrowing).
Original deadline correction: `3f77f64`. Comparison base: `2cc61c0`. Risk: **high**, as the
MCP lifecycle and error contract change. Rollback: revert this PR, which restores
unbounded agent subprocess calls. No new cache, dependency or renderer change.

## Measurements

Same Apple M4 / 16 GiB / macOS 27.2 arm64 as the baseline. Python 3.12.13,
Pandoc 3.9.0.2, uv 0.11.3 and Chrome 156.0.8078.80. Identical fixture input hashes
verified by `compare.py`. Twenty samples per operation and regime, 560 after
observations. Seconds, p50 / p95. No normal-operation speed improvement is claimed.

| Paper | Operation | Regime | Before | After |
| --- | --- | --- | ---: | ---: |
| Example | diagram_open | cold | 0.067 / 0.070 | 0.067 / 0.072 |
| Example | diagram_open | warm | 0.064 / 0.080 | 0.063 / 0.078 |
| Example | mcp_check | cold | 0.472 / 0.485 | 0.486 / 0.494 |
| Example | mcp_check | warm | 0.471 / 0.478 | 0.478 / 0.485 |
| Example | preview_start | cold | 1.455 / 1.478 | 1.469 / 1.476 |
| Example | preview_start | warm | 0.418 / 0.667 | 0.522 / 0.664 |
| Example | save_visible | cold | 1.065 / 1.099 | 1.051 / 1.082 |
| Example | save_visible | warm | 1.081 / 1.113 | 1.080 / 1.127 |
| Example | table_open | cold | 0.067 / 0.069 | 0.067 / 0.085 |
| Example | table_open | warm | 0.063 / 0.064 | 0.063 / 0.083 |
| Example | word_full | cold | 0.505 / 0.528 | 0.526 / 0.539 |
| Example | word_full | warm | 0.393 / 0.401 | 0.412 / 0.416 |
| Example | word_unchanged | cold | 0.119 / 0.121 | 0.122 / 0.126 |
| Example | word_unchanged | warm | 0.119 / 0.121 | 0.125 / 0.128 |
| Load | diagram_open | cold | 0.102 / 0.118 | 0.101 / 0.116 |
| Load | diagram_open | warm | 0.077 / 0.095 | 0.063 / 0.080 |
| Load | mcp_check | cold | 1.051 / 1.066 | 1.041 / 1.064 |
| Load | mcp_check | warm | 1.048 / 1.062 | 1.040 / 1.050 |
| Load | preview_start | cold | 1.519 / 1.542 | 1.521 / 1.555 |
| Load | preview_start | warm | 0.434 / 0.698 | 0.433 / 0.701 |
| Load | save_visible | cold | 2.332 / 2.366 | 2.334 / 2.365 |
| Load | save_visible | warm | 1.147 / 1.203 | 1.145 / 1.194 |
| Load | table_open | cold | 0.150 / 0.184 | 0.159 / 0.200 |
| Load | table_open | warm | 0.096 / 0.113 | 0.096 / 0.112 |
| Load | word_full | cold | 0.792 / 0.840 | 0.798 / 0.814 |
| Load | word_full | warm | 0.615 / 0.627 | 0.615 / 0.627 |
| Load | word_unchanged | cold | 0.132 / 0.136 | 0.134 / 0.138 |
| Load | word_unchanged | warm | 0.135 / 0.137 | 0.135 / 0.137 |

The same 25 of 28 p95 budgets pass. Warm source saves on both papers and cold
source saves on the load paper remain above budget. This final run records the
committed product, identical input hashes and all 560 observations. It is not a
claim that timeout handling makes successful commands faster.

An earlier run at `3f77f64`, retained in `.build/measure/deadline-after/`, showed
load unchanged-build p95 values of 0.185 s cold and 0.174 s warm, and a warm full
build p95 of 0.791 s. Those movements did not repeat in the final committed run
above or the adjacent control below. No observations were discarded.
That earlier serial control compared the unchanged base with the candidate immediately
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
zero overhead. The original observations remain in `.build/measure/deadline-after/` in the primary worktree. Complete
commands, samples and comparisons: `.build/measure/deadline-word-control/`.


The final run lives in this worktree. Earlier measurements, the baseline papers
and the before captures live in the primary sibling worktree `t3-a971c6e6`.
Set `BASE` to that checkout. Commands are serial, with no concurrent test/build
workload:

```sh
uv run --quiet --project packages/trazo python scripts/measure/run.py "$BASE/.build/measure/baseline-papers/example" .build/measure/deadline-final-head/example
uv run --quiet --project packages/trazo python scripts/measure/run.py "$BASE/.build/measure/final-papers/carga con acentos á" .build/measure/deadline-final-head/load
uv run --quiet --project packages/trazo python scripts/measure/compare.py "$BASE/.build/measure/baseline-example/results.json" .build/measure/deadline-final-head/example/results.json .build/measure/deadline-final-head/comparison-example.json
uv run --quiet --project packages/trazo python scripts/measure/compare.py "$BASE/.build/measure/final-load/results.json" .build/measure/deadline-final-head/load/results.json .build/measure/deadline-final-head/comparison-load.json
```

Both papers were force-built again from restored inputs. `equivalence.py PAPER OUT
--compare BEFORE` returned `equal: true` for both: all normalized DOCX ZIP members,
diagram exports and renderer hashes match. Captures and real `diagram check --all
--exports --json` results: `.build/measure/deadline-final-head/word-{example,load}/`.
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
- `make typecheck`: run after the optional-pipe correction; exit 2 with 148
  pre-existing errors in 37 files, none in `mcp_calls.py`. No strict module changed.
- `make test-update`: not directly applicable; the push hook runs it.
- Earlier acceptance at `3f77f64`: `.build/acceptance/20261106T212346Z/report.md`,
  18 fresh passes, no failures or skips.
- Final acceptance at `6edd945`: `.build/acceptance/20261106T234107Z/report.md`,
  **12 passed, 0 failed, 6 skipped, 0 not automated**. All 12 passes are fresh.
  The Claude keychain login expired or was about to expire. The six unproven
  scenarios are `n2-slash-commands`, `n2-mcp-connection-time`,
  `n2-resume-no-hook-note`, `n2-no-venv-standin`, `n2-install-claude` and
  `n2-update-claude`. Earlier passes are not evidence of a fresh final-tip pass.
  An agent did not renew the owner's login. Final benchmark, equality and
  acceptance command logs are in `.build/measure/deadline-final-head/`.

## Limits

Standalone CLI commands and the already-running preview watcher remain outside
this deadline. Deliberately detached sessions are not owned by the killed group.
The pipe drain waits at most five seconds; on Windows a daemon pipe reader may
remain while an escaped descendant retains the handle. Windows process-tree
termination has its existing separate 30-second bound. Linux and Windows behavior
was reviewed but not executed locally. Native executable writes and power-loss
durability were not tested. The pre-existing cancel/reaped-PID race is unchanged.

## Type validation follow-up

The combined validation found two new optional-stream diagnostics in the POSIX
cleanup path. Narrowing each pipe before closing it removes both diagnostics
without changing the configured PIPE behavior. `make lint` and the 13 MCP
lifetime tests pass. `make typecheck` still exits 2 with the original 148 errors
in 37 files; none is in `mcp_calls.py`. Logs are in
`.build/measure/deadline-type-narrowing/`. The integrated report retains the
intermediate 150-error run rather than labeling it pre-existing.
