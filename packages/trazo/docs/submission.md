# Journal submission

Articles use `[submission]` in `trazo.toml`: `journal` and `guidelines`
record the target and instructions URL; `title_page = "manuscript"` keeps
authors on documents, or `"separate"` generates a title page from metadata.
With `"separate"`, `trazo build --authors` (the Authors pill under the
preview's full build) keeps the authors under the title too, for a copy
coauthors read; the next build without it leaves them out again.
`declarations = "end"` appends `paths.declarations` sources before references;
`"separate"` creates a declarations document and Word file; `"form"` keeps
it in the preview only. Reports ignore this layer.

Sources in `manuscript/declarations/`, `manuscript/title-page/` (optional
additions) and `manuscript/cover-letter/` are read in file-name order.
Rename and reorder declaration headings for the journal, retaining ids:
`decl-ethics`, `decl-consent-publication`, `decl-data`, `decl-code`,
`decl-competing-interests`, `decl-funding`, `decl-contributions`, `decl-ai`,
`decl-acknowledgements`. Additional ids must also start with `decl-`.
These sections do not count as main text. A heading with `.form-only`
and all its subsections are visible with a preview badge but excluded from
every Word file and the manuscript's main-text count. Submission-document
panels show total words, including headings, form-only text and generated title pages.

Restart the preview after changing `[submission]` or creating/removing a
submission folder: documents are registered when the preview starts.

`trazo build --submission` builds the main and supplementary documents
and the active separate Word files in `submission.output` (default
`output`), with project metadata, numbering, citations and reference styles.
It removes inactive `title_page.docx`, `declarations.docx` and
`cover_letter.docx` from that folder; other files are left alone. Use the `journal-submission` skill to read the
journal's current instructions, configure placements and limits, and list
files to upload and information to enter in the journal form. Separate
title pages list one ORCID paragraph per author with an ORCID. Moving authors
alone does not anonymize the rest of the manuscript.

`trazo submission check [--strict] [--json]` lists what an
article still needs. An error must be fixed before submitting; a warning
is worth a look:

- Journal: `journal` and `guidelines` set, and where the title page and
  declarations go.
- Limits: the counts of `trazo counts` against `[limits]`, and the number
  of `keywords` against `[limits] keywords`; over a limit is an error.
- Metadata: authors (an error without any), an affiliation for each, a
  corresponding author with an email, keywords; missing ORCIDs are a note.
- Declarations: each `decl-` section in source order; empty or with a
  placeholder is an error, "Not applicable." is fine, `.form-only` sections
  (all of them with `declarations = "form"`) are typed into the form.
- Placeholders: `[PLACEHOLDER: …]`, `[TODO…]` and `[TBD…]`, also across
  lines, outside code, in every document source, its includes and the
  metadata, with file and line.
- Files to upload: each document's Word file; missing is an error, older
  than its sources, images, metadata, values or bibliography a warning.

It exits 1 only with `--strict` and an error; `--json` prints the report
with the form fields below. Reports have nothing to check.

The preview's **Submission** menu opens the same report as cards, the
Checklist (the button's badge counts the errors; the left sidebar lists the
groups). A row opens where it is fixed: a section (a declaration, a
placeholder, the abstract) highlighted in its document with its source line
in the Source tab; the title block or keywords for what comes from the
metadata file (named in the row); the Figures & Tables view; or a
document. Rows set in trazo.toml say so. **For the submission
form** gives the fields journals ask for in their web forms as plain text,
each with a Copy button: title, `short-title`, the Abstract section (its
sub-headings as "Objectives: …"), each Key points, Highlights or Key
results section of the first document (a list keeps one item per line; one
inside the Abstract is left out of the abstract), keywords, authors (name,
affiliations, email, ORCID), numbered affiliations, the corresponding
author and each declaration (form-only ones marked). Pandoc renders them
from the expanded Markdown of their document, so values and citation
numbers match the Word files (`GET /__submission`; `?fields=0` leaves the
fields out).
