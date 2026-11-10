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
| `review_storage.py:52`, `write_atomic` | Removes its temporary file on an ordinary exception and re-raises. Atomic rename is not a multi-file transaction or power-loss durability. SIGKILL may leave an unused temporary file. |
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
