#!/bin/sh
# Set trazo up for this macOS user: uv, Python 3.12, trazo and Pandoc.
#
#   sh plugin/scripts/setup.sh [--offline] [PLUGIN_ROOT]
#
# PLUGIN_ROOT (the folder with packages/trazo) defaults to the one this
# script is in. --offline (the preview's update, trazo.preview_updates)
# downloads nothing: it exits 3 when uv, Python or Pandoc must be
# downloaded, which only setup in the chat does.
# Everything goes into the per-user data folder ($TRAZO_DATA_DIR, else
# ~/Library/Application Support/trazo; it must be an absolute path, ~ is
# not expanded); no PATH, profile or admin change.
# This script only bootstraps uv (pinned below, sha256 from the release's
# assets) and trazo's environment; `trazo setup` downloads Pandoc.
# The setup skill runs it (plugin/skills/setup/SKILL.md).
set -eu

UV_VERSION=0.13.5
offline=
temporary=
if [ "${1:-}" = --offline ]; then offline=1; shift; fi
root=${1:-$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)}
data=${TRAZO_DATA_DIR:-$HOME/Library/Application Support/trazo}
arch=$(uname -m)
# Under Rosetta uname says x86_64 on Apple silicon; install arm64 tools.
if [ "$(/usr/sbin/sysctl -n sysctl.proc_translated 2>/dev/null)" = 1 ]; then arch=arm64; fi
case "$(uname -s) $arch" in
    "Darwin arm64")
        target=aarch64-apple-darwin
        sha256=743f85035a1996ecb350659e01cd9faea9709829be09b1b4b03c73eaf3c9cd73 ;;
    "Darwin x86_64")
        target=x86_64-apple-darwin
        sha256=b0f52b0e3945b4107d89024ea38e625683011fbfc05a0d98f9fa2d6dcee28c66 ;;
    *) echo "setup.sh: macOS only for now (this is $(uname -s) $arch)" >&2; exit 1 ;;
esac
if [ ! -f "$root/packages/trazo/pyproject.toml" ]; then
    echo "setup.sh: $root is not the plugin folder (no packages/trazo)" >&2
    exit 1
fi

mkdir -p "$data"
uv=$data/uv/uv
if ! "$uv" --version 2>/dev/null | grep -q "^uv $UV_VERSION "; then
    if [ -n "$offline" ]; then
        echo "setup.sh: offline: uv $UV_VERSION must be downloaded; nothing was changed" >&2
        exit 3
    fi
    url=https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-$target.tar.gz
    rm -rf "$data"/.setup.*  # left by a run that was killed
    temporary=$(mktemp -d "$data/.setup.XXXXXX")
    trap 'rm -rf "$temporary"' EXIT
    echo "Downloading $url"
    /usr/bin/curl --fail --location --silent --show-error --output "$temporary/uv.tar.gz" "$url"
    if [ "$(/usr/bin/shasum -a 256 "$temporary/uv.tar.gz" | cut -d ' ' -f 1)" != "$sha256" ]; then
        echo "setup.sh: uv-$target.tar.gz does not have the expected sha256; nothing was installed" >&2
        exit 1
    fi
    mkdir "$temporary/uv"
    /usr/bin/tar -xzf "$temporary/uv.tar.gz" -C "$temporary/uv" --strip-components 1
    rm -rf "$data/uv"
    mv "$temporary/uv" "$data/uv"
fi

# Keep uv's Python, cache and tools inside the data folder (the same
# settings as trazo.machine.uv_environment, which builds use).
export TRAZO_DATA_DIR="$data" UV_CACHE_DIR="$data/cache" UV_PYTHON_INSTALL_DIR="$data/python" \
    UV_PYTHON_BIN_DIR="$data/bin" UV_TOOL_DIR="$data/tools" UV_TOOL_BIN_DIR="$data/bin" UV_MANAGED_PYTHON=1
# Offline, uv fails rather than download Python or trazo's build backend:
# exit 3 then too, so setup in the chat downloads them.
if [ -n "$offline" ]; then export UV_OFFLINE=1; fi
run_uv() {
    "$uv" "$@" && return
    status=$?
    if [ -n "$offline" ]; then
        echo "setup.sh: offline: uv $1 failed, probably for a download" >&2
        exit 3
    fi
    exit "$status"
}
# uv runs install_name_tool on every Python it installs, to patch libpython's
# install name (needed only to build native extensions against it, which
# stdlib-only trazo never does). Without the Command Line Tools that is
# Apple's stub, which opens their install dialog; uv only warns when it
# fails, so put a no-op one first on PATH for this step. xcode-select -p
# fails on such a Mac without opening the dialog.
saved_path=$PATH
if ! /usr/bin/xcode-select -p >/dev/null 2>&1; then
    if [ -z "$temporary" ]; then
        temporary=$(mktemp -d "$data/.setup.XXXXXX")
        trap 'rm -rf "$temporary"' EXIT
    fi
    mkdir "$temporary/bin"
    ln -s /usr/bin/true "$temporary/bin/install_name_tool"
    PATH=$temporary/bin:$PATH
fi
run_uv python install --no-bin 3.12
PATH=$saved_path
if [ ! -x "$data/venv/bin/trazo" ]; then
    run_uv venv --quiet --clear --python 3.12 "$data/venv"
else
    run_uv venv --quiet --allow-existing --python 3.12 "$data/venv"
fi
run_uv pip install --quiet --python "$data/venv/bin/python" --reinstall-package trazo "$root/packages/trazo"
"$data/venv/bin/trazo" setup ${offline:+--offline}
