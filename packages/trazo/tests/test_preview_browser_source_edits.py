"""Viewer regressions in Chrome: caption and Source tab edits saved to the Markdown sources."""

import json
import unittest
from unittest.mock import patch

import _support
from _preview_browser import PreviewBrowserTestCase

from trazo import preview


class SourceEditTests(PreviewBrowserTestCase):
    def test_table_captions_share_gallery_controls_and_save_to_each_original_source(self):
        from trazo import table
        source = _support.ROOT / "manuscript/supplementary/99_viewer_table.md"
        source.write_text('# Introduction\n\n| Value |\n| --- |\n| 1 |\n\n: Demo table. {#tbl:demo.caption}\n')
        self.addCleanup(source.unlink, missing_ok=True)
        self.keep_files(preview.FILE_HISTORY_PATH, table.HISTORY_PATH, table.output_path("cohorts"))
        cases = [("cohorts", "manuscript", table.spec_path("cohorts"), "Table title"),
                 ("demo.caption", "supplementary", source, "Table legend")]
        for doc in preview.DOCS:
            page = preview.OUT_DIR / f"{doc}.html"
            tables = ''.join(f'<table id="tbl:{label}"><caption>Table {i}. {label}</caption>'
                '<tbody><tr><td>1</td></tr></tbody></table>'
                for i, (label, owner, _, _) in enumerate(cases, 1) if owner == doc)
            page.write_text(page.read_text().replace('</section><script>', tables + '</section><script>'))
        self.open("?view=figures")
        for label, doc, path, aria in cases:
            with self.subTest(label=label):
                original = path.read_bytes()
                self.keep_files(path)
                self.wait_for(f"document.querySelector('.pv-fcard[data-key=\"{doc}|tbl:{label}\"]')")
                self.evaluate(f"document.querySelector('.pv-fcard[data-key=\"{doc}|tbl:{label}\"] [data-a=\"edit-caption\"]').click()")
                field = f'.pv-caption-editor [aria-label="{aria}"]'
                self.wait_for(f"document.querySelector({json.dumps(field)})?.isContentEditable")
                self.assertEqual(self.evaluate("location.pathname"), f"/{doc}.html")
                self.evaluate(f'''(() => {{
                  const field = document.querySelector({json.dumps(field)});
                  field.textContent = 'Updated **caption**.';
                  field.dispatchEvent(new Event('input', {{bubbles:true}}));
                  field.closest('.pv-caption-editor').querySelector('[data-op=save]').click();
                }})()''')
                # Saving closes the editor; reopened, it offers Undo save.
                self.wait_for("document.querySelector('.pv-toast')?.textContent.startsWith('Saved') && !document.querySelector('.pv-caption-editor')")
                self.assertIn(b'Updated **caption**.', path.read_bytes())
                self.assertIsNone(self.evaluate(f"sessionStorage.getItem('preview-tbl:/{doc}.html')"))
                self.evaluate(f"document.getElementById('tbl:{label}').querySelector('caption .pv-tbl-edit').click()")
                self.wait_for("document.querySelector('.pv-caption-editor [data-op=undo]')?.disabled === false")
                self.evaluate("document.querySelector('.pv-caption-editor [data-op=undo]').click()")
                self.wait_for("document.querySelector('.pv-caption-editor .pv-tf-msg')?.textContent.startsWith('Restored')")
                self.assertEqual(path.read_bytes(), original)
                self.evaluate("document.querySelector('.pv-caption-editor [data-op=close]').click(); "
                              "document.querySelector('[data-view=figures]').click()")
        # A Markdown caption is edited as a title and a legend. An untitled
        # one opens as it is and offers its first sentence as the title.
        source.write_text(source.read_text().replace(": Demo table.", ": Demo table. Legend text."))
        demo = '.pv-fcard[data-key="supplementary|tbl:demo.caption"] [data-a="edit-caption"]'
        self.wait_for(f"document.querySelector({json.dumps(demo)})")
        self.evaluate(f"document.querySelector({json.dumps(demo)}).click()")
        self.wait_for("document.querySelector('.pv-caption-editor [aria-label=\"Table legend\"]')?.isContentEditable")
        texts = "Array.from(document.querySelectorAll('.pv-caption-editor .pv-tf-text'), f => f.textContent)"
        self.assertEqual(self.evaluate(texts), ["", "Demo table. Legend text.", ""])
        self.assertEqual(self.evaluate("document.querySelector('.pv-caption-editor .pv-tf-state').textContent"), "")
        type_title = """(text => {
          const field = document.querySelector('.pv-caption-editor [aria-label="Table title"]');
          field.textContent = text; field.dispatchEvent(new Event('input', {bubbles: true}));
          field.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', ctrlKey: true, bubbles: true}));
        })"""
        # Whitespace alone is no change.
        self.evaluate(type_title + "('  ')")
        self.assertEqual(self.evaluate("document.querySelector('.pv-caption-editor .pv-tf-state').textContent"), "")
        self.evaluate(type_title + "('')")
        self.evaluate("document.querySelector('.pv-caption-editor [data-op=offer]').click()")
        self.assertEqual(self.evaluate(texts), ["Demo table.", "Legend text.", ""])
        # Ctrl+Enter refuses an unpaired bracket, as the Save button does.
        self.evaluate(type_title + "('Bad [title')")
        self.evaluate("new Promise(resolve => setTimeout(resolve, 300))")
        self.assertTrue(self.evaluate("!!document.querySelector('.pv-caption-editor')"))
        self.assertIn(": Demo table. Legend text.", source.read_text())
        # The note goes below the table; Enter starts its next paragraph, on a new line.
        self.evaluate("""(() => {
          const note = document.querySelector('.pv-caption-editor [aria-label="Table note"]');
          note.focus(); note.textContent = 'SD, standard deviation.';
          const range = document.createRange(); range.selectNodeContents(note); range.collapse(false);
          getSelection().removeAllRanges(); getSelection().addRange(range);
          note.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
          document.execCommand('insertText', false, 'Bold: best.');
        })()""")
        self.assertEqual(self.evaluate("document.querySelector('.pv-caption-editor [aria-label=\"Table note\"]').textContent"),
                         "SD, standard deviation.\nBold: best.")
        self.evaluate(type_title + "('Demo table.')")
        self.wait_for("!document.querySelector('.pv-caption-editor')")
        self.assertIn(": [Demo table.]{.caption-title} Legend text. {#tbl:demo.caption}\n\n"
                      '::: {.table-note custom-style="Table Note"}\nSD, standard deviation.\n\nBold: best.\n:::', source.read_text())

    def test_figure_caption_controls_save_original_markdown(self):
        source = _support.ROOT / "manuscript/main/99_viewer_controls.md"
        original = '# Introduction\n\nAnother paragraph.\n\n![Diagram caption.](figures/external/example.png){#fig:diagram width=100%}\n'
        source.write_text(original)
        self.addCleanup(source.unlink, missing_ok=True)
        history = source.parent / "viewer_history.json"
        patcher = patch.object(preview, "FILE_HISTORY_PATH", history)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(history.unlink, missing_ok=True)
        page = preview.OUT_DIR / "manuscript.html"
        page.write_text(page.read_text().replace('window.PREVIEW_FILES = [];',
            'window.PREVIEW_FILES = ' + json.dumps([{"file": source.relative_to(_support.ROOT).as_posix(), "title": "Introduction"}]) + ';'))
        self.open()
        edit = """(() => {
          Array.from(document.getElementById('fig:diagram').querySelectorAll('button')).find(b => b.textContent === 'Edit caption').click();
        })()"""
        loaded = "document.querySelector('.pv-caption-editor [aria-label=\"Figure legend\"]')?.disabled === false"
        self.evaluate(edit)
        self.wait_for(loaded)
        # The same editor as table captions: a head with the file, then Title and Legend.
        self.assertEqual(self.evaluate("document.querySelector('.pv-caption-editor.pv-tf .pv-tf-head code').textContent"),
                         source.relative_to(_support.ROOT).as_posix())
        self.assertEqual(self.evaluate("Array.from(document.querySelectorAll('.pv-caption-editor .pv-tf-name'), n => n.textContent)"),
                         ["Title", "Legend"])
        fields = "[document.querySelector('[aria-label=\"Figure title\"]').value, document.querySelector('[aria-label=\"Figure legend\"]').value]"
        self.assertEqual(self.evaluate(fields), ["", "Diagram caption."])
        fill = """((title, legend) => {
          for (const [name, value] of [['Figure title', title], ['Figure legend', legend]]) {
            const field = document.querySelector(`[aria-label="${name}"]`);
            field.value = value; field.dispatchEvent(new Event('input', {bubbles:true}));
          }
          return document.querySelector('.pv-caption-editor [data-op=save]');
        })"""
        saved = "document.querySelector('.pv-toast')?.textContent.startsWith('Saved') && !document.querySelector('.pv-caption-editor')"
        # Brackets in the title must pair up.
        self.assertTrue(self.evaluate(fill + "('Bad [title', 'Short legend.').disabled"))
        # Saving closes the editor and does not reopen it after the reload.
        self.evaluate(fill + "('Edited *title* [@ref].', 'Short **legend**.').click()")
        self.wait_for(saved)
        self.assertIsNone(self.evaluate("sessionStorage.getItem('preview-figure-caption:manuscript')"))
        self.assertIn('![[Edited *title* [@ref].]{.caption-title} Short **legend**.](figures/external/example.png){#fig:diagram width=100%}',
                      source.read_text())
        self.evaluate(edit)
        self.wait_for(loaded)
        self.assertEqual(self.evaluate(fields), ["Edited *title* [@ref].", "Short **legend**."])
        # Without a title the caption is the legend alone.
        self.evaluate(fill + "('', 'Only a legend.').click()")
        self.wait_for(saved)
        self.assertIn('![Only a legend.](figures/external/example.png)', source.read_text())
        # An untitled caption opens unchanged and offers its first sentence.
        state = "document.querySelector('.pv-caption-editor .pv-tf-state').textContent"
        source.write_text(original.replace("Diagram caption.", "Study design. (A) Panels, e.g. one."))
        self.evaluate(edit)
        self.wait_for(loaded)
        self.assertEqual(self.evaluate(fields), ["", "Study design. (A) Panels, e.g. one."])
        self.assertEqual(self.evaluate(state), "")
        self.evaluate("document.querySelector('.pv-caption-editor [data-op=offer]').click()")
        self.assertEqual(self.evaluate(fields), ["Study design.", "(A) Panels, e.g. one."])
        self.assertEqual(self.evaluate(state), "● unsaved changes")
        self.assertTrue(self.evaluate("document.querySelector('.pv-caption-editor [data-op=offer]').hidden"))
        self.evaluate("window.confirm = () => true; document.querySelector('.pv-caption-editor [data-op=close]').click()")
        self.assertFalse(self.evaluate("!!document.querySelector('.pv-caption-editor')"))
        # A title wrapped in the source keeps a space between its lines.
        source.write_text(original.replace("Diagram caption.", "[Wrapped\ntitle.]{.caption-title} Legend."))
        self.evaluate(edit)
        self.wait_for(loaded)
        self.assertEqual(self.evaluate(fields), ["Wrapped title.", "Legend."])
        self.assertEqual(self.evaluate(state), "")
        # An editor left open comes back after a reload without moving the view.
        self.open("?view=figures")
        self.wait_for(loaded)
        self.assertEqual(self.evaluate("new URLSearchParams(location.search).get('view')"), "figures")
        self.assertFalse(self.evaluate("document.querySelector('.pv-figsview').hidden"))

    def test_source_save_keeps_reading_anchor_and_source_selection_after_reflow(self):
        source = _support.ROOT / "manuscript/main/99_reading_position.md"
        self.addCleanup(source.unlink, missing_ok=True)
        self.keep_files(preview.FILE_HISTORY_PATH)
        long_text = "Earlier text before the reading position. " * 100
        short_text = "Shortened earlier text."
        paragraphs = [f"Reading paragraph {i}. " + "More manuscript words. " * 12 for i in range(45)]
        original = "# Introduction\n\n" + long_text + "\n\n" + "\n\n".join(paragraphs)
        page = preview.OUT_DIR / "manuscript.html"
        html = page.read_text().replace('<p>Another paragraph.</p>',
            f'<p>{long_text}</p>' + ''.join(f'<p>{text}</p>' for text in paragraphs))
        html = html.replace('window.PREVIEW_FILES = [];', 'window.PREVIEW_FILES = ' + json.dumps([
            {"file": source.relative_to(_support.ROOT).as_posix(), "title": "Introduction"}]) + ';')
        for change in ("resize earlier", "insert earlier", "delete earlier", "shorten visible", "shorten first"):
            with self.subTest(change=change):
                source.write_text(original)
                current_html = html.replace('<p>A <em>word</em>, H<sub>2</sub>O.</p>', '') if change == "shorten first" else html
                page.write_text(current_html)
                self.open()
                self.evaluate("document.querySelector('[data-tab=source]').click()")
                self.wait_for("document.querySelector('.pv-src-text')?.value.includes('Reading paragraph 44')")
                self.evaluate("document.fonts.ready.then(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))")
                replacement = ("Inserted paragraph.\n\n" + long_text if change == "insert earlier" else
                               "" if change == "delete earlier" else short_text)
                rendered = ('<p>Inserted paragraph.</p><p>' + long_text + '</p>' if change == "insert earlier" else
                            "" if change == "delete earlier" else '<p>' + short_text + '</p>')
                shortening = change.startswith("shorten")
                visible = long_text if shortening else paragraphs[12]
                target = f"[...document.querySelectorAll('.pv-page p')].find(p => p.textContent.includes({json.dumps(visible)}))"
                self.evaluate(f'''(() => {{
                  const area = document.querySelector('.pv-src-text');
                  area.value = area.value.replace({json.dumps(long_text)}, {json.dumps(replacement)});
                  area.dispatchEvent(new Event('input', {{bubbles:true}}));
                  area.focus(); area.setSelectionRange(80, 85); area.scrollTop = 120;
                  const p = {target}, s = document.querySelector('.pv-scroller');
                  s.scrollTop += p.getBoundingClientRect().top - s.getBoundingClientRect().top
                    + {450 if shortening else 0};
                }})()''')
                # Measured from the top of the reading area, which a status banner may move.
                top = f"({target}).getBoundingClientRect().top - document.querySelector('.pv-scroller').getBoundingClientRect().top"
                before = self.evaluate(f"""({{origin: performance.timeOrigin,
                    top: {top},
                    sourceScroll: document.querySelector('.pv-src-text').scrollTop}})""")
                self.evaluate("document.querySelector('.pv-src-bar [data-op=save]').click()")
                self.wait_for("document.querySelector('.pv-src-msg').textContent.startsWith('Saved')")
                self.assertEqual(source.read_text(), original.replace(long_text, replacement))
                page.write_text(current_html.replace('<p>' + long_text + '</p>', rendered))
                with preview.LOCK:
                    preview.STATE["manuscript"]["version"] += 1
                self.wait_for(f"performance.timeOrigin !== {before['origin']} && document.readyState === 'complete' && "
                              "document.querySelector('.pv-src-text')?.value.includes('Reading paragraph 44')")
                if shortening:
                    target = target.replace(json.dumps(long_text), json.dumps(short_text))
                    self.assertTrue(self.evaluate(f"""(() => {{
                      const rect = ({target}).getBoundingClientRect(), viewport = document.querySelector('.pv-scroller').getBoundingClientRect();
                      return rect.bottom > viewport.top && rect.top < viewport.bottom;
                    }})()"""))
                else:
                    self.assertAlmostEqual(self.evaluate(top), before["top"], delta=2)
                self.assertEqual(self.evaluate("[document.querySelector('.pv-src-text').selectionStart, document.querySelector('.pv-src-text').selectionEnd]"), [80, 85])
                self.assertAlmostEqual(self.evaluate("document.querySelector('.pv-src-text').scrollTop"), before["sourceScroll"], delta=2)


if __name__ == "__main__":
    unittest.main()
