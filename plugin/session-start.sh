#!/bin/sh
# SessionStart hook (hooks/hooks.json): tell the agent when trazo is not
# set up, or when the installed trazo is not this plugin's version (older:
# run setup; newer: ask for a plugin update, since setup would downgrade).
# Offline, starts no Python, prints nothing when all is well, always exits 0.
# Set up means: the data folder's venv has trazo (its version is the name
# of its one trazo-VERSION.dist-info folder; more than one is broken) and
# a non-empty machine.json, which `trazo setup` writes last.
# trazo.machine.installed_version follows the same rule; setup_status is
# stricter (it also wants uv and Pandoc), this hook checks install and
# version only. macOS and Linux, like plugin/scripts/setup.sh.
# $TRAZO_DATA_DIR must be an absolute path (no ~ expansion here).
case $(uname -s 2>/dev/null) in
    Darwin) default="$HOME/Library/Application Support/trazo" ;;
    Linux) default="${XDG_DATA_HOME:-$HOME/.local/share}/trazo" ;;
    *) exit 0 ;;
esac

# Exit 0 when $1 is a newer release than $2, both digits and dots ("0.10.1");
# other versions (a pre-release) are not ordered, as trazo.machine.release_key.
newer() {
    case $1 in ''|.*|*.|*..*|*[!0-9.]*) return 1 ;; esac
    case $2 in ''|.*|*.|*..*|*[!0-9.]*) return 1 ;; esac
    a=$1. b=$2.
    while [ -n "$a$b" ]; do
        x=${a%%.*} y=${b%%.*}
        a=${a#*.} b=${b#*.}
        [ "${x:-0}" -gt "${y:-0}" ] && return 0
        [ "${x:-0}" -lt "${y:-0}" ] && return 1
    done
    return 1
}
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
data=${TRAZO_DATA_DIR:-$default}

# The top-level "version" (two-space indent); tests check it matches json.load.
plugin=$(sed -n 's/^  "version": *"\([^"]*\)".*/\1/p' "$root/.claude-plugin/plugin.json" 2>/dev/null | head -n 1)
# Only version-like text goes into the JSON below.
case $plugin in *[!A-Za-z0-9.+_-]*) plugin= ;; esac
# A plugin.json this cannot read is the plugin's own bug, not the user's.
[ -n "$plugin" ] || exit 0

installed=
if [ -x "$data/venv/bin/trazo" ]; then
    for info in "$data"/venv/lib/python*/site-packages/trazo-*.dist-info; do
        if [ -d "$info" ]; then
            if [ -n "$installed" ]; then
                installed=unknown
                break
            fi
            installed=${info##*/trazo-}
            installed=${installed%.dist-info}
        fi
    done
fi
case $installed in *[!A-Za-z0-9.+_-]*) installed=unknown ;; esac

if newer "$installed" "$plugin"; then
    # Both apps share the install; the other app's newer plugin installed it.
    context="trazo $installed is installed, newer than this plugin ($plugin): tell the user to update the trazo plugin in this app. Do not run the trazo:setup skill, which would install the older trazo. This matters when the user asks for trazo or paper work."
elif [ -z "$installed" ] || [ "$installed" = unknown ] || [ ! -s "$data/machine.json" ]; then
    context="The trazo plugin is not set up on this computer: its MCP tools are unavailable until the trazo:setup skill runs. When the user asks for trazo or paper work, run that skill; that request is the go-ahead."
elif [ "$installed" != "$plugin" ]; then
    context="trazo $installed is installed but the plugin is $plugin; the trazo:setup skill reinstalls trazo from the plugin's files and downloads only what is missing. When the user asks for trazo or paper work, run that skill; that request is the go-ahead."
else
    exit 0
fi
printf '{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "%s"}}\n' "$context"
exit 0
