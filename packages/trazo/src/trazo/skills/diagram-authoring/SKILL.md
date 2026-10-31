---
name: diagram-authoring
user-invocable: false
description: Use when creating or editing a declarative diagram in figures/diagrams.
---

# Diagram authoring

- Edit `figures/diagrams/<name>.json`. Do not hand-edit the SVG, PDF or PNG
  in `figures/external/`.
- Reported numbers use `{{v:key}}` from `data/values.json`. An unknown key
  is an error.
- Open the editor (part of the preview): with the trazo tools,
  `preview_start`, then `/diagrams` on its URL; in a terminal,
  `scripts/trazo build --prepare=inputs && scripts/trazo preview serve`,
  then http://127.0.0.1:8765/diagrams.
- Create, check, then export:

```bash
scripts/trazo diagram new NAME --width 183 --height 60
scripts/trazo diagram check NAME
scripts/trazo diagram export NAME
```

`scripts/trazo diagram check --all --exports` lints every diagram and fails if an export is
missing or stale. `meta.draft` true is linted and not exported.

- `scripts/trazo diagram --help` documents the JSON schema and the
  `patch` operations; `describe NAME` prints the layout in mm.
- Inspect `figures/external/<name>.png`. For a draft, render a preview
  instead: `scripts/trazo diagram render NAME --png .build/diagrams/NAME.png`.
- List comments with `scripts/trazo diagram comments`. Reply with
  `scripts/trazo diagram resolve ID "reply"` (sets `review`). Only the
  author marks a comment done.
