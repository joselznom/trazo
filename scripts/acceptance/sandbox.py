"""The throwaway world one scenario runs in.

    root/
      home/                                   HOME
        Library/Application Support/trazo     TRAZO_DATA_DIR (the default under HOME too)
      claude-config/                          CLAUDE_CONFIG_DIR
      codex-home/                             CODEX_HOME
      tmp/                                    TMPDIR
      work/                                   papers and other project folders
      plugins/                                this scenario's copies of plugin folders

Children start with `env()`: the PATH and HOME of an app that launches the
plugin (see packages/trazo/tests/test_plugin_launcher.py), the five folders
above and nothing else; a scenario adds what it needs. Every spawn goes
through guard.check_env first.
"""

from __future__ import annotations

import os
import shutil
import signal
import socket
import stat
import subprocess
import tempfile
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path

from . import guard
from . import seed as seeds
from .evidence import Evidence, redact
from .procs import Proc, group_members, process_table, started_at
from .registry import Skip
from .versions import PluginFolder

APP_PATH = "/usr/bin:/bin"  # what a GUI-launched app has


def minimal_env(home: Path, data: Path | None = None, tmp: Path | None = None) -> dict[str, str]:
    """The environment of a plugin launched by an app, plus the sandbox folders."""
    env = {"PATH": APP_PATH, "HOME": str(home)}
    if data is not None:
        env["TRAZO_DATA_DIR"] = str(data)
    if tmp is not None:
        env["TMPDIR"] = str(tmp)
    return env


def port_free(port: int) -> bool:
    with socket.socket() as probe:
        return probe.connect_ex(("127.0.0.1", port)) != 0


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def port_holders(port: int) -> list[str]:
    """Who listens on the TCP port, as "pid N: command" (lsof and ps only read; nothing is signalled)."""
    done = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fp"], capture_output=True, text=True)
    pids = sorted({int(line[1:]) for line in done.stdout.splitlines() if line[:1] == "p" and line[1:].isdigit()})
    holders = []
    for pid in pids:
        command = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True).stdout.strip()
        holders.append(f"pid {pid}: {command[:200] or 'command unknown'}")
    return holders


@contextmanager
def busy_port(port: int, evidence: Evidence | None = None) -> Iterator[None]:
    """Something listens on 127.0.0.1:port (docs/plugin.md: `nc -lk 127.0.0.1 8765`).

    Holds the port itself when it is free. When another process already listens there, that
    satisfies the precondition, so the scenario runs against it: the process is left alone and
    `evidence` records who it is. The scenario skips only when the port is unusable for another
    reason (nothing answers on it, yet it cannot be bound)."""
    listener = socket.socket()
    # Sockets of a previous preview sit in TIME_WAIT on 8765 for a minute and make a plain bind fail.
    # With SO_REUSEADDR the bind ignores them but still fails while something listens, which is what
    # the scenarios need: trazo's own server binds the same way and must find the port taken.
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listener.bind(("127.0.0.1", port))
    except OSError as exc:
        listener.close()
        if port_free(port):
            raise Skip(f"port {port} cannot be held on this computer ({exc}), and nothing listens on it") from exc
        if evidence is not None:
            held = "; ".join(port_holders(port)) or "a process lsof cannot name"
            evidence.note(f"port {port} was already in use, so it counts as busy; held by {held}")
        yield
        return
    listener.listen()
    try:
        yield
    finally:
        listener.close()


class Sandbox:
    def __init__(self, name: str, evidence: Evidence, keep: bool = False,
                 seed: seeds.Seed | None = None, parent: Path | None = None) -> None:
        self.evidence, self.keep, self.seed = evidence, keep, seed
        safe = "".join(c if c.isalnum() else "-" for c in name)[:40]
        self.root = Path(tempfile.mkdtemp(prefix=f"trazo-acc-{safe}-", dir=parent)).resolve()
        self.home = self.root / "home"
        self.data = self.home / "Library" / "Application Support" / "trazo"
        self.claude_config, self.codex_home = self.root / "claude-config", self.root / "codex-home"
        self.tmp, self.work, self.plugins = self.root / "tmp", self.root / "work", self.root / "plugins"
        self.processes: list[subprocess.Popen] = []
        # Every child the harness starts leads a process group of its own (start_new_session), so what
        # it leaves behind (a shell's background job) is told from the owner's processes by the group.
        # Leader pid -> its start time, to refuse a later process that reuses the pid.
        self.groups: dict[int, str] = {}
        self.seeded = False
        try:
            guard.check_env(self.env(), self.root)
        except guard.GuardError:
            shutil.rmtree(self.root, ignore_errors=True)
            raise
        for folder in (self.data.parent, self.claude_config, self.codex_home, self.tmp, self.work, self.plugins):
            folder.mkdir(parents=True)

    # --- the environment -------------------------------------------------

    def env(self, extra: Mapping[str, str | None] | None = None, data_dir: bool = True) -> dict[str, str]:
        """A child's environment. `extra` adds or replaces (None removes) variables;
        data_dir=False leaves TRAZO_DATA_DIR unset, so trazo uses the default folder under HOME."""
        env = minimal_env(self.home, self.data if data_dir else None, self.tmp)
        env["CLAUDE_CONFIG_DIR"], env["CODEX_HOME"] = str(self.claude_config), str(self.codex_home)
        for name, value in (extra or {}).items():
            if value is None:
                env.pop(name, None)
            else:
                env[name] = value
        return env

    # --- running things ----------------------------------------------------

    def run(self, argv: list[str | Path], env: Mapping[str, str] | None = None, cwd: Path | None = None,
            timeout: float = 120, input: str | None = None, check: bool = False,
            secrets: Sequence[str] = ()) -> subprocess.CompletedProcess:
        """Run to completion with the sandbox environment; the run goes into the evidence.
        `secrets` (credentials the env or a file gave the child) are redacted from the evidence,
        from the returned output, from the error messages and from a timeout's partial output."""
        argv = [str(a) for a in argv]
        shown = [redact(a, secrets) for a in argv]  # a secret may sit on the command line too
        env = dict(env if env is not None else self.env())
        guard.check_env(env, self.root)
        started = time.monotonic()
        try:
            done = self._run_in_own_group(argv, env, cwd or self.work, input, timeout)
        except subprocess.TimeoutExpired as exc:
            stdout, stderr = redact(_text(exc.stdout), secrets), redact(_text(exc.stderr), secrets)
            self.evidence.command(shown, None, f"timed out after {timeout}s\n{stdout}{stderr}", time.monotonic() - started)
            # A new exception, not the caught one: its cmd (and args, which repr prints) hold the unredacted
            # argv, and the exception travels into logs and chained errors.
            raise subprocess.TimeoutExpired(shown, exc.timeout, output=stdout, stderr=stderr) from None
        done.stdout, done.stderr = redact(done.stdout, secrets), redact(done.stderr, secrets)
        self.evidence.command(shown, done.returncode, done.stdout + done.stderr, time.monotonic() - started)
        if check and done.returncode:
            raise AssertionError(f"{' '.join(shown)} exited {done.returncode}:\n{(done.stdout + done.stderr)[-1500:]}")
        return done

    def _remember_group(self, pid: int) -> None:
        self.groups[pid] = started_at(pid)

    def _run_in_own_group(self, argv: list[str], env: Mapping[str, str], cwd: Path, input: str | None,
                          timeout: float) -> subprocess.CompletedProcess:
        """subprocess.run, except that the child's group is known to cleanup before it ends."""
        with subprocess.Popen(argv, env=env, cwd=cwd, stdin=subprocess.PIPE if input is not None else None,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                              start_new_session=True) as process:
            self._remember_group(process.pid)
            try:
                stdout, stderr = process.communicate(input, timeout)
            except BaseException:  # a timeout or Ctrl-C: the child dies here, its leftovers at cleanup
                process.kill()
                raise
        return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)

    def spawn(self, argv: list[str | Path], env: Mapping[str, str] | None = None, cwd: Path | None = None,
              **options) -> subprocess.Popen:
        """Start a process that outlives this call; it is killed at cleanup."""
        env = dict(env if env is not None else self.env())
        guard.check_env(env, self.root)
        options["start_new_session"] = True
        process = subprocess.Popen([str(a) for a in argv], env=env, cwd=cwd or self.work, **options)
        self._remember_group(process.pid)
        self.processes.append(process)
        self.evidence.note(f"started pid {process.pid}: {' '.join(map(str, argv))}")
        return process

    # --- plugin folders and the install --------------------------------------

    def plugin(self, folder: PluginFolder) -> Path:
        """This scenario's own copy of a cached plugin folder, as an app's cache holds one."""
        target = self.plugins / f"{folder.version}-{folder.key[:8]}"
        if not target.exists():
            clone(folder.path, target)
        return target

    def seed_data(self) -> None:
        """Fill the data folder with the seed's downloads (uv, Python, Pandoc, caches), no install."""
        if self.seed is None:
            raise Skip("needs the data seed: run once with --online to make it")
        if not self.seeded:
            seeds.clone_into(self.seed, self.data)
            self.seeded = True
            self.evidence.note(f"data folder seeded from {self.seed.root.name}")

    def install(self, folder: PluginFolder, timeout: float = 600) -> subprocess.CompletedProcess:
        """Install `folder`'s trazo into the data folder the way setup does, offline:
        the seed's downloads plus `setup.sh --offline` (so it also fails if setup would download)."""
        self.seed_data()
        root = self.plugin(folder)
        return self.run(["/bin/sh", root / "plugin/scripts/setup.sh", "--offline", root], timeout=timeout, check=True)

    # --- cleanup -------------------------------------------------------------

    def stray_pids(self) -> list[int]:
        """Processes the harness started that still run: its children (sb.spawn and sb.run), whatever
        they left behind in their process group, and the descendants of all of those (an orphaned
        server, a child of a killed shell). Never a process the harness did not start."""
        return self._owned(process_table())

    def _owned(self, table: list[Proc]) -> list[int]:
        return group_members(table, self.groups, [process.pid for process in self.processes if process.poll() is None])

    def observed(self) -> list[str]:
        """Processes that mention this sandbox in their command line or run in it but are not the
        harness's own (a log viewer or shell of the owner), as "pid N: command". They are reported,
        never signalled."""
        table = process_table()
        owned = set(self._owned(table))
        inside = self._pids_running_in_sandbox()
        return [f"pid {p.pid}: {p.command[:200]}" for p in table
                if p.pid not in owned and p.pid != os.getpid() and (str(self.root) in p.command or p.pid in inside)]

    def _pids_running_in_sandbox(self) -> set[int]:
        """Processes whose working directory is inside the sandbox (lsof: p<pid> then n<path>)."""
        done = subprocess.run(["lsof", "-a", "-d", "cwd", "-F", "pn"], capture_output=True, text=True)
        pid, found = 0, set()
        for line in done.stdout.splitlines():
            if line.startswith("p"):
                pid = int(line[1:])
            elif line.startswith("n") and Path(line[1:]).is_relative_to(self.root):
                found.add(pid)
        return found

    def cleanup(self) -> None:
        # Collect first: once a parent dies its children are reparented and no longer found.
        pids = self.stray_pids()
        for line in self.observed():
            self.evidence.note(f"left running, not started by the harness: {line}")
        for pid in pids:
            _signal(pid, signal.SIGTERM)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and any(self._alive(pid) for pid in pids):
            time.sleep(0.1)
        for pid in pids:
            if self._alive(pid):
                _signal(pid, signal.SIGKILL)
        for process in self.processes:
            try:
                process.wait(5)
            except subprocess.TimeoutExpired:
                process.kill()
        if self.keep:
            self.evidence.path("sandbox (kept)", self.root)
        else:
            shutil.rmtree(self.root, onexc=_make_writable)

    def _alive(self, pid: int) -> bool:
        for process in self.processes:  # our children stay zombies until polled
            if process.pid == pid:
                return process.poll() is None
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True


def clone(source: Path, target: Path) -> None:
    """Copy a folder, as a copy-on-write clone where the volume has them."""
    if subprocess.run(["cp", "-c", "-R", str(source), str(target)], capture_output=True).returncode:
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(source, target, symlinks=True)


def _signal(pid: int, sig: int) -> None:
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        pass


def _make_writable(function, path, _error) -> None:
    """rmtree's retry for read-only files (uv's cache and Python installs have some)."""
    os.chmod(path, stat.S_IRWXU)
    os.chmod(Path(path).parent, stat.S_IRWXU)
    function(path)


def _text(value: str | bytes | None) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value or ""
