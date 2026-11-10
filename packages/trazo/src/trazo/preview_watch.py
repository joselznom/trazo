"""Watching the sources and rebuilding the pages, regenerating tables first when they changed.

The preview's paths and state stay in trazo.preview, and this code reads them from
there when it runs (preview.NAME), so patching them on trazo.preview reaches it.

The tables stages take the build lock without waiting: while another build holds
it (build_lock.Busy), their work stays pending and runs on the next tick; the
pages still rebuild, and none shows an error. The
project's tables command has 1200 seconds, or $PREVIEW_TABLES_TIMEOUT's (as
PREVIEW_DOCX_TIMEOUT for the Word job that runs the same generator).
"""

import subprocess
import time
from typing import cast

from . import dependencies, labels, preview_edit_review, processes, table
from .build_lock import Busy, build_lock, child_env
from .local_server import CHANGES


def render_spec_tables() -> bool:
    """Render spec tables, keeping the last page and reporting any failure in its status."""
    from . import preview
    try:
        with build_lock(preview.PROJECT, lambda text: print(f"[preview] {text}", flush=True), wait=0):
            tables = table.render_all()
        failed = [t for t in tables if t.markdown is None]
        error = "; ".join(f"{t.label}: " + "; ".join(i["message"] for i in t.errors) for t in failed)
    except Busy:  # not the tables' fault: the watcher retries
        raise
    except Exception as exc:  # a broken spec must not stop the watcher or look like success
        error = f"{type(exc).__name__}: {exc}"
    if error:
        with preview.LOCK:
            for state in preview.STATE.values():
                state.update(ok=False, error=f"tables: {error}", built=time.strftime("%H:%M:%S"))
        CHANGES.notify()
    print(f"[preview] tables: {error or 'rendered'}", flush=True)
    return not error


def regenerate_tables():
    """Rerun the tables stage (or command) after a spec change; report a failure."""
    from . import preview
    log = lambda text: print(f"[preview] {text}", flush=True)  # noqa: E731
    # The tables stage writes build records and outputs: never during another build.
    with build_lock(preview.PROJECT, log, wait=0):
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
                result = processes.run(command, cwd=preview.ROOT, text=True, env=child_env(),
                                       timeout=processes.env_seconds("PREVIEW_TABLES_TIMEOUT", 1200),
                                       hint="PREVIEW_TABLES_TIMEOUT sets the limit")
            except (processes.Timeout, ValueError) as exc:  # kept pending: the next spec change runs it again
                result = subprocess.CompletedProcess([], 1, "", str(exc))
            except OSError as exc:
                result = subprocess.CompletedProcess([], 127, "", f"could not run the tables command: {exc}")
            lines = [line for line in result.stderr.splitlines() if line.strip()]
            error = "" if result.returncode == 0 else (
                "tables: " + (lines[-1] if lines else f"exited with {result.returncode}"))
    if error:
        with preview.LOCK:  # keep the last good page; show why the tables did not update
            for state in preview.STATE.values():
                state.update(ok=False, error=error, built=time.strftime("%H:%M:%S"))
        CHANGES.notify()
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


class PageFailed(Exception):
    """Pages whose build raised (an unreadable section, say): each already
    shows its error, and the watcher retries them as any other failure."""

    def __init__(self, failed: dict[str, str]) -> None:
        super().__init__("; ".join(failed.values()))
        self.pages = list(failed)


def build_all(names):
    """Build each page; one whose build raises keeps its last good version
    and shows the error. {page: log line} of those that raised."""
    from . import preview
    failed: dict[str, str] = {}
    for name in names:
        try:
            preview.build(name)
        except Exception as exc:  # as the renderer's errors: synced must not hide it
            with preview.LOCK:  # the page keeps its last good version
                preview.STATE[name].update(ok=False, error=f"{type(exc).__name__}: {exc}",
                                           built=time.strftime("%H:%M:%S"))
            failed[name] = f"{name}: error {exc!r}"
    return failed


def synced(checked: float) -> None:
    """The pages show the sources read at `checked` (preview.WATCHER): a page
    holding its reload for a save hears it at once (trazo.preview_status)."""
    from . import preview
    preview.WATCHER["synced"] = checked
    CHANGES.notify()


def report_failure(exc: Exception, last: str = "") -> str:
    """Show an unexpected watcher failure on every page, which keeps its
    last good version (a PageFailed is already on its pages); print it
    unless it is `last`. The message."""
    from . import preview
    if isinstance(exc, PageFailed):
        message = line = str(exc)
    else:
        message, line = f"preview watcher: {type(exc).__name__}: {exc}", f"watcher error: {exc!r}"
        with preview.LOCK:
            for state in preview.STATE.values():
                state.update(ok=False, error=message, built=time.strftime("%H:%M:%S"))
    if message != last:  # a lasting failure is printed once, not on every retry
        print(f"[preview] {line}", flush=True)
    return message


def build_and_watch(stop_event):
    """Build every page, then watch them: `trazo preview serve` runs it
    while it serves. A failure here must not end the thread: the watcher
    is what notices the fix. A source change during these first builds ends
    them, so the watcher builds the changed page before the others."""
    from . import preview
    before = preview.snapshot()  # a change during the first builds still rebuilds
    numbers, failure = None, ""
    failed: dict[str, str] = {}
    try:
        with labels.once():  # one label index for every first page and their numbers
            for name in preview.DOCS:
                if stop_event.is_set():
                    return
                failed |= build_all([name])
                if preview.snapshot() != before:
                    # The edited page goes first: watch() sees the change at
                    # once and, as `shown` is then None, builds every other page.
                    break
            numbers = numbered()
        preview_edit_review.check_outputs()
        if failed:
            raise PageFailed(failed)
    except Exception as exc:  # watch() retries it
        failure = report_failure(exc)
    preview.watch(stop_event=stop_event, previous=before, numbers=numbers, failure=failure)


def watch(interval=0.5, stop_event=None, previous=None, numbers=None, failure=""):
    """Rebuild the pages on every change since `previous` (default: now),
    at once for one already made by the time it starts.
    `numbers`: what numbered() gave in the block that built them (default:
    read now). `failure`: what report_failure() said when the first builds
    failed. A failed table render stays pending across prose edits; after
    recovery every page rebuilds so none of those edits is skipped.

    A pass that fails for any other reason shows the error on every page
    (a page whose build raises shows its own);
    it is retried at once when the sources change again (not the table
    outputs or data a generator writes), otherwise after 1, 2, 4 ... 30 s,
    and once it completes every page it could not show is rebuilt (only
    those that raised, after a PageFailed), so no change made before or
    during the failure is skipped. A tables stage that finds another build
    holding the lock is no failure: the pages are built with the tables they
    have, and the stage runs again on the next tick."""
    from . import preview
    previous = snapshot() if previous is None else previous
    # The numbers the pages used; unknown when sources changed during the
    # first builds or while reading them, so the first rebuild then covers
    # every page.
    tables_pending, render_pending = False, False
    # After a failure (`failure` is its message) the next complete pass rebuilds every page.
    shown = numbered() if numbers is None and not failure else numbers
    at_once = snapshot() != previous
    if at_once:
        shown = None
    failures, retry_at, failed_on = 0, 0.0, None
    waiting = False  # a tables stage found another build's lock: it stays pending, said once
    again = list(preview.DOCS) if failure else []  # pages a retry rebuilds
    outputs = [preview.ROOT / preview.PROJECT.relative(key) for key in ("table_output", "table_data")]

    def generated(path):
        return any(path.is_relative_to(folder) for folder in outputs)

    def stage(run):  # a tables stage's result, None while another build holds the lock (not a failure)
        try:
            return run()
        except Busy:
            return None

    def sources(stamps):  # a failed table step may still have written its outputs
        return {path: stamp for path, stamp in stamps.items() if not generated(path)}

    while True:
        if at_once:
            at_once = False
        elif stop_event is None:
            time.sleep(interval)
        elif stop_event.wait(interval):
            return
        requested: set[str] = set()
        current, checked = None, time.time()
        try:
            current = snapshot()
            if failure and sources(current) == failed_on and time.monotonic() < retry_at:
                synced(checked)  # the pages already show the error for these sources
                continue
            with preview.LOCK:  # pages asked for although no source changed (accepted edits)
                requested, preview.WATCHER["rebuild"] = cast(set[str], preview.WATCHER["rebuild"]), set()
            if current == previous and not requested and not failure and not waiting:
                synced(checked)  # preview_start waits for it (trazo.preview_inputs)
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
            rendered, regenerated, busy = False, False, False
            if render_pending and not tables_pending:
                if (done := stage(preview.render_spec_tables)) is False:
                    # The table error replaces the watcher's (and a lock wait), and its recovery rebuilds every page.
                    failure, failures, again, waiting = "", 0, [], False
                    continue  # unrelated prose edits must not hide the table error
                if done:
                    render_pending, rendered = False, True
                    output_dir = table.OUTPUT_DIR
                    # Absorb the rendered Markdown so it does not trigger a second build.
                    previous.update({p: stamp for p, stamp in snapshot().items()
                                     if p.is_relative_to(output_dir)})
                busy = done is None
            if tables_pending:
                if (done := stage(preview.regenerate_tables)) is False:
                    failure, failures, again, waiting = "", 0, [], False  # as for a failed render above
                    continue  # keep the error visible until the text is fixed
                if done:
                    tables_pending = render_pending = False
                    # Absorb generated outputs only. Source edits made while the
                    # generator was running must still trigger the next rebuild.
                    previous = {p: stamp for p, stamp in previous.items() if not generated(p)}
                    previous.update({p: stamp for p, stamp in snapshot().items() if generated(p)})
                    regenerated = True
                busy = busy or done is None  # the pages are built with the tables they have
            targets = (list(preview.DOCS) if rendered or regenerated
                       else changed_documents(changed) if changed else [])
            # Pages asked for, and those showing a failure until they build again.
            targets += [name for name in preview.DOCS if (name in requested or name in again) and name not in targets]
            # One label index for the pages built now: an edit meanwhile
            # changes the snapshot, and the next pass rebuilds with a new one.
            with labels.once():
                failed = build_all(targets)
                # A document cites another's numbers: when they
                # change, the other pages are rebuilt too. `shown` is what the
                # pages last used.
                if (now := numbered()) != shown:
                    failed |= build_all([name for name in preview.DOCS if name not in targets])
            shown = now
            # A render above replaced the outputs it could, so a note on one edited by hand may no
            # longer hold (and a hand edit during it must still be noted); otherwise only an output
            # changed by itself may differ.
            if failure or rendered or any(p.is_relative_to(table.OUTPUT_DIR) for p in changed):
                preview_edit_review.check_outputs()
            if failed:  # retried like any failure: a fix without an edit (a permission) still shows
                raise PageFailed(failed)
            if failure:
                print("[preview] watcher recovered", flush=True)
            failure, failures, again = "", 0, []
            if busy and not waiting:
                print("[preview] tables: waiting for another build of this project", flush=True)
            waiting = busy
            if not busy:  # the pages show the sources read at `checked`: a page holds no reload for them
                synced(checked)
        except Exception as exc:  # keep watching; every page says why it is old
            # Nothing of this pass is lost: table work that failed stays pending, and
            # the retry rebuilds the pages that failed (every page after a watcher failure).
            if not isinstance(exc, PageFailed):  # after one, every page asked for was built
                with preview.LOCK:
                    cast(set[str], preview.WATCHER["rebuild"]).update(requested)
            failure = report_failure(exc, failure)
            synced(checked)  # the status describes these sources: its error says the page is old
            failed_on, failures = None if current is None else sources(current), failures + 1
            again = exc.pages if isinstance(exc, PageFailed) else list(preview.DOCS)
            retry_at = time.monotonic() + min(30.0, 2.0 ** min(failures - 1, 5))  # a bounded exponent
