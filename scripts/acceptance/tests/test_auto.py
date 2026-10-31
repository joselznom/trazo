"""--auto: the base a branch is compared with, the previous release, the selection per mode and the advice on a block."""

import contextlib
import importlib.util
import io
import subprocess
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

import _path  # noqa: F401
from acceptance import auto, limits, registry, report
from acceptance.context import Shared
from acceptance.registry import Entry

spec = importlib.util.spec_from_file_location("acceptance_run_auto", Path(__file__).resolve().parents[1] / "run.py")
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)


def git(repo, *args):
    done = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, check=True, capture_output=True, text=True)
    return done.stdout.strip()


class GitCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "develop")
        self.commit("a.txt", "a")

    def commit(self, name, text, repo=None):
        repo = repo or self.repo
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", f"add {name}")
        return git(repo, "rev-parse", "HEAD")


class BaseTests(GitCase):
    def test_the_base_is_where_the_branch_left_develop(self):
        start = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "switch", "-q", "-c", "feature")
        self.commit("b.txt", "b")
        self.assertEqual(auto.base_ref(self.repo), start)
        self.assertEqual(registry.changed_files(auto.base_ref(self.repo), self.repo), ["b.txt"])

    def test_uncommitted_and_untracked_files_count(self):
        (self.repo / "a.txt").write_text("edited")
        (self.repo / "new.txt").write_text("new")
        self.assertEqual(registry.changed_files(auto.base_ref(self.repo), self.repo), ["a.txt", "new.txt"])

    def test_a_file_renamed_out_of_a_folder_lists_both_paths(self):
        self.commit("plugin/session-start.sh", "#!/bin/sh\necho start\n")
        base = git(self.repo, "rev-parse", "HEAD")
        (self.repo / "docs").mkdir()
        git(self.repo, "mv", "plugin/session-start.sh", "docs/disabled.sh")
        self.assertEqual(registry.changed_files(base, self.repo), ["docs/disabled.sh", "plugin/session-start.sh"])
        entry = Entry("n1-a", "n1", "t", "new", ("plugin/**",))
        self.assertEqual(auto.choose([entry], registry.changed_files(base, self.repo)), [entry])

    def test_origin_develop_wins_over_a_develop_that_moved_on(self):
        clone = self.root / "clone"
        git(self.root, "clone", "-q", str(self.repo), str(clone))
        origin_develop = git(clone, "rev-parse", "origin/develop")
        self.commit("local-develop.txt", "x", clone)  # the clone's own develop moved ahead of origin/develop
        git(clone, "switch", "-q", "-c", "feature")
        self.assertEqual(auto.base_ref(clone), origin_develop)

    def test_develop_wins_over_a_main_that_lags_behind(self):
        git(self.repo, "branch", "main")
        work = self.commit("unreleased.txt", "x")  # on develop, not yet released to main
        git(self.repo, "switch", "-q", "-c", "feature")
        self.commit("b.txt", "b")
        self.assertEqual(auto.base_ref(self.repo), work)

    def test_a_clone_with_main_only_compares_with_main(self):
        git(self.repo, "branch", "-m", "main")
        start = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "switch", "-q", "-c", "feature")
        self.commit("b.txt", "b")
        self.assertEqual(auto.base_ref(self.repo), start)

    def test_without_develop_or_main_it_says_what_to_do(self):
        git(self.repo, "branch", "-m", "trunk")
        with self.assertRaisesRegex(registry.CatalogError, "--changed-from"):
            auto.base_ref(self.repo)


class TagTests(GitCase):
    def setUp(self):
        super().setUp()
        for tag in ("v0.1.0", "v0.2.0", "v0.10.0", "v1.0.0-rc1", "nightly"):
            git(self.repo, "tag", tag)

    def test_the_previous_release_is_the_highest_below_by_number(self):
        self.assertEqual(auto.previous_tag("v0.10.0", self.repo), "v0.2.0")  # not "v0.1.0" or "v1.0.0-rc1" by text order
        self.assertEqual(auto.previous_tag("v99.0.0", self.repo), "v0.10.0")  # the tag need not exist yet
        self.assertIsNone(auto.previous_tag("v0.1.0", self.repo))

    def test_only_release_tags_are_accepted(self):
        with self.assertRaises(registry.CatalogError):
            auto.previous_tag("v1.0", self.repo)

    def test_the_changes_are_those_since_the_previous_release(self):
        self.commit("plugin/x.sh", "x")
        self.assertEqual(auto.tag_changes("v99.0.0", self.repo), ["plugin/x.sh"])

    def test_a_file_renamed_since_the_previous_release_lists_both_paths(self):
        self.commit("plugin/hook.sh", "#!/bin/sh\necho hook\n")
        git(self.repo, "tag", "v0.11.0")
        (self.repo / "docs").mkdir()
        git(self.repo, "mv", "plugin/hook.sh", "docs/hook.sh")
        git(self.repo, "commit", "-qm", "move")
        self.assertEqual(auto.tag_changes("v99.0.0", self.repo), ["docs/hook.sh", "plugin/hook.sh"])

    def test_a_first_release_changes_everything(self):
        solo = self.root / "solo"
        solo.mkdir()
        git(solo, "init", "-q", "-b", "main")
        self.commit("x.txt", "x", solo)
        self.assertEqual(auto.tag_changes("v0.1.0", solo), ["x.txt"])

    def test_a_tag_run_needs_the_tag_name(self):
        with self.assertRaises(registry.CatalogError):
            auto.changes("tag", self.repo)


class SelectionTests(unittest.TestCase):
    entries = [Entry("n1-a", "n1", "t", "new", ("plugin/**",)), Entry("n2-b", "n2", "t", "new", ("plugin/**",))]

    def ids(self, changed, patterns=()):
        return [e.id for e in auto.choose(self.entries, changed, patterns)]

    def test_the_changed_files_choose_the_scenarios(self):
        self.assertEqual(self.ids(["plugin/x"]), ["n1-a", "n2-b"])
        self.assertEqual(self.ids(["docs/x.md"]), [])

    def test_a_scenario_glob_narrows_the_choice(self):
        self.assertEqual(self.ids(["plugin/x"], ["n1-*"]), ["n1-a"])


class AdviceTests(unittest.TestCase):
    def result(self, status, reason):
        return report.Result("x-id", "n1", "t", status, reason)

    def test_a_failure_blocks_everywhere_and_a_skip_only_a_release_tag(self):
        for mode in auto.MODES:
            for status, blocks in ((report.FAILED, True), (report.SKIPPED, mode == "tag"), (report.PASSED, False),
                                   (report.NOT_AUTOMATED, False), (report.NOT_APPLICABLE, False)):
                self.assertEqual(auto.blocking(self.result(status, "r"), mode), blocks, (mode, status))

    def test_a_skip_that_does_not_block_is_still_listed_with_the_way_forward(self):
        skipped = self.result(report.SKIPPED, "the Claude login in the keychain has expired")
        self.assertEqual(auto.blockers([skipped], "push"), [])
        lines = auto.unproven([skipped, self.result(report.PASSED, "")], "push")
        self.assertTrue(lines[0].startswith("SKIPPED  x-id: the Claude login"), lines)
        self.assertIn("never logs in", lines[1])
        self.assertEqual(auto.unproven([skipped], "tag"), [])  # there it is a blocker instead

    def test_each_skip_says_how_to_go_on(self):
        advice = {"usage limit reached (...)": "limit to reset", "needs the network: run with --online": "add `--online`",
                  "no Claude login in the keychain": "never logs in", "the codex CLI is not installed (not on PATH)": "install that CLI",
                  "something odd": "read the reason"}
        for reason, words in advice.items():
            self.assertIn(words, auto.how_to_proceed(self.result(report.SKIPPED, reason)), reason)

    def test_blockers_list_only_what_stops_the_run_and_say_each_way_forward_once(self):
        passed, broke = self.result(report.PASSED, ""), self.result(report.FAILED, "it broke\nmore")
        lines = auto.blockers([passed, broke], "push")
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("FAILED"))
        self.assertIn("it broke", lines[0])
        self.assertNotIn("more", lines[0])
        self.assertTrue(lines[1].startswith("To proceed (x-id): "))
        skips = [report.Result(f"n2-{n}", "n2", "t", report.SKIPPED, "the codex CLI is not installed") for n in "ab"]
        lines = auto.blockers(skips, "tag")
        self.assertEqual(len(lines), 3)
        self.assertIn("(n2-a, n2-b)", lines[2])


class UsageLimitTests(unittest.TestCase):
    def test_the_wordings_of_both_providers_are_recognised(self):
        for text in ("You've hit your usage limit. Upgrade to Pro or try again at 3:14 PM.", "You've hit your limit · resets 3pm",
                     "Claude AI usage limit reached|1760000000", "Error: 429 Too Many Requests", "HTTP 429",
                     "insufficient_quota", "Your credit balance is too low to access the Anthropic API"):
            self.assertTrue(limits.usage_limit(f"line before\n{text}\nafter"), text)

    def test_ordinary_failures_are_not_mistaken_for_one(self):
        for text in ("AssertionError: port 4290 is busy", 'File "x.py", line 429, in run', "the limit of 1 s was exceeded", "timed out"):
            self.assertEqual(limits.usage_limit(text), "", text)


class SeedBuildTests(unittest.TestCase):
    """A missing seed is built by push and tag, never by a plain run without --online; a failed build stops with its reason."""

    def make(self, mode, online=False, wanted=True, found=None, built=None, failure=None):
        args = Namespace(auto=mode, online=online)
        build = mock.Mock(return_value=built, side_effect=failure)
        with (mock.patch.object(run.seeds, "find", return_value=found), mock.patch.object(run.seeds, "build", build),
              mock.patch.object(run.versions, "checkout_plugin", return_value=mock.Mock(path=Path("/plugin"))),
              contextlib.redirect_stderr(io.StringIO()) as err):
            try:
                return run.make_seed(args, Shared(Path("/cache"), online), wanted, mock.Mock()), build, err.getvalue(), None
            except run.versions.VersionError as error:
                return None, build, err.getvalue(), str(error)

    def test_push_and_tag_build_a_missing_seed_with_one_line_on_stderr(self):
        for mode in ("push", "tag"):
            seed, build, err, _ = self.make(mode, built="the seed")
            self.assertEqual((seed, build.call_count), ("the seed", 1), mode)
            self.assertEqual(len(err.splitlines()), 1, mode)
            self.assertIn("Making the data seed", err)

    def test_an_existing_seed_or_a_run_that_needs_none_builds_nothing(self):
        self.assertEqual(self.make("push", found="seed")[0:2], ("seed", mock.ANY))
        self.assertEqual(self.make("push", found="seed")[1].call_count, 0)
        seed, build, err, _ = self.make("push", wanted=False)
        self.assertEqual((seed, build.call_count, err), (None, 0, ""))

    def test_other_runs_still_need_online_to_build(self):
        for mode, online, builds in ((None, False, 0), (None, True, 1)):
            self.assertEqual(self.make(mode, online, built="s")[1].call_count, builds, (mode, online))

    def test_a_failed_build_stops_with_the_reason_and_the_command_to_retry(self):
        output = "\n".join(f"line {n}" for n in range(30))
        for failure in (run.versions.VersionError(f"setup.sh failed (1) while making the seed:\n{output}"),
                        run.guard.GuardError("the cache is the real home")):
            _, _, _, message = self.make("push", failure=failure)
            self.assertIn(str(failure).splitlines()[0], message)
            self.assertLessEqual(len(message.splitlines()), 6)
            self.assertTrue(message.splitlines()[-1].endswith("make acceptance-auto AUTO=push"))

    def test_a_build_that_fails_in_a_push_ends_the_run_as_a_stop(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(run, "make_seed", side_effect=run.versions.VersionError("no network")):
            args = Namespace(auto="push", no_cache=True, keep=False, online=False)
            entries = registry.load_catalog()
            registry.discover(entries)
            chosen = [e for e in entries if e.id == "n1-sandbox-chain"]
            meta = {"commit": "abc", "command": "run.py", "dirty": False, "started": "now"}
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
                code = run.run_selected(chosen, args, Shared(Path(tmp) / "cache", False), meta, Path(tmp) / "report")
        self.assertEqual(code, 2)
        self.assertIn("no network", err.getvalue())


class SkipJudgementTests(unittest.TestCase):
    """A skip ends a push run with status 0 and a warning, and stops a release tag."""

    def judge(self, mode):
        entries = registry.load_catalog()
        registry.discover(entries)
        chosen = [e for e in entries if e.id == "n2-codex-hook-note"]
        skipped = report.Result(chosen[0].id, "n2", "t", report.SKIPPED, "claude: the Claude login in the keychain has expired")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(run, "with_retry", return_value=skipped):
            args = Namespace(auto=mode, no_cache=True, keep=False, online=False)
            meta = {"commit": "abc", "command": "run.py", "dirty": False, "started": "now"}
            with contextlib.redirect_stdout(io.StringIO()) as out:
                code = run.run_selected(chosen, args, Shared(Path(tmp) / "cache", False), meta, Path(tmp) / "report")
        return code, out.getvalue()

    def test_a_push_goes_on_with_the_skip_listed(self):
        code, out = self.judge("push")
        self.assertEqual(code, 0)
        self.assertIn("Not proven", out)
        self.assertIn("SKIPPED  n2-codex-hook-note", out)
        self.assertIn("never logs in", out)
        self.assertNotIn("Blocked", out)

    def test_a_release_tag_is_blocked_by_the_skip(self):
        code, out = self.judge("tag")
        self.assertEqual(code, 1)
        self.assertIn("Blocked", out)


if __name__ == "__main__":
    unittest.main()
