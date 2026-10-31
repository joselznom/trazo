"""The Codex host's readers: the exec JSONL, the rollout, `plugin list` and the hook trust edit."""

import base64
import json
import os
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import _path  # noqa: F401  (puts scripts/ on sys.path)
from acceptance.evidence import Evidence
from acceptance.hosts import codex, codex_appserver, codex_events
from acceptance.hosts.trace import SessionFailed
from acceptance.registry import Skip
from acceptance.sandbox import Sandbox


def jsonl(*events: dict) -> str:
    return "\n".join(json.dumps(e) for e in events)


def item(kind: str, id: str, **fields) -> dict:
    return {"type": kind, "item": {"id": id, **fields}}


SESSION = jsonl(
    {"type": "thread.started", "thread_id": "T1"},
    {"type": "turn.started"},
    item("item.completed", "i0", type="agent_message", text="Checking."),
    item("item.started", "i1", type="mcp_tool_call", server="trazo", tool="setup_status", arguments={}, status="in_progress"),
    item("item.completed", "i1", type="mcp_tool_call", server="trazo", tool="setup_status", arguments={}, status="completed"),
    item("item.started", "i2", type="command_execution", command="/bin/zsh -lc 'pwd && ls'", status="in_progress"),
    item("item.completed", "i2", type="command_execution", command="/bin/zsh -lc 'pwd && ls'", exit_code=0, status="completed"),
    item("item.completed", "i3", type="command_execution", status="completed",
         command="/bin/zsh -lc 'cat /x/plugin/skills/setup/SKILL.md'"),
    item("item.started", "i4", type="command_execution", command="sh setup.sh", status="in_progress"),  # never completed
    item("item.completed", "i5", type="mcp_tool_call", server="other", tool="t", arguments={"a": 1}),
    item("item.completed", "i6", type="agent_message", text="Done."),
    {"type": "turn.completed", "usage": {}},
)


class ExecParserTests(unittest.TestCase):
    def test_mcp_tool_calls_and_the_end_of_the_turn(self):
        events = codex_events.parse_exec(SESSION)
        self.assertEqual(events.thread_id, "T1")
        self.assertEqual([c.name for c in events.tool_calls], ["trazo.setup_status", "other.t"])
        self.assertIsNone(events.failure)
        self.assertTrue(events.completed)

    def test_a_started_item_is_reported_once_with_its_last_state(self):
        events = codex_events.parse_exec(SESSION)
        self.assertEqual(sum(c.name == "trazo.setup_status" for c in events.tool_calls), 1)

    def test_a_failed_turn_and_noise_lines(self):
        text = "not json\n" + jsonl({"type": "turn.failed", "error": {"message": "401 Unauthorized"}})
        self.assertEqual(codex_events.parse_exec(text).failure, "401 Unauthorized")

def message(role: str, text: str, kind: str) -> dict:
    return {"type": "response_item", "payload": {
        "type": "message", "role": role, "content": [{"type": "input_text", "text": text}],
        "internal_chat_message_metadata_passthrough": {"content_item_kinds": [kind]}}}


class RolloutTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        day = self.home / "sessions/2026/10/29"
        day.mkdir(parents=True)
        self.rollout = day / "rollout-2026-10-29T10-10-50-T1.jsonl"
        self.rollout.write_text(jsonl(
            {"type": "session_meta", "payload": {"id": "T1"}},
            message("developer", "base instructions", "developer.text"),
            message("developer", "The trazo plugin is not set up.", codex_events.HOOK_NOTE),
            message("user", "hi", "user.text"),
        ), encoding="utf-8")

    def test_finds_the_rollout_by_thread_id(self):
        self.assertEqual(codex_events.find_rollout(self.home, "T1"), self.rollout)
        self.assertIsNone(codex_events.find_rollout(self.home, "T2"))

    def test_hook_notes_are_only_the_hook_messages(self):
        self.assertEqual(codex_events.hook_notes(self.rollout), ["The trazo plugin is not set up."])

class PluginListTests(unittest.TestCase):
    LISTING = """Marketplace `trazo`
/private/tmp/x/.claude-plugin/marketplace.json

PLUGIN       STATUS              VERSION  SOURCE
trazo@trazo  installed, enabled  0.8.1    /private/tmp/x

Marketplace `openai-curated-remote`
PLUGIN                       STATUS              VERSION  SOURCE
gmail@openai-curated-remote  installed, enabled  0.1.10   plugin_connector_1p_95d
"""

    def test_only_the_trazo_row_is_kept(self):
        (row,) = codex.parse_plugin_list(self.LISTING)
        self.assertEqual((row.status, row.version, row.source, row.installed),
                         ("installed, enabled", "0.8.1", "/private/tmp/x", True))

    def test_a_plugin_that_is_not_installed_has_no_version(self):
        text = "trazo@trazo  not installed           /private/tmp/x\n"
        (row,) = codex.parse_plugin_list(text)
        self.assertEqual((row.installed, row.version, row.source), (False, None, "/private/tmp/x"))


class TrustTests(unittest.TestCase):
    def test_the_current_hash_splits_into_the_two_stored_values(self):
        hook = {"key": "trazo@trazo:hooks/hooks.json:session_start:0:0", "currentHash": "sha256:aa/sha256:bb"}
        (edit,) = codex_appserver.trust_edit([hook])["edits"]
        self.assertEqual(edit["keyPath"], "hooks.state")  # not the dotted key: it contains "hooks.json"
        self.assertEqual(edit["value"][hook["key"]], {"trusted_hash": "sha256:aa", "trusted_content_hash": "sha256:bb"})

    def test_hooks_of_filters_by_plugin(self):
        listing = {"data": [{"hooks": [{"pluginId": "a@b", "key": 1}, {"pluginId": "trazo@trazo", "key": 2}]}]}
        self.assertEqual(codex_appserver.hooks_of(listing, "trazo@trazo"), [{"pluginId": "trazo@trazo", "key": 2}])

    def test_the_approval_covers_one_plugin_server_only(self):
        (edit,) = codex_appserver.approve_edit("trazo@trazo", "trazo")["edits"]
        self.assertEqual(edit["keyPath"], "plugins")  # the id has an "@": written as a table, like the hooks
        self.assertEqual(edit["value"], {"trazo@trazo": {"mcp_servers": {"trazo": {"default_tools_approval_mode": "approve"}}}})


def jwt(exp: float) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"h.{body}.s"


class LoginExpiryTests(unittest.TestCase):
    def test_seconds_left_reads_the_token_expiry(self):
        left = codex.seconds_left({"tokens": {"access_token": jwt(time.time() + 7200)}})
        self.assertTrue(7100 < left <= 7200)

    def test_no_token_or_an_unreadable_one_means_unknown(self):
        self.assertIsNone(codex.seconds_left({"OPENAI_API_KEY": "k"}))
        self.assertIsNone(codex.seconds_left({"tokens": {"access_token": "garbage"}}))


class CredentialsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.real, self.sandbox_home = Path(tmp.name, "real"), Path(tmp.name, "sandbox")
        for folder in (self.real, self.sandbox_home):
            folder.mkdir()
        self.sb = SimpleNamespace(codex_home=self.sandbox_home, evidence=Evidence())
        patch = mock.patch.dict(os.environ, {"CODEX_HOME": str(self.real)})
        patch.start()
        self.addCleanup(patch.stop)

    def login(self, **auth):
        (self.real / "auth.json").write_text(json.dumps(auth), encoding="utf-8")

    def test_no_auth_file_skips(self):
        with self.assertRaises(Skip), codex.credentials(self.sb):
            pass

    def test_a_token_about_to_expire_skips_without_copying(self):
        self.login(tokens={"access_token": jwt(time.time() + 60)})
        with self.assertRaises(Skip), codex.credentials(self.sb):
            pass
        self.assertFalse((self.sandbox_home / "auth.json").exists())

    def test_the_copy_is_private_and_deleted_even_when_the_run_fails(self):
        self.login(tokens={"access_token": jwt(time.time() + 86400)})
        target = self.sandbox_home / "auth.json"
        with self.assertRaises(RuntimeError), codex.credentials(self.sb):
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            raise RuntimeError("the run failed")
        self.assertFalse(target.exists())
        self.assertEqual(self.sb.evidence.items, [])  # nothing about the login in the evidence

    def test_a_login_rewritten_by_codex_is_reported(self):
        self.login(tokens={"access_token": jwt(time.time() + 86400)})
        with codex.credentials(self.sb):
            (self.sandbox_home / "auth.json").write_text("{}", encoding="utf-8")
        self.assertIn("rewrote the copied login", self.sb.evidence.items[0]["text"])


SECRETS = {"access_token": jwt(time.time() + 86400) + "-ACCESS", "refresh_token": "rt-SYNTHETIC-refresh-token",
           "id_token": "eyJ-SYNTHETIC-id-token"}


class SessionOutcomeTests(unittest.TestCase):
    """A turn that did not complete is a failed session, and the login's tokens reach no output."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        real = Path(tmp.name, "real")
        real.mkdir()
        (real / "auth.json").write_text(json.dumps({"tokens": SECRETS}), encoding="utf-8")
        self.evidence = Evidence()
        self.sb = Sandbox("unit-codex-session", self.evidence)
        self.addCleanup(self.sb.cleanup)
        self.sessions = Path(tmp.name, "sessions")
        for patch in (mock.patch.dict(os.environ, {"CODEX_HOME": str(real)}), mock.patch.object(codex, "SESSIONS", self.sessions),
                      mock.patch.object(codex, "codex_bin", return_value="/usr/bin/codex"),
                      mock.patch.object(codex, "mcp_tools", return_value=["setup_status"])):
            patch.start()
            self.addCleanup(patch.stop)

    def stream(self, *, completed=True, leak=True):
        mark = " ".join(SECRETS.values()) if leak else "nothing"
        events = [{"type": "thread.started", "thread_id": "T1"}, item("item.completed", "i0", type="agent_message", text=f"seen {mark}"),
                  item("item.completed", "i1", type="command_execution", command=f"echo {SECRETS['refresh_token']}", status="completed")]
        return jsonl(*events, *([{"type": "turn.completed", "usage": {}}] if completed else []))

    def session(self, outcome):
        if isinstance(outcome, BaseException):
            run = mock.patch.object(Sandbox, "_run_in_own_group", side_effect=outcome)
        else:
            run = mock.patch.object(Sandbox, "_run_in_own_group", return_value=outcome)
        with run:
            return codex.session(self.sb, None, "p", cwd=self.sb.work)

    def finished(self, stdout, code=0, stderr=""):
        return subprocess.CompletedProcess([], code, stdout, stderr)

    def assert_clean(self, *texts):
        everything = json.dumps(self.evidence.items) + "".join(str(t) for t in texts) + "".join(
            p.read_text() for p in self.sessions.iterdir())
        for name, value in SECRETS.items():
            self.assertNotIn(value, everything, name)

    def test_a_completed_turn_is_a_trace_and_is_clean(self):
        trace = self.session(self.finished(self.stream()))
        self.assertIn("[secret]", trace.raw_path.read_text())
        self.assert_clean([vars(c) for c in trace.tool_calls], trace.raw_path.read_text())
        self.assertIsNone(trace.cost_usd)  # Codex reports none

    def test_a_turn_without_turn_completed_is_a_failed_session(self):
        with self.assertRaises(SessionFailed) as raised:
            self.session(self.finished(self.stream(completed=False)))
        self.assertIn("cut short", str(raised.exception))
        self.assert_clean(str(raised.exception), raised.exception.trace.raw_path.read_text())

    def test_a_failed_turn_keeps_its_clean_message(self):
        failure = jsonl({"type": "turn.failed", "error": {"message": f"401 {SECRETS['id_token']}"}})
        with self.assertRaises(SessionFailed) as raised:
            self.session(self.finished(failure, code=1, stderr=f"stderr {SECRETS['access_token']}"))
        self.assertIn("401", str(raised.exception))
        self.assert_clean(str(raised.exception))

    def test_a_timeout_is_a_failed_session_with_clean_partial_output(self):
        error = subprocess.TimeoutExpired(["codex"], 5, output=self.stream(completed=False).encode(),
                                          stderr=SECRETS["access_token"].encode())
        with self.assertRaises(SessionFailed) as raised:
            self.session(error)
        cause = raised.exception.__cause__  # the exception that travels on: sb.run raises a redacted copy
        self.assertIsInstance(cause, subprocess.TimeoutExpired)
        self.assert_clean(str(raised.exception), cause, cause.cmd, cause.stdout, cause.stderr,
                          raised.exception.trace.raw_path.read_text())

    def write_rollout(self):
        """What Codex leaves in its home while a session runs: a hook note and an echoed credential."""
        day = self.sb.codex_home / "sessions/2026/10/29"
        day.mkdir(parents=True, exist_ok=True)
        self.rollout = day / "rollout-2026-10-29T10-10-50-T1.jsonl"
        self.rollout.write_text(jsonl(
            message("developer", f"Note {SECRETS['refresh_token']}", codex_events.HOOK_NOTE),
            message("user", f"echo {SECRETS['access_token']}", "user.text")), encoding="utf-8")

    def session_with_rollout(self, outcome):
        def run(*args, **kwargs):
            self.write_rollout()
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        with mock.patch.object(Sandbox, "_run_in_own_group", side_effect=run):
            return codex.session(self.sb, None, "p", cwd=self.sb.work)

    def assert_rollout_clean(self):
        kept = self.rollout.read_text(encoding="utf-8")
        for name, value in SECRETS.items():
            self.assertNotIn(value, kept, name)
        self.assertIn("[secret]", kept)

    def test_the_rollout_is_redacted_in_place_and_so_are_the_notes_read_from_it(self):
        trace = self.session_with_rollout(self.finished(self.stream()))
        self.assert_rollout_clean()
        self.assertEqual(trace.hook_notes, ["Note [secret]"])

    def test_the_rollout_of_a_failed_turn_is_redacted_too(self):
        with self.assertRaises(SessionFailed):
            self.session_with_rollout(self.finished(self.stream(completed=False)))
        self.assert_rollout_clean()

    def test_the_rollout_of_a_timed_out_session_is_redacted_too(self):
        with self.assertRaises(SessionFailed):
            self.session_with_rollout(subprocess.TimeoutExpired(["codex"], 5, output=self.stream(completed=False).encode()))
        self.assert_rollout_clean()

    def test_every_token_of_the_login_is_a_secret(self):
        self.assertCountEqual(codex.login_secrets({"OPENAI_API_KEY": "sk-key", "tokens": {**SECRETS, "account_id": "a"}}),
                              ["sk-key", *SECRETS.values()])


if __name__ == "__main__":
    unittest.main()
