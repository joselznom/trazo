# Workflow validation record

The complete product was checked as local assembly `6ee0bae`. No PR was merged.
The [final report](measurement-final.md) distinguishes measured successes, open
failures and uncovered paths. All commands below were actually run. Command logs
retain unsuccessful attempts; nothing labelled skipped or cached is a fresh pass.

## Locating local evidence

Paths below are relative to the named worktree, all siblings in the task's
worktree folder. `primary` means `t3-a971c6e6`; it retains earlier measurements even
though its current branch is the preview-input fix. Other checkouts are
`t3-mcp-deadline`, `t3-preview-table-errors`, `t3-recover-source-history`,
`t3-preview-status`, `t3-measure-integrated` and `t3-measure-limits`.
This naming avoids committing personal absolute filesystem paths.

| Scope | Actual command | Actual output | Local record |
| --- | --- | --- | --- |
| Baseline | `make lint`, `make test-package`, `make test-template` | All exit 0; 911 tests in 419.934 s, `OK (skipped=1)` | primary, baseline report and `.build/measure/` |
| Recoverable saves | `make lint`, `make test-package`, `make test-template` | All exit 0; 915 tests in 415.564 s, `OK (skipped=1)`; template command 40.664 s | primary, `.build/measure/history-validation-final/` |
| Original deadline fix | `make lint`, `make test-package`, `make test-template` | All exit 0; 914 tests in 422.497 s, `OK (skipped=1)`; template command 40.179 s | primary, `.build/measure/deadline-validation-final/` |
| Final deadline pipe narrowing | `make lint`; lifetime unittest command below | Exit 0; 13 tests in 29.051 s, `OK` | t3-mcp-deadline, `.build/measure/deadline-type-narrowing/` |
| Visible table errors | `make lint`, `make test-package`, `make test-template` | All exit 0; 912 tests in 417.594 s, `OK (skipped=1)`; template command 40.320 s | t3-preview-table-errors, `.build/measure/preview-table-final/` |
| Damaged source history | `make lint`, `make test-package`, `make test-template` | All exit 0; 914 tests in 418.804 s, `OK (skipped=1)`; template command 42.365 s | t3-recover-source-history, `.build/measure/source-history-final/` |
| Preview intermediates | `make lint`, `make test-package`, `make test-template` | All exit 0; 912 tests in 411.672 s, `OK (skipped=1)`; template command 40.701 s | primary, `.build/measure/preview-scratch-final/` |
| Visible polling | `make lint`, `make test-package`, `make test-template` | All exit 0; 912 tests in 445.883 s, `OK (skipped=1)`; template command 52.404 s | t3-preview-status, `.build/measure/status-final/` |
| Combined final | `make lint`, `make test-package`, `make test-template` | All exit 0; 924 tests in 440.292 s, `OK (skipped=1)`; template command 41.066 s | primary, `.build/measure/final-serial/combined-*.log` |
| Combined update | `make test-update` | Exit 0, 16.968 s at `4e2c3b0`, before the optional-pipe narrowing | t3-measure-integrated, `.build/measure/integrated-final/test-update.log` |
| Measurement tools | `uv run --quiet --project packages/trazo ruff check --config packages/trazo/pyproject.toml scripts/measure` | Exit 0 | t3-measure-limits, `.build/measure/writer-limits-validation/measure-lint.log` |
| Measurement oracles | `uv run --quiet --project packages/trazo python -m unittest discover -s scripts/measure/tests` | Two tests pass | t3-measure-limits, `.build/measure/writer-limits-validation/oracles.log` |

The package skip is
`test_plugin_launcher.LauncherTests.test_stand_in_reports_a_pandoc_on_the_windows_path`:
it requires Windows PATH behavior. Template validation includes both example
answers and generated variants. The original deadline suite predates the
optional-pipe correction; the final combined suite includes it. Push hooks repeat
package/template/update on each pushed tip in clean checkouts; their separate
results belong to the corresponding PR evidence, not to the earlier rows.

`make typecheck` is **not green**. The final combined run exits 2 with
`Found 148 errors in 37 files (checked 80 source files)`; the changed strict
`review_history` module has none. The intermediate `4e2c3b0` run had 150 errors
in 38 files, including two new Optional stream errors. Those were corrected in
`6edd945` and the local combined `6ee0bae`. The final output is retained in
primary `.build/measure/final-serial/combined-typecheck.log`; the intermediate
output remains in t3-measure-integrated `.build/measure/integrated-final/typecheck.log`.
Do not describe the intermediate two errors as pre-existing.

The focused commands use the same package environment:

```sh
uv run --quiet --project packages/trazo python -m unittest discover -s packages/trazo/tests -p test_save_history.py
uv run --quiet --project packages/trazo python -m unittest discover -s packages/trazo/tests -p test_mcp_lifetime.py
uv run --quiet --project packages/trazo python -m unittest discover -s packages/trazo/tests -p test_preview_source_history.py
uv run --quiet --project packages/trazo python -m unittest discover -s packages/trazo/tests -p test_preview_scratch.py
uv run --quiet --project packages/trazo python -m unittest discover -s packages/trazo/tests -p test_preview_browser_shell.py
uv run --quiet --project packages/trazo python -m unittest discover -s packages/trazo/tests -p 'test_preview_browser_mcp_app*.py'
```

Failure-first logs, tests and individual before/after tables live in each product
PR's `docs/measurement-*.md`. The polling negative control is in
`.build/measure/status-review-validation/regression-final-before.log` (one test,
two failing views); `regression-final-after.log` passes. The malformed early
fixture is not counted as a regression reproduction.

## Measurement, artifact and fault commands

`BASE` denotes primary; `PAPER` is either `$BASE/.build/measure/baseline-papers/example`
or `$BASE/.build/measure/final-papers/carga con acentos á`. `SEED` is
`$BASE/.build/measure/papers/example`. Each output is a fresh directory. Run from
the selected product checkout, serially:

```sh
uv run --quiet --project packages/trazo python scripts/measure/run.py "$PAPER" "$OUT"
uv run --quiet --project packages/trazo python scripts/measure/compare.py "$BEFORE/results.json" "$OUT/results.json" "$OUT/comparison.json"
uv run --quiet --project packages/trazo python scripts/measure/equivalence.py "$PAPER" "$CAPTURE" --compare "$BEFORE_CAPTURE"
uv run --quiet --project packages/trazo python scripts/measure/run.py "$PAPER" "$PROFILE" --smoke --samples 1 --profile
uv run --quiet --project packages/trazo python scripts/measure/analyze_profiles.py "$PROFILE"
uv run --quiet --project packages/trazo python scripts/measure/idle.py "$PAPER" "$IDLE_JSON" --checkout "$CHECKOUT"
uv run --quiet --project packages/trazo python scripts/measure/inventory.py "$INVENTORY"
uv run --quiet --project packages/trazo python scripts/measure/faults.py "$SEED" "$FAULTS"
TRAZO_MCP_TIMEOUT=1 uv run --quiet --project packages/trazo python scripts/measure/scenarios.py "$SEED" "$HANG" --scenario pandoc_hang
uv run --quiet --project packages/trazo python scripts/measure/scenarios.py "$SEED" "$SCENARIOS" --scenario pandoc_missing --scenario pandoc_invalid --scenario port_busy --scenario edit_during_build --scenario readonly_directory --scenario corrupt_history --scenario corrupt_table --scenario concurrent_writers --scenario concurrent_atomic_writers --scenario fuzz
```

The substituted argv and environment are preserved in `commands.json`,
`checks.json` and `profile-commands.json`; these examples name the same commands
with portable path variables. `equivalence.py` follows a forced build, and runs
the real `diagram check --all --exports --json`. It compares both documents in
each paper, all normalized ZIP members and every export. Raw binaries remain
under `word-{example,load}/` in each run folder. The before captures are primary
`.build/measure/word-before-{example,load}/`.

Final measurements are in t3-measure-integrated
`.build/measure/integrated-measured/`. Separate final cProfiles, Python phase
spans and Chrome trace JSONs are in `.build/measure/integrated-final/profile-example/`
and `profile-load/`, under `profiles/` and `traces/`. The corresponding baseline
load profile is primary `.build/measure/profile-load/`. These single instrumented
observations explain phases; they do not substitute for the 20-sample benchmarks.

The full 197-injection campaign is
`t3-measure-integrated/.build/measure/integrated-final/write-faults/report.md`
and `report.json`: exit 0, 85 reached sites, all triggers hit, zero affected
violations and all normal retries recovered. The environmental command exits 1:
`concurrent_writers` and `concurrent_atomic_writers` retain explicit violations.
The hang command exits 0 with the real product deadline, old DOCX preserved,
MCP still answering and a successful retry. Persistent faults must first be
removed; recovery does not mean a full disk can accept a write.

## Acceptance

Every row is from an actual `make acceptance-auto AUTO=push` report. The archived
summaries include every scenario name and its status. Original complete files
remain at the worktree and timestamp path recorded below each summary.

| Run | Commit | Actual summary | Versioned report summary |
| --- | --- | --- | --- |
| baseline | `ee13ca0a8c83 (uncommitted changes)` | 18 passed, 0 failed, 0 skipped, 0 not automated | [summary](measurements/2026-11-07/acceptance-baseline.md) |
| history | `6e92b9e01305 (uncommitted changes)` | 18 passed, 0 failed, 0 skipped, 0 not automated | [summary](measurements/2026-11-07/acceptance-history.md) |
| deadline-original | `3f77f64e3807` | 18 passed, 0 failed, 0 skipped, 0 not automated | [summary](measurements/2026-11-07/acceptance-deadline-original.md) |
| deadline-final | `6edd9451ec25` | 12 passed, 0 failed, 6 skipped, 0 not automated | [summary](measurements/2026-11-07/acceptance-deadline-final.md) |
| table-errors | `e0dab4a2b6ca` | 18 passed, 0 failed, 0 skipped, 0 not automated | [summary](measurements/2026-11-07/acceptance-table-errors.md) |
| source-history | `09e4f45c8197` | 18 passed, 0 failed, 0 skipped, 0 not automated | [summary](measurements/2026-11-07/acceptance-source-history.md) |
| preview-inputs | `12dd3a685f5b` | 18 passed, 0 failed, 0 skipped, 0 not automated | [summary](measurements/2026-11-07/acceptance-preview-inputs.md) |
| polling | `b6005a5b9c13` | 12 passed, 0 failed, 6 skipped, 0 not automated | [summary](measurements/2026-11-07/acceptance-polling.md) |
| combined-original | `4e2c3b004674` | 18 passed, 0 failed, 0 skipped, 0 not automated | [summary](measurements/2026-11-07/acceptance-combined-original.md) |
| combined-final | `6ee0bae9a8a6` | 12 passed, 0 failed, 6 skipped, 0 not automated | [summary](measurements/2026-11-07/acceptance-combined-final.md) |

The final deadline, polling and combined runs have **12 fresh passed, 0 failed,
6 skipped, 0 not automated**. None of those 12 passes is cached. The skips are
`n2-slash-commands`, `n2-mcp-connection-time`, `n2-resume-no-hook-note`,
`n2-no-venv-standin`, `n2-install-claude`, `n2-update-claude`: the owner's keychain
login expired or was about to expire. No agent renewed it. Earlier fresh passes
are historical evidence and do not establish a final-tip Claude pass.

The earlier push hooks for PRs 33 through 37 reused **18 cached** passes each;
those are not new acceptance runs. PRs 35 through 37 retain their complete hook
output in primary `.build/measure/robustness-push/hook-{4,5,6}.log`. Their temporary
report checkouts were removed by the hook, so the durable fresh reports above
are the report paths to inspect. All three push package/template/update checks
passed. Final pushes are recorded separately in primary `.build/measure/final-push/`.

Not run locally: Linux (`make test-linux`), Windows, manual desktop-app release
checks, native executable write interruption, setup/update fault injection,
physical power loss and every browser editing mode. `make test-acceptance` is
not applicable because `scripts/acceptance/` did not change. Typecheck and update
were not required by the low-tier asset-only polling diff; the combined run and
push hook evidence are reported explicitly instead. Release checks requiring
zero skips are not satisfied, and no release or merge was performed.
