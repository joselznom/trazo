# Independent writers during a source save

Evidence: `.build/open-points/writers/` in the `t3-investigate-open-workflows` worktree (local, git-ignored). Prototype worktrees (`wt/`, `wt-*`) were not retained; their changes are the `*.diff` files there, all against b775566 and never integrated. Back to the [decision summary](../open-points.md).

## Point 1: text loss between the locked read and publication

### Status
Fixable on APFS (and, by code reading, Linux ext4/xfs/btrfs/tmpfs) with an
atomic-exchange protocol: prototype reduces measured loss to 0. Narrowed but
**still open** on filesystems without exchange (exFAT, FAT, HFS+, network,
Windows). The open-descriptor late writer is preserved on APFS only for a bounded
grace window; after it, a documented limit.

### Reproduction
```
uv run --quiet --project packages/trazo python scripts/measure/fixture.py .build/open-points/writers/papers/example        # exit 0
uv run --quiet --project packages/trazo python scripts/measure/scenarios.py .build/open-points/writers/papers/example \
  .build/open-points/writers/repro-b775566 --scenario concurrent_writers --scenario concurrent_atomic_writers               # exit 1
```
Both FAIL at b775566: HTTP 200, marker absent from file and history
(`repro-b775566/report.json`), site `before_replace | portable.py:46:replace >
review_storage.py:51:write_atomic > preview_sources.py:166:change_source > preview.py:361`.

### Root cause and confidence (high, reproduced)
`packages/trazo/src/trazo/preview_sources.py` `change_source`:
- :133 takes `review_lock` (`review_storage.py:28`, flock on `notes/review/.lock`);
- :135 locked read, :157 hash check against `base_sha256` (409 otherwise);
- :165 `save_file_history` writes the undo JSON (widens the window);
- :166 `write_atomic` (`review_storage.py:42-54`: mkstemp, write, chmod,
  `portable.replace` :37-46 = `os.replace`).

`os.replace` unlinks whatever is at the name at rename time, with no comparison.
A writer that ignores the lock and finishes after :135 is overwritten; the
displaced inode is gone. Undo (:148) and edit-review reject
(`preview_edit_review.py:210`) have the same window.

### Recommended solution
`review_storage.publish_over(path, data, expected, name) -> Publication` (prototype),
called by `change_source` for save and undo:
1. Write the new bytes to a sibling `NAME.<rand>.swap`.
2. `portable.exchange(tmp, path)`: macOS `renamex_np(RENAME_SWAP)`, Linux
   `renameat2(RENAME_EXCHANGE)`, via ctypes. The displaced file is now at `tmp`.
3. Displaced bytes == bytes read under the lock: clean. Park the displaced file
   in `notes/review/displaced/NAME.<ns>.<sha>` (same inode) for 600 s.
4. Different: exchange back. The independent version is published again; the
   browser bytes go to `notes/review/conflicts/`; if the swap-back displaced a
   third version (inode differs from ours) it is kept too. The history entry is
   removed, `absorb` is not called, response **409** with `kept` and `conflicts`.
5. `sweep()` under the lock (prototype: in every `source_state`): a parked file
   whose hash changed becomes a `late` conflict copy; unchanged ones are deleted
   after the grace. A `.swap` orphan (SIGKILL) is deleted only when its bytes
   equal the current file or a history `before_text`, else kept as `recovered`.
   The undo history acts as the journal; no new store.
6. Exchange is called **only on an allowlisted filesystem**: on macOS `statfs`
   `f_fstypename == "apfs"`. Reason (reproduced): on an FSKit FAT32 image,
   `renamex_np` left a process in uninterruptible state (`sample` shows the stack
   in `renamex_np`), and it cannot be killed. "Call and catch ENOTSUP" is unsafe.
7. Without exchange, the fallback (`exact: false`) compares the file just before
   `os.replace` and keeps a descriptor on it. After the rename it re-reads that
   descriptor and keeps any in-place write it finds there (response 200 with
   `conflicts`). On Windows the descriptor is closed before the rename.

"External wins" (step 4) was chosen over "browser wins + copy". It matches
today's pre-read 409 exactly (same banner, same edit-review state). Swapping back
also republishes the inode an in-place writer still holds, so its later writes
land in the live file.

### Rejected alternatives and why
- (b) Hard link plus a comparison after the rename: an atomic replacement between
  `link` and `rename` is still lost (same residual as the fallback). It also needs
  link support, which exFAT/FAT lack. It is useful only as a fallback addition on
  HFS+ to park late writers.
- (c) Detection after publication only: an atomic replacement is unrecoverable
  once renamed over. It detects but never preserves.
- (d) Contract-only: honest, but it leaves the 280/500 to 477/500 loss rates
  measured below on common filesystems.
- Second hash check before the rename, or moving the file aside first: settled
  before this front, not revisited.
- Windows `ReplaceFileW` with `lpBackupFileName`: the backup does preserve the
  displaced file, but ReplaceFile is not atomic and has documented half-done
  states. Plan it as the Windows variant ("browser wins + external kept", 200
  with conflict) only after a Windows experiment (see Verification).

### Checkable invariants
- I1 (exchange FS): every version present at the path at any instant between the
  locked read and the response is afterwards at the path or in `conflicts/`.
- I2: a 200 save's `sha256` equals sha256 of the bytes sent (Point 2).
- I3: a race 409 adds no history entry and no edit-review absorb; `can_undo` is false.
- I4: trazo never deletes a file whose bytes are not the current file, a history
  `before_text`, or a kept copy (parked, `.swap` and conflict files).
- I5: after SIGKILL at any reached site, old text is in the file or history; the
  next GET sweeps unique `.swap` bytes into `conflicts/`.
- I6: `exchange` is never called on a non-allowlisted filesystem; fallback
  responses carry `exact: false`.

### Portability and risks
| Platform / FS | Exchange | Measured or source |
|---|---|---|
| macOS APFS (internal, external SSD) | yes | `swap.py` on both: swapped |
| macOS HFS+, exFAT, FAT32 images | ENOTSUP (errno 45) | `swap.py` |
| macOS FSKit FAT32 under concurrent rename and rm | **hang (state U)** | process 18686, stack in `renamex_np` |
| macOS SMB/NFS | not tested | allowlist routes to fallback |
| Linux ext4/xfs/btrfs/tmpfs | renameat2 documented | code reading, not run |
| Linux NFS/CIFS/FAT/FUSE | EINVAL expected | not run |
| Windows NTFS | none | fallback; ReplaceFileW plan unverified |

Other risks:
- An extra `displaced/` file per save for 10 min (each one manuscript file in size).
- `notes/review/` is git-ignored, so conflict copies are never committed.
- If `notes/review` is on another filesystem than `manuscript/`, parking falls
  back to a copy and late writes are lost.
- Sync clients (iCloud, Dropbox) are realistic independent writers; exchange
  covers their atomic replacements.

### Test that would fail before the fix
`wt/packages/trazo/tests/test_preview_source_writers.py` (4 tests). The window is
entered by wrapping `preview_sources.save_file_history` (no production seam).
On b775566 (guarded copy `before-tests/`): 4 FAIL/ERROR (`200 != 409` in-place
and atomic, orphan kept, `KeyError: 'conflicts'`). On the prototype: 4 OK. The
behavioral check is the unchanged harness: prototype `concurrent_writers` and
`concurrent_atomic_writers` **PASS**, 409, marker on disk, browser text in
`notes/review/conflicts/`, 0 history entries, no `.swap` left (`repro-prototype/`).

### Verification needed
- Linux: run `swap.py` and `race.py` on ext4, tmpfs, NFS and FUSE. Expected:
  exchange works, or EINVAL leads to the fallback. A hang would require a
  filesystem-type allowlist via `/proc/self/mountinfo`.
- Windows NTFS: the fallback race, plus ReplaceFileW with a backup, an editor
  holding the file open, and SIGKILL between its steps. Expected: sharing
  violations block most in-place late writers. Decide between ReplaceFileW and
  the fallback.
- A browser test for the UI (Point 1 UI below).
- The `faults.py` rule for site 009 (see Measured impact).

### Measured impact or cost
`race.py`, 500 iterations per cell: a separate writer process, a 1 ms simulated
history write, and a writer delay uniform in [0, 2 ms]. Cells show lost
independent writes (silent):

| FS | protocol | in-place | atomic | late fd writer |
|---|---|---:|---:|---:|
| APFS | baseline | 280 | 245 | 500 |
| APFS | exchange | **0** (283 got 409) | **0** (251 got 409) | **0** (500 kept as late) |
| APFS | fallback | 2 | 8 | 500 |
| exFAT | baseline / fallback | 468 / **0** | 241 / **45** | 500 / 500 |
| HFS+ | baseline / fallback | 289 / 6 | 230 / **34** | 500 / 500 |

Browser text was never lost by trazo. The `browser_overwritten_after_200`
counts are the independent writer's own read-before/write-after lost update; the
edit review shows it as a pending deletion (code reading).

`faults.py --operation source` on the prototype: 23 PASS, 2 FAIL at site 009
(the post-publication parking rename under ENOSPC/EACCES). The prototype then
leaves the `.swap` file for the sweep and answers 200. The save did publish, and
the displaced bytes equal undo history, so no text is lost. The harness rule
"injected failure reported as success" flags it. **Owner decision:** accept a
post-publication housekeeping exception in the harness, or drop parking (that
gives up late-writer protection). Timings were not measured (shared machine).

### UI behavior and degradation
- Race 409: the existing `.pv-src-conflict` banner (Reload, View diff,
  Overwrite) stays. The message becomes "Another program changed this file while
  it was being saved. Its version is on disk; your text is still here and a copy
  is at <kept>." The tab stays dirty.
- `conflicts` non-empty on GET or save (late, recovered, fallback): a persistent,
  non-blocking Source-tab notice, "trazo kept text another program wrote at the
  same moment: <path>", with Open and Dismiss. Dismiss needs confirmation and
  only then deletes; trazo never auto-deletes a copy.
- `exact: false` (no exchange: exFAT, FAT, HFS+, network, Windows): once per
  session per folder, "This disk cannot swap files atomically. trazo checks for
  other programs' writes just before saving, but a write at that exact instant
  can still be lost. Avoid saving while an agent edits the same file."
- Recovery after SIGKILL: the next GET of that file shows the notice. A product
  version should sweep every source root at preview start and expose a count in
  `/__status`.

### Proposed PR(s)
1. `fix:` the save response hash (Point 2), with its test. Files
   `S/preview_sources.py` and a test: **medium**.
2. `feat:` add `portable.exchange` with the APFS/Linux allowlist and
   `review_storage.publish_over` plus `sweep`, used by `change_source` save and
   undo, with 3 tests and the `fault_io.py` exchange hook. Paths are medium;
   **raise to high**: it is a data-preservation and concurrency contract plus a
   native call proven able to hang on an untrusted filesystem.
   `review_storage` is under strict mypy (prototype: no new errors).
3. `feat:` UI notices (`S/assets/preview/preview-source.js`): low by path,
   **medium** by judgment, since they carry the contract to the user.
4. `fix:` apply `publish_over` to edit-review reject (`preview_edit_review.py:210`),
   and decide whether table and diagram spec saves need it: medium.
5. `docs:` rewrite `docs/measurement-writer-limits.md` and the README promise to
   the new contract. The harness expectation for site 009 goes in PR 2.

### Not verified
Linux, Windows and network filesystems (code reading only). Behavior of FSKit
exFAT under heavy concurrency; the FAT hang's exact trigger (a concurrent
`rm -rf` of the folder was running). Browser UI (not prototyped). Real agent
writers (Claude Code, Codex): whether they write in place or atomically was not
checked. Performance.

## Point 2: a write right after publication poisons the browser's base hash

### Status
Still open at b775566; a fixable defect, found here; fixed in the prototype.

### Reproduction
`postpublish.py` pauses save 1 at `after_replace` (preview_sources.py:166), an
independent writer appends in place, then save 2 is sent with the returned hash,
as `preview-source.js:256` does (`b.sha = payload.sha256`). b775566:
`save1_sha_is_sent_text: false`, `save2_status: 200`, marker gone from the file
(only in history `before_text`) (`pp/pp-race.json`). Prototype: save 2 **409**,
marker kept (`pp/proto-race.json`).

### Root cause and confidence (high, reproduced)
`change_source` :168 re-reads the file after publishing and returns that hash.
The browser pairs it with its own text, so its next save passes the check against
a version it never saw.

### Recommended solution
Return `source_state(file, path, new, text)` for saves, built from the bytes
written. A write after publication then yields 409 at the next save.

### Rejected alternatives
Browser-side hashing of its sent text: duplicates the server's encoding rules;
the server knows the bytes.

### Checkable invariants, test, PR
I2 above. `test_write_right_after_publication_makes_the_next_save_conflict` fails
on b775566 (`200 != 409`) and passes on the prototype. PR 1, medium.

### Portability, cost, not verified
Platform-independent; no cost. Undo responses keep the re-read: they return text
and hash from the same read, so they are consistent.

## Limits that need writer cooperation (not fixable by trazo)
- A writer holding a descriptor that writes after the 600 s grace window, or
  after its displaced file was copied across filesystems, writes into a deleted
  file. The next watcher poll sees nothing, because the published file never
  changed. Only the writer's own error, if any, reveals it.
- Without exchange, an atomic replacement in the microseconds between the final
  check and `os.replace` is lost silently (measured 8 to 45 per 500 under a
  forced race).
- A read-modify-write by the independent writer that spans trazo's publication
  overwrites the browser's just-saved text. That is the writer's own lost
  update. trazo shows it in the edit review (code reading) and at the next save
  (409, after Point 2).
