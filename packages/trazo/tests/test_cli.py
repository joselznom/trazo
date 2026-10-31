"""The trazo command, citations and agent skills."""

import contextlib
import io
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _support  # noqa: F401  (sets TRAZO_ROOT first)

from trazo import agents, citations, cli
from trazo.project import DEFAULTS, SKILLS_DIR, Project, merge


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class CliTests(unittest.TestCase):
    def test_version_names_the_project(self):
        code, out, _ = run(["version"])
        self.assertEqual(code, 0)
        self.assertIn("project pins v0.0.0", out)

    def test_help_and_unknown_command(self):
        self.assertEqual(run(["--help"])[0], 0)
        code, _, err = run(["nope"])
        self.assertEqual(code, 2)
        self.assertIn("unknown command", err)

    def test_citations_pass_on_the_fixture(self):
        code, out, _ = run(["citations"])
        self.assertEqual(code, 0, out)
        self.assertIn("Used citation keys: 1", out)

    def test_missing_citation_fails(self):
        with patch.object(citations, "bibliography_keys", return_value=set()):
            code, out, _ = run(["citations"])
        self.assertEqual(code, 1)
        self.assertIn("smith2020", out)

    def test_citation_keys_follow_pandoc_syntax_and_skip_code(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "section.md").write_text(
                "Citations [@Smith.2020; @A/B; @A+B; @A?B; @_Private; @müller2019ångström; @2020x].\n\n"
                "`[@inline-example]`\n\n````md\n```\n[@fenced-example]\n```\n````\n", encoding="utf-8")
            bibliography = root / "references.bib"
            bibliography.write_text("\n".join(
                f"  @article {{ {key}, title = {{Example}} }}"
                for key in ("Smith.2020", "A/B", "A+B", "A?B", "_Private", "müller2019ångström", "2020x")),
                encoding="utf-8")
            project = Project(root, merge(DEFAULTS, {"documents": {"paper": {"path": "."}}}), True)
            with patch.object(citations, "PROJECT", project), \
                    patch.object(citations, "BIBLIOGRAPHY", bibliography):
                code, out, err = run(["citations"])
            self.assertEqual(code, 0, out + err)
            self.assertIn("Used citation keys: 7", out)

    def test_citations_reports_missing_inputs_without_a_traceback(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(citations, "PROJECT", Project(root, merge(DEFAULTS, {}), True)), \
                    patch.object(citations, "BIBLIOGRAPHY", root / "missing.bib"):
                code, _, err = run(["citations"])
            self.assertEqual(code, 1)
            self.assertIn("does not exist", err)
            self.assertNotIn("Traceback", err)

    def test_missing_citations_in_indented_list_continuations_fail(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "section.md").write_text(
                "- A list item.\n\n    A continuation cites [@missing-reference].\n")
            project = Project(root, merge(DEFAULTS, {"documents": {"paper": {"path": "."}}}), True)
            with patch.object(citations, "PROJECT", project):
                code, out, err = run(["citations"])
            self.assertEqual(code, 1, out + err)
            self.assertIn("missing-reference", out)

    def test_a_busy_preview_port_fails_in_one_line(self):
        with socket.socket() as held:
            held.bind(("127.0.0.1", 0))
            held.listen()
            port = held.getsockname()[1]
            result = subprocess.run([sys.executable, "-m", "trazo", "preview", "serve", "--port", str(port)],
                                    cwd=_support.ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr.count("\n"), 1, result.stderr)
        self.assertIn(f"port {port} is already in use", result.stderr)
        self.assertIn("--port", result.stderr)


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.own = self.tmp / "skills"
        (self.own / "critique").mkdir(parents=True)
        (self.own / "critique" / "SKILL.md").write_text("project critique\n")
        (self.own / "local-only").mkdir()
        (self.own / "local-only" / "SKILL.md").write_text("local\n")
        self.targets = [self.tmp / ".agents/skills", self.tmp / ".claude/skills"]
        project = Project(self.tmp, merge(DEFAULTS, {"paths": {"skills": "skills"}}), True)
        patcher = patch.object(agents, "PROJECT", project)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_project_skills_add_to_and_replace_trazo_skills(self):
        skills = agents.canonical_skills(agents.PROJECT)
        self.assertEqual(skills["critique"], self.own / "critique")
        self.assertEqual(skills["local-only"], self.own / "local-only")
        self.assertEqual(skills["diagram-authoring"], SKILLS_DIR / "diagram-authoring")

    def test_sync_then_check(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(agents.main([]), 0)
            self.assertEqual(agents.main(["--check"]), 0)
        copy = self.targets[1] / "critique" / "SKILL.md"
        self.assertEqual(copy.read_text(), "project critique\n")
        copy.write_text("edited by hand\n")
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(agents.main(["--check"]), 1)
        self.assertIn("out of sync", err.getvalue())

    def test_sync_removes_a_dropped_skill(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(agents.main([]), 0)
        shutil.rmtree(self.own / "local-only")
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(agents.main(["--check"]), 1)
        self.assertIn("stale generated skill", err.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(agents.main([]), 0)
            self.assertEqual(agents.main(["--check"]), 0)
        self.assertFalse((self.targets[0] / "local-only").exists())

    def test_sync_prunes_a_template_target_the_project_dropped(self):
        stale = self.tmp / ".claude/skills/retired"
        stale.mkdir(parents=True)
        (stale / "SKILL.md").write_text("retired\n")
        (stale / agents.MARKER_NAME).write_text("Generated.\n")
        (self.tmp / ".claude/skills/notes").mkdir()  # unmarked: the author's
        project = Project(self.tmp, merge(DEFAULTS, {"paths": {"skills": "skills", "skill_targets": [".agents/skills"]}}),
                          True)
        with patch.object(agents, "PROJECT", project):
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(agents.main(["--check"]), 1)
            self.assertIn("stale generated skill: .claude/skills/retired", err.getvalue())
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(agents.main([]), 0)
        self.assertFalse(stale.exists())
        self.assertTrue((self.tmp / ".claude/skills/notes").is_dir())

    def test_a_target_symlinked_to_another_is_one_folder(self):
        self.targets[0].mkdir(parents=True)
        self.targets[1].parent.mkdir()
        self.targets[1].symlink_to(self.targets[0], target_is_directory=True)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(agents.main([]), 0)
            self.assertEqual(agents.main(["--check"]), 0)

    def test_a_target_that_is_a_file(self):
        (self.tmp / ".claude").mkdir()
        (self.tmp / ".claude/skills").write_text("not a folder\n")
        dropped = Project(self.tmp, merge(DEFAULTS, {"paths": {"skills": "skills", "skill_targets": [".agents/skills"]}}),
                          True)
        with patch.object(agents, "PROJECT", dropped), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(agents.main([]), 0)  # a template target the project dropped: not its own
            self.assertEqual(agents.main(["--check"]), 0)
        listed = Project(self.tmp, merge(DEFAULTS, {"paths": {"skills": "skills"}}), True)
        with patch.object(agents, "PROJECT", listed), self.assertRaises(SystemExit) as raised:
            agents.main([])
        self.assertIn("is not a folder", str(raised.exception))

    def test_check_names_a_target_outside_the_project(self):
        outside = self.tmp.parent / f"{self.tmp.name}-outside"
        self.addCleanup(shutil.rmtree, outside, True)
        project = Project(self.tmp, merge(DEFAULTS, {"paths": {"skills": "skills", "skill_targets": [str(outside)]}}),
                          True)
        with patch.object(agents, "PROJECT", project), contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(agents.main(["--check"]), 1)
        self.assertIn(f"missing generated skill: ../{outside.name}/critique", err.getvalue())

    def test_refuses_a_skills_folder_its_copies_would_overwrite(self):
        (self.targets[1] / "local-only").mkdir(parents=True)
        (self.targets[1] / "local-only/SKILL.md").write_text("local\n")
        project = Project(self.tmp, merge(DEFAULTS, {"paths": {"skills": ".claude/skills"}}), True)
        with patch.object(agents, "PROJECT", project), self.assertRaises(SystemExit) as raised:
            agents.main([])
        self.assertIn("move one of them", str(raised.exception))
        self.assertTrue((self.targets[1] / "local-only/SKILL.md").is_file())


if __name__ == "__main__":
    unittest.main()
