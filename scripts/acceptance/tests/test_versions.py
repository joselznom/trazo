"""Plugin folders: the stamped checkout and the release tag."""

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import _path  # noqa: F401
from acceptance import auto, guard, resultcache, versions

REPO = versions.REPO
sys.path.insert(0, str(REPO / "packages/trazo/src"))
from trazo import variants  # noqa: E402


class StampTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cache = Path(cls.tmp.name)
        cls.folder = versions.checkout_plugin(cls.cache, 7)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_every_place_that_carries_the_version_says_99_0_7(self):
        root = self.folder.path
        self.assertEqual(self.folder.version, "99.0.7")
        for name in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
            self.assertEqual(json.loads((root / name).read_text())["version"], "99.0.7", name)
        self.assertIn('\nversion = "99.0.7"\n', (root / "packages/trazo/pyproject.toml").read_text())
        self.assertIn('\n__version__ = "99.0.7"\n', (root / "packages/trazo/src/trazo/__init__.py").read_text())
        self.assertIn('name = "trazo"\nversion = "99.0.7"\n', (root / "packages/trazo/uv.lock").read_text())
        self.assertIn("default: v99.0.7\n", (root / "copier.yml").read_text())

    def test_the_hook_reads_the_stamped_version_like_the_release_check(self):
        # session-start.sh's sed needs the two-space indent the manifest has.
        text = (self.folder.path / ".claude-plugin/plugin.json").read_text()
        self.assertEqual(re.search(r'^  "version": *"([^"]*)"', text, re.M)[1], "99.0.7")

    def test_the_template_store_is_at_the_stamped_release_and_only_trazo_toml_changed(self):
        real, stamped = variants.Store(REPO / versions.STORE), variants.Store(self.folder.path / versions.STORE)
        self.assertEqual(stamped.template, "v99.0.7")
        self.assertEqual(stamped.index["copier"], real.index["copier"])
        values = {"title": "T", "project_name": "p"}
        for kind in variants.KINDS:
            for example in (True, False):
                before, after = real.render(kind, example, values), stamped.render(kind, example, values)
                self.assertEqual(before.keys(), after.keys())
                self.assertEqual([p for p in before if before[p] != after[p]], ["trazo.toml"])
                old = before["trazo.toml"].decode()
                self.assertEqual(after["trazo.toml"].decode(), old.replace(f'version = "{real.template}"', 'version = "v99.0.7"'))
                self.assertIn('version = "v99.0.7"\n', after["trazo.toml"].decode())

    def test_blobs_are_named_by_their_content_and_none_is_left_behind(self):
        import hashlib
        store = self.folder.path / versions.STORE
        index = json.loads((store / "index.json").read_text())
        used = {d for v in index["variants"].values() for d in v["files"].values()}
        stored = {p.name for p in (store / "blobs").iterdir()}
        self.assertEqual(stored, used)
        for digest in stored:
            self.assertEqual(hashlib.sha256((store / "blobs" / digest).read_bytes()).hexdigest(), digest)

    def test_the_same_content_is_built_once_and_another_n_is_another_release(self):
        self.assertEqual(versions.checkout_plugin(self.cache, 7).path, self.folder.path)
        other = versions.checkout_plugin(self.cache, 8)
        self.assertNotEqual(other.path, self.folder.path)
        self.assertEqual(other.version, "99.0.8")

    def test_the_checkout_is_what_git_lists_and_the_cache_is_not_part_of_it(self):
        self.assertTrue((self.folder.path / "plugin/mcp.sh").is_file())
        self.assertFalse((self.folder.path / ".git").exists())
        self.assertFalse((self.folder.path / ".build").exists())
        self.assertFalse((self.folder.path / "packages/trazo/.venv").exists())


class CacheDirTests(unittest.TestCase):
    def test_the_default_is_the_clones_main_checkout_and_the_variable_moves_it(self):
        default = versions.main_checkout(REPO) / ".build" / "acceptance" / "cache"
        self.assertEqual(versions.cache_dir({}), default)
        self.assertEqual(versions.cache_dir({versions.CACHE_ENV: ""}), default)
        with tempfile.TemporaryDirectory() as tmp:
            shared = Path(tmp).resolve() / "shared"
            self.assertEqual(versions.cache_dir({versions.CACHE_ENV: str(shared)}), shared)

    def test_every_worktree_of_a_clone_defaults_to_the_same_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            main, other = Path(tmp).resolve() / "main", Path(tmp).resolve() / "other"
            main.mkdir()
            for args in (["init", "-q"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x"],
                         ["worktree", "add", "-q", "--detach", str(other)]):
                subprocess.run(["git", *args], cwd=main, check=True, capture_output=True)
            self.assertEqual(versions.main_checkout(main), main)
            self.assertEqual(versions.main_checkout(other), main)
            self.assertEqual(versions.cache_dir({}, repo=other), main / ".build" / "acceptance" / "cache")

    def test_a_cache_in_a_protected_place_is_refused_before_anything_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve() / "real home"
            for name in (".codex", ".claude", ".claude-personal", "Library/Application Support/trazo",
                         "Library/Caches/claude-cli-nodejs"):
                (home / name).mkdir(parents=True)
            for place in (home, home / ".codex" / "cache", home / ".claude" / "cache", home / ".claude-personal" / "x",
                          home / "Library/Application Support/trazo", home / "Library/Application Support/trazo/cache",
                          home / "Library/Caches/claude-cli-nodejs" / "c", home.parent):
                with self.assertRaises(guard.GuardError, msg=str(place)):
                    versions.cache_dir({versions.CACHE_ENV: str(place)}, home)
            self.assertEqual(versions.cache_dir({versions.CACHE_ENV: str(home / "elsewhere/cache")}, home),
                             home / "elsewhere/cache")
            self.assertEqual(sorted(p.name for p in home.iterdir()),
                             [".claude", ".claude-personal", ".codex", "Library"], "nothing was written")

    def test_every_writer_refuses_a_protected_cache_too(self):
        # prune and the plugin builders take the cache as an argument; the guard applies there as well.
        # The "real" home is a fake one, so a missing guard would write into a temporary folder.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve() / "real home"
            cache = home / ".codex" / "cache"
            with mock.patch.object(guard, "real_home", return_value=home):
                with self.assertRaises(guard.GuardError):
                    versions.prune(cache, cache / "plugin-checkout-x")
                with self.assertRaises(guard.GuardError):
                    versions.checkout_plugin(cache)
                with self.assertRaises(guard.GuardError):
                    versions.tag_plugin(cache)
            self.assertFalse(home.exists(), "a refused cache was still written")


class PruneTests(unittest.TestCase):
    def test_the_least_recently_used_checkout_folders_go_and_the_current_one_stays(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            names = [f"plugin-checkout-{n}" for n in range(versions.KEEP_CHECKOUTS + 3)]
            for age, name in enumerate(names):
                (cache / name).mkdir()
                os.utime(cache / name, (1000 + age, 1000 + age))
            (cache / "plugin-0123").mkdir()
            versions.prune(cache, cache / names[0])  # the oldest, but in use
            left = sorted(p.name for p in cache.iterdir())
            self.assertIn(names[0], left)
            self.assertIn("plugin-0123", left)  # a tag folder
            self.assertEqual(len([n for n in left if n.startswith("plugin")]), versions.KEEP_CHECKOUTS + 2)
            self.assertNotIn(names[1], left)

    def test_a_folder_used_recently_is_kept_even_beyond_the_limit(self):
        # Another worktree's run may be reading it: the age is the cheap way to know it is not in use.
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            now = time.time()
            for n in range(versions.KEEP_CHECKOUTS + 2):
                (cache / f"plugin-checkout-{n}").mkdir()
                os.utime(cache / f"plugin-checkout-{n}", (now - n, now - n))
            versions.prune(cache, cache / "plugin-checkout-0")
            self.assertEqual(len(list(cache.glob("plugin-checkout-*"))), versions.KEEP_CHECKOUTS + 2)

    def test_pruning_waits_for_whoever_holds_the_plugins_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            for n in range(versions.KEEP_CHECKOUTS + 1):
                (cache / f"plugin-checkout-{n}").mkdir()
                os.utime(cache / f"plugin-checkout-{n}", (1000 + n, 1000 + n))
            pruner = threading.Thread(target=versions.prune, args=(cache, cache / "plugin-checkout-6"))
            with versions.plugins_locked(cache):
                pruner.start()
                time.sleep(0.3)
                self.assertTrue((cache / "plugin-checkout-0").is_dir(), "pruned while another run held the lock")
            pruner.join(10)
            self.assertFalse((cache / "plugin-checkout-0").exists())


class PatchStoreTests(unittest.TestCase):
    def fake_store(self, root, extra=None):
        import hashlib
        blobs = root / "blobs"
        blobs.mkdir(parents=True)
        files = {}
        for name, data in {"trazo.toml": b'a = 1\nversion = "v1.2.3"\n', "other": extra or b"x"}.items():
            digest = hashlib.sha256(data).hexdigest()
            (blobs / digest).write_bytes(data)
            files[name] = digest
        index = {"format": 1, "template": "v1.2.3", "variants": {"v": {"files": files}}}
        (root / "index.json").write_text(json.dumps(index, indent=1, ensure_ascii=False) + "\n")

    def test_a_file_other_than_trazo_toml_with_the_version_asks_for_a_re_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.fake_store(Path(tmp), extra=b"released as 1.2.3")
            with self.assertRaisesRegex(versions.VersionError, "re-render"):
                versions.patch_store(Path(tmp), "v1.2.3", "v99.0.1")

    def test_a_store_at_another_release_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.fake_store(Path(tmp))
            with self.assertRaisesRegex(versions.VersionError, "expected v9.9.9"):
                versions.patch_store(Path(tmp), "v9.9.9", "v99.0.1")

    def test_the_patch_moves_the_tag_and_the_pin(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.fake_store(Path(tmp))
            versions.patch_store(Path(tmp), "v1.2.3", "v99.0.1")
            index = json.loads((Path(tmp) / "index.json").read_text())
            self.assertEqual(index["template"], "v99.0.1")
            digest = index["variants"]["v"]["files"]["trazo.toml"]
            self.assertEqual((Path(tmp) / "blobs" / digest).read_bytes(), b'a = 1\nversion = "v99.0.1"\n')
            self.assertEqual(len(list((Path(tmp) / "blobs").iterdir())), 2)


def tagged_repo(repo: Path, *tags: str) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                    "commit", "-q", "--allow-empty", "-m", "x"], cwd=repo, check=True)
    for tag in tags:
        subprocess.run(["git", "-c", "tag.gpgsign=false", "tag", tag], cwd=repo, check=True)
    return repo


class TagTests(unittest.TestCase):
    def test_the_highest_release_tag_wins_by_number_not_by_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = tagged_repo(Path(tmp), "v0.2.0", "v0.9.0", "v0.10.0", "v1.0.0-rc1", "vnext")
            self.assertEqual(versions.latest_tag(repo), "v0.10.0")
            # A tag run: the release being pushed already exists locally, and "the last release" (what the
            # update scenarios install, what the fingerprints hash) must be the one before it.
            with mock.patch.dict(os.environ):
                auto.configure("tag", tag="v0.10.0")
                self.assertEqual(versions.latest_tag(repo), "v0.9.0")
                self.assertEqual(resultcache.latest_tag_commit(repo), versions.git("rev-parse", "v0.9.0^{commit}", repo=repo,
                                                                                   text=True).stdout.strip())
            with mock.patch.dict(os.environ):
                auto.configure("push")
                self.assertEqual(versions.latest_tag(repo), "v0.10.0")

    def test_a_first_release_has_nothing_to_update_from(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = tagged_repo(Path(tmp), "v1.0.0")
            with mock.patch.dict(os.environ):
                auto.configure("tag", tag="v1.0.0")
                with self.assertRaisesRegex(versions.FirstRelease, "v1.0.0 is the first release"):
                    versions.latest_tag(repo)
            # Outside a tag run, a clone without tags is an error to fix, not a first release.
            versions.git("tag", "-d", "v1.0.0", repo=repo)
            with self.assertRaises(versions.VersionError) as caught:
                versions.latest_tag(repo)
            self.assertNotIsInstance(caught.exception, versions.FirstRelease)

    def test_the_tag_folder_is_the_released_files(self):
        try:
            tag = versions.latest_tag()
        except versions.VersionError:
            self.skipTest("this clone has no release tags")
        with tempfile.TemporaryDirectory() as tmp:
            folder = versions.tag_plugin(Path(tmp))
            self.assertEqual(folder.label, f"tag {tag}")
            self.assertEqual(folder.version, tag.removeprefix("v"))
            self.assertEqual(versions.tag_plugin(Path(tmp)).path, folder.path)


if __name__ == "__main__":
    unittest.main()
