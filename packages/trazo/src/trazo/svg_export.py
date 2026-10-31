"""Export settings diagram exports share: the figure fonts folder and the PNG resolution."""

from __future__ import annotations

from .project import PROJECT

FONT_DIR = PROJECT.path("figure_fonts")
# PNG resolution of exports: about 600 dpi.
DPI = 600
