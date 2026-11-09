# Open points after the workflow campaign, 2026-11-09

Investigation of every point the [final measurement report](measurement-final.md)
left open, at `b775566` (product tree `a15861a`, identical to the measured assembly
`6ee0bae`). Each point ends with evidence, a concrete fix, or an explicit limit or
owner decision. Nothing is integrated; prototypes are diffs against `b775566`.
Details and failing-first tests are in [`open-points/`](open-points/), with an
independent [cross-check](open-points/verification.md); raw evidence is in
`.build/open-points/` of the `t3-investigate-open-workflows` worktree.

## Not verified

- Nothing ran on Linux or Windows. Statements about them are code reading or
  GitHub logs, never a run. Windows CI was not triggered and no branch was pushed.
- Prototypes passed focused tests only. No prototype ran `make test-package`,
  `make test-template`, `make test-update` or the acceptance harness.
- Phase B latency numbers come from one same-session A/B run on the owner's
  machine while the owner's own sessions were running (see point 5). They do not
  replace the 2026-11-07 archive, which was not changed.
- Not prototyped: writer-conflict notices, the Windows runner path and
  `ReplaceFileW` variant, the diagram-export coherence case, power loss.
- Side effect: a FAT32 test image hung `renamex_np`; two processes are stuck and
  the image will not detach until a reboot. No repository or user file changed.

## Summary

| # | Point | Status | Root cause, confidence | Recommendation | Tier |
| --- | --- | --- | --- | --- | --- |
| 1 | Independent writers | Reproduced, fixable on APFS/Linux, limit elsewhere | `os.replace` discards whatever is at the name, high | Atomic exchange, compare the displaced file, keep both on mismatch | High |
| 1b | Save response hash (new) | Reproduced | Reply re-reads the file after publishing, high | Return the hash of the bytes written | Medium |
| 2 | Silent errors | Reproduced, 3 families | Failed watcher pass consumes its changes and only prints, high | Report through the existing page error, retry with backoff | Medium (Chrome: High) |
| 3 | Unbounded waits | Reproduced (Pandoc, metadata, uv, tables command, held pipe) | No shared deadline outside MCP, high | One bounded runner extracted from `Calls.run` | High |
| 4 | Build coherence | Reproduced: mixed DOCX and false stamps, plus steps never rebuilt (new) | Hash once, read later, record without re-check, high | Re-check before publishing, 2 attempts, then explicit failure | High |
| 5 | Cold save-visible | Fixed in prototype: load cold p95 1.866 to 1.334 s (same-session A/B) | Watcher sleeps for a change it already saw, high | Skip the first wait, with the stale-page fix (new bug) | Medium |
| 6 | Idle requests | Prototype meets 1 req/s but slows warm saves | 250 ms status polling, high | One quiet-machine experiment, then adopt or accept the cost | Medium |
| 7 | Typecheck | 148 errors confirmed, gate not enforced | 137 noise, 3 real runtime bugs | Enforce the strict list in CI, then fix the 3 bugs | High, then Medium |
| 8 | Windows timeouts | Windows-only, 18% of jobs; mechanism identified, trigger unknown | A viewer script did not run, medium-high | Branch-only experiment, then the matching fix | Experiment: no PR |
| 9 | Other limits | Orphans, retention, quarantine reproduced | See point 9 | Three medium fixes, one documented limit, fsync decision | Medium |

## 1. Independent writers ([annex](open-points/writers.md))

Reproduced at `b775566`: `scenarios.py ... --scenario concurrent_writers
--scenario concurrent_atomic_writers` exits 1, HTTP 200, external text gone from
file and history. Path: `preview_sources.change_source` takes `review_lock`
(:133), reads and checks the hash (:135, :157), writes history (:165), then
`write_atomic` ends in `os.replace` (:166).

**Recommended contract.** On filesystems with atomic exchange (APFS; Linux
ext4/xfs/btrfs/tmpfs by documentation), no version present at the path between
the locked read and the reply is lost: trazo swaps its file in
(`renamex_np RENAME_SWAP`, `renameat2 RENAME_EXCHANGE` via ctypes), compares the
displaced file with the bytes read under the lock and, on mismatch, swaps back,
keeps the browser text in `notes/review/conflicts/`, records no undo entry and
answers 409 with the existing conflict banner. A matching displaced file is
parked for 600 s so a writer holding it open writes into a kept file. A sweep
under the lock recovers parked and killed-save files; the undo history is the
journal. Elsewhere (exFAT, FAT, HFS+, network, Windows) trazo checks just
before `os.replace`, reports `exact: false`, and the page says once that a write
at that instant can still be lost.

**Evidence.** In a forced race of 500 iterations per cell, today's code loses 245
to 500 external writes per cell on APFS; the exchange protocol loses none, including
a writer holding the file open; the fallback loses 2 to 45 in-place or atomic writes
and every late write through an open file (table in the annex).
With the prototype both harness scenarios pass (409, marker on disk, browser text
kept). Four new tests fail on `b775566` and pass on the prototype.

**Limits that need writer cooperation.** A late write through a file held open
past the grace window; without exchange, an atomic replacement in the microseconds
before the rename; a writer's own read-modify-write spanning the save. The exchange is called only on an allowlisted filesystem: on a FAT32 image
it hung the calling process unkillably, so "try and catch ENOTSUP" is unsafe.
Corrected premise: the owner's external SSD is case-sensitive APFS.

**1b, new defect.** The 200 reply returns the hash of a re-read after the rename
(`preview_sources.py:168`), which the browser keeps as its base, so a write in that
instant is overwritten by the next save with no 409. Reproduced and fixed.

## 2. Silent errors ([annex](open-points/silent-errors.md))

- **Outer watcher, `preview_watch.py:197`: user must know.** Reproduced with real
  failures: a tables command writing one non-UTF-8 byte to stderr leaves "Build
  OK" while later edits never appear; a section unreadable at start kills the
  watcher thread (`build_and_watch` has no handler) and the page says
  "building..." forever. Cause: the pass drains its pending changes before the
  work that can fail and only prints. Fix: restore the pending changes, set every
  page's existing `error` field (the PR #35 channel), log once per distinct
  error, retry at once on a new change or after 1 to 30 s backoff, rebuild every
  page on recovery. Two tests fail before.
- **Drift check, `preview_edit_review.py:247`: mostly tolerable.** An unreadable
  spec is already loud (page build fails, `/__workspace` 500). The silent case is
  line 250: a hand edit re-saved as Latin-1 gets no "Edited by hand?" warning and
  is overwritten by the next render. Fix: treat undecodable output as drift,
  narrow the except to `OSError`. Test fails before.
- **Chrome cleanup, `diagram_editor.py:335`: tolerable, but leaks.** A non-
  `BrowserError` at close skips the reap and deletes the profile while Chrome
  lives, so the profile is recreated and leaks. Fix in `browser.Chrome.close`:
  always reap before removing the profile; `drop` logs one line. Never in status.

## 3. Unbounded subprocesses and waits ([annex](open-points/process-bounds.md))

Inventory with file:line in the annex. Reproduced with no end: `trazo build` with
a hung Pandoc, metadata read or `uv sync` (holding the build lock); a hung watcher
tables command ("Build OK" shown, other builds blocked); a hung page Pandoc that
survives preview shutdown. A descendant holding stdout after its parent exits makes
a successful MCP build report a timeout at 1200 s, and a `CommandJob` report
success as "timed out" or stay "running". `subprocess.run(timeout=)` kills only the
leader on POSIX and, on Windows, drains pipes without a bound.

**Recommendation.** Extract the mechanism of `Calls.run` into one
`processes.run` (group start, deadline, group kill, bounded drain, replacement
decoding) and keep `Calls.run` as a thin wrapper with its contract. Additions: a
5 s linger once the leader exits, then group kill and a `Held` result with the
real exit code; group kill on Ctrl+C. Defaults: Pandoc 600 s
(`TRAZO_PANDOC_TIMEOUT`), metadata/field reads 60 s, `pandoc --version` 30 s,
`uv sync` 1800 s (`TRAZO_UV_TIMEOUT`), tables command 1200 s; user build steps
keep no deadline but get their own group; the watcher takes the build lock
non-blocking; the HTTP table save waits 20 s. The only MCP contract change is the
held-pipe case, which today reports a 20-minute false timeout. POSIX prototype: 5
runner tests pass. Windows: reader threads plus taskkill, Job objects for full
containment; not run.

## 4. Build coherence ([annex](open-points/build-coherence.md))

Reproduced with a pausing Pandoc shim during `trazo build`: a bibliography,
metadata title or figure changed mid-build lands in the DOCX as v2 while the
stamp records v1; two sections edited together give methods v1 with results v2.
Reverting to v1 then leaves the v2 DOCX "up to date". **New and worse:** steps and
the diagram export hash their inputs after the run (`build_graph.py:392`, `:497`),
so a step input edited while the step runs is never rebuilt, contradicting "the
next build detects the change". Natural windows on the load paper: about 115 ms
from hash to Pandoc reading images and bibliography, 270 ms to the stamp.

**Recommendation (candidate b).** Snapshot watched inputs (hash, mtime, size,
inode) before the work and again just before publishing; on any difference discard
the result, retry once, then fail with "changed during the build" and keep the old
DOCX. Steps compare pre-run and post-run snapshots and write no record on change.
Cost: 5.6 ms per manuscript attempt, about 2% of the Word stage. Rejected: a
staging copy (no cross-file coherence) and pre-read hashes only (today's behavior).
Residual: an edit undone with mtime and inode restored. `build_graph.py` is over the file
size limit, so the PR moves helpers into a new module. Three tests fail before.

## 5. Cold save-visible ([annex](open-points/preview-latency.md))

The archived profile, aligned with the Chrome trace, explains the 1.85 s: the
first manuscript render still running when the save arrives (0.33 s left), the
first renders of the other two documents (0.25 s), table checks (0.09 s), then the
watcher sleeping its full 0.5 s interval for a change it had already seen, the
render with the save (0.58 s), browser detection, reload and paint (about 0.33 s).
The cold regime keeps `.build`, so the save lands 0.54 s into the first render;
users meet this when they or an agent edit during the first 1.1 s of builds.

- **5.2, fix:** `preview_watch.watch()` skips its first wait when the snapshot
  already differs. Test fails before.
- **5.3, new bug, reproduced:** a page keeps stale text forever when a build lands
  between serving its HTML and its first status answer, because `preview.js` takes
  its baseline from that answer. Fix: the server reads the version before the file
  and serves `<meta name="trazo-version">`. Chrome test fails on `b775566` after
  15 s, passes in 3.5 s. Required by 5.2, which makes the race likely.
- **5.4, optional:** stop the other documents' first renders once a change is seen,
  so the edited page renders first. Same total work.

Phase B, same-session A/B, 20 samples per cell, seconds p50/p95
([measurements](open-points/perf-phase-b.md)):

| Load paper, cold save | Before | After | Gate (interval, 5%) |
| --- | ---: | ---: | --- |
| A = 5.2 + 5.3 vs base | 1.851 / 1.866 | 1.282 / 1.334 | p50 and p95 pass |
| B = A + 5.4 vs A | 1.282 / 1.334 | 1.049 / 1.362 | p50 passes, p95 not established |

Example cold save: 1.117 to 0.632 s (A), 0.313 s (B), all gates pass. Warm saves
and `preview_start` unchanged; no timeout. The drift control passes no gate, but the
owner's own work raised load from 1.5 to 14.9, so p95 tails are noisy.
Recommendation: one PR with 5.2, 5.3 and 5.4 (p50 claim only). Re-measure on a
quiet machine before claiming 28/28.

## 6. Idle requests ([annex](open-points/preview-latency.md))

Reproduced as a request count: a visible standalone page makes 11 requests in
3 s (1 workspace plus 3 status per second); MCP views keep the old cadence.
The prototype (a held `/__workspace?wait=`, answered on a
state change or after 1 s, never used by MCP views) reaches the target, 1.0
workspace and 0 status requests per second, with lower renderer CPU. It fails the
latency rule: example warm save p50 rose from 0.579 to 0.700 s against the adjacent
base, interval excluding zero; the likely cause is the full workspace build on wake.
Not recommended as prototyped.

**Decision with one concrete experiment.** A held `/__status?wait=` returning when
the next 1 s workspace scan is due (about 2 requests/s, the cheap 1 ms answer on
wake) measured with the phase B plan on a quiet machine (load average below 2). Adopt
it if saves are not worse beyond noise and idle is about 2 requests/s; otherwise keep
PR #38's cadence and record 3 extra status requests/s (about 1 ms each; server CPU
differences were within this session's noise) as an accepted cost, replacing the
no-increase criterion for standalone previews.

## 7. Typecheck ([annex](open-points/typecheck.md))

Confirmed: 148 errors in 37 files (80 checked), the same on `--platform linux`, 150
on win32, zero in the 14 strict modules. Nothing runs mypy in CI, hooks or scripts.
137 errors are noise. Three hide real bugs, each reproduced: `counts.py`
`None += int` for a raw `<header>` inside `::: abstract`; clearing a table note whose
closing fence ends in `\r` or NBSP gives HTTP 500; a comment request without `id`
gets 404 instead of 400. Plan: a CI gate failing only on strict modules (no code
change), the three fixes, `sys.platform` branches (-11), a `load` overload (-15),
then bottom-up promotions, watching re-export traps (`machine`, `mcp`, `table`).
No global cleanup.

## 8. Windows browser timeouts ([annex](open-points/windows-browser.md))

From 62 Windows job logs since 2026-11-03: 9 label timeouts in 7 of 60 jobs, 0 in
102 Linux and macOS jobs; 18% of Windows jobs failed and passed on retry. Not PR #38
polling (the failing PR #36 head lacks it), not slow runs once the 15 s waits are
removed, not one predecessor test; 6 of 9 fall in the first two tests of a class,
while Chrome is new (P about 0.03, a hint). Mechanism, medium-high confidence: one
viewer script did not run. Failing `preview-build.js` alone reproduces the PR #39
retry's page exactly; failing any of 8 other scripts reproduces "no API request".
Chrome lists failed scripts in Resource Timing, so the CI dumps do not show they ran.
The Windows trigger is unknown; the PR #39 diagnostics would name a failed script but
lack server-side request logs.

**Next experiment (written, not pushed).** Branch `experiment/windows-browser-flake`,
never merged: server and page tracing, a 35-minute loop, a machine sampler, 5 jobs
(2 browser loops, 2 with Defender off, 1 whole suite), about 225 runner-minutes. The
annex maps each outcome to its fix (reload a page whose script failed, path handling
in `static_file`, or the named script). Longer waits or retries are rejected: users
can get the same half-started page.

## 9. Other limits ([annex](open-points/storage-limits.md))

- **Orphan temporaries after SIGKILL (reproduced).** `<name>.<8>.tmp` beside the
  target (visible in `git status` under `manuscript/`) and a valid but unfinished
  DOCX in `output/.trazo-*`; nothing cleans them. Fix: the writer locks its temp;
  the next write of the same file removes unlocked siblings older than 600 s.
- **Retention (reproduced).** A failed save at the limit evicts the oldest entry
  in both stores (200 table/diagram, 50 manuscript). New: after a failed manuscript
  save, undo looks unavailable and the first undo wrongly reports a changed file.
  Fix: the pending entry does not count toward the limit; manuscript history
  reconciles pending saves.
- **Quarantine.** Salvaging entries is safe but not worth it: documented limit.
  New: a damaged table or diagram history blocks all their saves; give it the
  manuscript store's quarantine.
- **Coverage gaps, none a demonstrated bug.** Each has a concrete experiment in
  the annex. Suspected: `trazo update` can leave a new executable at 0644 after a
  kill. Power loss is bounded only by an fsync policy: file plus folder fsync for
  user data adds about 0.04 ms per 20 kB write here, but macOS `fsync` does not
  flush the drive cache; `F_FULLFSYNC` does, at about 7 ms per write.

## Proposed PR sequence

One root cause each, ordered by user impact against risk. Every PR needs `make
lint`, `make test-package`, `make test-template`, the acceptance areas it touches,
and its failing-first test shown red on `b775566`.

| Order | PR | Tier |
| --- | --- | --- |
| 1 | `fix:` return the hash of the bytes a source save wrote (1b) | Medium |
| 2 | `fix:` report watcher failures in the preview status and retry (2) | Medium |
| 3 | `fix:` publish a Word file only for inputs that held still, steps included (4) | High |
| 4 | `fix:` keep the oldest undo entry when a save fails (9) | Medium |
| 5 | `fix:` quarantine damaged table and diagram history (9) | Medium |
| 6 | `feat:` exchange-based source publication with conflict copies (1) | High |
| 7 | `feat:` conflict and degraded-mode notices in the Source tab (1) | Medium |
| 8 | `refactor:` share the MCP command deadline with every caller (3) | High |
| 9 | `fix:` bound Pandoc, metadata reads and uv sync in builds (3) | High |
| 10 | `fix:` bound the watcher's commands and lock waits (3) | Medium |
| 11 | `fix:` finish background jobs when their command exits (3) | Medium |
| 12 | `fix:` show a save made while the preview opens without waiting a poll (5.2, 5.3, 5.4) | Medium |
| 13 | After the quiet-machine experiment: held status request, or a docs PR accepting the cadence (6) | Medium |
| 14 | `ci:` enforce the strict type-check list (7) | High |
| 15 | `fix:` the three type-revealed bugs, one PR each (7) | Medium |
| 16 | `fix:` treat undecodable table outputs as drift (2) | Medium |
| 17 | `fix:` always reap Chrome before removing its profile (2) | High |
| 18 | `fix:` remove temporary files of killed writers (9) | Medium |
| 19 | `fix:` reject edit-review publication through the exchange path (1) | Medium |

Experiment branch for point 8 first, without a PR; its fix follows the outcome.
Each PR updates the matching paragraph of `measurement-boundaries.md` or
`measurement-writer-limits.md`. PRs 2, 10, 12 and 13 all touch `preview_watch.py`
and land one after another.

## Owner decisions

1. Writers: on a conflict, keep the external version on disk and answer 409
   (recommended, matches today's banner) or keep the browser version and copy the
   external one.
2. Writers: accept "post-publication housekeeping failed, save reported" as a
   harness exception at fault site 009 (recommended), or drop the parking that
   protects writers holding the file open.
3. Build coherence: after 2 changed attempts, fail and keep the old DOCX
   (recommended) or publish without recording freshness.
4. Process bounds: approve the default deadlines above; ship POSIX first with
   Windows best effort, or require the Windows path in `windows.yml` before PR 8.
5. Durability: no fsync (documented), file plus folder fsync for user data
   (recommended), plus `F_FULLFSYNC` for source saves on macOS.
6. Template: add `*.tmp` to the paper `.gitignore` (high tier; also hides a
   user's own `.tmp` files).
7. Windows: push the experiment branch (about 225 runner-minutes).
8. Idle requests: run the held-status experiment (recommended), or accept PR #38's
   3 extra status requests per second now.
