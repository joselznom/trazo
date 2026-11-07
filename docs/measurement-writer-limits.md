# Independent writer limits

The strict no-text-loss invariant remains **open** for a writer that ignores
trazo's lock. Both in-place writes and atomic replacements can be overwritten
between the browser save's locked read and its publication. A known limitation
is still an invariant violation: the harness retains `violations` and exits 1.
It does not turn these failures into passing tests.

## Supported ordering and the gap

A source save compares the supplied hash with the file read under `review_lock`.
It rejects with HTTP 409 any edit completed before that read. trazo writers that
use the same lock are serialized. An independent edit completed after the read
and before the rename can be lost without notice, even when the save returns
HTTP 200. An independent atomic replacement in that interval is not recoverable.

An independent write after publication wins on disk. The next browser save
returns 409; the browser's unsaved text survives in its open tab. Closing that
tab is not a durable draft backup. A first Word build concurrent with an edit
may use the earlier snapshot; the next build includes the new source.

## Why no partial storage protocol was added

A hard link to the previous inode can preserve an in-place write during
publication without making the source disappear. This would close the measured
in-place case. It does not preserve an independent atomic replacement that lands
between taking the link and publishing. It also needs a persistent recovery
journal, orphan discovery after SIGKILL, an explicit conflict response, undo
semantics and a fallback when links are unsupported. That partial protocol was
deferred rather than presented as a general concurrency fix. The exact in-place
case remains open too.

A second hash comparison still precedes the rename and leaves a race. Moving the
source aside before claiming its name can leave it absent after SIGKILL. Native
atomic exchange through ctypes is permitted by the standard-library constraint;
platform and filesystem coverage, plus recovery of the displaced file, are the
obstacles. A writer holding an already-open displaced inode is a further limit.
A full solution needs writer cooperation or a separately designed conflict and
recovery protocol with explicit platform support.

## Reproduction

Run from this checkout, using a generated example paper as `SEED`. Each command
uses its own output folder. Both pause a real HTTP save immediately before its
source replacement, perform the independent write, and resume the request.

```sh
uv run --quiet --project packages/trazo python scripts/measure/scenarios.py "$SEED" .build/measure/writer-limits --scenario concurrent_writers --scenario concurrent_atomic_writers
```

`concurrent_writers` writes the file in place. `concurrent_atomic_writers` writes
a sibling temporary file and calls `os.replace`. Each records the writer mode,
exact publication site, HTTP response, retained-text check and limitation. No
production hook was added. The existing external fault shim supplies the barrier.

The corrupt-history oracle also now records source preservation immediately
after the failing request, before a successful retry deliberately changes it.
That fixes a misleading diagnostic field; it does not relax an assertion.

Risk: low, measurement tools and a narrowed README statement only. No product
behavior, cache, template or renderer change. Revert this change to undo it.
Validation output and the combined final campaign are recorded in the final
measurement report.
