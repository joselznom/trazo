"""Build a project without Make: fixed stages plus the steps in trazo.toml.

    trazo build [--doc NAME] [--submission] [--force] [--env K=V] [--toc ...]
    trazo run STEP [--force] [--env K=V]
    trazo verify [--env K=V]

Stages, in order: values (scripts/build_values.py) -> tables (the table data
generator, then `table render --all`) -> diagrams (export when stale) ->
expand (expand and numbering, per document) -> docx (Pandoc, after-docx
steps, docx-finish, per document). An image Pandoc cannot find fails the docx
stage (Pandoc itself only warns). [[build.steps]] run before the stage
named by `before` or, with after = "docx", on each Word file (see
trazo.project). A nonempty [commands] entry runs as written instead of
its stage.

Each stage and step is skipped when the hash of its command and input
contents equals the one recorded in <build>/steps.json and its outputs
exist and are nonempty; a missing output runs it again. [build.env] is part
of the hash of every project script (steps, the values and tables
generators, and the Word build when it has after-docx steps); the Word
build also hashes the trazo and Pandoc versions and the outputs of
every step. A stage's
record is dropped before it runs, and the outputs a failed step changed are
removed, so a failure never looks fresh. A command run as written
receives the --env overrides in $TRAZO_BUILD_ENV, so a trazo build it
calls hashes and runs the same environment.

Steps run as `<project .venv python> <run...>` from the project root,
without a shell, with VIRTUAL_ENV and PATH pointing at .venv; the
environment is created with `uv sync --locked` when uv.lock changes or
.venv is missing.
"""

from __future__ import annotations

import argparse
import fnmatch
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import __version__
from .build_lock import child_env, locked
from .project import ASSETS_DIR, PROJECT, Project
from .review_storage import write_atomic, write_json

STATE_NAME = "steps.json"
BUILD_ENV = "TRAZO_BUILD_ENV"
TARGET = re.compile(r"\s*(?:<([^>\n]+)>|([^\s)]+))")


MISSING_RESOURCE = re.compile(r"Could not fetch resource '?(.+?)'?(?::\s|$)", re.M)


def missing_resources(stderr: str) -> str | None:
    """The error for images Pandoc could not find (it only warns, and writes
    the image's description), or None."""
    missing = [urllib.parse.unquote(name) for name in MISSING_RESOURCE.findall(stderr)]
    if not missing:
        return None
    return (f"Pandoc could not find {', '.join(dict.fromkeys(missing))}: build it first "
            "(a [[build.steps]] entry with it in outputs) or fix the image path")


class BuildError(Exception):
    """A stage or step failed, or the steps cannot be ordered."""


def env_overrides() -> dict[str, str]:
    """The --env overrides a parent build passed in $TRAZO_BUILD_ENV (JSON)."""
    try:
        data = json.loads(os.environ.get(BUILD_ENV) or "{}")
    except ValueError:
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def order(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Steps of one slot: a step that reads another's outputs runs after it,
    otherwise in declaration order; a cycle is an error."""
    def feeds(producer, consumer):
        return any(out == pattern or fnmatch.fnmatchcase(out, pattern) or fnmatch.fnmatchcase(pattern, out)
                   for out in producer["outputs"] for pattern in consumer["inputs"])
    waiting = {step["name"]: {other["name"] for other in steps if other is not step and feeds(other, step)}
               for step in steps}
    ordered = []
    while waiting:
        ready = [step for step in steps if step["name"] in waiting and not waiting[step["name"]]]
        if not ready:
            raise BuildError("build.steps: cycle between " + ", ".join(sorted(waiting)))
        ordered.append(ready[0])
        del waiting[ready[0]["name"]]
        for needs in waiting.values():
            needs.discard(ready[0]["name"])
    return ordered


def image_targets(markdown: str) -> list[str]:
    """Local targets of Markdown images, `![alt](target "title")`; the alt
    text may hold brackets ([Title.]{.caption-title}) and nested images."""
    found, start = [], 0
    while (start := markdown.find("![", start)) != -1:
        depth, end = 0, start + 1
        while end < len(markdown):
            char = markdown[end]
            if char == "\\":
                end += 1
            elif char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
                if not depth:
                    break
            end += 1
        start += 2
        if depth or markdown[end + 1:end + 2] != "(":
            continue
        if (match := TARGET.match(markdown, end + 2)) and "://" not in (target := match[1] or match[2]):
            found.append(target)
    return found


def expand_includes(paths: list[Path]) -> list[Path]:
    """The sources and every file they `!include`, recursively (missing ones too)."""
    from .expand import include_lines, resolve_include
    found, pending = [], list(paths)
    while pending:
        path = pending.pop(0)
        if path in found:
            continue
        found.append(path)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for _, target in include_lines(text):
            if target is not None:
                try:
                    pending.append(resolve_include(target, path))
                except FileNotFoundError:
                    pass
    return found


def recorded_inputs(paths) -> list[str]:
    """The inputs a generated JSON file records ({"inputs": {path: sha256}})."""
    found = []
    for path in paths:
        try:
            found += [name for name in json.loads(path.read_text(encoding="utf-8")).get("inputs", {})
                      if not Path(name).is_absolute()]
        except (OSError, ValueError, AttributeError):
            pass
    return found


class Builder:
    """One build: freshness records, the project Python and the stages."""

    def __init__(self, project: Project = PROJECT, env: dict[str, str] | None = None,
                 force: bool = False, submission: bool = False, authors: bool = False,
                 log: Callable[[str], None] | None = None) -> None:
        self.project, self.root, self.force, self.submission = project, project.root, force, submission
        # --authors only changes a project whose title page is separate; elsewhere
        # it would only rewrite fresh files.
        from .frontmatter import separate_title_page
        self.authors = authors and separate_title_page(project)
        # --env overrides; a command run as written passes them on in
        # $TRAZO_BUILD_ENV to the trazo it calls (see execute).
        self.overrides = dict(env) if env is not None else env_overrides()
        self.env = {**project.config["build"]["env"], **self.overrides}
        self.steps = project.config["build"]["steps"]
        self.state_path = project.path("build") / STATE_NAME
        self.state = self.read_state()
        self.ran: list[str] = []  # what did work, in order
        self.log = log or (lambda text: print(text, flush=True))
        self.warned: set[str] = set()
        self._python: str | None = None
        self._versions: dict[str, str] | None = None

    # Freshness ----------------------------------------

    def rel(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return str(path)

    def warn(self, text: str) -> None:
        if text not in self.warned:
            self.warned.add(text)
            print(f"[build] warning: {text}", file=sys.stderr, flush=True)

    def files(self, patterns, owner: str | None = None) -> list[Path]:
        """Files matching globs relative to the root (or absolute); a folder
        counts with its files. A missing literal path counts (it hashes as
        missing), except for the inputs of `owner`, a step: there it is an
        error, and a glob without a match is a warning."""
        found = set()
        for pattern in patterns:
            path = self.root / pattern
            if glob.has_magic(pattern):
                matches = [Path(p) for p in glob.glob(str(path), recursive=True, include_hidden=True)]
                matches = [p for p in matches if p.is_file()]
                if owner and not matches:
                    self.warn(f"{owner}: input {pattern} matches no file")
                found.update(matches)
            elif path.is_dir():
                found.update(p for p in path.rglob("*") if p.is_file())
            elif owner and not path.exists():
                raise BuildError(f"{owner}: input {pattern} does not exist")
            else:
                found.add(path)
        return sorted(found)

    def digest(self, command, inputs, extra=None, env=True) -> str:
        """The hash of a command, its input contents, `extra` and, with `env`, [build.env]."""
        contents = {}
        for path in sorted(set(inputs)):
            try:
                contents[self.rel(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                contents[self.rel(path)] = None
        data = {"command": command, "inputs": contents, "extra": extra, **({"env": self.env} if env else {})}
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    def fresh(self, key: str, digest: str, outputs=()) -> bool:
        record = self.state.get(key)
        if self.force or not record or record.get("hash") != digest:
            return False
        paths = {*outputs, *(self.root / name for name in record.get("outputs", []))}
        return all(path.is_file() and path.stat().st_size > 0 for path in paths)

    def read_state(self) -> dict[str, Any]:
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return state if isinstance(state, dict) else {}

    def save(self, key: str, value: dict[str, Any] | None) -> None:
        """Update one record. The file is read again first, so records that
        another process (the preview, a DOCX job) wrote meanwhile are kept."""
        state = self.read_state()
        if value is None:
            state.pop(key, None)
        else:
            state[key] = value
        write_json(self.state_path, state, indent=1, sort_keys=True)
        self.state = state

    def record(self, key: str, digest: str, outputs=()) -> None:
        self.save(key, {"hash": digest, "outputs": sorted(self.rel(p) for p in outputs)})

    def forget(self, key: str) -> None:
        """Drop a record before its stage runs: if the run fails, it runs again."""
        if key in self.read_state():
            self.save(key, None)

    def guarded(self, key: str, outputs, run: Callable[[], None]) -> None:
        """run() with `key`'s record dropped; when it fails, remove the files
        matching `outputs` that it wrote or changed (Make's .DELETE_ON_ERROR)."""
        self.forget(key)
        stamp = lambda: {p: (p.stat().st_mtime_ns, p.stat().st_size)  # noqa: E731
                         for p in self.files(outputs) if p.is_file()}
        before = stamp()
        try:
            run()
        except BaseException:
            for path, now in stamp().items():
                if before.get(path) != now:
                    path.unlink(missing_ok=True)
                    self.log(f"[build] removed {self.rel(path)}: written by the failed {key}")
            raise

    def versions(self) -> dict[str, str]:
        """trazo's version and the first line of `pandoc --version`, read
        once per build."""
        if self._versions is None:
            self._versions = {"trazo": __version__}
            try:
                result = subprocess.run([*self.project.command("pandoc"), "--version"], cwd=self.root,
                                        capture_output=True, text=True)
                self._versions["pandoc"] = (result.stdout.splitlines() or [""])[0]
            except OSError:
                self._versions["pandoc"] = ""
        return self._versions

    def did(self, label: str) -> None:
        self.ran.append(label)
        self.log(f"[build] {label}")

    # Running commands ---------------------------------

    def python(self) -> str:
        """The project's Python (.venv), synced with uv.lock first when needed."""
        if self._python:
            return self._python
        venv = self.root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not (self.root / "pyproject.toml").is_file():
            self._python = sys.executable  # not a uv project: trazo's own Python
            return self._python
        lock = self.root / "uv.lock"
        digest = lambda: self.digest(["uv", "sync"], [lock, self.root / "pyproject.toml"], env=False)  # noqa: E731
        if not venv.is_file() or self.state.get("python", {}).get("hash") != digest():  # --force keeps .venv
            from .machine import resolve_tool
            from .uv_sync import sync
            uv, where = resolve_tool("uv")
            if not uv:
                raise BuildError("uv not found: it creates the project's Python environment (.venv); "
                                 "run trazo doctor")
            self.did("python: uv sync" + (" --locked" if lock.is_file() else ""))
            code = sync(uv, where, self.root, lock.is_file())
            if code or not venv.is_file():
                raise BuildError(f"uv sync failed (exit {code})")
            self.record("python", digest())
        self._python = str(venv)
        return self._python

    def step_env(self) -> dict[str, str]:
        """The environment of a step: [build.env], and the project's .venv
        active (VIRTUAL_ENV, first on PATH) so `python`, `uv` or `pip` in a
        step use it rather than the environment trazo runs in."""
        env = {**os.environ, **self.env}
        python = Path(self.python())
        if python != Path(sys.executable):
            env["VIRTUAL_ENV"] = str(python.parent.parent)
            env["PATH"] = str(python.parent) + os.pathsep + env.get("PATH", "")
            env.pop("PYTHONHOME", None)
        return env

    def execute(self, argv: list[str], what: str, env: dict[str, str] | None = None) -> None:
        """Run argv; without `env`, a command as written: [build.env], and the
        overrides in $TRAZO_BUILD_ENV for a trazo build it calls."""
        env = env or {**os.environ, **self.env, BUILD_ENV: json.dumps(self.overrides, sort_keys=True)}
        try:
            result = subprocess.run(argv, cwd=self.root, env=child_env(env))
        except FileNotFoundError as exc:
            raise BuildError(f"{what}: {argv[0]} not found; install it, or run trazo doctor "
                             "to check the tools") from exc
        if result.returncode:
            raise BuildError(f"{what} failed (exit {result.returncode}): {' '.join(argv)}")

    def run_python(self, argv: list[str], what: str) -> None:
        """`<project python> argv...` with the step environment."""
        self.execute([self.python(), *argv], what, self.step_env())

    def run_command(self, name: str) -> bool:
        """Run a nonempty [commands] entry as written; False when it is built in."""
        command = self.project.command(name)
        if not command:
            return False
        self.did(f"{name}: {' '.join(command)}")
        self.execute(command, f"commands.{name}")
        return True

    def script(self, key, run, inputs, outputs, label=None, extra=None, record_outputs=None,
               owner=None, writes=None) -> None:
        """Run `<python> run...` unless fresh. `inputs` are globs, or a function
        returning them (asked again after the run, for inputs the script
        records itself), checked as `owner`'s when given; `outputs` are globs
        that must match nonempty files; record_outputs() lists them after the
        run. When the run fails, files matching `writes` (default: outputs)
        that it changed are removed."""
        script = self.root / run[0]
        patterns = inputs if callable(inputs) else (lambda: inputs)
        hashed = lambda: [*self.files(patterns(), owner), *([script] if script.is_file() else []),  # noqa: E731
                          self.root / "uv.lock"]
        digest = self.digest(run, hashed(), extra)
        declared = [self.root / out for out in outputs if not glob.has_magic(out)]
        if self.fresh(key, digest, declared) and all(self.files([out]) for out in outputs):
            return
        self.python()
        self.did(label or key)
        self.guarded(key, outputs if writes is None else writes,
                     lambda: self.run_python(run, label or key))
        written = record_outputs() if record_outputs else self.files(outputs)
        missing = [out for out in outputs if not any(p.is_file() and p.stat().st_size for p in self.files([out]))]
        if missing:
            raise BuildError(f"{label or key} did not write {', '.join(missing)}")
        self.record(key, self.digest(run, hashed(), extra), written)

    def step(self, step: dict[str, Any], key: str | None = None) -> None:
        if step["run"]:
            self.script(key or f"step:{step['name']}", step["run"], step["inputs"], step["outputs"],
                        f"step {step['name']}", owner=f"step {step['name']}")

    def slot(self, name: str) -> None:
        """The steps that run before stage `name`."""
        for step in order([s for s in self.steps if s["before"] == name and s["name"] not in ("values", "tables")
                           and (s["when"] == "build" or (s["when"] == "submission" and self.submission))]):
            self.step(step)

    def named(self, name: str) -> dict[str, Any] | None:
        return next((s for s in self.steps if s["name"] == name), None)

    # Stages ---------------------------------------------

    def values(self) -> None:
        self.slot("values")
        if self.run_command("values"):
            return
        if (step := self.named("values")) is not None:
            return self.step(step)
        if not self.project.path("values_script").is_file():
            return
        output = self.project.path("values")
        self.script("values", [self.project.relative("values_script")], lambda: recorded_inputs([output]),
                    [self.rel(output)])

    def tables(self) -> None:
        self.slot("tables")
        if self.run_command("tables"):
            return
        data = self.project.path("table_data")
        generator = self.project.relative("table_generator")
        if (step := self.named("tables")) is not None:
            self.step(step)
        elif self.project.path("table_generator").is_file() and self.table_sources():
            recorded = self.state.get("tables", {}).get("outputs", [])
            scripts = Path(generator).parent.as_posix() + "/*.py"
            inputs = lambda: [scripts, *recorded_inputs(data.glob("*.json"))]  # noqa: E731
            md = self.project.path("table_output") / "md"
            before = {p: p.stat().st_mtime_ns for p in md.glob("*.md")}
            writes = [f"{self.project.relative('table_data')}/*.json",
                      f"{self.project.relative('table_output')}/md/*.md"]
            self.script("tables", [generator], inputs, [],
                        "tables: generate", writes=writes, record_outputs=lambda: [
                            *data.glob("*.json"),
                            *(p for p in md.glob("*.md") if before.get(p) != p.stat().st_mtime_ns
                              or self.rel(p) in recorded)])
        self.render_tables()

    def table_sources(self) -> bool:
        """Whether a table needs the generator: a spec."""
        return any(not p.name.startswith("_") for p in self.project.path("table_specs").glob("*.json"))

    def render_tables(self) -> None:
        from . import table
        specs = self.project.path("table_specs")
        labels = sorted(p.stem for p in specs.glob("*.json") if p.name != "_shared.json")
        if not labels:
            return
        outputs = [table.output_path(label) for label in labels]
        digest = self.digest(["table", "render", "--all"],
                             [*specs.glob("*.json"), *self.project.path("table_data").glob("*.json")],
                             __version__, env=False)
        if self.fresh("tables-render", digest, outputs):
            return
        self.did("tables: render")

        def render():
            if table.main(["render", "--all"]):
                raise BuildError("table render --all failed")
        self.guarded("tables-render", [self.rel(p) for p in outputs], render)
        missing = [self.rel(p) for p in outputs if not p.is_file()]
        if missing:
            raise BuildError(f"table render did not write {', '.join(missing)}")
        self.record("tables-render", digest, outputs)

    def figures(self) -> None:
        """The steps before diagrams and commands.figures (the figure scripts)."""
        self.slot("diagrams")
        self.run_command("figures")

    def diagrams(self) -> None:
        self.figures()
        from . import diagram
        figures = self.project.path("figures")
        inputs = lambda: [*(p for p in figures.rglob("*") if p.is_file()),  # noqa: E731
                          *(p for key in ("diagrams", "diagram_output", "figure_fonts", "figure_embeds")
                            for p in self.project.path(key).rglob("*") if p.is_file()),
                          self.project.path("figure_tokens"), self.project.path("values"),
                          ASSETS_DIR / "diagram" / "renderer.js"]
        digest = lambda: self.digest(["diagram", "export"], inputs(), env=False)  # noqa: E731
        if not diagram.all_diagrams() or self.fresh("diagrams", digest()):
            return
        stale = [p.stem for p in diagram.all_diagrams() if diagram.export_state(p) not in ("current", "draft")]
        if stale:
            self.did("diagrams: export " + ", ".join(stale))
            # Exports are written atomically and stamped, so a failed export
            # leaves stale or missing files that export_state finds again.
            self.forget("diagrams")
            if diagram.cmd_export(stale, False):
                raise BuildError("diagram export failed")
        self.record("diagrams", digest())

    def prepare(self, exports: bool = True) -> None:
        """Every stage before the per-document ones; without `exports`, all
        but the diagram export (no Chrome), for the preview and checks."""
        self.values()
        self.tables()
        if exports:
            self.diagrams()
        else:
            self.figures()
        self.slot("expand")

    # Documents ------------------------------------------

    def pandoc_args(self, source: Path, numbering: Path, output: Path) -> list[str]:
        """Pandoc's arguments for the Word file; pandoc_run runs trazo's own
        cross-references where they name `--filter pandoc-crossref`."""
        paths = self.project.relative
        flags = [f"--metadata-file={paths('metadata')}", "--standalone",
                 f"--bibliography={paths('bibliography')}", f"--csl={paths('csl')}"]
        if self.project.path("reference_doc").is_file():
            flags.append(f"--reference-doc={paths('reference_doc')}")
        flags += ["--resource-path=.", "--filter", "pandoc-crossref", "--citeproc"]
        return [self.rel(source), *flags, f"--metadata-file={self.rel(numbering)}", "-o", self.rel(output)]

    def pandoc(self, source: Path, numbering: Path, output: Path) -> list[str]:
        """The Pandoc command line for the Word file's hash: commands.pandoc as
        configured, not where it was found (PATH differs between a terminal
        and an app); versions() hashes the version that runs."""
        return [*self.project.config["commands"]["pandoc"], *self.pandoc_args(source, numbering, output)]

    def run_pandoc(self, args: list[str]) -> None:
        """Run Pandoc through pandoc_run (trazo's cross-references)."""
        from . import pandoc_run
        command = self.project.command("pandoc")
        try:
            result = pandoc_run.run(args, command=command, cwd=self.root, text=True)
        except FileNotFoundError as exc:
            raise BuildError(f"Pandoc: {command[0]} not found; run trazo setup to download it, "
                             "or set commands.pandoc in trazo.toml") from exc
        sys.stderr.write(result.stderr)
        if result.returncode:
            raise BuildError(f"Pandoc failed (exit {result.returncode}): {' '.join([*command, *args])}")
        if missing := missing_resources(result.stderr):
            raise BuildError(missing)

    def expand(self, name: str) -> Path:
        from .expand import expand_files
        from .numbering import metadata_file
        folder = self.project.path("build")
        sources = self.project.document_files(name)
        target = folder / f"{name}.md"
        metadata_file(name, folder)
        inputs = [*expand_includes(sources), self.project.path("values"),
                  self.project.path("table_specs") / "_shared.json", self.project.path("metadata"),
                  self.root / "trazo.toml"]
        extra = [[self.rel(p) for p in sources], __version__, *(["authors"] if self.authors else [])]
        digest = self.digest(["expand", name], inputs, extra, env=False)
        if not self.fresh(f"expand:{name}", digest, [target]):
            self.did(f"expand: {name}")
            self.forget(f"expand:{name}")
            write_atomic(target, expand_files(sources, document=name, with_authors=self.authors).encode("utf-8"))
            self.record(f"expand:{name}", digest, [target])
        return target

    def docx(self, name: str, indexes=()) -> None:
        command, output = self.project.document_build(name), self.project.document_docx(name)
        if command is None:
            raise BuildError(f"documents.{name} has no full build (set its docx and build)")
        if command:  # as written; the command keeps its own freshness
            self.did(f"docx: {name}: {' '.join(command)}")
            self.execute(command, f"the build of {name}")
            if indexes or output.is_file():
                from .docx_indexes import apply
                data = output.read_bytes()
                if (updated := apply(data, list(indexes))) != data:
                    write_atomic(output, updated)
            return
        self.pandoc_docx(name, indexes)

    def pandoc_docx(self, name: str, indexes=()) -> None:
        """trazo's own Word build of a document: expand, Pandoc, after-docx
        steps, docx-finish; skipped when fresh."""
        output = self.project.document_docx(name)
        source = self.expand(name)
        numbering = self.project.path("build") / f"{name}-numbering.json"
        after = [s for s in self.steps if s["after"] == "docx" and (not s["documents"] or name in s["documents"])
                 and (s["when"] == "build" or (s["when"] == "submission" and self.submission))]
        markdown = source.read_text(encoding="utf-8")
        images = {self.root / target for target in image_targets(markdown)}
        # Images a step writes change the Word file even when no image link
        # in the text names them (or names them in a form not parsed here).
        produced = [f for s in self.steps if s["when"] != "manual" and not s["after"]
                    for f in self.files(s["outputs"])]
        p = self.project.path
        inputs = [source, numbering, *images, *produced, *(f for f in p("diagram_output").glob("*.png")),
                  p("metadata"), p("bibliography"), p("csl"), p("reference_doc"),
                  *(f for s in after for f in self.files(s["inputs"], f"step {s['name']}")),
                  *(self.root / s["run"][0] for s in after if s["run"]),
                  *([self.root / "uv.lock"] if after else [])]
        commands = [self.pandoc(source, numbering, output), *(s["run"] for s in after)]
        # [build.env] only matters to the after-docx steps that receive it.
        digest = self.digest(commands, inputs, [sorted(indexes), self.versions()], env=bool(after))
        if self.fresh(f"docx:{name}", digest, [output]):
            return
        self.did(f"docx: {name}")
        self.forget(f"docx:{name}")
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".trazo-", dir=output.parent) as temporary:
            target = Path(temporary) / output.name
            self.run_pandoc(self.pandoc_args(source, numbering, target))
            for step in order(after):  # before docx-finish
                if step["run"]:
                    self.log(f"[build] step {step['name']}: {name}")
                    run = [arg.replace("{docx}", self.rel(target)) for arg in step["run"]]
                    self.run_python(run, f"step {step['name']}")
            from .docx_indexes import apply
            write_atomic(output, apply(target.read_bytes(), list(indexes)))
        self.record(f"docx:{name}", digest, [output])

    def documents(self, names=None) -> list[str]:
        """The documents `trazo build` writes: every one with a Word file;
        the separate submission files only with --submission."""
        if names:
            return list(names)
        return [name for name in self.project.documents
                if self.project.document_build(name) is not None
                and (self.submission or name not in self.project.submission_documents)]

    def build(self, names=None, indexes=()) -> None:
        self.prepare()
        names = self.documents(names)
        article = self.project.config["kind"] == "article"
        separate = [n for n in names if n in self.project.submission_documents]
        command = article and separate and self.project.command("submission")
        if self.submission and article:
            self.remove_inactive_submission_files()
        if command:  # one command builds them all
            names = [n for n in names if n not in separate]
        for name in names:
            self.docx(name, indexes)
        if command:
            self.run_command("submission")
        if not self.ran:
            self.log("[build] up to date")

    def remove_inactive_submission_files(self) -> None:
        output = self.root / self.project.config["submission"]["output"]
        active = {self.project.document_docx(name) for name in self.project.documents}
        for name in ("title_page", "declarations", "cover_letter"):
            target = output / f"{name}.docx"
            if target not in active and target.is_file():
                target.unlink()
                self.log(f"Removed {self.rel(target)}")


def parse_env(pairs) -> dict[str, str]:
    env = {}
    for pair in pairs or []:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise BuildError(f"--env needs KEY=VALUE, not {pair!r}")
        env[key] = value
    return env


@locked
def run_main(argv=None) -> int:
    """trazo run STEP: one declared step (any `when`), or a built-in stage."""
    parser = argparse.ArgumentParser(prog="trazo run", description=run_main.__doc__)
    parser.add_argument("step", help="a [[build.steps]] name, or values, tables or diagrams")
    parser.add_argument("--force", action="store_true", help="run even when fresh")
    parser.add_argument("--check", action="store_true", help="run the step's check instead")
    parser.add_argument("--env", action="append", metavar="K=V", help="override a [build.env] value")
    args = parser.parse_args(argv)
    try:
        builder = Builder(env=parse_env(args.env), force=args.force)
        step = builder.named(args.step)
        if args.check:
            if step is None or not step["check"]:
                raise BuildError(f"no step named {args.step!r} with a check")
            builder.run_python(step["check"], f"the check of {args.step}")
            return 0
        if args.step in ("values", "tables", "diagrams"):
            getattr(builder, args.step)()
        elif step is None:
            raise BuildError(f"no step named {args.step!r} in build.steps")
        elif not step["run"]:
            raise BuildError(f"step {args.step} has only a check")
        elif step["after"]:
            raise BuildError(f"step {args.step} runs on Word files: use trazo build")
        else:
            builder.step(step)
        if not builder.ran:
            print(f"[build] {args.step}: up to date")
    except (BuildError, OSError, ValueError, RuntimeError) as exc:
        print(f"run: {exc}", file=sys.stderr)
        return 1
    return 0


@locked
def verify_main(argv=None) -> int:
    """trazo verify: clean, build everything, run the checks and test each
    Word file."""
    parser = argparse.ArgumentParser(prog="trazo verify", description=verify_main.__doc__)
    parser.add_argument("--env", action="append", metavar="K=V", help="override a [build.env] value")
    args = parser.parse_args(argv)
    from . import agents, citations, diagram, table
    project = PROJECT
    article = project.config["kind"] == "article"
    try:
        shutil.rmtree(project.path("build"), ignore_errors=True)
        written = [project.document_docx(name) for name in project.documents if project.document_docx(name)]
        written += [project.root / project.config["submission"]["output"] / f"{name}.docx"
                    for name in ("title_page", "declarations", "cover_letter")]
        for path in written:
            path.unlink(missing_ok=True)
        builder = Builder(env=parse_env(args.env), submission=article)
        builder.build()
        if article:
            from .submission import check
            print()
            check()
        checks = [("tables", lambda: table.main(["check", "--all", "--outputs"])),
                  ("citations", lambda: citations.main([])),
                  ("agents", lambda: agents.main(["--check"])),
                  ("diagrams", lambda: diagram.main(["check", "--all", "--exports"]))]
        if not project.command("values") and builder.named("values") is None \
                and project.path("values_script").is_file():
            checks.insert(0, ("values", lambda: builder.run_python(
                [project.relative("values_script"), "--check"], "the values check")))
        for step in builder.steps:  # manual steps' checks: trazo run NAME --check
            if step["check"] and step["when"] != "manual":
                checks.append((f"step {step['name']}", lambda step=step: builder.run_python(
                    step["check"], f"the check of {step['name']}")))
        for name, check in checks:
            print(f"[verify] {name}", flush=True)
            if check():
                raise BuildError(f"the {name} check failed")
        print("Checking generated DOCX files...")
        for path in written:
            if path.is_file():
                with zipfile.ZipFile(path) as archive:
                    if archive.testzip() is not None:
                        raise BuildError(f"{builder.rel(path)} is damaged")
    except (BuildError, diagram.DiagramError, OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        print(f"verify: {exc}", file=sys.stderr)
        return 1
    print(f"Verification passed: {', '.join(sorted({builder.rel(p.parent) for p in written if p.is_file()}))}")
    return 0
