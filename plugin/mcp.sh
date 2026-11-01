#!/bin/sh
# Start trazo's MCP server (trazo mcp) for the desktop apps: the
# per-user install made by setup, else, in a checkout of this repository,
# the development venv (made by `make test-package`). When neither can
# serve, start plugin/mcp-standin.pl, a stand-in server whose one tool,
# setup_status, offers setup: a failed server would show only "Connection
# closed", and Claude Code caches the failure past setup and the new chat.
# Needs no PATH and writes nothing.
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
# Both apps share this per-user folder (trazo.machine.data_dir).
# $TRAZO_DATA_DIR must be an absolute path (no ~ expansion here).
# uname by full path, since PATH may lack it: /bin on Linux without merged /usr.
uname=/usr/bin/uname
[ -x "$uname" ] || uname=/bin/uname
if [ "$("$uname" -s 2>/dev/null)" = Darwin ]; then
    default="$HOME/Library/Application Support/trazo"
else
    default="${XDG_DATA_HOME:-$HOME/.local/share}/trazo"
fi
data=${TRAZO_DATA_DIR:-$default}
# setup_status compares the installed trazo with this plugin's version.
export TRAZO_PLUGIN_ROOT="$root"
# Both manifests pass --no-views, so the preview opens in the app's browser
# only: the Claude app draws MCP Apps views but refuses the tool calls they
# make ("IPC not available for MCP tool call"), and the ChatGPT app draws
# them for some accounts only, as a second window beside the browser.
if [ "${1-}" = --no-views ]; then
    export TRAZO_MCP_VIEWS=0
fi
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
