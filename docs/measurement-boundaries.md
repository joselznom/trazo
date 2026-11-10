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
| `background.py:73`, `CommandJob.run` | The job runs through `processes.run`: it ends when its command exits (a process that keeps the log open is stopped after 5 s and noted in the log), at its deadline, or on cancel; the group is stopped with SIGTERM, then SIGKILL after the job's grace, and every wait has a bound. Corrected in the process-bounds PR. |
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
| `preview_watch.py:43`, `regenerate_tables` | Captures generator failure, or the tables command running past its 1200 s deadline (stopped with its group), as a table error and exposes it in preview state. A build lock held elsewhere fails the pass, which the watcher retries. |
| `preview_watch.py:116`, `build_all` | Sets the failed document's error and preserves its last good version. The pass then counts as failed (`PageFailed`): the error stays on that page only, is printed once, and only that page is retried with the watcher's backoff below, so fixing a permission alone brings it back. Corrected in the silent-errors PR. |
| `preview_watch.py:159`, `build_and_watch`, and `:269`, `watch` | An unexpected failure of the first builds or of a watcher pass sets every page's `error` (last good page kept, as for table errors), prints one log line per distinct error and leaves the watcher running. It is retried at once on a new change (table outputs and data a failed generator wrote do not count), otherwise after 1, 2, 4 ... 30 s; table work that failed stays pending, requested rebuilds are put back, and the first complete pass rebuilds every page, reruns the drift check and clears the error, so no change made before or during the failure is skipped. Corrected in the silent-errors PR. |
| `review_storage.py:52`, `write_atomic` | Removes its temporary file on an ordinary exception and re-raises. Atomic rename is not a multi-file transaction or power-loss durability. SIGKILL may leave an unused temporary file. |
| `table_editor.py:276`, `publish` | Rolls back a newly created table and re-raises. A rollback failure could mask the initiating error; this was not reproduced in the bounded campaign. |

The three silent-error findings (outer watcher, table drift checking and
browser cleanup) are corrected in the silent-errors PR. The two optional
matching fallbacks are separate from those failures and do not acknowledge a
source save.

## Scope of the deadlines

Every program trazo runs goes through one runner, `processes.run`, extracted
from the MCP deadline (`Calls.run` keeps its 1200-second `TRAZO_MCP_TIMEOUT`
contract). Each command runs in its own group (POSIX: a new session; Windows:
a job object, nested inside its parent's) and has a finite deadline: Pandoc
600 s per document across both cross-reference stages (`TRAZO_PANDOC_TIMEOUT`),
metadata and form-field reads 60 s, `pandoc --version` 30 s, `uv sync` 1800 s
(`TRAZO_UV_TIMEOUT`), the preview's tables command 1200 s
(`PREVIEW_TABLES_TIMEOUT`) and a paper's own steps and commands 3600 s
(`[build] timeout`; 0 turns it off). On expiry, Ctrl+C, SIGTERM or SIGHUP the
group is stopped (SIGTERM or SIGINT, then SIGKILL after 3 s; a nested trazo
stops its own groups first, since its owners wait 5 s) and the error names the
command and the limit. Output a descendant keeps open after the command exits
delays the result by 5 s at most: the group is killed and the result, a `Held`,
carries the real exit code. That is the one change to the MCP contract: such a
build is no longer reported as a timeout after 20 minutes. A holder outside the
group (a deliberately detached session) is left running.

The watcher takes the build lock without waiting: while another build holds it,
the pass fails with "another build of this project is running" and is retried
with the backoff above. Table saves from the preview wait 20 s for the lock,
then answer 503 and write nothing. The preview stops every command it started
when it shuts down (and Python's exit does the same for any process), so a
page's Pandoc no longer outlives it. A page whose Pandoc could not run, ran
out of time or was killed by a signal joins the watcher's 1 to 30 s retry; text
Pandoc rejects is not rebuilt until it changes.

Limits: the CLI still waits for the build lock without a bound (its holders are
now bounded, and the terminal shows the waiting line); `review_lock` waits only
on a stalled filesystem; the Rosetta `sysctl` probe keeps its leader-only
10-second timeout. Processes left behind by a command that exited normally
without holding its output are not stopped, as before. A process a command
starts in the microseconds before Windows assigns it to the job escapes it.
Evidence is macOS; Windows runs the runner and lifecycle tests in
`windows.yml`, and Linux runs them in CI.

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
autosaves preserve the prior group. History retention still has a size/count
limit; an unsuccessful attempt near that limit can evict the oldest entry.

Corrupt manuscript history is moved atomically to a unique backup before any
source edit. The failing operation reports the backup and retry advice; the next
normal operation works. This preserves damaged bytes, not a promise to reconstruct
old undo entries from them. Keyboard undo now shows the same error.

Independent writers that ignore `review_lock` remain able to overwrite, or be
overwritten by, a concurrent browser save. Both in-place and atomic variants stay
failing in the harness. The [design disposition](measurement-writer-limits.md)
explains why no incomplete hard-link or platform-specific exchange protocol is
presented as a general solution.

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
