# Latency and idle measurements, phase B

Evidence: `.build/open-points/perf/` in the `t3-investigate-open-workflows` worktree (local, git-ignored). Prototype worktrees (`wt/`, `wt-*`) were not retained; their changes are the `*.diff` files there, all against b775566 and never integrated. Back to the [decision summary](../open-points.md).

## Phase B results (same-session A/B comparisons)

These runs compare variants with each other in one session on this machine. They are
**not** a replacement for the 2026-11-07 archive, and their absolute numbers must not
be read against the archived budget table without this caveat. Fixture: generated
from `wt-base` (`fixture.py`); the load paper's input hashes equal the archive's
`fixture-load.json`, the example paper's do not (not investigated).

**Machine state, recorded not fixed:** the owner's own sessions ran throughout
(a `trazo mcp --serve 8765` process at ~3% CPU, several claude CLI processes, Dropbox,
the owner's Chrome); two stuck processes from another agent (PID 18686 race.py,
PID 19986 `rm -rf`, uninterruptible, 0% CPU, FAT32 image /dev/disk10) were left alone.
Load average (1 min) before/after each variant, from `runs/uptime.log`:

| Variant | Before | After |
| --- | ---: | ---: |
| base1 | 1.51 | 3.54 |
| A | 3.41 | 4.10 |
| B | 4.10 | 3.83 |
| C (v1) | 3.83 | 7.28 |
| AC (v1) | 7.28 | 4.14 |
| base2 | 4.14 | 14.86 |
| C2 (C v2) | 4.20 | 10.76 |
| AC2 (A + C v2) | 10.76 | 9.88 |
| base3 | 9.88 | 6.38 |
| idle base / C2 / A | 6.38 / 4.82 / 3.35 | 4.82 / 3.35 / 3.61 |

Load rose during the session, so later variants ran on a busier machine than base1:
gains against base1 are, if anything, understated; regressions of later variants may
be load. **Drift guard:** base2 vs base1 passes no improvement gate in any cell
(`runs/cmp-base2-{load,example}.json`), so per the brief no pair was rerun; base2 and
base3 do show p95 regressions (load cold p95 1.866 to 2.497 and 2.458 s), which is why
C2/AC2 on the load paper are judged inconclusive and C2 is compared with the adjacent
base3 as well.

Seconds, p50/p95, 20 observations each (`runs/<variant>-<paper>/results.json`):

| Paper | Variant | preview_start cold | preview_start warm | save_visible cold | save_visible warm |
| --- | --- | ---: | ---: | ---: | ---: |
| load | base1 | 1.509/1.534 | 0.429/0.695 | 1.851/1.866 | 0.910/0.927 |
| load | A | 1.524/1.549 | 0.431/0.695 | **1.282/1.334** | 0.903/0.927 |
| load | B | 1.530/1.594 | 0.475/0.801 | **1.049/1.362** | 0.909/1.191 |
| load | C v1 | 1.533/1.594 | 0.427/0.708 | 2.375/2.479 | 1.195/1.255 |
| load | AC v1 | 1.535/1.765 | 0.432/0.677 | 1.177/1.384 | 1.175/1.195 |
| load | base2 | 1.548/1.645 | 0.415/0.726 | 1.906/2.497 | 0.914/1.243 |
| load | C2 | 1.516/1.557 | 0.415/0.697 | 1.948/2.047 | 0.928/1.152 |
| load | AC2 | 1.724/3.021 | 0.512/0.867 | 2.282/2.824 | 1.292/1.622 |
| load | base3 | 1.540/1.670 | 0.431/0.690 | 2.036/2.458 | 1.122/1.363 |
| example | base1 | 1.451/1.477 | 0.515/0.665 | 1.117/1.133 | 0.578/0.847 |
| example | A | 1.456/1.475 | 0.515/0.665 | **0.632/0.649** | 0.593/0.844 |
| example | B | 1.446/1.473 | 0.515/0.649 | **0.313/0.318** | 0.577/0.830 |
| example | C v1 | 1.473/1.581 | 0.497/0.902 | 1.083/1.149 | 1.096/1.194 |
| example | AC v1 | 1.458/1.484 | 0.515/0.649 | 1.089/1.100 | 1.086/1.114 |
| example | base2 | 1.461/1.539 | 0.477/0.680 | 1.165/1.659 | 0.596/0.850 |
| example | C2 | 1.615/1.823 | 0.495/1.030 | 1.241/1.568 | 0.700/0.813 |
| example | AC2 | 1.467/1.669 | 0.444/0.688 | 0.606/0.852 | 0.657/0.818 |
| example | base3 | 1.457/1.505 | 0.532/0.663 | 1.132/1.166 | 0.579/0.829 |

compare.py (2,000 resamples, gate = interval above zero and >= 5%), save_visible:

| Comparison (`runs/cmp-*.json`) | Cell | p50 change, interval | p95 change, interval | Gate p50/p95 |
| --- | --- | --- | --- | --- |
| A vs base1, load | cold | -30.7%, [0.546, 0.584] | -28.5%, [0.518, 0.552] | pass/pass |
| A vs base1, load | warm | -0.7%, [-0.015, 0.019] | 0.0%, [-0.022, 0.017] | no change |
| A vs base1, example | cold | -43.4%, [0.484, 0.500] | -42.7%, [0.482, 0.499] | pass/pass |
| A vs base1, example | warm | +2.6%, [-0.148, 0.023] | -0.4%, [-0.049, 0.034] | no change |
| B vs A, load | cold | -18.2%, [0.201, 0.257] | +2.1%, [-0.068, 0.240] | pass/fail |
| B vs A, example | cold | -50.5%, [0.311, 0.332] | -51.0%, [0.247, 0.335] | pass/pass |
| B vs base1, load | warm | -0.1%, [-0.016, 0.014] | +28.4%, [-0.281, 0.014] | no change (3 outliers) |
| C v1 vs base1, load | cold / warm | +28.3% / +31.4% | +32.8% / +35.4% | regression |
| C2 vs base3, load | cold / warm | -4.3% / -17.3% | -16.7% / -15.5% | fail / p95 warm pass; inconclusive under load |
| C2 vs base3, example | cold | +9.7%, [-0.216, -0.065] | +34.5% | regression |
| C2 vs base3, example | warm | +20.9%, [-0.171, -0.045] | -1.9%, [-0.162, 0.070] | p50 regression |
| AC2 vs base3, example | cold | -46.4%, [0.419, 0.600] | -26.9%, [0.196, 0.522] | pass/pass (from A) |
| base2 vs base1 (drift) | all | no cell passes | p95 regressions up to +34% | guard holds |

(Change = after vs before; negative is faster. Intervals are compare.py's
before-minus-after deltas in seconds.)

preview_start (guard): A, B and C change it by at most 1 to 2% p50 on load, the same
size as the base2 drift (+2.6%); no variant is better or worse beyond that drift.
C2 example cold p50 +0.158 s vs base3 is within this session's load noise but not
excluded as a C cost.

Idle (`runs/idle-{base,C2,A}.json`, load paper, 20 windows of 2 s, p50/p95):

| Metric | base | C2 | A |
| --- | ---: | ---: | ---: |
| workspace requests/s | 0.999/1.000 | 0.999/0.999 | 0.999/0.999 |
| status requests/s | 2.998/2.999 | **0.000/0.000** | 2.997/2.998 |
| server CPU % of one core | 1.999/3.496 | 3.496/5.493 | 3.247/4.498 |
| renderer main thread % | 0.362/0.692 | 0.129/0.629 | 0.525/0.922 |

A does not touch idle behavior, yet its server CPU differs from base as much as C2's
does: server CPU differences here are load noise and no CPU claim is made.

Diagnostic profiles (`runs/profile-{a,save,idle}/profile-summary.json`, one sample,
instrumented): A's second manuscript render starts at +0.531 s, 0.03 s after the first
builds end (base: a 0.59 s gap); B's starts at +0.569 s, right after the first
manuscript render, before the other documents; C's watcher timeline is unchanged
(+1.216 s), as expected.

**Decisions (plan table):**
- **A (5.2 + 5.3): adopt.** Load cold save_visible 1.851/1.866 to 1.282/1.334 s,
  both gates pass, p95 under 1.5 s in this session; example cold 1.117 to 0.632 s;
  warm and preview_start unchanged; no timeouts (all 18 runs exit 0). PR: "fix: show
  a save made while the preview opens without waiting a poll", medium.
- **B (5.4): include, claiming p50 only on the load paper.** B vs A passes the p50
  gate on both papers and p95 on example; load p95 is not established (three slow cold
  samples of 1.29 to 1.38 s under load average ~4). Low risk, same PR as A.
- **C (front 6): not adopted** as prototyped (see 6). Idle target met (4 to 1 req/s),
  latency not.
- **AC:** not a shipping estimate; C's latency cost carries into it.

**Commands run (phase B), all passed (exit 0):**
`git worktree add --detach` for `wt-base` and `wt-ac`; `git apply` of the diffs;
`fixture.py` for both papers from `wt-base`; `runs/bench.sh <variant> <worktree>`
(= `run.py PAPER OUT --operation preview_start --operation save_visible` for load and
example) for base1, A, B, C, AC, base2, C2, AC2, base3; `compare.py` for every row
above; `idle.py` for base, C2, A; `run.py --smoke --samples 1 --profile` plus
`analyze_profiles.py` for A, B, C2. Functional after the C v2 fixes, in `wt-idle`:
`test_preview_browser_shell`, `test_preview_browser_mcp_app`, `_parity`,
`test_workspace`, `test_preview_browser_figures`, `test_preview_browser_writing`
(52 tests, OK, twice); ruff as `make lint`: OK.
