# trazo brand

The logo is an italic serif "t" followed by a dot: the dot is the data
point every number in the paper comes from. The wordmark is "trazo"
with the same dot.

| File | Use |
| --- | --- |
| `svg/trazo-mark.svg`, `svg/trazo-mark-white.svg` | The "t" and dot on light or dark backgrounds |
| `svg/trazo-wordmark.svg`, `svg/trazo-wordmark-white.svg` | The name, on light or dark backgrounds |
| `svg/trazo-icon-{ink,paper,red}.svg` | App icon, rounded square; ink is the default |
| `png/trazo-icon-*-{16..1024}.png` | The icons as PNG, 16 to 1024 px |

The plugin manifests use the ink icon: `.claude-plugin/plugin.json`
(`icon`, for Anthropic's directory) and `.codex-plugin/plugin.json`
(`interface.logo` and `interface.composerIcon`).

## Colours

| Name | Hex | Role |
| --- | --- | --- |
| Ink | `#1C2541` | Letters, default icon background |
| Paper | `#F7F3EA` | Light background |
| Red | `#E4572E` | The dot |

On a red background the dot turns ink.

## Type

Fraunces Italic, licensed under the SIL Open Font License
(`OFL-Fraunces.txt`), at weight 560, optical size 48, SOFT 0, WONK 0.
The SVGs contain outlines, not text, so they need no font installed.

## Changing the logo

Every file here, and the preview's favicon and top-bar wordmark
(`packages/trazo/src/trazo/assets/app/favicon.svg` and
`assets/preview/preview-wordmark.js`), is generated:
change `scripts/build-logo.py`, never the files, and rerun it (macOS
with Google Chrome; `uv` installs the rest):

```sh
scripts/build-logo.py
```
