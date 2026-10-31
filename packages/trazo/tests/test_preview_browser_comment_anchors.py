"""Viewer regressions in Chrome: where a comment attaches (paragraphs, quotes and their context)."""

import json
import unittest

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)
from _preview_browser import PreviewBrowserTestCase

from trazo import preview


class CommentAnchorTests(PreviewBrowserTestCase):
    def test_generated_metadata_is_not_labelled_or_commentable(self):
        page = preview.OUT_DIR / "manuscript.html"
        page.write_text(page.read_text().replace('<section class="level1"',
            '<div class="pk-front"><div data-custom-style="Author"><p>A word, H2O.</p></div></div>'
            '<section class="level1"', 1).replace('<p>Another paragraph.</p>',
            '<div class="pk-keywords"><p>Keywords: term.</p></div><p>Another paragraph.</p>'))
        self.open()
        self.assertEqual(self.evaluate("[...document.querySelectorAll('.pk-front [data-label], .pk-keywords [data-label], .pk-front .pv-para, .pk-keywords .pv-para')].length"), 0)
        self.assertEqual(self.evaluate("[...document.querySelectorAll('.pv-page p[data-label]')].map(el => el.dataset.label)"),
                         ["Introduction ¶1", "Introduction ¶2"])
        self.assertEqual(self.evaluate("getComputedStyle(document.querySelector('.pk-front p')).fontWeight"), "500")

    def test_agent_reply_navigates_before_and_after_a_rewrite(self):
        for rewritten in (False, True, "complete"):
            with self.subTest(rewritten=rewritten):
                preview.COMMENTS_PATH.unlink(missing_ok=True)
                comment = preview.change_comments({"op": "add", "doc": "manuscript",
                    "para": "Introduction ¶2", "section": "Introduction", "quote": "Another paragraph.",
                    "anchor": {"text": "Another paragraph.", "before": "A word, H2O.",
                               "after": "Figure 1. External image.", "sectionId": "intro"},
                    "comment": "Clarify"})["comments"][0]
                preview.change_comments({"op": "reply", "id": comment["id"], "by": "agent", "text": "Done"})
                page = preview.OUT_DIR / "manuscript.html"
                original = page.read_text()
                if rewritten == "complete":
                    page.write_text(original.replace("<p>Another paragraph.</p>",
                        "<p>The finding now has a more precise interpretation.</p>"))
                elif rewritten:
                    page.write_text(original.replace("<p>Another paragraph.</p>",
                        "<p>Inserted paragraph.</p><p>Another, clarified paragraph.</p>"))
                self.open()
                self.wait_for("document.querySelector('.pv-card.review')")
                self.evaluate("document.querySelector('.pv-card.review .pv-text').click()")
                self.wait_for("document.querySelector('.pv-flash')")
                expected = ("The finding now has a more precise interpretation." if rewritten == "complete"
                            else "Another, clarified paragraph." if rewritten else "Another paragraph.")
                self.assertEqual(self.evaluate("document.querySelector('.pv-flash').textContent.replace(/¶\\d+|✎/g, '').trim()"), expected)
                page.write_text(original)

    def test_context_beats_an_unrelated_quote_and_refuses_deleted_or_ambiguous_targets(self):
        page = preview.OUT_DIR / "manuscript.html"
        original = page.read_text()
        for variant in ("rewritten", "deleted", "ambiguous"):
            with self.subTest(variant=variant):
                preview.COMMENTS_PATH.unlink(missing_ok=True)
                preview.change_comments({"op": "add", "doc": "manuscript", "para": "Introduction ¶2",
                    "section": "Introduction", "quote": "Another paragraph.", "comment": "Clarify",
                    "anchor": {"text": "Another paragraph.", "before": "A word, H2O.",
                               "after": "Figure 1. External image.", "sectionId": "intro"}})
                changed = {"rewritten": "<p>Another, clarified paragraph.</p>",
                           "deleted": "", "ambiguous": "<p>Unrelated text.</p>"}[variant]
                text = original.replace("<p>Another paragraph.</p>", changed)
                if variant == "rewritten":
                    text = text.replace("</section>", '<p>Another paragraph.</p><p>Another paragraph.</p></section>')
                elif variant == "ambiguous":
                    # Two rewritten blocks have the same original neighbours:
                    # neither is a uniquely identifiable target.
                    text = text.replace("</section>",
                        '<p>A <em>word</em>, H<sub>2</sub>O.</p><p>Entirely different explanation.</p>'
                        '<figure id="fig:duplicate"><img src="figures/external/ordinary.png">'
                        '<figcaption>Figure 1. External image.</figcaption></figure></section>')
                page.write_text(text)
                self.open()
                self.wait_for("document.querySelector('.pv-card .pv-text')")
                self.evaluate("document.querySelector('.pv-card .pv-text').click()")
                if variant == "rewritten":
                    self.wait_for("document.querySelector('.pv-flash')")
                    self.assertIn("clarified", self.evaluate("document.querySelector('.pv-flash').textContent"))
                else:
                    self.wait_for("document.querySelector('.pv-card.lost')")
                    self.assertFalse(self.evaluate("!!document.querySelector('.pv-flash')"))

    def test_a_comment_whose_text_is_gone_marks_its_paragraph_and_does_not_hide_a_later_one(self):
        page = preview.OUT_DIR / "manuscript.html"
        page.write_text(page.read_text().replace("<p>Another paragraph.</p>",
            '<p>The <span id="first">finding</span> now has a more <span id="later">precise</span> interpretation.</p>'))
        old = preview.change_comments({"op": "add", "doc": "manuscript", "para": "Introduction ¶2",
            "section": "Introduction", "quote": "Another paragraph.", "comment": "Clarify",
            "anchor": {"text": "Another paragraph.", "before": "A word, H2O.",
                       "after": "Figure 1. External image.", "sectionId": "intro"}})["comments"][-1]["id"]
        new = preview.change_comments({"op": "add", "doc": "manuscript", "para": "Introduction ¶2",
            "section": "Introduction", "quote": "precise", "comment": "Which word"})["comments"][-1]["id"]
        self.open()
        self.wait_for("document.querySelectorAll('.pv-card').length === 2")
        card = lambda comment_id: f"document.querySelector('.pv-card[data-id=\"{comment_id}\"]')"
        # Only the later comment has text to paint; the other marks the margin.
        self.assertEqual(self.evaluate("[...CSS.highlights.get('pv-open')].map(r => r.toString())"), ["precise"])
        self.assertEqual(self.evaluate("document.querySelector('[data-label=\"Introduction ¶2\"]').dataset.lost"), "open")
        self.assertIn("target rewritten", self.evaluate(card(old) + ".querySelector('.pv-where').textContent"))
        self.assertEqual(self.evaluate(card(old) + ".querySelector('blockquote').textContent"), "Another paragraph.")
        # Clicking elsewhere in the paragraph picks nothing; the later word picks only its comment.
        self.evaluate("document.querySelector('[data-side=right]').click()")
        self.click_text("#first")
        self.assertFalse(self.evaluate("!!document.querySelector('.pv-card.current')"))
        self.click_text("#later")
        self.wait_for("document.querySelector('.pv-card.current')")
        self.click_text("#later")
        self.assertEqual(self.evaluate("document.querySelector('.pv-card.current').dataset.id"), new)
        # The rewritten comment's card still goes to its paragraph, and the stepper reaches both.
        self.evaluate(card(old) + ".querySelector('.pv-text').click()")
        self.wait_for("document.querySelector('.pv-flash')")
        self.assertEqual(self.evaluate("document.querySelector('.pv-card.current').dataset.id"), old)
        self.evaluate("document.querySelector('[data-step=\"1\"]').click()")
        self.assertEqual(self.evaluate("document.querySelector('.pv-card.current').dataset.id"), new)

    def test_a_repeated_quote_stays_on_the_occurrence_that_was_selected(self):
        page = preview.OUT_DIR / "manuscript.html"
        page.write_text(page.read_text().replace("<p>Another paragraph.</p>",
            '<p>Alpha beta, then <span id="second">beta</span> again.</p>'))
        # A comment made before prefix and suffix existed finds the first one.
        old = preview.change_comments({"op": "add", "doc": "manuscript", "para": "Introduction ¶2",
            "section": "Introduction", "quote": "beta", "comment": "Old",
            "anchor": {"text": "Alpha beta, then beta again.", "sectionId": "intro"}})["comments"][-1]["id"]
        self.open()
        self.wait_for("document.querySelectorAll('.pv-card').length === 1")
        self.evaluate("""(() => {
          const p = document.querySelector('[data-label="Introduction ¶2"]');
          const range = document.createRange(); range.selectNodeContents(document.querySelector('#second'));
          const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
          p.dispatchEvent(new MouseEvent('mouseup', {bubbles:true}));
        })()""")
        self.wait_for("document.querySelector('.pv-fab').hidden === false")
        self.evaluate("""(() => {
          document.querySelector('[data-select=comment]').click();
          const area = document.querySelector('.pv-editor textarea');
          area.value = 'New';
          area.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', metaKey: true, bubbles: true}));
        })()""")
        self.wait_for("document.querySelectorAll('.pv-card').length === 2")
        added = preview.load_comments()[-1]
        self.assertEqual((added["anchor"]["prefix"], added["anchor"]["suffix"]), ("Alpha beta, then ", " again."))
        # How far into the paragraph the current comment's highlight starts.
        start = """(id) => {
          document.querySelector(`.pv-card[data-id="${id}"] .pv-text`).click();
          const r = [...CSS.highlights.get('pv-open-sel')][0];
          const before = document.createRange();
          before.setStart(document.querySelector('[data-label="Introduction ¶2"]'), 0);
          before.setEnd(r.startContainer, r.startOffset);
          return [r.toString(), before.toString().length];
        }"""
        first = self.evaluate(f"({start})({json.dumps(old)})")
        second = self.evaluate(f"({start})({json.dumps(added['id'])})")
        self.assertEqual((first[0], second[0]), ("beta", "beta"))
        self.assertEqual(second[1] - first[1], len("beta, then "))

    def comment_on_selection(self, label, boundaries, text):
        """Select text in a paragraph (boundaries: JS returning [node, offset, node, offset]) and comment on it."""
        self.evaluate(f"""(() => {{
          const p = document.querySelector('[data-label="{label}"]');
          const range = document.createRange();
          const [a, i, b, j] = ({boundaries})(p);
          range.setStart(a, i); range.setEnd(b, j);
          window.picked = [a, i];
          const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
          p.dispatchEvent(new MouseEvent('mouseup', {{bubbles: true}}));
        }})()""")
        self.wait_for("document.querySelector('.pv-fab').hidden === false")
        self.evaluate(f"""(() => {{
          document.querySelector('[data-select=comment]').click();
          const area = document.querySelector('.pv-editor textarea');
          area.value = {json.dumps(text)};
          area.dispatchEvent(new KeyboardEvent('keydown', {{key: 'Enter', metaKey: true, bubbles: true}}));
        }})()""")
        self.wait_for(f"document.querySelector('.pv-card.current .pv-text')?.textContent === {json.dumps(text)}")

    def test_a_selection_ending_mid_text_or_inside_markup_keeps_its_own_occurrence(self):
        page = preview.OUT_DIR / "manuscript.html"
        page.write_text(page.read_text().replace("<p>Another paragraph.</p>",
            "<p>Alpha beta, then beta, again.</p>"
            "<p>Alpha beta, then <em>x beta, y</em> end.</p>"
            "<p>Alpha beta, then <em>x be</em>ta, y end.</p>"))
        self.open()
        text_in = 'const text = (p, needle) => { const w = document.createTreeWalker(p, NodeFilter.SHOW_TEXT); while (w.nextNode()) if (w.currentNode.data.includes(needle)) return w.currentNode; };'
        cases = {  # the second "beta", as a selection ending before the comma
            "Introduction ¶2": ("(p) => { " + text_in + " const n = text(p, 'Alpha'); const i = n.data.lastIndexOf('beta'); return [n, i, n, i + 4]; }",
                                ("Alpha beta, then ", ", again.")),
            "Introduction ¶3": ("(p) => { const n = p.querySelector('em').firstChild; return [n, 2, n, 6]; }",
                                ("Alpha beta, then x ", ", y end.")),
            "Introduction ¶4": ("(p) => { const n = p.querySelector('em').firstChild; return [n, 2, n.parentNode.nextSibling, 2]; }",
                                ("Alpha beta, then x ", ", y end.")),
        }
        for number, (label, (boundaries, around)) in enumerate(cases.items(), 1):
            with self.subTest(label=label):
                self.comment_on_selection(label, boundaries, f"Second {number}")
                self.assertEqual(self.evaluate("document.querySelectorAll('.pv-card').length"), number)
                added = preview.load_comments()[-1]
                self.assertEqual((added["quote"], added["anchor"]["prefix"], added["anchor"]["suffix"]), ("beta", *around))
                # Its highlight starts where the selection did, not at the first "beta".
                self.assertEqual(self.evaluate("""(() => {
                  const r = [...CSS.highlights.get('pv-open-sel')][0];
                  return r.toString() === 'beta' && r.startContainer === window.picked[0] && r.startOffset === window.picked[1];
                })()"""), True)

    def test_a_quote_of_only_whitespace_is_no_quote_and_does_not_hang_the_page(self):
        cid = preview.change_comments({"op": "add", "doc": "manuscript", "para": "Introduction ¶2",
            "section": "Introduction", "quote": "Another", "comment": "Blank",
            "anchor": {"prefix": "x", "suffix": "y", "sectionId": "intro"}})["comments"][-1]["id"]
        comments = preview.load_comments()  # written by an older version, or by hand
        comments[0]["quote"] = "  "
        preview.PreviewComments().save(comments)
        self.open()
        self.wait_for(f"document.querySelector('.pv-card[data-id=\"{cid}\"]')")
        self.assertEqual(self.evaluate("[...CSS.highlights.values()].reduce((n, hl) => n + hl.size, 0)"), 0)
        self.assertFalse(self.evaluate("!!document.querySelector('.pv-lost')"))

    def test_a_reload_never_shows_a_place_found_on_an_older_page_or_comment(self):
        # The viewer keeps where it found each comment across reloads of the tab.
        cid = preview.change_comments({"op": "add", "doc": "manuscript", "para": "Introduction ¶2",
            "section": "Introduction", "quote": "Another paragraph.", "comment": "Clarify",
            "anchor": {"text": "Another paragraph.", "before": "A word, H2O.",
                       "after": "Figure 1. External image.", "sectionId": "intro"}})["comments"][-1]["id"]
        where = f"document.querySelector('.pv-card[data-id=\"{cid}\"] .pv-where')?.textContent"
        page = preview.OUT_DIR / "manuscript.html"
        for step, expected in (("first load", "Introduction ¶2"), ("nothing changed", "Introduction ¶2"),
                               ("page rebuilt", "Introduction ¶3"), ("comment edited", "Introduction ¶1")):
            with self.subTest(step):
                if step == "page rebuilt":  # a paragraph inserted before the quoted one
                    page.write_text(page.read_text().replace("<p>Another paragraph.</p>",
                        "<p>Inserted paragraph.</p><p>Another paragraph.</p>"))
                elif step == "comment edited":  # by hand or by the agent; same id
                    comments = preview.load_comments()
                    comments[0].update(quote="A word, H2O.", anchor={"text": "A word, H2O.", "sectionId": "intro"})
                    preview.PreviewComments().save(comments)
                self.open()
                self.wait_for(f"{where} !== undefined")
                self.assertEqual(self.evaluate(where), expected)

    def test_supplementary_prefix_keeps_legacy_caption_comments_and_visible_numbers(self):
        page = preview.OUT_DIR / "supplementary.html"
        page.write_text(page.read_text().replace('Figure 2. Diagram caption.', 'Figure S2. Diagram caption.')
            .replace('</section><script>', '<table id="tbl:legacy"><caption>Table S1. Legacy table.</caption>'
                '<tbody><tr><td>1</td></tr></tbody></table></section><script>'))
        comments = []
        for para, quote in (("Figure 2 caption", "Figure 2. Diagram caption."),
                            ("Table 1", "Table 1. Legacy table.")):
            comments.append(preview.change_comments({"op": "add", "doc": "supplementary",
                "para": para, "section": "Introduction", "quote": quote,
                "comment": "Review this caption"})["comments"][-1])
        self.chrome.call("Page.navigate", {"url": self.url.replace("manuscript.html", "supplementary.html")}, self.session)
        self.wait_for("document.querySelectorAll('.pv-card.open').length === 2")
        self.assertFalse(self.evaluate("!!document.querySelector('.pv-card.lost')"))
        for comment, label in zip(comments, ("Figure S2 caption", "Table S1")):
            self.assertEqual(self.evaluate(f"document.querySelector('.pv-card[data-id=\"{comment['id']}\"] .pv-where').textContent"), label)
        self.assertEqual([c["quote"] for c in preview.load_comments()], [c["quote"] for c in comments])
        self.evaluate("document.querySelector('[data-view=figures]').click()")
        self.wait_for("document.querySelector('.pv-fcard[data-key=\"supplementary|tbl:legacy\"]')")
        for identifier, expected in (("fig:diagram", "Figure S2"), ("tbl:legacy", "Table S1")):
            self.assertEqual(self.evaluate(f"document.querySelector('.pv-fcard[data-key=\"supplementary|{identifier}\"] .pv-flabel').textContent"), expected)


if __name__ == "__main__":
    unittest.main()
