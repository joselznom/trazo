"""The claude host: event parsing on trimmed real lines, the environment and the keychain lookup."""

import hashlib
import json
import subprocess
import time
import unittest
from pathlib import Path
from unittest import mock

import _path  # noqa: F401
from acceptance.evidence import Evidence
from acceptance.hosts import claude, claude_events
from acceptance.registry import Skip
from acceptance.sandbox import Sandbox

NOTE = "The trazo plugin is not set up on this computer."
HOOK = {"type": "system", "subtype": "hook_response", "hook_name": "SessionStart:startup", "outcome": "success",
        "exit_code": 0, "session_id": "s1",
        "stdout": json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": NOTE}})}
INIT = {"type": "system", "subtype": "init", "session_id": "s1", "slash_commands": ["trazo:open"],
        "tools": ["Bash", "Skill", "mcp__plugin_trazo_trazo__setup_status", "mcp__plugin_trazo_trazo__build"]}


def assistant(*blocks):
    return {"type": "assistant", "message": {"content": list(blocks)}}


def tool_use(id, name, **tool_input):
    return {"type": "tool_use", "id": id, "name": name, "input": tool_input}


def tool_result(id, content, is_error=None):
    block = {"type": "tool_result", "tool_use_id": id, "content": content}
    if is_error:
        block["is_error"] = True
    return {"type": "user", "message": {"content": [block]}}


EVENTS = [
    HOOK, INIT,
    assistant(tool_use("a", "Skill", skill="trazo:setup")),
    tool_result("a", "Launching skill: trazo:setup"),
    assistant(tool_use("b", "mcp__plugin_trazo_trazo__setup_status")),
    tool_result("b", json.dumps({"next": "setup"})),
    assistant({"type": "text", "text": "Getting trazo ready."}, tool_use("c", "Bash", command="sh setup.sh; echo $?")),
    tool_result("c", "This Bash command contains multiple operations", is_error=True),
    {"type": "result", "subtype": "success", "result": "Trazo is ready.", "total_cost_usd": 0.0031,
     "permission_denials": [{"tool_name": "Bash", "tool_input": {"command": "sh setup.sh; echo $?"}}]},
]


class ParsingTests(unittest.TestCase):
    def setUp(self):
        self.session = claude_events.build_session(EVENTS, Path("raw.jsonl"), 2.5)

    def test_tool_calls_have_host_neutral_names(self):
        self.assertEqual([c.name for c in self.session.trace.tool_calls],
                         ["skill:setup", "trazo.setup_status", "Bash"])
        self.assertTrue(self.session.trace.called("trazo.setup_status"))
        self.assertEqual(self.session.trace.tool_calls[0].input, {"skill": "trazo:setup"})

    def test_a_tool_result_that_is_an_error_is_marked(self):
        self.assertTrue(self.session.output_of("Bash").is_error)

    def test_hook_notes_cost_and_tools(self):
        trace = self.session.trace
        self.assertEqual((trace.hook_notes, trace.cost_usd, trace.duration_s), ([NOTE], 0.0031, 2.5))
        self.assertEqual(trace.mcp_tools, ["trazo.setup_status", "trazo.build"])
        self.assertEqual((self.session.session_id, self.session.hook_responses[0]["hook_name"]), ("s1", "SessionStart:startup"))

    def test_tool_results_are_paired_with_their_calls(self):
        self.assertEqual(self.session.output_of("trazo.setup_status").json(), {"next": "setup"})

    def test_a_session_with_no_init_has_no_tool_list(self):
        self.assertIsNone(claude_events.build_session([HOOK], Path("r"), 1).trace.mcp_tools)

    def test_non_json_lines_are_skipped(self):
        text = "warning: x\n" + json.dumps(INIT) + "\n\n[1, 2]\n"
        self.assertEqual(claude_events.parse_lines(text), [INIT])

    def test_a_hook_that_is_silent_gives_no_note(self):
        self.assertIsNone(claude_events.hook_note(""))
        self.assertIsNone(claude_events.hook_note('{"other": 1}'))

    def test_other_skills_keep_their_names(self):
        self.assertEqual(claude_events.neutral_name("Skill", {"skill": "scientific-writing-style"}),
                         "skill:scientific-writing-style")
        self.assertEqual(claude_events.neutral_name("Edit", {}), "Edit")

    def test_connection_time_comes_from_the_trazo_server_line(self):
        log = ('2026-10-29T09:10:13.334Z [DEBUG] MCP server "plugin:trazo:trazo": Successfully connected '
               '(transport: stdio) in 40ms\n[DEBUG] MCP server "other": Successfully connected (transport: stdio) in 9ms\n')
        self.assertEqual(claude_events.connect_ms(log), 40)
        self.assertIsNone(claude_events.connect_ms("nothing here"))


class EnvironmentTests(unittest.TestCase):
    def test_child_env_drops_inherited_variables_and_adds_the_token(self):
        sb = Sandbox("unit-claude-env", Evidence())
        self.addCleanup(sb.cleanup)
        env = claude.child_env(sb, "tok", {"CLAUDE_CODE_SESSION": "1", "AI_AGENT": "x", "CLAUDECODE": "1", "KEEP": "y"})
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "tok")
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], str(sb.claude_config))
        self.assertEqual(env["KEEP"], "y")
        self.assertEqual([n for n in env if n.startswith(("CLAUDE", "AI_AGENT"))],
                         ["CLAUDE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN"])


class KeychainTests(unittest.TestCase):
    def lookup(self, stdout="", code=0, environ=None):
        done = subprocess.CompletedProcess([], code, stdout, "")
        with mock.patch.object(claude.subprocess, "run", return_value=done) as run:
            try:
                return claude.read_token(environ or {"CLAUDE_CONFIG_DIR": "/some/dir"}), run
            except Skip as skip:
                return skip, run

    def test_the_item_name_hashes_the_config_dir(self):
        digest = hashlib.sha256(b"/some/dir").hexdigest()[:8]
        self.assertEqual(claude.keychain_services(Path("/some/dir"), custom=True), [f"Claude Code-credentials-{digest}"])
        self.assertEqual(claude.keychain_services(Path("/some/dir"), custom=False)[1], "Claude Code-credentials")

    def test_only_a_read_of_the_credentials_item_can_be_built(self):
        self.assertEqual(claude.keychain_read_argv("Claude Code-credentials-62e4bd9c"),
                         ["/usr/bin/security", "find-generic-password", "-s", "Claude Code-credentials-62e4bd9c", "-w"])
        for other in ("login", "Claude Code-credentialsX", "", "Safari"):
            with self.assertRaises(ValueError):
                claude.keychain_read_argv(other)

    def test_every_security_call_of_the_host_is_a_read(self):
        sources = [Path(claude.__file__), Path(claude_events.__file__)]
        text = "".join(p.read_text(encoding="utf-8") for p in sources)
        self.assertEqual(text.count("find-generic-password"), 1)
        for verb in ("delete-", "add-", "set-", "import", "create-keychain", "unlock-keychain", "lock-keychain",
                     "default-keychain", "login-keychain", "list-keychains", "setup-token", "logout", "/login"):
            self.assertNotIn(f'"{verb}', text)

    def test_the_access_token_is_read_from_the_item(self):
        stored = json.dumps({"claudeAiOauth": {"accessToken": "secret", "expiresAt": (time.time() + 3600) * 1000}})
        token, run = self.lookup(stored)
        self.assertEqual(token, "secret")
        self.assertEqual(run.call_args.args[0][3], f"Claude Code-credentials-{hashlib.sha256(b'/some/dir').hexdigest()[:8]}")

    def test_missing_or_expired_logins_skip_with_a_reason(self):
        missing, _ = self.lookup(code=44)
        self.assertIsInstance(missing, Skip)
        self.assertIn("log in", str(missing))
        old = json.dumps({"claudeAiOauth": {"accessToken": "secret", "expiresAt": 1000}})
        expired, _ = self.lookup(old)
        self.assertIsInstance(expired, Skip)
        self.assertIn("expired", str(expired))
        self.assertNotIn("secret", f"{missing} {expired}")


TOKEN = "sk-ant-oat01-SYNTHETIC-token-value"


def success_result(**fields):
    return {"type": "result", "subtype": "success", "is_error": False, "result": "ok", "total_cost_usd": 0.01, **fields}


class RedactionTests(unittest.TestCase):
    """The OAuth token the host put in the child's environment must not reach any output."""

    def setUp(self):
        self.evidence = Evidence()
        self.sb = Sandbox("unit-claude-redact", self.evidence)
        self.addCleanup(self.sb.cleanup)
        self.host = claude.Claude.__new__(claude.Claude)
        self.host.sb, self.host.binary, self.host.token = self.sb, "/usr/bin/claude", TOKEN
        self.host.env = claude.child_env(self.sb, TOKEN)

    def everything(self, session):
        trace = session.trace
        return json.dumps([self.evidence.items, [vars(c) for c in trace.tool_calls],
                           session.result, session.hook_responses, [vars(o) for o in session.outputs]]) + \
            "".join(p.read_text() for p in (self.sb.root / "logs").iterdir())

    def stdout(self):
        events = [INIT, assistant({"type": "text", "text": f"the token is {TOKEN}"}, tool_use("c", "Bash", command=f"echo {TOKEN}")),
                  tool_result("c", f"echoed {TOKEN}"), success_result(result=f"done {TOKEN}")]
        return "\n".join(json.dumps(e) for e in events)

    def fake_run(self, stdout, code=0):
        def run(argv, **options):
            if "--debug-file" in argv:
                Path(argv[argv.index("--debug-file") + 1]).write_text(f"[DEBUG] env CLAUDE_CODE_OAUTH_TOKEN={TOKEN}\n")
            return subprocess.CompletedProcess(argv, code, stdout, f"stderr {TOKEN}")
        return run

    def test_a_finished_session_is_clean_at_every_boundary(self):
        with mock.patch.object(claude.subprocess, "run", side_effect=self.fake_run(self.stdout())):
            session = self.host.run_session(None, "p")
        self.assertNotIn(TOKEN, self.everything(session))
        self.assertIn("[secret]", session.result["result"])

    def test_a_timeout_is_clean_at_every_boundary(self):
        partial = self.stdout().encode()
        error = subprocess.TimeoutExpired(["claude"], 5, output=partial, stderr=f"e {TOKEN}".encode())
        def timed_out(argv, **_):
            Path(argv[argv.index("--debug-file") + 1]).write_text(f"token {TOKEN}", encoding="utf-8")
            raise error
        with mock.patch.object(claude.subprocess, "run", side_effect=timed_out):
            with self.assertRaises(subprocess.TimeoutExpired):
                self.host.run_session(None, "p")
        self.assertNotIn(TOKEN, json.dumps(self.evidence.items))
        for log in (self.host.sb.root / "logs").iterdir():
            self.assertNotIn(TOKEN, log.read_text(encoding="utf-8"))
        self.assertNotIn(TOKEN, f"{error.stdout}{error.stderr}")

    def test_an_error_message_is_clean(self):
        with mock.patch.object(claude.subprocess, "run", side_effect=self.fake_run(self.stdout(), code=2)):
            with self.assertRaises(AssertionError) as raised:
                self.host.run(["-p", "x"], check=True)
        self.assertNotIn(TOKEN, str(raised.exception))


if __name__ == "__main__":
    unittest.main()
