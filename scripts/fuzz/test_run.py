from __future__ import annotations

import contextlib
import io
import json
import shlex
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import run as fuzz_run


def _write_fake_bombadil(directory: Path, body: str) -> Path:
    script = directory / "bombadil"
    cmd = f"exec {shlex.quote(sys.executable)} -c {shlex.quote(body)}"
    script.write_text(f"#!/bin/sh\n{cmd}\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


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

    def test_main_refuses_nonempty_output_dir_without_deleting_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_bin = _write_fake_bombadil(root, "import sys; sys.exit(0)")
            out_dir = root / "out"
            keep = out_dir / "preview" / "paper" / "keep.txt"
            keep.parent.mkdir(parents=True)
            keep.write_text("keep", encoding="utf-8")
            stderr = io.StringIO()
            with (
                mock.patch.object(fuzz_run, "BOMBADIL_BIN", fake_bin),
                contextlib.redirect_stderr(stderr),
            ):
                rc = fuzz_run.main(["--suite", "preview", "--output-dir", str(out_dir)])
            self.assertEqual(rc, 2)
            self.assertIn("not empty", stderr.getvalue())
            self.assertEqual(keep.read_text(encoding="utf-8"), "keep")

    def test_summarize_trace_keeps_diagnostic_when_bombadil_trace_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trace = root / "trace.bin"
            trace.write_bytes(b"corrupt")
            fake_bin = _write_fake_bombadil(
                root,
                "import sys; sys.stderr.write('not a bombadil trace\\n'); sys.exit(1)",
            )
            with mock.patch.object(fuzz_run, "BOMBADIL_BIN", fake_bin):
                summary = fuzz_run.summarize_trace(trace, root / "coverage.jsonl")
            self.assertEqual(summary["states"], 0)
            self.assertIn("exited 1", summary["trace_error"])
            self.assertIn("not a bombadil trace", summary["trace_error"])

    def test_summarize_trace_rejects_empty_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trace = root / "trace.bin"
            trace.write_bytes(b"")
            fake_bin = _write_fake_bombadil(root, "import sys; sys.exit(0)")
            with mock.patch.object(fuzz_run, "BOMBADIL_BIN", fake_bin):
                summary = fuzz_run.summarize_trace(trace, root / "coverage.jsonl")
            self.assertEqual(summary["states"], 0)
            self.assertEqual(summary["trace_error"], "trace contained 0 states")


if __name__ == "__main__":
    unittest.main()
