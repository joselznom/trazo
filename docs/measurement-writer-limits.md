# Independent writer limits

A source save from the preview (the Source tab, writing on the page, captions,
undo and rejecting an edit) and a table or diagram spec save (the table and
diagram editors, their undo, `trazo table` and `trazo diagram` patches) now keep
the text of a program that ignores trazo's lock, such as an agent, an editor or
a sync client, on filesystems that can swap two files atomically. Elsewhere they
narrow the loss to one instant and say so. This is not a universal no-loss
guarantee.

## The contract

A save reads the file under `review_lock` and rejects with HTTP 409 any edit
completed before that read. `source_publish.publish` then covers the interval
between that read and the reply:

- **Atomic exchange** (macOS APFS: `renamex_np` with `RENAME_SWAP`; Linux ext4,
  XFS, Btrfs and tmpfs: `renameat2` with `RENAME_EXCHANGE`). The new file is
  swapped in and the displaced file is compared with the bytes read under the
  lock. On a difference the two are swapped back: the other program's version
  stays at the path, the text trazo meant to write is copied to
  `notes/review/conflicts/<file>/`, no undo entry is recorded and the reply is
  the usual 409 conflict banner. A matching displaced file is parked in
  `notes/review/displaced/` for 600 seconds, so a program still holding the
  file open writes into a kept file; the next read of that file moves a changed
  one to `conflicts/`. The exchange is called only on these filesystem types,
  never tried elsewhere: on a FAT volume `renamex_np` hung the calling process.
- **No exchange** (exFAT, FAT, HFS+, network filesystems, Windows, other Linux
  filesystems). The file is compared just before `os.replace`, and an in-place
  write that lands between that check and the rename is read back through a
  descriptor and kept. Replies carry `exact: false` and the Source tab says once
  per session that a write at that instant can still be lost.

**Contract on exchange filesystems:** no version present at the path between
the locked read and the reply is lost, except a late write through a file held
open past the 600-second grace window, and a writer's own read-modify-write
that spans the save. On APFS this is shown by the runs below. On Linux it rests
on the kernel's documented `renameat2` semantics and on CI: the Linux job runs
the package tests, which read the filesystem type of a known mount, swap two
files where the type is allowed and run the collision tests. No forced race was
run on Linux, and the CI log does not say which path a runner's disk took.

A save's 200 reply carries the sha256 of the bytes it wrote, not of a re-read.
The browser keeps that hash as its base, so a write landing right after the
save makes the next save a 409 instead of overwriting it.

## Limits

- A write through a descriptor opened before a save, made after the grace
  window, or after a parked file was copied to another disk, goes into a
  deleted file. Only the writer itself can notice.
- Without exchange, a write in the instant between the final check and the
  rename can be lost silently.
- An independent writer that reads, waits and writes back over trazo's
  just-saved text loses trazo's text. That is the writer's own lost update; the
  edit review shows it as a pending deletion and the next browser save is a 409.
- If the review folder is on another filesystem than the manuscript, a matching
  displaced file is not parked, so late writes through an open file are lost.
- Post-publication housekeeping: when parking fails (a full disk, a read-only
  folder) the save is published and answers 200 with a `warning`, shown by
  every editor that saves a source or a spec and printed by the `trazo table`
  and `trazo diagram` commands. The replaced text stays in undo history,
  and the replaced file stays beside the source as a `.swap` for the same 600
  seconds (measured from its ctime, which the exchange updates: checked on
  APFS, not on Linux), so a writer holding it open still writes into a kept
  file. The fault harness accepts exactly this case, checking that the new text
  is published and the old text is in undo history.
- Power loss: either path flushes the new file to the disk before it takes the
  name and the folder after (`F_FULLFSYNC` on macOS, about 7 ms more per save),
  so a power loss should leave one whole version. This bounds the loss by that
  policy; no forced power-off was run ([storage limits](measurement-boundaries.md#storage-and-recovery-limits)).
- Linux and Windows were not run locally; network filesystems were not tested.

## Evidence

Forced race of the real `change_source` (separate writer process, the writer
told to go right after the locked read, a 1 ms slower history write, writer
delay uniform in [0, 2] ms, 500 iterations per cell, APFS, macOS arm64). Lost
independent writes, silent:

| Protocol | In-place | Atomic | Late, through an open file |
| --- | ---: | ---: | ---: |
| Before (6a60168) | 288 | 267 | 500 |
| Exchange (APFS) | 0 (307 got 409) | 0 (276 got 409) | 0 (500 kept as late copies) |
| No exchange, simulated | 4 | 12 | 500 |

The simulated row reports an `exfat` filesystem type to trazo on the same APFS
disk; no disk image was mounted. Browser text was never lost by trazo: every
409 left a copy in `conflicts/`.

The same race against the table editor save, the diagram editor save and the
table editor's undo (`review_history.History.publish` and `revert_file`), with
the writer keeping the spec valid JSON. Saves add the same 1 ms to the history
write; the undo keeps its natural window, so it loses less before the fix:

| Path, protocol | In-place | Atomic | Late, through an open file |
| --- | ---: | ---: | ---: |
| Table save, before | 324 | 284 | 500 |
| Table save, exchange | 0 (308 got 409) | 0 (298 got 409) | 0 (500 kept) |
| Table save, no exchange | 2 | 11 | 500 |
| Diagram save, before | 303 | 275 | 500 |
| Diagram save, exchange | 0 (317 got 409) | 0 (288 got 409) | 0 (500 kept) |
| Diagram save, no exchange | 2 | 11 | 500 |
| Table undo, before | 15 | 0 | 500 |
| Table undo, exchange | 0 (30 got 409) | 0 (16 got 409) | 0 (500 kept) |
| Table undo, no exchange | 2 | 11 | 500 |

A spec collision answers the editors' existing 409 (the table editor's
"changed on disk" banner, the diagram editor's Keep mine and Load theirs), and
both banners say where the copy of the unsaved edit is. A diagram save now answers
the sha256 of the bytes it wrote, as a source save does. A new spec (a table
typed by hand, `base_sha256` null for a diagram) is still created with a plain
rename: there is no existing file to exchange with. Undoing such a creation
deletes the file after its hash check, so a write in that instant is lost.

## Reproduction

Run from this checkout, using a generated example paper as `SEED`:

```sh
uv run --quiet --project packages/trazo python scripts/measure/scenarios.py "$SEED" .build/measure/writer-limits --scenario concurrent_writers --scenario concurrent_atomic_writers
uv run --quiet --project packages/trazo python scripts/measure/faults.py "$SEED" .build/measure/save-faults --operation source --operation table --operation diagram
```

Both scenarios pause a real HTTP save at its publication, write the file from a
separate process (in place, or a temporary file and `os.replace`), and resume
the request. They pass when the reply is 409, the other writer's text is on disk
and the browser's text is in a kept copy. They were run on APFS. On a disk
without exchange the pause lands after the final check, the instant this
contract leaves open, so the atomic case is expected to fail there. The tests in
`packages/trazo/tests/test_preview_source_writers.py`,
`test_preview_browser_source_writers.py` and `test_spec_writers.py` cover the
same cases at the HTTP and browser boundaries, for sources and for specs.
