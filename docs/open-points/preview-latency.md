# Cold save-to-visible latency

Evidence: `.build/open-points/perf/` in the `t3-investigate-open-workflows` worktree (local, git-ignored). Prototype worktrees (`wt/`, `wt-*`) were not retained; their changes are the `*.diff` files there, all against b775566 and never integrated. Back to the [decision summary](../open-points.md).

Final version (phase A analysis, phase B measurements). Phase A figures come from the
archived runs and profiles; phase B results are in the section "Phase B results" and
supersede the phase A estimates.

Head b775566 (product tree identical to the measured assembly 6ee0bae). macOS arm64
only. Linux and Windows statements are code reading.

Evidence used:
- Raw samples: `docs/measurements/2026-11-07/*-load.csv`, per-sample status in
  `t3-measure-integrated/.build/measure/integrated-measured/load/results.json`.
- Profile with traces: `t3-measure-integrated/.build/measure/integrated-final/profile-load/`
  (`profile-summary.json`, `profiles/save_visible-{cold,warm}-0/*.json`,
  `traces/save_visible-{cold,warm}-0.json`). Baseline profile (ee13ca0):
  `t3-a971c6e6/.build/measure/profile-load/`.
- Chrome trace timestamps and Python `perf_counter` share the macOS monotonic clock:
  the trace's `/__source` POST lands at t=0.000 of the operation, so the two
  timelines below are aligned (checked, not assumed).

Prototypes (detached worktrees at b775566, never integrated):
- `.build/open-points/perf/wt-a`: diff A = points 5.2 + 5.3 (`prototype-save-a.diff`).
- `.build/open-points/perf/wt-save`: diff B = A + point 5.4 (`prototype-save-b.diff`).
- `.build/open-points/perf/wt-idle`: diff C = point 6 (`prototype-idle.diff`, now version 2: see 6).
- `.build/open-points/perf/wt-ac`: A + product files of C version 2.
- C's product files apply on top of A or B (`git apply --exclude=packages/trazo/tests/test_preview_browser_shell.py`, checked with `--check`); only the two new shell tests collide textually.

---

## 5.1 Cold save-visible decomposition (load paper, p95 1.877 s vs 1.5 s budget)

**Status:** resolved by evidence (decomposition); fixes in 5.2 to 5.4.

**Reproduction:** archived data only (no new timing). Uninstrumented cold samples are
deterministic: 1.80 to 1.88 s for 18 of 20, plus 1.48 s for samples 0 and 1
(combined-load.csv; the same two-mode shape exists in polling-load.csv). Before PR #38
every run sat at 2.24 to 2.38 s.

**What the cold regime does** (`scripts/measure/run.py`, `Session`): a fresh MCP and
Chrome per sample; `preview_start` (a new server); navigate until READY; then at
once GET `/__source` and POST the save. Cold `save_visible` does **not** delete
`.build`, so the previous session's `.build/preview/manuscript.html` is served while
the new server's first full render runs. READY is satisfied by that stale page, and the
save lands about 0.54 s into the first manuscript render.

**Timeline of the profiled cold sample** (instrumented, total 2.10 s; t=0 is the POST;
server spans from `56428.json`, browser from the trace):

| t (s) | Phase | Duration | On the critical path? |
| --- | --- | ---: | --- |
| -0.537 to 0.329 | First manuscript render (read the old text before the save) | 0.329 after the save | yes, cannot be shortened without cancelling Pandoc |
| 0.329 to 0.575 | First supplementary + cover_letter renders | 0.246 | yes, removable (5.4) |
| 0.575 to ~0.667 | `check_outputs` (ten table specs) + snapshots | ~0.09 | yes, small |
| ~0.667 to 1.167 | `watch()` sleeps its first 0.5 s interval although the change is already known | 0.500 | yes, removable (5.2) |
| 1.167 to 1.742 | Manuscript render with the save | 0.575 | yes, fixed cost (Pandoc x2 + Python post-processing) |
| 1.742 to 1.771 | Browser notices version 2 (here via the 1 s `/__workspace`, lucky) | 0.029 (expected 0 to 0.25) | yes, removable (6) |
| 1.798 to 1.920 | Reload: navigation to HTML response (decorate, counts, under GIL contention with the next renders and `/__catalog`) | 0.122 | yes, fixed-ish |
| 1.920 to 2.100 | Page load to marker visible, fonts, two frames | 0.180 | yes, fixed (browser) |
| 1.742 to 2.028 | Second-pass supplementary + cover_letter renders (`shown = None`) | 0.286 | no, concurrent; adds contention |

Also in the window: a reload of the stale page to version 1 (status at 0.581 found
version 1; the poll at 0.323 missed it by 6 ms), and that page's `/__submission`
(0.293 s) and `/__catalog` requests. The final status of every sample shows all three
documents at version 2: every document was rendered twice.

Warm profiled sample (1.65 s instrumented): watcher detection 0.508 (the 0.5 s poll
phase), render 0.584, browser detection 0.271 (poll 250 ms plus listener time),
serve 0.09, load to visible 0.19. Warm samples are phase-locked to the 0.5 s watcher
timer, which explains the narrow warm spread.

**Root cause and confidence:** high (structure), medium (uninstrumented magnitudes).
The 1.5 s miss is the sum of two removable waits on the critical path: the watcher's
first sleep (0.5 s, deterministic in this regime) and the other documents' first
renders (~0.25 s instrumented), on top of fixed costs (in-flight render remainder,
one manuscript render, serve, page load) and the status poll phase.

**Harness artifact or real?** A real but narrow scenario. Any change landing during
the first builds (about 1.1 s on the load paper) pays both removable waits: an agent
that calls `preview_start` and then edits a file, or a user who reopens a paper and
saves at once. The harness is deterministic (stale page always present, save always
~0.54 s into the first render), so p50 and p95 coincide; real users would see a spread
from zero to the whole first-build window. Saves after the first builds (the warm
regime) are unaffected.

**Samples 0 and 1 at 1.48 s:** their final status shows cover_letter still at
version 1, so the marker was visible before the second-pass cover render ended:
the save landed later relative to the server, not a faster pipeline. Likely the first
fresh Chrome profiles reached READY later. Not verified; it does not change the decision.

**Not verified:** uninstrumented per-phase durations (cProfile inflates the render
thread; the profiled sample is 0.25 s slower than the uninstrumented median).

## 5.2 The watcher waits a poll interval for a change it already saw

**Status:** still open on b775566; fix prototyped and measured (diff A): adopt.

**Reproduction:** `PYTHONPATH=packages/trazo/tests uv run --quiet --project packages/trazo python -m unittest -v test_numbering.BetweenDocuments.test_an_edit_during_the_first_builds_rebuilds_every_page_before_the_first_wait`
in `wt-save` with only the test applied: **FAIL** (manuscript still cites S1). With the
fix: pass.

**Root cause:** `preview_watch.watch()` computes `snapshot() != previous` before its
loop (to set `shown = None`) but still sleeps `interval` before the first pass.

**Recommended solution:** in `watch()`, keep that comparison as `pending` and skip
the first wait when it is true (8 lines). No cache, no new state.

**Rejected alternatives:** a shorter watcher interval (also helps warm, about 0.125 s
mean, but doubles idle snapshot cost; not justified by this cell); OS file events
(not stdlib); cancelling the in-flight first render (kills Pandoc mid-run, complex,
gain bounded by the render remainder, random for real users).

**Checkable invariants:** a change seen at watcher start is rendered before the
first wait; every page still ends with the latest numbers (existing
`test_an_edit_while_the_watcher_starts_rebuilds_every_page` passes).

**Portability and risks:** pure control flow, platform-neutral. An editor that writes
in two steps can be caught between them, as at any poll today; the next pass renders
the final text. **Depends on 5.3:** with the wait gone, version 1 and version 2 can
land within one page load on a small paper, which triggers the missed-reload race.

**Test that fails before the fix:** the renamed test above (the existing
`test_an_edit_during_the_first_builds_rebuilds_every_page` with the stop event set at
the first wait; it replaces the old one, no duplicate).

**Verification needed:** phase B, variant A.

**Measured impact or cost:** phase B, variant A (with 5.3): load cold save_visible
1.851/1.866 to 1.282/1.334 s (p50/p95, both gates pass), example cold 1.117 to 0.632 s;
warm and preview_start unchanged; idle unchanged.

**Proposed PR:** together with 5.3, "fix: show a save made while the preview opens
without waiting a poll", **medium** (`S/preview_watch.py`, `S/server.py`).

## 5.3 A page can keep stale text after a build lands before its first status (new finding)

**Status:** still open on b775566, **reproduced**; fix prototyped (diff A), adopt.

**Reproduction:** `PYTHONPATH=packages/trazo/tests uv run --quiet --project packages/trazo python -m unittest -v test_preview_browser_shell.ShellTests.test_a_build_between_serving_the_page_and_its_first_status_still_reloads`
with b775566 product code: **FAIL** after 15 s (page stays on "Second build.");
with the fix: pass in 3.5 s. The test holds the reloaded page's status requests in
the browser (a fetch wrapper in the test, no product seam), lands a build, releases.

**Root cause (high confidence):** `preview.js` sets `loaded` from the first status
answer, not from the version the HTML was served at. A build that completes between
serving the page and that answer is recorded as already shown, so the page never
reloads until the next change. Window today: about 0.1 to 0.2 s per page load; agents
editing several files in quick succession can hit it.

**Recommended solution:** `server.served_page` reads `STATE[name]["version"]` under
`LOCK` **before** reading the file and `decorate_page(..., version=)` adds
`<meta name="trazo-version">` after the page stamp (so comment-anchor caches keyed by
the stamp are not invalidated by version-only changes). `preview.js` uses it for
`loaded[doc]`. A build landing between the two reads causes one extra reload, never a
missed one. The MCP view gets it too (`page_bundle` uses `served_page`).

**Rejected alternatives:** a version in the HTML file at render time (changes the
Pandoc output and DOCX-adjacent paths); comparing content digests in the browser
(a second fetch per poll).

**Checkable invariants:** served version <= version of the served text; a page whose
served version differs from the first status reloads once.

**Portability and risks:** browser and server logic only. Risk: one extra reload in
the rare inverse race. A test wrapper of `decorate_page` needed `**served`.

**Test that fails before the fix:** the test above.

**Verification needed:** run in phase A on macOS: the new test, `test_preview_browser_shell`
(12 OK), `test_preview_browser_mcp_app*` (9 OK), counts, figures, writing, source edits,
edits, `test_preview_served_paths`, `test_page_bundle` (all OK). Linux/Windows CI pending.

**Measured impact or cost:** measured only together with 5.2 (variant A): no warm or
preview_start change beyond drift; no timeouts in its 80 save samples (nor in the 160 of AC and AC2).

**Proposed PR:** with 5.2 (or alone first), **medium**.

## 5.4 Other documents render before the edited one when the edit lands during the first builds

**Status:** still open; fix prototyped and measured (diff B = A + this): include.

**Reproduction:** `test_numbering.BetweenDocuments.test_an_edit_during_a_first_build_renders_that_page_again_before_the_others`:
FAIL without the change (`['manuscript', 'supplementary']`), pass with it.

**Root cause:** `build_and_watch` finishes every first page from stale sources first.

**Recommended solution:** after each first build, `if preview.snapshot() != before:
break`; `watch()` then renders the changed page first and, because `shown is None`,
every other page right after. Same total work, different order. One extra snapshot per
document (~8 ms instrumented).

**Risks:** pages not yet built show the "building..." stub ~0.5 s longer in this case.
The break relies on the `shown = None` rule (comment in code).

**Measured impact or cost:** B vs A: load cold p50 1.282 to 1.049 s (gate passes), p95
not established (1.334 to 1.362, three slow samples under load); example cold 0.632 to
0.313 s (both gates pass).

**Proposed PR:** same PR as 5.2 if adopted, **medium**.

## 5.5 Redundant second pass of every document (documented, not pursued)

**Status:** documented limit (by choice). After an edit during the first builds,
`shown = None` forces every other document to render again even when no number
changed (0.29 s instrumented, off the critical path). Code reading suggests `numbers`
from the `labels.once()` block is exactly what the first pages used, so the rule may
be redundant for `build_and_watch`; but it guards the "edit while reading the numbers"
case and 5.4 relies on it. Gain is only indirect (CPU/GIL contention while serving the
reload). No PR proposed without a profile showing it on the critical path.

---
