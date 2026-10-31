# Coding standards

<!-- The rules a person or an agent keeps in mind while writing and reviewing code here. Only what the linter and CI can't check; each rule's essence here, longer reasoning in a linked doc. Delete the sections that don't apply. Stack profiles add their own section at the end. -->

## Scope

- Fix the root cause with the smallest coherent change.
- Touch only the files the requested outcome needs; refactors and cleanup of unrelated code get their own task.
- Add an abstraction, configuration option or extension point only for a current need.

## Dependencies

- Build on the existing dependencies: check what they already do before adding a package or reimplementing something.
- Add a dependency only for a current need.

## Tests

- Write the test before the fix or feature and watch it fail for the right reason.
- Test observable behavior at the public boundary. Mock only what you don't own (network, clock, external services).
- Never weaken an existing assertion or expected value to make a test pass. If a test looks wrong, say so and stop.
- A bug fix comes with a regression test that fails on the old code.
- Skip tests that only restate the implementation: getters, wiring, snapshots of your own output.

## Docs

- Update the docstrings next to the code you change, and the public docs the change affects.

## Python

- trazo uses the standard library only (Python 3.12+): never add a runtime dependency. Development tools go in a uv dependency group.
- Tests use `unittest` and run through the Makefile (`make test-package`), which calls uv.
- Ruff is the linter (`make lint`; rules in `packages/trazo/pyproject.toml`). `ruff format` is not used yet: it would grow files already over the size limit.
- mypy reports on the whole package (`make typecheck`); a module that comes back clean joins the strict override in `packages/trazo/pyproject.toml` and must stay clean.
- A name imported only so other modules or tests can reach it (a re-export, `import _support`) carries `# noqa: F401` and says why.
- Annotate every parameter and return value; write unions with PEP 604 (`int | None`).
- f-strings for exception messages and prints.
- Raise project-specific exception classes with clear, actionable messages.
