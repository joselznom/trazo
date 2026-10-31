# trazo

Tools shared by every project made from the trazo template: live HTML preview
with review comments, the declarative diagram editor, `{{v:key}}` value
expansion, citation checks and the shared agent skills.

Projects do not add trazo to their own `pyproject.toml` or `uv.lock`.
They run it through `scripts/trazo`: the per-user install the plugin's
setup made. A project lock stays unchanged when trazo is updated.

## Commands

```bash
scripts/trazo build --prepare=inputs      # first: values, tables, figures
scripts/trazo preview serve --port 8765   # documents, tables and diagrams
scripts/trazo citations                   # citation keys vs bibliography
scripts/trazo counts [--check]            # main-text words, figures, tables, references
scripts/trazo review comments             # open comments across the workspace
scripts/trazo build                        # prepare diagrams and build DOCX
scripts/trazo diagram check NAME          # lint a diagram
scripts/trazo diagram export NAME         # write figures/external/NAME.*
scripts/trazo table describe LABEL        # a table spec, its data and output
scripts/trazo table render --all          # write tables/output/md/<label>.md
scripts/trazo table comments              # open comments from the table editor
scripts/trazo expand manuscript/main/*.md # includes and values to stdout
scripts/trazo agents [--check]            # sync agent skills
scripts/trazo doctor                      # uv, Python, Pandoc, Chrome: paths, versions
scripts/trazo setup [--plan]              # download Pandoc and, with no Chrome, a headless one
scripts/trazo version
```

`trazo new FOLDER [--kind report] [--no-example] [--title T]` creates a
paper without Copier, git or a network: what `copier copy` writes, plus
`.trazo/manifest.json`. `trazo update [FOLDER]` updates such a paper to
the template of the trazo that runs it (so run a newer trazo, not the
project's pinned one): unchanged files are replaced, changed ones get `<file>.new`.

Each command has `--help`.

## Live preview

`preview serve` shows each document as a page: tabs per document,
**Figures & Tables** and, for articles, a **Submission** menu in the top bar (the checklist and the
declarations, title page and cover letter pages), an index of the headings with word
counts and comment badges on the left (under the document's main-text words,
figures, tables and references; see [Word counts](#word-counts)), and on the right the **Comments**,
**Source** (the Markdown file of the section in view) and **Diff** (against
git HEAD) tabs. Select text to add a review comment; click a paragraph
marker (¶) to open its source. Keys: `[` and `]` fold the sidebars, `j` / `k`
step through open comments. Sidebars resize by dragging their inner edge;
below 900 px they become drawers. The theme menu offers auto, light and dark
modes and five colour palettes, shared with the table and diagram editors.
Editor links retain the original document or gallery as their return destination.

**Figures & Tables** (`?view=figures`) lists the figures and tables of every
document as cards: thumbnail (click for a larger view), caption, **Go to
text**, **Edit diagram** or **Edit caption**,
the places that cite it, and a warning when nothing cites it. The left sidebar filters by type, document
and "Not cited"; the right one shows the comments on captions and tables.
Diagram cards also show export freshness and diagram comment counts; diagrams
outside the documents, including drafts, appear in their own list.
**Submission** (`?view=submission`, articles) is described under
[Journal submission](docs/submission.md).
The viewer fonts (IBM Plex Sans and Mono, Source Serif 4; SIL Open Font
License, texts in `assets/preview/OFL-*.txt`) ship with trazo, so the
viewer makes no network requests outside 127.0.0.1.

The **Export** menu builds the complete manuscript (⌘B),
downloads the section in view, or opens a section picker (⌘E). In the
picker, ticking a section ticks its subsections; untick any of them to
leave it out. The result is one DOCX in manuscript order, with or without
the cited references. Filter by name, or use the quick picks: whole
document, current section, last export. The zoom bar scales the document from 50%
to 200%; **Fit width** follows the available panel width, and clicking the
percentage resets to 100%. **Aa** adjusts text from 10 to 22 px (default
14 px), independently of zoom and panel width. Tables and captions stay
at least 10 px before zoom; text inside images keeps its original size.
The viewer remembers both settings and preserves the reading position.
These settings affect the document view only, never exports.

**Export > Full <document>** builds the open document (`trazo build --doc
NAME`; `[documents.<name>] docx` and `build` in `trazo.toml`). A line under it says which Word indexes the build
adds (**No indexes**, **With figure list**, ...) and unfolds their pills:
**Contents**, **Figures** and **Tables**, with counts. The section picker shows
the same pills for the chosen sections. Indexes are off initially, remembered
per document, and offered only when the exported content has something to list
(two headings, a figure, a table); **Current section** never adds them. They follow the title block and
end with a page break; update fields in Word to fill them and their page
numbers. `trazo build --doc NAME --toc --lof --lot` does the same from the
terminal.

Figures, including multiple panels, export as DOCX, PNG or PDF. SVG is available
for a single-image figure with a vector source;
tables export as DOCX or Markdown. Captions, original object numbers and
citation numbers are kept.
Exports use the complete Markdown pipeline before selecting the object.
`[paths] reference_doc` sets an optional Word style document and
`[commands] pandoc` sets the HTML preview and export executable. Both old
and new Pandoc math options are supported automatically. SVG captions use
standard SVG foreign content for styled text and equations; view them in a
browser.

**Edit source** opens the Markdown of a figure with multiple panels.
**Edit caption** changes a single-image figure's original Markdown caption, as a
title and a legend, and preserves the image path and attributes. The title is
written as `[Title.]{.caption-title}`: bold after the figure number in the
article, Figures & Tables and the enlarged viewer, plain in Word (which keeps
its caption style). Table spec titles are written the same way. An untitled
caption offers **Use first sentence as title**. Figure and table captions
use the same editor; saving closes it. A table's **Note** field edits the
note below it (abbreviations, symbols, sources): the spec's `note`, or in
a Markdown table the `::: {.table-note custom-style="Table Note"}` block
right after the table and its caption. Word gives it the Table Note style;
DOCX and Markdown table exports keep it. Saves reject concurrent source edits;
**Undo save** restores the preceding save to that Markdown file. Generated table words belong in
**Edit table** (the JSON spec), so regeneration retains them. Tables without a
spec offer **Edit source** to open their Markdown.
Other inline formatting, such as `**…**`, shows everywhere, Word included.
Click an article image (or press Enter when
focused) to enlarge it; Esc closes the viewer. After saving source, the
preview keeps the visible paragraph and the Source selection and scroll.

Text selection offers **Comment** (for the agent) and **Underline**, a
reading mark for the author: not a comment, left out of the agent's list,
and the Markdown and Word output are unchanged. Select or click underlined
text to remove it. The **Comments** tab shows **Active** comments (open, or
to check after the agent's reply) in document order, and **Resolved** ones,
the last resolved first, with **Show in text**, **Reopen**, **Delete all
resolved** and the deleted ones. **✓ Resolve** closes a comment and **Reply**
reopens it for the agent; each change shows a notice with **Undo** (⌘Z also
undoes). Resolved comments are an archive: they are not painted or picked by
a click in the text; **Show in text** lights their text for a moment.
New comments store textual context (and the words around the quote, so the
right one of several identical quotes is found) so navigation
can follow a rewritten paragraph; ambiguous or deleted targets remain
unlocated. When the quoted text is gone, the card keeps the original quote
and a marker in the margin shows its paragraph (one strip for each of open,
to check and underline; click in the paragraph to remove its underline).
Older comments retain exact-quote matching and their original quotes; a
complete rewrite without stored context cannot be recovered safely.

## Project settings

`trazo.toml` at the project root pins the release (`version`) and
overrides the default layout. Invalid keys and types are errors. Commands use
argument lists; legacy command strings support shell quoting (without a shell). The defaults and
every key are documented in `src/trazo/project.py`.

Supplementary items use `S1`, `S2`, … numbers in previews, Word files and
exports; other documents cite them by label. Set `number_prefix` (`""`
for none) and `title_prefix` ("Supplementary") in a `[documents.NAME]`
entry; custom documents have neither.

Figure paths default to `figures/`. The `[paths]` keys `figures`, `diagrams`,
`diagram_output`, `figure_tokens`, `figure_fonts` and `figure_embeds` override
the image root, JSON sources, exports, scientific styles, fonts and SVG picker.
Configured documents and scientific inputs are always watched;
`[preview] watch` adds project-specific inputs.

Diagrams and SVG export need Google Chrome, Edge or Chromium. Set
`TRAZO_CHROME` or `[browser] chrome` when it is not in a usual place.
The older `[diagrams] chrome` setting remains supported. On macOS,
`trazo setup` downloads Chrome for Testing's headless shell into the data
folder when none is installed.
An installed browser always wins, and `--offline` skips the download.
macOS and Linux are supported.
All renders use temporary Chrome profiles, separate from your browsing session.
On macOS, a sandbox that denies LaunchServices prevents even headless Chrome
from starting. Run the command from a terminal or allow the agent to launch
GUI processes; trazo detects this restriction before launching Chrome.

### Document metadata

Top-level `kind = "article"` (default) or `kind = "report"` in `trazo.toml`
uses the same build. The configured metadata file accepts Quarto-style authors
(`name`, or `name: {given: Ana, family: Pérez}`, `email`, `corresponding`,
`equal-contributor`, `orcid`) and affiliations (refs/ids, inline maps or names).
Top-level `affiliations` defines ids with department, name, address, city,
region, postal-code and country; unknown refs are errors. Structured authors
render Word/preview front matter; string authors keep Pandoc's title block,
and `orcid` is displayed on separate title pages only. `keywords` and optional `keywords-title`
appear after `# Abstract`, outside word counts. Metadata edits rebuild the
title block in the next build; updates preserve the project-owned file.

### Journal submission

Submission placements, the separate Word files and `trazo submission check`:
[docs/submission.md](docs/submission.md).

### Word counts

Words count the main text, as journals do. The title block, headings,
figures, tables (with their notes) and references are never words;
in-text citations are, as in Word, and a footnote counts with the section
that cites it. A section is left out, with its sub-sections, when its
heading is a `[counts] exclude` phrase (abstract, key points, declarations,
ethics approval, funding, conflict of interest, author contributions, data
availability, use of AI tools, acknowledgements, appendix, ...), optionally
followed by "statement", a label ("Appendix A") or a subtitle after a colon;
"Funding models" or "Abstract syntax trees" still count. For any other
heading, add the class: `## Journal form {.uncounted}`.

The index shows the words each counted section adds; left-out sections
have no number. The abstract (the "Abstract" section, else `abstract:` in
the metadata) has its own count. Set the journal's limits of the first
document in `[limits]`; a count without one is shown as a plain number:

```toml
[limits]          # 0 = no limit
words = 3000
abstract = 250
figures = 6
tables = 5
references = 50
keywords = 6      # number of keywords in the metadata (trazo submission check)
```

The preview shows each limit as a meter or a tile that says what is left or
how far over it is. Reload the page after editing `trazo.toml`.
`scripts/trazo counts` prints the same numbers (after `build --prepare=inputs`), and
`--check` fails when a count is over its limit or there is no abstract to
check.

## Tables

The project's generator writes unformatted data to
`tables/output/data/<name>.json`; `tables/specs/<label>.json` holds the
presentation, and `trazo table render` writes the Pandoc grid table
`tables/output/md/<label>.md`. trazo never runs project code. The data
contract, every spec key and the patch operations are documented in
`src/trazo/table.py` (`trazo table --help`).

The preview has a table editor at `/tables#<label>` (**Edit table** on a
spec table and on its Figures & Tables card): click the caption, a header or
a cell to change titles, headers, formats, row order, blocks, merges and
bold cells, comment on them (`C`), and undo with ⌘Z. Every change is a
`trazo table patch` operation, so it is validated, saved, rendered and
undoable like one; numbers are never edited there. Agents answer its
comments with `trazo table comments` and `trazo table resolve`.

The diagram editor is also part of the preview, at `/diagrams#<name>`
(**Edit diagram** on a figure, **All diagrams** in Figures & Tables). It
needs Google Chrome or Chromium for checks and exports; its endpoints are
documented in `src/trazo/diagram_editor.py`.

The **Comments** tabs of both editors work like the preview's: **Active**
and **Resolved**, **✓ Resolve**, **Reply** after the agent answers, and a
notice with **Undo** after each change. There ⌘Z undoes table or diagram
edits; **↶ Undo** next to Active / Resolved undoes the last comment change.

Saving retains the JSON source; **Export pending** means the manuscript's
image still needs updating. Export, a workspace DOCX build, and downloads
prepare stale diagrams first, preserving no-op builds and `meta.draft`
figures. Value, style,
font and embedded-file changes refresh the open editor without replacing
unsaved edits. Existing source stamps stay valid; new exports also record
font content in a render stamp. UI themes never affect scientific exports.

Workspace DOCX builds run `[commands] values` and `figures` before preparing
diagrams, then `docx`. `build --prepare=all` runs every stage before the
documents (values, tables, steps, diagrams), `build --prepare=inputs` all but
the diagram export (no Chrome; before preview, `citations`, `counts`).

The application host is `server.py`; documents, tables and diagrams expose
the same route contract. `resources.py`, `dependencies.py`, `browser.py`
and `assets/app/` own shared file access, revisions, Chrome and browser UI.
`/__workspace`, `/__catalog` and `/__review` expose common state, objects
and review operations. Existing feature endpoints and comment/history files
remain compatible. `review` uses `document:ID`, `table:ID` or `diagram:ID`;
`review undo SCOPE [NAME]` undoes the last comment change in that scope.

## Development

From the trazo repository:

```bash
cd packages/trazo
uv run python -m unittest discover -s tests
```

To try unreleased changes in a project, export `TRAZO_DEV` before its
`scripts/trazo` commands:

```bash
export TRAZO_DEV=../trazo/packages/trazo
```

Release: bump `version` in `pyproject.toml` and `src/trazo/__init__.py`,
add a CHANGELOG entry, merge to `main`, and tag `vX.Y.Z`.
## Abbreviations

In any document (article or report), write a Markdown definition list under
`# Abbreviations`, `# List of abbreviations` (case-insensitive; a section
number is allowed), or a heading with `{#abbreviations}`. Its source position
is its Word position. Each entry is a term line followed by `:   Long form`.
Entries are sorted stably, ignoring case and non-letters.

In `trazo.toml`, `[abbreviations]` accepts `style = "table"` (default,
borderless Abbreviation / Meaning columns), `"list"` (definition list), or
`"inline"` (one semicolon-separated paragraph); `sort = true` and `ignore = []`
are the other defaults. Tables have their own Word style, Abbreviations;
they are not numbered figures/tables, limits, editor objects or list-of-table
entries. Preview layout matches Word and text abbreviations have long-form
hover help without changing the text copied or counted.
An empty list (including its heading) is omitted from Word, preview and the
Submission form. Documents without entries have no abbreviation checks.

Run `trazo abbreviations check [DOCUMENT ...]` for every kind.
`submission check` also reports
an Abbreviations group for articles; the Submission form includes a plain-text
inline list. Checks warn about listed-but-unused and acronym-like unlisted
tokens (at least two uppercase letters, including mixed case and digits),
and missing definitions at first use in the abstract and main text
separately. Define `long form (TERM)` or `TERM (long form)`. Hyphens and simple
plurals are accepted. Captions/tables count as uses for listed-but-unused,
but are outside the unlisted and first-use checks. Hyphenated proper names
such as MAMA-MIA and NSCLC-Radiomics are also outside those two checks;
AUC-based and COVID-19 still count. Code,
citations, cross-reference identifiers, URLs, math, Roman numerals and
ordinary CamelCase names are
ignored. Review warnings rather than treating the heuristic as a proof of
completeness; add deliberate exceptions to `ignore`.
