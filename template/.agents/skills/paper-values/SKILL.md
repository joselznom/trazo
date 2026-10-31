---
name: paper-values
user-invocable: false
description: Use when a number in the prose (a count, mean, percentage, p-value, interval) is added or changed, or when working with {{v:key}} placeholders, scripts/build_values.py or data/values.json.
---

# Paper Values

Numbers quoted in prose, captions and diagrams come from
`data/values.json`, never typed by hand.

- Compute each value in `compute()` in `scripts/build_values.py`, from
  the data files listed in `INPUTS`. Each key maps to `value` (the raw
  number, or an object of numbers such as `{mean, sd}` or
  `{est, low, high}`), `text` (what `{{v:key}}` shows) and `source`.
- Name keys `<dataset>.<quantity>`, for example `cohort.n` or
  `model.auc`.
- A new data file goes in `INPUTS`: the script records each input's hash
  in `data/values.json`, and `scripts/trazo build` reruns it when one
  changes. A project whose values script needs more
  declares it as a `[[build.steps]]` entry named `values` in
  `trazo.toml` (keys documented in trazo's project.py).
- Run `scripts/trazo run values`, then
  `.venv/bin/python scripts/build_values.py --check`.

In Markdown:

- `{{v:key}}` shows the key's `text`.
- `{{v:key|format}}` applies a format from `tables/specs/_shared.json`
  (for example `mean_sd`, `ci`) to the raw `value`, so prose and tables
  share one rounding. Add a format with
  `scripts/trazo table patch --shared ops.json` (see
  paper-table-generation).
- Placeholders inside code spans are left as written.

An unknown key or format, a value that does not fit the format, or a
values file older than its inputs makes the build fail. Fix the source;
do not replace the placeholder with a typed number. If the data needed
for a value is not in the project, ask the user for it.
