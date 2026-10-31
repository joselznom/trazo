"""Viewer regressions in Chrome: word, figure and table counts in the top bar."""

import subprocess
import unittest
from unittest.mock import patch

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)
from _preview_browser import PreviewBrowserTestCase

from trazo import preview


class CountTests(PreviewBrowserTestCase):
    def test_word_count_ignores_inline_markup_boundaries(self):
        self.open()
        self.assertEqual(self.evaluate("document.querySelector('.pv-words').textContent"), "5 words")

    def open_counts_page(self, **limits):
        """The manuscript page as test_counts.SAMPLE, served with these [limits]."""
        import copy

        import test_counts

        from trazo import counts
        page = preview.OUT_DIR / "manuscript.html"
        html = page.read_text(encoding="utf-8")
        start, end = html.index("<body>") + len("<body>"), html.index("<script>")
        body = test_counts.SAMPLE.split("<body>", 1)[1].split("</body>", 1)[0]
        page.write_text(html[:start] + body + html[end:], encoding="utf-8")
        config = copy.deepcopy(preview.PROJECT.config)
        config["limits"].update(limits)
        with patch.object(counts, "current_config", return_value=config):
            self.open()

    def test_form_only_badge_keeps_labels_and_deep_outline_counts(self):
        from trazo import expand
        source = "# Statements\n\n## Administrative {.form-only}\n\nForm information.\n\n" \
                 "##### Detail\n\nNested information.\n\n###### More\n\nMore information.\n"
        marked = expand.form_sections(source, mark=True)
        html = subprocess.run(["pandoc", "-f", "markdown", "-t", "html", "--section-divs"],
            input=marked, text=True, capture_output=True, check=True).stdout
        page = preview.OUT_DIR / "manuscript.html"
        page.write_text(page.read_text().replace("<script>", html + "<script>", 1))
        self.open()
        self.assertEqual(self.evaluate("document.querySelector('.pv-words').textContent"), "5 words")
        self.assertEqual(self.evaluate("[...document.querySelectorAll('#intro p[data-label]')].map(el => el.dataset.label)"),
                         ["Introduction ¶1", "Introduction ¶2"])
        self.assertFalse(self.evaluate("!!document.querySelector('.pk-form-badge [data-label]')"))
        self.assertEqual(self.evaluate("document.querySelector('.pk-form-badge').textContent.trim()"),
                         "Form only – not in the Word file")
        self.assertTrue(self.evaluate("PreviewModel.outline(document.querySelector('.pv-page'), window.PREVIEW_COUNTS).known"))

    def test_counts_follow_journal_rules_and_limits(self):
        self.open_counts_page(words=8, tables=1)
        self.assertEqual(self.evaluate("document.querySelector('.pv-words').textContent"), "11 / 8 words")
        self.assertTrue(self.evaluate("document.querySelector('.pv-words').classList.contains('over')"))
        # Words with a limit get a meter and say how far over they are; a
        # count without a limit is just the number.
        counts = self.evaluate("""Object.fromEntries([...document.querySelectorAll('.pv-counts [data-count]')].map(el =>
            [el.dataset.count, [el.className, el.innerText.replace(/\\s+/g, ' ').trim(),
                                !!el.querySelector('[role=meter]')]]))""")
        self.assertEqual(counts, {
            "words": ["pv-meter pv-meter-words over", "Main text 11 / 8 words 3 over the limit", True],
            "abstract": ["pv-meter pv-meter-abstract", "Abstract 4 words", False],
            "figures": ["pv-tile", "1 Figures", False],
            "tables": ["pv-tile near", "1 / 1 Tables At the limit", False],
            "references": ["pv-tile", "2 References", False]})
        self.assertFalse(self.evaluate("!!document.querySelector('.pv-counts-hint')"))
        self.assertIn("Not in the main text: Abstract (4), Use of AI tools (4), Journal form (3), Declarations (5)",
                      self.evaluate("document.querySelector('.pv-words').title"))
        # Sections outside the main text show no number in the index.
        index = self.evaluate("""[...document.querySelectorAll('.pv-ix')].map(b =>
            [b.querySelector('.pv-lbl').textContent, b.querySelector('.pv-wc').textContent])""")
        self.assertEqual(index[:5], [["Abstract", ""], ["1. Introduction", "11"], ["Use of AI tools", ""],
                                     ["Funding models", "7"], ["Journal form", ""]])

    def test_top_bar_turns_red_only_for_the_words(self):
        self.open_counts_page(words=100, references=1, abstract=2)
        self.assertEqual(self.evaluate("document.querySelector('.pv-words').textContent"), "11 / 100 words")
        self.assertFalse(self.evaluate("document.querySelector('.pv-words').classList.contains('over')"))
        self.assertEqual(self.evaluate("document.querySelector('[data-count=references]').className"), "pv-tile over")
        self.assertEqual(self.evaluate("document.querySelector('[data-count=abstract]').className"),
                         "pv-meter pv-meter-abstract over")

    def test_counts_without_limits_say_where_to_set_them(self):
        self.open()
        self.assertEqual(self.evaluate("document.querySelector('.pv-words').textContent"), "5 words")
        self.assertFalse(self.evaluate("!!document.querySelector('.pv-counts [role=meter]')"))
        self.assertIn("[limits]", self.evaluate("document.querySelector('.pv-counts-hint').textContent"))

    def test_submission_documents_show_only_total_words(self):
        from trazo import counts
        from trazo.project import DEFAULTS, Project, merge
        root = preview.OUT_DIR
        for name in ("declarations", "cover-letter"):
            folder = root / "manuscript" / name
            folder.mkdir(parents=True)
            (folder / "source.md").write_text("Submission source.")
        project = Project(root, merge(DEFAULTS, {"submission": {
            "title_page": "separate", "declarations": "form"}}), True)
        original = (root / "manuscript.html").read_text()
        cases = {
            "declarations": ('<section id="decl-ethics"><h1>Ethics approval</h1>'
                             '<p>Author confirmed consent.</p></section>'
                             '<section class="form-only"><h2>Journal form</h2>'
                             '<p>Private form information.</p>'
                             '<span class="pk-form-badge">Form only – not in the Word file</span></section>', 10),
            "title_page": ('<div class="pk-front"><h1>Study title</h1>'
                           '<p>First Author</p><p>Example University</p></div>', 6),
            "cover_letter": ('<p>Dear Editor,</p><p>Please consider our manuscript.</p>', 6),
        }
        for name, (body, expected) in cases.items():
            with self.subTest(document=name):
                start, end = original.index("<body>") + len("<body>"), original.index("<script>")
                html = (original[:start] + body + original[end:]).replace(
                    'window.PREVIEW_DOC = "manuscript";', f'window.PREVIEW_DOC = "{name}";')
                (root / f"{name}.html").write_text(html)
                self.url = self.url.rsplit("/", 1)[0] + f"/{name}.html"
                with patch.object(counts, "PROJECT", project), patch.object(preview, "PROJECT", project), \
                        patch.object(preview, "DOCS", project.documents):
                    self.open()
                self.assertEqual(self.evaluate("document.querySelector('.pv-words').textContent"),
                                 f"{expected} words")
                self.assertEqual(self.evaluate("document.querySelector('.pv-counts').innerText.replace(/\\s+/g, ' ').trim()"),
                                 f"Total {expected} words")
                self.assertEqual(self.evaluate("document.querySelectorAll('.pv-counts [data-count]').length"), 1)
                self.assertFalse(self.evaluate("!!document.querySelector('.pv-counts .pv-tiles, .pv-counts-hint, .pv-counts [role=meter]')"))


if __name__ == "__main__":
    unittest.main()
