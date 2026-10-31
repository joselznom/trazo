"""Table specs: schema, rendering, merges, pivots, emphasis, CLI, patch and undo."""

import contextlib
import io
import json
import re
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import _support

from trazo import diagram, expand, preview_locate, table

GOLDEN = Path(__file__).resolve().parent / "golden"
SPECS = _support.ROOT / "tables/specs"
DATA = _support.ROOT / "tables/output/data"


def run(argv, stdin=None):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        if stdin is not None:
            with patch("sys.stdin", io.StringIO(stdin)):
                code = table.main(argv)
        else:
            code = table.main(argv)
    return code, out.getvalue(), err.getvalue()


def spec(label):
    return json.loads((SPECS / f"{label}.json").read_text(encoding="utf-8"))


def grid(markdown):
    return [line for line in markdown.splitlines() if line.startswith(("+", "|"))]


def codes(result):
    return [i["code"] for i in result.errors]


def write_data(name, records, **extra):
    payload = {"schema_version": 1, "generator": "test", "inputs": {}, "values": {},
               "records": records, **extra}
    (DATA / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")


class Restore(unittest.TestCase):
    """Put the fixture specs, outputs and inputs back after each test."""

    def setUp(self):
        saved = {p: p.read_bytes() for folder in (SPECS, DATA, _support.ROOT / "data")
                 for p in folder.glob("*") if p.is_file()}

        def restore():
            for folder in (SPECS, DATA):
                for p in folder.glob("*"):
                    if p not in saved:
                        p.unlink()
            for p, data in saved.items():
                p.write_bytes(data)
            shutil.rmtree(table.OUTPUT_DIR, ignore_errors=True)
            table.HISTORY_PATH.unlink(missing_ok=True)
            table.COMMENTS_PATH.unlink(missing_ok=True)

        self.addCleanup(restore)


class SchemaTests(Restore):
    def test_fixture_tables_are_clean(self):
        for label in ("cohorts", "results"):
            result = table.build(label)
            self.assertEqual(result.issues, [], label)

    def test_unknown_and_invalid_keys_are_errors(self):
        data = spec("cohorts")
        data["colour"] = "red"
        data["columns"][0]["align"] = "middle"
        del data["columns"][1]["header"]
        data["columns"].append({"id": "g", "header": "G", "columns": [
            {"id": "x", "header": "X", "columns": [{"id": "y", "field": "note", "header": "Y"}]}]})
        messages = " | ".join(i["message"] for i in table.build("cohorts", data).errors)
        self.assertIn("unknown key 'colour'", messages)
        self.assertIn("align must be one of", messages)
        self.assertIn("'header' is required", messages)
        self.assertIn("two header levels", messages)

    def test_references_are_checked(self):
        data = spec("cohorts")
        data["rows"]["keys"] = ["cohort", "nope"]
        self.assertIn("unknown field 'nope'", table.build("cohorts", data).errors[0]["message"])
        data = spec("cohorts")
        data["columns"][2]["format"] = "missing_format"
        self.assertIn("unknown format", table.build("cohorts", data).errors[0]["message"])
        data = spec("cohorts")
        data["columns"][3]["header"] = "Age, years"
        self.assertIn("same header", table.build("cohorts", data).errors[0]["message"])

    def test_floats_and_composites_need_a_format(self):
        data = spec("cohorts")
        del data["columns"][2]["format"]
        self.assertIn("composite values need a format",
                      table.build("cohorts", data).errors[0]["message"])

    def test_a_failing_format_names_the_cell(self):
        data = spec("cohorts")
        data["formats"]["pct"] = "{k} of {total}"
        error = table.build("cohorts", data).errors[0]
        self.assertEqual(error["code"], "format")
        self.assertIn('row cohort="train", column female', error["message"])
        self.assertIn("{total}", error["message"])

    def test_bad_data_values_are_errors(self):
        write_data("bad", [{"a": True}, {"a": {"x": "text"}}])
        data = spec("cohorts")
        data["data"] = "bad"
        self.assertEqual(len(table.build("cohorts", data).errors), 2)

    def test_unknown_caption_value_is_an_error(self):
        data = spec("cohorts")
        data["legend"] = "{missing} patients; braces {{kept}}."
        self.assertIn("unknown value {missing}", table.build("cohorts", data).errors[0]["message"])
        data["legend"] = "{n} patients; braces {{kept}}."
        self.assertIn("160 patients; braces {kept}.", table.build("cohorts", data).caption)


    def test_note_follows_the_table_with_values_and_paragraphs(self):
        data = spec("cohorts")
        data["note"] = "SD, standard deviation;\n  {n} patients.\n\n*p* < 0.05."
        built = table.build("cohorts", data)
        self.assertEqual(built.errors, [])
        self.assertTrue(built.markdown.endswith('+\n\n::: {.table-note custom-style="Table Note"}\n'
                                                "SD, standard deviation; 160 patients.\n\n*p* < 0.05.\n:::\n"))
        self.assertEqual(table.render_model(built)["note_html"],
                         "<p>SD, standard deviation; 160 patients.</p><p><em>p</em> &lt; 0.05.</p>")
        self.assertNotIn("table-note", table.build("cohorts", spec("cohorts")).markdown)
        data["note"] = "{missing} values."
        self.assertIn("note: unknown value {missing}", table.build("cohorts", data).errors[0]["message"])
        data["note"] = ["not text"]
        self.assertIn("spec.note must be a string", table.build("cohorts", data).errors[0]["message"])


class RenderTests(Restore):
    def test_golden_files(self):
        for label, leaves in (("cohorts", 5), ("results", 7)):
            with self.subTest(label=label):
                markdown = table.build(label).markdown
                self.assertEqual(markdown, (GOLDEN / f"{label}.md").read_text(encoding="utf-8"))
                lines = grid(markdown)
                self.assertEqual({len(line) for line in lines}, {len(lines[0])})
                self.assertEqual(lines[0].count("+") - 1, leaves)
                self.assertTrue(markdown.splitlines()[1].endswith(f"{{#tbl:{label}}}"))

    def test_rendering_is_deterministic(self):
        self.assertEqual(table.build("results").markdown, table.build("results").markdown)

    def test_narrow_tables_are_padded_to_the_text_width(self):
        lines = grid(table.build("cohorts").markdown)
        dashes = [len(seg) for seg in lines[0].strip("+").split("+")]
        self.assertGreaterEqual(sum(d + 1 for d in dashes) + len(dashes) - 2, table.TEXT_WIDTH)

    def test_escaping_and_missing_values(self):
        markdown = table.build("cohorts").markdown
        self.assertIn(r"a\*b\_c\|d", markdown)
        self.assertIn("| –", markdown)
        self.assertEqual(table.escape("- item"), r"\- item")
        self.assertEqual(table.escape("1. first"), r"1\. first")
        self.assertEqual(table.escape("<b> & [x]"), r"\<b\> \& \[x\]")

    def test_merges_join_equal_cells_and_stop_at_blocks(self):
        data = spec("results")
        data["maps"]["cohort"] = {"site1": "Site", "site2": "Site", "site3": "Site"}
        result = table.build("results", data)
        items = table.body_items(result)
        joined = table.joined_cells(result, items)
        kinds = [kind for kind, _ in items]
        self.assertEqual(kinds, ["block", "row", "row", "block", "row", "row", "row", "row"])
        # First row after a block row never joins, even with equal text.
        self.assertEqual(joined[4][:2], [False, False])
        self.assertEqual(joined[2][:2], [True, True])
        # site2 -> site3: cohort text is equal, n (150 vs 90) is not.
        self.assertEqual(joined[6][:2], [True, False])
        # n joins only while the merge column to its left (cohort) is joined.
        records = json.loads((DATA / "results.json").read_text())["records"]
        for record in records:
            record["n"] = 150
        write_data("results", records, values={"models": 2})
        result = table.build("results", data)
        self.assertEqual(table.joined_cells(result, table.body_items(result))[6][:2], [True, True])
        data["maps"]["cohort"] = {"site2": "A", "site3": "B"}
        result = table.build("results", data)
        self.assertEqual(table.joined_cells(result, table.body_items(result))[6][:2],
                         [False, False])

    def test_merge_needs_every_column_to_the_left_joined(self):
        records = json.loads((DATA / "results.json").read_text())["records"]
        for record in records:
            record["n"] = 150
        write_data("results", records, values={"models": 2})
        data = spec("results")
        data["columns"][0]["merge"] = False  # Cohort no longer merges
        result = table.build("results", data)
        joined = table.joined_cells(result, table.body_items(result))
        # Equal n, but the cohort column to its left is not joined.
        self.assertEqual(joined[2][:2], [False, False])
        self.assertEqual(joined[6][:2], [False, False])
        data["columns"][0]["merge"] = True
        result = table.build("results", data)
        joined = table.joined_cells(result, table.body_items(result))
        self.assertEqual(joined[2][:2], [True, True])
        self.assertEqual(joined[6][:2], [False, False])  # site2 -> site3

    def test_block_rows_span_the_table(self):
        lines = grid(table.build("results").markdown)
        block = next(line for line in lines if "Internal validation" in line)
        self.assertEqual(block.count("|"), 2)

    def test_single_leaf_pivot_has_one_header_level(self):
        data = spec("results")
        data["columns"][3]["columns"].pop()
        result = table.build("results", data)
        self.assertEqual([leaf.id for leaf in result.leaves][3:],
                         ["metric.auc.est", "metric.brier.est"])
        self.assertEqual([leaf.header for leaf in result.leaves][3:], ["AUC", "Brier score"])
        lines = grid(result.markdown)
        self.assertTrue(lines[2].startswith("+:="))  # header separator right after one row

    def test_pivot_with_two_records_for_a_value_is_an_error(self):
        records = json.loads((DATA / "results.json").read_text())["records"]
        records.append(dict(records[0]))
        write_data("results", records, values={"models": 2})
        error = table.build("results").errors[0]
        self.assertIn("2 records have metric=\"auc\"", error["message"])

    def test_unkeyed_field_that_differs_is_an_error(self):
        data = spec("results")
        data["columns"][3] = {"id": "value", "field": "value", "header": "V", "format": "est3"}
        error = table.build("results", data).errors[0]
        self.assertEqual(error["code"], "rows")
        self.assertIn("add it to rows.keys or pivot it", error["message"])

    def test_row_order_and_unlisted_values(self):
        data = spec("results")
        data["rows"]["order"]["method"] = ["forest"]
        result = table.build("results", data)
        self.assertEqual([r["key"]["method"] for r in result.rows[:2]], ["forest", "logistic"])
        self.assertIn('does not list "logistic"', result.warnings[0]["message"])


class EmphasisTests(Restore):
    def bold_cells(self, result):
        return {(result.rows[r]["key"]["method"], result.rows[r]["key"]["cohort"],
                 result.leaves[c].id) for r, c in result.bold}

    def test_highlight_within_block(self):
        bold = self.bold_cells(table.build("results"))
        self.assertIn(("forest", "site1", "metric.auc.est"), bold)
        self.assertIn(("forest", "site2", "metric.auc.est"), bold)
        self.assertNotIn(("logistic", "site3", "metric.auc.est"), bold)

    def test_highlight_within_a_key_and_ties(self):
        data = spec("results")
        data["highlight"] = [{"column": "metric.auc.est", "best": "max", "within": "cohort"}]
        records = json.loads((DATA / "results.json").read_text())["records"]
        for record in records:  # site1: both methods tie on AUC
            if record["cohort"] == "site1" and record["metric"] == "auc":
                record["value"]["est"] = 0.9
        write_data("results", records, values={"models": 2})
        bold = self.bold_cells(table.build("results", data))
        self.assertEqual(bold, {("logistic", "site1", "metric.auc.est"),
                                ("forest", "site1", "metric.auc.est"),
                                ("forest", "site2", "metric.auc.est"),
                                ("logistic", "site3", "metric.auc.est")})

    def test_highlight_ties_go_by_displayed_text(self):
        records = json.loads((DATA / "results.json").read_text())["records"]
        for record in records:  # site1: 0.8449 and 0.845 both show 0.845
            if record["cohort"] == "site1" and record["metric"] == "auc":
                record["value"]["est"] = 0.845 if record["method"] == "forest" else 0.8449
        write_data("results", records, values={"models": 2})
        bold = self.bold_cells(table.build("results"))
        self.assertIn(("forest", "site1", "metric.auc.est"), bold)
        self.assertIn(("logistic", "site1", "metric.auc.est"), bold)

    def test_emphasis_and_stale_emphasis(self):
        data = spec("results")
        data["highlight"] = []
        data["emphasis"] = [{"row": {"cohort": "site3", "method": "logistic"}, "column": "n"}]
        result = table.build("results", data)
        self.assertEqual(self.bold_cells(result), {("logistic", "site3", "n")})
        self.assertIn("| **90**", result.markdown)
        data["emphasis"] = [{"row": {"cohort": "site9"}, "column": "n"}]
        self.assertIn("matches 0 rows", table.build("results", data).errors[0]["message"])
        data["emphasis"] = [{"row": {"cohort": "site3", "method": "forest"}, "column": "gone"}]
        self.assertIn("no visible column 'gone'", table.build("results", data).errors[0]["message"])

    def test_highlight_does_not_substitute_another_composite_key(self):
        data = spec("results")
        data["highlight"] = [{"column": "metric.auc.est", "best": "max", "key": "missing"}]
        result = table.build("results", data)
        self.assertEqual(result.bold, set())
        self.assertIn("has no numbers", result.warnings[0]["message"])


class CommandTests(Restore):
    def test_list_reports_non_object_specs_without_hiding_other_tables(self):
        path = SPECS / "broken.json"
        for invalid in ([], None, "text"):
            with self.subTest(spec=invalid):
                path.write_text(json.dumps(invalid), encoding="utf-8")
                code, out, err = run(["list", "--json"])
                self.assertEqual(code, 0, err)
                entries = {item["label"]: item for item in json.loads(out)}
                self.assertEqual(entries["broken"],
                                 {"label": "broken", "data": None, "status": "error"})
                self.assertEqual(set(entries), {"broken", "cohorts", "results"})

    def test_stale_inputs(self):
        self.assertEqual(run(["check", "cohorts"])[0], 0)
        (_support.ROOT / "data/table_source.csv").write_text("changed\n", encoding="utf-8")
        code, out, _ = run(["check", "cohorts"])
        self.assertEqual(code, 1)
        self.assertIn("stale-data", out)
        self.assertEqual(run(["render", "cohorts"])[0], 1)
        self.assertIn("cohorts  data cohorts  stale", run(["list"])[1])

    def test_render_and_check_outputs(self):
        self.assertEqual(run(["check", "--all", "--outputs"])[0], 1)
        code, out, _ = run(["render", "--all"])
        self.assertEqual(code, 0, out)
        self.assertEqual(run(["check", "--all", "--outputs"])[0], 0)
        output = table.output_path("results")
        output.write_text(output.read_text() + "edited\n")
        code, out, _ = run(["check", "--all", "--outputs", "--json"])
        self.assertEqual(code, 1)
        payload = {entry["label"]: entry for entry in json.loads(out)}
        self.assertEqual(payload["results"]["issues"][0]["code"], "stale-output")
        self.assertTrue(payload["cohorts"]["ok"])

    def test_describe(self):
        code, out, _ = run(["describe", "results"])
        self.assertEqual(code, 0)
        for text in ("string: auc, brier", "composite {est, low, high}",
                     "metric.est", "pivot on metric", "est3 = ", "within block",
                     "{#tbl:results}"):
            self.assertIn(text, out)

    def test_new_scaffolds_a_clean_spec(self):
        code, out, _ = run(["new", "wide", "--data", "cohorts"])
        self.assertEqual(code, 0, out)
        created = spec("wide")
        self.assertEqual([c["field"] for c in created["columns"]],
                         ["cohort", "patients", "age", "female", "note"])
        self.assertEqual(table.build("wide").errors, [])
        self.assertEqual(run(["new", "wide", "--data", "cohorts"])[0], 1)
        self.assertIn("undone: new wide", run(["undo"])[1])
        self.assertFalse((SPECS / "wide.json").exists())


class PatchTests(Restore):
    def patch(self, ops):
        return run(["patch", "results", "-"], stdin=json.dumps(ops))

    def test_every_op(self):
        before = (SPECS / "results.json").read_bytes()
        code, out, err = self.patch([
            {"op": "set", "path": "columns.metric.est.header", "value": "Estimate"},
            {"op": "set", "path": "title", "value": "Model performance."},
            {"op": "set", "path": "maps.cohort.site1", "value": "Site One"},
            {"op": "set", "path": "legend", "value": None},
            {"op": "add", "into": "columns", "before": "method",
             "item": {"id": "metric_name", "field": "validation", "header": "Validation",
                      "hidden": True}},
            {"op": "add", "into": "columns.metric",
             "item": {"id": "low", "field": "value", "header": "Low", "format": "low"}},
            {"op": "set", "path": "formats.low", "value": "{low:.2f}"},
            {"op": "move", "column": "metric.low", "before": "metric.est"},
            {"op": "move", "column": "method", "before": "cohort"},
            {"op": "remove", "column": "metric.ci"},
        ])
        self.assertEqual(code, 0, out + err)
        data = spec("results")
        self.assertEqual([c["id"] for c in data["columns"]],
                         ["method", "cohort", "n", "metric_name", "metric"])
        self.assertEqual([c["id"] for c in data["columns"][4]["columns"]], ["low", "est"])
        self.assertEqual(data["columns"][4]["columns"][1]["header"], "Estimate")
        self.assertNotIn("legend", data)
        self.assertEqual(data["maps"]["cohort"]["site1"], "Site One")
        text = (SPECS / "results.json").read_text(encoding="utf-8")
        self.assertEqual(text, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        self.assertIn("undone: patch results", run(["undo"])[1])
        self.assertEqual((SPECS / "results.json").read_bytes(), before)

    def test_patch_is_all_or_nothing(self):
        before = (SPECS / "results.json").read_bytes()
        code, _, err = self.patch([{"op": "set", "path": "title", "value": "New."},
                                   {"op": "remove", "column": "nope"}])
        self.assertEqual(code, 1)
        self.assertIn("no column 'nope'", err)
        code, out, err = self.patch([{"op": "set", "path": "columns.n.format", "value": "zzz"}])
        self.assertEqual(code, 1)
        self.assertIn("unknown format", out)
        self.assertEqual((SPECS / "results.json").read_bytes(), before)
        self.assertEqual(run(["undo"])[1].strip(), "nothing to undo")

    def test_ids_are_stable_and_undo_refuses_a_changed_file(self):
        self.assertIn("ids are stable",
                      self.patch([{"op": "set", "path": "columns.n.id", "value": "m"}])[2])
        self.assertEqual(self.patch([{"op": "set", "path": "title", "value": "T."}])[0], 0)
        (SPECS / "results.json").write_text("{}\n")
        code, _, err = run(["undo"])
        self.assertEqual(code, 1)
        self.assertIn("changed since", err)


class ValueFormatTests(Restore):
    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.values = self.tmp / "values.json"
        self.values.write_text(json.dumps({"schema_version": 1, "inputs": {}, "values": {
            "age": {"value": {"mean": 61.24, "sd": 9.81}, "text": "61.2", "source": "t"},
            "auc": {"value": 0.8123, "text": "0.81", "source": "t"}}}))

    def expand(self, text):
        doc = self.tmp / "doc.md"
        doc.write_text(text, encoding="utf-8")
        return expand.expand_files([doc], values_path=self.values, check_inputs=False)

    def test_named_formats_apply_to_raw_values(self):
        self.assertEqual(self.expand("Age {{v:age|mean_sd}}, AUC {{v:auc|dec3}}, {{v:auc}}.\n"),
                         "Age 61.2 (9.8), AUC 0.812, 0.81.\n")

    def test_unknown_format_or_unfit_value_is_an_error(self):
        with self.assertRaisesRegex(expand.ValuesError, "unknown format 'nope'"):
            self.expand("{{v:auc|nope}}\n")
        with self.assertRaisesRegex(expand.ValuesError, "auc\\|mean_sd"):
            self.expand("{{v:auc|mean_sd}}\n")
        with self.assertRaisesRegex(expand.ValuesError, "unknown value key: gone"):
            self.expand("{{v:gone|dec3}}\n")

    def test_marked_values_keep_the_plain_key(self):
        doc = self.tmp / "doc.md"
        doc.write_text("AUC {{v:auc|dec3}}\n", encoding="utf-8")
        out = expand.expand_files([doc], values_path=self.values, check_inputs=False, mark=True)
        self.assertIn('[0.812]{.pv-val data-key="auc"}', out)

    def test_locate_matches_formatted_values(self):
        values = expand.Values(expand.load_entries(self.values, check_inputs=False))
        tokens = preview_locate.normalize_source("AUC {{v:auc|dec3}} {{v:auc|nope}}", values)
        self.assertEqual(tokens["812"], 1)
        self.assertNotIn("nope", tokens)

    def test_diagrams_read_keys_and_reject_formats(self):
        source = {"nodes": [{"id": "a", "kind": "box", "text": "n = {{v:cohort.n}}"},
                            {"id": "b", "kind": "box", "text": "{{v:cohort.n|dec3}}"}]}
        self.assertEqual(diagram.value_keys(source), ["cohort.n"])
        values, problems = diagram.resolve_values(diagram.value_keys(source))
        self.assertEqual((values, problems), ({"cohort.n": "120"}, []))
        self.assertEqual([p["code"] for p in diagram.format_problems(source)], ["value-format"])


class ModelTests(Restore):
    def assert_spans_match(self, result, model):
        """Header cells, block rows and the cells left after merges agree with the grid."""
        split = lambda line: re.split(r"(?<!\\)\|", line.strip("|"))  # noqa: E731
        lines = grid(result.markdown)
        sep = next(i for i, line in enumerate(lines) if line.startswith(("+:", "+=")))
        self.assertEqual(len(model["header"]), 1 if sep == 2 else 2)
        self.assertEqual(len(split(lines[1])), len(model["header"][0]))
        leaves = [c for row in model["header"] for c in row if c["kind"] == "leaf"]
        self.assertEqual([c["id"] for c in leaves if not c["hidden"]],
                         [leaf.id for leaf in result.leaves if leaf.group is None]
                         + [leaf.id for leaf in result.leaves if leaf.group is not None]
                         if len(model["header"]) == 2 else [leaf.id for leaf in result.leaves])
        body = [line for line in lines[sep + 1:] if line.startswith("|") and "+-" not in line]
        self.assertEqual(len(body), len(model["body"]))
        for line, item in zip(body, model["body"]):
            cells = split(line)
            if "block" in item:
                self.assertEqual(len(cells), 1)
                self.assertEqual(table.md_inline(cells[0].strip()), item["block"])
                continue
            shown = [c for c in item["cells"] if not c["hidden"]]
            self.assertEqual(len([c for c in cells if c.strip()]), len(shown), line)

    def test_spans_match_the_grid(self):
        for label in ("cohorts", "results"):
            with self.subTest(label=label):
                result = table.build(label)
                self.assert_spans_match(result, table.render_model(result))
        model = table.render_model(table.build("results"))
        first = model["body"][1]["cells"]
        self.assertEqual([(c["id"], c["rowspan"]) for c in first[:2]], [("cohort", 2), ("n", 2)])
        self.assertEqual(model["header"][0][3]["kind"], "pivot-value")
        self.assertEqual(model["header"][0][3]["colspan"], 2)
        self.assertTrue(first[3]["raw"]["est"] == 0.812 and first[3]["bold"] is False)

    def test_hidden_leaves_are_flagged_and_change_nothing_else(self):
        data = spec("results")
        data["columns"].insert(1, {"id": "val", "field": "validation", "header": "Validation",
                                   "hidden": True})
        data["columns"][4]["columns"][1]["hidden"] = True  # metric.<v>.ci
        result = table.build("results", data)
        plain = table.render_model(result)
        full = table.render_model(result, hidden=True)
        self.assert_spans_match(result, plain)
        self.assertEqual(plain, table.render_model(table.build("results", data)))
        ids = [c["id"] for row in full["header"] for c in row if c["kind"] == "leaf"]
        self.assertIn("val", ids)
        self.assertIn("metric.auc.ci", ids)
        for a, b in zip(plain["body"], full["body"]):
            if "block" in a:
                continue
            self.assertEqual([c for c in b["cells"] if not c["hidden"]], a["cells"])
            hidden = [c for c in b["cells"] if c["hidden"]]
            self.assertTrue(all(c["rowspan"] == 1 for c in hidden))
        self.assertEqual(full["body"][1]["cells"][1]["text_html"], "Internal validation")

    def test_inline_markdown(self):
        self.assertEqual(table.md_inline(r"**0.85** and *n* a\*b\_c\|d <x> & y"),
                         "<strong>0.85</strong> and <em>n</em> a*b_c|d &lt;x&gt; &amp; y")
        self.assertEqual(table.md_inline("2 * 3 * 4"), "2 * 3 * 4")
        self.assertEqual(table.md_inline(r"\*\*not bold\*\*"), "**not bold**")
        self.assertEqual(table.md_inline("[Scores by *site* [@ref].]{.caption-title} Legend."),
                         '<span class="caption-title">Scores by <em>site</em> [@ref].</span> Legend.')


class SharedAndHistoryTests(Restore):
    def test_shared_patch_refused_when_a_clean_table_breaks(self):
        before = table.SHARED_PATH.read_bytes()
        with self.assertRaises(table.SpecInvalid) as caught:
            table.patch_shared([{"op": "set", "path": "formats.mean_sd", "value": "{x:.1f}"}])
        self.assertIn("cohorts", caught.exception.issues[0]["message"])
        self.assertEqual(table.SHARED_PATH.read_bytes(), before)
        with self.assertRaises(table.TableError):
            table.patch_shared([{"op": "set", "path": "title", "value": "x"}])
        with self.assertRaises(table.SpecChanged):
            table.patch_shared([{"op": "set", "path": "maps.method.forest", "value": "RF"}], "0" * 64)

    def test_shared_patch_renders_every_table(self):
        code, out, _ = run(["patch", "--shared", "-"], stdin=json.dumps(
            [{"op": "set", "path": "formats.mean_sd", "value": "{mean:.2f} ({sd:.2f})"},
             {"op": "set", "path": "maps.method.forest", "value": "RF"}]))
        self.assertEqual(code, 0, out)
        self.assertIn("61.24 (9.81)", table.output_path("cohorts").read_text())
        self.assertIn("| RF ", table.output_path("results").read_text())
        self.assertIn("undone: patch shared", table.undo())
        self.assertIn("61.2 (9.8)", table.output_path("cohorts").read_text())

    def test_format_usage(self):
        prose = _support.ROOT / "manuscript/main/09_usage.md"
        prose.write_text("A {{v:cohort.n|dec3}} and {{v:cohort.n}}.\n", encoding="utf-8")
        self.addCleanup(prose.unlink)
        usage = table.format_usage()
        self.assertEqual(usage["mean_sd"]["tables"], [{"label": "cohorts", "leaves": ["age"], "local": False}])
        self.assertTrue(usage["mean_sd"]["shared"])
        self.assertEqual(usage["est3"]["tables"][0]["leaves"],
                         ["metric.auc.est", "metric.brier.est"])
        self.assertFalse(usage["est3"]["shared"])
        self.assertEqual(usage["dec3"]["prose"],
                         [{"file": "manuscript/main/09_usage.md", "line": 1, "key": "cohort.n"}])

    def test_patch_renders_and_undo_is_per_table(self):
        table.patch("results", [{"op": "set", "path": "title", "value": "Results."}])
        self.assertIn("Table: [Results.]{.caption-title}", table.output_path("results").read_text())
        table.patch("cohorts", [{"op": "set", "path": "title", "value": "Cohorts again."}])
        self.assertIn("undone: patch results", table.undo("results"))
        self.assertEqual(spec("results")["title"], "Discrimination and calibration of the {models} models.")
        self.assertEqual(spec("cohorts")["title"], "Cohorts again.")
        self.assertIn("Table: [Discrimination", table.output_path("results").read_text())
        self.assertEqual(table.undo("results"), "nothing to undo")
        self.assertIn("undone: patch cohorts", table.undo())

    def test_patch_refuses_a_stale_base(self):
        with self.assertRaises(table.SpecChanged) as caught:
            table.patch("results", [{"op": "set", "path": "title", "value": "X."}], "0" * 64)
        self.assertEqual(caught.exception.sha256, table.sha256_of(table.spec_path("results")))


class CommentTests(Restore):
    def add(self, **extra):
        return table.COMMENTS.change({"op": "add", "table": "results", "comment": "Check this",
                                      **extra})["comments"][-1]

    def test_ops_cli_and_undo(self):
        cell = self.add(target={"column": "metric.auc.est", "row": {"validation": "internal",
                                                                    "cohort": "site1", "method": "forest"}})
        self.add(target={"column": "n"})
        self.add()
        with self.assertRaises(table.RequestError):
            self.add(target={"row": {}})
        with self.assertRaises(table.NotFound):
            table.COMMENTS.change({"op": "add", "table": "nope", "comment": "x"})
        code, out, _ = run(["comments", "results"])
        self.assertIn("metric.auc.est · internal · site1 · forest", out)
        self.assertEqual(out.count("open |"), 3)
        code, out, _ = run(["resolve", cell["id"], "Checked."])
        self.assertEqual(code, 0)
        state = table.COMMENTS.payload("results")
        self.assertEqual(next(c for c in state["comments"] if c["id"] == cell["id"])["status"], "review")
        self.assertTrue(state["undo"].startswith("reply (results:"), state["undo"])
        other = self.add(table="cohorts")
        undone = table.COMMENTS.change({"op": "undo", "table": "results"})
        self.assertEqual(next(c for c in undone["comments"] if c["id"] == cell["id"])["status"], "open")
        self.assertEqual(table.COMMENTS.payload("cohorts")["comments"], [other])
        self.assertEqual(next(c for c in table.COMMENTS.load() if c["id"] == cell["id"])["status"], "open")
        payload = table.COMMENTS.change({"op": "update", "id": cell["id"], "status": "done"})
        self.assertEqual(payload["undo"], f"mark done (results: {table.comment_place(cell)})")
        table.COMMENTS.change({"op": "undo", "table": "results"})
        self.assertEqual(next(c for c in table.COMMENTS.load() if c["id"] == cell["id"])["status"], "open")
        self.assertIn(other["id"], run(["comments", "cohorts", "--all"])[1])


@unittest.skipUnless(shutil.which("pandoc"), "needs pandoc")
class PandocTests(Restore):
    def test_docx_has_the_grid_structure(self):
        result = table.build("results")
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "table.md"
            source.write_text(result.markdown, encoding="utf-8")
            out = Path(folder) / "table.docx"
            subprocess.run(["pandoc", str(source), "-o", str(out)], check=True)
            with zipfile.ZipFile(out) as docx:
                xml = docx.read("word/document.xml").decode("utf-8")
        self.assertEqual(len(re.findall(r"<w:gridCol ", xml)), len(result.leaves))
        self.assertRegex(xml, r'<w:gridSpan w:val="7"')  # block row
        self.assertRegex(xml, r'<w:gridSpan w:val="2"')  # pivot group header
        self.assertIn("<w:vMerge", xml)


if __name__ == "__main__":
    unittest.main()
