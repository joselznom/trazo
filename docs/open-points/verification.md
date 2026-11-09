# Coordinator cross-check

Six investigation agents produced the annexes in this folder. The coordinator read
every annex against the code and reran the key failing-first tests independently,
on a clean detached checkout of `b775566` and on each prototype. macOS arm64 only.
Back to the [decision summary](../open-points.md).

## Tests rerun

Base runs use the agents' guarded copies (`before-tests/`, `baseline-tests/`), which
use only names that exist at `b775566`. The final prototype tests import new names,
so on `b775566` they fail at import, which proves nothing; those runs were discarded.

| Test module | `b775566` | Prototype |
| --- | --- | --- |
| `test_preview_source_writers` (point 1, 1b) | 4 failures, 1 error (`200 != 409`, `KeyError: 'conflicts'`) | 4 OK |
| `test_build_coherence` (point 4) | 3 failures (`'1' != '2'`, Word file published from changed inputs, no failure after repeated changes) | 3 OK |
| `test_history_limits` (point 9) | 2 failures (oldest entry evicted in both stores) | 2 OK |
| `test_atomic_orphans` (point 9) | 1 failure (`2 != 1`, killed writer's temp kept) | 1 OK |
| `test_watcher_failures` (point 2) | 2 failures | 2 OK |
| `test_drift_check` (point 2) | 1 failure | 1 OK |
| `ShellTests.test_a_build_between_serving_the_page_and_its_first_status_still_reloads` (5.3) | FAIL after 18.4 s, page never shows the third build | OK |

Command shape, from the checkout under test:

```sh
PYTHONPATH=packages/trazo/tests:<folder with the test file> uv run --quiet --project packages/trazo python -m unittest <module>
```

The 5.3 test was applied to the clean checkout with `git apply` and reverted
afterwards (`git status` clean). Logs: `.build/open-points/verify/`.

## Facts checked by reading

- `preview_sources.change_source` returns `source_state` from a re-read after
  `write_atomic` (point 1b).
- `counts.count_page('<main><div class="abstract"><header>Hello world</header></div>...', [])`
  raises `TypeError: unsupported operand type(s) for +=: 'NoneType' and 'int'` at
  `b775566` (point 7).
- No workflow, hook or script runs mypy (point 7).
- `.github/workflows/windows.yml` runs the package tests on windows-2025. One annex
  said CI covers only Linux and macOS; `process-bounds.md` was corrected.
- Phase B numbers in `open-points.md` match `runs/cmp-*.json`.

## Not done

`make test-package`, `make test-template`, `make test-update` and acceptance were
not run on any prototype: none is integrated. `make lint` passed on this checkout,
which changes documentation only.
