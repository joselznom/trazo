---
name: paper-figure-generation
user-invocable: false
description: Use when creating or updating generated manuscript figures (plots made by scripts in figures/scripts).
---

# Paper Figure Generation

- Edit scripts in `figures/scripts/`; do not hand-edit files in
  `figures/output/`. Read the data from files under `data/` or
  `inputs/`; do not type numbers into the script.
- A new figure needs a `[[build.steps]]` entry in `trazo.toml`, listing
  every input the script reads (the script itself always counts) and every
  file it writes; `scripts/trazo build` runs it when one changes:

```toml
[[build.steps]]
name = "fig2_name"
run = ["figures/scripts/fig2_name.py"]
inputs = ["figures/scripts/_style.py", "data/example.csv"]
outputs = ["figures/output/png/fig2_name.png"]
before = "diagrams"
```

- Run `scripts/trazo run fig2_name` after changes and inspect the PNG.
- Reference figures in Markdown with a caption and label, and refer to
  them with `[@fig:name]`:

```markdown
![[Short title.]{.caption-title} Legend text.](figures/output/png/fig2_name.png){#fig:name width=4.5in}
```

- Mark the caption's title with `[...]{.caption-title}`: the preview shows
  it in bold, Word keeps its caption style.

- Keep figures readable in Word: clear axes, restrained colors, no
  caption text inside the image. Numbers quoted in the caption use
  `{{v:key}}` (see paper-values).
- If a script needs a missing dependency, report it and the
  `uv add <package>` command instead of changing the workflow silently.
- Schematic diagrams (boxes and arrows) are not plots: use
  diagram-authoring.
