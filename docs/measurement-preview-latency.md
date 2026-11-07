# Measured preview refresh latency

Measured committed product: `b6005a5`; baseline product: `ee13ca0`, measurement
base: `2cc61c0`. Risk: **medium** by judgment for shared browser polling and its
regression coverage (the asset paths themselves are low tier). Revert this PR
to restore the previous polling cadence. No content cache, backend, watcher,
template, dependency or diagram renderer change.

## Behavior and cost

A visible standalone preview checks document versions every 250 ms, while the
full workspace scan keeps its approximately one-second cadence. Only one request
is in flight. Hidden pages use five seconds. Editors and MCP-hosted views retain
the old cadence. Documents named `comments`, `docx`, `synced` or `server` use the
full workspace response, avoiding collisions with the status response's metadata.

This changes detection latency, not rendering work. Cached workspace metadata
is refreshed every second; document versions come from each status response.
No generated content or export is newly cached. Existing unsaved-text and active
writing guards still prevent replacement of the author's work.

The phase 1 proposal of no increase in idle request load is **not met** for
standalone previews. About three cheap status requests per second are added.
The measured CPU cost is reported below rather than treated as free. The MCP
host transport keeps its previous request rate; host energy was not measured.

## Measurements on the same machine

Apple M4, 16 GiB, macOS 27.2 arm64; Python 3.12.13, Pandoc 3.9.0.2,
uv 0.11.3, Chrome 156.0.8078.80. Twenty observations per operation and regime,
560 in this final run. Identical machine/tool versions and fixture hashes were
checked by `compare.py`. Application-cold is defined in the harness README;
OS caches were not flushed. Runs were serial with no concurrent tests/builds.
Seconds:

| Paper | Operation | Regime | Before p50 / p95 | After p50 / p95 |
| --- | --- | --- | ---: | ---: |
| Example | diagram_open | cold | 0.067 / 0.070 | 0.067 / 0.083 |
| Example | diagram_open | warm | 0.064 / 0.080 | 0.063 / 0.079 |
| Example | mcp_check | cold | 0.472 / 0.485 | 0.475 / 0.484 |
| Example | mcp_check | warm | 0.471 / 0.478 | 0.474 / 0.484 |
| Example | preview_start | cold | 1.455 / 1.478 | 1.469 / 1.487 |
| Example | preview_start | warm | 0.418 / 0.667 | 0.516 / 0.665 |
| Example | save_visible | cold | 1.065 / 1.099 | 1.117 / 1.135 |
| Example | save_visible | warm | 1.081 / 1.113 | 0.580 / 0.844 |
| Example | table_open | cold | 0.067 / 0.069 | 0.068 / 0.071 |
| Example | table_open | warm | 0.063 / 0.064 | 0.063 / 0.077 |
| Example | word_full | cold | 0.505 / 0.528 | 0.522 / 0.532 |
| Example | word_full | warm | 0.393 / 0.401 | 0.405 / 0.420 |
| Example | word_unchanged | cold | 0.119 / 0.121 | 0.123 / 0.125 |
| Example | word_unchanged | warm | 0.119 / 0.121 | 0.123 / 0.125 |
| Load | diagram_open | cold | 0.102 / 0.118 | 0.102 / 0.118 |
| Load | diagram_open | warm | 0.077 / 0.095 | 0.070 / 0.082 |
| Load | mcp_check | cold | 1.051 / 1.066 | 1.042 / 1.062 |
| Load | mcp_check | warm | 1.048 / 1.062 | 1.039 / 1.063 |
| Load | preview_start | cold | 1.519 / 1.542 | 1.531 / 1.553 |
| Load | preview_start | warm | 0.434 / 0.698 | 0.439 / 0.696 |
| Load | save_visible | cold | 2.332 / 2.366 | 1.866 / 1.883 |
| Load | save_visible | warm | 1.147 / 1.203 | 0.880 / 0.930 |
| Load | table_open | cold | 0.150 / 0.184 | 0.150 / 0.184 |
| Load | table_open | warm | 0.096 / 0.113 | 0.096 / 0.112 |
| Load | word_full | cold | 0.792 / 0.840 | 0.791 / 0.851 |
| Load | word_full | warm | 0.615 / 0.627 | 0.616 / 0.626 |
| Load | word_unchanged | cold | 0.132 / 0.136 | 0.134 / 0.137 |
| Load | word_unchanged | warm | 0.135 / 0.137 | 0.135 / 0.137 |

| Paper / regime | p50 reduction | p95 reduction | p95 delta 95% bootstrap interval (s) | Meets both noise and 5% gates? |
| --- | ---: | ---: | --- | --- |
| example / cold | -4.9% | -3.3% | -0.062 to 1.052 | p50: False; p95: False |
| example / warm | 46.4% | 24.1% | 0.249 to 0.285 | p50: True; p95: True |
| load / cold | 20.0% | 20.4% | 0.466 to 0.501 | p50: True; p95: True |
| load / warm | 23.3% | 22.7% | -0.015 to 0.304 | p50: True; p95: False |

The bootstrap gate uses 2,000 resamples, fixed seed 20261106, plus a 5% practical
threshold. Twenty observations give a sparse empirical p95, not a service-level
guarantee. Warm example saves and cold load saves pass both improvement gates.
Warm load saves pass the median gate, but the p95 interval crosses zero, so this
isolated correction does not establish a p95 improvement for that cell. Cold
example saves do not improve. Unrelated timing movements are not claimed as code
improvements.

| Warm idle metric | Before p50 / p95 | After p50 / p95 |
| --- | ---: | ---: |
| workspace_requests_per_second | 0.998 / 0.999 | 0.999 / 1.000 |
| status_requests_per_second | 0.000 / 0.000 | 2.996 / 2.999 |
| server_cpu_percent_one_core | 3.246 / 4.991 | 3.245 / 5.491 |
| renderer_task_percent_one_core | 0.137 / 0.429 | 0.580 / 1.402 |

Idle measurements are twenty two-second windows per version, after settling.
Server CPU excludes descendants; renderer TaskDuration covers the page's main
thread, not total browser CPU or system energy. The baseline idle product is
`527db73` (measurement-only changes); the after run is `b6005a5`.

Earlier exploratory after runs named `2cc61c0` because they used an uncommitted
tree. They are excluded from final proof. An initial idle-before invocation used
the wrong checkout and failed; its output remains under `status-investigation`.
The final benchmark and idle runs name committed revisions. Final source tree
hashes and individual observations are also archived by the final report.

## Regression, artifacts and actual checks

The public Chrome regression with a document named `comments` fails on `2301bff`
(one test, two failing views) and passes on `b6005a5`. It checks no unsolicited
navigation or repeated HTML fetches after settling, then verifies that an actual
edit is still shown. An early malformed fixture and prefetch-settlement attempts
are retained but not counted as negative-control evidence.

Commands and output: `.build/measure/status-final/checks.json` and companion logs.

- `uv run --quiet --project packages/trazo python -m unittest discover -s packages/trazo/tests -p test_preview_browser_shell.py`: pass, 16.498 s.
- `uv run --quiet --project packages/trazo python -m unittest discover -s packages/trazo/tests -p 'test_preview_browser_mcp_app*.py'`: pass, 20.193 s.
- `make lint`: exit 0, 0.132 s.
- `make test-package`: 912 tests in 445.883 s, `OK (skipped=1)`.
- `make test-template`: exit 0, 52.404 s, both example variants.
- `make typecheck` and `make test-update`: not required by the changed paths;
  the push hook runs update anyway. `make test-acceptance`: not applicable,
  the acceptance harness is unchanged.
- Linux, Windows and manual desktop checks: not run locally. The one package
  skip requires Windows PATH behavior.

Both papers were force-built from restored inputs. `equivalence.py` reports
`equal: true` for both: all normalized DOCX members, exported images and renderer
hashes match. Only ZIP container metadata and core created/modified timestamps
are normalized. Both real diagram export checks pass. Renderer SHA256 remains
`41dd04c3e8a95b79c8a404fb97a51f0b5a25e902f0b100adde44ee06023e0599`.
This polling change introduces no storage path; the combined fault campaign
checks the integrated behavior separately.

Final acceptance report: `.build/acceptance/20261106T235000Z/report.md`.
Result: **12 passed, 0 failed, 6 skipped, 0 not automated**. The table distinguishes cached results and skips:

| Scenario | Status | Seconds | Note |
| --- | --- | --- | --- |
| n1-sandbox-chain | passed | 5.5 |  |
| n1-port-busy | passed | 4.2 |  |
| n1-force-quit-build | passed | 4.3 |  |
| n1-old-install-update | passed | 5.1 |  |
| n1-setup-status-ok | passed | 3.6 |  |
| n1-install-newer-than-plugin | passed | 4.8 |  |
| n1-two-sessions-share-preview | passed | 5.2 |  |
| n2-slash-commands | skipped | 3.3 | the Claude login in the keychain has expired or is about to: use `claude` once to renew it |
| n2-mcp-connection-time | skipped | 3.4 | the Claude login in the keychain has expired or is about to: use `claude` once to renew it |
| n2-resume-no-hook-note | skipped | 0.4 | the Claude login in the keychain has expired or is about to: use `claude` once to renew it |
| n2-no-venv-standin | skipped | 0.2 | the Claude login in the keychain has expired or is about to: use `claude` once to renew it |
| n2-install-claude | skipped | 0.2 | the Claude login in the keychain has expired or is about to: use `claude` once to renew it |
| n2-install-codex | passed | 1.1 |  |
| n2-update-claude | skipped | 0.2 | the Claude login in the keychain has expired or is about to: use `claude` once to renew it |
| n2-update-codex | passed | 1.8 |  |
| n2-codex-hook-retrust | passed | 7.8 |  |
| n2-codex-tools-by-state | passed | 3.9 |  |
| n2-codex-hook-note | passed | 4.0 |  |


No agent renewed the owner's Claude login. A skipped host scenario is not proof
of successful acceptance. Earlier fresh passes are retained separately.

## Replay and retained evidence

Use `BASE` for the measurement checkout holding the generated example and load
papers; use a new `OUT` folder in this checkout. All commands are serial:

```sh
uv run --quiet --project packages/trazo python scripts/measure/run.py "$BASE/.build/measure/baseline-papers/example" "$OUT/example"
uv run --quiet --project packages/trazo python scripts/measure/run.py "$BASE/.build/measure/final-papers/carga con acentos á" "$OUT/load"
uv run --quiet --project packages/trazo python scripts/measure/compare.py "$BASE/.build/measure/baseline-example/results.json" "$OUT/example/results.json" "$OUT/comparison-example.json"
uv run --quiet --project packages/trazo python scripts/measure/compare.py "$BASE/.build/measure/final-load/results.json" "$OUT/load/results.json" "$OUT/comparison-load.json"
uv run --quiet --project packages/trazo python scripts/measure/idle.py PAPER idle.json --checkout CHECKOUT
```

The complete final argv, exit codes, timing observations, idle windows and Word
captures are under `.build/measure/status-committed/`. The final measurement
branch archives the numeric observations under `docs/measurements/2026-11-07/`.
