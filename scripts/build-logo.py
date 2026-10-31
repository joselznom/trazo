#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["fonttools==4.67.0", "uharfbuzz"]
# ///
"""Write the trazo logo files in brand/, and the preview's favicon and
top-bar wordmark, from Fraunces Italic.

The letters are outlined, so the SVGs look the same on machines without
the font. The font is downloaded from a pinned google/fonts commit and
checked by sha256; only its licence is kept in brand/. PNGs are rendered
with headless Chrome and scaled down with sips, so they need macOS.

Usage: scripts/build-logo.py
"""

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

import uharfbuzz as hb
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

FONTS = "https://raw.githubusercontent.com/google/fonts/ac502d8eff76ef4d9477cdcc8ef7d0c84fde5372/ofl/fraunces/"
FONT_FILE = "Fraunces-Italic%5BSOFT,WONK,opsz,wght%5D.ttf"
FONT_SHA256 = "b24448c43702fac4ee856781d461a0dfba8d8e594b6e8e190234b75fed2c0e01"
AXES = {"wght": 560, "opsz": 48, "SOFT": 0, "WONK": 0}
INK, PAPER, RED, WHITE = "#1C2541", "#F7F3EA", "#E4572E", "#FFFFFF"
# The dot is the data point a number comes from; sizes in em.
DOT_R, DOT_GAP = 0.085, 0.05
PNG_SIZES = (1024, 512, 256, 128, 64, 32, 16)
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


class Font:
    def __init__(self, path):
        self.tt = instantiateVariableFont(TTFont(path), AXES)
        self.upem = self.tt["head"].unitsPerEm
        self.glyphs = self.tt.getGlyphSet()
        self.names = self.tt.getGlyphOrder()
        self.hb = hb.Font(hb.Face(hb.Blob.from_file_path(str(path))))
        self.hb.set_variations(AXES)

    def outline(self, text, ntos=str):
        """Return (SVG path in em with y up, advance in em, bounds)."""
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(self.hb, buf, {"kern": True, "liga": False})
        path, bounds = SVGPathPen(self.glyphs, ntos), BoundsPen(self.glyphs)
        x = 0
        for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
            matrix = (1 / self.upem, 0, 0, 1 / self.upem, (x + pos.x_offset) / self.upem, 0)
            glyph = self.glyphs[self.names[info.codepoint]]
            glyph.draw(TransformPen(path, matrix))
            glyph.draw(TransformPen(bounds, matrix))
            x += pos.x_advance
        return path.getCommands(), x / self.upem, bounds.bounds

    def art(self, text, fg, dot, ntos=str):
        """Glyphs plus the dot in em with y down; returns (SVG group, bbox)."""
        d, adv, (x0, y0, x1, y1) = self.outline(text, ntos)
        cx = adv + DOT_GAP + DOT_R
        group = (f'<g transform="scale(1 -1)"><path d="{d}" fill="{fg}"/></g>'
                 f'<circle cx="{cx:.4f}" cy="{-DOT_R:.4f}" r="{DOT_R}" fill="{dot}"/>')
        return group, (min(x0, 0), -y1, max(x1, cx + DOT_R), max(-y0, 0))

    def standalone(self, text, fg, dot, height=None, pad=0.06, ntos=str):
        """A whole SVG; without `height`, its size is left to the page."""
        group, (x0, y0, x1, y1) = self.art(text, fg, dot, ntos)
        x0, y0, x1, y1 = x0 - pad, y0 - pad, x1 + pad, y1 + pad
        size = "" if height is None else f'width="{height * (x1 - x0) / (y1 - y0):.0f}" height="{height}" '
        return (f'<svg xmlns="http://www.w3.org/2000/svg" {size}'
                f'viewBox="{x0:.4f} {y0:.4f} {x1 - x0:.4f} {y1 - y0:.4f}">{group}</svg>\n')

    def icon(self, bg, fg, dot, size=1024, fill=0.60):
        group, (x0, y0, x1, y1) = self.art("t", fg, dot)
        s = size * fill / (y1 - y0)
        tx = (size - s * (x1 - x0)) / 2 - s * x0
        ty = (size - s * (y1 - y0)) / 2 - s * y0
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
                f'viewBox="0 0 {size} {size}"><rect width="{size}" height="{size}" '
                f'rx="{size * 0.23:.0f}" fill="{bg}"/>'
                f'<g transform="translate({tx:.2f} {ty:.2f}) scale({s:.2f})">{group}</g></svg>\n')


def fetch(name, dest):
    with urllib.request.urlopen(FONTS + name) as response:
        dest.write_bytes(response.read())
    return dest


def render_png(svg, out_dir, tmp):
    page = tmp / "render.html"
    page.write_text(f'<html><body style="margin:0"><img src="{svg.as_uri()}" width="1024" height="1024"></body></html>')
    big = out_dir / f"{svg.stem}-1024.png"
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                    "--default-background-color=00000000", "--window-size=1024,1024",
                    f"--screenshot={big}", page.as_uri()], check=True, capture_output=True)
    for size in PNG_SIZES[1:]:
        subprocess.run(["sips", "-s", "format", "png", "-z", str(size), str(size), str(big),
                        "--out", str(out_dir / f"{svg.stem}-{size}.png")], check=True, capture_output=True)


def main():
    repo = Path(__file__).resolve().parent.parent
    brand = repo / "brand"
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        font_path = fetch(FONT_FILE, tmp / "Fraunces-Italic.ttf")
        if hashlib.sha256(font_path.read_bytes()).hexdigest() != FONT_SHA256:
            sys.exit("Fraunces-Italic.ttf does not match its pinned sha256")
        font = Font(font_path)
        files = {
            "trazo-mark.svg": font.standalone("t", INK, RED, 512),
            "trazo-mark-white.svg": font.standalone("t", WHITE, RED, 512),
            "trazo-wordmark.svg": font.standalone("trazo", INK, RED, 160),
            "trazo-wordmark-white.svg": font.standalone("trazo", WHITE, RED, 160),
            "trazo-icon-ink.svg": font.icon(INK, WHITE, RED),
            "trazo-icon-paper.svg": font.icon(PAPER, INK, RED),
            "trazo-icon-red.svg": font.icon(RED, WHITE, INK),
        }
        svg_dir, png_dir = brand / "svg", brand / "png"
        for d in (svg_dir, png_dir):
            shutil.rmtree(d, ignore_errors=True)
            d.mkdir(parents=True)
        for name, svg in files.items():
            (svg_dir / name).write_text(svg)
        fetch("OFL.txt", brand / "OFL-Fraunces.txt")
        # The package ships without brand/, so the preview keeps its own copy.
        (repo / "packages/trazo/src/trazo/assets/app/favicon.svg").write_text(files["trazo-icon-ink.svg"])
        # The top bar's logo is inline markup, so its currentColor letters follow the
        # theme and the MCP App's bundled page needs no other file.
        # HTML needs no xmlns on inline SVG, and the viewer scripts hold no external URLs.
        word = font.standalone("trazo", "currentColor", RED, ntos=lambda v: f"{v:.4f}").strip()
        word = word.replace(' xmlns="http://www.w3.org/2000/svg"', "")
        (repo / "packages/trazo/src/trazo/assets/preview/preview-wordmark.js").write_text(
            "// Generated by scripts/build-logo.py: the top bar's trazo wordmark.\n"
            f"const WORDMARK = '{word}';\n")
        for svg in sorted(svg_dir.glob("trazo-icon-*.svg")):
            render_png(svg, png_dir, tmp)
    print(f"Wrote {len(files)} SVGs and {3 * len(PNG_SIZES)} PNGs in {brand}")


if __name__ == "__main__":
    main()
