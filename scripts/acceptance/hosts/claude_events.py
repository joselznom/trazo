"""Read the stream-json events of `claude -p --output-format stream-json
--verbose --include-hook-events` into a host-neutral Trace plus what only
Claude shows (the init event, hook events, tool results).

Pure functions over text: no process is started here (see claude.py)."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .trace import ToolCall, Trace

MCP_PREFIX = "mcp__plugin_trazo_trazo__"
# --debug-file line for a plugin's MCP server; the server key is "plugin:<plugin>:<server>".
CONNECTED = re.compile(r'MCP server "([^"]+)": Successfully connected \(transport: stdio\) in (\d+)ms')


@dataclass
class ToolOutput:
    name: str       # host-neutral, as in ToolCall
    input: dict
    text: str
    is_error: bool

    def json(self) -> dict:
        """The result of a trazo tool as JSON (they all return one object)."""
        return json.loads(self.text)


@dataclass
class Session:
    """A Trace plus the Claude-only details the n2 scenarios read."""
    trace: Trace
    session_id: str | None = None
    init: dict = field(default_factory=dict)             # the system/init event, {} when none came
    hook_names: list[str] = field(default_factory=list)  # hook_started, e.g. "SessionStart:resume"
    hook_responses: list[dict] = field(default_factory=list)  # hook_name, outcome, exit_code, note
    outputs: list[ToolOutput] = field(default_factory=list)
    result: dict = field(default_factory=dict)           # the result event, {} when none came
    connect_ms: int | None = None                        # trazo's MCP server, from the debug file

    def output_of(self, name: str) -> ToolOutput | None:
        return next((o for o in self.outputs if o.name == name), None)

    @property
    def slash_commands(self) -> list[str]:
        return self.init.get("slash_commands", [])

    def mcp_status(self, server: str = "plugin:trazo:trazo") -> str | None:
        return next((s.get("status") for s in self.init.get("mcp_servers", []) if s.get("name") == server), None)

    def plugin_version(self, name: str = "trazo") -> str | None:
        return next((p.get("version") for p in self.init.get("plugins", []) if p.get("name") == name), None)


def parse_lines(text: str) -> list[dict]:
    """The JSON objects among the lines (a stray non-JSON line is skipped)."""
    events = []
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def neutral_name(name: str, tool_input: dict) -> str:
    """mcp__plugin_trazo_trazo__X -> trazo.X; Skill{skill: trazo:S} -> skill:S; others unchanged."""
    if name.startswith(MCP_PREFIX):
        return "trazo." + name[len(MCP_PREFIX):]
    if name == "Skill":
        return "skill:" + str(tool_input.get("skill", "")).removeprefix("trazo:")
    return name


def hook_note(stdout: str) -> str | None:
    """additionalContext of a hook's JSON output; None for anything else."""
    try:
        return json.loads(stdout)["hookSpecificOutput"]["additionalContext"]
    except (ValueError, KeyError, TypeError):
        return None


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return ""


def connect_ms(debug_log: str, server: str = "plugin:trazo:trazo") -> int | None:
    found = [int(ms) for name, ms in CONNECTED.findall(debug_log) if name == server]
    return found[-1] if found else None


def build_session(events: Iterable[dict], raw_path: Path, duration_s: float, host: str = "claude") -> Session:
    calls: list[ToolCall] = []
    started: dict[str, tuple[str, dict]] = {}   # tool_use_id -> (neutral name, input)
    outputs: list[ToolOutput] = []
    notes: list[str] = []
    session = Session(Trace(host, calls, notes, None, None, duration_s, raw_path))
    for event in events:
        kind, subtype = event.get("type"), event.get("subtype")
        session.session_id = session.session_id or event.get("session_id")
        if kind == "system" and subtype == "init":
            session.init = event
            session.trace.mcp_tools = ["trazo." + tool[len(MCP_PREFIX):] for tool in event.get("tools", [])
                                       if tool.startswith(MCP_PREFIX)]
        elif kind == "system" and subtype == "hook_started":
            session.hook_names.append(event.get("hook_name", ""))
        elif kind == "system" and subtype == "hook_response":
            note = hook_note(event.get("stdout") or "")
            session.hook_responses.append({"hook_name": event.get("hook_name"), "outcome": event.get("outcome"),
                                           "exit_code": event.get("exit_code"), "note": note})
            if note:
                notes.append(note)
        elif kind == "assistant":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    tool_input = block.get("input") or {}
                    name = neutral_name(block.get("name", ""), tool_input)
                    calls.append(ToolCall(name, tool_input))
                    started[block.get("id", "")] = (name, tool_input)
        elif kind == "user":
            content = event.get("message", {}).get("content", [])
            for block in content if isinstance(content, list) else []:
                if block.get("type") == "tool_result" and block.get("tool_use_id") in started:
                    name, tool_input = started[block["tool_use_id"]]
                    outputs.append(ToolOutput(name, tool_input, _text(block.get("content")), bool(block.get("is_error"))))
        elif kind == "result":
            session.result = event
            session.trace.cost_usd = event.get("total_cost_usd")
    session.outputs = outputs
    return session
