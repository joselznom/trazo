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
| `build_graph.py:280`, `Builder.guarded` | Removes outputs changed by the failed step and re-raises. SIGKILL cannot run this cleanup; the build records and next operation are checked separately. |
| `diagram_editor.py:335`, `SharedChrome.drop` | Silently suppresses a Chrome cleanup error. Open audit finding: a resource failure may be hidden even though the initiating error is surfaced elsewhere. |
| `diagram_editor.py:359`, `SharedChrome.check` | Drops the broken browser and re-raises; HTTP reports the failure. |
| `local_server.py:175`, `GuardedHandler.reply` | HTTP 500 JSON with the error; the server stays alive. |
| `mcp.py:216`, `Server.call_tool` | MCP `isError` reply; the session stays alive. |
| `mcp.py:285`, `Server.run` | JSON-RPC internal-error reply; the session stays alive. |
| `new.py:74`, `create` | Removes staging and re-raises. |
| `preview_edit_review.py:247`, `check_outputs` | Silently omits an invalid table from drift checks. Open audit finding; the table endpoint's error does not make this omission visible in the drift check. |
| `preview_locate.py:30`, `lookup` | Optional matching degrades to a space. No source write; a programming error could be hidden. Retained, explicitly limited fallback. |
| `preview_sources.py:215`, `locate_source` | Optional generated-value matching degrades to an empty mapping. No source write; retained fallback. |
| `preview_watch.py:22`, `render_spec_tables` | Now marks every document unsuccessful with an actionable table error and retains the last good page. Retry remains pending. Corrected in the table-error PR. |
| `preview_watch.py:43`, `regenerate_tables` | Captures generator failure as a table error and exposes it in preview state. |
| `preview_watch.py:103`, `build_all` | Sets the failed document's error, preserves its last good version and continues watching. |
| `preview_watch.py:197`, `watch` | Outer watcher exceptions still only print to the server log. Open audit finding; the table/render paths above cover specific failures, not every exception here. |
| `review_storage.py:52`, `write_atomic` | Removes its temporary file on an ordinary exception and re-raises. Atomic rename is not a multi-file transaction or power-loss durability. SIGKILL may leave an unused temporary file. |
| `table_editor.py:276`, `publish` | Rolls back a newly created table and re-raises. A rollback failure could mask the initiating error; this was not reproduced in the bounded campaign. |

The three remaining silent-error findings are the outer watcher, table drift
checking and browser cleanup. The two optional matching fallbacks are separate
from those failures and do not acknowledge a source save.

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

A first build during an edit can contain the earlier snapshot; the next build
incorporates the change. Single-build snapshot coherence remains unproven.
Persistent disk/permission/tool failures must be repaired in the environment;
automatic recovery means the next ordinary operation succeeds after that fault
is removed. ENOSPC was injected through file APIs; the owner's disk was not filled.
