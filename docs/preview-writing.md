# Writing on the preview

A click in a paragraph, list item or heading writes there, with no mode to
enter. Leaving it, or ⌘↵, saves the words that changed to the Markdown. Esc
discards what you typed since the last save; Enter, Backspace at the start and
bold or italic save at once, and ⌘Z takes them back. Citations, values, math,
code, note calls and images are locked chips.

- **Enter** breaks a paragraph or opens a new one, at its start before it. In a
  list it makes a new item, and in a heading it goes on to the text.
- **Backspace** at the start joins the paragraph before. Emptying a paragraph
  takes it out of the file.
- **The arrows** at a paragraph's edges go to the next one.
- **A paste** loses its formatting. Blank lines make paragraphs, except in a
  heading, and single line breaks become spaces.
- **Bold, italic and underline:** select text within one paragraph, then press
  ⌘B, ⌘I or ⌘U, or B, I or U in the selection bar. They reach Word; a button
  shows pressed when all the selected text has the style, and a press takes it
  off. Underline is written `[text]{.underline}`.
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

Inside the text, ⌘Z undoes your typing and then, one at a time, the Enter,
Backspace, bold or italic before it: the page and the file go back together.
⌘⇧Z does them again. If the file changed since, nothing is undone and a notice
says why. Leaving the text ends that history: outside the text, ⌘Z undoes your
latest change in the document, a comment or a saved paragraph.

After a save the page reloads once its build has the save, so what you wrote
never disappears for a moment.
