#!/bin/sh
# The steps of .github/workflows/ci.yml in a Debian container, to test the
# suite on Linux without GitHub Actions: `make test-linux` (needs Docker).
# Runs as root, so it also covers Chrome's --no-sandbox path. Uses the
# working tree (uncommitted changes included) mounted read-only at /src.
set -eu
. /src/scripts/ci-versions.sh
arch=$(dpkg --print-architecture)            # amd64 or arm64
crossref=$( [ "$arch" = arm64 ] && echo Linux-ARM64 || echo Linux-X64 )
apt-get update -qq >/dev/null
apt-get install -y -qq curl git make unzip xz-utils ca-certificates chromium >/dev/null
curl -fsSL -o /tmp/pandoc.deb \
    "https://github.com/jgm/pandoc/releases/download/${PANDOC_VERSION}/pandoc-${PANDOC_VERSION}-1-$arch.deb"
dpkg -i /tmp/pandoc.deb >/dev/null
curl -fsSL "https://github.com/lierdakil/pandoc-crossref/releases/download/${CROSSREF_VERSION}/pandoc-crossref-$crossref.tar.xz" \
    | tar -xJ -C /usr/local/bin
curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1
export PATH="$HOME/.local/bin:$PATH"
git config --global user.email ci@example.com
git config --global user.name ci
git config --global --add safe.directory '*'
mkdir /work
tar -C /src --exclude=./.venv --exclude=./.build --exclude=./packages/trazo/.venv -cf - . \
    | tar -C /work -xf -
cd /work
make test-package
make test-template
