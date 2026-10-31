"""Source edits and independent exports at their user-facing boundaries."""

import base64
import io
import json
import shutil
import struct
import unittest
import xml.etree.ElementTree as ET
import zipfile
from unittest.mock import patch

import _support

from trazo import diagram, preview, preview_export


class SourceEdits(unittest.TestCase):
    def setUp(self):
        self.path = _support.ROOT / "manuscript/main/99_viewer_edit.md"
        self.file = self.path.relative_to(_support.ROOT).as_posix()
        self.addCleanup(self.path.unlink, missing_ok=True)
        self.history = _support.ROOT / "viewer-file-history.json"
        self.addCleanup(self.history.unlink, missing_ok=True)
        patcher = patch.object(preview, "FILE_HISTORY_PATH", self.history)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_caption_changes_original_image_and_conflicts_and_undoes(self):
        original = ("# Caption edit\n\n<!-- ![Commented](x.png){#fig:edit} -->\n"
            "`![Code](x.png){#fig:edit}`\n\n````markdown\n```\n![Example](x.png){#fig:edit}\n````\n\n"
            "![Original **title** and [link](https://example.org).](figures/external/a.png \"Image title\"){#fig:edit width=80%}\n")
        self.path.write_text(original)
        state = preview.get_figure_text("manuscript", "fig:edit")
        changed = preview.change_figure_text({"doc": "manuscript", "id": "fig:edit",
            "caption": "Short **title**. Legend with $x^2$.", "base_sha256": state["sha256"]})
        self.assertEqual(self.path.read_text(), original.replace(
            "Original **title** and [link](https://example.org).", "Short **title**. Legend with $x^2$."))
        with self.assertRaises(preview.Conflict):
            preview.change_figure_text({"doc": "manuscript", "id": "fig:edit", "caption": "Stale",
                "base_sha256": state["sha256"]})
        preview.change_figure_text({"op": "undo", "doc": "manuscript", "id": "fig:edit"})
        self.assertEqual(self.path.read_text(), original)
        self.assertTrue(changed["can_undo"])

    def test_markdown_table_caption_preserves_source_and_guards_saves_and_undo(self):
        original = ("# Tables\n\n<!-- Table: Commented {#tbl:edit.v1} -->\n\n"
            "```markdown\nTable: Example {#tbl:edit.v1}\n```\n\n"
            "| A | B |\n|---|---|\n| 1 | 2 |\n\n"
            ": **Original title**. Legend with `code`\n  and {{v:cohort.n}}. {#tbl:edit.v1 .wide}\n")
        self.path.write_text(original)
        state = preview.get_table_caption("manuscript", "tbl:edit.v1")
        self.assertEqual(state["kind"], "markdown")
        caption = "**Updated title**. Legend with [a link](https://example.org) and $x^2$."
        saved = preview.change_table_caption({"doc": "manuscript", "id": "tbl:edit.v1",
            "base_sha256": state["sha256"], "changes": {"caption": caption}})
        self.assertEqual(self.path.read_text(), original.replace(
            "**Original title**. Legend with `code`\n  and {{v:cohort.n}}.", caption))
        with self.assertRaises(preview.Conflict):
            preview.change_table_caption({"doc": "manuscript", "id": "tbl:edit.v1",
                "base_sha256": state["sha256"], "changes": {"caption": "Stale"}})
        self.assertTrue(saved["can_undo"])
        preview.change_table_caption({"op": "undo", "doc": "manuscript", "id": "tbl:edit.v1"})
        self.assertEqual(self.path.read_text(), original)

    def test_markdown_table_note_is_added_edited_and_removed_after_table_and_caption(self):
        for layout, original in (
                ("caption first", "# Tables\n\nTable: Title. {#tbl:note}\n\n| A |\n|---|\n| 1 |\n\nAfter.\n"),
                ("caption after", "# Tables\n\n+---+\n| A |\n+===+\n| 1 |\n+---+\n\n: Title. {#tbl:note}\n\nAfter.\n")):
            with self.subTest(layout=layout):
                self.path.write_text(original)
                change = lambda note: preview.change_table_caption({"doc": "manuscript", "id": "tbl:note",
                    "base_sha256": preview.get_table_caption("manuscript", "tbl:note")["sha256"], "changes": {"note": note}})
                note = lambda state: next(f for f in state["fields"] if f["key"] == "note")["segments"][0]["text"]
                self.assertEqual(note(preview.get_table_caption("manuscript", "tbl:note")), "")
                saved = change("SD, standard\n deviation.\n\n*p* < 0.05.")
                self.assertEqual(note(saved), "SD, standard deviation.\n\n*p* < 0.05.")
                self.assertTrue(self.path.read_text().endswith(
                    '\n\n::: {.table-note custom-style="Table Note"}\nSD, standard deviation.\n\n*p* < 0.05.\n:::\n\nAfter.\n'))
                self.assertEqual(note(change("Only one.")), "Only one.")
                with self.assertRaises(preview.CommentError):
                    change("::: nested")
                change("")
                self.assertEqual(self.path.read_text(), original)

    def test_generated_caption_uses_the_spec_with_locked_values(self):
        for label, kind in (("cohorts", "spec"),):
            with self.subTest(kind=kind):
                state = preview.get_table_caption("supplementary", "tbl:" + label)
                self.assertEqual(state["kind"], kind)
                path = _support.ROOT / state["file"]
                original = path.read_bytes()
                self.addCleanup(path.write_bytes, original)
                # A spec has a note below the table.
                self.assertEqual({f["key"] for f in state["fields"]}, {"title", "legend", "note"})
                legend = next(f for f in state["fields"] if f["key"] == "legend")
                self.assertIn("160", [s.get("shown") for s in legend["segments"]])
                saved = preview.change_table_caption({"doc": "supplementary", "id": "tbl:" + label,
                    "base_sha256": state["sha256"],
                    "changes": {"title": "**Updated** title.", "legend": "All {n} patients."}})
                self.assertIn(b"All {n} patients.", path.read_bytes())
                self.assertTrue(saved["can_undo"])
                with self.assertRaises(preview.Conflict):
                    preview.change_table_caption({"doc": "supplementary", "id": "tbl:" + label,
                        "base_sha256": state["sha256"], "changes": {"title": "Stale"}})
                with self.assertRaises(preview.CommentError):
                    preview.change_table_caption({"doc": "supplementary", "id": "tbl:" + label,
                        "base_sha256": saved["sha256"], "changes": {"columns": "Invalid"}})
                written = path.read_bytes()
                with self.assertRaises(preview.CommentError):
                    preview.change_table_caption({"doc": "supplementary", "id": "tbl:" + label,
                        "base_sha256": saved["sha256"], "changes": {"legend": "Unknown {not_a_value}."}})
                self.assertEqual(path.read_bytes(), written)
                preview.change_table_caption({"op": "undo", "doc": "supplementary", "id": "tbl:" + label})
                self.assertEqual(path.read_bytes(), original)


@unittest.skipUnless(shutil.which("pandoc"), "needs Pandoc")
class ObjectExports(unittest.TestCase):
    def setUp(self):
        self.path = _support.ROOT / "manuscript/main/99_viewer_export.md"
        self.addCleanup(self.path.unlink, missing_ok=True)
        self.svg = _support.ROOT / "figures/external/viewer-export.svg"
        self.svg.parent.mkdir(parents=True, exist_ok=True)
        self.svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 60">'
                            '<rect width="200" height="60" fill="#117799"/><text x="10" y="35">Vector content</text></svg>')
        self.addCleanup(self.svg.unlink, missing_ok=True)
        self.path.write_text("# Export first\n\n![First figure.](figures/external/viewer-export.svg){#fig:export-first}\n\n"
            "## Export child\n\nChild prose [@smith2020].\n\n"
            "# Export omitted\n\nUnselected prose.\n\n"
            "# Export selected\n\nSelected prose [with underline]{.underline}, $x^2$, and [@smith2020].\n\n"
            "![**Second title**. Legend $x^2$.](figures/external/viewer-export.svg){#fig:export-second}\n\n"
            "| A | B |\n|---|---|\n| 1 | 2 |\n\nTable: **Second table title**. Legend. {#tbl:export-second}\n")

    def docx(self, kind, identifier, **options):
        data, mime, filename = preview_export.export("manuscript", kind, identifier, "docx", **options)
        self.assertTrue(filename.endswith(".docx"))
        with zipfile.ZipFile(io.BytesIO(data)) as doc:
            xml = doc.read("word/document.xml")
        return ET.fromstring(xml), xml

    def test_selected_sections_share_one_docx_in_document_order_without_duplicates(self):
        section, xml = self.docx("section", ["export-selected", "export-child", "export-first", "export-first"])
        text = "".join(section.itertext())
        self.assertLess(text.index("Export first"), text.index("Export child"))
        self.assertLess(text.index("Export child"), text.index("Export selected"))
        self.assertEqual(text.count("Child prose"), 1)
        self.assertEqual(text.count("First figure"), 1)
        self.assertEqual(text.count("Smith"), 1)
        self.assertNotIn("Unselected prose", text)
        self.assertNotIn("We studied", text)
        self.assertIn("Figure 2", text)
        self.assertIn("Table 2", text)
        self.assertIn(b"m:oMath", xml)

    def test_exports_leave_out_the_title_block_and_generated_authors(self):
        metadata = _support.ROOT / "manuscript/metadata.yaml"
        original = metadata.read_bytes()
        self.addCleanup(metadata.write_bytes, original)
        metadata.write_text(json.dumps({"title": "Export Document Title", "author": [
            {"name": "Structured Export Author", "affiliations": [{"name": "Export Institution"}]}]}))
        for kind, identifier in (("section", "export-selected"), ("figure", "fig:export-second"),
                                 ("table", "tbl:export-second")):
            with self.subTest(kind=kind):
                document, _ = self.docx(kind, identifier)
                text = "".join(document.itertext())
                for generated in ("Export Document Title", "Structured Export Author", "Export Institution"):
                    self.assertNotIn(generated, text)

    def test_excluded_subsections_and_references_can_be_left_out(self):
        section, _ = self.docx("section", ["export-first", "export-selected"], exclude=["export-child"])
        text = "".join(section.itertext())
        self.assertIn("First figure", text)
        self.assertIn("Selected prose", text)
        self.assertNotIn("Child prose", text)
        section, _ = self.docx("section", ["export-selected"], references=False)
        self.assertNotIn("Smith", "".join(section.itertext()))
        with self.assertRaises(preview_export.RequestError):
            preview_export.export("manuscript", "section", ["export-first"], "docx", ["export-first"])

    def test_section_indexes_are_added_only_when_the_exported_content_has_entries(self):
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        toc, lof, lot = ('TOC \\o "1-3" \\h \\z \\u', 'TOC \\h \\z \\t "Image Caption,1"',
                         'TOC \\h \\z \\t "Table Caption,1"')
        for ids, indexes, instructions in (
                (["export-selected"], (), []),
                (["export-selected"], ("lof",), [lof]),
                (["export-selected"], ("lot",), [lot]),
                # One heading leaves a table of contents nothing to list.
                (["export-selected"], ("toc",), []),
                # A section with its subsection, a figure and no table.
                (["export-first"], ("toc", "lof", "lot"), [toc, lof])):
            with self.subTest(ids=ids, indexes=indexes):
                document, _ = self.docx("section", ids, indexes=indexes)
                self.assertEqual([n.text for n in document.findall('.//w:instrText', ns)], instructions)
                # The indexes end with a page break before the exported content.
                breaks = document.findall('.//w:sdtContent/w:p/w:r/w:br', ns)
                self.assertEqual([b.get(f"{{{ns['w']}}}type") for b in breaks], ["page"] if instructions else [])
                text = "".join(document.itertext())
                if ids == ["export-selected"]:
                    self.assertIn("Second title", text)
                    self.assertNotIn("First figure", text)
                    self.assertNotIn("Unselected prose", text)

    def test_section_table_and_figure_exports_preserve_numbers_and_docx_content(self):
        section, xml = self.docx("section", "export-selected")
        text = "".join(section.itertext())
        self.assertIn("Export selected", text)
        self.assertNotIn("Export first", text)
        self.assertIn("Figure 2", text)
        self.assertIn("Table 2", text)
        self.assertIn("Smith", text)
        self.assertIn(b"m:oMath", xml)
        self.assertIn(b'w:u ', xml)
        figure, xml = self.docx("figure", "fig:export-second")
        self.assertIn("Figure 2", "".join(figure.itertext()))
        self.assertNotIn("Selected prose", "".join(figure.itertext()))
        self.assertIn(b"w:drawing", xml)
        table, xml = self.docx("table", "tbl:export-second")
        self.assertIn("Table 2", "".join(table.itertext()))
        self.assertIn(b"w:tbl", xml)
        md, _, _ = preview_export.export("manuscript", "table", "tbl:export-second", "md")
        self.assertIn(b"Table 2", md)
        self.assertIn(b"Second table title", md)

    def test_table_exports_bring_the_table_note(self):
        self.path.write_text(self.path.read_text() + '\n::: {.table-note custom-style="Table Note"}\n'
                             "SD, standard deviation.\n:::\n\nProse after the note.\n")
        table, xml = self.docx("table", "tbl:export-second")
        self.assertIn("SD, standard deviation.", "".join(table.itertext()))
        self.assertNotIn("Prose after the note", "".join(table.itertext()))
        self.assertIn(b'w:pStyle w:val="TableNote"', xml)
        md, _, _ = preview_export.export("manuscript", "table", "tbl:export-second", "md")
        self.assertIn(b"table-note", md)
        self.assertIn(b"SD, standard deviation.", md)

    @unittest.skipUnless(diagram.CHROME, "needs Chrome")
    def test_multipanel_figures_export_as_png_and_pdf(self):
        second = self.svg.with_name("viewer-export-second.svg")
        second.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 60">'
                          '<rect width="200" height="60" fill="#ee33aa"/></svg>')
        self.addCleanup(second.unlink, missing_ok=True)
        self.path.write_text(
            '# Multiple panels\n\n::: {#fig:panels}\n'
            '![First panel](figures/external/viewer-export.svg){#fig:panel-a}\n\n'
            '![Second panel](figures/external/viewer-export-second.svg){#fig:panel-b}\n\n'
            'Combined figure caption.\n:::\n')
        for fmt, signature in (("png", b"\x89PNG\r\n\x1a\n"), ("pdf", b"%PDF-")):
            with self.subTest(format=fmt):
                data, mime, filename = preview_export.export("manuscript", "figure", "fig:panels", fmt)
                self.assertTrue(data.startswith(signature))
                self.assertEqual(mime, "image/png" if fmt == "png" else "application/pdf")
                self.assertTrue(filename.endswith("." + fmt))
                if fmt == "png":
                    width, height = struct.unpack(">II", data[16:24])
                    self.assertEqual(width, 2000)
                    self.assertGreater(height, 0)
                    # Decode the delivered PNG and check each panel's own colour.
                    with diagram.Chrome() as chrome, chrome.target() as session:
                        result = chrome.call("Runtime.evaluate", {
                            "expression": """(async () => {
                              const image = new Image();
                              image.src = 'data:image/png;base64,""" + base64.b64encode(data).decode() + """';
                              await image.decode();
                              const canvas = document.createElement('canvas');
                              canvas.width = image.width; canvas.height = image.height;
                              const ctx = canvas.getContext('2d'); ctx.drawImage(image, 0, 0);
                              const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
                              const colours = [[17, 119, 153], [238, 51, 170]], found = [false, false];
                              for (let i = 0; i < pixels.length; i += 4)
                                colours.forEach((rgb, n) => { if (rgb.every((v, k) => v === pixels[i + k])) found[n] = true; });
                              return found;
                            })()""", "awaitPromise": True, "returnByValue": True}, session)
                    self.assertEqual(result["result"].get("value"), [True, True], result)

    @unittest.skipUnless(diagram.CHROME, "needs Chrome")
    def test_figure_image_formats_include_caption_and_keep_svg_vectors(self):
        svg, _, _ = preview_export.export("manuscript", "figure", "fig:export-second", "svg")
        root = ET.fromstring(svg)
        self.assertIn("Second title", "".join(root.itertext()))
        self.assertTrue(any(n.tag.endswith("rect") for n in root.iter()))
        self.assertFalse(any(n.tag.endswith("image") for n in root.iter()))
        png, _, _ = preview_export.export("manuscript", "figure", "fig:export-second", "png")
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        width, height = struct.unpack(">II", png[16:24])
        self.assertGreater(width, 1000)
        self.assertGreater(height, 120)
        pdf, _, _ = preview_export.export("manuscript", "figure", "fig:export-second", "pdf")
        self.assertTrue(pdf.startswith(b"%PDF-"))


if __name__ == "__main__":
    unittest.main()
