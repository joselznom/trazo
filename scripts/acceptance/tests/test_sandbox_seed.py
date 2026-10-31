"""The sandbox's cleanup and the seed's clone."""

import os
import signal
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

import _path  # noqa: F401
from acceptance import guard, seed
from acceptance.evidence import Evidence
from acceptance.sandbox import Sandbox, busy_port, free_port, minimal_env, port_free


class SandboxTests(unittest.TestCase):
    def test_cleanup_stops_what_was_started_and_removes_the_folder(self):
        sandbox = Sandbox("unit", Evidence())
        child = sandbox.spawn(["/bin/sh", "-c", "sleep 300; :", str(sandbox.root)])
        sandbox.run(["/bin/sh", "-c", 'echo hi > "$HOME/file"'], check=True)
        self.assertTrue((sandbox.home / "file").is_file())
        grandchild = int(sandbox.run(["/bin/sh", "-c", "sleep 300 >/dev/null 2>&1 & echo $!"], check=True).stdout)  # outlives its shell
        sandbox.cleanup()
        self.assertIsNotNone(child.poll())
        self.assertFalse(sandbox.root.exists())
        with self.assertRaises(ProcessLookupError):
            os.kill(grandchild, 0)

    def test_a_process_the_owner_started_is_reported_and_left_running(self):
        evidence = Evidence()
        sandbox = Sandbox("unit-observer", evidence)
        # A log viewer with its cwd in the sandbox, and a shell whose command line names it.
        # Their own sessions, so that this test can stop them and the shell's child with one killpg.
        in_cwd = subprocess.Popen(["sleep", "300"], cwd=sandbox.work, start_new_session=True)
        by_name = subprocess.Popen(["/bin/sh", "-c", "sleep 300; :", str(sandbox.root)], start_new_session=True)
        for observer in (in_cwd, by_name):
            self.addCleanup(observer.wait)
            self.addCleanup(os.killpg, observer.pid, signal.SIGKILL)
        ours = sandbox.spawn(["/bin/sh", "-c", "sleep 300; :"])
        self.assertEqual(sandbox.stray_pids(), sorted({ours.pid, *self.children(ours.pid)}))
        sandbox.cleanup()
        self.assertIsNone(in_cwd.poll())
        self.assertIsNone(by_name.poll())
        self.assertIsNotNone(ours.poll())
        left = " ".join(item["text"] for item in evidence.items if item["kind"] == "note" and "left running" in item["text"])
        self.assertIn(f"pid {in_cwd.pid}:", left)
        self.assertIn(f"pid {by_name.pid}:", left)
        self.assertNotIn(f"pid {ours.pid}:", left)

    def children(self, pid):
        time.sleep(0.3)  # the shell has forked its sleep
        done = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True)
        return {int(n) for n in done.stdout.split()}

    def test_a_spawned_process_its_child_and_what_a_run_left_behind_are_stopped(self):
        sandbox = Sandbox("unit-own", Evidence())
        parent = sandbox.spawn(["/bin/sh", "-c", "sleep 300 & echo $!; wait"], stdout=subprocess.PIPE, text=True)
        child = int(parent.stdout.readline())
        left = int(sandbox.run(["/bin/sh", "-c", "sleep 300 >/dev/null 2>&1 & echo $!"], check=True).stdout)
        self.assertIn(child, sandbox.stray_pids())
        self.assertIn(left, sandbox.stray_pids())
        sandbox.cleanup()
        parent.stdout.close()
        for pid in (child, left):
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)

    def test_keep_leaves_the_folder_and_says_where(self):
        evidence = Evidence()
        sandbox = Sandbox("unit-keep", evidence, keep=True)
        self.addCleanup(lambda: __import__("shutil").rmtree(sandbox.root, True))
        sandbox.cleanup()
        self.assertTrue(sandbox.root.is_dir())
        self.assertIn({"kind": "path", "label": "sandbox (kept)", "path": str(sandbox.root)}, evidence.items)

    def test_secrets_are_redacted_from_the_output_the_evidence_and_the_errors(self):
        evidence = Evidence()
        sandbox = Sandbox("unit-secrets", evidence)
        self.addCleanup(sandbox.cleanup)
        secret = "tok-SYNTHETIC-1234"
        script = f"echo out {secret}; echo err {secret} >&2; exit 3"
        done = sandbox.run(["/bin/sh", "-c", script], secrets=[secret])
        self.assertNotIn(secret, done.stdout + done.stderr + str(evidence.items))
        with self.assertRaises(AssertionError) as raised:
            sandbox.run(["/bin/sh", "-c", script], secrets=[secret], check=True)
        self.assertNotIn(secret, str(raised.exception))
        with self.assertRaises(subprocess.TimeoutExpired) as timeout:
            sandbox.run(["/bin/sh", "-c", f"echo {secret}; sleep 5"], secrets=[secret], timeout=1)
        self.assertNotIn(secret, f"{timeout.exception.stdout}{timeout.exception.stderr}{evidence.items}")

    def test_a_timeout_does_not_show_a_secret_passed_in_argv(self):
        evidence = Evidence()
        sandbox = Sandbox("unit-timeout-argv", evidence)
        self.addCleanup(sandbox.cleanup)
        secret = "tok-SYNTHETIC-5678"
        with self.assertRaises(subprocess.TimeoutExpired) as timeout:
            sandbox.run(["/bin/sh", "-c", "sleep 5", "sh", f"--token={secret}"], secrets=[secret], timeout=1)
        exc = timeout.exception
        self.assertNotIn(secret, f"{exc} {exc!r} {exc.cmd} {exc.stdout} {exc.stderr} {evidence.items}")
        self.assertIn("--token=[secret]", str(exc))

    def test_extra_environment_is_added_or_removed(self):
        sandbox = Sandbox("unit-env", Evidence())
        self.addCleanup(sandbox.cleanup)
        env = sandbox.env({"FOO": "1", "PATH": None})
        self.assertEqual((env["FOO"], "PATH" in env), ("1", False))


class BusyPortTests(unittest.TestCase):
    def test_a_port_in_time_wait_can_be_held_and_a_second_listener_still_fails(self):
        port = free_port()
        # The server closes first, so its side of the connection stays in TIME_WAIT on the port.
        server = socket.socket()
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", port))
        server.listen()
        client = socket.create_connection(("127.0.0.1", port))
        accepted, _ = server.accept()
        accepted.close()
        client.close()
        server.close()
        with busy_port(port):
            # What trazo's own server does (http.server sets SO_REUSEADDR): it must find the port taken.
            other = socket.socket()
            other.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            with self.assertRaises(OSError):
                other.bind(("127.0.0.1", port))
            other.close()

    def test_a_port_another_process_already_holds_counts_as_busy_and_names_the_holder(self):
        port = free_port()
        holder = socket.socket()  # stands in for a real server (a `kanvas serve`) that is not ours
        holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        holder.bind(("127.0.0.1", port))
        holder.listen()
        self.addCleanup(holder.close)
        evidence = Evidence()
        with busy_port(port, evidence):
            self.assertFalse(port_free(port))
        notes = [item["text"] for item in evidence.items if item["kind"] == "note"]
        self.assertEqual(len(notes), 1, notes)
        self.assertIn(f"pid {os.getpid()}:", notes[0])
        self.assertFalse(port_free(port), "the scenario must not close a listener it did not open")

    def test_a_free_port_is_held_by_us_and_leaves_no_note_of_a_foreign_holder(self):
        port = free_port()
        evidence = Evidence()
        with busy_port(port, evidence):
            self.assertFalse(port_free(port))
        self.assertTrue(port_free(port))
        self.assertEqual(evidence.items, [])


class SeedTests(unittest.TestCase):
    def test_pins_come_from_the_sources_setup_downloads_by(self):
        pins = seed.pins()
        self.assertRegex(pins["uv"], r"^\d+\.\d+\.\d+$")
        self.assertRegex(pins["pandoc"], r"^\d+(\.\d+)+$")
        self.assertNotEqual(seed.key(pins), seed.key({**pins, "pandoc": "0"}))

    def test_a_clone_leaves_out_the_install_and_points_absolute_links_into_itself(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            fake = seed.Seed(root / "seed", {})
            data = fake.data
            (data / "python/cpython-3.12.1").mkdir(parents=True)
            (data / "python/cpython-3.12").symlink_to(data / "python/cpython-3.12.1")
            (data / "uv").mkdir()
            (data / "uv/uv").write_text("binary")
            (data / "venv/bin").mkdir(parents=True)
            (data / "machine.json").write_text("{}")
            (data / "pandoc").mkdir()
            target = root / "clone"
            seed.clone_into(fake, target)
            self.assertEqual(sorted(p.name for p in target.iterdir()), ["pandoc", "python", "uv"])
            self.assertEqual(os.readlink(target / "python/cpython-3.12"), str(target / "python/cpython-3.12.1"))
            self.assertTrue((target / "python/cpython-3.12/").is_dir())
            time.sleep(0)
            self.assertEqual(os.readlink(data / "python/cpython-3.12"), str(data / "python/cpython-3.12.1"))


class SeedBuildTests(unittest.TestCase):
    """seed.build with a stand-in setup.sh, so that no download happens."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.plugin = self.tmp / "plugin-root"
        (self.plugin / "plugin/scripts").mkdir(parents=True)
        self.runs = self.tmp / "runs"
        # What the real setup does to its data folder, slowly enough for a second builder to arrive.
        (self.plugin / "plugin/scripts/setup.sh").write_text(
            f'echo "$$" >> "{self.runs}"\nmkdir -p "$TRAZO_DATA_DIR"\necho first > "$TRAZO_DATA_DIR/a"\n'
            f'sleep 1\necho second > "$TRAZO_DATA_DIR/b"\n')

    def build(self, cache, **options):
        return seed.build(cache, self.plugin, minimal_env, lambda argv, done: None, **options)

    def test_two_builders_make_one_seed_and_neither_deletes_the_others_files(self):
        cache = self.tmp / "cache"
        made, errors = [], []

        def builder():
            try:
                made.append(self.build(cache))
            except Exception as exc:  # reported below, not lost in the thread
                errors.append(exc)
        first = threading.Thread(target=builder)
        first.start()
        time.sleep(0.4)  # the first is inside setup.sh now
        second = threading.Thread(target=builder)
        second.start()
        first.join(30)
        second.join(30)
        self.assertEqual(errors, [])
        self.assertEqual(len(self.runs.read_text().split()), 1, "setup.sh ran more than once")
        self.assertEqual({m.root for m in made}, {made[0].root})
        data = made[0].data
        self.assertEqual((data / "a").read_text() + (data / "b").read_text(), "first\nsecond\n")
        self.assertTrue((made[0].root / ".complete").is_file())

    def test_a_finished_seed_is_not_built_again(self):
        cache = self.tmp / "cache"
        self.build(cache)
        self.build(cache)
        self.assertEqual(len(self.runs.read_text().split()), 1)

    def test_a_protected_cache_is_refused_and_setup_never_starts(self):
        home = self.tmp / "real home"
        cache = home / ".claude-personal" / "cache"
        with self.assertRaises(guard.GuardError):
            self.build(cache, home=home)
        self.assertFalse(home.exists())
        self.assertFalse(self.runs.exists())

    def test_a_protected_data_folder_for_setup_is_refused_and_setup_never_starts(self):
        home = self.tmp / "real home"
        data = home / "Library/Application Support/trazo"
        for env in (lambda h, d: {"PATH": "/usr/bin:/bin", "HOME": str(h), "TRAZO_DATA_DIR": str(data)},
                    lambda h, d: {"PATH": "/usr/bin:/bin", "HOME": str(home), "TRAZO_DATA_DIR": str(d)},
                    lambda h, d: {"PATH": "/usr/bin:/bin", "TRAZO_DATA_DIR": str(d)}):  # no HOME: the real one
            with self.assertRaises(guard.GuardError):
                seed.build(self.tmp / "cache", self.plugin, env, lambda argv, done: None, home=home)
        self.assertFalse(self.runs.exists())
        self.assertFalse((self.tmp / "cache").exists(), "a refused build still made the cache")


if __name__ == "__main__":
    unittest.main()
