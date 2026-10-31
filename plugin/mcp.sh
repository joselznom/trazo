#!/bin/sh
# Start trazo's MCP server (trazo mcp) for the desktop apps: the
# per-user install made by setup, else, in a checkout of this repository,
# the development venv (made by `make test-package`). When neither can
# serve, start plugin/mcp-standin.pl, a stand-in server whose one tool,
# setup_status, offers setup: a failed server would show only "Connection
# closed", and Claude Code caches the failure past setup and the new chat.
# Needs no PATH and writes nothing.
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
# Both apps share this per-user folder.
# $TRAZO_DATA_DIR must be an absolute path (no ~ expansion here).
data=${TRAZO_DATA_DIR:-"$HOME/Library/Application Support/trazo"}
# setup_status compares the installed trazo with this plugin's version.
export TRAZO_PLUGIN_ROOT="$root"
# An install without trazo/mcp.py (0.5.0 and older) has no `mcp` command.
if [ -x "$data/venv/bin/trazo" ]; then
    for server in "$data"/venv/lib/python*/site-packages/trazo/mcp.py; do
        if [ -f "$server" ]; then
            exec "$data/venv/bin/trazo" mcp
        fi
    done
fi
if [ -x "$root/packages/trazo/.venv/bin/trazo" ]; then
    exec "$root/packages/trazo/.venv/bin/trazo" mcp
fi
# The system Perl and its core modules only.
if [ -x /usr/bin/perl ]; then
    unset PERL5LIB PERL5OPT PERLLIB
    exec /usr/bin/perl "$root/plugin/mcp-standin.pl" "$data"
fi
echo "trazo is not set up; ask the agent to set it up" >&2
exit 1
