from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import run as fuzz_run


class FuzzRunnerTests(unittest.TestCase):
    def test_scan_server_log_separates_fatal_errors_from_background_exits(self) -> None:
        log = "\n".join(
            [
                "[trazo] http://127.0.0.1:18765/",
                "[export] python3 -m trazo diagram export fig2_workflow: exit 1",
                "[build] python3 -m trazo build --doc manuscript: exit 0",
                "Traceback (most recent call last):",
                '127.0.0.1 - - [26/Nov/2026] "POST /__block HTTP/1.1" 500 -',
            ]
        )
        scan = fuzz_run.scan_server_log(log)
        self.assertEqual(len(scan.fatal_lines), 2)
        self.assertEqual(len(scan.nonzero_bg_job_lines), 1)
        self.assertIn("diagram export", scan.nonzero_bg_job_lines[0])

    def test_seed_hand_table_writes_valid_hand_spec(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = fuzz_run.seed_hand_table(Path(tmp))
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["schema_version"], 1)
            self.assertIn("body", data)
            self.assertTrue(len(data["columns"]) >= 2)

    def test_every_suite_points_to_an_existing_spec(self) -> None:
        for suite in fuzz_run.SUITES.values():
            self.assertTrue((fuzz_run.SPECS_DIR / suite.spec_file).is_file(), suite.spec_file)

    def test_free_port_returns_positive_port(self) -> None:
        port = fuzz_run.free_port()
        self.assertGreater(port, 0)


if __name__ == "__main__":
    unittest.main()
