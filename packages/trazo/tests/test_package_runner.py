"""scripts/test-package.py (`make test-package`) runs each test module in a process of its
own: a failing test fails the run, and no test that `unittest discover` runs is left out."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

RUNNER = Path(__file__).resolve().parents[3] / "scripts/test-package.py"

# Each test leaves a file named after itself, so a test can tell which ones ran.
MODULE = """import unittest
from pathlib import Path


class Sample(unittest.TestCase):
    def setUp(self):
        Path(__file__).with_name(self.id() + ".ran").touch()
"""


class PackageRunnerTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.package = Path(tmp.name).resolve()
        self.tests = self.package / "tests"
        self.tests.mkdir()

    def module(self, path: str, *tests: str) -> None:
        file = self.tests / path
        file.parent.mkdir(exist_ok=True)
        file.write_text(MODULE + "".join(f"\n    def {test}\n" for test in tests), encoding="utf-8")

    def run_tests(self, *options: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(RUNNER), str(self.package), "--jobs", "2", *options],
                              capture_output=True, text=True, encoding="utf-8", timeout=120)

    def ran(self) -> list[str]:
        return sorted(path.stem for path in self.tests.rglob("*.ran"))

    def test_every_module_runs_and_the_tests_are_counted(self):
        self.module("test_a.py", "test_one(self): pass", "test_two(self): pass")
        self.module("test_b.py", "test_three(self): self.skipTest('not here')")
        result = self.run_tests()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.ran(), ["test_a.Sample.test_one", "test_a.Sample.test_two", "test_b.Sample.test_three"])
        self.assertRegex(result.stdout, r"Ran 3 tests in 2 modules .*skipped=1")

    def test_a_failing_test_fails_the_run_and_shows_its_report_while_the_rest_run(self):
        self.module("test_a.py", "test_broken(self): self.assertEqual(1, 2)")
        self.module("test_b.py", "test_fine(self): pass")
        result = self.run_tests()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        # unittest's own report, which scripts/ci-failures.py reads from the CI logs.
        self.assertIn("FAIL: test_broken (test_a.Sample.test_broken)", result.stdout)
        self.assertIn("AssertionError: 1 != 2", result.stdout)
        self.assertIn("test_b.Sample.test_fine", self.ran())

    def test_the_shards_of_a_run_share_out_every_module_once(self):
        for name in "abc":
            self.module(f"test_{name}.py", "test_one(self): pass")
        parts = []
        for shard in ("1/2", "2/2"):
            result = self.run_tests("--shard", shard)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            parts.append(self.ran())
            for mark in self.tests.glob("*.ran"):
                mark.unlink()
        self.assertEqual(sorted(parts[0] + parts[1]), [f"test_{name}.Sample.test_one" for name in "abc"])
        self.assertTrue(parts[0] and parts[1], parts)

    def test_a_test_the_runner_would_leave_out_stops_the_run(self):
        self.module("test_a.py", "test_one(self): pass")
        self.module("more/test_inner.py", "test_inner(self): pass")  # discover runs it; no tests/test*.py holds it
        (self.tests / "more" / "__init__.py").touch()
        result = self.run_tests()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertRegex(result.stderr, r"discover finds 2 tests.* hold 1\b")
        self.assertEqual(self.ran(), [])


if __name__ == "__main__":
    unittest.main()
