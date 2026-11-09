# Type-check baseline and strict-module plan

Evidence: `.build/open-points/typecheck/` in the `t3-investigate-open-workflows` worktree (local, git-ignored). Prototype worktrees (`wt/`, `wt-*`) were not retained; their changes are the `*.diff` files there, all against b775566 and never integrated. Back to the [decision summary](../open-points.md).

## 7. 148 preexisting mypy errors, no enforced gate

### Status

Still open, confirmed. The 14 strict modules are clean today, but nothing enforces it:
no CI workflow, hook or script runs mypy (grep of `.github/`, `.githooks/`, `scripts/`,
`Makefile`). The pyproject comment "so it cannot regress" is aspirational.

### Reproduction

`make typecheck` would write `packages/trazo/.mypy_cache` into the checkout (not in
`.gitignore`), so the equivalent command ran with the cache in scratch:

```
cd packages/trazo && uv run --quiet mypy --cache-dir .build/open-points/typecheck/mypy-cache
-> Found 148 errors in 37 files (checked 80 source files); mypy exit 1 (make reports 2)
```

mypy 2.4.0 (as locked). `--platform linux` gives the identical 148 lines (so the Linux
CI runner would see the same set); `--platform win32` gives 150 (the platform class flips:
fcntl/select errors instead of msvcrt/ctypes ones). Zero errors in the strict modules,
`review_history` included.

### Root cause and confidence

Classification of all 148 (high confidence; each site read in code, `classified.tsv`):

| Class | Errors | Tiers (high/med/low) | Typical site |
|---|---|---|---|
| O4 correlated Optionals mypy cannot link | 32 | 18/14/0 | `build_graph.docx`: `document_build` not None implies `document_docx` not None |
| R variable reused with another type | 30 | 4/25/1 | `table.py` `at`, `source`, `name`; `table_model.span`; `cli.module` |
| A empty or heterogeneous literal needs an annotation | 21 | 12/9/0 | `out = []`, `keys = set()`, `[("text", x), ("value", x, y)]` |
| O1 Optional return whose contract is non-None | 15 | 15/0/0 | `mcp_calls.load(args)` typed `Project \| None` though `required=True` raises |
| P platform branch via `os.name` (mypy only understands `sys.platform`) | 11 | 3/8/0 | `portable.py`, `browser.py` msvcrt/ctypes/STARTUPINFO |
| S stdlib Optional or union return used unchecked | 11 | 8/3/0 | `ET.find`, `re.search`, `server_address` |
| J untyped JSON value (`dict.get` gives `Any \| None`) into typed code | 9 | 5/2/2 | `crossref.inlines`, `mcp_preview.running` (relies on catching TypeError) |
| H untyped module state or constant dicts | 8 | 4/4/0 | `preview.STATE`, `WATCHER`, crossref `DEFAULTS`/`CHILDREN`, `mcp` tools table |
| O2 lifecycle-Optional attribute | 8 | 7/0/1 | `Chrome.proc/reader = None` in `__init__`, set later |
| O3 Popen pipe Optional | 1 | 1/0/0 | `child.stdin.close()` |
| B None-initialised counter | 1 | 0/1/0 | `counts.Counter.yaml_abstract` |
| V unvalidated request field | 1 | 0/1/0 | `review_comments.ids_field` |

By tier: 77 high, 67 medium, 4 low. By module (top): build_graph 17 (high), mcp_preview 17
(high), table 16 (medium), browser 10 (high), portable 8 (medium), docx_indexes 8 (high),
crossref, mcp, preview_captions 7 each. Full table: `classify.py` output and `classified.tsv`.

Runtime versus noise: 137 are annotation or inference noise (the code holds an invariant
mypy cannot see). The rest hide real defects, reproduced on b775566:

1. **counts: `None += int` (class B, medium, plausible).** `yaml_abstract` starts as None
   and becomes 0 only when a `div.abstract` *starts* inside a `<header>`; `in_abstract` is
   true for any `<header>` inside such a div. Markdown `::: abstract` + raw `<header>x</header>`
   + `:::` makes Pandoc emit exactly that (checked with `pandoc -t html`).
   `count_page('<main><div class="abstract"><header>Hello world</header></div>...', [])`
   -> `TypeError: unsupported operand type(s) for +=: 'NoneType' and 'int'`. The word-count
   panel and `trazo counts` fail on that manuscript.
2. **preview_captions: clearing a table note crashes (class S, medium, plausible).**
   `with_table_note` finds the note with `preview_edits.NOTE_CLOSE` (`\s*$`, so `\r`,
   NBSP, form feed pass) but re-finds the closing fence with `[ \t]*$`, then calls
   `close.end()` on None. `with_table_note(text, "tbl:x", "")` with the closing line
   `:::\r` (one CRLF line in an LF file), `::: ` or `:::\x0c` ->
   `AttributeError: 'NoneType' object has no attribute 'end'`; the server answers 500
   "server error". Nothing is written (it fails before `change_source`), so no data loss.
   A file that is fully CRLF is not located at all (Conflict); that is a separate limit.
3. **review_comments: missing id gives 404, not 400 (class V, medium, minor).**
   `ids_field({"op": "edit"})` -> `[None]`; the store then raises
   `NotFound("no comment with id None")`. A non-string id (`7`) is also accepted and 404s.
4. **docx_indexes on a malformed DOCX (class S, high, low plausibility).** A marker
   paragraph followed by a `w:tbl` without `w:tblPr` -> `AttributeError: 'NoneType' object
   has no attribute 'find'`, not caught by `build`/`verify`. Pandoc always writes `tblPr`
   and `w:body` (OOXML requires them), and only trazo emits the marker, so this needs a
   custom `documents.X.build` writing broken OOXML. Owner decision; I would not fix it now.

Everything else (all of O1 to O4, R, A, H, J except the cases above) is noise: I read
each site and found the invariant held by surrounding code (for example
`mcp_preview.running` deliberately catches the TypeError of `None["pid"]`).

### Recommended solution

Do not sweep. Gate first, then fix the 3 defects, then remove root causes in the order that
unlocks the most promotions per line of diff. Each PR has one root cause and leaves the
invariant "every module under the strict override reports zero errors" true.

| # | PR (one root cause) | Files | Tier | Diff | Errors gone | Invariant after merge |
|---|---|---|---|---|---|---|
| 1 | Enforce the strict list: `make typecheck-strict` (mypy, fail on any error in a module of the override; prototype `strict_gate.py`, 15 lines, stdlib tomllib), run in the CI lint job; add the already clean `preview_block_patch`, `table_cli`, `table_hand` | Makefile, .github/workflows/ci.yml, a script, pyproject | high | ~40 | 0 | CI fails when a strict module has an error; 17 modules listed |
| 2 | counts abstract counter | counts.py + test_counts | medium | ~10 | 1 | defect 1 regression test passes |
| 3 | table-note close fence | preview_captions.py (use `locate_table(...)["stop"]`, the offset already computed with the same regex) + test | medium | ~8 | 1 | defect 2 test passes |
| 4 | comment id validation | review_comments.py: `[text_field(request, "id", required=True)]` + test | medium | ~3 | 1 | defect 3 test passes; module promotable (2 left: annotations) |
| 5 | Platform branches via `sys.platform` | portable.py, browser.py (prototype.diff: +55/-42) | high (browser) | ~100 | 11 (darwin, linux); portable clean on win32 too | portable zero on `--platform` darwin, linux, win32, listed (needs one `bool()` for its last strict error); gate also runs `--platform win32` |
| 6 | `load` overload (`required: Literal[True]` returns `Project`) | mcp_calls.py (prototype: +5 lines) | high | ~6 | 15 | mcp.py/mcp_preview.py have no `Project \| None` errors; `mcp_app.load_paper` workaround becomes removable (separate PR) |
| 7 | Near-clean setup modules: missing annotations/type args | builds, dependencies, doctor, manifest, numbering (3 strict errors each) | high | ~20 | 0 (strict only) | five modules listed |
| 8 | Re-export chain | updates imports `no_window` from portable, not machine; machine `no-any-return` | high | ~3 | 0 | machine listed without breaking `updates` |
| 9 | Chrome lifecycle Optionals | browser.py (proc/reader typed once set; methods narrow), diagram_editor | high | ~25 | 8 | browser listed (after 5) |
| 10 | build_graph correlated Optionals | one guard (`output is None` -> BuildError) or one Project method returning `(command, output)` | high | ~12 | 14 | build_graph has no `Path \| None` errors |
| 11 | Variable reuse in table/table_model | renames only | medium | ~30 | 21 | table_model promotable after annotations |

After 5 and 6 the prototype worktree reports 122 errors in 36 files (measured).
Later promotions go bottom-up in the import graph: 599 of the 1,203 errors under a
package-wide strict run are `no-untyped-call`, so annotating a callee (for example
`preview_sources.get_source/change_source/source_path`, `preview_edits.markdown_context`)
clears its callers (`preview_block_format`, `edit_diff`, `preview_mentions`).

Two promotion traps found: `no_implicit_reexport` applies to the exporting module, so
promoting `machine` breaks strict `updates` (it imports `no_window` through machine) and
promoting `mcp` breaks strict `preview_inputs` (`ToolError`); `table` would break
`table_cli`/`table_hand` (`rel`). Each such PR must fix the importer in the same change.

CI gate: yes, per module, not global. The global run stays report-only (`make typecheck`);
the gate fails only on modules in the override, so it can land today with zero code
changes and each promotion PR extends it. Running it in the Linux lint job is enough for
darwin too (identical output); after PR 5 add a `--platform win32` pass so Windows-only
branches, which mypy then skips on Linux, stay checked. Prototype check: the gate returns
0 on b775566 and 1 with an injected error in `resources.py` (then reverted).

### Rejected alternatives and why

- Global cleanup or `strict = true`: 1,203 errors in 67 files; mostly untyped defs, high
  review cost for no runtime gain.
- Global `allow_redefinition_new` + `local_partial_types`: removes 27 R errors but adds 2
  and changes checking for every module; a global config change (high tier) to save
  renames in two medium files. I did not check its stability status in the mypy 2.4 docs.
  Owner decision; renames preferred.
- `# type: ignore` per site: hides the three real defects' siblings and `warn_unused_ignores`
  already polices them; only for genuine stub gaps.
- `assert sys.platform == "win32"` or an early `raise` inside a function: tested, mypy still
  reports `Name "msvcrt" is not defined`; only an `if`/`else` on `sys.platform` works.
- Running mypy with `ignore_errors` for non-strict modules as the gate: works, but loses the
  report from `make typecheck`; the filter keeps one mypy run for both.

### Checkable invariants

- `make typecheck-strict` exits 0 and CI lint runs it (after PR 1).
- Each promotion PR: the module is listed under `[[tool.mypy.overrides]]` and the gate is
  green on `--platform linux` (and win32 after PR 5).
- Total from `make typecheck` never grows (report it in each PR, as now).

### Portability and risks

`os.name == "nt"` and `sys.platform == "win32"` select the same CPython builds (Cygwin and
MSYS2 are both posix and non-win32), so PR 5 keeps runtime behavior; only verified on
macOS (focused tests) and by mypy for linux/win32. Windows behavior not run: the windows
workflow must pass. With `sys.platform` checks, mypy skips the other platform's branch, so
the win32 pass is what keeps it checked.

### Test that would fail before the fix

- PR 2: `test_counts`: `count_page` on Pandoc's HTML for `::: abstract` containing a raw
  `<header>` returns counts (today TypeError).
- PR 3: `with_table_note(text, id, "")` where the closing fence line ends with `\r` removes
  the note (today AttributeError).
- PR 4: a comment edit request without `id` answers 400 "id is required" (today 404).
- PR 1: the gate script returns 1 for an error injected in a strict module (shown in scratch).

### Verification needed

`make lint`, `make test-package`, `make test-template`, `make typecheck` (count) and the
new gate for each PR; for PR 1/5 the CI run on Linux, macOS and the windows workflow.

### Measured impact or cost

mypy run under 1 s warm, a few seconds cold: cheap enough for the lint job and even
pre-push. Error deltas above are measured in the prototype worktree.

### Proposed PR(s) with risk tier

As in the table: 1, 5 to 10 high (Makefile/.github/pyproject, browser, mcp_calls,
setup modules, build_graph); 2, 3, 4, 11 medium.

### Not verified

Windows and Linux runtime of the platform prototype (mypy only); the full test suite on
the prototype (ran `test_browser`, `test_build_lock`, `test_mcp` only); whether the
`<header>` in an abstract case occurs in real papers; the mypy docs status of
`allow_redefinition_new`.
