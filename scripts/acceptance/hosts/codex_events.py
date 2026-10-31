"""Read what a `codex exec --json` session left: the event stream and the rollout.

The stream (stdout JSONL) has the agent's MCP calls. It has no hook output:
a hook's note is a `developer` message in `$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*-<thread id>.jsonl`.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from ..evidence import redact
from .trace import ToolCall

HOOK_NOTE = "hooks.additional_context"


@dataclass
class Events:
    thread_id: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    failure: str | None = None   # turn.failed's message, if the turn failed
    completed: bool = False      # a turn.completed event came: without it the turn was cut short


def parse_exec(text: str) -> Events:
    """Fold the JSONL into a host-neutral summary. An item is reported once, in the order it
    started, with its last state."""
    events, items = Events(), {}
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue  # codex prints "Reading additional input..." style lines on stderr only, but be lenient
        kind = event.get("type")
        if kind == "thread.started":
            events.thread_id = event.get("thread_id")
        elif kind == "turn.completed":
            events.completed = True
        elif kind == "turn.failed":
            events.failure = (event.get("error") or {}).get("message", "turn failed")
        elif kind in ("item.started", "item.updated", "item.completed"):
            item = event["item"]
            items[item["id"]] = item
    for item in items.values():
        if item["type"] == "mcp_tool_call":
            events.tool_calls.append(ToolCall(f"{item['server']}.{item['tool']}", item.get("arguments") or {}))
    return events


def find_rollout(codex_home: Path, thread_id: str) -> Path | None:
    found = sorted((codex_home / "sessions").glob(f"**/rollout-*-{thread_id}.jsonl"))
    return found[-1] if found else None


def scrub_rollouts(codex_home: Path, secrets: Iterable[str]) -> None:
    """Redact `secrets` from every rollout under `codex_home`, in place. Codex writes the agent's messages and
    command output there as they came, so a credential the agent echoed would stay in a file that
    --keep retains and that hook_notes reads. surrogateescape keeps bytes that are not UTF-8 as they were."""
    secrets = list(secrets)
    for path in (codex_home / "sessions").glob("**/rollout-*.jsonl"):
        text = path.read_text(encoding="utf-8", errors="surrogateescape")
        clean = redact(text, secrets)
        if clean != text:
            path.write_text(clean, encoding="utf-8", errors="surrogateescape")


def rollout_messages(rollout: Path) -> list[tuple[str, str]]:
    """(content kind, text) of every message in the rollout; Codex tags each content
    item with where it came from (`hooks.additional_context`, `user.text`, ...)."""
    found = []
    for line in rollout.read_text(encoding="utf-8").splitlines():
        try:
            payload = json.loads(line).get("payload", {})
        except ValueError:
            continue
        if payload.get("type") != "message":
            continue
        kinds = (payload.get("internal_chat_message_metadata_passthrough") or {}).get("content_item_kinds") or []
        for index, content in enumerate(payload.get("content", [])):
            found.append((kinds[index] if index < len(kinds) else "", content.get("text", "")))
    return found


def hook_notes(rollout: Path) -> list[str]:
    """The additionalContext notes the plugin's SessionStart hook gave the agent."""
    return [text for kind, text in rollout_messages(rollout) if kind == HOOK_NOTE]
