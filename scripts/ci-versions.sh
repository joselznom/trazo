# Shared Pandoc toolchain pins for CI and the Linux container check.
PANDOC_VERSION=3.9.0.2
# v0.3.24a is v0.3.24 rebuilt with Pandoc 3.9.0.2; v0.3.24 was built with 3.9
# and warns on every run, which breaks the oracle's stderr comparison.
CROSSREF_VERSION=v0.3.24a
