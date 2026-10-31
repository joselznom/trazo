---
name: citation-discipline
user-invocable: false
description: Use when adding, changing or checking manuscript citations, or when asked to check citation keys against the bibliography.
---

# Citation Discipline

- Never invent citation keys.
- Before adding `[@key]` or `@key`, verify the key exists in
  `bibliography/references.bib`.
- If a needed source is missing, ask the user for it (a DOI is best)
  and add it with add-reference only once they agree. Do not otherwise
  edit `bibliography/references.bib`.
- After citation edits, or when asked to check citations, run
  `scripts/trazo build --prepare=inputs`, then `scripts/trazo citations`.
  Report whether it passed and list any
  missing keys exactly; do not invent replacements.

Valid Pandoc forms:

- Parenthetical: `[@key]`
- Inline: `@key`
- Multiple: `[@key1; @key2]`
- With page: `[@key, p. 10]`

Cross-references are not citations:

- Figures: `[@fig:name]`, `{#fig:name}`
- Tables: `[@tbl:name]`, `{#tbl:name}`
- Sections: `[@sec:name]`, `{#sec:name}`

A label in another document of the paper resolves there: `[@fig:name]` in
the manuscript, for a figure in the supplementary, reads "Figure S1". Never
type such numbers by hand, and give labels names unique across documents.
