"""What one agent session did, the same for every host, so the n2 scenarios
read Claude and Codex runs the same way."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ToolCall:
    # Host-neutral names: trazo's MCP tools as "trazo.<tool>" (e.g.
    # "trazo.setup_status"), any other tool by the host's own name.
    name: str
    input: dict = field(default_factory=dict)


@dataclass
class Trace:
    host: str  # "claude" or "codex"
    tool_calls: list[ToolCall]
    # additionalContext notes the plugin's SessionStart hook gave the agent.
    hook_notes: list[str]
    # The trazo MCP tools the session had, when the host reports them.
    mcp_tools: list[str] | None
    cost_usd: float | None
    duration_s: float
    raw_path: Path  # the host's raw event log, kept as evidence

    def called(self, name: str) -> bool:
        return any(call.name == name for call in self.tool_calls)


class SessionFailed(Exception):
    """A host session that did not end well (timed out, errored, hit the turn limit). It carries the
    Trace of what happened, so the failure keeps its evidence and its cost; a Trace that came back
    normally is a session that completed."""

    def __init__(self, reason: str, trace: Trace) -> None:
        super().__init__(reason)
        self.trace = trace
