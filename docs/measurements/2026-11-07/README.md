# Measurement evidence, 2026-11-07

Read [the final report](../../measurement-final.md) for conclusions and
[the validation record](../../measurement-final-evidence.md) for commands and
local logs. This archive contains selected final evidence, not every exploratory
run. No observation within a selected run was removed.

## Files and provenance

- `index.json`: eight runs, two papers per run, 280 observations per paper,
  **4,480 observations**. Each CSV contains all 20 observations for seven operations
  in both application-cold and warm regimes.
- `<run>-<paper>.json`: machine/tool versions, UTC start, measured commit,
  Git product-tree hash, quantiles and SHA256 of the original result file.
- `<run>-<paper>.csv`: raw seconds and available save acknowledgement/MCP timings.
- `<run>-<paper>-delta.json`: p50/p95 changes against the common baseline,
  fixed-seed bootstrap intervals and the declared noise plus 5% gate.
- `fixture-*.json`: reproducible fixture description and input hashes.
- `budgets-final.json`: every final cell, its p95 budget and pass/miss result.
- `idle-*.json`: twenty two-second idle windows per side, request counts and
  one-core CPU percentages; the after revision is committed `b6005a5`.
- `*-artifacts.json`: normalized DOCX member hashes, export hashes, renderer
  hash and real diagram export checks. All selected comparisons are equal.
- `baseline-faults-*.csv` and `faults-*.csv`: 189 before and 197 combined
  injections, including trigger, recovery and invariant violations. Only reached
  Python boundaries are covered. Raw snapshots remain local.
- `scenarios.json`: environmental outcomes and both known independent-writer
  violations. The `limitation` field does not turn a violation into a pass.
- `inventory-*.jsonl` and `inventory-index.json`: static disk/process/concurrency/
  state/port candidates. The inventory is from `4e2c3b0`, before the optional-pipe
  narrowing, and is not a dynamic coverage claim.
- `profile-*.json`: separate cProfile/perf_counter/Chrome trace summaries, one
  diagnostic observation per operation/regime. Never mix these with timing CSVs.
- `acceptance-*.md`: unchanged summary sections from actual reports, with source
  report hashes. Full reports stay local because they include machine paths.

The original product is `ee13ca0`; the measurement base is `2cc61c0`. Baseline
result metadata names the latter, whose product tree is unchanged. Other runs
name their committed product revisions. `combined` is `6ee0bae`, a local assembly
of the six corrections, not another product PR. Product corrections are not
merged. Reports dated November 6 UTC belong to November 7 local time in Brussels
when their UTC start is after 23:00.

Cold means the per-operation application reset defined in the harness README,
not flushed OS caches. All quantitative runs were serial without simultaneous
builds/tests. Twenty observations provide a sparse empirical p95. Bootstrap
intervals and practical thresholds constrain improvement claims; passing one
empirical budget cell is not a universal latency guarantee.

Only ZIP container metadata and core created/modified timestamps are normalized
for DOCX comparisons. No text, styles, relationships, images or diagram stamps
are discarded. Browser traces and cProfiles are not compressed into git; their
locations are listed in the validation record. Local DOCX binaries are retained
with their manifests.
