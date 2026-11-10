# Failure boundaries after the workflow corrections

This is the human disposition of the 19 broad handlers in the measured product.
The phase 1 inventory and the final generated call-site inventory cover local
atomicity, timeouts and exception boundaries. Static candidates are not proof of
runtime coverage. The final campaign covers 85 reached Python publication/write
boundaries, not every call in the inventory or writes inside native executables.

## Broad handlers

Paths below are relative to `packages/trazo/src/trazo/`; locations refer to the
combined product. Owners remain useful when line numbers move.

| Handler | User-visible behavior and disposition |
| --- | --- |
| `background.py:79`, `CommandJob.run` | The owning job records an error and terminates the child. Its cleanup `wait()` still has no local timeout; the universal bounded-process invariant remains open. |
| `browser.py:141`, `Chrome.__init__` | Cleans up and re-raises; does not suppress the original failure. |
| `browser.py:249`, `Chrome.target` | Cleans up and re-raises. |
| `browser.py:300`, `Chrome.discard` | Prints a failed close to the server log, never to the page status; `Chrome.close` reaps Chrome before it removes the profile whatever the close reply was, so the failure leaks neither the process nor the profile. Corrected in the silent-errors PR. |
| `build_graph.py:280`, `Builder.guarded` | Removes outputs changed by the failed step and re-raises. SIGKILL cannot run this cleanup; the build records and next operation are checked separately. |
| `diagram_editor.py:331`, `SharedChrome.drop` | Closes the shared Chrome through `Chrome.discard` (above): one log line, no status noise, the next check starts a new Chrome. Corrected in the silent-errors PR. |
| `diagram_editor.py:359`, `SharedChrome.check` | Drops the broken browser and re-raises; HTTP reports the failure. |
| `local_server.py:175`, `GuardedHandler.reply` | HTTP 500 JSON with the error; the server stays alive. |
| `mcp.py:216`, `Server.call_tool` | MCP `isError` reply; the session stays alive. |
| `mcp.py:285`, `Server.run` | JSON-RPC internal-error reply; the session stays alive. |
| `new.py:74`, `create` | Removes staging and re-raises. |
| `preview_edit_review.py:247`, `check_outputs` | Skips only a table whose spec or data cannot be read (`OSError`): the page build reports an unreadable spec and the next table render an unreadable data file. An output that is not UTF-8 (a hand edit re-saved in another encoding) counts as edited by hand, as it does for `trazo table check --outputs` and the table editor's status, and the next table render rewrites it (`review_storage.holds_text`), after which the note clears; any other exception reaches the watcher boundary below. Limit: unreadable data with no source change is reported only at the next table render. Corrected in the silent-errors PR. |
| `preview_locate.py:30`, `lookup` | Optional matching degrades to a space. No source write; a programming error could be hidden. Retained, explicitly limited fallback. |
| `preview_sources.py:215`, `locate_source` | Optional generated-value matching degrades to an empty mapping. No source write; retained fallback. |
| `preview_watch.py:22`, `render_spec_tables` | Now marks every document unsuccessful with an actionable table error and retains the last good page. Retry remains pending. Corrected in the table-error PR. |
| `preview_watch.py:43`, `regenerate_tables` | Captures generator failure as a table error and exposes it in preview state. |
| `preview_watch.py:116`, `build_all` | Sets the failed document's error and preserves its last good version. The pass then counts as failed (`PageFailed`): the error stays on that page only, is printed once, and only that page is retried with the watcher's backoff below, so fixing a permission alone brings it back. Corrected in the silent-errors PR. |
| `preview_watch.py:159`, `build_and_watch`, and `:269`, `watch` | An unexpected failure of the first builds or of a watcher pass sets every page's `error` (last good page kept, as for table errors), prints one log line per distinct error and leaves the watcher running. It is retried at once on a new change (table outputs and data a failed generator wrote do not count), otherwise after 1, 2, 4 ... 30 s; table work that failed stays pending, requested rebuilds are put back, and the first complete pass rebuilds every page, reruns the drift check and clears the error, so no change made before or during the failure is skipped. Corrected in the silent-errors PR. |
| `review_storage.py:79`, `write_atomic` | Removes its temporary file on an ordinary exception and re-raises. Atomic rename is not a multi-file transaction. SIGKILL leaves the temporary file, locked while it was written; the next write of the same file removes it once it is 600 s old and unlocked (storage limits below). |
| `table_editor.py:276`, `publish` | Rolls back a newly created table and re-raises. A rollback failure could mask the initiating error; this was not reproduced in the bounded campaign. |

The three silent-error findings (outer watcher, table drift checking and
browser cleanup) are corrected in the silent-errors PR. The two optional
matching fallbacks are separate from those failures and do not acknowledge a
source save.

## Scope of the new deadline

MCP tools that use `Calls.run` have a positive finite deadline, 1200 seconds by
default. Timeout kills the owned process group, bounds pipe draining to five
seconds, includes captured output and leaves the same MCP session usable. The
real detached-descendant test proves that a pipe held by a deliberately detached
process cannot block the reply indefinitely. That process is outside the owned
group and survives until the test cleans it up.

Standalone CLI builds still reach unbounded calls in `build_graph.py`, metadata
reads in `frontmatter.py` and environment work in `uv_sync.py`. The existing
preview watcher and custom table commands are not brought under the MCP deadline
once the tool has returned. Default build-lock waits are also unbounded outside
the preparation path that sets a 20-second wait. Some shutdown paths use an
unbounded reap after termination. These are open boundaries, not successful
proof of the universal no-hang invariant.

Windows avoids closing pipes owned by still-running reader threads. A detached
process can keep such a daemon thread alive. The changed Windows path and Linux
process-group behavior were not run locally; all measured evidence is macOS.

## Viewer page loads

A served page (document, table editor, diagram editor) loads about 45 files
over separate HTTP/1.0 connections. On Windows CI about one test in 600 lost
one script: the request failed inside Chrome (status 0) without reaching the
server, and the page stayed half-started. That network layer is not
identified. A trazo script that fails while the page is still parsing now
shows "Viewer scripts did not load; reload the page." and reloads the page
once per tab; if it fails again the note stays and nothing reloads. A failure
after the page has started never reloads it, so typed text is not lost.
Checked on macOS by resetting one script's connection in Chrome; the Windows
rate after the fix is not measured. A failed wait in the browser tests now
prints the failed resources with their status and the server's request log.

## Storage and recovery limits

Table and diagram history is now persisted before source publication. Pending
entries reconcile on the next operation: an unpublished save restores the prior
undo entry, and a published save retains the previous text. Failed grouped
autosaves preserve the prior group. Manuscript saves and edit rejections are
recorded "pending" the same way and settled on the next read, so a failed or
killed save leaves undo as it was. History keeps a size/count limit (200 entries
and 5 MB for tables and diagrams, 50 saves and 20 MB for the manuscript); the
pending entry does not count, so a failed attempt at the limit evicts nothing,
and a store holds at most one entry over it until the next save.

Corrupt manuscript, table or diagram history is moved atomically to a unique
backup (`<name>.corrupt-<id>.json`) before any edit. The failing operation
reports the backup and retry advice; the next normal operation starts a new
history. This preserves damaged bytes, not a promise to reconstruct old undo
entries from them: salvaging entries stays a documented limit. Table and diagram
history that is valid JSON with malformed entries is not quarantined: non-object
entries are dropped silently on the next save. Keyboard undo now shows the same
error.

A writer killed before its rename leaves `<name>.<8 characters>.tmp` beside the
file, and a killed Word build leaves `output/.trazo-*` with an unfinished DOCX.
Each is locked while in use (the folder through its `.lock` file); the next write
of the same file, or the next Word build, removes those older than 600 s that
nobody holds, the same grace as the parked files of a source save. A writer
suspended for 10 minutes in the microseconds before it locks its file, or between
closing it and the rename, loses it and fails visibly (`FileNotFoundError`, the target
untouched). On network or synced folders another machine's lock
is invisible. Temporaries of files never written again stay; new papers'
`.gitignore` hides both temporary patterns. Not covered: `trazo setup`'s
`.setup-*` folder and `trazo new`'s staging folder after a kill (code reading).

Durability: user data is synced (`portable.sync`): file and folder `fsync` for
histories, comments, the edit review's baseline, conflict copies and the files
`trazo update` writes, and
`F_FULLFSYNC` on macOS for the files a save publishes (sources, table specs,
diagrams), since macOS `fsync` leaves data in the drive's cache. Build outputs
are not synced: a build makes them again. Measured with `perf_counter` on APFS
(20 kB, 200 samples, a shared machine): `write_atomic` 0.15 ms unsynced, 0.19 ms
with `fsync`, 7.0 ms with `F_FULLFSYNC`; `source_publish.publish` 2.8 ms before,
10 ms after. Power loss itself is bounded by this policy, not proven: no forced
power-off was run, Linux and Windows were not measured, and a filesystem that
refuses a sync (some network or FUSE volumes) falls back to `fsync` or nothing.

A `trazo update` killed after adding an executable left it at 0644, and the
rerun compares content only; a new executable now gets its mode before it takes
its name. Coverage gaps still open, none a demonstrated bug: faults inside
native executables (Pandoc, Chrome, `uv sync`), fault injection during `trazo
setup` and `update` beyond that case, each browser editing mode's handling of a
500 or 409, the fault harness on Linux and Windows, and power loss.

Independent writers that ignore `review_lock` no longer lose text to a
concurrent source, table or diagram save on filesystems with atomic exchange (APFS; Linux ext4,
XFS, Btrfs and tmpfs): the save swaps its file in, compares what it displaced
and, on a difference, swaps back and answers 409 with the browser text kept in
`notes/review/conflicts/`. Both harness scenarios pass on APFS. Elsewhere the
check runs just before `os.replace` and replies say `exact: false`. The
[contract and its limits](measurement-writer-limits.md) name what is still lost:
a late write through a file held open past 600 seconds, a write at the instant
of the rename without exchange, and a writer's own read-modify-write.

A Word build snapshots its watched inputs (content hash, mtime, size, inode)
before reading them and again just before publishing; on any difference it
discards the result, retries once, then fails and keeps the earlier DOCX. Steps,
the table render and the diagram export compare pre-run and post-run snapshots
and write no record when an input changed. So a published DOCX and its stamp
describe one state of those inputs, and undoing a mid-build edit rebuilds it.
Not covered: an edit undone with its mtime and inode restored, files outside the
watched set (paths Pandoc resolves by itself, a step's undeclared reads), and
documents a `[documents] build` command builds (mtime freshness).
Persistent disk/permission/tool failures must be repaired in the environment;
automatic recovery means the next ordinary operation succeeds after that fault
is removed. ENOSPC was injected through file APIs; the owner's disk was not filled.
