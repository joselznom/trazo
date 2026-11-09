# Storage limits and coverage gaps

Evidence: `.build/open-points/build-storage/` in the `t3-investigate-open-workflows` worktree (local, git-ignored). Prototype worktrees (`wt/`, `wt-*`) were not retained; their changes are the `*.diff` files there, all against b775566 and never integrated. Back to the [decision summary](../open-points.md).

Head b775566, macOS arm64 (APFS), Pandoc 3.9.0.2. `S` below is
`.build/open-points/build-storage`.
Other agents shared the machine: every timing is indicative, never a budget.
Prototype worktree: `S/wt` (detached at b775566), full diff `S/prototype.diff`.
Paths in the product are relative to `packages/trazo/src/trazo/`.

## B1. Orphan temporary files after SIGKILL

- **Status:** documented limit, fixable; reproduced.
- **Reproduction:** `uv run --quiet --project packages/trazo python S/tools/orphan_probe.py S/papers/example S/orphans`
  (`S/orphans/result.json`). SIGKILL inside `write_atomic` before its rename leaves
  `manuscript/main/01_introduction.md.8i7g9ail.tmp` (source intact). SIGKILL of a build
  during an after-docx step leaves `output/.trazo-72tf10a5/manuscript.docx`, a complete
  valid Pandoc DOCX (zip test passes, 19 parts) that is not the finished file. The next
  successful build removes neither.
- **Root cause and confidence (high):** `write_atomic` (`review_storage.py:42`) names
  temps `<target>.<8 chars of [a-z0-9_]>.tmp` next to the target and cleans only on
  exceptions; `pandoc_docx` uses `TemporaryDirectory(prefix=".trazo-", dir=output.parent)`;
  `tool_setup.install` uses `.setup-*` next to the tools folder (about 100 MB archives;
  its cleanup loop removes only release-named folders, code reading). Temps under the
  system temp folder (`trazo-pandoc-*`, `trazo-counts-*`, ...) are left to the OS.
- **Confusion with outputs:** temps end in `.tmp`, so `*.md`, `*.json`, `*.png` globs
  skip them. They are hashed by folder-wide inputs (`Builder.files` on a folder,
  `diagrams()` `figures.rglob`, preview fingerprints): one harmless rebuild. Risks are
  human: `*.tmp` is not in the template `.gitignore`, so orphans in `manuscript/` show
  in `git status` and can be committed; `output/.trazo-*` holds an unfinished but valid
  DOCX in the folder users upload from (hidden in Finder).
- **Recommended solution:** in `write_atomic`, hold `try_lock` on the temp while writing
  it, and after a successful rename call `remove_orphans(path)`: delete siblings that
  match `re.escape(name) + r"\.[a-z0-9_]{8}\.tmp"`, are older than 600 s and can be
  locked. Prototyped (`S/wt/.../review_storage.py`). Same age rule for `output/.trazo-*`
  folders at the start of a non-nested build, and for `.setup-*` in `tool_setup`.
- **Proof it never deletes a live temp:** a live writer holds the flock from just after
  `mkstemp` until it closes the file; after closing it renames within microseconds, and
  its file is younger than 600 s. Demonstrated: `S/tools/temp_lock_probe.py` (lock
  refused while the writer lives, granted after SIGKILL, `S/temp-lock-probe.txt`), and
  the test below, where a live writer whose mtime was forced 11 minutes old is spared.
  The only unguarded moment is between `mkstemp` and `try_lock` (microseconds); a writer
  suspended there for 10 minutes loses its temp and its rename raises
  `FileNotFoundError`: a visible failure, the target untouched, never silent loss. On
  Windows an open file cannot be deleted at all, a second guard.
- **Rejected alternatives:** age only (a suspended or slow writer, for example across a
  laptop sleep, could lose its temp; the lock removes that case); PID in the name (PID
  reuse, meaningless on shared or synced folders); cleanup scoped by directory lock
  (`.build/labels.json` is written by the preview outside the build lock, and nested
  builds share the lock, so a directory sweep could hit a live temp).
- **Checkable invariant:** after any successful `write_atomic(path)`, no sibling temp of
  `path` older than 600 s and unlocked remains; a locked temp is never removed.
- **Portability and risks:** flock is local; on NFS/SMB or synced folders another
  machine's writer is invisible, so its temp could be removed after 600 s (its rename then
  fails visibly). Orphans of targets never written again stay; `trazo doctor` could list
  them (report only).
- **Test that fails before the fix:** `S/wt/packages/trazo/tests/test_atomic_orphans.py`;
  on b775566 (`S/baseline-tests/test_atomic_orphans.py`): `AssertionError: 2 != 1`.
- **Verification needed:** full gates; Linux CI (flock), a Windows run (msvcrt locking
  of a temp file).
- **Measured cost:** `remove_orphans` 33 microseconds median per write on an 11-entry folder (`S/remove-orphans-cost.txt`).
- **Proposed PRs:** "fix: remove temporary files of killed writers" in `review_storage.py`
  (**Medium**); the `output/.trazo-*` part touches `build_graph.py` (**High**, can join PR1);
  `.setup-*` in `tool_setup.py` (**High**); adding `*.tmp` to `template/.gitignore` is a
  template change (**High**, owner decision: it also hides a user's own `.tmp` files).
- **Not verified:** Windows and Linux behaviour; `.setup-*` orphan (code reading only).

## B2. History retention limit

- **Status:** still open, fixable; reproduced for both stores.
- **Limits:** table and diagram history `HISTORY_LIMIT = 200`, `HISTORY_BYTES = 5_000_000`
  (`review_history.py:29`); manuscript source history `FILE_HISTORY_LIMIT = 50`,
  `FILE_HISTORY_BYTES = 20_000_000` (`preview.py:193`).
- **Reproduction:** `PYTHONPATH=packages/trazo/tests:S/baseline-tests uv run --quiet --project packages/trazo python -m unittest -v test_history_limits`
  on b775566 (limits patched to 3 for speed): tables/diagrams `['save 2', 'save 3'] !=
  ['save 1', 'save 2', 'save 3']`; source history loses its oldest entry too
  (`S/baseline-tests/history-b775566-run.txt`). Extra finding, `S/baseline-tests/probe_source_undo.py`:
  after one failed source save (not at the limit) `can_undo` is False and the first undo
  answers "undo discarded: the file changed since that save" although the file did not
  change; only the second undo works.
- **Root cause, confidence high:** `History.publish` appends the pending entry and
  `save()` trims to the limits before the publication; if the write fails, `load()` drops
  the pending entry but the evicted one is gone. `change_source` appends, trims and saves,
  then writes the source; a failed write leaves an entry that never matches the file and
  there is no reconciliation.
- **Recommended solution (prototyped):** do not count the newest pending entry against
  either limit (`spare = 1`); the next load settles or drops it and the next save trims.
  For source history add `"pending": True` to the appended entry and a `resolve_pending`
  step in `read_file_history` mirroring `History.load` (drop when the file still has
  `before_sha`, settle when it has `after_sha`).
- **Rejected alternatives:** trim after a successful publication (a second commit whose
  failure or SIGKILL leaves the store at limit plus one anyway); document the limit only
  (the fix is small and local).
- **Checkable invariants:** a failed save never removes an entry that existed before it;
  the store exceeds its count limit by at most one pending entry; after a failed source
  save, `can_undo` and undo behave as before the attempt.
- **Portability and risks:** none platform-specific. The store may hold one entry over
  the byte limit until the next save.
- **Tests that fail before the fix:** `S/wt/packages/trazo/tests/test_history_limits.py`
  (both fail on b775566, pass on the prototype; probe output `S/probe-source-undo-prototype.txt`).
- **Verification needed:** full gates; `test_save_history`, `test_preview_*` pass per
  module on the prototype (`S/prototype-per-module-tests.txt`; running several preview
  modules in one process fails the same 19 tests on b775566 and on the prototype, a
  test-isolation issue, `S/baseline-same-combo.txt`).
- **Measured cost:** none measurable (one list slice).
- **Proposed PR:** "fix: keep the oldest undo entry when a save fails", **Medium**
  (`review_history.py`, `preview_sources.py`, not in the tier lists).
- **Not verified:** the edit-review "reject" path writes the source before its history
  entry (`preview_edit_review.py:207-212`): a kill between them loses that undo (code
  reading, not run).

## B3. Damaged-history quarantine preserves bytes, not meaning

- **Status:** documented limit (recommended), plus one asymmetry to fix.
- **Reproduction:** manuscript history: covered by #36 tests. Table/diagram history,
  `S/baseline-tests/probe_history_damage.py` on b775566
  (`S/baseline-tests/probe-history-damage-b775566.txt`): malformed JSON raises
  `StoreError` and blocks every table or diagram save until the user removes the file (no
  quarantine); valid JSON with odd entries silently drops non-dict entries on the next
  save and keeps dicts missing keys (an undo reaching one would raise `KeyError`, code
  reading).
- **Root cause:** `read_file_history` moves the whole file aside on any invalid entry;
  `History.load` uses `read_list` (raise) and filters per entry silently.
- **Salvage decision:** partial salvage is safe, because every undo checks `after_sha`
  against the current file before writing, so a salvaged entry can never restore wrong
  text; a broken chain only stops undo early. It is not worth it now: `write_atomic` makes
  corruption possible only through other writers (manual edits, sync conflicts, disk
  faults), undo is a convenience, the bytes are preserved for manual recovery, and a
  tolerant JSON prefix parser adds code and tests. State the limit: "a damaged history is
  kept unchanged beside the store; undo restarts empty".
- **Recommended change:** give `History.load` the same quarantine as `read_file_history`
  (move to `<name>.corrupt-<uuid>.json`, report it, let the next save start fresh), so a
  damaged table or diagram history no longer blocks saves.
- **Rejected alternatives:** salvage valid entries (above); keep blocking (inconsistent
  with the manuscript store, and the error asks the user to delete data by hand).
- **Checkable invariant:** a damaged store is never overwritten and never blocks the
  next save; its bytes stay readable at the reported path.
- **Portability and risks:** `portable.replace` already retries on Windows.
- **Test that would fail before the fix:** write malformed JSON to the table history,
  save a table: expect success and a `.corrupt-*` file; b775566 raises `StoreError`
  (probe output above).
- **Verification needed:** full gates; table editor and diagram editor browser tests.
- **Measured cost:** none.
- **Proposed PR:** "fix: quarantine damaged table and diagram history", **Medium**.
- **Not verified:** the `KeyError` path for malformed dict entries (code reading).

## B4. Coverage gaps

For each: status, then the experiment that would settle it. None is a demonstrated bug
unless marked.

- **Native executable writes (pending coverage).** Pandoc writes only into trazo's
  temporary folder and the final file goes through `write_atomic`, so a Pandoc fault
  cannot damage the published DOCX (reproduced indirectly: SIGKILL leaves the old DOCX,
  B1). Chrome returns bytes over CDP and Python writes them. `uv sync` writes `.venv` in
  place; its record is written only after success, so the next build resyncs (code
  reading). Experiment: Linux, `strace -f -e inject=write:error=ENOSPC:when=50` (and
  `inject=rename:error=EIO`) around `trazo build` and `trazo setup`; expected: nonzero
  exit, old outputs intact, next clean run succeeds. A failure there would be a defect in
  the cleanup or record order.
- **Setup and update fault injection (pending coverage, one suspected defect).**
  `update.py` writes files then the manifest last; a rerun compares content, so it
  resumes (code reading). Suspected defect: a file new in the template and executable is
  written with mode 0644 and then `make_executable` runs; SIGKILL between them leaves it
  non-executable, and the rerun ignores mode ("Only content is compared"), so it stays so.
  Fix: `write_atomic(target, data, 0o755)` for executables (`update.py:87`, `new.py`).
  Experiment: the existing `scripts/measure/fault_io.py` injector with `--fault kill` at
  each `write_atomic` and at `make_executable` during `trazo update` from an older
  template; check the tree equals an uninterrupted update after one rerun. `tool_setup`:
  kill during download leaves `.setup-*` (B1) and, between `rmtree(folder)` and
  `os.replace`, no Pandoc until setup reruns (code reading).
- **Browser editing modes (pending coverage).** The campaign injected faults at the HTTP
  save endpoints (source, table, diagram). Not covered: each client mode's handling of a
  500 or 409 (paragraph and heading editing, inline table, hand table editor, diagram
  autosave groups). Experiment: run the existing `test_preview_browser_source_edits`,
  `..._table_inline`, `..._table_hand`, `..._writing` flows with the server-side
  `os.replace` refusal used in `test_save_history.refuse_replace`; assert the unsaved text
  stays in the page, the error is shown, and a retry after removing the fault saves.
- **Linux and Windows injection (pending coverage).** CI runs Linux and macOS tests but
  not the fault harness; nothing runs on Windows. Experiment: run
  `scripts/measure/faults.py` and `scenarios.py` on ubuntu (CI job or container) and on a
  Windows runner, with SIGKILL replaced by `TerminateProcess` there. Windows-specific
  expectation to check: a DOCX open in Word makes `portable.replace` fail after its 1 s
  retry, so the build must fail visibly with the old file intact.
- **Power loss (limit; bounded only by an fsync policy).** `write_atomic` never fsyncs.
  After power loss the renamed file may be empty or old (ext4 `auto_da_alloc` mitigates
  rename-over; APFS and NTFS give no such promise). Measured here, 20 kB atomic write
  (`S/fsync-cost.txt`): no sync 0.12 ms, `fsync` file 0.14 ms, `fsync` file and folder
  0.16 ms, `F_FULLFSYNC` 7 ms (macOS `fsync` does not flush the drive cache, so only
  `F_FULLFSYNC` is durable there). Writes per operation (`S/write-counts.txt`): a forced
  build 31 (17 of them `steps.json`), a source save 2. Policy options: (1) none, document
  the limit; (2) `fsync` file plus folder for user-owned data only (sources, specs,
  diagrams, histories): durable on Linux, cheap; (3) add `F_FULLFSYNC` on macOS for those:
  about 28 ms per source save. Build outputs are regenerable and need no sync if
  `fresh()` also rejects an unreadable DOCX. Owner decision; recommended (2) with (3)
  limited to source saves. Experiment for any policy: only a VM with a forced power-off
  (or `dm-flakey` on Linux) can test it; it cannot be proven on this machine.

## Commands run (all from the repo root unless stated)

| Command | Result |
| --- | --- |
| `scripts/measure/fixture.py S/papers/load --load` and `... S/papers/example` | pass |
| `S/tools/race_cases.py S/papers/example S/race` (b775566) | ran; A1, A2 reproduced |
| same with `TRAZO_SRC=S/wt/packages/trazo/src`, output `S/race-prototype` | ran; all cases coherent |
| `S/tools/window_times.py` in `S/papers/load` | pass, exit 0 |
| `S/tools/hash_cost.py 30`, `S/tools/snapshot_cost.py` (load, example) | pass |
| `unittest -v test_build_coherence` on b775566 / on `S/wt` | 3 FAIL / 3 OK |
| `unittest -v test_history_limits` on b775566 / on `S/wt` | 2 FAIL / 2 OK |
| `unittest test_atomic_orphans` on b775566 / on `S/wt` | FAIL / OK |
| `S/baseline-tests/probe_source_undo.py`, `probe_history_damage.py` | ran (findings above) |
| `S/tools/orphan_probe.py`, `temp_lock_probe.py`, `count_writes.py`, fsync micro-benchmark | ran |
| `S/wt`: `unittest` (in groups or per module): test_build_graph, test_builds, test_docx_freshness, test_build_lock, test_numbering, test_expand, test_diagram, test_submission, test_submission_check, test_table, test_save_history, test_preview_edits, test_preview_comments, test_preview_edit_review, test_preview, test_table_editor, test_diagram_routes, test_update, test_new | all OK |
| `S/wt`: `ruff check` on changed files; `mypy` review_history, review_storage | pass; build_graph 17 errors, same as b775566 |

Skipped: `make lint`, `make test-package`, `make test-template`, `make test-update`,
acceptance harness, Linux and Windows runs, browser tests, Chrome diagram export.
