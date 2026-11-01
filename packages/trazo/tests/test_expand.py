"""Includes and {{v:key}} values."""

import hashlib
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import _support

from trazo import build_graph, dependencies, expand, submission_check


def write_values(path, values):
    payload = {"schema_version": 1, "inputs": {},
               "values": {k: {"value": t, "text": t, "source": "test"} for k, t in values.items()}}
    path.write_text(json.dumps(payload), encoding="utf-8")


class SubstitutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.values_path = self.tmp / "values.json"
        write_values(self.values_path, {"a.n": "1,000", "b.rate": "0.77%"})
        self.values = expand.load_values(self.values_path, check_inputs=False)

    def test_known_key_is_replaced_by_its_text(self):
        self.assertEqual(expand.substitute("n = {{v:a.n}}.", self.values), "n = 1,000.")

    def test_multiple_placeholders_per_line(self):
        self.assertEqual(expand.substitute("{{v:a.n}} and {{v:b.rate}}", self.values),
                         "1,000 and 0.77%")

    def test_unknown_key_fails_with_the_key_name(self):
        with self.assertRaisesRegex(expand.ValuesError, "missing.key"):
            expand.substitute("x {{v:missing.key}}", self.values)

    def test_inline_code_and_fenced_blocks_are_left_alone(self):
        text = "`{{v:a.n}}` {{v:a.n}}\n```\n{{v:nope}}\n```\n{{v:b.rate}}"
        self.assertEqual(expand.substitute(text, self.values),
                         "`{{v:a.n}}` 1,000\n```\n{{v:nope}}\n```\n0.77%")

    def test_marked_values_get_spans_outside_tables(self):
        text = "n = {{v:a.n}}\n\n| x |\n|---|\n| {{v:a.n}} |"
        out = expand.substitute(text, self.values, mark=True)
        self.assertIn('n = [1,000]{.pv-val data-key="a.n"}', out)
        self.assertIn("| 1,000 |", out)

    def test_missing_values_file_fails_with_the_values_command(self):
        with self.assertRaisesRegex(expand.ValuesError, "scripts/trazo build"):
            expand.load_values(self.tmp / "absent.json")

    def recorded_input(self):
        """An input whose digest the values file records."""
        source = self.tmp / "input.csv"
        source.write_text("x\n1\n", encoding="utf-8")
        payload = json.loads(self.values_path.read_text())
        payload["inputs"] = {str(source): hashlib.sha256(source.read_bytes()).hexdigest()}
        self.values_path.write_text(json.dumps(payload))
        return source

    def test_stale_values_file_fails_clearly(self):
        source = self.recorded_input()
        expand.load_values(self.values_path)
        source.write_text("x\n2\n", encoding="utf-8")
        with self.assertRaisesRegex(expand.ValuesError, "stale"):
            expand.load_values(self.values_path)

    @unittest.skipIf(os.name == "nt", "Windows has no change time: st_ctime is the creation time")
    def test_input_edited_keeping_size_and_mtime_is_still_stale(self):
        source = self.recorded_input()
        # Cache digests at once, as for an input untouched for a while.
        with patch.object(dependencies, "SETTLE_NS", 0):
            expand.load_values(self.values_path)
            before = source.stat()
            for _ in range(500):  # until the edit lands in a later ctime tick
                source.write_text("x\n2\n", encoding="utf-8")
                os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
                if source.stat().st_ctime_ns != before.st_ctime_ns:
                    break
                time.sleep(0.01)
            after = source.stat()
            self.assertEqual((after.st_size, after.st_mtime_ns), (before.st_size, before.st_mtime_ns))
            self.assertNotEqual(after.st_ctime_ns, before.st_ctime_ns)
            with self.assertRaisesRegex(expand.ValuesError, "stale"):
                expand.load_values(self.values_path)

    def test_input_edited_within_one_timestamp_tick_is_still_stale(self):
        source = self.recorded_input()
        # A file system with coarse timestamps: both versions share one stat.
        frozen = source.stat()

        def coarse(real):
            def stat(target, *args, **kwargs):
                st = real(target, *args, **kwargs)
                return frozen if (st.st_dev, st.st_ino) == (frozen.st_dev, frozen.st_ino) else st
            return stat

        with patch.object(os, "stat", coarse(os.stat)), patch.object(os, "fstat", coarse(os.fstat)):
            expand.load_values(self.values_path)
            source.write_text("x\n2\n", encoding="utf-8")
            with self.assertRaisesRegex(expand.ValuesError, "stale"):
                expand.load_values(self.values_path)

    def test_files_without_placeholders_need_no_values_file(self):
        doc = self.tmp / "doc.md"
        doc.write_text("plain\n", encoding="utf-8")
        self.assertEqual(expand.expand_files([doc], values_path=self.tmp / "absent.json"),
                         "plain\n")

    def test_code_literals_need_no_values_file(self):
        doc = self.tmp / "doc.md"
        text = ("`{{v:literal}}`\n\n````md\n```\n{{v:literal}}\n"
                "```\n````\n\n~~~\n{{v:literal}}\n~~~\n")
        doc.write_text(text, encoding="utf-8")
        self.assertEqual(expand.expand_files([doc], values_path=self.tmp / "absent.json"),
                         text)

    def test_project_relative_includes_work_from_a_subfolder(self):
        included = self.tmp / "tables" / "result.md"
        included.parent.mkdir()
        included.write_text("Included result.\n", encoding="utf-8")
        source = self.tmp / "sections" / "doc.md"
        source.parent.mkdir()
        source.write_text("!include tables/result.md\n", encoding="utf-8")
        with patch.object(expand, "ROOT", self.tmp):
            self.assertIn("Included result.", expand.expand_files([source]))

    def test_expand_files_substitutes_included_content(self):
        part = self.tmp / "part.md"
        part.write_text("N = {{v:a.n}}\n", encoding="utf-8")
        doc = self.tmp / "doc.md"
        doc.write_text(f"!include {part}\n", encoding="utf-8")
        out = expand.expand_files([doc], values_path=self.values_path, check_inputs=False)
        self.assertIn("N = 1,000", out)

    def test_includes_in_fenced_code_blocks_are_text(self):
        part, shown = self.tmp / "part.md", self.tmp / "shown.md"
        part.write_text("Included part.\n", encoding="utf-8")
        shown.write_text("Never included.\n", encoding="utf-8")
        fenced = (f"```md\n!include {shown}\n!include does/not/exist.md\n```\n\n"
                  f"~~~~\n!include {shown}\n~~~~\n")
        doc = self.tmp / "doc.md"
        doc.write_text(f"!include {part}\n\n{fenced}", encoding="utf-8")
        out = expand.expand_files([doc])
        self.assertTrue(out.startswith("Included part.\n") and out.endswith(fenced), out)
        self.assertEqual(build_graph.expand_includes([doc]), [doc, part])
        self.assertEqual(list(submission_check.includes(doc)), [part])

    def test_circular_include_fails(self):
        doc = self.tmp / "doc.md"
        doc.write_text(f"!include {doc}\n", encoding="utf-8")
        with self.assertRaises(RecursionError):
            expand.expand_files([doc])

    def test_fixture_document_uses_the_project_values(self):
        out = expand.expand_files(sorted((_support.ROOT / "manuscript/main").glob("*.md")))
        self.assertIn("We studied 120 patients", out)
        self.assertIn("{#tbl:example}", out)


if __name__ == "__main__":
    unittest.main()
