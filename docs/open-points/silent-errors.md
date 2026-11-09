# Silent errors: watcher, drift check, Chrome cleanup

Evidence: `.build/open-points/errors-waits/` in the `t3-investigate-open-workflows` worktree (local, git-ignored). Prototype worktrees (`wt/`, `wt-*`) were not retained; their changes are the `*.diff` files there, all against b775566 and never integrated. Back to the [decision summary](../open-points.md).

Head b775566, macOS arm64 only. `SCR` below is
`.build/open-points/errors-waits`.
Paths in code are relative to `packages/trazo/src/trazo/`. Reproductions ran
against the detached worktree `SCR/wt` (b775566 product, then the prototype).
Prototype (never integrated): `SCR/prototype.diff` (preview_watch.py,
preview_edit_review.py, browser.py, diagram_editor.py, new processes.py).
Test sketches: `SCR/tests/`. Logs: `SCR/repro/*.log`, `SCR/tests/*.log`.

Common command prefix (abbreviated `RUN` below):
`cd SCR/wt && PYTHONPATH=SCR/wt/packages/trazo/tests[:SCR/tests] uv run --quiet --project SCR/wt/packages/trazo python`

## Summary of decisions

| Point | Status | Class | PR | Tier |
| --- | --- | --- | --- | --- |
| A1 outer watcher handler (+ thread death at start) | still open, reproduced | user must know | PR-1 | medium |
| A2 drift check `check_outputs` | still open, reproduced (decode path) | partly tolerable | PR-2 | medium |
| A3 `SharedChrome.drop` | still open, reproduced with injection | tolerable cleanup, but leaks | PR-3 | high (browser.py) |
| B0 inventory | complete, below | | | |
| B1 shared bounded runner | design + POSIX prototype | | PR-4 | high (mcp_calls.py) |
| B2 standalone CLI build | still open, reproduced (Pandoc, metadata, uv) | | PR-5 | high |
| B3 watcher commands, page Pandoc, lock waits, orphans | still open, reproduced | | PR-6 | medium |
| B4 `CommandJob` | still open, reproduced | | PR-7 | medium |
| B5 Windows trees and reader threads | documented limit, code reading | owner decision | none | |

Order: PR-1, PR-3 and PR-4 are independent; PR-2 after PR-1 (its narrowed
except relies on PR-1's reporting); PR-5, PR-6, PR-7 after PR-4.

---

## A1. Outer watcher handler (`preview_watch.watch`, except at line 197) and `build_and_watch`

**Status.** Still open. Reproduced with real failures, no mocks.

**Reproduction.**
- R1 `RUN SCR/repro/watcher.py r1_tables_stderr_not_utf8`: a custom tables
  command writes one non-UTF-8 byte to stderr (exit 0). `subprocess.run(...,
  text=True)` at line 48 raises `UnicodeDecodeError`, which is not `OSError`.
  Result: `synced` advances, `ok: True`, `error: ''`, version unchanged, a later
  prose edit never appears, a second `preview_start` returns no note. Only
  `.build/preview.log` has `[preview] watcher error: UnicodeDecodeError(...)`
  (printed on every retry). Log: `SCR/repro/r1_tables_stderr_not_utf8.log`.
- R2 `RUN SCR/repro/watcher.py r2_unreadable_section_at_start`: a section with
  mode 000 at start. `preview.build` at line 119 is outside any handler
  (`render` catches `FileNotFoundError`, not `PermissionError`), so the
  `build_and_watch` thread dies. Browser: stub "building..." forever; status
  `ok: False, error: '', version: 0, synced: 0`. After `chmod 644` and an edit,
  nothing rebuilds. The second `preview_start` waits 30 s and says "had not yet
  shown the regenerated values" (misleading). Traceback only in the log.
- Test sketch `SCR/tests/test_watcher_failures.py` (2 tests, also covers an
  unopenable build lock file, which raises in `regenerate_tables` outside its
  try): **both FAIL on b775566** (`SCR/tests/test_watcher_failures.b775566.log`).

**Root cause (high confidence, reproduced).** (1) The pass advances
`previous` (line 154) and drains `WATCHER["rebuild"]` (line 148) before the
work that can fail, so a failed pass consumes its changes; the next quiet poll
then sets `synced`, so browser and `preview_start` both claim freshness.
(2) The handler only prints. (3) `build_and_watch` (lines 114-122) has no
handler: `preview.build`, `numbered()` or `check_outputs()` raising ends the
watcher for the life of the server.

**Recommended solution (prototyped).** In `preview_watch.py` only:
- `report_failure(error)`: the PR #35 channel, every page's `STATE`
  `ok=False, error="preview watcher: <Type>: <message>"`, last good page kept.
  This already reaches the browser banner, the stub page, `/__status` and
  `preview_start` (`preview_inputs.with_failures`); no new channel.
- On an exception: restore `previous = base` and put `requested` back, report,
  set `synced = checked` (the documented meaning: the status describes these
  sources, its error says the page is old), print the log line only when the
  message differs from the last one.
- Retry: at once when the snapshot differs from the one that failed, else
  after a backoff of 1, 2, 4 ... 30 s. A later table failure replaces the
  watcher error (its own recovery rebuilds every page). On the first complete
  pass every page is rebuilt (`recover`), which clears the error, and the log
  says `watcher recovered`.
- `build_and_watch`: first builds through `build_all` (per-page errors);
  `numbered()`/`check_outputs()` failures are reported and the first `watch`
  pass rebuilds everything (`previous = {}`).
Prototype result: the two sketches and the existing preview suites pass
(`SCR/tests/prototype-a-final.log`: 37 tests OK). R1/R2 after: R1 status
`ok: False`, error names the `UnicodeDecodeError`, the `preview_start` note
carries it, the log has one line; R2 status shows the `PermissionError`, and the
page rebuilds within 10 s after the fix (`SCR/repro/*.prototype.log`).
R1's own trigger (strict decoding) is removed separately in PR-6.

**Rejected alternatives.** Printing to stderr only (today). A new `watcher`
field in `/__status`: the per-page error already drives every consumer. Not
advancing `synced` on failure: `preview_start` would wait 30 s and give the
wrong note (R2 shows this). Retrying every poll: reruns Pandoc every 0.5 s.

**Checkable invariants.** After any exception in a pass: every page `ok` is
false with a non-empty error before `synced` passes the change time; the
watcher thread stays alive; a change made during the failure is visible after
recovery; one log line per distinct error.

**Portability and risks.** Pure Python; same on Linux/Windows. Risk: a
persistent unexpected failure now marks pages failed (intended). Backoff
delays recovery of an unchanged environment fix by up to 30 s (a source edit
retries at once).

**Test that fails before the fix.** `SCR/tests/test_watcher_failures.py`
(shown failing).

**Verification needed.** `make lint`, `make test-package`, `make test-template`,
acceptance per catalog (preview areas).

**Measured impact.** Normal path: one tuple comparison per poll; not
benchmarked (shared machine rule).

**Proposed PR.** PR-1 `fix: report watcher failures in the preview status and retry`.
Files `preview_watch.py` (+ test file). Tier **medium** (not in the high list).

**Not verified.** Linux/Windows; acceptance; full suite.

---

## A2. Drift check (`preview_edit_review.check_outputs`, except at line 247)

**Status.** Still open. The silent omission at line 247 is mostly covered by
louder errors; the real silent failure is one line later.

**Reproduction.** `RUN SCR/repro/drift.py` (project without its own tables
stage, preview running). Control: a hand edit of `tables/output/md/results.md`
is listed in `/__workspace` `outputs` (banner "Edited by hand?").
- Unreadable spec (mode 000): the drift check skips it, but the page build
  fails visibly (`hand_table_labels` reads every spec: `ok: False`,
  `PermissionError`) and `/__workspace` returns HTTP 500 (`table_editor.status`),
  so the browser shows "Offline". Not silent.
- Output re-saved as Latin-1: `path.read_text` at line 250, outside the try,
  raises `UnicodeDecodeError` into A1's handler: `ok: True`, `outputs: []`, log
  only. The user is not warned that the next render overwrites the edit.
- Sketch `SCR/tests/test_drift_check.py`: **FAILS on b775566**, passes on the
  prototype (`SCR/tests/test_drift_check.*.log`).

**Root cause (high).** Decoding the output is outside the guarded region; the
broad except hides programming errors. `TableError` never escapes
`table.build` (it becomes `table.errors`; invalid specs give `markdown None`
and are reported by `render_spec_tables`, PR #35).

**Recommended solution (prototyped).** Catch only `OSError` from
`table.build` (unreadable spec: reported by the page build; unreadable data:
reported by the next table render, which is what replaces the output); treat
an undecodable output as drift (it is not what the spec makes); let anything
else reach PR-1's boundary.

**Classification.** Tolerable omission for unreadable inputs (other channels
report them); an undecodable hand edit is a must-know warning.

**Rejected.** Reporting drift-check failures in the page `error`: a later
successful build clears it while the cause stays, and it labels a fresh page
"showing last good version". A new `unchecked` list in `/__workspace`: no
need found once OSError cases are covered elsewhere.

**Invariants.** An output whose bytes differ from the rendered Markdown is
listed in `outputs` whatever its encoding; `check_outputs` raises only for
unexpected errors, which PR-1 reports.

**Portability.** Same everywhere; CRLF handling unchanged (`read_text`).

**Limit.** Unreadable data with no source change is not reported until the
next table render.

**PR.** PR-2 `fix: note table outputs the drift check cannot decode`,
`preview_edit_review.py`, **medium**, after PR-1.

**Not verified.** Linux/Windows; full suite.

---

## A3. Chrome cleanup (`diagram_editor.SharedChrome.drop`, except at line 335)

**Status.** Still open; reproduced only with an injected failure.

**Reproduction.**
- `RUN SCR/repro/chrome_drop.py` with real Chrome 156: `Browser.close` raising
  a `ValueError` (what `json.loads` in `Chrome.call` raises on a malformed
  reply) skips `proc.wait`/`kill`; `rmtree(profile)` runs while Chrome is
  alive. Observed: Chrome exited on pipe EOF but stayed a zombie, and the
  profile directory was recreated and leaked (1.7 MB); `drop` returned
  silently. Log `SCR/repro/chrome_drop.log` (my leaked profile was removed).
- Sketch `SCR/tests/test_chrome_cleanup.py` uses the existing test peer
  (`test_browser.PEER`) as a browser that answers `Browser.close` with non-JSON
  and does not exit on EOF: **FAILS on b775566** ("Chrome kept running after
  drop()"), passes on the prototype with `test_browser` (6 tests OK).

**Root cause (high).** `Chrome.close` reaps only after a successful or
`BrowserError` close call; any other exception skips the reap but still runs
the profile removal. `drop` hides it.

**User impact and class.** No visible failure: the next check starts a fresh
Chrome. Leak of a profile (about 2-3 MB) and, for a browser that does not exit
on EOF, a running process. Tolerable cleanup: log it, never put it in the
status (noise; the initiating operation already reported its own result).

**Recommended solution (prototyped).** `browser.Chrome.close`: an inner
`finally` that always waits/kills/reaps before the profile is removed; the
exception still propagates. `drop`: print one line
`[diagrams] closing headless Chrome failed: <repr>`.

**Rejected.** Fixing only `drop` (kill there): leaves the same leak for
diagram export and preview image export, which also use `Chrome.close`.

**Invariants.** After `close()` returns or raises, `proc.returncode` is set
and the profile is gone.

**Portability.** Windows: `proc.kill` is TerminateProcess; Chrome children are
not in a job, so renderer children could survive (not verified).

**PR.** PR-3 `fix: always reap Chrome before removing its profile`,
`browser.py` (high: setup list) + `diagram_editor.py` (low). Tier **high**.
Alternative for the owner: the `drop`-only half is low but incomplete.

**Not verified.** Which real faults produce a non-BrowserError at close;
`$TMPDIR` holds about 47 older `trazo-chrome-*` profiles from Nov 6-7 whose
cause was not determined (SIGKILLed processes in the fault campaigns are
plausible).

---

## Commands run (all from `SCR/wt` unless noted)

| Command | Result |
| --- | --- |
| `git worktree add --detach SCR/wt b775566` (main checkout) | ok |
| `scripts/measure/fixture.py SCR/papers/example` (main checkout) | exit 0 |
| `scripts/measure/inventory.py SCR/inventory` (main checkout) | exit 0, 19 broad catches |
| `SCR/tools/waits.py packages/trazo/src/trazo` | 108 rows |
| `SCR/repro/chrome_drop.py` | reproduced leak |
| `SCR/repro/watcher.py r1 / r2 / r3` (base) | all reproduced |
| `SCR/repro/cli_hang.py SCR pandoc-docx / pandoc-meta / uv 15` | all hung; BUSY 75; SIGINT exit 130 |
| `SCR/repro/descendant.py` | three defects reproduced |
| `SCR/repro/drift.py` | decode path silent; unreadable spec loud (500) |
| `SCR/repro/preview_pandoc_hang.py SCR` | stuck page, orphan Pandoc |
| `SCR/repro/run_timeout_baseline.py` | leader-only kill, holder wait |
| `unittest test_watcher_failures` (base) | FAIL 2/2 (intended) |
| `unittest test_drift_check` (base / prototype) | FAIL / OK |
| `test_chrome_cleanup` (base / prototype with test_browser) | FAIL / 6 OK |
| `unittest test_processes` (prototype) | 5 OK |
| prototype focused: watcher, drift, preview_tables, preview, preview_edit_review, mcp_preview_inputs | 37 OK |
| prototype all scratch tests + test_browser + test_diagram_routes + the above | 62 OK |
| `ruff check` on product tree and `SCR/tests` (prototype) | clean |
| `watcher.py r1 / r2` (prototype) | error visible, recovery |

Skipped: `make test-package`, `make test-template`, `make test-update`,
`make typecheck`, acceptance, benchmarks (COMMON rules), Linux and Windows
runs. Leftover test processes were checked and killed; one leaked Chrome
profile from my injection was removed.
