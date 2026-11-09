# Idle preview requests

Evidence: `.build/open-points/perf/` in the `t3-investigate-open-workflows` worktree (local, git-ignored). Prototype worktrees (`wt/`, `wt-*`) were not retained; their changes are the `*.diff` files there, all against b775566 and never integrated. Back to the [decision summary](../open-points.md).

## 6 Idle request rate after PR #38

**Status:** still open. The prototyped fix (diff C) meets the idle target but not the
latency rule, so it is not recommended; next experiment named below.

**Reproduction:** functional count in Chrome (not a timing benchmark):
`test_preview_browser_shell.ShellTests.test_an_idle_page_asks_about_once_a_second_and_shows_a_new_version_at_once`
against b775566 polling: **FAIL, 11 requests in 3 s**; with diff C: pass (<= 4).
Archived idle run: 1.0 workspace/s + 3.0 status/s, server CPU p50 3.2% of one core,
renderer main thread 0.14% to 0.58%.

**Root cause:** `assets/app/app.js` `poll()`: a visible standalone preview polls
`/__status` every 250 ms between 1 s `/__workspace` scans. `window.TRAZO_VIEW` (MCP
views) and the editors (listeners with `preview: false`) keep 1 s workspace polls.

**Options** (idle req/s for one visible page; latency = delay from a version bump to
the browser knowing):

| Option | Idle req/s | Latency | Threads | MCP/iframe hosts | Windows | Test cost | Risk |
| --- | ---: | --- | --- | --- | --- | --- | --- |
| Today (PR #38) | 4 | U(0, 0.25) s + listener time (0.03 and 0.27 s seen) | none held | unchanged | as now | none | known |
| **Held workspace request** (`/__workspace?wait=<states>`, answered on a document state change or after 1 s) | ~1 | wake + one workspace build (~0.03 to 0.05 s in traces) | one per visible standalone tab, blocked <= 1 s in `Condition.wait` (daemon threads) | not used by MCP views (`!TRAZO_VIEW`), so host tool calls never hang | stdlib `Condition` and `ThreadingHTTPServer`; not run on Windows | 3 HTTP tests (~2 s) + 1 Chrome test (~9 s) | medium |
| Held `/__status` plus 1 s workspace | ~2 | ~0.001 s | as above | as above | as above | similar | medium, still 2x the old rate |
| Server-Sent Events | 1 + 1 open stream | ~0.001 s | one per open tab, indefinitely | an MCP view cannot reach loopback; separate path needed | streaming over HTTP/1.0, needs heartbeats to detect gone clients | higher | higher (lifecycle, cleanup) |
| ETag/conditional workspace | 4 (fewer bytes only) | as today | none | unchanged | n/a | low | does not address request count; server still computes the workspace to compare |
| Adaptive (fast after the page's own save or while rendering) | 1 idle | 0.125 s for page saves; 0.5 s for file edits (agents, editors) | none | unchanged | n/a | medium (state machine) | loses the gain for the main trazo workflow (agent edits files) |
| Merge status into a 250 ms workspace | 4 (heavier) | as today | none | unchanged | n/a | low | more server CPU (snapshot + asset fingerprint 4x/s) |

**Recommended solution:** the held workspace request. `workspace.status(wait)` waits
on `preview.CHANGED = threading.Condition(LOCK)` until the digest of `STATE`
(`workspace.states`) differs from `wait` or `HOLD = 1.0 s` passes, then returns the
normal workspace (plus `states`). `preview.build()` and the three error paths in
`preview_watch` call `CHANGED.notify_all()` inside their existing `LOCK` blocks; a
missed notify degrades to the 1 s hold, never to staleness. `app.js` sends `wait` only
for a visible standalone preview with a previous answer, re-polls at once after a held
answer, waits 1 s after a failure (no hot loop when the server is down), 5 s when
hidden. The status branch and its reserved-name guard (`comments`, `docx`, `synced`,
`server`) disappear, since the workspace nests documents. `/__status` stays for the
stub page and `preview_status`.

**Rejected alternatives and why:** see the table. SSE gives no extra benefit over a
1 s hold and adds a connection lifecycle; ETag does not reduce requests; adaptive
polling loses latency for file edits; a held `/__status` keeps 2 req/s.

**Checkable invariants:** idle visible page <= ~1.05 workspace req/s and 0 status
req/s; a version bump is answered within one workspace build; comments and DOCX
state reach the page within ~1 s (the old cadence, as MCP views have today); MCP views
and editors send no `wait`.

**Portability and risks:** Chrome allows 6 connections per host: six visible preview
tabs of one server would hold all of them for up to 1 s at a time (hidden tabs do
not hold). Loopback only, so proxies do not apply; framing is already refused. Windows
and Linux not run. Shutdown is not delayed (`ThreadingHTTPServer` daemon threads; the
hold is 1 s). Docs to update with a PR: `docs/measurement-preview-latency.md`, CHANGELOG.

**Test that fails before the fix:** the Chrome idle-rate test above (11 > 4 on
b775566); the three `HeldWorkspaceTests` error on b775566 (no `HOLD`/`states`), so the
Chrome test is the meaningful negative control.

**Verification needed:** phase B idle.py and save_visible runs. Functional, phase A
(macOS): `test_preview_browser_shell` 12 OK, `test_preview_browser_mcp_app` 4 OK,
`_parity` 5 OK, figures 12, writing 14, source edits 3, edits 8, table editor 7,
`test_workspace` 5, `test_page_bundle` 6, `test_diagram_routes` 14,
`test_preview_tables` 6, `test_mcp_app` 3, `test_numbering` 15: all OK. `ruff check`
(as `make lint`) passes in both worktrees.

**Measured impact or cost (phase B):** idle goal met, latency goal not. See "Phase B
results". Version 1 of the prototype had a defect that phase B exposed (the first
answer of each page was followed by a 1 s wait, so after every reload the page fell
back to the pre-#38 cadence: load cold 2.375 s, warm 1.195 s). Version 2 fixes it
(re-poll at once whenever the next request would be held) and also ignores the
aborted held request when the page is left (otherwise the next page restores
"Offline": `test_reload_shows_the_last_build_status_and_figures_count_at_once` caught
it). The strengthened Chrome test now also asserts a reload < 0.9 s right after the
first answer; it fails with the version 1 timer (1.06 s) and passes with version 2.
Version 2 brings idle to 1.0 workspace/s and 0 status/s, but its save latency is not
better than PR #38 and is worse on the example warm cell (p50 0.579 to 0.700 s against
the adjacent base3; interval excludes zero). Likely cause: the wake answers with a
full workspace build (snapshot, asset fingerprint, diagram/table status) that competes
with serving the reloaded page; `/__status` costs about 1 ms. Not proven (one profile).

**Decision:** C is **not recommended as prototyped** (plan rule: latency regression
beyond noise). Next experiment: a held `/__status?wait=` (cheap answer on wake) that
returns when the next 1 s workspace scan is due, i.e. 2 req/s idle instead of 4, with
the same latency path as PR #38 minus the poll phase. Command: rerun section 2 of
`phase-b-plan.md` for that variant plus base on a quiet machine (load average < 2);
adopt it if save_visible is not worse beyond noise and idle is ~2 req/s; otherwise
keep PR #38's cadence and document the 3 extra requests/s as an accepted cost.

**Proposed PR:** none yet for front 6 (see decision).

## Not verified (all points)

- Linux and Windows behavior of all diffs.
- The phase B numbers on a quiet machine: load rose from 1.5 to up to 14.9 during the
  session; C2 and AC2 on the load paper are inconclusive for that reason.
- The cause of C2's example latency regression (full workspace build at wake is a
  hypothesis from code and one trace).
- Whether the < 0.9 s assertions in the Chrome idle test are stable on slow CI.
- Root cause of the two 1.48 s cold samples in the archive (base1 shows one at 1.47 s
  too).
- `make test-package`, `make test-template`, `make test-update`, acceptance: not run.
