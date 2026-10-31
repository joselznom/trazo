"""The cache of passes: what its fingerprint depends on, and how run.py uses it (never for a failure or a skip)."""

import contextlib
import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

import _path  # noqa: F401
from acceptance import registry, resultcache, versions
from acceptance.context import Shared
from acceptance.registry import Entry

RUN_PY = Path(__file__).resolve().parents[1] / "run.py"
spec = importlib.util.spec_from_file_location("acceptance_run", RUN_PY)
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)


META = {"commit": "abc", "command": "run.py", "dirty": False, "started": "now"}


def git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, check=True, capture_output=True)


class Repo(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.repo, self.cache = self.root / "repo", self.root / "cache"
        for name, text in {"plugin/a.txt": "a", "docs/b.md": "b", "scripts/acceptance/harness.py": "h",
                           "scripts/acceptance/tests/test_x.py": "t", "scripts/acceptance/README.md": "r", ".gitignore": "ignored/\n"}.items():
            self.write(name, text)
        git(self.repo, "init", "-q")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "init")
        self.entry = Entry("n1-fake", "n1", "A fake", "new", ("plugin/**",))

    def write(self, name, text):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def fingerprint(self, entry=None):
        return resultcache.Fingerprints(self.repo).of(entry or self.entry)


class FingerprintTests(Repo):
    def test_the_same_content_is_the_same_fingerprint(self):
        first = self.fingerprint()
        git(self.repo, "commit", "-qm", "again", "--allow-empty")  # a new commit is not new content
        self.assertEqual(self.fingerprint(), first)

    def test_a_file_in_the_areas_changes_it_whether_edited_or_new(self):
        first = self.fingerprint()
        self.write("plugin/a.txt", "edited")
        edited = self.fingerprint()
        self.assertNotEqual(edited, first)
        self.write("plugin/new.txt", "untracked")
        self.assertNotEqual(self.fingerprint(), edited)

    def test_a_deleted_file_changes_it(self):
        first = self.fingerprint()
        (self.repo / "plugin/a.txt").unlink()
        self.assertNotEqual(self.fingerprint(), first)

    def test_files_outside_the_areas_and_ignored_files_do_not(self):
        first = self.fingerprint()
        self.write("docs/b.md", "other")
        self.write("ignored/plugin.txt", "x")
        self.assertEqual(self.fingerprint(), first)

    def test_an_ignored_file_inside_an_area_does_not(self):
        self.write(".gitignore", "plugin/secret.txt\n")
        first = self.fingerprint()
        self.write("plugin/secret.txt", "x")
        self.assertEqual(self.fingerprint(), first)

    def test_the_harness_code_changes_it_but_its_tests_and_docs_do_not(self):
        first = self.fingerprint()
        self.write("scripts/acceptance/tests/test_x.py", "t2")
        self.write("scripts/acceptance/README.md", "r2")
        self.assertEqual(self.fingerprint(), first)
        self.write("scripts/acceptance/harness.py", "h2")
        self.assertNotEqual(self.fingerprint(), first)

    def test_a_new_release_tag_changes_it(self):
        first = self.fingerprint()
        git(self.repo, "tag", "v0.1.0")
        self.assertNotEqual(self.fingerprint(), first)

    def test_a_scenario_that_runs_the_checkout_plugin_depends_on_the_whole_checkout(self):
        plain = Entry("n1-plain", "n1", "t", "new", ("plugin/**",))
        runs_plugin = Entry("n1-runs", "n1", "t", "new", ("plugin/**",), uses_checkout=True)
        before = self.fingerprint(runs_plugin), self.fingerprint(plain)
        self.write("other/forgotten.txt", "an input the catalog forgot")
        self.assertNotEqual(self.fingerprint(runs_plugin), before[0])
        self.assertEqual(self.fingerprint(plain), before[1])

    def test_docs_do_not_change_the_checkout_fingerprint_but_what_the_plugin_reads_does(self):
        entry = Entry("n1-runs", "n1", "t", "new", ("plugin/**",), uses_checkout=True)
        read = ("packages/trazo/src/trazo/skills/x/SKILL.md", "template/AGENTS.md.jinja", "packages/trazo/src/trazo/mcp.py",
                "packages/trazo/README.md", "packages/trazo/src/trazo/assets/template/AGENTS.md")
        for name in ("docs/x.md", "docs/deep/y.md", "README.md", "CHANGELOG.md", "AGENTS.md", "CLAUDE.md", "CODING_STANDARDS.md",
                     "REVIEW.md", "scripts/acceptance/README.md", "scripts/acceptance/scenarios.md"):
            first = self.fingerprint(entry)
            self.write(name, "edited")
            self.assertEqual(self.fingerprint(entry), first, name)
        for name in read:
            first = self.fingerprint(entry)
            self.write(name, "one")
            added = self.fingerprint(entry)
            self.write(name, "two")
            self.assertNotEqual(added, first, name)
            self.assertNotEqual(self.fingerprint(entry), added, name)

    def test_the_checkout_dependence_matches_the_plugin_folders_content(self):
        entry = Entry("n1-runs", "n1", "t", "new", ("plugin/**",), uses_checkout=True)
        for name, text in (("other/c.txt", "edited"), ("packages/trazo/src/trazo/mcp.py", "raise SystemExit"), ("new/file.txt", "x")):
            key, first = versions.content_key(self.repo, versions.checkout_files(self.repo), 1), self.fingerprint(entry)
            self.write(name, text)
            self.assertNotEqual(versions.content_key(self.repo, versions.checkout_files(self.repo), 1), key, name)
            self.assertNotEqual(self.fingerprint(entry), first, name)

    def test_a_change_to_a_package_source_misses_the_cache_of_the_real_chain_scenario(self):
        chain = next(e for e in registry.load_catalog() if e.id == "n1-sandbox-chain")
        self.write("packages/trazo/src/trazo/mcp.py", "print('ok')\n")
        git(self.repo, "add", "-A")
        first = self.fingerprint(chain)
        self.write("packages/trazo/src/trazo/mcp.py", "raise SystemExit(1)\n")
        self.assertNotEqual(self.fingerprint(chain), first)

    def test_the_seed_pins_change_it_even_when_the_areas_leave_them_out(self):
        entry = Entry("n1-fake", "n1", "t", "new", ("docs/**",))
        self.write("plugin/scripts/setup.sh", "UV_VERSION=0.1.0\n")
        self.write("packages/trazo/src/trazo/tool_setup.py", 'PANDOC_VERSION = "3.1"\n')
        first = self.fingerprint(entry)
        self.write("packages/trazo/src/trazo/tool_setup.py", 'PANDOC_VERSION = "3.2"\n')
        self.assertNotEqual(self.fingerprint(entry), first)

    def test_each_scenario_has_its_own_fingerprint(self):
        other = Entry("n1-other", "n1", "t", "new", ("plugin/**",))
        self.assertNotEqual(self.fingerprint(other), self.fingerprint())


class StoreTests(Repo):
    def test_a_stored_pass_is_found_by_its_fingerprint_only(self):
        resultcache.store(self.cache, "n1-fake", "f" * 64, {"commit": "abc"})
        self.assertEqual(resultcache.lookup(self.cache, "n1-fake", "f" * 64)["commit"], "abc")
        self.assertIsNone(resultcache.lookup(self.cache, "n1-fake", "e" * 64))
        self.assertIsNone(resultcache.lookup(self.cache, "n1-other", "f" * 64))

    def test_a_damaged_entry_is_a_miss(self):
        resultcache.store(self.cache, "n1-fake", "f" * 64, {})
        (self.cache / "results/n1-fake" / ("f" * 40 + ".json")).write_text("{not json")
        self.assertIsNone(resultcache.lookup(self.cache, "n1-fake", "f" * 64))

    def test_old_entries_are_dropped(self):
        for n in range(resultcache.KEEP_PER_SCENARIO + 3):
            resultcache.store(self.cache, "n1-fake", f"{n:02x}".ljust(64, "0"), {})
        self.assertEqual(len(list((self.cache / "results/n1-fake").glob("*.json"))), resultcache.KEEP_PER_SCENARIO)

    def test_the_cache_follows_the_guards_rules(self):
        with mock.patch.object(resultcache.versions, "check_cache", side_effect=RuntimeError("refused")):
            with self.assertRaises(RuntimeError):
                resultcache.store(self.cache, "n1-fake", "f" * 64, {})
        self.assertFalse(self.cache.exists())


class Scenarios(Repo):
    """Scenarios that count their calls, run through run.run_selected."""

    def setUp(self):
        super().setUp()
        self.calls, self.outcome = {}, {}
        self.shared = Shared(self.cache, False, repo=self.repo)

    def entry_for(self, id_, level="n1"):
        entry = Entry(id_, level, "A fake", "new", ("plugin/**",))

        def function(ctx):
            self.calls[id_] = self.calls.get(id_, 0) + 1
            outcome = self.outcome.get(id_, ["pass"])
            what = outcome[min(self.calls[id_], len(outcome)) - 1]
            if what == "fail":
                raise AssertionError("it broke")
            if what == "skip":
                raise registry.Skip("no login")
            if what.startswith("limit"):
                raise AssertionError("claude session did not succeed: You've hit your usage limit")
        patcher = mock.patch.dict(registry.IMPLEMENTATIONS, {id_: registry.Implementation(function, ())})
        patcher.start()
        self.addCleanup(patcher.stop)
        return entry

    def go(self, entries, **options):
        args = Namespace(auto=None, no_cache=False, keep=False, online=False, **options)
        folder = self.root / f"report-{len(list(self.root.glob('report-*')))}"
        with contextlib.redirect_stdout(io.StringIO()):
            code = run.run_selected(entries, args, self.shared, META, folder)
        return code, json.loads((folder / "report.json").read_text())["results"]


class RunTests(Scenarios):
    def test_a_pass_is_not_run_again_while_nothing_changed(self):
        entry = self.entry_for("n1-a")
        self.assertEqual(self.go([entry])[0], 0)
        code, results = self.go([entry])
        self.assertEqual((self.calls["n1-a"], code, results[0]["status"], results[0]["cached"]), (1, 0, "passed", True))

    def test_editing_a_file_in_its_areas_runs_it_again(self):
        entry = self.entry_for("n1-a")
        self.go([entry])
        self.write("plugin/a.txt", "changed")
        _, results = self.go([entry])
        self.assertEqual((self.calls["n1-a"], results[0]["cached"]), (2, False))

    def test_editing_the_harness_runs_it_again(self):
        entry = self.entry_for("n1-a")
        self.go([entry])
        self.write("scripts/acceptance/harness.py", "changed")
        self.go([entry])
        self.assertEqual(self.calls["n1-a"], 2)

    def test_no_cache_forces_a_run(self):
        entry = self.entry_for("n1-a")
        self.go([entry])
        args = Namespace(auto=None, no_cache=True, keep=False, online=False)
        with contextlib.redirect_stdout(io.StringIO()):
            run.run_selected([entry], args, self.shared, META, self.root / "forced")
        self.assertEqual(self.calls["n1-a"], 2)

    def test_a_failure_is_never_cached(self):
        entry = self.entry_for("n1-a")
        self.outcome["n1-a"] = ["fail"]
        for _ in range(2):
            code, results = self.go([entry])
            self.assertEqual((code, results[0]["status"]), (1, "failed"))
        self.assertEqual(self.calls["n1-a"], 2)
        self.assertFalse((self.cache / "results/n1-a").exists())

    def test_a_skip_is_never_cached_and_does_not_fail_a_manual_run(self):
        entry = self.entry_for("n1-a")
        self.outcome["n1-a"] = ["skip"]
        for _ in range(2):
            code, results = self.go([entry])
            self.assertEqual((code, results[0]["status"]), (0, "skipped"))
        self.assertEqual(self.calls["n1-a"], 2)

    def test_the_report_marks_cached_passes(self):
        entry = self.entry_for("n1-a")
        self.go([entry])
        folder = self.root / "marked"
        args = Namespace(auto=None, no_cache=False, keep=False, online=False)
        with contextlib.redirect_stdout(io.StringIO()):
            run.run_selected([entry], args, self.shared, META, folder)
        text = (folder / "report.md").read_text()
        self.assertIn("| n1-a | passed (cached) |", text)
        self.assertIn("1 passed, 0 failed, 0 skipped, 0 not automated (1 cached)", text)

    def test_the_seed_is_not_needed_when_everything_is_cached(self):
        entry = self.entry_for("n1-a")
        self.go([entry])
        with mock.patch.dict(registry.IMPLEMENTATIONS, {"n1-a": registry.Implementation(lambda ctx: None, ("seed",))}):
            code, results = self.go([entry])
        self.assertEqual((code, results[0]["status"]), (0, "passed"))


class AutomaticRunTests(Scenarios):
    def go(self, entries, auto="push", **options):
        args = Namespace(auto=auto, no_cache=False, keep=False, online=False, **options)
        folder = self.root / f"report-{len(list(self.root.glob('report-*')))}"
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = run.run_selected(entries, args, self.shared, META, folder)
        self.out = out.getvalue()
        return code, json.loads((folder / "report.json").read_text())["results"]

    def test_a_skip_is_listed_in_a_push_blocks_a_tag_and_is_tried_again(self):
        entry = self.entry_for("n1-a")
        self.outcome["n1-a"] = ["skip", "skip"]
        code, results = self.go([entry])
        self.assertEqual((code, results[0]["status"]), (0, "skipped"))
        self.assertIn("Not proven", self.out)
        self.assertIn("To proceed", self.out)
        code, results = self.go([entry], auto="tag")  # never cached: the release tag runs it again
        self.assertEqual((code, results[0]["status"], self.calls["n1-a"]), (1, "skipped", 2))
        self.assertIn("Blocked", self.out)

    def test_a_flaky_n1_scenario_is_run_once_more_and_the_pass_says_so(self):
        entry = self.entry_for("n1-a")
        self.outcome["n1-a"] = ["fail", "pass"]
        code, results = self.go([entry])
        self.assertEqual((code, self.calls["n1-a"], results[0]["status"], results[0]["retried"]), (0, 2, "passed", True))
        self.assertIn("passed on retry", self.out)
        self.assertIn("first attempt failed: it broke", results[0]["reason"])

    def test_a_scenario_that_fails_twice_fails(self):
        entry = self.entry_for("n2-a", "n2")
        self.outcome["n2-a"] = ["fail", "fail"]
        code, results = self.go([entry])
        self.assertEqual((code, self.calls["n2-a"], results[0]["status"]), (1, 2, "failed"))
        self.assertIn("failed on retry too", results[0]["reason"])

    def test_a_usage_limit_is_a_skip_with_its_reason(self):
        entry = self.entry_for("n2-a", "n2")
        self.outcome["n2-a"] = ["limit"]
        code, results = self.go([entry])
        self.assertEqual((code, results[0]["status"], self.calls["n2-a"]), (0, "skipped", 1))
        self.assertIn("usage limit reached", results[0]["reason"])
        self.assertIn("wait for the provider's limit to reset", self.out)

if __name__ == "__main__":
    unittest.main()
