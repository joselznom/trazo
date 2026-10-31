"""trazo's cross-reference pass reproduces pandoc-crossref (trazo.crossref)."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)

from trazo import crossref, numbering, pandoc_run
from trazo.crossref import template, text
from trazo.project import DEFAULTS, ConfigError, merge

API = [1, 23, 1, 1]
NBSP = " "


def meta_inlines(nodes):
    return {"t": "MetaInlines", "c": nodes}


def meta(value):
    """A metadata value as Pandoc writes it into JSON."""
    if isinstance(value, list):
        return {"t": "MetaList", "c": [meta(item) for item in value]}
    if isinstance(value, dict):
        return {"t": "MetaMap", "c": {key: meta(item) for key, item in value.items()}}
    if isinstance(value, bool):
        return {"t": "MetaBool", "c": value}
    return meta_inlines(template(value))


# The project's metadata.yaml: bold "Figure 1." titles, full-word prefixes.
PROJECT_META = {
    "figureTitle": meta("Figure"), "tableTitle": meta("Table"), "figPrefix": meta("Figure"),
    "tblPrefix": meta("Table"), "secPrefix": meta("Section"),
    "figureTemplate": meta_inlines([{"t": "Strong", "c": template("$$figureTitle$$ $$i$$.")},
                                    {"t": "Space"}, *template("$$t$$")]),
    "tableTemplate": meta_inlines([{"t": "Strong", "c": template("$$tableTitle$$ $$i$$.")},
                                   {"t": "Space"}, *template("$$t$$")]),
}


def figure(label, caption):
    inlines = text(caption)
    return {"t": "Figure", "c": [[label, [], []], [None, [{"t": "Plain", "c": inlines}]],
                                 [{"t": "Plain", "c": [{"t": "Image", "c": [["", [], []], inlines, ["x.png", ""]]}]}]]}


def table(label, caption):
    cell = [["", [], []], {"t": "AlignDefault"}, 1, 1, [{"t": "Plain", "c": text("1")}]]
    return {"t": "Table", "c": [[label, [], []], [None, [{"t": "Plain", "c": text(caption)}]],
                                [[{"t": "AlignDefault"}, {"t": "ColWidthDefault"}]],
                                [["", [], []], []], [[["", [], []], 0, [], [[["", [], []], [cell]]]]],
                                [["", [], []], []]]}


def header(level, label, title, classes=()):
    return {"t": "Header", "c": [level, [label, list(classes), []], text(title)]}


def cite(*ids, mode="NormalCitation", prefix=(), suffix=()):
    citations = [{"citationId": i, "citationPrefix": list(prefix), "citationSuffix": list(suffix),
                  "citationMode": {"t": mode}, "citationNoteNum": 1, "citationHash": 0} for i in ids]
    return {"t": "Cite", "c": [citations, text("[" + "; ".join("@" + i for i in ids) + "]")]}


def para(*inlines):
    return {"t": "Para", "c": list(inlines)}


def doc(*blocks, **keys):
    return {"pandoc-api-version": API, "meta": {**PROJECT_META, **keys}, "blocks": list(blocks)}


def resolved(*inlines, figures=3, **keys):
    """The text of a paragraph of references after the pass, and its warnings."""
    warnings = []
    blocks = [figure(f"fig:{name}", name.upper()) for name in "abcde"[:figures]]
    out = crossref.apply(doc(*blocks, table("tbl:t", "T"), para(*inlines), **keys), warn=warnings.append)
    return crossref.plain(out["blocks"][-1]["c"]), warnings


class Captions(unittest.TestCase):
    def test_figures_and_tables_are_numbered_with_the_caption_template(self):
        out = crossref.apply(doc(figure("fig:a", "First"), figure("other", "Unlabelled"),
                                 table("tbl:t", "A table"), figure("fig:b", "Second")))
        blocks = out["blocks"]
        caption = blocks[0]["c"][1][1][0]["c"]
        self.assertEqual(caption[0], {"t": "Strong", "c": text("Figure 1.")})
        self.assertEqual(crossref.plain(caption), "Figure 1. First")
        # The image's alt text carries the numbered caption too.
        self.assertEqual(blocks[0]["c"][2][0]["c"][0]["c"][1], caption)
        self.assertEqual(crossref.plain(blocks[1]["c"][1][1][0]["c"]), "Unlabelled")
        self.assertEqual(crossref.plain(blocks[2]["c"][1][1][0]["c"]), "Table 1. A table")
        self.assertEqual(crossref.plain(blocks[3]["c"][1][1][0]["c"]), "Figure 2. Second")

    def test_default_template_uses_title_delimiter(self):
        out = crossref.apply({"pandoc-api-version": API, "meta": {}, "blocks": [figure("fig:a", "Cap")]})
        self.assertEqual(crossref.plain(out["blocks"][0]["c"][1][1][0]["c"]), "Figure 1: Cap")

    def test_trailing_table_label_in_the_caption_text_is_read(self):
        legacy = table("", "Old caption {#tbl:old}")
        out = crossref.apply(doc(legacy, para(cite("tbl:old"))))
        self.assertEqual(out["blocks"][0]["c"][0][0], "tbl:old")
        self.assertEqual(crossref.plain(out["blocks"][0]["c"][1][1][0]["c"]), "Table 1. Old caption")
        self.assertEqual(crossref.plain(out["blocks"][1]["c"]), f"Table{NBSP}1")

    def test_supplement_prefix_from_numbering_metadata(self):
        keys = {key: meta(value) for key, value in {
            "figureTemplate": "$$figureTitle$$ S$$i$$. $$t$$", "tableTemplate": "$$tableTitle$$ S$$i$$. $$t$$",
            "refIndexTemplate": {"fig": "S$$i$$$$suf$$", "tbl": "S$$i$$$$suf$$", "default": "$$i$$$$suf$$"},
        }.items()}
        out = crossref.apply(doc(header(1, "sec:m", "Methods"), figure("fig:a", "A"), figure("fig:b", "B"),
                                 figure("fig:c", "C"), table("tbl:t", "T"),
                                 para(cite("fig:a", "fig:b", "fig:c"), {"t": "Space"}, cite("tbl:t"),
                                      {"t": "Space"}, cite("sec:m")), **keys))
        self.assertEqual(crossref.plain(out["blocks"][1]["c"][1][1][0]["c"]), "Figure S1. A")
        self.assertEqual(crossref.plain(out["blocks"][-1]["c"]),
                         f"Figure{NBSP}S1-S3 Table{NBSP}S1 Section{NBSP}1")

    def test_numbering_module_metadata_is_understood(self):
        # numbering.metadata() is what every build passes for a prefixed document.
        values = numbering.metadata("supplementary")
        keys = {"figureTemplate": meta(values["figureTemplate"]),
                "refIndexTemplate": meta(values["refIndexTemplate"])}
        out = crossref.apply(doc(figure("fig:a", "A"), para(cite("fig:a")), **keys))
        # Pandoc would parse the ** as bold; meta() here keeps them as text.
        self.assertEqual(crossref.plain(out["blocks"][0]["c"][1][1][0]["c"]), "**Figure S1.** A")
        self.assertEqual(crossref.plain(out["blocks"][1]["c"]), f"Figure{NBSP}S1")


class Sections(unittest.TestCase):
    def test_sections_are_numbered_even_without_numbersections(self):
        out = crossref.apply(doc(
            header(2, "sec:pre", "Before"), header(1, "sec:a", "A"), header(3, "sec:deep", "Deep"),
            header(1, "sec:u", "Unnumbered", ["unnumbered"]), header(2, "sec:under", "Under"),
            header(1, "sec:b", "B"),
            para(*[x for name in ("pre", "a", "deep", "under", "b") for x in (cite(f"sec:{name}"), {"t": "Space"})]),
            numbersections=meta(False)))
        self.assertEqual(crossref.plain(out["blocks"][-1]["c"]).split(" "),
                         [f"Section{NBSP}{n}" for n in ("0.1", "1", "1.0.1", "1.1", "2")] + [""])
        # Headings themselves are not changed.
        self.assertEqual(out["blocks"][1], header(1, "sec:a", "A"))

    def test_unnumbered_section_cannot_be_referenced(self):
        warnings = []
        out = crossref.apply(doc(header(1, "sec:u", "U", ["unnumbered"]), para(cite("sec:u"))),
                             warn=warnings.append)
        self.assertEqual(crossref.plain(out["blocks"][-1]["c"]), f"Section{NBSP}¿sec:u?")
        self.assertEqual(warnings, ["Undefined cross-reference: sec:u"])


class References(unittest.TestCase):
    def test_single_reference_melds_with_following_punctuation(self):
        out = crossref.apply(doc(figure("fig:a", "A"), para(*text("see ("), cite("fig:a"), *text(")."))))
        self.assertEqual(out["blocks"][-1]["c"][-1], {"t": "Str", "c": f"Figure{NBSP}1)."})
        self.assertEqual(out["blocks"][-1]["c"][-2], {"t": "Str", "c": "("})

    def test_grouped_references_sort_and_collapse_ranges(self):
        self.assertEqual(resolved(cite("fig:b", "fig:a"))[0], f"Figure{NBSP}1, 2")
        self.assertEqual(resolved(cite("fig:c", "fig:a", "fig:b"))[0], f"Figure{NBSP}1-3")
        self.assertEqual(resolved(cite("fig:a", "fig:c", "fig:d", "fig:e"), figures=5)[0],
                         f"Figure{NBSP}1, 3-5")
        self.assertEqual(resolved(cite("fig:a", "fig:a"))[0], f"Figure{NBSP}1")
        self.assertEqual(resolved(cite("fig:a", "tbl:t", "fig:b"))[0],
                         f"Figure{NBSP}1, Table{NBSP}1, Figure{NBSP}2")

    def test_singular_and_plural_prefixes(self):
        keys = {"figPrefix": meta(["fig.", "figs."])}
        self.assertEqual(resolved(cite("fig:a"), **keys)[0], f"fig.{NBSP}1")
        self.assertEqual(resolved(cite("fig:a", "fig:b"), **keys)[0], f"figs.{NBSP}1, 2")
        self.assertEqual(resolved(cite("Fig:a", "fig:b"), **keys)[0], f"Figs.{NBSP}1, 2")
        defaults = {key: value for key, value in PROJECT_META.items() if key != "figPrefix"}
        out = crossref.apply({"pandoc-api-version": API, "meta": defaults,
                              "blocks": [figure("fig:a", "A"), para(cite("fig:a"))]})
        self.assertEqual(crossref.plain(out["blocks"][-1]["c"]), f"fig.{NBSP}1")

    def test_suppress_author_drops_the_prefix(self):
        self.assertEqual(resolved(cite("fig:b", mode="SuppressAuthor"))[0], "2")
        mixed = cite("fig:a", "fig:b")
        mixed["c"][0][0]["citationMode"] = {"t": "SuppressAuthor"}
        self.assertEqual(resolved(mixed)[0], f"1, Figure{NBSP}2")

    def test_citation_prefix_and_suffix(self):
        self.assertEqual(resolved(cite("fig:a", prefix=text("see")))[0], "see 1")
        suffixed = cite("fig:a", "fig:b", "fig:c")
        suffixed["c"][0][1]["citationSuffix"] = text(", left")
        self.assertEqual(resolved(suffixed)[0], f"Figure{NBSP}1, 2, left, 3")

    def test_missing_label_stays_as_text_with_a_warning(self):
        shown, warnings = resolved(cite("fig:a", "fig:nope"))
        self.assertEqual(shown, f"Figure{NBSP}¿fig:nope?, 1")
        self.assertEqual(warnings, ["Undefined cross-reference: fig:nope"])
        out = crossref.apply(doc(para(cite("fig:nope"))), warn=lambda message: None)
        self.assertEqual(out["blocks"][0]["c"][1], {"t": "Strong", "c": text("¿fig:nope?")})

    def test_bibliography_citations_are_left_for_citeproc(self):
        out = crossref.apply(doc(figure("fig:a", "A"), para(cite("fig:a", "smith2020"))))
        inlines = out["blocks"][-1]["c"]
        self.assertEqual(crossref.plain(inlines[:1]), f"Figure{NBSP}1,")
        self.assertEqual(inlines[-1]["t"], "Cite")
        self.assertEqual([c["citationId"] for c in inlines[-1]["c"][0]], ["smith2020"])

    def test_link_references(self):
        for options, keys in (({"link_references": True}, {}), ({}, {"linkReferences": meta(True)})):
            with self.subTest(options=options):
                blocks = [figure(f"fig:{name}", name) for name in "abc"]
                out = crossref.apply(doc(*blocks, para(cite("fig:a", "fig:b", "fig:c")), **keys), **options)
                inlines = out["blocks"][-1]["c"]
                self.assertEqual(inlines[0], {"t": "Str", "c": f"Figure{NBSP}"})
                self.assertEqual(inlines[1], {"t": "Link", "c": [["", [], []], text("1"), ["#fig:a", ""]]})
                self.assertEqual(inlines[2], {"t": "Str", "c": "-"})
                self.assertEqual(inlines[3]["c"][2], ["#fig:c", ""])

    def test_references_inside_captions_and_notes(self):
        caption = figure("fig:a", "A")
        caption["c"][1][1][0]["c"] += [{"t": "Space"}, cite("tbl:t")]
        note = {"t": "Note", "c": [para(cite("fig:a"))]}
        out = crossref.apply(doc(caption, table("tbl:t", "T"), para(note)))
        self.assertEqual(crossref.plain(out["blocks"][0]["c"][1][1][0]["c"]), f"Figure 1. A Table{NBSP}1")
        self.assertEqual(crossref.plain(out["blocks"][-1]["c"][0]["c"][0]["c"]), f"Figure{NBSP}1")

    def test_equations_are_numbered_in_their_tex(self):
        math = {"t": "Math", "c": [{"t": "DisplayMath"}, "E = mc^2"]}
        out = crossref.apply(doc(para(math, {"t": "Space"}, {"t": "Str", "c": "{#eq:e}"}),
                                 para(cite("eq:e"))))
        self.assertEqual(out["blocks"][0]["c"], [{"t": "Span", "c": [["eq:e", [], []], [
            {"t": "Math", "c": [{"t": "DisplayMath"}, "E = mc^2\\qquad{(1)}"]}]]}])
        self.assertEqual(crossref.plain(out["blocks"][1]["c"]), f"eq.{NBSP}1")

    def test_subfigures_are_lettered_panels_of_one_figure(self):
        div = {"t": "Div", "c": [["fig:panels", ["wide"], []],
                                 [figure("fig:left", "Left"), figure("fig:right", "Right"), para(*text("Both."))]]}
        out = crossref.apply(doc(figure("fig:a", "A"), div, para(cite("fig:right", "fig:panels"))))
        panels = out["blocks"][1]
        self.assertEqual((panels["t"], panels["c"][0]), ("Figure", ["fig:panels", ["subfigures", "wide"], []]))
        self.assertEqual(crossref.plain(panels["c"][1][1][0]["c"]),
                         "Figure 2: Both.. a \u2014 Left, b \u2014 Right")
        self.assertEqual([crossref.plain(child["c"][1][1][0]["c"]) for child in panels["c"][2]], ["a", "b"])
        self.assertEqual(crossref.plain(out["blocks"][-1]["c"]), f"Figure{NBSP}2, 2 (b)")

    def test_subfigure_images_in_one_paragraph_are_panels(self):
        def image(label, alt):
            return {"t": "Image", "c": [[label, [], []], text(alt), ["x.png", ""]]}
        div = {"t": "Div", "c": [["fig:row", [], []], [
            para(image("fig:ra", "Left"), {"t": "SoftBreak"}, image("fig:rb", "Right"), {"t": "SoftBreak"},
                 image("", "")),
            para(image("fig:alone", "")), para(*text("Both."))]]}
        warnings = []
        out = crossref.apply(doc(div, para(cite("fig:ra", "fig:rb"), {"t": "Space"}, cite("fig:alone"))),
                             warn=warnings.append)
        row = out["blocks"][0]
        self.assertEqual(crossref.plain(row["c"][1][1][0]["c"]), "Figure 1: Both.. a \u2014 Left, b \u2014 Right")
        self.assertEqual([crossref.plain(node["c"][1]) for node in row["c"][2][0]["c"] if node["t"] == "Image"],
                         ["a", "b", "c"])
        # A lone image without alt text is not a panel.
        self.assertEqual(row["c"][2][1], para(image("fig:alone", "")))
        self.assertEqual(crossref.plain(out["blocks"][-1]["c"]), f"Figure{NBSP}1 (a), 1 (b) Figure{NBSP}¿fig:alone?")
        self.assertEqual(warnings, ["Undefined cross-reference: fig:alone"])

    def test_table_in_a_div_is_numbered_under_the_div_label(self):
        div = {"t": "Div", "c": [["tbl:d", [], []], [table("", "In a div")]]}
        crowded = {"t": "Div", "c": [["tbl:x", [], []], [para(*text("Text.")), table("", "Not alone")]]}
        out = crossref.apply(doc(table("tbl:t", "Plain"), div, crowded, para(cite("tbl:d"))),
                             warn=lambda message: None)
        self.assertEqual(out["blocks"][1]["c"][0][0], "tbl:d")
        self.assertEqual(crossref.plain(out["blocks"][1]["c"][1][0]["c"][1][1][0]["c"]), "Table 2. In a div")
        self.assertEqual(crossref.plain(out["blocks"][2]["c"][1][1]["c"][1][1][0]["c"]), "Not alone")
        self.assertEqual(crossref.plain(out["blocks"][-1]["c"]), f"Table{NBSP}2")

    def test_equations_are_numbered_in_document_order_and_not_in_figures(self):
        def equation(label):
            return [{"t": "Math", "c": [{"t": "DisplayMath"}, label]}, {"t": "Space"},
                    {"t": "Str", "c": f"{{#eq:{label}}}"}]
        captioned = figure("fig:f", "Cap")
        captioned["c"][1][1][0]["c"] += [{"t": "Space"}, *equation("cap")]
        out = crossref.apply(doc(
            captioned, para(*equation("after")),
            para(*equation("b"), {"t": "Space"}, {"t": "Emph", "c": equation("c")}, {"t": "Space"}, *equation("d")),
            para(cite("eq:after", "eq:b", "eq:c", "eq:d"), {"t": "Space"}, cite("eq:cap"))),
            warn=lambda message: None)
        self.assertEqual(crossref.plain(out["blocks"][-1]["c"]), f"eqns.{NBSP}1-4 eq.{NBSP}¿eq:cap?")
        # In the figure the equation gets its id but no number.
        span = out["blocks"][0]["c"][1][1][0]["c"][-1]
        self.assertEqual(span, {"t": "Span", "c": [["eq:cap", [], []], [equation("cap")[0]]]})

    def test_block_ids_with_a_capital_are_not_labels(self):
        out = crossref.apply(doc(figure("Fig:up", "Up"), figure("fig:next", "Next"),
                                 para(cite("Fig:next"), {"t": "Space"}, cite("fig:up"))), warn=lambda message: None)
        self.assertEqual(crossref.plain(out["blocks"][0]["c"][1][1][0]["c"]), "Up")
        self.assertEqual(crossref.plain(out["blocks"][1]["c"][1][1][0]["c"]), "Figure 1. Next")
        self.assertEqual(crossref.plain(out["blocks"][-1]["c"]), f"Figure{NBSP}1 Figure{NBSP}¿fig:up?")

    def test_duplicate_labels_are_an_error(self):
        math = [{"t": "Math", "c": [{"t": "DisplayMath"}, "x"]}, {"t": "Str", "c": "{#eq:e}"}]
        for blocks, label in (([figure("fig:a", "A"), figure("fig:a", "B")], "fig:a"),
                              ([table("tbl:t", "A"), table("tbl:t", "B")], "tbl:t"),
                              ([para(*math), para(*math)], "eq:e")):
            with self.subTest(label=label):
                with self.assertRaisesRegex(crossref.CrossrefError, f"^Duplicate label: {label}$"):
                    crossref.apply(doc(*blocks))
        # A repeated section label takes the later heading, as in pandoc-crossref.
        out = crossref.apply(doc(header(1, "sec:a", "A"), header(1, "sec:a", "B"), para(cite("sec:a"))))
        self.assertEqual(crossref.plain(out["blocks"][-1]["c"]), f"Section{NBSP}2")

    def test_unsupported_options_warn_once(self):
        keys = {"chapters": meta(True), "numberSections": meta(True), "autoSectionLabels": meta(True),
                "cref": meta(False), "figLabels": meta("arabic"), "subfigLabels": meta("alpha a"),
                "eqnBlockTemplate": meta("$$i$$")}
        warnings = []
        crossref.apply(doc(figure("fig:a", "A"), para(cite("fig:a")), **keys), warn=warnings.append)
        self.assertEqual([w.split(" option ")[1].split(";")[0] for w in warnings],
                         ["chapters", "numberSections", "autoSectionLabels", "eqnBlockTemplate"])
        self.assertTrue(warnings[0].endswith("option chapters"))
        self.assertTrue(set(crossref.UNSUPPORTED_OPTIONS).isdisjoint(crossref.SUPPORTED_OPTIONS))

    def test_unsupported_pandoc_version_is_refused(self):
        with self.assertRaisesRegex(crossref.CrossrefError, "pandoc-api-version 1.23"):
            crossref.apply({"pandoc-api-version": [1, 22, 2], "meta": {}, "blocks": []})

    def test_input_is_not_changed(self):
        source = doc(figure("fig:a", "A"), para(cite("fig:a")))
        before = json.dumps(source)
        self.assertIsNot(crossref.apply(source), source)
        self.assertEqual(json.dumps(source), before)
        self.assertIs(crossref.apply(source, in_place=True), source)
        self.assertNotEqual(json.dumps(source), before)


class Runner(unittest.TestCase):
    def test_arguments_split_between_reader_and_writer(self):
        first, second = pandoc_run.split([
            "in.md", "--metadata-file=m.yaml", "--standalone", "--bibliography=b.bib", "--csl", "c.csl",
            "--reference-doc=r.docx", "--resource-path=.", "--filter", "pandoc-crossref", "--citeproc",
            "-M", "linkReferences:true", "--to=html5", "--section-divs", "--mathml", "-o", "out.html"])
        self.assertEqual(first, ["in.md", "--metadata-file=m.yaml", "--bibliography=b.bib", "--csl", "c.csl",
                                 "--resource-path=.", "-M", "linkReferences:true", "--to=json"])
        self.assertEqual(second, ["--from=json", "--standalone", "--bibliography=b.bib", "--csl", "c.csl",
                                  "--reference-doc=r.docx", "--resource-path=.", "--citeproc", "--to=html5",
                                  "--section-divs", "--mathml", "-o", "out.html"])

    def test_filters_keep_their_side_of_the_crossref_filter(self):
        first, second = pandoc_run.split(["--lua-filter=a.lua", "--filter=/opt/bin/pandoc-crossref",
                                          "--lua-filter=b.lua", "--citeproc"])
        self.assertEqual(first, ["--lua-filter=a.lua", "--to=json"])
        self.assertEqual(second, ["--from=json", "--lua-filter=b.lua", "--citeproc"])

    def test_calls_without_the_crossref_filter_are_not_split(self):
        self.assertIsNone(pandoc_run.split(["-f", "markdown", "-t", "plain"]))

    def test_valued_options_are_not_taken_as_inputs(self):
        args = ["in.md", "--syntax-definition", "x.xml", "-C", "--abbreviations", "abbr.txt", "-V", "k:v",
                "--template", "t.html", "--filter", "pandoc-crossref", "--base-header-level", "2"]
        self.assertEqual(pandoc_run.inputs(args), ["in.md"])
        first, second = pandoc_run.split(args)
        self.assertEqual(first, ["in.md", "-C", "--abbreviations", "abbr.txt", "--base-header-level", "2",
                                 "--to=json"])
        self.assertEqual(second, ["--from=json", "--syntax-definition", "x.xml", "-V", "k:v",
                                  "--template", "t.html"])

    def test_valued_options_match_pandoc_help(self):
        if not shutil.which("pandoc"):
            self.skipTest("needs pandoc")
        help_text = subprocess.run(["pandoc", "--help"], capture_output=True, text=True, check=True).stdout
        valued = set(re.findall(r"(--[\w-]+)=", help_text))
        self.assertEqual(valued - pandoc_run.VALUED, set())
        self.assertEqual(pandoc_run.VALUED - set(re.findall(r"--[\w-]+", help_text)), set())

    def test_defaults_file_fails_only_with_the_crossref_filter(self):
        self.assertIsNone(pandoc_run.split(["-d", "x.yaml", "in.md"]))
        with self.assertRaisesRegex(ValueError, "--defaults"):
            pandoc_run.split(["--defaults=x.yaml", "in.md", "--filter", "pandoc-crossref"])

    def test_engine_setting(self):
        self.assertEqual(DEFAULTS["crossref"]["engine"], "trazo")
        self.assertEqual(merge(DEFAULTS, {"crossref": {"engine": "trazo"}})["crossref"]["engine"], "trazo")
        with self.assertRaisesRegex(ConfigError, 'must be "trazo" .pandoc-crossref is not'):
            merge(DEFAULTS, {"crossref": {"engine": "pandoc-crossref"}})


FAKE_PANDOC = """import json, os, sys, time
args = sys.argv[1:]
stage = 1 if "--to=json" in args else 2
behaviour = os.environ.get(f"FAKE_STAGE{stage}", "ok")
time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
log = next((a.split("=", 1)[1] for a in args if a.startswith("--log=")), None)
if log:
    open(log, "w").write(json.dumps([{"stage": stage}]))
print(f"stage {stage} ran", file=sys.stderr)
if behaviour == "fail":
    sys.exit(5)
if stage == 1:
    if behaviour == "badjson":
        sys.stdout.write("not json")
    else:
        version = [1, 22] if behaviour == "oldapi" else [1, 23, 1, 1]
        fig = {"t": "Figure", "c": [["fig:a", [], []], [None, [{"t": "Plain", "c": [{"t": "Str", "c": "A"}]}]], []]}
        blocks = [fig, fig] if behaviour == "dup" else [fig, {"t": "Para", "c": [{"t": "Cite", "c": [[{
            "citationId": "fig:none", "citationPrefix": [], "citationSuffix": [], "citationMode": {"t": "NormalCitation"},
            "citationNoteNum": 1, "citationHash": 0}], []]}]}]
        json.dump({"pandoc-api-version": version, "meta": {}, "blocks": blocks}, sys.stdout)
else:
    source = [a for a in args if not a.startswith("-")]
    data = open(source[0]).read() if source else sys.stdin.read()
    sys.stdout.write(str(len(json.loads(data)["blocks"])))
"""


class RunnerStages(unittest.TestCase):
    """pandoc_run.run, against a fake pandoc."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="trazo-fake-pandoc-"))
        self.addCleanup(shutil.rmtree, self.folder, True)
        (self.folder / "pandoc.py").write_text(FAKE_PANDOC)
        self.addCleanup(lambda: [os.environ.pop(f"FAKE_{key}", None) for key in ("STAGE1", "STAGE2", "SLEEP")])

    def run_fake(self, *args, timeout=None, **environ):
        os.environ.update({f"FAKE_{key}": value for key, value in environ.items()})
        return pandoc_run.run(["in.md", "--filter", "pandoc-crossref", "--citeproc", *args], text=True,
                              command=[sys.executable, str(self.folder / "pandoc.py")], cwd=self.folder,
                              timeout=timeout)

    def test_both_stages_run_and_warnings_are_kept(self):
        result = self.run_fake()
        self.assertEqual((result.returncode, result.stdout), (0, "2"))
        self.assertEqual(result.stderr, "stage 1 ran\nUndefined cross-reference: fig:none\nstage 2 ran\n")

    def test_stage_one_failure_stops_the_run(self):
        result = self.run_fake(STAGE1="fail")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (5, "", "stage 1 ran\n"))

    def test_invalid_json_from_stage_one(self):
        result = self.run_fake(STAGE1="badjson")
        self.assertEqual(result.returncode, 1)
        self.assertRegex(result.stderr, r"^stage 1 ran\ntrazo crossref: Expecting value")

    def test_pass_errors(self):
        for behaviour, message in (("dup", "trazo crossref: Duplicate label: fig:a\n"),
                                   ("oldapi", "trazo crossref: trazo's cross-references support "
                                              "pandoc-api-version 1.23.x (Pandoc 3.x), not 1.22")):
            with self.subTest(behaviour=behaviour):
                result = self.run_fake(STAGE1=behaviour)
                self.assertEqual((result.returncode, result.stdout), (1, ""))
                self.assertTrue(result.stderr.startswith("stage 1 ran\n" + message), result.stderr)
                self.assertNotIn("stage 2 ran", result.stderr)

    def test_stage_two_failure(self):
        result = self.run_fake(STAGE2="fail")
        self.assertEqual(result.returncode, 5)
        self.assertEqual(result.stderr, "stage 1 ran\nUndefined cross-reference: fig:none\nstage 2 ran\n")

    def test_log_covers_both_stages(self):
        result = self.run_fake("--log=pandoc-log.json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((self.folder / "pandoc-log.json").read_text()), [{"stage": 1}, {"stage": 2}])

    def test_timeout_is_for_the_whole_run(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_fake(timeout=1.0, SLEEP="0.6")

    def test_defaults_file_is_refused(self):
        with self.assertRaisesRegex(ValueError, "--defaults"):
            self.run_fake("--defaults", "d.yaml")


@unittest.skipUnless(shutil.which("pandoc"), "needs pandoc")
class RunnerWithPandoc(unittest.TestCase):
    def test_stdin_input(self):
        result = pandoc_run.run(["--filter", "pandoc-crossref", "-t", "plain"], text=True,
                                input="![A](x.png){#fig:a}\n\nSee [@fig:a] and [@fig:none].\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"See fig.{NBSP}1 and fig.{NBSP}¿fig:none?.", result.stdout)
        self.assertEqual(result.stderr, "Undefined cross-reference: fig:none\n")


if __name__ == "__main__":
    unittest.main()
