---
name: journal-submission
user-invocable: false
description: Use when preparing an article for a journal, configuring submission files or applying instructions for authors.
argument-hint: [journal and instructions URL]
---

# Journal submission

For article projects only. Ask for the target journal and the URL of its
instructions for authors. Fetch and read the current page; never rely on
memory. Summarize the required files, headings, order, limits and form-only
information, with the source URL. If the instructions cannot be read, ask
for the relevant text rather than guessing.

1. Set `journal`, `guidelines`, `title_page` and `declarations` under
   `[submission]` in `trazo.toml`. Title-page choices are `manuscript`
   and `separate`. Declarations choices are `end` (before references),
   `separate` (own Word file), and `form` (preview only; type into the form).
   Set `[limits]` words, abstract, figures, tables, references and keywords
   (the maximum number of keywords); 0 means no configured limit. Check the
   counting rules in `[counts]`. Match how the journal names supplementary
   items in `[documents.supplementary]`: `number_prefix` ("S" gives "Figure
   S1"; "" gives "Figure 1") and `title_prefix` ("Supplementary" gives
   "Supplementary Figure 1"), in captions and in every reference to them.
2. Edit `manuscript/declarations/*.md` in file-name order. Rename and reorder
   headings to match the journal, but retain the canonical ids:
   `decl-ethics`, `decl-consent-publication`, `decl-data`, `decl-code`,
   `decl-competing-interests`, `decl-funding`, `decl-contributions`,
   `decl-ai`, `decl-acknowledgements`. Add new `decl-` ids for extra sections.
   Mark individual form-only headings with `.form-only`; their subsections
   are excluded from every Word file. Keep required headings and write
   "Not applicable." when appropriate, rather than dropping them.
3. Never invent approval numbers, committees, grants, funders, competing
   interests or author contributions. Leave `[PLACEHOLDER: …]` instructions
   for unresolved facts and list what the authors must supply.
4. Review title-page metadata (authors, affiliations, corresponding email,
   equal contributions and ORCIDs). A separate title page is generated from
   metadata; optional `manuscript/title-page/*.md` follows it. For a copy
   with the authors under the title (coauthors), run `scripts/trazo build
   --authors`; the next build without it removes them again. Edit the cover
   letter in `manuscript/cover-letter/` without unverified confirmations.
5. Flag other requirements: key points/highlights, graphical abstract,
   keyword count, abstract structure, ethics sentence in Methods, AI statement
   location, cover-letter content and reporting checklist. Edit these only
   with the author's approval. Moving authors alone is not full anonymization.
6. Match the abbreviation layout and source position to the instructions.
   Put a Markdown definition list under Abbreviations / List of abbreviations
   or a heading with `#abbreviations` as its id, in that document's sources.
   Set `[abbreviations] style = "table"` for a borderless table (for instance
   MDPI at the end), `"list"` for definitions (for instance Springer after
   keywords), or `"inline"` for one paragraph. Verify the journal's actual
   instructions rather than assuming these conventions. Entries sort by
   default (`sort = true`); `ignore = [...]` suppresses deliberate acronym
   exceptions. Define terms at first use in abstract and main text separately.
   First-page footnotes such as Elsevier's are out of scope: use inline in
   title-page sources or leave that requirement for the author. The preview's
   Submission form has a copy-ready Abbreviations field.
7. Build the submission files and check them. With the trazo tools:
   `build` with `submission: true`, then `check`; the call succeeds even
   when the report has errors, so its `errors` decide. In a terminal:
   `scripts/trazo build --submission`, then
   `scripts/trazo submission check --strict`, which fails on errors.
   The check has an error while a limit is exceeded, a declaration is empty
   or a `[PLACEHOLDER: …]`, `[TODO…]` or `[TBD…]` remains, and lists the
   files to upload. Report its errors and warnings, the requirements not
   yet satisfied and what the authors must supply. Point the author to the
   preview's **Submission** menu (the `preview_start` tool; in a terminal,
   `scripts/trazo build --prepare=inputs && scripts/trazo preview serve`):
   it shows the same checklist and the journal form's
   fields (title, abstract, key points, keywords, authors, affiliations,
   declarations) as plain text with Copy buttons. Reports have no
   submission layer.
