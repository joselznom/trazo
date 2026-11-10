"""A spec table's Markdown re-saved by hand in another encoding: checked and rebuilt, never a crash."""

import json
import unittest

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)
from test_table import Restore, run

from trazo import table


class ReencodedOutputTests(Restore):
    def reencoded(self, label: str):
        self.assertEqual(run(["render", label])[0], 0)
        path = table.output_path(label)
        path.write_bytes(path.read_bytes() + "Café, saved as Latin-1.\n".encode("latin-1"))
        return path

    def test_check_reports_it_as_out_of_date(self):
        self.reencoded("results")
        code, out, err = run(["check", "results", "--outputs", "--json"])
        self.assertEqual(code, 1, out + err)
        self.assertIn("stale-output", [issue["code"] for issue in json.loads(out)["issues"]])

    def test_the_next_render_rebuilds_it_from_its_spec(self):
        path = self.reencoded("results")
        rendered = table.render_one("results")
        self.assertIsNotNone(rendered.markdown, rendered.errors)
        self.assertEqual(path.read_bytes(), rendered.markdown.encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
