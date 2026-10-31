"""Recognise a provider refusing to serve a model session because the account is out of allowance.

Such a session says nothing about the code under test, so it is a skip with that reason, never a
pass and never a failure of the change. The patterns are the wordings seen from Claude Code and
Codex ("usage limit", "rate limit", quota, credit balance); a new wording is added here."""

from __future__ import annotations

import re

USAGE_LIMIT = re.compile(
    r"usage limit|hit your (?:usage )?limit|limit reached|rate[ _-]?limit|too many requests"
    r"|(?:http|status|error)[ :]*429|insufficient[_ ]quota|exceeded your (?:current )?quota"
    r"|quota (?:exceeded|exhausted)|credit balance is too low|out of (?:credits|extra usage)",
    re.IGNORECASE)


def usage_limit(text: str) -> str:
    """The first line of `text` that reads as a usage-limit message, or "" when none does."""
    for line in text.splitlines():
        if USAGE_LIMIT.search(line):
            return line.strip()[:300]
    return ""
