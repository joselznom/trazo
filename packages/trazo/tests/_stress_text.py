"""Markdown of the stress paper: prose, citations, values, abbreviations, figures,
equations and tables, with the numbers each label must get.

`Doc` writes one document's files and records what a correct build shows:
the number of every figure, panel, table and equation (in order of
definition, with the document's prefix), the keys it cites, and each
paragraph's plain sentences (the quotes review comments are made on).
"""

import re
from collections import Counter

import _stress_assets as assets

WORDS = """analysis baseline cohort signal model feature variance estimate sample protocol
scanner contrast region volume texture intensity outcome response method trial dataset
cluster threshold margin noise weight prior posterior calibration validation measure
uptake lesion tissue marker gradient kernel filter resolution acquisition setting batch
effect correction transfer pattern domain shift error bias interval score ranking selector
subgroup treatment follow imaging radiomics pipeline segmentation reader agreement
sensitivity specificity curve slope bound fold split repeat stable robust reported observed
expected compared adjusted pooled paired independent external internal larger smaller
higher lower earlier later mostly rarely across within between after before during while
""".split()
FOREIGN = ["naïve", "façade", "Zürich", "São Paulo", "Ångström", "μ-opioid", "β-blocker", "Gödel",
           "Øresund", "東京", "Ελλάδα", "señal", "Kraków", "Malmö", "coöperation"]
TERMS = """adaptive boosted calibrated dynamic elastic fused generalized hierarchical iterative
joint kernel latent mixed nonlinear ordinal partial quantile recursive spatial temporal
uniform variational weighted contrast diffusion enhancement estimator imaging network
regression sampling transform""".split()
EQUATIONS = [
    r"\hat{y}_{i} = \beta_0 + \sum_{j=1}^{p} \beta_j x_{ij} + \varepsilon_i",
    r"\mathrm{AUC} = \int_0^1 \mathrm{TPR}\left(\mathrm{FPR}^{-1}(u)\right)\,du",
    "\\begin{aligned}\nL(\\theta) &= \\sum_{i=1}^{n} \\log p(y_i \\mid x_i, \\theta) \\\\\n"
    "&\\quad - \\lambda \\lVert \\theta \\rVert_2^2\n\\end{aligned}",
    r"\kappa = \frac{p_o - p_e}{1 - p_e}",
]


class Context:
    """What both documents share: the bibliography, values and abbreviations."""

    def __init__(self, rng, references: int, abbreviations: int):
        self.rng = rng
        self.keys = [f"{rng.choice(WORDS)}{1990 + i % 35}{chr(97 + i % 26)}{i}" for i in range(references)]
        # Keys a tokenizer may trip on: non-ASCII, colon, dash, dot and underscore.
        self.keys[:4] = ["müller2019ångström", "doe:2020-a", "smith_2021.b", "o-brien2018"]
        self.values: dict[str, dict] = {}
        self.abbreviations = self._abbreviations(abbreviations)
        self.defined: dict[str, set] = {}

    def _abbreviations(self, count: int) -> dict[str, str]:
        found: dict[str, str] = {}
        while len(found) < count:
            words = self.rng.sample(TERMS, self.rng.randint(2, 4))
            term = "".join(word[0] for word in words).upper()
            if term not in found and len(term) > 1:
                found[term] = " ".join(words)
        return found

    def value(self, prefix: str) -> str:
        """A new value's placeholder; one in four is a composite shown with a format."""
        key = f"{prefix}.v{len(self.values)}"
        if self.rng.random() < 0.25:
            mean, sd = round(self.rng.uniform(1, 90), 3), round(self.rng.uniform(0.1, 9), 3)
            self.values[key] = {"value": {"mean": mean, "sd": sd}, "text": f"{mean:.1f}"}
            return f"{{{{v:{key}|mean_sd}}}}"
        number = self.rng.randint(2, 5000)
        self.values[key] = {"value": number, "text": f"{number:,}"}
        return f"{{{{v:{key}}}}}"

    def fixed_value(self, key: str, text: str) -> str:
        self.values[key] = {"value": text, "text": text}
        return f"{{{{v:{key}}}}}"

    def citation(self, cited: set) -> str:
        rng = self.rng
        keys = rng.sample(self.keys, rng.choice([1, 1, 1, 2, 3]))
        form = rng.random()
        if form < 0.15:
            cited.add(keys[0])
            return f"[see @{keys[0]}, pp. 33–35]" if form < 0.1 else f"[-@{keys[0]}]"
        cited.update(keys)
        return "[" + "; ".join(f"@{key}" for key in keys) + "]"

    def abbreviation(self, region: str) -> str:
        """An abbreviation, written out with the short form at its first use in `region`."""
        term = self.rng.choice(list(self.abbreviations))
        seen = self.defined.setdefault(region, set())
        if term in seen:
            return term + ("s" if self.rng.random() < 0.1 else "")
        seen.add(term)
        return f"{self.abbreviations[term]} ({term})"


def wrap(text: str, width: int = 72) -> str:
    """Hard-wrap as authors do, never inside a placeholder, citation or span."""
    lines, line = [], ""
    for word in re.findall(r"\{\{[^}]*\}\}\S*|\[[^\]]*\]\S*|\S+", text):
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}" if line else word
    return "\n".join(lines + [line])


class Doc:
    def __init__(self, ctx: Context, name: str, prefix: str, region: str = "main"):
        self.ctx, self.name, self.prefix, self.region = ctx, name, prefix, region
        self.files: dict[str, list[str]] = {}
        self.chunks: list[str] = []
        self.numbers: dict[str, str] = {}
        self.across: dict[str, str] = {}  # label -> number, for references to another document
        self.panels: list[str] = []  # labels of figures with panels, and of their panels
        self.counters = Counter()
        self.paragraphs: list[dict] = []
        self.cited: set[str] = set()
        self.section, self.section_id, self.heading_text = "", "", ""
        self.para_count = Counter()
        self.footnotes = 0

    def file(self, name: str) -> None:
        self.chunks = self.files.setdefault(name, [])
        self.current = name

    def add(self, text: str) -> None:
        self.chunks.append(text.rstrip("\n") + "\n")

    def heading(self, level: int, text: str, attributes: str = "") -> None:
        self.add(f"{'#' * level} {text}" + (f" {attributes}" if attributes else ""))
        plain = re.sub(r"[*_`]", "", text)
        if level == 1:
            self.section = plain
            ident = re.search(r"#([\w-]+)", attributes)
            self.section_id = ident[1] if ident else re.sub(r"[^\w-]", "", plain.lower().replace(" ", "-"))
        self.heading_text = plain

    def sentence(self, words: tuple[int, int] = (10, 24)) -> str:
        rng = self.ctx.rng
        chosen = [rng.choice(WORDS) for _ in range(rng.randint(*words))]
        if rng.random() < 0.15:
            chosen[rng.randrange(len(chosen))] = rng.choice(FOREIGN)
        text = " ".join(chosen)
        return text[0].upper() + text[1:] + "."

    def decorated(self, refs: bool) -> str:
        """A sentence with one of: a citation, a value, math, an abbreviation,
        a cross-reference, a footnote."""
        rng, ctx = self.ctx.rng, self.ctx
        words = self.sentence()[:-1].split(" ")
        kinds = ["cite", "value", "math", "abbr"] + (["ref"] * 2 if refs and self.numbers else []) + ["note"]
        kind = rng.choice(kinds)
        if kind == "cite":
            words.append(ctx.citation(self.cited))
        elif kind == "value":
            words.insert(rng.randrange(1, len(words)), f"{ctx.value(self.name[:4])} patients")
        elif kind == "math":
            words.insert(rng.randrange(1, len(words)), r"$\beta_{" + str(rng.randint(1, 9)) + r"}=0.5$")
        elif kind == "abbr" and ctx.abbreviations:
            words.insert(rng.randrange(1, len(words)), ctx.abbreviation(self.region))
        elif kind == "ref":
            label = rng.choice(list(self.numbers))
            words.append(f"([@{label}])")
        elif kind == "note":
            self.footnotes += 1
            words[-1] += f"[^{self.name[:1]}{self.footnotes}]"
            self.notes.append(f"[^{self.name[:1]}{self.footnotes}]: A note with {ctx.value('note')} and "
                              f"{ctx.citation(self.cited)}.")
        return " ".join(words) + "."

    def paragraph(self, sentences: int = 5, refs: bool = True) -> None:
        self.notes = []
        rng = self.ctx.rng
        plain, parts = [], []
        for _ in range(sentences):
            if rng.random() < 0.45:
                parts.append(self.decorated(refs))
            else:
                text = self.sentence()
                plain.append(text)
                parts.append(text)
        self.para_count[self.heading_text] += 1
        self.paragraphs.append({"doc": self.name, "file": self.current, "section": self.section,
                                "section_id": self.section_id,
                                "para": f"{self.heading_text} ¶{self.para_count[self.heading_text]}",
                                "plain": plain})
        self.add(wrap(" ".join(parts)) + "\n")
        for note in self.notes:
            self.add(note + "\n")

    def number(self, kind: str) -> str:
        self.counters[kind] += 1
        return f"{self.prefix if kind != 'eq' else ''}{self.counters[kind]}"

    def caption(self, title: str, lines: int = 3) -> str:
        """A caption over several source lines: a title span, emphasis,
        brackets, a value and a citation."""
        ctx = self.ctx
        body = (f"[{title}]{{.caption-title}} {self.sentence()} *Shaded* bands [log scale] show "
                f"{ctx.value('cap')} cases {ctx.citation(self.cited)}. " + " ".join(self.sentence() for _ in range(lines - 2)))
        return wrap(body, 64)

    def figure(self, label: str, image: str, panels: int = 0, inline_panels: bool = False) -> None:
        number = self.number("fig")
        self.numbers[f"fig:{label}"] = number
        letters = "abcdefgh"[:panels]
        if not panels:
            self.add(f"![{self.caption('Figure ' + label + '.')}]({image}){{#fig:{label} width=5in}}\n")
            return
        images = [f"![Panel {letter}, {self.sentence((3, 6))[:-1]}]({image}){{#fig:{label}{letter}}}" for letter in letters]
        self.panels.append(f"fig:{label}")
        for letter in letters:
            self.numbers[f"fig:{label}{letter}"] = f"{number} ({letter})"  # as pandoc-crossref writes it
            self.panels.append(f"fig:{label}{letter}")
        block = ("\n" if inline_panels else "\n\n").join(images)
        self.add(f"::: {{#fig:{label}}}\n{block}\n\n{self.caption('Panels of ' + label + '.')}\n:::\n")

    def equation(self, label: str) -> None:
        self.numbers[f"eq:{label}"] = self.number("eq")
        tex = self.ctx.rng.choice(EQUATIONS)
        self.add(f"$$\n{tex}\n$$ {{#eq:{label}}}\n" if "\n" in tex else f"$$ {tex} $$ {{#eq:{label}}}\n")

    def spec_table(self, root, label: str, kind: str, include: str) -> None:
        self.numbers[f"tbl:{label}"] = self.number("tbl")
        rng = self.ctx.rng
        note = ""
        if rng.random() < 0.5:
            cite = self.ctx.citation(self.cited)
            note = f"Values are shown as {kind} rows {cite}.\n\nA second note paragraph: σ² and ±1.96."
        title = f"Stress {kind} table {label} of the {{n}} records."
        assets.table(root, label, kind, rng, WORDS, title, self.sentence(), note,
                     rows=max(30, int(200 * min(1.0, rng.random() + 0.5))))
        self.add(f"!include {include}/{label}.md\n")

    def markdown_table(self, label: str) -> None:
        """A pipe table typed by hand, with a caption wrapped over lines."""
        self.numbers[f"tbl:{label}"] = self.number("tbl")
        rng = self.ctx.rng
        caption = wrap(f"Table: [Hand-typed {label}.]{{.caption-title}} {self.sentence()} Values"
                       f" {self.ctx.value('tab')} and {self.ctx.citation(self.cited)} {{#tbl:{label}}}", 60)
        rows = "\n".join(f"| {rng.choice(WORDS)} {rng.choice(FOREIGN)} | {rng.randint(1, 999)} | "
                         f"{rng.uniform(0, 1):.3f} |" for _ in range(rng.randint(3, 12)))
        self.add(f"{caption}\n\n| Item | *n* | Value |\n|:-----|----:|------:|\n{rows}\n")

    def ref(self, kind: str) -> str:
        labels = [label for label in self.numbers if label.startswith(kind + ":")]
        return f"[@{self.ctx.rng.choice(labels)}]" if labels else ""

    def write(self, root) -> None:
        for name, chunks in self.files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(chunks), encoding="utf-8")
