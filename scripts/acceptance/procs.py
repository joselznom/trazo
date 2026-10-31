"""The processes the acceptance harness started, told from the owner's by process group.

A group is recorded by its leader's pid and start time (the pid alone may be reused). Cleanup in
sandbox.py selects what to signal only through `group_members`."""

from __future__ import annotations

import os
import re
import subprocess
import time
from collections.abc import Iterable
from dataclasses import dataclass

# ps -o pid=,ppid=,pgid=,lstart=,command=: lstart is fixed-width ("Sat Oct 24 15:51:09 2026").
PS_ROW = re.compile(r"\s*(\d+)\s+(\d+)\s+(\d+)\s+(\w{3} \w{3} [ \d]\d \d\d:\d\d:\d\d \d{4})\s+(.*)")


@dataclass(frozen=True)
class Proc:
    pid: int
    ppid: int
    pgid: int
    started: str
    command: str


def process_table() -> list[Proc]:
    listing = subprocess.run(["ps", "-axo", "pid=,ppid=,pgid=,lstart=,command="], capture_output=True, text=True).stdout
    return [Proc(int(m[1]), int(m[2]), int(m[3]), m[4], m[5]) for line in listing.splitlines() if (m := PS_ROW.fullmatch(line))]


def started_at(pid: int) -> str:
    """When the process began (empty once it is gone): with its pid, it tells it from a later one that reuses the pid."""
    done = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True)
    return done.stdout.strip()


def begun_at(lstart: str) -> float | None:
    """`lstart` as a timestamp (ps prints whole seconds in the local zone), None when it does not parse."""
    try:
        return time.mktime(time.strptime(lstart, "%a %b %d %H:%M:%S %Y"))
    except (ValueError, OverflowError):
        return None


def _in_recorded_group(table: list[Proc], pgid: int, recorded: str) -> list[Proc]:
    """The processes of group `pgid` that the harness started, given its leader's start time as recorded.

    A pid that still names a group is never reissued, so a leader pid held by a process that started at
    another time (or that no longer leads the group) means the recorded group ended and the pid was
    reused: the members now carrying that group id are strangers, and none is returned. With the leader
    gone (a shell's background job outlives it), a member is kept only if it began no earlier than the
    leader did, since a process of this run cannot predate its group; an unreadable time drops it."""
    members = [p for p in table if p.pgid == pgid]
    holder = next((p for p in table if p.pid == pgid), None)
    if holder is not None:
        return members if holder.started == recorded and holder.pgid == pgid else []
    leader_began = begun_at(recorded)
    if leader_began is None:
        return []
    return [p for p in members if (began := begun_at(p.started)) is not None and began >= leader_began]


def group_members(table: list[Proc], groups: dict[int, str], also: Iterable[int] = ()) -> list[int]:
    """Processes in the harness's own groups (and `also`), and the descendants of all of those. A group
    counts only when `_in_recorded_group` shows it is the one the harness started: never a process the
    harness did not start."""
    found = {p.pid for pgid, recorded in groups.items() for p in _in_recorded_group(table, pgid, recorded)} | set(also)
    grew = True
    while grew:
        children = {p.pid for p in table if p.ppid in found} - found
        found |= children
        grew = bool(children)
    return sorted(found - {os.getpid()})
