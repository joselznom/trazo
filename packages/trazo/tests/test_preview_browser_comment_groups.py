"""Viewer regressions in Chrome: the comments list grouped by section."""

import json
import unittest

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)
from _preview_browser import PreviewBrowserTestCase

from trazo import preview

LIST = """[...document.querySelectorAll('.pv-cgroup, .pv-card')].map(el => el.matches('.pv-cgroup')
  ? '# ' + el.querySelector('.pv-lbl').textContent : el.querySelector('.pv-text, .pv-ctext').textContent)"""


class CommentGroupTests(PreviewBrowserTestCase):
    def setUp(self):
        super().setUp()
        page = preview.OUT_DIR / "manuscript.html"
        page.write_text(page.read_text()
                        .replace('<section class="level1" id="intro">', '<p>Front words here.</p><section class="level1" id="intro">')
                        .replace("</section>", '</section><section class="level1" id="methods"><h1>Methods</h1>'
                                 '<p>Methods words here.</p><p><span id="later">More methods text.</span></p></section>'))

    def add(self, quote, comment, section="Methods", para="Methods ¶1", doc="manuscript"):
        return preview.change_comments({"op": "add", "doc": doc, "para": para, "section": section,
                                        "quote": quote, "comment": comment})["comments"][-1]["id"]

    def open_comments(self, query=""):
        self.open(query)
        self.evaluate("document.querySelector('[data-side=right]').click()")
        self.wait_for("document.querySelector('.pv-card')")

    def fold(self, title):
        self.evaluate(f"[...document.querySelectorAll('.pv-cgroup')].find(b => b.textContent.startsWith({json.dumps(title)})).click()")

    def test_comments_are_grouped_by_the_section_their_text_is_in_now(self):
        self.add("More methods text.", "Later in methods")
        intro = self.add("Another paragraph.", "In the introduction", "Introduction", "Introduction ¶2")
        self.add("Methods words here.", "Moved to methods", "Introduction", "Introduction ¶1")
        self.add("Vanished words", "Text and section gone", "Gone section", "Gone section ¶1")
        self.add("Vanished too", "Text gone, section kept")
        self.add("Front words here.", "Before the first section", "", "¶1")
        preview.change_comments({"op": "update", "id": intro, "status": "review"})
        self.open_comments()
        self.assertEqual(self.evaluate(LIST), [
            "# Front matter", "Before the first section",
            "# Introduction", "In the introduction",
            "# Methods", "Moved to methods", "Later in methods", "Text gone, section kept",
            "# Not found in the text", "Text and section gone"])
        badges = "[...document.querySelectorAll('.pv-cgroup .pv-bd')].map(b => b.className + ' ' + b.textContent)"
        self.assertEqual(self.evaluate(badges), ["pv-bd 1", "pv-bd review 1", "pv-bd 3", "pv-bd 1"])
        order = []
        for _ in range(6):
            self.key("j", 74)
            order.append(self.evaluate("document.querySelector('.pv-card.current .pv-text').textContent"))
        self.assertEqual(order, ["Before the first section", "In the introduction", "Moved to methods",
                                 "Later in methods", "Text gone, section kept", "Text and section gone"])

    def test_one_section_shows_no_headers(self):
        self.add("Another paragraph.", "Only here", "Introduction", "Introduction ¶2")
        self.open_comments()
        self.assertEqual(self.evaluate(LIST), ["Only here"])

    def test_a_folded_section_opens_for_j_and_for_a_click_on_its_text(self):
        self.add("Another paragraph.", "In the introduction", "Introduction", "Introduction ¶2")
        later = self.add("More methods text.", "Later in methods", para="Methods ¶2")
        self.open_comments()
        self.fold("Methods")
        self.assertEqual(self.evaluate(LIST), ["# Introduction", "In the introduction", "# Methods"])
        self.assertEqual(self.evaluate("document.querySelectorAll('.pv-cgroup')[1].getAttribute('aria-expanded')"), "false")
        self.key("j", 74)
        self.key("j", 74)
        self.wait_for(f"document.querySelector('.pv-card.current')?.dataset.id === {json.dumps(later)}")
        self.assertEqual(self.evaluate("document.querySelectorAll('.pv-cgroup')[1].getAttribute('aria-expanded')"), "true")
        self.key("j", 74)
        self.assertEqual(self.evaluate(LIST), ["# Introduction", "In the introduction", "# Methods"])
        self.click_text("#later")
        self.wait_for(f"document.querySelector('.pv-card.current')?.dataset.id === {json.dumps(later)}")
        visible = """(() => { const c = document.querySelector('.pv-card.current').getBoundingClientRect();
          const s = document.querySelector('[data-pane=comments] .pv-pane-scroll').getBoundingClientRect();
          return c.top >= s.top && c.bottom <= s.bottom; })()"""
        self.assertTrue(self.evaluate(visible))
        self.fold("Methods")
        self.assertEqual(self.evaluate(LIST), ["# Introduction", "In the introduction", "# Methods"])

    def test_a_caption_comment_is_in_its_section_and_figures_group_by_document(self):
        self.add("Another paragraph.", "In the introduction", "Introduction", "Introduction ¶2")
        self.add("More methods text.", "Later in methods", para="Methods ¶2")
        self.add("Diagram caption.", "Manuscript caption", "Introduction", "Figure 2 caption")
        self.add("Diagram caption.", "Supplement caption", "Introduction", "Figure 2 caption", doc="supplementary")
        self.open_comments()
        self.assertEqual(self.evaluate(LIST), ["# Introduction", "In the introduction", "Manuscript caption",
                                               "# Methods", "Later in methods"])
        self.open_comments("?view=figures")
        self.wait_for("document.querySelectorAll('.pv-cgroup').length === 2")
        titles = preview.PROJECT.titles
        self.assertEqual(self.evaluate(LIST), [f"# {titles['manuscript']}", "Manuscript caption",
                                               f"# {titles['supplementary']}", "Supplement caption"])

    def test_resolved_comments_are_grouped_newest_first(self):
        ids = {text: self.add(quote, text, section, para) for quote, text, section, para in (
            ("Another paragraph.", "Intro early", "Introduction", "Introduction ¶2"),
            ("Methods words here.", "Methods early", "Methods", "Methods ¶1"),
            ("More methods text.", "Methods late", "Methods", "Methods ¶2"),
            ("A word", "Intro late", "Introduction", "Introduction ¶1"))}
        for text in ids:
            preview.change_comments({"op": "update", "id": ids[text], "status": "done"})
        stored = json.loads(preview.COMMENTS_PATH.read_text())
        for i, text in enumerate(ids):
            next(c for c in stored if c["id"] == ids[text])["resolved"] = f"2026-01-0{i + 1} 10:00:00"
        preview.COMMENTS_PATH.write_text(json.dumps(stored))
        self.open()
        self.evaluate("document.querySelector('[data-side=right]').click()")
        self.evaluate("document.querySelector('[data-filter=resolved]').click()")
        self.wait_for("document.querySelector('.pv-card.done')")
        self.assertEqual(self.evaluate(LIST), ["# Introduction", "Intro late", "Intro early",
                                               "# Methods", "Methods late", "Methods early"])


if __name__ == "__main__":
    unittest.main()
