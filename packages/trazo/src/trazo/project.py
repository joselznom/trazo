"""The project trazo works on: its root folder and its trazo.toml.

The root is $TRAZO_ROOT, else the nearest folder (the current one or a
parent) with a trazo.toml, else the current folder. trazo.toml only
overrides the defaults below; unknown keys are errors so typos show up.

    version = "v0.1.0"            # trazo release the project runs
    kind = "article"             # document kind: "article" or "report"

    [documents.manuscript]        # one entry per document: a folder of
    path = "manuscript/main"      # Markdown sections built in name order
    title = "Manuscript"
    number_prefix = ""           # figure/table numbers; supplementary defaults to "S"
    title_prefix = ""            # before "Figure" in captions and references
    docx = "output/manuscript.docx"  # its full build output ("" = none)
    build = []                    # its full build; [] = built in (trazo
                                  # build); the first document defaults
                                  # to commands.docx

    [paths]                       # metadata, bibliography, csl, values,
    values = "data/values.json"   # review, build, table_specs,
                                  # table_data, table_output, skills,
                                  # skill_targets
    declarations = "manuscript/declarations"  # journal declarations
    title_page = "manuscript/title-page"      # optional additions to the generated title page
    cover_letter = "manuscript/cover-letter"  # cover letter sources
    values_script = "scripts/build_values.py"  # built-in values stage
    table_generator = "tables/scripts/generate_tables.py"  # built-in tables
                                  # stage; every *.py beside it is an input
    [commands]                    # string argument lists, run from root; [] (the
                                  # default) = trazo's own build stage
    tables = []                   # table data generator, then table render --all
    values = []                   # paths.values_script
    figures = []                  # run before diagrams; figure scripts are build.steps
    docx = []                     # first document: expand, Pandoc, docx-finish
    pandoc = ["pandoc"]          # nonempty; Word builds, HTML preview, object exports;
                                  # the default runs Pandoc from PATH, else the one
                                  # `trazo setup` recorded (trazo.machine)
    submission = []              # separate submission Word files

    [build.env]                   # passed to every build step; part of every hash
    RESULTS_MODE = "canonical"    # trazo build --env K=V overrides one

    [[build.steps]]               # a project rule, run by trazo build
    name = "fig1"                 # unique; "values" or "tables" replaces that stage
    run = ["figures/scripts/fig1.py"]  # argv after the project's .venv Python, no shell
    inputs = ["data/*.csv"]       # globs; the script, uv.lock and build.env always count;
                                  # may be absolute or reach outside the project
                                  # (../data); a missing path is an error, a
                                  # glob without a match a warning
    outputs = ["figures/output/png/fig1.png"]  # inside the project; must exist and be
                                  # nonempty afterwards; removed when changed by
                                  # a failed run
    before = "diagrams"           # values, tables, diagrams or expand (the default),
                                  # or after = "docx": once per Word file before
                                  # docx-finish, {docx} in run is that file;
                                  # documents = ["manuscript"] limits it
    when = "build"                # build, submission (build --submission) or
                                  # manual (only trazo run NAME)
    check = ["figures/scripts/fig1.py", "--check"]  # run by trazo verify (manual
                                  # steps: trazo run NAME --check)

    [submission]                 # articles only; ignored for reports
    journal = ""                 # target journal
    guidelines = ""              # instructions for authors URL
    title_page = "manuscript"     # manuscript or separate
    declarations = "end"          # end, separate or form (no Word file)
    output = "output"             # separate Word files folder

    [preview]
    watch = ["manuscript/**/*.md", ...]  # list of nonempty glob strings

    [counts]                      # main-text word count (preview, trazo counts)
    exclude = ["abstract", ...]   # headings of sections outside the main text;
                                  # {.uncounted} on a heading also leaves it out

    [abbreviations]               # every document, articles and reports
    style = "table"               # table (borderless), list, or inline
    sort = true                   # stable alphabetic order, letters only
    ignore = []                   # tokens exempt from unlisted-acronym warnings

    [limits]                      # journal limits of the first document, 0 = none
    words = 3000                  # main text; also abstract, figures, tables,
                                  # references, keywords (maximum number)

    [browser]
    chrome = ""                   # empty: $TRAZO_CHROME, legacy [diagrams], common paths

    [crossref]
    engine = "trazo"           # trazo numbers figures, tables and sections

Tables: table_specs holds <label>.json (the presentation of one table) and
the optional _shared.json (formats and maps used by several tables);
table_data holds <name>.json, written by the project's own generator;
`trazo table render` writes <table_output>/md/<label>.md.
reference_doc is the optional Word style document for independent exports;
if absent, Pandoc's default Word styles are used.

Figure paths: figures is the public image root, diagrams holds JSON sources,
diagram_output the SVG/PDF/PNG exports, figure_tokens the scientific style,
figure_fonts its font files and figure_embeds the SVG embed picker folder.
The default figures/ URLs remain valid; configured paths are used throughout
the workspace, rendering and export pipeline. preview.watch adds extra inputs
to the configured documents, data and figure paths, which are always watched.
"""

from __future__ import annotations

import copy
import os
import re
import shlex
import tomllib
from pathlib import Path
from typing import Any

CONFIG_NAME = "trazo.toml"
PACKAGE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = PACKAGE_DIR / "assets"
SKILLS_DIR = PACKAGE_DIR / "skills"
# An article's submission documents besides the paper, in the preview's
# Submission menu order (see Project.submission_parts).
SUBMISSION_PARTS = ("declarations", "title_page", "cover_letter")

DEFAULTS: dict[str, Any] = {
    "version": "",
    "kind": "article",
    "abbreviations": {"style": "table", "sort": True, "ignore": []},
    "submission": {"journal": "", "guidelines": "", "title_page": "manuscript",
                   "declarations": "end", "output": "output"},
    "documents": {
        "manuscript": {"path": "manuscript/main", "title": "Manuscript", "number_prefix": "",
                       "docx": "output/manuscript.docx"},  # built by commands.docx
        "supplementary": {"path": "manuscript/supplementary", "title": "Supplementary", "number_prefix": "S",
                          "docx": "output/supplementary.docx", "build": []},
    },
    "paths": {  # nonempty path strings; skill_targets is a list of them
        "metadata": "manuscript/metadata.yaml",
        "declarations": "manuscript/declarations",
        "title_page": "manuscript/title-page",
        "cover_letter": "manuscript/cover-letter",
        "bibliography": "bibliography/references.bib",
        "csl": "bibliography/csl/ieee.csl",
        "reference_doc": "templates/reference_universal.docx",  # optional Word styles (builds, exports)
        "values": "data/values.json",
        "review": "notes/review",
        "build": ".build",
        "figures": "figures",
        "diagrams": "figures/diagrams",
        "diagram_output": "figures/external",
        "figure_tokens": "figures/style/tokens.json",
        "figure_fonts": "figures/fonts",
        "figure_embeds": "figures/output/svg",
        "table_specs": "tables/specs",
        "table_data": "tables/output/data",
        "table_output": "tables/output",
        "skills": "agent-workflows/skills",
        "skill_targets": [".agents/skills", ".claude/skills"],
        # Scripts of the built-in values and tables stages (trazo build);
        # the generator's folder's *.py files are inputs of the tables stage.
        "values_script": "scripts/build_values.py",
        "table_generator": "tables/scripts/generate_tables.py",
    },
    # [] = built in (trazo build_graph); a nonempty list runs as written.
    "commands": {
        "tables": [],
        "docx": [],  # full build of the first document
        "values": [],
        "figures": [],
        # Word builds, HTML preview and exports (crossref and citeproc run first).
        # The default: Pandoc on PATH, else the machine file's (`trazo setup`);
        # anything else runs as written (machine.resolve_pandoc).
        "pandoc": ["pandoc"],
        "submission": [],
    },
    # Project build rules; see trazo.build_graph and the module docstring.
    "build": {"env": {}, "steps": []},
    "preview": {
        "watch": [
            "manuscript/**/*.md",
            "manuscript/metadata.yaml",
            "bibliography/**/*",
            "tables/output/md/*.md",
            "tables/specs/*.json",
            "tables/output/data/*.json",
            "data/values.json",
            "figures/output/png/*",
            "figures/external/*",
        ],
    },
    "counts": {
        # Sections outside the main text, with their sub-sections, as
        # counts.excluded_heading matches them; {.uncounted} leaves one out too.
        "exclude": [
            "abstract", "graphical abstract", "key points", "key results", "highlights", "keywords",
            "abbreviations", "list of abbreviations", "acknowledgements", "acknowledgments",
            "acknowledgement", "acknowledgment", "funding", "funding information", "financial support",
            "declarations", "statements and declarations", "declaration of competing interest",
            "declaration of interests", "competing interests", "conflict of interest",
            "conflicts of interest", "disclosures", "ethics", "ethics approval", "ethics declarations",
            "ethical approval", "ethics approval and consent to participate", "informed consent",
            "consent for publication", "consent to participate", "author contributions", "contributors",
            "credit authorship contribution statement", "data availability",
            "availability of data and materials", "data and code availability", "code availability",
            "software and code availability", "use of ai", "use of ai tools", "use of generative ai",
            "declaration of generative ai and ai-assisted technologies in the writing process",
            "appendix", "appendices",
        ],
    },
    # Journal limits of the first document (the manuscript); 0 = none.
    "limits": {"words": 0, "abstract": 0, "figures": 0, "tables": 0, "references": 0, "keywords": 0},
    "diagrams": {"chrome": ""},
    "browser": {"chrome": ""},
    # Who resolves {#fig:} labels and [@fig:] references: trazo.crossref,
    # between `pandoc -t json` and `pandoc -f json` (pandoc_run).
    "crossref": {"engine": "trazo"},
}

# Common Chrome locations, tried in order after the configured one.
CHROME_PATHS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    *(str(Path(os.environ[folder]) / app)
      for app in (r"Google\Chrome\Application\chrome.exe", r"Microsoft\Edge\Application\msedge.exe")
      for folder in ("ProgramFiles", "ProgramFiles(x86)") if os.environ.get(folder)),
)
CHROME_COMMANDS = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
                   "chrome")


def chrome_flags() -> list[str]:
    """Extra Chrome flags: --no-sandbox when running as root (containers, CI),
    where Chrome refuses to start with its sandbox."""
    return ["--no-sandbox"] if hasattr(os, "geteuid") and os.geteuid() == 0 else []


class ConfigError(Exception):
    """trazo.toml is unreadable or has invalid keys or values."""


def find_root(start: Path | None = None) -> Path | None:
    """The project root: $TRAZO_ROOT or the nearest folder with trazo.toml."""
    here = Path(os.environ.get("TRAZO_ROOT") or start or os.getcwd()).resolve()
    for folder in (here, *here.parents):
        if (folder / CONFIG_NAME).is_file():
            return folder
        if os.environ.get("TRAZO_ROOT"):
            return here
    return None


def merge(base: dict[str, Any], extra: dict[str, Any], where: str = "") -> dict[str, Any]:
    """Defaults updated with trazo.toml; tables merge, other values replace."""
    merged = copy.deepcopy(base)
    for key, value in extra.items():
        name = f"{where}{key}"
        if key not in base:
            raise ConfigError(f"{CONFIG_NAME}: unknown key {name}")
        if isinstance(base[key], dict) and not isinstance(value, dict):
            raise ConfigError(f"{CONFIG_NAME}: {name} must be a table")
        if name == "documents":  # replaces the default documents
            if not value:
                raise ConfigError(f"{CONFIG_NAME}: documents needs at least one entry")
            merged[key] = {}
            for doc, spec in value.items():
                if (not isinstance(spec, dict) or "path" not in spec
                        or set(spec) - {"path", "title", "number_prefix", "title_prefix", "docx", "build"}):
                    raise ConfigError(f"{CONFIG_NAME}: documents.{doc} needs path "
                                      "(and optional title, number_prefix, title_prefix, docx and build)")
                if not doc:
                    raise ConfigError(f"{CONFIG_NAME}: document names must not be empty")
                spec = dict(spec)
                if "build" in spec:
                    spec["build"] = command_list(spec["build"], f"documents.{doc}.build", empty=True)
                for field, text in spec.items():
                    if field == "build":
                        continue
                    if not isinstance(text, str) or (field == "path" and not text.strip()):
                        raise ConfigError(f"{CONFIG_NAME}: documents.{doc}.{field} must be "
                                          + ("a nonempty path string" if field == "path"
                                             else "a string"))
                    spaces = field == "number_prefix"
                    if field.endswith("_prefix") and re.search(r"[$*_\\]" + r"|\s" * spaces, text):
                        raise ConfigError(f"{CONFIG_NAME}: documents.{doc}.{field} must not contain "
                                          "$, *, _, backslashes" + " or whitespace" * spaces)
                known = base[key].get(doc, {})
                merged[key][doc] = {"title": doc.capitalize(), "number_prefix": known.get("number_prefix", ""),
                                   **{f: known[f] for f in ("docx", "build") if f in known}, **spec}
        elif name == "abbreviations.sort":
            if not isinstance(value, bool):
                raise ConfigError(f"{CONFIG_NAME}: {name} must be a boolean")
            merged[key] = value
        elif name == "abbreviations.style":
            if value not in ("table", "list", "inline"):
                raise ConfigError(f"{CONFIG_NAME}: {name} must be table, list or inline")
            merged[key] = value
        elif name == "kind":
            if not isinstance(value, str) or value not in ("article", "report"):
                raise ConfigError(f'{CONFIG_NAME}: kind must be "article" or "report"')
            merged[key] = value
        elif name == "crossref.engine":
            if value != "trazo":
                raise ConfigError(f'{CONFIG_NAME}: {name} must be "trazo" (pandoc-crossref is not supported)')
            merged[key] = value
        elif name in ("submission.title_page", "submission.declarations"):
            choices = ("manuscript", "separate") if key == "title_page" else ("end", "separate", "form")
            if value not in choices:
                raise ConfigError(f"{CONFIG_NAME}: {name} must be one of {', '.join(choices)}")
            merged[key] = value
        elif name == "submission.output":
            if not isinstance(value, str) or not value.strip():
                raise ConfigError(f"{CONFIG_NAME}: {name} must be a nonempty path string")
            merged[key] = value
        elif name == "build.env":
            if not isinstance(value, dict) or not all(
                    isinstance(item, str) and env_name for env_name, item in value.items()):
                raise ConfigError(f"{CONFIG_NAME}: {name} must be a table of strings")
            merged[key] = dict(value)
        elif name == "build.steps":
            merged[key] = build_steps(value)
        elif isinstance(base[key], dict):
            merged[key] = merge(base[key], value, f"{name}.")
        elif where == "commands.":
            merged[key] = command_list(value, name, empty=key != "pandoc")
        elif isinstance(base[key], int):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ConfigError(f"{CONFIG_NAME}: {name} must be a whole number (0 = no limit)")
            merged[key] = value
        else:
            if isinstance(base[key], list):
                valid = isinstance(value, list) and all(
                    isinstance(item, str) and item.strip() for item in value)
                expected = "a list of nonempty strings"
            else:
                valid = isinstance(value, str) and (where != "paths." or value.strip())
                expected = "a nonempty path string" if where == "paths." else "a string"
            if not valid:
                raise ConfigError(f"{CONFIG_NAME}: {name} must be {expected}")
            merged[key] = value
    return merged


def command_list(value: Any, name: str, empty: bool = False) -> list[str]:
    """A command as an argument list; older projects accepted command strings.
    With `empty`, [] (or "") is allowed and means trazo's built-in stage."""
    if isinstance(value, str):
        try:
            value = shlex.split(value)
        except ValueError as exc:
            raise ConfigError(f"{CONFIG_NAME}: {name}: {exc}") from exc
    if empty and value == []:
        return value
    if (not isinstance(value, list) or not value
            or any(not isinstance(arg, str) for arg in value) or not value[0].strip()):
        raise ConfigError(f"{CONFIG_NAME}: {name} must be a nonempty argument list")
    return value


STEP_KEYS = {"name", "run", "inputs", "outputs", "before", "after", "documents", "when", "check"}
STEP_SLOTS = ("values", "tables", "diagrams", "expand")


def build_steps(value: Any) -> list[dict[str, Any]]:
    """[[build.steps]] checked and completed with their defaults."""
    if not isinstance(value, list):
        raise ConfigError(f"{CONFIG_NAME}: build.steps must be an array of tables ([[build.steps]])")
    steps, names = [], set()
    for number, raw in enumerate(value, 1):
        where = f"build.steps[{number}]"
        if not isinstance(raw, dict):
            raise ConfigError(f"{CONFIG_NAME}: {where} must be a table")
        if set(raw) - STEP_KEYS:
            raise ConfigError(f"{CONFIG_NAME}: {where}: unknown key {sorted(set(raw) - STEP_KEYS)[0]}")
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip() or "{" in name:
            raise ConfigError(f"{CONFIG_NAME}: {where}.name must be a nonempty string")
        where = f"build.steps.{name}"
        if name in names:
            raise ConfigError(f"{CONFIG_NAME}: {where}: the name is used twice")
        names.add(name)
        step = {"name": name, "run": [], "inputs": [], "outputs": [], "before": "expand",
                "after": "", "documents": [], "when": "build", "check": []}
        for key in ("run", "check"):
            if key in raw:
                step[key] = command_list(raw[key], f"{where}.{key}")
        for key in ("inputs", "outputs", "documents"):
            items = raw.get(key, [])
            if not isinstance(items, list) or not all(isinstance(item, str) and item.strip() for item in items):
                raise ConfigError(f"{CONFIG_NAME}: {where}.{key} must be a list of nonempty strings")
            if key == "outputs" and any(Path(item).anchor or ".." in Path(item).parts for item in items):
                raise ConfigError(f"{CONFIG_NAME}: {where}.{key} must stay inside the project")
            step[key] = list(items)
        if not step["run"] and not step["check"]:
            raise ConfigError(f"{CONFIG_NAME}: {where} needs run or check")
        if "after" in raw:
            if "before" in raw or raw["after"] != "docx":
                raise ConfigError(f'{CONFIG_NAME}: {where}.after must be "docx" (and no before)')
            step["after"], step["before"] = "docx", ""
        elif "before" in raw and raw["before"] not in STEP_SLOTS:
            raise ConfigError(f"{CONFIG_NAME}: {where}.before must be one of {', '.join(STEP_SLOTS)}")
        else:
            step["before"] = raw.get("before", "expand")
        if step["documents"] and not step["after"]:
            raise ConfigError(f'{CONFIG_NAME}: {where}.documents needs after = "docx"')
        if raw.get("when", "build") not in ("build", "submission", "manual"):
            raise ConfigError(f"{CONFIG_NAME}: {where}.when must be build, submission or manual")
        step["when"] = raw.get("when", "build")
        if name in ("values", "tables") and (step["after"] or step["when"] != "build"):
            raise ConfigError(f"{CONFIG_NAME}: {where} replaces a stage: it cannot set after or when")
        steps.append(step)
    return steps


class Project:
    """Paths and commands of one project."""

    def __init__(self, root: Path, config: dict[str, Any], found: bool) -> None:
        self.root = root
        self.config = config
        self.found = found  # False when no trazo.toml exists (defaults only)
        self.submission_documents = []
        self.auto_documents = []
        if config["kind"] == "article":
            settings = config["submission"]
            for name, title, active, word in (
                ("declarations", "Declarations", settings["declarations"] != "end"
                 and bool(list(self.path("declarations").glob("*.md"))), settings["declarations"] == "separate"),
                ("title_page", "Title page", settings["title_page"] == "separate", True),
                ("cover_letter", "Cover letter", bool(list(self.path("cover_letter").glob("*.md"))), True),
            ):
                if active and name not in config["documents"]:
                    self.auto_documents.append(name)
                    config["documents"][name] = {"path": config["paths"][name], "title": title,
                        "number_prefix": "", "docx": str(Path(settings["output"]) / f"{name}.docx") if word else "",
                        **({"build": self.command("submission")} if word else {})}
                    if word:
                        self.submission_documents.append(name)

    def document_roots(self, name: str) -> list[Path]:
        roots = [self.root / self.documents[name]]
        if (self.config["kind"] == "article" and name == next(iter(self.documents))
                and self.config["submission"]["declarations"] == "end"):
            roots.append(self.path("declarations"))
        return list(dict.fromkeys(roots))

    def document_files(self, name: str) -> list[Path]:
        """Ordered Markdown sources, including declarations appended to the manuscript."""
        return list(dict.fromkeys(path for folder in self.document_roots(name)
                                  for path in sorted(folder.glob("*.md"))))

    def source_document(self, paths) -> str | None:
        resolved = [Path(path).resolve() for path in paths]
        for name in self.documents:
            if resolved and all(any(path.is_relative_to(root.resolve())
                                    for root in self.document_roots(name)) for path in resolved):
                return name
        return None

    @classmethod
    def load(cls, start: Path | None = None) -> Project:
        root = find_root(start)
        found = root is not None and (root / CONFIG_NAME).is_file()
        root = root or Path(os.getcwd()).resolve()
        data: dict[str, Any] = {}
        if found:
            try:
                data = tomllib.loads((root / CONFIG_NAME).read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ConfigError(f"cannot read {root / CONFIG_NAME}: {exc}") from exc
        return cls(root, merge(DEFAULTS, data), found)

    def path(self, key: str) -> Path:
        """An absolute path from [paths]."""
        return self.root / self.config["paths"][key]

    def paths(self, key: str) -> list[Path]:
        return [self.root / p for p in self.config["paths"][key]]

    @property
    def documents(self) -> dict[str, str]:
        """Document name -> folder (relative to the root)."""
        return {name: doc["path"] for name, doc in self.config["documents"].items()}

    @property
    def titles(self) -> dict[str, str]:
        return {name: doc["title"] for name, doc in self.config["documents"].items()}

    @property
    def submission_parts(self) -> list[str]:
        """The submission documents of an article (SUBMISSION_PARTS it has):
        the preview lists them under Submission, not as their own tabs."""
        if self.config["kind"] != "article":
            return []
        return [name for name in SUBMISSION_PARTS if name in self.config["documents"]]

    @property
    def number_prefixes(self) -> dict[str, str]:
        """Document name -> figure/table number prefix (empty for ordinary numbers)."""
        return {name: doc["number_prefix"] for name, doc in self.config["documents"].items()}

    def command(self, name: str) -> list[str]:
        """A [commands] entry; [] means trazo's built-in stage. pandoc's
        default falls back to the machine file's Pandoc when PATH has none."""
        if name == "pandoc":
            from .machine import resolve_pandoc
            return resolve_pandoc(self.config["commands"][name])[0]
        return list(self.config["commands"][name])

    def command_hint(self, name: str) -> str:
        """What to run for a [commands] entry."""
        command = self.command(name)
        if command:
            return " ".join(command)
        if name == "submission":
            return "scripts/trazo build --submission" if self.config["kind"] == "article" else "scripts/trazo build"
        return "scripts/trazo build --prepare=inputs"

    def document_build(self, name: str) -> list[str] | None:
        """The full build of a document: its build, else commands.docx for the
        first document; [] means trazo builds it itself (build_graph);
        None when it has no DOCX output."""
        spec = self.config["documents"][name]
        if not spec.get("docx"):
            return None
        if spec.get("build"):
            return list(spec["build"])
        return self.command("docx") if name == next(iter(self.config["documents"])) else []

    def document_docx(self, name: str) -> Path | None:
        """The DOCX a document's full build writes, if any."""
        docx = self.config["documents"][name].get("docx")
        return self.root / docx if docx else None

    def relative(self, key: str) -> str:
        return self.config["paths"][key]

    def chrome(self) -> str:
        """The Chrome (or Chromium) executable used for diagrams: the configured
        one, an installed one, else the one `trazo setup` downloaded; "" if none."""
        configured = (os.environ.get("TRAZO_CHROME") or self.config["browser"]["chrome"]
                      or self.config["diagrams"]["chrome"])
        if configured:
            return configured
        from . import machine  # imports this module, so only now
        return machine.find_chrome()[0]


# Loaded once on import; the CLI reports LOAD_ERROR before running a command.
LOAD_ERROR: ConfigError | None = None
try:
    PROJECT = Project.load()
except ConfigError as _exc:
    LOAD_ERROR = _exc
    PROJECT = Project(Path(os.getcwd()).resolve(), copy.deepcopy(DEFAULTS), False)
