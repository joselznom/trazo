"""The guard refuses the real home, data folder and CLI configuration, and anything not in the sandbox."""

import tempfile
import unittest
from pathlib import Path

import _path  # noqa: F401  (puts scripts/ on sys.path)
from acceptance import guard


class GuardTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name).resolve() / "real home"
        self.root = Path(tmp.name).resolve() / "sandbox"
        for folder in (self.home, self.root / "home"):
            folder.mkdir(parents=True)

    def env(self, **extra):
        return {"PATH": "/usr/bin:/bin", "HOME": str(self.root / "home"), "TMPDIR": str(self.root / "tmp"),
                "CLAUDE_CONFIG_DIR": str(self.root / "claude"), "CODEX_HOME": str(self.root / "codex"),
                "TRAZO_DATA_DIR": str(self.root / "home/Library/Application Support/trazo"), **extra}

    def refused(self, **extra):
        with self.assertRaises(guard.GuardError, msg=extra):
            guard.check_env(self.env(**extra), self.root, self.home)

    def test_a_sandbox_environment_passes(self):
        guard.check_env(self.env(), self.root, self.home)

    def test_the_real_locations_are_refused(self):
        self.refused(HOME=str(self.home))
        self.refused(CLAUDE_CONFIG_DIR=str(self.home / ".claude"))
        self.refused(CLAUDE_CONFIG_DIR=str(self.home / ".claude-personal"))  # any .claude*
        self.refused(CODEX_HOME=str(self.home / ".codex"))
        self.refused(TRAZO_DATA_DIR=str(self.home / "Library/Application Support/trazo"))
        self.refused(TRAZO_DATA_DIR=str(self.home / "Library/Application Support/trazo/venv"))

    def test_a_folder_that_contains_the_real_home_is_refused(self):
        guard.check_path(self.home.parent / "elsewhere", "x", self.home)
        with self.assertRaises(guard.GuardError):
            guard.check_path(self.home.parent, "x", self.home)

    def test_a_sandbox_inside_the_home_is_fine_unless_it_is_in_a_protected_folder(self):
        guard.check_path(self.home / "projects/sandbox", "x", self.home)
        with self.assertRaises(guard.GuardError):
            guard.check_path(self.home / ".claude/sandbox", "x", self.home)

    def test_symlinks_are_followed(self):
        (self.home / ".codex").mkdir()
        link = self.root / "innocent"
        link.symlink_to(self.home / ".codex")
        with self.assertRaises(guard.GuardError):
            guard.check_path(link, "x", self.home)

    def test_variables_outside_the_sandbox_are_refused(self):
        self.refused(TMPDIR="/tmp")
        self.refused(TRAZO_DATA_DIR="relative/path")

    def test_a_missing_home_is_refused(self):
        env = self.env()
        del env["HOME"]
        with self.assertRaises(guard.GuardError):
            guard.check_env(env, self.root, self.home)

    def test_any_value_that_names_a_protected_path_is_refused(self):
        self.refused(PATH=f"/usr/bin:{self.home}/.codex/bin")
        self.refused(SOMETHING=f"--config={self.home}/.claude.json")


if __name__ == "__main__":
    unittest.main()
