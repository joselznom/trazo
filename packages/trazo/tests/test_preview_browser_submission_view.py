"""Viewer regressions in Chrome: the Submission view (articles)."""

import json
import shutil
import threading
import unittest

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)
from _preview_browser import PreviewBrowserTestCase

from trazo import preview


class SubmissionViewTests(PreviewBrowserTestCase):
    def subm_row(self, card, label):
        """Click the row of a Submission card with this label."""
        self.evaluate(f"""[...document.querySelectorAll('#pv-subm-{card} .pv-subm-row')]
            .find(r => r.querySelector('.pv-subm-label').textContent === {json.dumps(label)}).click()""")

    def test_submission_view_renders_cards_and_copies_plain_text(self):
        asked = self.serve_report(self.REPORT)
        self.open("?view=submission")
        self.wait_for("document.querySelector('#pv-subm-form .pv-subm-field')")
        self.assertEqual(asked[-1], True)
        self.assertEqual(self.evaluate("document.querySelector('[data-view=submission]').getAttribute('aria-current')"), "page")
        self.assertEqual(self.evaluate("[document.querySelector('.pv-docview').hidden, document.querySelector('.pv-figsview').hidden, document.querySelector('.pv-viewbar').hidden]"),
                         [True, True, True])
        self.assertEqual(self.evaluate("document.querySelector('.pv-subm-head h2').textContent"), "No journal set")
        self.assertIn("journal-submission skill", self.evaluate("document.querySelector('.pv-subm-hint').textContent"))
        self.assertEqual(self.evaluate("document.querySelector('.pv-subm-sum').textContent"), "2 to fix · 1 to check")
        self.assertEqual(self.evaluate("document.querySelector('.pv-words').textContent"), "2 to fix · 1 to check")
        self.assertEqual(self.evaluate("[...document.querySelectorAll('.pv-subm-card')].map(c => c.id)"),
                         ["pv-subm-journal", "pv-subm-limits", "pv-subm-metadata", "pv-subm-declarations",
                          "pv-subm-placeholders", "pv-subm-form"])
        self.assertEqual(self.evaluate("""[...document.querySelectorAll('#pv-subm-limits .pv-subm-row')].map(r =>
            [r.className, r.querySelector('.pv-subm-icon').getAttribute('aria-label'), r.querySelector('.pv-subm-label').textContent,
             r.querySelector('.pv-subm-value').textContent, r.querySelector('.pv-subm-detail').textContent])"""),
            [["pv-subm-row error", "To fix", "Main text", "120 / 100 words", "20 over the limit"],
             ["pv-subm-row ok", "OK", "Figures", "2 / 6", "4 left"]])
        # The whole row is the link, with nothing interactive inside; a
        # setting of trazo.toml is no link.
        self.assertEqual(self.evaluate("[...document.querySelectorAll('.pv-subm-row')].map(r => r.tagName === 'A' ? r.getAttribute('href') : null)"),
                         [None, "/manuscript.html", "/manuscript.html?view=figures", "/manuscript.html",
                          "/manuscript.html", "/manuscript.html#intro", "/supplementary.html#intro", "/manuscript.html"])
        self.assertEqual(self.evaluate("document.querySelectorAll('.pv-subm-row :is(a, button, input)').length"), 0)
        # The left sidebar lists the groups with what is to fix or check.
        self.assertEqual(self.evaluate("[...document.querySelectorAll('.pv-side.left [data-sub]')].map(b => [b.dataset.sub, b.textContent])"),
                         [["journal", "Journal1"], ["limits", "Limits1"], ["metadata", "Metadata0"],
                          ["declarations", "Declarations0"], ["placeholders", "Placeholders1"],
                          ["form", "For the submission form3"]])
        self.evaluate("document.querySelector('.pv-side.left [data-sub=form]').click()")
        self.wait_for("document.activeElement.id === 'pv-subm-form' && (s => s.scrollTop > 0"
                      " && s.scrollTop >= s.scrollHeight - s.clientHeight - 2)(document.querySelector('.pv-scroller'))")
        # Form fields: plain text, form-only marked, Copy writes the text and says so.
        self.assertEqual(self.evaluate("[...document.querySelectorAll('.pv-subm-field')].map(f => [f.querySelector('h4').textContent, !!f.querySelector('.pv-subm-tag'), f.querySelector('.pv-subm-copy').disabled, f.querySelector('.pv-subm-text').textContent])"),
                         [["Abstract", False, False, "Objectives: one “quoted”.\n\nResults: two."],
                          ["Funding", True, False, "Grant 42."], ["Short title", False, True, "Not set"]])
        self.evaluate("window.copied = []; navigator.clipboard.writeText = async text => { window.copied.push(text); }")
        self.evaluate("document.querySelector('.pv-subm-copy[aria-label=\"Copy Abstract\"]').click()")
        self.wait_for("document.querySelector('.pv-subm-copy[aria-label=\"Copy Abstract\"]').textContent === 'Copied'")
        self.assertEqual(self.evaluate("window.copied"), ["Objectives: one “quoted”.\n\nResults: two."])
        # The buttons work from the keyboard.
        self.evaluate("document.querySelector('.pv-subm-copy[aria-label=\"Copy Funding\"]').focus()")
        for kind in ("keyDown", "keyUp"):
            self.chrome.call("Input.dispatchKeyEvent", {"type": kind, "key": "Enter", "code": "Enter",
                                                        "windowsVirtualKeyCode": 13, "text": "\r"}, self.session)
        self.wait_for("window.copied.length === 2")
        self.assertEqual(self.evaluate("window.copied[1]"), "Grant 42.")
        self.wait_for("document.querySelector('.pv-subm-copy[aria-label=\"Copy Abstract\"]').textContent === 'Copy'")

    def test_the_view_shows_the_kept_report_then_a_fresh_check(self):
        kept, fresh = {**self.REPORT, "checked": 1.0}, {**self.REPORT, "errors": 5, "checked": 2e10}
        asked = []
        gate = threading.Event()

        def answer(fields, seconds):
            asked.append(seconds)
            if seconds is None:
                return kept
            gate.wait(10)
            return fresh
        self.serve_report(answer)
        self.addCleanup(gate.set)
        self.open("?view=submission")
        self.wait_for("document.querySelector('.pv-subm-again')?.textContent === 'Checking…'")
        self.assertEqual(self.evaluate("document.querySelector('.pv-subm-sum b').textContent"), "2 to fix")
        self.evaluate("dispatchEvent(new Event('focus'))")  # answered at once, with the kept report
        self.wait_for("document.querySelector('.pv-subm-again')?.textContent === 'Checking…'")
        gate.set()
        self.wait_for("document.querySelector('.pv-subm-sum b').textContent === '5 to fix'")
        self.assertEqual(asked[:2], [None, 0])
        self.evaluate("dispatchEvent(new Event('focus'))")  # the kept report is older than the one shown
        self.evaluate("new Promise(resolve => setTimeout(resolve, 300))")
        self.assertEqual(self.evaluate("document.querySelector('.pv-subm-sum b').textContent"), "5 to fix")
        self.evaluate("document.querySelector('.pv-subm-again').click()")
        self.wait_for("document.querySelector('.pv-toast')?.textContent === 'Nothing changed since the last check'")
        self.assertEqual(asked.count(0), 2)

    def test_opening_the_view_during_a_badge_check_still_gets_the_fields(self):
        gate = threading.Event()

        def answer(fields, seconds):
            if seconds is None:  # the kept full report is older than the badge's
                return {**self.REPORT, "checked": 5.0 if not fields else 1.0}
            if seconds == 60:
                gate.wait(10)
            return {**self.REPORT, "checked": 3e10}
        self.serve_report(answer)
        self.addCleanup(gate.set)
        self.open()
        self.wait_for("!document.querySelector('.pv-subm-toggle .pv-badge').hidden")
        self.evaluate("submissionAsked = 0; checkSubmissionNow()")  # the minute check, held open
        self.evaluate("document.querySelector('[data-view=submission]').click()")
        self.wait_for("document.querySelector('#pv-subm-form .pv-subm-field')")

    def test_submission_view_keeps_history_with_the_other_views(self):
        self.serve_report({**self.REPORT, "journal": {**self.REPORT["journal"], "journal": "Journal of Tests",
                                                      "guidelines": "https://example.org/authors"}})
        self.open()
        state = """[location.search, document.querySelector('.pv-docview').hidden, document.querySelector('.pv-figsview').hidden,
            document.querySelector('.pv-subm').hidden, [...document.querySelectorAll('.pv-tabs-top [aria-current=page]')].map(a => a.dataset.doc || a.dataset.view)]"""
        self.assertEqual(self.evaluate(state), ["", False, True, True, ["manuscript"]])
        self.evaluate("document.querySelector('[data-view=submission]').click()")
        self.wait_for("document.querySelector('.pv-subm-card')")
        self.assertEqual(self.evaluate(state), ["?view=submission", True, True, False, ["submission"]])
        self.assertEqual(self.evaluate("document.querySelector('.pv-subm-head h2').textContent"), "Journal of Tests")
        self.assertEqual(self.evaluate("document.querySelector('.pv-subm-meta a').getAttribute('href')"), "https://example.org/authors")
        self.assertEqual(self.evaluate("document.querySelector('.pv-side-title').textContent"), "Submission")
        self.evaluate("document.querySelector('[data-view=figures]').click()")
        self.wait_for("document.querySelector('.pv-fcard')")
        self.assertEqual(self.evaluate(state)[:4], ["?view=figures", True, False, True])
        self.evaluate("history.back()")
        self.wait_for("location.search === '?view=submission' && !document.querySelector('.pv-subm').hidden")
        self.evaluate("history.back()")
        self.wait_for("location.search === '' && !document.querySelector('.pv-docview').hidden")
        self.assertEqual(self.evaluate(state), ["", False, True, True, ["manuscript"]])
        self.assertEqual(self.evaluate("document.querySelector('.pv-words').textContent"), "5 words")
        self.assertEqual(self.evaluate("document.querySelector('.pv-side-title').textContent"), "Contents")
        self.evaluate("history.forward()")
        self.wait_for("location.search === '?view=submission' && !document.querySelector('.pv-subm').hidden")

    def test_submission_rows_open_where_they_are_fixed(self):
        page = preview.OUT_DIR / "manuscript.html"
        page.write_text(page.read_text().replace('<section class="level1"',
            '<div class="pk-front"><p>Ana</p></div><section class="level1"', 1).replace('<p>Another paragraph.</p>',
            '<div class="pk-keywords"><p>Keywords: term.</p></div><p>Another paragraph.</p>', 1).replace(
            'window.PREVIEW_FILES = [];',
            'window.PREVIEW_FILES = [{"file": "manuscript/main/01_introduction.md", "title": "Introduction"}];'))
        self.serve_report(self.REPORT)
        back = "!document.querySelector('.pv-subm').hidden && !!document.querySelector('.pv-subm-card')"
        flashed = "(document.querySelector('.pv-flash') || {}).className"
        tab = "document.querySelector('[role=tab][aria-selected=true]').dataset.tab"
        selected = """(() => { const t = document.querySelector('.pv-source textarea');
            return t.value.slice(t.selectionStart, t.selectionEnd); })()"""
        in_view = """(el => { const r = el.getBoundingClientRect(), s = document.querySelector('.pv-scroller').getBoundingClientRect();
            return r.top >= s.top && r.top < s.bottom; })"""
        self.open("?view=submission")
        self.wait_for(back)
        # The metadata: its block in the page, highlighted, without the Source tab.
        self.subm_row("metadata", "Keywords")
        self.wait_for("location.search === '' && !document.querySelector('.pv-docview').hidden"
                      " && !!document.querySelector('.pk-keywords.pv-flash')")
        self.wait_for(in_view + "(document.querySelector('.pk-keywords'))")
        self.assertEqual(self.evaluate(tab), "comments")
        self.evaluate("history.back()")
        self.wait_for(back)
        self.subm_row("metadata", "Authors")
        self.wait_for("!!document.querySelector('.pk-front.pv-flash')")
        self.evaluate("history.back()")
        self.wait_for(back)
        # A figure limit: the Figures & Tables view.
        self.subm_row("limits", "Figures")
        self.wait_for("location.search === '?view=figures'")
        self.evaluate("history.back()")
        self.wait_for(back)
        # A placeholder: its section, found by heading in its file's part of
        # the page, and its line in the Source tab.
        self.subm_row("placeholders", "manuscript/main/01_introduction.md:3")
        self.wait_for("!document.querySelector('.pv-docview').hidden && !!document.querySelector('#intro > h1.pv-flash')")
        self.wait_for(f"{tab} === 'source' && document.querySelector('.pv-source textarea').value.length > 0")
        self.wait_for(f"{selected} === 'We studied {{{{v:cohort.n}}}} patients [@smith2020].'")
        self.assertEqual(self.evaluate("location.search"), "")
        self.evaluate("history.back()")
        self.wait_for(back)
        # From the keyboard: Enter on a row opens it.
        self.evaluate("[...document.querySelectorAll('#pv-subm-limits .pv-subm-row')][0].focus()")
        self.key("Enter", 13)
        self.wait_for("location.search === '' && !document.querySelector('.pv-docview').hidden")
        self.evaluate("history.back()")
        self.wait_for(back)
        # Another document's section: that page lands on it and opens its line.
        self.subm_row("declarations", "Data")
        self.wait_for("location.pathname === '/supplementary.html' && !!document.querySelector('#intro > h1.pv-flash')")
        self.wait_for(f"{tab} === 'source' && document.querySelector('.pv-source textarea').value.length > 0")
        self.assertEqual(self.evaluate(selected), "Details.")
        self.assertEqual(self.evaluate("[location.search, location.hash, sessionStorage.getItem('preview-open-source:/supplementary.html')]"),
                         ["", "", None])
        self.assertEqual(self.evaluate(flashed), "pv-flash")

    @unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
    def test_abbreviation_help_and_checklist_source_navigation(self):
        from trazo import abbreviations
        source = preview.PROJECT.root / "manuscript/main/01_introduction.md"
        original = source.read_text()
        self.addCleanup(source.write_text, original)
        source.write_text("# Introduction {#intro}\n\nAUC rose. {{v:cohort.n}}.\n")
        glossary = source.with_name("02_abbreviations.md")
        self.addCleanup(glossary.unlink)
        glossary.write_text("# Abbreviations\n\nAUC\n:   Area under the curve\n")
        finding = next(item for item in abbreviations.check(preview.PROJECT, "manuscript")
                       if item["label"] == "Define at first use" and item["value"] == "AUC")
        error, warnings = preview.render("manuscript", preview.OUT_DIR)
        self.assertEqual(error, "", warnings)
        self.serve_report({"kind": "article", "errors": 0, "warnings": 1,
            "groups": [{"id": "abbreviations", "label": "Abbreviations", "items": [finding]}], "fields": []})
        self.open()
        self.assertEqual(self.evaluate("[document.querySelector('abbr').title, document.querySelector('abbr').textContent]"),
                         ["Area under the curve", "AUC"])
        self.assertEqual(self.evaluate("document.querySelector('abbr').closest('p').dataset.label"), "Introduction ¶1")
        self.assertEqual(self.evaluate("document.querySelector('.pv-val').textContent"), "120")
        self.assertEqual(self.evaluate("[!!document.querySelector('.pk-abbreviations table'), PreviewModel.items(document.querySelector('.pv-docview'), 'manuscript').length]"),
                         [True, 0])
        self.assertEqual(self.evaluate("(() => { const range = document.createRange(); range.selectNodeContents(document.querySelector('abbr').closest('p')); const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range); return selection.toString(); })()"),
                         "AUC rose. 120.")
        self.evaluate("getSelection().removeAllRanges(); document.querySelector('[data-view=submission]').click()")
        self.wait_for("!!document.querySelector('#pv-subm-abbreviations .pv-subm-row')")
        self.subm_row("abbreviations", "Define at first use")
        self.wait_for("document.querySelector('[role=tab][aria-selected=true]').dataset.tab === 'source' && document.querySelector('.pv-source textarea').value.includes('AUC rose.')")
        self.assertEqual(self.evaluate("(() => { const field = document.querySelector('.pv-source textarea'); return field.value.slice(field.selectionStart, field.selectionEnd); })()"), "AUC rose. {{v:cohort.n}}.")


if __name__ == "__main__":
    unittest.main()
