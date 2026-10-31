"""What trazo tells the reader to run for configured and built-in stages."""

import unittest
from pathlib import Path

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo loads)

from trazo.project import DEFAULTS, Project, merge


def load(extra: dict) -> Project:
    return Project(Path("."), merge(DEFAULTS, extra), True)


class CommandHintTests(unittest.TestCase):
    def test_built_in_stages_name_scripts_trazo(self):
        found = load({})
        self.assertEqual(found.command_hint("values"), "scripts/trazo build --prepare=inputs")
        self.assertEqual(found.command_hint("submission"), "scripts/trazo build --submission")
        self.assertEqual(load({"commands": {"values": ["python3", "v.py"]}}).command_hint("values"), "python3 v.py")

    def test_a_report_has_no_submission_files(self):
        self.assertEqual(load({"kind": "report"}).command_hint("submission"), "scripts/trazo build")


if __name__ == "__main__":
    unittest.main()
