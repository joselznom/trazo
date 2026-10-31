"""References between a project's documents: a label not in
the citing document resolves in the others, through numbering metadata."""

import unittest

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)
from test_crossref import NBSP, cite, doc, figure, header, meta, para, table

from trazo import crossref, numbering

SUPPLEMENTARY = {"fig:qc": "fig 1", "fig:grid": "fig 2", "fig:panel": "fig 2 a", "fig:b2": "fig 2",
                 "fig:c3": "fig 3", "tbl:extra": "tbl 1", "sec:sens": "sec 2.1", "eq:model": "eq 1"}


def documents(current, *, supplementary=SUPPLEMENTARY, manuscript=None, prefix="S", title_prefix="",
              extra=None):
    """The numbering metadata a document's pass gets: its name and every
    document's labels and reference templates, as numbering.metadata writes
    them (here without Pandoc's Markdown reading)."""
    entries = [("manuscript", "Manuscript", manuscript or {"fig:trend": "fig 1", "fig:a": "fig 1"}, "", ""),
               ("supplementary", "Supplementary", supplementary, prefix, title_prefix),
               *(extra or [])]
    keys = {"trazo-document": meta(current), "trazo-documents": meta([
        {"name": name, "title": title, "labels": labels if name != current else {},
         **numbering.reference_templates(number_prefix, words)}
        for name, title, labels, number_prefix, words in entries])}
    own = next(entry for entry in entries if entry[0] == current)
    if own[3] or own[4]:
        keys.update({key: meta(value) for key, value in numbering.reference_templates(own[3], own[4]).items()})
    return keys


def shown(*inlines, blocks=(), link=False, **keys):
    warnings = []
    out = crossref.apply(doc(*blocks, para(*inlines), **keys), warn=warnings.append, link_references=link)
    return crossref.plain(out["blocks"][-1]["c"]), warnings, out


class BetweenDocuments(unittest.TestCase):
    def test_the_manuscript_cites_a_supplementary_figure_and_table(self):
        text, warnings, _ = shown(cite("fig:qc"), {"t": "Space"}, cite("tbl:extra"), **documents("manuscript"))
        self.assertEqual(text, f"Figure{NBSP}S1 Table{NBSP}S1")
        self.assertEqual(warnings, [])

    def test_the_supplementary_cites_a_manuscript_figure(self):
        text, warnings, _ = shown(cite("fig:trend"), **documents("supplementary"))
        self.assertEqual(text, f"Figure{NBSP}1")
        self.assertEqual(warnings, [])

    def test_a_panel_and_a_section_carry_the_document_prefix(self):
        text, _, _ = shown(cite("fig:panel"), {"t": "Space"}, cite("sec:sens"), **documents("manuscript"))
        self.assertEqual(text, f"Figure{NBSP}S2 (a) Section{NBSP}S2.1")

    def test_own_items_come_first_and_ranges_stay_within_a_document(self):
        text, _, _ = shown(cite("fig:c3", "fig:a", "fig:qc", "fig:grid"), blocks=[figure("fig:a", "A")],
                           **documents("manuscript", manuscript={"fig:trend": "fig 1"}))
        self.assertEqual(text, f"Figure{NBSP}1, S1-S3")
        text, _, _ = shown(cite("fig:qc", "fig:a"), blocks=[figure("fig:a", "A")],
                           **documents("manuscript", manuscript={"fig:trend": "fig 1"}))
        self.assertEqual(text, f"Figure{NBSP}1, S1")
        text, _, _ = shown(cite("fig:qc", "fig:a"), blocks=[figure("fig:a", "A")], figPrefix=meta(["Figure", "Figures"]),
                           **documents("manuscript", manuscript={"fig:trend": "fig 1"}))
        self.assertEqual(text, f"Figures{NBSP}1, S1")

    def test_references_follow_the_documents_order_from_the_supplementary(self):
        text, _, _ = shown(cite("fig:a", "fig:trend"), blocks=[figure("fig:a", "A")],
                           **documents("supplementary", supplementary={}))
        self.assertEqual(text, f"Figure{NBSP}1, S1")

    def test_title_prefix_words_the_reference_and_splits_mixed_citations(self):
        keys = documents("manuscript", prefix="", title_prefix="Supplementary")
        text, _, _ = shown(cite("fig:qc"), {"t": "Space"}, cite("Fig:qc"), **keys)
        self.assertEqual(text, f"Supplementary Figure{NBSP}1 Supplementary Figure{NBSP}1")
        text, warnings, _ = shown(cite("fig:a", "fig:qc", "fig:grid"), blocks=[figure("fig:a", "A")],
                                  **documents("manuscript", manuscript={}, prefix="", title_prefix="Supplementary"))
        self.assertEqual(text, f"Figure{NBSP}1, Supplementary Figure{NBSP}1, 2")
        self.assertEqual(warnings, [])

    def test_title_prefix_and_number_prefix_in_the_documents_own_captions(self):
        keys = {key: meta(value) for key, value in numbering.document_templates("S", "Supplementary").items()}
        out = crossref.apply(doc(figure("fig:a", "A"), para(cite("fig:a")), **keys))
        self.assertEqual(crossref.plain(out["blocks"][0]["c"][1][1][0]["c"]), "**Supplementary Figure S1.** A")
        self.assertEqual(crossref.plain(out["blocks"][1]["c"]), f"Supplementary Figure{NBSP}S1")

    def test_links_open_the_other_documents_page(self):
        _, _, out = shown(cite("fig:qc"), link=True, **documents("manuscript"))
        link = out["blocks"][-1]["c"][1]
        self.assertEqual(link["t"], "Link")
        self.assertEqual(link["c"][2], ["supplementary.html#fig:qc", ""])

    def test_the_metadata_does_not_reach_the_output(self):
        _, _, out = shown(cite("fig:qc"), **documents("manuscript"))
        self.assertFalse({"trazo-document", "trazo-documents"} & out["meta"].keys())

    def test_an_equation_in_another_document_is_not_resolved(self):
        text, warnings, _ = shown(cite("eq:model"), **documents("manuscript"))
        self.assertEqual(text, "eq. ¿eq:model?")
        self.assertEqual(warnings, ["Cross-document references cover figures, tables and sections: "
                                    "eq:model is in Supplementary"])

    def test_a_label_in_two_other_documents_is_ambiguous(self):
        extra = [("cover_letter", "Cover letter", {"fig:qc": "fig 1"}, "", "")]
        text, warnings, _ = shown(cite("fig:qc"), **documents("manuscript", extra=extra))
        self.assertEqual(text, f"Figure{NBSP}¿fig:qc?")
        self.assertEqual(warnings, ["Ambiguous cross-reference: fig:qc is in Supplementary and Cover letter"])

    def test_the_citing_documents_own_label_wins(self):
        text, warnings, _ = shown(cite("fig:qc"), blocks=[figure("fig:qc", "Local")],
                                  **documents("manuscript", manuscript={}))
        self.assertEqual(text, f"Figure{NBSP}1")
        self.assertEqual(warnings, [])  # the submission check lists labels in two documents

    def test_documents_whose_items_read_alike_warn(self):
        text, warnings, _ = shown(cite("fig:qc", "fig:grid"), **documents("manuscript", prefix=""))
        self.assertEqual(text, f"Figure{NBSP}1, 2")
        self.assertEqual(warnings, ["Supplementary's figures read like Manuscript's: give one of them "
                                    "a number_prefix or title_prefix in trazo.toml"])

    def test_an_unknown_label_warns_as_before(self):
        text, warnings, _ = shown(cite("fig:nope"), **documents("manuscript"))
        self.assertEqual(text, f"Figure{NBSP}¿fig:nope?")
        self.assertEqual(warnings, ["Undefined cross-reference: fig:nope"])

    def test_own_sections_take_the_documents_prefix(self):
        keys = {key: meta(value) for key, value in numbering.document_templates("S", "").items()}
        out = crossref.apply(doc(header(1, "sec:a", "A"), table("tbl:t", "T"), para(cite("sec:a")), **keys))
        self.assertEqual(crossref.plain(out["blocks"][-1]["c"]), f"Section{NBSP}S1")


if __name__ == "__main__":
    unittest.main()
