"""Viewer regressions in Chrome: figures in the document and the Figures gallery."""

import json
import shutil
import time
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import _support
from _preview_browser import PreviewBrowserTestCase

from trazo import preview


class FigureTests(PreviewBrowserTestCase):
    def test_diagram_refreshes_dependencies_and_preserves_local_edits(self):
        from trazo import diagram_editor
        self.addCleanup(diagram_editor.shutdown)
        path = _support.ROOT / "figures/diagrams/example.json"
        tokens_path = _support.ROOT / "figures/style/tokens.json"
        values_path = _support.ROOT / "data/values.json"
        embed = _support.ROOT / "figures/output/svg/browser-probe.svg"
        for file in (path, tokens_path, values_path, embed):
            before = file.read_bytes() if file.exists() else None
            self.addCleanup(lambda f=file, data=before: f.write_bytes(data) if data is not None else f.unlink(missing_ok=True))
        data = json.loads(path.read_text())
        data["panels"] = [{"id": "panel", "x": 35, "y": 2, "w": 15, "h": 24,
                           "embed": "figures/output/svg/browser-probe.svg"}]
        path.write_text(json.dumps(data))
        embed.parent.mkdir(parents=True, exist_ok=True)
        embed.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="10mm" viewBox="0 0 10 10"><rect width="10" height="10" fill="red"/></svg>')
        self.chrome.call("Page.navigate", {"url": self.url.replace("/manuscript.html", "/diagrams#example")}, self.session)
        self.wait_for("document.querySelector('#paper')?.textContent.includes('120')")
        self.wait_for("document.querySelector('#lint .source-note')?.textContent.includes('Checked on disk')")

        values = json.loads(values_path.read_text())
        values["values"]["cohort.n"].update(value=999, text="999")
        values_path.write_text(json.dumps(values))
        tokens = json.loads(tokens_path.read_text())
        tokens["roles"]["muted"] = "#123456"
        tokens_path.write_text(json.dumps(tokens))
        embed.write_text(embed.read_text().replace('fill="red"', 'fill="blue"'))
        self.wait_for("document.querySelector('#paper').textContent.includes('999') && document.querySelector('#paper [fill=blue]')")
        self.wait_for("!!document.querySelector('#paper [fill=\"#123456\"]')")
        self.wait_for("document.querySelector('#lint .source-note')?.textContent.includes('Checked on disk')")

        # A dependency refresh must not adopt disk JSON over unsaved local work.
        self.evaluate("""DiagramEditor.commit('local text', d => { d.nodes[0].text = 'Local draft'; });
            clearTimeout(DiagramEditor.state.saveTimer); DiagramEditor.state.blocked = 'error';""")
        values["values"]["cohort.n"].update(value=888, text="888")
        values_path.write_text(json.dumps(values))
        self.wait_for("document.querySelector('#paper').textContent.includes('888')")
        self.assertIn("Local draft", self.evaluate("document.querySelector('#paper').textContent"))
        self.assertNotIn("Local draft", path.read_text())

    @unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
    def test_composite_figure_previews_all_panels_and_opens_its_source(self):
        source = _support.ROOT / "manuscript/main/99_panels.md"
        source.write_text('# Composite figure\n\n::: {#fig:panels}\n'
            '![First](figures/external/example.png){#fig:panel-a}\n\n'
            '![Second](figures/external/ordinary.png){#fig:panel-b}\n\n'
            'Combined caption.\n:::\n')
        self.addCleanup(source.unlink, missing_ok=True)
        self.assertTrue(preview.build("manuscript"), preview.STATE["manuscript"]["error"])
        self.open("?view=figures")
        card = '.pv-fcard[data-key="manuscript|fig:panels"]'
        self.wait_for(f"document.querySelector({json.dumps(card)})")
        self.assertFalse(self.evaluate(f"!!document.querySelector('{card} a[href^=\"/diagrams\"]')"))
        self.assertEqual(self.evaluate(f"document.querySelectorAll('{card} .pv-art img').length"), 2)
        self.evaluate(f"document.querySelector('{card} .pv-thumb').click()")
        self.assertEqual(self.evaluate("document.querySelectorAll('.pv-lb-body img').length"), 2)
        self.evaluate("document.querySelector('[data-lb=close]').click()")
        self.assertTrue(self.evaluate(f"[...document.querySelectorAll('{card} button')].find(b => b.textContent === 'SVG').disabled"))
        self.evaluate(f"[...document.querySelectorAll('{card} button')].find(b => b.textContent === 'Edit source').click()")
        # Citeproc appends references inside this section without a separate title.
        self.wait_for("document.querySelector('textarea[aria-label=\"Markdown source\"]')?.value.includes('Combined caption')")

    @unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
    def test_a_figure_cited_from_another_document_is_cited(self):
        for folder, text in (("main", "# Cross\n\nSee [@fig:cross-extra].\n"),
                             ("supplementary", "# Cross\n\n![Extra.](figures/external/example.png){#fig:cross-extra}\n")):
            source = _support.ROOT / f"manuscript/{folder}/99_cross.md"
            source.write_text(text)
            self.addCleanup(source.unlink, missing_ok=True)
        words = {"number_prefix": "", "title_prefix": "Supplementary"}
        with mock.patch.dict(preview.PROJECT.config["documents"]["supplementary"], words):
            for name in ("manuscript", "supplementary"):
                self.assertTrue(preview.build(name), preview.STATE[name]["error"])
        self.open("?view=figures")
        card = json.dumps('.pv-fcard[data-key="supplementary|fig:cross-extra"]')
        self.wait_for(f"document.querySelector({card})?.textContent.includes('Cited in (1)')")
        self.assertEqual(self.evaluate(f"document.querySelector({card}).querySelector('.pv-flabel').textContent"),
                         "Supplementary Figure 1")
        self.assertEqual(self.evaluate(f"document.querySelector({card}).querySelector('.pv-where').textContent"),
                         "Manuscript › Cross ¶1")

    def test_captions_are_justified_and_exports_live_on_figure_cards(self):
        page = preview.OUT_DIR / "manuscript.html"
        # A figure title from [Title.]{.caption-title}; a table title from **bold**.
        page.write_text(page.read_text().replace('Diagram caption.', '<span class="caption-title">Diagram title.</span> Explanation.').replace('</section><script>',
            '<table id="tbl:demo"><caption>Table 1. <strong>Table title.</strong> Explanation.</caption><tbody><tr><td>1</td></tr></tbody></table></section><script>'))
        self.open()
        self.assertEqual(self.evaluate("Array.from(document.querySelectorAll('.pv-page figcaption, .pv-page caption'), c => getComputedStyle(c).textAlign)"),
                         ["justify", "justify", "justify"])
        self.assertFalse(self.evaluate("!!document.querySelector('.pv-page .pv-exports')"))
        self.assertFalse(self.evaluate("!!document.querySelector('.pv-page figcaption .pv-src-mark, .pv-page caption .pv-src-mark')"))
        self.assertTrue(self.evaluate("!!document.querySelector('#fig\\\\:diagram figcaption .pv-tbl-edit')"))
        self.assertEqual(self.evaluate("getComputedStyle(document.querySelector('.pv-page figcaption .caption-title')).fontWeight"), "600")
        self.open("?view=figures")
        self.wait_for("document.querySelectorAll('.pv-fcard').length > 0")
        self.assertTrue(self.evaluate("Array.from(document.querySelectorAll('.pv-fcard')).every(c => c.querySelector('.pv-exports'))"))
        self.assertEqual(self.evaluate("document.querySelector('.pv-figs-head h2').textContent"), "Figures & Tables")
        # A caption-title reads with the number; other bold stays in the legend.
        for identifier, title, mark in (("fig:diagram", "Diagram title.", ".caption-title"), ("tbl:demo", "Table title.", "strong")):
            with self.subTest(object=identifier):
                selector = f'.pv-fcard[data-key="manuscript|{identifier}"]'
                where = ".pv-fhead .pv-ftitle" if mark == ".caption-title" else ".pv-fcap strong"
                self.assertEqual(self.evaluate(f"document.querySelector('{selector} {where}').textContent"), title)
                self.assertEqual(self.evaluate(f"getComputedStyle(document.querySelector('{selector} {where}')).fontWeight"), "600")
                if mark == ".caption-title":
                    self.assertEqual(self.evaluate(f"document.querySelector('{selector} .pv-fcap').textContent"), "Explanation.")
                    self.assertEqual(self.evaluate(f"getComputedStyle(document.querySelector('{selector} .pv-flabel'), '::after').content"), '"."')
                self.evaluate(f"document.querySelector('{selector} .pv-thumb').click()")
                self.assertEqual(self.evaluate(f"document.querySelector('.pv-lb-cap {mark}').textContent"), title)
                self.assertEqual(self.evaluate(f"getComputedStyle(document.querySelector('.pv-lb-cap {mark}')).fontWeight"), "600")
                self.evaluate("document.querySelector('[data-lb=close]').click()")

    def test_document_figures_open_the_existing_viewer_by_mouse_and_keyboard(self):
        self.open()
        self.evaluate("document.getElementById('fig:external').querySelector('img').click()")
        self.assertFalse(self.evaluate("document.querySelector('.pv-lb').hidden"))
        self.assertIn("Figure 1", self.evaluate("document.querySelector('.pv-lb .ttl').textContent"))
        self.evaluate("document.querySelector('.pv-lb').dispatchEvent(new KeyboardEvent('keydown', {key:'ArrowRight', bubbles:true}))")
        self.assertIn("Figure 2", self.evaluate("document.querySelector('.pv-lb .ttl').textContent"))
        self.evaluate("document.querySelector('.pv-lb').dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true}))")
        self.assertTrue(self.evaluate("document.querySelector('.pv-lb').hidden"))
        self.assertTrue(self.evaluate("document.activeElement === document.getElementById('fig:external').querySelector('img')"))
        self.evaluate("document.activeElement.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true}))")
        self.assertFalse(self.evaluate("document.querySelector('.pv-lb').hidden"))

    def test_go_to_text_shows_the_figure_from_its_card_and_the_viewer(self):
        card = "document.querySelector('.pv-fcard[data-key=\"manuscript|fig:diagram\"]')"
        for surface, click in (("card", f"{card}.querySelector('[data-a=go]').click()"),
                               ("viewer", f"{card}.querySelector('[data-a=zoom]').click();"
                                          "document.querySelector('[data-lb=go]').click()")):
            with self.subTest(surface=surface):
                self.open("?view=figures")
                self.wait_for(card)
                self.evaluate(click)
                self.wait_for("document.getElementById('fig:diagram').classList.contains('pv-flash')")
                self.assertFalse(self.evaluate("document.querySelector('.pv-docview').hidden"))

    def test_caption_links_open_their_document_from_gallery_and_viewer(self):
        for name in preview.DOCS:
            path = preview.OUT_DIR / f"{name}.html"
            link = '<a href="#intro">Introduction</a>'
            html = path.read_text().replace('Diagram caption.', '<strong>Diagram title.</strong> See ' + link + '.')
            html = html.replace('</section><script>',
                '<table id="tbl:links"><caption>Table 1. <strong>Table title.</strong> See ' + link
                + '.</caption><tbody><tr><td>1</td></tr></tbody></table></section><script>')
            if name == "supplementary":
                html = html.replace('<h1>Introduction</h1>', '<h1>Supplementary introduction</h1>')
            path.write_text(html)
        for name, identifier, surface in (("manuscript", "fig:diagram", "gallery"),
                ("manuscript", "tbl:links", "viewer"), ("supplementary", "tbl:links", "gallery"),
                ("supplementary", "fig:diagram", "viewer")):
            with self.subTest(document=name, object=identifier, surface=surface):
                self.open("?view=figures")
                card = f'.pv-fcard[data-key="{name}|{identifier}"]'
                self.wait_for(f"document.querySelector('{card}')")
                selector = card + ' .pv-fcap a'
                if surface == "viewer":
                    self.evaluate(f"document.querySelector('{card} .pv-thumb').click()")
                    selector = '.pv-lb-cap a'
                # Native modified clicks must also retain the originating document.
                self.assertEqual(self.evaluate(f"new URL(document.querySelector('{selector}').href).pathname"), f"/{name}.html")
                self.evaluate(f"document.querySelector('{selector}').click()")
                self.wait_for(f"location.pathname === '/{name}.html' && document.querySelector('.pv-page #intro.pv-flash')")
                self.assertEqual(self.evaluate("({doc: !document.querySelector('.pv-docview').hidden, "
                    "gallery: !document.querySelector('.pv-figsview').hidden, modal: !document.querySelector('.pv-lb').hidden})"),
                    {"doc": True, "gallery": False, "modal": False})

    def test_gallery_undo_restores_the_other_documents_comment(self):
        comment = preview.change_comments({"op": "add", "doc": "supplementary",
            "para": "Figure 2 caption", "section": "Introduction", "quote": "Diagram caption.",
            "comment": "Fix the caption"})["comments"][0]
        self.open("?view=figures")
        self.wait_for("document.querySelector('.pv-card [data-op=resolve]')")
        self.evaluate("document.querySelector('.pv-card [data-op=resolve]').click()")
        self.wait_for("document.querySelector('.pv-toast button')?.textContent === 'Undo'")
        self.assertFalse(self.evaluate("!!document.querySelector('.pv-card.open')"))
        self.evaluate("document.querySelector('.pv-toast button').click()")
        self.wait_for("document.querySelector('.pv-card.open')")
        restored = next(c for c in preview.load_comments() if c["id"] == comment["id"])
        self.assertEqual(restored["status"], "open")
        # The document view's Undo has its own scope: ⌘Z there leaves it alone.
        preview.change_comments({"op": "update", "id": comment["id"], "status": "done"})
        self.evaluate("document.querySelector('a[data-doc=manuscript]').click()")
        self.wait_for("document.querySelector('.pv-empty b')?.textContent === 'No comments'")
        self.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'z', metaKey: true, bubbles: true}))")
        time.sleep(0.3)
        self.assertEqual(next(c for c in preview.load_comments() if c["id"] == comment["id"])["status"], "done")

    def test_gallery_refresh_relocates_comments_without_losing_a_draft(self):
        preview.change_comments({"op": "add", "doc": "supplementary",
            "para": "Figure 2 caption", "section": "Introduction", "quote": "Diagram caption.",
            "comment": "Fix the caption"})
        self.open("?view=figures")
        self.wait_for("document.querySelector('.pv-card [data-op=edit]')")
        self.evaluate("""(() => {
          document.querySelector('.pv-card [data-op=edit]').click();
          const field = document.querySelector('.pv-card textarea');
          field.value = 'Keep this unsaved draft';
          field.dispatchEvent(new Event('input', {bubbles: true}));
        })()""")
        page = preview.OUT_DIR / "supplementary.html"
        page.write_text(page.read_text().replace("Diagram caption.", "Updated caption."))
        with preview.LOCK:
            preview.STATE["supplementary"]["version"] += 1
        self.wait_for("Array.from(document.querySelectorAll('.pv-fcap')).some(p => p.textContent === 'Updated caption.')")
        self.wait_for("document.querySelector('.pv-card .pv-where').textContent.includes('text changed')")
        self.assertEqual(self.evaluate("document.querySelector('.pv-card textarea').value"), "Keep this unsaved draft")

    def test_gallery_refreshes_copies_fetched_before_the_first_status(self):
        self.fail_fetches("/__workspace")
        self.open("?view=figures")
        self.wait_for("window.failed > 0 && document.querySelectorAll('.pv-fcard').length === 4")
        page = preview.OUT_DIR / "supplementary.html"
        page.write_text(page.read_text().replace("Diagram caption.", "Updated caption."))
        with preview.LOCK:
            preview.STATE["supplementary"]["version"] += 1
        self.evaluate("window.serverUp = true")
        self.wait_for("Array.from(document.querySelectorAll('.pv-fcap')).some(p => p.textContent === 'Updated caption.')")

    def test_gallery_only_offers_edit_for_existing_diagrams(self):
        self.open("?view=figures")
        self.wait_for("document.querySelectorAll('.pv-fcard').length === 4")
        links = self.evaluate("Array.from(document.querySelectorAll('.pv-fcard a'), a => a.getAttribute('href'))")
        self.assertEqual(len(links), 2)
        for link in links:
            parsed = urlsplit(link)
            self.assertEqual((parsed.path, parsed.fragment), ("/diagrams", "example"))
            self.assertEqual(parse_qs(parsed.query)["return"], ["/manuscript.html?view=figures#fig:diagram"])

    def test_gallery_links_to_all_diagrams(self):
        self.open("?view=figures")
        self.wait_for("document.querySelector('.pv-figs-meta a') !== null")
        self.assertEqual(self.evaluate("""(() => { const a = document.querySelector('.pv-figs-meta a');
          return [a.getAttribute('href'), a.target, a.textContent]; })()"""),
                         ["/diagrams", "", "All diagrams"])


if __name__ == "__main__":
    unittest.main()
