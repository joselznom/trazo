# Development of trazo: the package (packages/trazo)
# and the Copier project template (template/, configured by copier.yml).

TRAZO_DIR := $(CURDIR)/packages/trazo
SCRATCH ?= $(CURDIR)/.build/template-test
BLANK_SCRATCH ?= $(CURDIR)/.build/template-blank-test
REPORT_SCRATCH ?= $(CURDIR)/.build/template-report-test
UPDATE_SCRATCH ?= $(CURDIR)/.build/update-test
REFERENCE_DOCX := $(CURDIR)/template/templates/reference_universal.docx
# Run trazo from this checkout inside the template folder.
TEMPLATE_TRAZO := TRAZO_ROOT=$(CURDIR)/template uv run --quiet --project $(TRAZO_DIR) trazo
TRAZO_PYTHON := uv run --quiet --project $(TRAZO_DIR) python
# The acceptance harness (scripts/acceptance/README.md). CHANGED_FROM=<ref> limits a run to the
# scenarios whose catalog `areas` match files changed since <ref>; ACCEPTANCE_ARGS adds run.py
# options (--online makes the data seed the first time, --scenario 'n1-port*', --keep).
ACCEPTANCE := $(TRAZO_PYTHON) scripts/acceptance/run.py
CHANGED_FROM ?=
ACCEPTANCE_ARGS ?=
ACCEPTANCE_FILTER = $(if $(CHANGED_FROM),--changed-from $(CHANGED_FROM)) $(ACCEPTANCE_ARGS)

.PHONY: hooks lint typecheck test test-package test-template test-update test-linux test-acceptance acceptance acceptance-auto template-agents template-diagrams template-reference template-variants clean help

test: test-package test-template

# Lint trazo and the acceptance harness with trazo's ruff config; the pre-commit hook lints
# staged files of trazo only.
lint:
	cd $(TRAZO_DIR) && uv run --quiet ruff check
	cd $(TRAZO_DIR) && uv run --quiet ruff check --config pyproject.toml $(CURDIR)/scripts/acceptance

# mypy on trazo; reports only, nothing runs it automatically yet (see pyproject.toml).
typecheck:
	cd $(TRAZO_DIR) && uv run --quiet mypy

test-package:
	cd $(TRAZO_DIR) && uv run --quiet python -m unittest discover -s tests

# Check the template variants stored in trazo, that `trazo new`
# equals `copier copy` and that its paper builds and previews without git.
# Then make each variant as the desktop apps do, with `trazo new` from
# the stored variants (checked against template/ first), and build it with
# scripts/trazo, never Make: the example article (verify, the strict
# submission check and the build checks), the blank article with a title
# that needs escaping, and the report with and without the example.
NEW_PAPER := uv run --quiet --project $(TRAZO_DIR) trazo new
SCRIPTS_TRAZO := TRAZO_DEV=$(TRAZO_DIR) scripts/trazo
# verify tests the Word files it finds; every test paper must have both.
WORD_FILES = for name in manuscript supplementary; do python3 -m zipfile -t $(1)/output/$$name.docx >/dev/null || exit 1; done

test-template:
	python3 scripts/build-reference-docx.py $(REFERENCE_DOCX) --check
	$(TRAZO_PYTHON) scripts/template-variants.py --check
	$(TRAZO_PYTHON) scripts/check-paper-create.py
	TRAZO_DEV=$(TRAZO_DIR) $(TRAZO_PYTHON) scripts/check-no-git.py
	rm -rf $(SCRATCH)
	$(NEW_PAPER) $(SCRATCH)
	python3 scripts/check-template-build.py --layout $(SCRATCH)
	cd $(SCRATCH) && git init -q && git add -A && git commit -qm init
	cd $(SCRATCH) && $(SCRIPTS_TRAZO) verify
	$(call WORD_FILES,$(SCRATCH))
	cd $(SCRATCH) && $(SCRIPTS_TRAZO) submission check --strict
	cd $(SCRATCH) && $(SCRIPTS_TRAZO) version
	python3 scripts/verify-example.py $(SCRATCH)/output/manuscript.docx
	TRAZO_DEV=$(TRAZO_DIR) python3 scripts/check-template-build.py $(SCRATCH)
	rm -rf $(BLANK_SCRATCH)
	$(NEW_PAPER) --no-example --title 'A "quoted" title: C:\papers 😀' $(BLANK_SCRATCH)
	python3 scripts/check-template-build.py --layout $(BLANK_SCRATCH)
	cd $(BLANK_SCRATCH) && git init -q && git add -A && git commit -qm init
	cd $(BLANK_SCRATCH) && $(SCRIPTS_TRAZO) verify && $(SCRIPTS_TRAZO) doctor
	$(call WORD_FILES,$(BLANK_SCRATCH))
	cd $(BLANK_SCRATCH) && $(SCRIPTS_TRAZO) build --submission && $(SCRIPTS_TRAZO) submission check
	cd $(BLANK_SCRATCH) && ! $(SCRIPTS_TRAZO) submission check --strict > .build/check-submission.txt 2>&1
	grep -E '^  ERROR  manuscript/cover-letter/cover_letter\.md:[0-9]+: \[PLACEHOLDER: ' $(BLANK_SCRATCH)/.build/check-submission.txt
	python3 scripts/check-template-build.py --title $(BLANK_SCRATCH) 'A "quoted" title: C:\papers 😀'
	! grep -rl "example" $(BLANK_SCRATCH)/manuscript $(BLANK_SCRATCH)/data $(BLANK_SCRATCH)/figures/diagrams $(BLANK_SCRATCH)/tables/specs
	rm -rf $(REPORT_SCRATCH)
	$(NEW_PAPER) --no-example --kind report $(REPORT_SCRATCH)
	python3 scripts/check-template-build.py --layout $(REPORT_SCRATCH)
	cd $(REPORT_SCRATCH) && $(SCRIPTS_TRAZO) verify
	$(call WORD_FILES,$(REPORT_SCRATCH))
	test ! -d $(REPORT_SCRATCH)/manuscript/declarations
	test ! -d $(REPORT_SCRATCH)/manuscript/cover-letter
	test ! -e $(REPORT_SCRATCH)/Makefile
	rm -rf $(REPORT_SCRATCH)-example
	$(NEW_PAPER) --kind report $(REPORT_SCRATCH)-example
	python3 scripts/check-template-build.py --layout $(REPORT_SCRATCH)-example
	cd $(REPORT_SCRATCH)-example && $(SCRIPTS_TRAZO) verify
	$(call WORD_FILES,$(REPORT_SCRATCH)-example)

# A paper made by the last release's `trazo new` updates to this checkout's
# stored template with `trazo update` and verifies; then `trazo
# update` must update one between two rendered tags (scripts/update-test.sh).
test-update:
	sh scripts/update-test.sh $(CURDIR) $(UPDATE_SCRATCH)

# Unit tests of the acceptance harness itself (they run no scenario and call no model).
test-acceptance:
	$(TRAZO_PYTHON) -m unittest discover -s scripts/acceptance/tests

# Acceptance levels n1 (the plugin driven like the apps do, offline) and n2 (the real claude and
# codex CLIs in throwaway config; reads the owner's login, see scripts/acceptance/README.md).
# The report path and its summary line are the evidence for a PR.
acceptance:
	$(ACCEPTANCE) --level n1 --level n2 $(ACCEPTANCE_FILTER)

# What pre-push runs (AUTO=push, the default; AUTO=tag with ACCEPTANCE_ARGS="--tag vX.Y.Z"): n1 and n2
# chosen by the files changed since main, passes cached. Nothing to run costs a second.
AUTO ?= push
acceptance-auto:
	$(ACCEPTANCE) --auto $(AUTO) $(ACCEPTANCE_ARGS)

# The CI steps in a Debian container (Docker), on the working tree.
test-linux:
	docker run --rm -v $(CURDIR):/src:ro debian:trixie sh /src/scripts/ci-linux.sh

# Test each pushed commit locally (.githooks/pre-push), in place of CI.
hooks:
	git config core.hooksPath .githooks

# Refresh the generated skill copies and diagram exports shipped in template/.
template-agents:
	$(TEMPLATE_TRAZO) agents

template-diagrams:
	$(TEMPLATE_TRAZO) diagram export --all

# Rewrite the Word reference document from scripts/build-reference-docx.py.
template-reference:
	python3 scripts/build-reference-docx.py $(REFERENCE_DOCX)

# Render the four template variants with Copier into trazo's package data
# (trazo new and update use them; see trazo/variants.py).
template-variants:
	$(TRAZO_PYTHON) scripts/template-variants.py

clean:
	rm -rf .build

help:
	@echo "make hooks           test every pushed commit first (.githooks/pre-push)"
	@echo "make lint            ruff check on trazo and scripts/acceptance"
	@echo "make typecheck       mypy on trazo (reports only)"
	@echo "make test-package    trazo unit tests"
	@echo "make test-template   render the template (with and without the example), verify builds"
	@echo "make test-update     update a v0.6.0 trazo new paper to HEAD, verify; trazo update between tags"
	@echo "make test-acceptance  unit tests of the acceptance harness"
	@echo "make acceptance      acceptance n1 + n2 (CHANGED_FROM=<ref> limits it; ACCEPTANCE_ARGS=--online makes the seed)"
	@echo "make acceptance-auto  automatic acceptance: AUTO=push|tag (pre-push runs it); cached"
	@echo "make test-linux      the CI steps in a Debian container (needs Docker)"
	@echo "make template-agents refresh template/.agents and template/.claude skills"
	@echo "make template-diagrams  re-export template/figures/diagrams"
	@echo "make template-reference rewrite template/templates/reference_universal.docx"
	@echo "make template-variants  store the rendered template variants in trazo"
