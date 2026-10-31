---
name: comments
description: "Use when the user asks to answer, address or resolve review comments left in trazo: in the live preview, the diagram editor, the table editor, or with Annotate in the ChatGPT app's browser."
---

# Preview review comments

- Comments live in the workspace's scoped review files (git-ignored). A comment is
  `open` (the author wrote it), `review` (the agent answered; the author
  checks it) or `done` (only the author marks it).
- List the open ones; `--all` shows every state with the reply thread. A
  reopened comment has a follow-up in its thread.

```bash
scripts/trazo review comments
scripts/trazo review resolve SCOPE:ID "reply"
```

- Edit the Markdown source (for tables, the spec through
  `scripts/trazo table patch`),
  then `resolve` each comment with a short reply saying what changed.
  Every comment change is undoable (`scripts/trazo review undo SCOPE [NAME]`).
- The author may edit the same files in the page's Source tab, a caption
  editor or the table editor; re-read a file before editing it.
- The shared list includes document, diagram and table comments; use the
  `document:ID`, `diagram:ID` or `table:ID` it prints. Existing `preview`,
  `diagram` and `table` comment commands remain available. Change a
  spec with `scripts/trazo table patch` (see paper-table-generation).
- The preview server only accepts same-origin JSON requests on 127.0.0.1;
  use the commands above rather than calling its endpoints.

## Browser comments (the ChatGPT app's Annotate)

Annotate comments made on the preview in the ChatGPT app's built-in browser
are not in the review files: they come in the user's message, under
`# Browser comments:`, one `## Comment N` (or `## Requested annotation N`)
each, with `Page URL`, `Target selector`, `Nearby text` or `Selected text`,
then the comment. Find each one's source:

- `Page URL` path: `/NAME.html` is document NAME, whose Markdown is the
  folder `[documents.NAME] path` in `trazo.toml` (defaults
  `manuscript/main`, `manuscript/supplementary`); `declarations`,
  `title_page` and `cover_letter` are the `[paths]` folders of the same
  names. `/diagrams` is a diagram (`figures/diagrams/`), `/tables` a table
  spec (`tables/specs/`).
- `Target selector`: its ids name the place. A section's id is its
  heading's (the heading text in lowercase with hyphens, or its `{#ID}`);
  `fig:NAME` and `tbl:NAME` (`#fig\:NAME` in CSS) are the `{#fig:NAME}`
  and `{#tbl:NAME}` labels (a table's cells come from its spec, a diagram
  figure from its JSON).
- `Nearby text` or `Selected text`: search that document's Markdown for a
  few distinctive words; rendered numbers may come from `{{v:key}}`
  placeholders and citations are rendered, so do not search for them.

Edit the source, then say per comment what changed in the reply; there is
nothing to resolve. Comments in trazo's own review panel work as above.
