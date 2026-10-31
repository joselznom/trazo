"""The catalog, glob matching and the --changed-from selection."""

import re
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

import _path  # noqa: F401
from acceptance import registry
from acceptance.registry import Entry

REPO = Path(__file__).resolve().parents[3]


def entry(id_, *areas, level="n1"):
    return Entry(id_, level, "title", "new", tuple(areas))


class GlobTests(unittest.TestCase):
    def matches(self, pattern, path):
        return bool(registry.glob_regex(pattern).fullmatch(path))

    def test_star_stays_in_a_folder_and_double_star_does_not(self):
        self.assertTrue(self.matches("packages/trazo/src/trazo/mcp*.py", "packages/trazo/src/trazo/mcp_preview.py"))
        self.assertTrue(self.matches("packages/trazo/src/trazo/mcp*.py", "packages/trazo/src/trazo/mcp.py"))
        self.assertFalse(self.matches("packages/trazo/src/trazo/mcp*.py", "packages/trazo/src/trazo/x/mcp_a.py"))
        self.assertTrue(self.matches("plugin/**", "plugin/scripts/setup.sh"))
        self.assertTrue(self.matches("plugin/**", "plugin/mcp.sh"))
        self.assertFalse(self.matches("plugin/**", "plugin"))
        self.assertFalse(self.matches("plugin/**", "plugins/x"))
        self.assertTrue(self.matches("**/SKILL.md", "a/b/SKILL.md"))
        self.assertTrue(self.matches("**/SKILL.md", "SKILL.md"))

    def test_dots_and_other_characters_are_literal(self):
        self.assertTrue(self.matches(".codex-mcp.json", ".codex-mcp.json"))
        self.assertFalse(self.matches(".codex-mcp.json", "xcodex-mcp.json"))


class CatalogAreasTests(unittest.TestCase):
    """The real catalog: which scenarios a change selects."""
    entries = registry.load_catalog()

    def ids(self, *changed, level=None):
        return [e.id for e in registry.select(self.entries, [level] if level else (), changed=list(changed))]

    def test_a_package_source_selects_the_scenarios_that_run_the_checkout_plugin(self):
        chosen = self.ids("packages/trazo/src/trazo/mcp.py")
        self.assertIn("n1-sandbox-chain", chosen)  # it installs and runs the checkout's MCP server
        self.assertIn("n2-install-claude", chosen)
        self.assertNotIn("n1-sandbox", chosen)  # the harness self-check never runs the plugin

    def test_a_change_outside_every_area_selects_nothing(self):
        self.assertEqual(self.ids("docs/x.md"), [])

    def test_the_word_build_selects_n1_and_n2(self):
        chosen = self.ids("packages/trazo/src/trazo/crossref.py")
        self.assertTrue({"n1-sandbox-chain", "n2-install-codex"} <= set(chosen))

    def test_the_inputs_of_setup_and_the_stamping_select_them_too(self):
        for path in ("plugin/scripts/setup.sh", "packages/trazo/pyproject.toml", "packages/trazo/uv.lock", "copier.yml",
                     ".codex-mcp.json", "packages/trazo/src/trazo/assets/template/index.json"):
            with self.subTest(path):
                self.assertIn("n1-sandbox-chain", self.ids(path))

    def test_a_scenario_that_runs_the_plugin_covers_the_shared_list(self):
        shared = tomllib.loads(registry.CATALOG.read_text(encoding="utf-8"))["checkout_areas"]
        for e in self.entries:
            if e.uses_checkout:
                self.assertTrue(set(shared) <= set(e.areas), e.id)
        self.assertFalse(next(e for e in self.entries if e.id == "n1-sandbox").uses_checkout)

    def test_a_missing_area_list_is_an_error(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "catalog.toml"
            path.write_text('[[scenario]]\nid = "n1-x"\nlevel = "n1"\ntitle = "t"\nsource = "new"\n')
            with self.assertRaisesRegex(registry.CatalogError, "areas"):
                registry.load_catalog(path)


class SelectionTests(unittest.TestCase):
    entries = [entry("n1-a", "plugin/**"), entry("n1-b", "hooks/**", "docs/x.md"),
               entry("n2-c", "plugin/**", level="n2"), entry("n2-d", "a/*.py", level="n2")]

    def ids(self, **filters):
        return [e.id for e in registry.select(self.entries, **filters)]

    def test_no_filter_selects_everything(self):
        self.assertEqual(self.ids(), ["n1-a", "n1-b", "n2-c", "n2-d"])

    def test_filters_combine(self):
        self.assertEqual(self.ids(levels=["n1"]), ["n1-a", "n1-b"])
        self.assertEqual(self.ids(patterns=["n?-[ac]", "n2-d*"]), ["n1-a", "n2-c", "n2-d"])
        self.assertEqual(self.ids(levels=["n2"], changed=["plugin/mcp.sh"]), ["n2-c"])

    def test_changed_files_select_by_area(self):
        self.assertEqual(self.ids(changed=["plugin/mcp.sh"]), ["n1-a", "n2-c"])
        self.assertEqual(self.ids(changed=["docs/x.md", "a/b/c.py"]), ["n1-b"])
        self.assertEqual(self.ids(changed=["README.md"]), [])
        self.assertEqual(self.ids(changed=[]), [])


class ChangedFilesTests(unittest.TestCase):
    def test_committed_uncommitted_and_untracked_changes_count_but_ignored_files_do_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)

            def git(*args):
                subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                                *args], cwd=repo, check=True, capture_output=True)
            git("init", "-q")
            for name in ("old.txt", "edited.txt", "committed.txt", "gone.txt"):
                (repo / name).write_text("1")
            git("add", "-A")
            git("commit", "-qm", "base")
            git("tag", "base")
            (repo / "committed.txt").write_text("2")
            git("commit", "-qam", "later")
            (repo / "edited.txt").write_text("2")
            (repo / "gone.txt").unlink()
            (repo / "new.txt").write_text("x")
            (repo / ".gitignore").write_text("ignored.txt\n")
            (repo / "ignored.txt").write_text("x")
            self.assertEqual(registry.changed_files("base", repo),
                             [".gitignore", "committed.txt", "edited.txt", "gone.txt", "new.txt"])
            self.assertEqual(registry.changed_files("HEAD", repo), [".gitignore", "edited.txt", "gone.txt", "new.txt"])
            with self.assertRaises(registry.CatalogError):
                registry.changed_files("no-such-ref", repo)


class CatalogTests(unittest.TestCase):
    def test_the_catalog_names_only_checks_that_exist(self):
        entries = registry.load_catalog()
        text = (REPO / "docs/plugin.md").read_text()
        section = text.split("## Checks by hand", 1)[1]
        bullets = re.split(r"^- ", section, flags=re.M)[1:]
        numbers = {int(n) for e in entries for n in re.findall(r"check (\d+)$", e.source)}
        self.assertEqual(numbers, set(range(1, len(bullets) + 1)), "every published check needs a scenario")
        for found in entries:
            match = re.fullmatch(r"docs/plugin.md check (\d+)", found.source)
            if match:
                self.assertIn(f"`{found.id}`", bullets[int(match[1]) - 1], found.source)

    def test_the_viewer_assets_select_the_preview_and_update_scenarios(self):
        entries = registry.load_catalog()
        for changed in ("packages/trazo/src/trazo/assets/preview/preview.js",
                        "packages/trazo/src/trazo/assets/preview/preview-updates.js"):
            ids = [e.id for e in registry.select(entries, changed=[changed])]
            for wanted in ("n1-preview-update", "n1-port-busy", "n1-two-sessions-share-preview"):
                self.assertIn(wanted, ids, changed)

    def test_every_area_still_matches_a_file_of_the_repository(self):
        # A moved or renamed path would otherwise stop selecting its scenarios without a sign.
        listed = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True, text=True, check=True)
        files = [name for name in listed.stdout.split("\0") if name]
        for found in registry.load_catalog():
            for area in found.areas:
                self.assertTrue(any(registry.glob_regex(area).fullmatch(name) for name in files),
                                f"{found.id}: {area} matches no tracked file")

    def test_a_bad_entry_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "catalog.toml"
            for body in ('[[scenario]]\nid="n1-a"\nlevel="n9"\ntitle="t"\nsource="new"\nareas=["a"]\n',
                         '[[scenario]]\nid="n2-a"\nlevel="n1"\ntitle="t"\nsource="new"\nareas=["a"]\n',
                         '[[scenario]]\nid="n1-a"\nlevel="n1"\ntitle="t"\nsource="new"\nareas=[]\n',
                         '[[scenario]]\nid="n1-a"\nlevel="n1"\ntitle="t"\nareas=["a"]\n',
                         '[[scenario]]\nid="n1-a"\nlevel="n1"\ntitle="t"\nsource="new"\nareas=["a"]\n' * 2):
                path.write_text(body)
                with self.assertRaises(registry.CatalogError, msg=body):
                    registry.load_catalog(path)

    def test_a_scenario_needs_a_known_requirement(self):
        with self.assertRaises(registry.CatalogError):
            registry.scenario("n1-x", needs=["internet"])


if __name__ == "__main__":
    unittest.main()
