# Intermittent Windows browser-test timeouts

Evidence: `.build/open-points/windows/` in the `t3-investigate-open-workflows` worktree (local, git-ignored). Prototype worktrees (`wt/`, `wt-*`) were not retained; their changes are the `*.diff` files there, all against b775566 and never integrated. Back to the [decision summary](../open-points.md).

Scope: `windows.yml` job `test-package` (windows-2025, `python -m unittest discover -s tests`,
one process, Chrome per test class, one `LocalServer` on port 0 and one browser context per
test). Code at b775566. Platform available here: macOS arm64 only. Nothing below was run on
Windows; Windows facts come from GitHub logs fetched read-only.

Evidence files (this folder): `runs/` (62 Windows `test-package` job logs since 2026-11-03,
60 completed: 42 green, 18 red; `jobs-table.txt`), `ci-runs/` (102 Linux/macOS jobs),
`failures-classified.txt`, `tools/` (log segmentation, Resource Timing probe), `probe/`
(per-asset fault injection) and `experiment.diff` (workflow, tracing, loop).

How the two waited-for elements appear (code reading):
- `.pv-bs-label` (open()): preview.js registers `poll` with `Trazo.watch(poll, {preview: true})`
  at its last line; app.js then fetches `/__workspace` (once a second) and `/__status` (every
  250 ms in between, fe4706c) and calls `poll`, which calls `showBuild` (preview-build.js) on
  success and on failure ("Offline"). The label is never cleared once set. No watcher,
  render or subprocess is involved in these tests.
- `.pv-write-bar` (PR39 attempt 1): `sendBlock` posts `/__block` and shows the bar on any
  outcome, a network error included. A timeout means the POST did not resolve in 15 s or the
  Enter was not handled. `/__block` can run Pandoc (`preview_block_save` uses `pandoc_run`).
  That attempt ran before the diagnostics commit, so it printed nothing and stays undecided.

## 8.1 The status label never fills (the open() timeout), on normal-speed runners

### Status
Still open. Not reproduced on Windows (not available); the two observed page signatures were
reproduced on macOS by fault injection, which identifies a mechanism but not the Windows trigger.

### Reproduction
- Frequency (reproduced from logs): 9 `.pv-bs-label` timeouts in 7 of 60 completed Windows jobs
  since 2026-11-03 (2d280f8 a2 x2, 513358c, 734c4eb, fdc85c8 a1, 9ffef75 a1, 581e65e a1,
  4c147a5 a2 x2). The same tests on ci.yml: 0 in 51 Linux and 51 macOS jobs (browser tests ran
  there: 9 skips per run). Windows-specific by evidence. 9 of 60 Windows jobs failed and then
  passed on a retry of the same commit, plus the two 4c147a5 attempts that passed only after a
  diagnostics-only commit (11 jobs, 18 %); not all from this mechanism.
- Not late in the suite and not after one test: failures sit at test indexes 437 to 613 of the
  browser block 437 to 615 (the first browser test of the suite is among them) and their
  predecessors all differ.
- But early in a Chrome's life: 4 of the 9 are the first test of their class and 6 of 9 are in
  its first two (Chrome starts per class). Base rates over the 180 browser tests: 16 % and 31 %;
  binomial tail P = 0.043 and 0.028. A hint, not proof (I looked at several patterns).
- Not a generally slow runner on these jobs: after removing 15 s per failed wait, the failing
  jobs took 600, 589, 570, 564 s against a same-era green median of 573 s (range 451 to 595).
  PR39 segment of tests 474 to 634: 276.7 s and 261.5 s failing, 257.2 s green; minus the
  timeouts 261.7 and 231.5 s. The 603/599 vs 593 s totals are explained by the 15/30 s of waits.
- Not caused by PR38 polling (fe4706c): label timeouts occur from 2026-11-04 on, and the PR36
  failing head 581e65e does not contain fe4706c (`git merge-base --is-ancestor`).
- Page state printed by the four failures that had diagnostics (from the logs):
  - A (581e65e, 4c147a5 a2 Underline, fdc85c8 a1): `ready: complete`, shell present, all 27
    scripts and the favicon in Resource Timing, zero API requests finished, zero server handler
    threads alive.
  - B (4c147a5 a2 Paragraph `enter_at_the_start`): `/__review`, `/__edits`, `/__submission`,
    `/__workspace`, then `/supplementary.html`, `/__catalog`, `/__review`, then 16 x
    (`/__workspace` + 3 `/__status`) for the full 15 s; label still empty; 0 handler threads.
- Fault injection on macOS (reproduced; `probe/signatures.jsonl`, command below): answering one
  viewer asset with a reset connection gives
  - exactly signature B when the asset is `preview-build.js` (label empty, startup fetches,
    loadAll, polls at 1 workspace + 3 status per second);
  - exactly signature A when it is `app.js`, `preview-layout.js`, `preview-navigation.js`,
    `preview-comment-list.js`, `preview-comment-highlights.js`, `preview-source.js`,
    `preview-edits.js` or `preview.js`;
  - a filled label ("Build failed"/"Offline") for the other scripts, and no shell for
    `preview-page.js`, `preview-shell.js`, `preview-wordmark.js`, `preview-model.js`.
  - Command: `PYTHONPATH=packages/trazo/tests:.build/open-points/windows/probe uv run --quiet --project
    packages/trazo python -m unittest test_script_failure_signatures` (27 tests, OK).
- `tools/rt_probe.py` (Chrome 156, macOS): a reset, an empty reply, a truncated body and a 404
  script are all listed in `performance.getEntriesByType('resource')` (status 0 or 404, body 0)
  and the scripts do not run. So "all 27 scripts listed" in the CI dumps does not show they ran.

### Root cause and confidence
- Mechanism, medium-high confidence: one viewer script did not execute (its load failed or its
  top-level code threw), and the viewer ran half-started. For B there is no other path in the
  code: polls flowing means preview.js reached `App.watch`; the `/__catalog` and `/__review`
  after the first `/__workspace` mean `poll` ran `resolveStatus()` (loadAll awaits it); every
  later branch of `poll` calls `showBuild`, in `try` or in `catch`, unless `showBuild` itself
  throws, which happens when preview-build.js's bindings do not exist; the `polling` flag then
  stays true and every later poll returns at once.
- For A, medium: no API request reached the server while the favicon, requested after `load`,
  did; a viewer that stopped before preview.js line 117 (`loadComments`, `loadEdits`,
  `loadSubmission`, then `App.watch`) explains that, a network stall that spares the favicon
  does not.
- Trigger on Windows, unknown. Ranked candidates, none observed yet:
  1. One asset request fails at the connection level (status 0): the request never reaches the
     Python server, or the server aborts it. Each page opens about 50 HTTP/1.0 connections plus
     4 per second of polling; LocalServer's `handle_error` drops every `ConnectionError`
     silently, so a server-side abort would leave no trace today. The early-in-Chrome hint fits
     a freshly started headless Chrome (new profile folder, network service) on its first
     pages. If that is the trigger it is harness-only: users open the preview in their own
     browser, and headless Chrome only renders diagrams and exports.
  2. A 404 for an asset: `resources.local_file` returns None when `path.resolve() != path` or
     `is_file()` fails, and `SimpleHTTPRequestHandler.send_head` answers 404 on any `OSError`
     from `open()`. On Windows both touch file handles (resolve opens the file); a transient
     sharing or access error there (scanner, indexer) would 404 one script.
  3. A nondeterministic exception in a script's top-level code. Code reading found no
     timing-dependent throw in the candidates, so this ranks last.
- Rejected by evidence: watcher mtime resolution or NTFS polling (no watcher thread runs in
  `PreviewBrowserTestCase`; versions only change through explicit `preview.render`), status
  polling (predates fe4706c), Chrome failing to start (setUpClass validates it with
  `Browser.getVersion`; the page did load), subprocess spawn (the open() wait spawns nothing; `WritingCase.build` renders before it),
  port reuse (port 0 per test; a collision raises at bind instead of hanging), suite order or
  a specific predecessor, generic runner slowness for A/B.

### Recommended solution
Run the experiment first (8.3); the fix depends on which layer fails. Prepared per outcome:
- Status 0 (connection): make the viewer refuse to run half-started. The served page gives
  each viewer `<script>` an error handler (server-side, `decorate_page`), and on any failed
  viewer script the page shows the existing "Viewer scripts did not load; reload the page"
  note and reloads once (guarded by sessionStorage so it cannot loop). This also helps users,
  who today get a page with an empty status and no comments. Then fix the server or socket
  cause the trace shows.
- 404: in `server.static_file`/`resources.local_file`, compare normalized paths without
  `resolve()` on files under `ASSETS_DIR`, and retry `open()` briefly on `PermissionError`
  as `portable.replace` already does for writes; or serve the immutable assets from memory
  read once at start (invalidation: package files never change while serving; prove with
  a test that edits an asset and expects the new bytes, or keep reading from disk).
- Exception: fix the script named by the captured error.

### Rejected alternatives
Longer waits, test retries or skipping Windows browser tests: they hide a page that can also
break for users. A blind CI rerun proves nothing (PR39: different tests failed on the same head,
then a diagnostics-only commit passed).

### Checkable invariants
- A page whose viewer scripts all answered 200 with a non-empty body fills `.pv-bs-label`
  within one poll (about 1 s on CI).
- If any viewer script fails to load, the page never stays silent: it reloads or shows the
  reload note.

### Portability and risks
The error-handler and reload path is browser-only and platform-neutral; a reload loop is the
risk (guard with a per-tab counter). Changing `resolve()` checks affects path containment on
every OS: keep the symlink and `..` guards and test them on macOS, Linux and Windows.

### Test that would fail before the fix
`probe/test_script_failure_signatures.py` turned into a regression test: serve the page with
`preview-build.js` (and separately `preview.js`) answering a reset, then expect the reload note
or a filled label. On b775566 the label stays empty for 5 s and no note appears (reproduced:
signatures above).

### Verification needed
The Windows experiment (8.3), then the full Windows job green on 3 consecutive runs plus a new
loop run with 0 half-started pages in at least 5,000 test executions.

### Measured impact or cost
Each failure costs 15 s, then a 10-minute Windows rerun and a red PR. 18 % of Windows jobs in
the window failed and passed on retry.

### Proposed PR(s)
- Experiment branch `experiment/windows-browser-flake`, never merged and no PR (workflow file
  is high tier, so it must not reach develop).
- After the outcome: `test: trace requests and page state in failed viewer waits` (tests only,
  medium). `fix: reload a viewer page whose scripts did not load` (`S/server.py` medium,
  `S/assets/preview/*` low; tier medium). `ci: print Chrome version and per-test durations on
  Windows` (`.github/`, high).

### Not verified
Any Windows behaviour; whether the failing request is an asset, which layer fails, and whether
Defender is involved. macOS fault injection shows signatures match, not that this is what
happens on Windows.

## 8.2 Slow runners (PR38 head 9ffef75 attempt 1, 2d280f8 attempt 2)

### Status
Documented, cause probably the runner; not a product defect by current evidence.

### Reproduction
From logs only. 9ffef75 a1: 912 tests in 774.7 s against 571.8 s for the green retry of the same
commit (+35 %); two failures; at the label failure `ready: loading`, no API request finished,
three handler threads alive in file-system calls (`os.walk` in `preview_submission.tree_stamps`,
`msvcrt.locking` unlock in `review_storage.review_lock`, the review lock's `open`). 2d280f8 a2:
717 tests in 702.9 s against 397 to 407 s for the other attempts (+75 %), two label timeouts
and one other failure, no page diagnostics yet.

### Root cause and confidence
Low to medium: the whole job ran 35 to 75 % slower and requests that take milliseconds were
still in file-system calls after 15 s, which points to a stalled disk or CPU on that runner. A
lock deadlock is unlikely: the three threads hold different locks and a single stack snapshot
cannot show whether they were moving.

### Recommended solution
None in product code yet. The experiment's 5-second sampler (CPU, disk latency, free memory,
TIME_WAIT and established sockets, Chrome processes, Defender CPU) lines a failure up with the
machine state. If a slow sample coincides with each such failure, record it as an
infrastructure limit; the owner decides whether the Windows job keeps its current runner.

### Rejected alternatives
Raising the 15 s wait: it would hide 8.1 as well.

### Checkable invariants
In the experiment, every failure whose server log shows API requests running for more than 5 s
also shows a sampler line with high disk latency or CPU; otherwise reopen as a product defect.

### Portability and risks
Windows-only by evidence (0 such stalls on Linux and macOS CI).

### Test that would fail before the fix
None: no product defect is identified.

### Verification needed
Sampler data from the experiment.

### Measured impact or cost
2 of 60 Windows jobs.

### Proposed PR(s)
None until the experiment.

### Not verified
That the runner was slow for an external reason; Defender's share.

## 8.3 Diagnostics and the next experiment

### Status
Resolved as a plan: experiment written and checked locally; not pushed, CI not triggered.

### Reproduction
- The PR39 diagnostics (7e2b242, in b775566) capture window `error` events in the capture phase,
  which include failed script loads. Checked on the unmodified checkout: with
  `preview-build.js` reset, the report's `errors` names it (`probe/test_trace_report.py`).
  They also print the label text, the focused element and the page state on any failed wait
  (the PR39 attempt 1 write-bar failure had none).
- They cannot tell: the status of a failed asset (0 vs 404 vs short body); whether the request
  reached the server; the viewer's own state (`started`, `polling`, `showBuild`); fetches still
  pending; how long after navigation things happened; whether the server still answers.
- Missing server-side logging: `GuardedHandler.log_message` is a no-op, `LocalServer.handle_error`
  drops `ConnectionError` without a trace, a 404 from `send_head` is invisible, and nothing
  records accept time, request-line time, status, bytes and duration per request.
- Missing CI context: no per-test durations (no `-v`/`--durations`), Chrome version not logged.

### Root cause and confidence
N/A.

### Recommended solution
Push `experiment.diff` to `experiment/windows-browser-flake` (not a PR). Contents:
- `tests/_browser_trace.py` (test support only): a traced `preview.Handler` subclass and
  `LocalServer` subclass logging per request: accept, request-line delay, status, bytes, time,
  errors including swallowed `ConnectionError`; a page script recording every fetch's start,
  end, status or error; a report with viewer flags, navigation timing, failed resources with
  status and sizes, script timings, pending fetches, a Python probe of `/__status`, and the
  server log since the test started (polls counted, not listed).
- `tests/_preview_browser.py`: uses them and records each page's place in its Chrome's life
  (`page_of_chrome`, `chrome_age`); waits and assertions unchanged.
- `scripts/windows-flake/loop.py`: repeats the browser modules (or `discover`) for 35 minutes,
  each pass a fresh process with `-v --durations 0`; `sample.ps1`: machine samples every 5 s.
- `.github/workflows/windows-browser-flake.yml`: push-triggered on that branch only (windows.yml
  and ci.yml do not run on it); 5 jobs: 2 browser loops, 2 browser loops with Defender real-time
  scanning off (the step fails loudly if it cannot be turned off), 1 whole-suite loop; artifacts
  per job.

Expected outcomes and decisions. Prior rate: about 19 failures in 60 jobs x about 180 browser
tests, so about 0.2 % per test; each browser job runs about 7 passes x 182 tests, so about 2.5
expected failures per job if the loop reproduces the suite's conditions.
- Failures show a viewer script with status 0 and the server log has no request for it: the
  request never reached Python. Chrome or loopback layer; fix via 8.1 recovery, then test
  HTTP/1.1 keep-alive in a later experiment (a contract change, high tier).
- Status 0 and the server log shows the request with an error or no status: server-side abort;
  the logged exception names the fix.
- Status 404: path resolution or open failed on Windows; fix in `static_file`/`local_file`.
- A JS exception with file and line: fix that script.
- API requests running for seconds with slow sampler lines: 8.2.
- Failures mostly at `page_of_chrome` 1 or 2: a cold headless Chrome is the trigger. Then the
  next experiment opens and discards one warm-up page per new Chrome in one arm; if that arm
  is clean, the defect is in the harness (test-only fix, medium) rather than the product,
  and the 8.1 page recovery becomes optional for users.
- Failures only in the Defender-on jobs, with at least 5 in them and 0 in the same number of
  Defender-off tests: Defender is a trigger; still fix the page recovery, and consider a
  documented exclusion for users only with that evidence.
- 0 failures in all four browser jobs (about 5,000 tests): the trigger needs the earlier
  non-browser tests; the suite job, or a next run looping `discover`, is the follow-up.

Cost: 5 jobs x about 45 minutes, about 225 Windows runner minutes, about 50 minutes wall time.
Free on GitHub-hosted standard runners for a public repository; on a private one Windows minutes
bill at twice the Linux rate (about 450 Linux-equivalent minutes).

### Rejected alternatives
A workflow_dispatch-only file: dispatch needs the file on the default branch, which would put an
experiment on main. Looping only the three PR39 tests: 60 runs at 0.2 % predict 0 failures (the
local 60/60 macOS passes are equally uninformative: P(0 | 0.2 %) is about 89 %).

### Checkable invariants
The tracing changes no wait, timeout or assertion: 182 browser tests passed locally with it.
The PR39 diagnostics' `errors` list always holds two 404s for `figures/external/*.png` on the
synthetic setUp page; they are noise, not the failure.

### Portability and risks
Test-only code plus a branch-only workflow. The sampler uses Windows cmdlets only. The probe
GET at failure adds one request after the wait has already failed.

### Test that would fail before the fix
N/A.

### Verification needed
Commands run here (macOS, all passed unless noted):
- `uv run --quiet --project packages/trazo ruff check --config packages/trazo/pyproject.toml`
  on the three changed Python files: passed.
- In `wt/`: `test_trace_report` plus the two PR39 retry tests: 3 tests OK, report names
  `preview-build.js` with status 0 and viewer `{"started": true, "polling": true,
  "showBuild": "undefined"}`.
- In `wt/`: `loop.py --minutes 0.01` over all browser modules: 182 tests, 0 failures. The
  later `page_of_chrome` addition was rechecked only with `test_trace_report`,
  `ParagraphTests.test_enter_at_the_start_opens_a_paragraph_before_this_one` and
  `test_preview_browser_underlines`: 5 tests OK.
- YAML parsed (Ruby YAML): 5 matrix jobs. The workflow itself was not run.

### Measured impact or cost
See cost above.

### Proposed PR(s)
None for the experiment. Keeping `_browser_trace.py` afterwards would be a tests-only PR (medium).

### Not verified
The workflow on GitHub (Defender cmdlets, `Get-NetTCPConnection` cost, artifact size); the
`--durations` output on Windows.
