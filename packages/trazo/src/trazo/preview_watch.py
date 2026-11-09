"""Watching the sources and rebuilding the pages, regenerating tables first when they changed.

The preview's paths and state stay in trazo.preview, and this code reads them from
there when it runs (preview.NAME), so patching them on trazo.preview reaches it.
"""

import subprocess
import time

from . import dependencies, labels, preview_edit_review, table
from .build_lock import build_lock, child_env


def render_spec_tables() -> bool:
    """Render spec tables, keeping the last page and reporting any failure in its status."""
    from . import preview
    try:
        with build_lock(preview.PROJECT, lambda text: print(f"[preview] {text}", flush=True)):
            tables = table.render_all()
        failed = [t for t in tables if t.markdown is None]
        error = "; ".join(f"{t.label}: " + "; ".join(i["message"] for i in t.errors) for t in failed)
    except Exception as exc:  # a broken spec must not stop the watcher or look like success
        error = f"{type(exc).__name__}: {exc}"
    if error:
        with preview.LOCK:
            for state in preview.STATE.values():
                state.update(ok=False, error=f"tables: {error}", built=time.strftime("%H:%M:%S"))
    print(f"[preview] tables: {error or 'rendered'}", flush=True)
    return not error


def regenerate_tables():
    """Rerun the tables stage (or command) after a spec change; report a failure."""
    from . import preview
    log = lambda text: print(f"[preview] {text}", flush=True)  # noqa: E731
    # The tables stage writes build records and outputs: wait for a build running meanwhile.
    with build_lock(preview.PROJECT, log):
        if not preview.PROJECT.command("tables"):
            from . import build_graph
            try:
                build_graph.Builder(log=log).tables()
                error = ""
            except Exception as exc:  # the generator's own error, or a broken spec
                error = f"tables: {exc}"
        else:
            command = preview.PROJECT.command("tables")
            try:  # child_env: a trazo the command calls runs in this turn
                result = subprocess.run(command, cwd=preview.ROOT, capture_output=True, text=True,
                                        env=child_env())
            except OSError as exc:
                result = subprocess.CompletedProcess([], 127, "", f"could not run the tables command: {exc}")
            lines = [line for line in result.stderr.splitlines() if line.strip()]
            error = "" if result.returncode == 0 else (
                "tables: " + (lines[-1] if lines else f"exited with {result.returncode}"))
    if error:
        with preview.LOCK:  # keep the last good page; show why the tables did not update
            for state in preview.STATE.values():
                state.update(ok=False, error=error, built=time.strftime("%H:%M:%S"))
    print(f"[preview] tables: {'error' if error else 'regenerated'}", flush=True)
    return not error


def own_tables_stage(project):
    """Whether the project runs its own tables stage: a [[build.steps]] entry
    named `tables` or a [commands] tables entry (trazo.build_graph)."""
    return bool(project.command("tables")) or any(step["name"] == "tables" for step in project.config["build"]["steps"])


def snapshot():
    """path -> (mtime, size, inode) of every watched file.

    Size and inode catch writes that land in the same mtime tick (Linux
    file timestamps advance in steps of a few milliseconds) and atomic
    replacements.
    """
    from . import preview
    return dependencies.snapshot(preview.ROOT, preview.WATCHED)


def changed_documents(changed):
    from . import preview
    targets = [name for name in preview.DOCS if any(
        path.is_relative_to(folder) for path in changed for folder in preview.PROJECT.document_roots(name))]
    if not targets or any(not any(path.is_relative_to(folder) for name in preview.DOCS
                                 for folder in preview.PROJECT.document_roots(name)) for path in changed):
        return list(preview.DOCS)
    return targets


def numbered():
    """Every document's labels and numbers, as the pages built in the
    current labels.once() block used them: not labels.json, which a check
    in another thread may have refreshed meanwhile."""
    from . import preview
    return {name: entry["labels"] for name, entry in labels.index(preview.PROJECT).items()}


def build_all(names):
    from . import preview
    for name in names:
        try:
            preview.build(name)
        except Exception as exc:  # as the renderer's errors: synced must not hide it
            with preview.LOCK:  # the page keeps its last good version
                preview.STATE[name].update(ok=False, error=f"{type(exc).__name__}: {exc}",
                                           built=time.strftime("%H:%M:%S"))
            print(f"[preview] {name}: error {exc!r}", flush=True)


def report_failure(exc: Exception, last: str = "") -> str:
    """Show an unexpected watcher failure on every page, which keeps its
    last good version; print it unless it is `last`. The message."""
    from . import preview
    message = f"preview watcher: {type(exc).__name__}: {exc}"
    with preview.LOCK:
        for state in preview.STATE.values():
            state.update(ok=False, error=message, built=time.strftime("%H:%M:%S"))
    if message != last:  # a lasting failure is printed once, not on every retry
        print(f"[preview] watcher error: {exc!r}", flush=True)
    return message


def build_and_watch(stop_event):
    """Build every page, then watch them: `trazo preview serve` runs it
    while it serves. A failure here must not end the thread: the watcher
    is what notices the fix."""
    from . import preview
    before = preview.snapshot()  # a change during the first builds still rebuilds
    numbers, failure = None, ""
    try:
        with labels.once():  # one label index for every first page and their numbers
            for name in preview.DOCS:
                if stop_event.is_set():
                    return
                build_all([name])
            numbers = numbered()
        preview_edit_review.check_outputs()
    except Exception as exc:  # watch() retries it
        failure = report_failure(exc)
    preview.watch(stop_event=stop_event, previous=before, numbers=numbers, failure=failure)


def watch(interval=0.5, stop_event=None, previous=None, numbers=None, failure=""):
    """Rebuild the pages on every change since `previous` (default: now).
    `numbers`: what numbered() gave in the block that built them (default:
    read now). `failure`: what report_failure() said when the first builds
    failed. A failed table render stays pending across prose edits; after
    recovery every page rebuilds so none of those edits is skipped.

    A pass that fails for any other reason keeps its changes and shows the
    error on every page; it is retried at once when the sources change
    again, otherwise after 1, 2, 4 ... 30 s, and once it completes every
    page is rebuilt."""
    from . import preview
    previous = snapshot() if previous is None else previous
    # The numbers the pages used; unknown when sources changed during the
    # first builds or while reading them, so the first rebuild then covers
    # every page.
    tables_pending, render_pending = False, False
    recover = bool(failure)  # the next complete pass rebuilds every page
    try:
        shown = numbered() if numbers is None else numbers
    except Exception:  # the first pass reads them again, and reports a failure
        shown, recover = None, True
    if snapshot() != previous:
        shown = None
    failures, retry_at, failed_on = 0, 0.0, None
    while True:
        if stop_event is None:
            time.sleep(interval)
        elif stop_event.wait(interval):
            return
        base, requested, current, checked = previous, set(), None, time.time()
        try:
            current = snapshot()
            if failure and current == failed_on and time.monotonic() < retry_at:
                preview.WATCHER["synced"] = checked  # the pages already show the error for these sources
                continue
            with preview.LOCK:  # pages asked for although no source changed (accepted edits)
                requested, preview.WATCHER["rebuild"] = preview.WATCHER["rebuild"], set()
            if current == previous and not requested and not recover:
                preview.WATCHER["synced"] = checked  # preview_start waits for it (trazo.preview_inputs)
                continue
            changed = {p for p in current.keys() | previous.keys()
                       if current.get(p) != previous.get(p)}
            previous = current
            if own_tables_stage(preview.PROJECT) and any(
                    p.is_relative_to(preview.ROOT / preview.PROJECT.relative("table_specs")) for p in changed):
                tables_pending = True  # the project's generator may read the specs
            specs = [preview.ROOT / preview.PROJECT.relative(key) for key in preview.TABLE_SPEC_SOURCES]
            if any(p.is_relative_to(folder) for p in changed for folder in specs):
                render_pending = True
            rendered = False
            if render_pending and not tables_pending:
                if not preview.render_spec_tables():
                    # The table error replaces the watcher's, and its recovery rebuilds every page.
                    failure, failures, recover = "", 0, False
                    continue  # unrelated prose edits must not hide the table error
                render_pending, rendered = False, True
                output_dir = table.OUTPUT_DIR
                # Absorb the rendered Markdown so it does not trigger a second build.
                previous.update({p: stamp for p, stamp in snapshot().items()
                                 if p.is_relative_to(output_dir)})
            if tables_pending:
                if not preview.regenerate_tables():
                    failure, failures, recover = "", 0, False  # as for a failed render above
                    continue  # keep the error visible until the text is fixed
                tables_pending = render_pending = False
                # Absorb generated outputs only. Source edits made while the
                # generator was running must still trigger the next rebuild.
                outputs = [preview.ROOT / preview.PROJECT.relative(key) for key in ("table_output", "table_data")]
                generated = lambda p: any(p.is_relative_to(d) for d in outputs)  # noqa: E731
                previous = {p: stamp for p, stamp in previous.items() if not generated(p)}
                previous.update({p: stamp for p, stamp in snapshot().items() if generated(p)})
                targets = list(preview.DOCS)
            else:
                targets = list(preview.DOCS) if rendered else changed_documents(changed) if changed else []
            if recover:  # every page shows the failure until it builds again
                targets = list(preview.DOCS)
            targets += [name for name in preview.DOCS if name in requested and name not in targets]
            # One label index for the pages built now: an edit meanwhile
            # changes the snapshot, and the next pass rebuilds with a new one.
            with labels.once():
                build_all(targets)
                # A document cites another's numbers: when they
                # change, the other pages are rebuilt too. `shown` is what the
                # pages last used.
                if (now := numbered()) != shown:
                    build_all([name for name in preview.DOCS if name not in targets])
            shown = now
            # A spec or data change re-rendered the outputs above; only an output changed by itself may differ.
            if recover or any(p.is_relative_to(table.OUTPUT_DIR) for p in changed):
                preview_edit_review.check_outputs()
            if failure:
                print("[preview] watcher recovered", flush=True)
            failure, failures, recover = "", 0, False
        except Exception as exc:  # keep watching; every page says why it is old
            previous = base  # the changes of this pass are still to show
            with preview.LOCK:
                preview.WATCHER["rebuild"].update(requested)
            failure = report_failure(exc, failure)
            preview.WATCHER["synced"] = checked  # the status describes these sources: its error says the page is old
            failed_on, recover, failures = current, True, failures + 1
            retry_at = time.monotonic() + min(30.0, 2.0 ** (failures - 1))
