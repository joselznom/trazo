---
name: paper-table-generation
user-invocable: false
description: Use when creating or updating generated manuscript tables, editing a table spec, or answering table editor comments.
---

# Paper Table Generation

- Choose the kind of table first. **From the data**: its numbers come from
  the project's data, so they stay computed. **Typed by hand**: its content
  is text, or numbers from outside the project's data (the literature,
  another program, definitions). See "Tables typed by hand" below.
- Numbers live in `tables/scripts/generate_tables.py`, which writes
  unformatted data to `tables/output/data/<name>.json` (records of strings,
  ints, floats, null or objects of numbers such as `{mean, sd, low, high}`,
  plus `values` for the caption and the sha256 of each input). Do not
  round or format numbers there.
- Presentation lives in `tables/specs/<label>.json`: `data`, `title`,
  `legend` (may quote data values as `{n}`), `note` (below the table, in
  Word's Table Note style; a blank line starts a paragraph), `rows` (`keys`, `order`,
  `filter`, `block`), `columns` (leaves, groups and pivots), `formats`,
  `maps`, `highlight`, `emphasis`, `marks` and `missing`. `_shared.json` holds
  formats and maps used by several tables and by `{{v:key|format}}` in
  prose. `scripts/trazo table --help` documents every key.
- `marks` puts a superscript (1 to 3 characters: a, *, †) after a cell
  (`{"column", "row", "mark"}`) or a column header (no `row`); explain each
  mark in the `note`. Row labels are `maps` entries: the author renames them
  in the editor, in this spec's `maps` or, for every table, in
  `_shared.json`.
- `merge` joins a cell to the one above when its text is equal and every
  column to its left is joined too, so merge columns go leftmost.
  `highlight` bolds the best value per `within` set; cells that show the
  same text as the best are bold too.
- The generator records the sha256 of each input in its data files, and
  `scripts/trazo build` reruns it when one changes (or when a script in
  `tables/scripts/` does). A generator that needs more declares a
  `[[build.steps]]` entry named `tables` in `trazo.toml`.
- Never hand-edit `tables/output/`. Run `scripts/trazo run tables`, then
  `scripts/trazo table check --all --outputs`.

Work on a spec through the CLI:

```bash
scripts/trazo table describe LABEL   # fields, values, column ids, the table
scripts/trazo table new LABEL --data NAME
scripts/trazo table patch LABEL ops.json   # or "-" for stdin
scripts/trazo table check LABEL
scripts/trazo table render LABEL
scripts/trazo table undo
```

Patch operations (applied all-or-nothing, then checked):

```json
[{"op": "set", "path": "columns.score.mean.header", "value": "Mean"},
 {"op": "set", "path": "formats.ci", "value": "{low:.1f} to {high:.1f}"},
 {"op": "add", "into": "columns.score", "item": {"id": "sd", "field": "score",
  "header": "SD", "format": "sd"}, "before": "ci"},
 {"op": "move", "column": "visit", "before": "group"},
 {"op": "remove", "column": "score.ci"}]
```

`table patch --shared ops.json` sets `formats.*` and `maps.*` in
`_shared.json` (every table and `{{v:key|format}}` in prose use them).
A `null` value deletes a key. Paths after `columns` are column ids; leaf
ids for `highlight` and `emphasis` are `<id>`, `<group>.<leaf>` or
`<pivot>.<value>.<leaf>`. Re-read a spec before editing it: the author may change it in the
preview's table editor ("Edit table"), which saves through the same
operations.

Comments from the table editor target the table, a column or one cell.
The editor never changes a number: the author asks for new columns, rows
or numbers with a comment that starts "Add a column:", "Add a row:",
"Change this:" or "Change this column:". Change the generator (and the
spec) for those, then resolve the comment saying what changed:

```bash
scripts/trazo table comments [LABEL]          # open ones (--all: every state)
scripts/trazo table resolve ID "what changed" # reply; it moves to review
```

## Tables typed by hand

The cells live in `tables/specs/<label>.json`, with `body` instead of `data`
and `rows`; there is no data and no script, and nothing is computed:

```json
{"schema_version": 1, "title": "Included studies.",
 "columns": [{"id": "study", "header": "Study"}, {"id": "n", "header": "n", "align": "right"}],
 "body": [{"id": "b1", "block": "Europe"},
          {"id": "r1", "cells": {"study": "Smith 2020 [@smith2020]", "n": "120"}}]}
```

Text is published as typed. Bold, italic, `^superscript^`, `~subscript~` and
citations work; other Markdown is literal, and a cell is one paragraph.
`scripts/trazo table new LABEL --hand` scaffolds one. Besides the column
operations, patch takes `cell`, `block`, `row-add`, `row-remove`, `row-move`
and `paste` (`scripts/trazo table --help`, `trazo.table_hand`). The author
edits these tables cell by cell in the preview: re-read the spec first.

## In the manuscript

- Include a table with `!include tables/output/md/<label>.md` and refer to
  it with `[@tbl:<label>]`.
- Keep captions self-contained. Put abbreviations, symbols (*, †, a) and
  data sources in the table's `note`. A Markdown table written by hand
  takes its note in a block right after the table and its caption:
  `::: {.table-note custom-style="Table Note"}`, the paragraphs, `:::`.
