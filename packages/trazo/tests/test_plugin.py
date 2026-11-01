"""The plugin's files: the manifests and marketplace, the SessionStart hook
(plugin/session-start.sh) and the repository size limit."""

import json
import os
import shlex
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import _support  # noqa: F401  (sets TRAZO_ROOT before trazo is imported)
from test_machine import fake_tool

import trazo
from trazo import machine

REPO = Path(__file__).resolve().parents[3]
PLUGIN = REPO / ".claude-plugin"


# PEP 440's canonical public version (its appendix B).
PEP440 = r"^([1-9][0-9]*!)?(0|[1-9][0-9]*)(\.(0|[1-9][0-9]*))*((a|b|rc)(0|[1-9][0-9]*))?(\.post(0|[1-9][0-9]*))?(\.dev(0|[1-9][0-9]*))?$"


def install(data: Path, *versions: str, machine_file=True) -> Path:
    """A data folder with venv/bin/trazo, one dist-info folder per version
    and, unless told not to, machine.json as `trazo setup` writes it."""
    site = data / "venv" / "lib" / "python3.12" / "site-packages"
    site.mkdir(parents=True, exist_ok=True)
    for version in versions:
        (site / f"trazo-{version}.dist-info").mkdir()
    (data / "venv" / "bin").mkdir(exist_ok=True)
    fake_tool(data / "venv" / "bin", "trazo", "trazo")
    if machine_file:
        with patch.dict(os.environ, {"TRAZO_DATA_DIR": str(data)}):
            machine.write({})
    return data


def run_hook(root: Path, env: dict[str, str], tmp: Path, system: str = "Darwin") -> str:
    """The SessionStart hook as Claude Code and Codex run it: hooks.json's
    command with ${CLAUDE_PLUGIN_ROOT} replaced by `root`, run by a shell,
    the event on stdin, `uname -s` saying `system`. Returns
    additionalContext, or ""."""
    hook = json.loads((REPO / "hooks" / "hooks.json").read_text())["hooks"]["SessionStart"][0]["hooks"][0]
    argv = ["/bin/sh", "-c", hook["command"].replace("${CLAUDE_PLUGIN_ROOT}", str(root))]
    bin_ = tmp / f"uname-{system}"
    bin_.mkdir(exist_ok=True)
    fake_tool(bin_, "uname", system)
    home = tmp / "home"
    home.mkdir(exist_ok=True)
    event = {"session_id": "test", "transcript_path": str(tmp / "t.jsonl"), "cwd": str(tmp),
             "hook_event_name": "SessionStart", "source": "startup"}
    started = time.monotonic()
    result = subprocess.run(argv, capture_output=True, text=True, input=json.dumps(event), timeout=hook["timeout"],
                            env={"PATH": f"{bin_}:/usr/bin:/bin", "HOME": str(home),
                                 "CLAUDE_PLUGIN_ROOT": str(root), **env})
    assert time.monotonic() - started < 1.0, "the hook took a second or more"
    assert (result.returncode, result.stderr) == (0, ""), result
    if not result.stdout:
        return ""
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output["hookEventName"] == "SessionStart", output
    return output["additionalContext"]


NOT_SET_UP = ("The trazo plugin is not set up on this computer: its MCP tools are unavailable until the "
              "trazo:setup skill runs. When the user asks for trazo or paper work, run that skill; that "
              "request is the go-ahead.")


def update(installed: str, plugin: str) -> str:
    return (f"trazo {installed} is installed but the plugin is {plugin}; the trazo:setup skill reinstalls "
            "trazo from the plugin's files and downloads only what is missing. When the user asks for trazo or "
            "paper work, run that skill; that request is the go-ahead.")


def older_plugin(installed: str, plugin: str) -> str:
    return (f"trazo {installed} is installed, newer than this plugin ({plugin}): tell the user to update the "
            "trazo plugin in this app. Do not run the trazo:setup skill, which would install the older "
            "trazo. This matters when the user asks for trazo or paper work.")


@unittest.skipIf(os.name == "nt", "the hook runs /bin/sh")
class SessionStartHookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.data = self.tmp / "dätä folder"
        self.version = trazo.__version__

    def hook(self, root=REPO, system="Darwin"):
        return run_hook(root, {"TRAZO_DATA_DIR": str(self.data)}, self.tmp, system)

    def plugin_root(self, manifest: str) -> Path:
        """A copy of the plugin root with this plugin.json text."""
        root = self.tmp / "plugin root"
        shutil.rmtree(root, ignore_errors=True)
        (root / ".claude-plugin").mkdir(parents=True)
        (root / ".claude-plugin" / "plugin.json").write_text(manifest)
        (root / "plugin").mkdir()
        shutil.copy(REPO / "plugin" / "session-start.sh", root / "plugin")
        return root

    def test_default_data_folder_follows_the_system(self):
        home = self.tmp / "home"
        self.assertEqual(run_hook(REPO, {}, self.tmp), NOT_SET_UP)
        install(home / ".local" / "share" / "trazo", self.version)
        self.assertEqual(run_hook(REPO, {}, self.tmp, "Linux"), "")
        self.assertEqual(run_hook(REPO, {}, self.tmp, "Darwin"), NOT_SET_UP)
        install(home / "Library" / "Application Support" / "trazo", self.version)
        self.assertEqual(run_hook(REPO, {}, self.tmp, "Darwin"), "")
        xdg = {"XDG_DATA_HOME": str(self.tmp / "xdg")}
        self.assertEqual(run_hook(REPO, xdg, self.tmp, "Linux"), NOT_SET_UP)
        install(self.tmp / "xdg" / "trazo", self.version)
        self.assertEqual(run_hook(REPO, xdg, self.tmp, "Linux"), "")

    def test_not_set_up(self):
        self.assertEqual(self.hook(), NOT_SET_UP)
        install(self.data, "0.4.0", machine_file=False)
        # Without machine.json, `trazo setup` did not finish; an empty one is no better.
        self.assertEqual(self.hook(), NOT_SET_UP)
        (self.data / "machine.json").write_text("")
        self.assertEqual(self.hook(), NOT_SET_UP)

    def test_update_then_ok(self):
        install(self.data, "0.4.0")
        self.assertEqual(self.hook(), update("0.4.0", self.version))
        shutil.rmtree(next(self.data.glob("venv/lib/*/site-packages/trazo-0.4.0.dist-info")))
        install(self.data, self.version)
        self.assertEqual(self.hook(), "")

    def test_two_dist_info_folders_are_broken(self):
        install(self.data, "0.5.0", "0.10.0")
        self.assertEqual(self.hook(), NOT_SET_UP)

    def test_an_older_plugin_is_not_a_downgrade(self):
        # The other app's newer plugin installed trazo: setup here would downgrade it.
        for plugin, installed in (("0.9.0", "0.10.0"), ("0.9.9", "0.10"), ("1.2", "1.2.1")):
            root = self.plugin_root(json.dumps({"name": "trazo", "version": plugin}, indent=2))
            shutil.rmtree(self.data, ignore_errors=True)
            install(self.data, installed)
            self.assertEqual(self.hook(root), older_plugin(installed, plugin))
        # Also when the other app's setup stopped before machine.json.
        (self.data / "machine.json").unlink()
        self.assertEqual(self.hook(root), older_plugin("1.2.1", "1.2"))
        # Newer, or versions it cannot compare (a pre-release): the plugin's setup, as before.
        for plugin, installed in (("0.10.0", "0.9.0"), ("0.10.0", "0.10.0rc1"), ("0.10.0rc1", "0.10.0")):
            root = self.plugin_root(json.dumps({"name": "trazo", "version": plugin}, indent=2))
            shutil.rmtree(self.data, ignore_errors=True)
            install(self.data, installed)
            self.assertEqual(self.hook(root), update(installed, plugin))

    def test_unknown_installed_version(self):
        install(self.data, '0.4.0"x')
        self.assertEqual(self.hook(), NOT_SET_UP)

    def test_linux_gets_the_notes(self):
        self.assertEqual(self.hook(system="Linux"), NOT_SET_UP)
        # Where setup.sh does not run, a note would only send the agent to a failing setup.
        self.assertEqual(self.hook(system="FreeBSD"), "")

    def test_reordered_manifest_with_a_nested_version(self):
        manifest = {"name": "trazo", "author": {"name": "x", "version": "9.9.9"}, "version": "0.7.0"}
        root = self.plugin_root(json.dumps(manifest, indent=2))
        install(self.data, "0.4.0")
        self.assertEqual(self.hook(root), update("0.4.0", "0.7.0"))

    def test_unreadable_plugin_version_prints_nothing(self):
        # A minified or version-less plugin.json is the plugin's bug: no message at all.
        for manifest in ('{"name":"trazo","version":"0.7.0"}', '{\n  "name": "trazo"\n}\n',
                         '{\n  "version": "0.7.0\\"x"\n}\n'):
            self.assertEqual(self.hook(self.plugin_root(manifest)), "", manifest)


@unittest.skipUnless(PLUGIN.is_dir(), "not in a checkout of the repository")
class PluginTests(unittest.TestCase):
    def test_plugin_manifest(self):
        manifest = json.loads((PLUGIN / "plugin.json").read_text())
        self.assertEqual(manifest["name"], "trazo")
        self.assertEqual(manifest["version"], trazo.__version__)
        for path in manifest["skills"]:
            self.assertTrue(path.startswith("./") and (REPO / path).is_dir(), path)
        self.assertIn("./plugin/skills/", manifest["skills"])
        # Anthropic's directory listing reads it; Claude Code ignores it.
        self.assertTrue(manifest["icon"].startswith("./") and (REPO / manifest["icon"]).is_file())
        self.assertTrue((REPO / "plugin" / "skills" / "setup" / "SKILL.md").is_file())
        # Canonical PEP 440, so setup_status and the hook can compare versions as strings.
        self.assertRegex(manifest["version"], PEP440)
        if os.name != "nt":
            # The hook reads the version line by line: it must find the same one.
            with tempfile.TemporaryDirectory() as tmp:
                data = install(Path(tmp) / "data", "0.0.0")
                self.assertIn(f"the plugin is {manifest['version']};",
                              run_hook(REPO, {"TRAZO_DATA_DIR": str(data)}, Path(tmp)))
        # One command for both systems: plugin/mcp runs here, and Windows finds
        # plugin/mcp.cmd through PATHEXT; neither needs PATH.
        for server in manifest["mcpServers"].values():
            self.assertEqual((server["command"], server["args"]), ("${CLAUDE_PLUGIN_ROOT}/plugin/mcp", ["--no-views"]))
        self.assertTrue(os.access(REPO / "plugin" / "mcp", os.X_OK) or os.name == "nt")
        self.assertTrue((REPO / "plugin" / "mcp.cmd").is_file())
        self.assertIn(b"\r\n", (REPO / "plugin" / "mcp.cmd").read_bytes())  # cmd.exe misreads LF-only batch files

    def test_codex_manifest(self):
        # ChatGPT's Codex reads .codex-plugin/, which also names the install folder by version.
        claude = json.loads((PLUGIN / "plugin.json").read_text())
        codex = json.loads((REPO / ".codex-plugin" / "plugin.json").read_text())
        for key in ("name", "version", "description", "skills"):
            self.assertEqual(codex[key], claude[key], key)
        self.assertEqual(codex["hooks"], "./hooks/hooks.json")
        # Codex resolves neither ${CLAUDE_PLUGIN_ROOT} nor a relative path without cwd ".", the plugin root.
        self.assertEqual(codex["mcpServers"], "./.codex-mcp.json")
        servers = json.loads((REPO / codex["mcpServers"]).read_text())["mcpServers"]
        self.assertEqual(list(servers), list(claude["mcpServers"]))
        for server in servers.values():
            # As in Claude, the preview opens in the browser only (trazo.mcp_app.VIEWS_ENV).
            self.assertEqual((server["command"], server["args"], server["cwd"]), ("./plugin/mcp", ["--no-views"], "."))
        # Codex shows these in its plugin list and composer; paths are from the plugin root.
        for key in ("composerIcon", "logo", "logoDark"):
            path = codex["interface"][key]
            self.assertTrue(path.startswith("./") and (REPO / path).is_file(), path)
        # Codex offers "run setup" only when this names the SKILL.md of a skill it loaded.
        onboarding = codex["extensions"]["com.openai"]["onboardingSkill"]
        self.assertTrue(onboarding.endswith("/SKILL.md") and (REPO / onboarding).is_file(), onboarding)
        self.assertIn(Path(onboarding).parent.parent, [Path(folder) for folder in codex["skills"]])
        # A root plugin.json or mcp.json would make Codex ignore the manifests above.
        for name in ("plugin.json", "mcp.json", ".mcp.json"):
            self.assertFalse((REPO / name).exists(), name)

    def test_skills_name_no_claude_variable(self):
        # Only Claude substitutes ${CLAUDE_PLUGIN_ROOT} in skill text; setup_status gives plugin.root.
        skills = json.loads((PLUGIN / "plugin.json").read_text())["skills"]
        for path in [path for folder in skills for path in (REPO / folder).glob("*/SKILL.md")]:
            self.assertNotIn("CLAUDE_PLUGIN_ROOT", path.read_text(encoding="utf-8"), path)

    def test_hooks(self):
        # The default location, which plugin.json must not list again (it would load twice).
        hooks = json.loads((REPO / "hooks" / "hooks.json").read_text())["hooks"]
        self.assertNotIn("hooks", json.loads((PLUGIN / "plugin.json").read_text()))
        self.assertEqual(list(hooks), ["SessionStart"])
        for group in hooks["SessionStart"]:
            # Not on resume or fork: the transcript already holds the first context.
            self.assertEqual(group["matcher"], "startup|clear|compact")
            for hook in group["hooks"]:
                # One command string: Codex ignores `args` and would run a bare /bin/sh.
                self.assertEqual(set(hook), {"type", "command", "timeout"})
                self.assertEqual(hook["type"], "command")
                self.assertLessEqual(hook["timeout"], 10)
                program, script = shlex.split(hook["command"])
                self.assertEqual(program, "/bin/sh")
                self.assertTrue(script.startswith("${CLAUDE_PLUGIN_ROOT}/"), script)
                self.assertTrue((REPO / script.removeprefix("${CLAUDE_PLUGIN_ROOT}/")).is_file(), script)

    def test_marketplace(self):
        marketplace = json.loads((PLUGIN / "marketplace.json").read_text())
        self.assertTrue(marketplace["owner"]["name"])
        for plugin in marketplace["plugins"]:
            self.assertTrue(plugin["source"].startswith("./"))
            self.assertTrue((REPO / plugin["source"] / ".claude-plugin" / "plugin.json").is_file())


class RepositoryTests(unittest.TestCase):
    def test_no_tracked_file_over_256_kib(self):
        # The Claude plugin directory holds back plugins with larger files.
        try:
            listed = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True)
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("not a git checkout")
        files = [REPO / name for name in listed.stdout.decode().split("\0") if name]
        large = [str(path.relative_to(REPO)) for path in files if path.is_file() and path.stat().st_size > 256 * 1024]
        self.assertEqual(large, [])


class SkillInvocationTests(unittest.TestCase):
    def test_only_three_claude_skills_are_user_invocable(self):
        folders = (REPO / "plugin/skills", REPO / "packages/trazo/src/trazo/skills")
        visible = []
        for folder in folders:
            for skill in folder.glob("*/SKILL.md"):
                header = skill.read_text().split("---", 2)[1]
                if "user-invocable: false" not in header.splitlines():
                    visible.append(skill.parent.name)
        self.assertEqual(sorted(visible), ["close", "comments", "open"])

    def test_open_and_close_require_explicit_invocation_in_both_apps(self):
        for name in ("open", "close", "comments"):
            folder = (REPO / "packages/trazo/src/trazo/skills/comments" if name == "comments"
                      else REPO / "plugin/skills" / name)
            header = (folder / "SKILL.md").read_text().split("---", 2)[1]
            self.assertIn(f"name: {name}", header.splitlines())
            policy = (folder / "agents/openai.yaml").read_text()
            self.assertIn("interface:\n  display_name:", policy)
            if name != "comments":
                self.assertIn("disable-model-invocation: true", header.splitlines())
                self.assertIn("policy:\n  allow_implicit_invocation: false", policy)


if __name__ == "__main__":
    unittest.main()
