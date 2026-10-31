"""Scenarios of level n2: every module here registers its functions with @scenario."""

from pathlib import Path

from ..registry import import_modules

import_modules(__package__, Path(__file__).parent)
