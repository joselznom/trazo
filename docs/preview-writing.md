# Writing on the preview

A click in a paragraph, list item or heading writes there, with no mode to
enter. Leaving it, or ⌘↵, saves the words that changed to the Markdown. Esc
discards what you typed since the last save; Enter, Backspace at the start and
bold or italic save at once, so to go back from those, leave the text and press
⌘Z. Citations, values, math, code, note calls and images are locked chips.

- **Enter** breaks a paragraph or opens a new one, at its start before it. In a
  list it makes a new item, and in a heading it goes on to the text.
- **Backspace** at the start joins the paragraph before. Emptying a paragraph
  takes it out of the file.
- **The arrows** at a paragraph's edges go to the next one.
- **A paste** loses its formatting. Blank lines make paragraphs, except in a
  heading, and single line breaks become spaces.
- **Bold and italic:** select text within one paragraph, then press ⌘B or ⌘I, or
  B or I in the selection bar.
- **`@`** lists the paper's references, figures, tables and values, and inserts
  one as a chip.

What is typed reads literally, so `*` stays an asterisk. Every save is checked
before it is written: built alone, the paragraph must read what you left on the
page. If not, nothing is written, your text stays and the paragraph is flagged
until you edit its Markdown in place or discard it; if the file changed there,
you choose yours, the file's, or the Markdown. New headings, sections, tables,
figures, equations and footnotes stay in the Source tab or the agent's hands,
and so do lines that hold a raw TeX environment or HTML, and the lines next to
them with no blank line between.

Inside a paragraph, ⌘Z undoes your typing. Outside the text, it undoes your
latest change in the document, a comment or a saved paragraph.
