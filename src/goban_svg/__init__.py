"""goban-svg: convert screenshots of Go board positions into clean SVG diagrams.

The package implements a three-stage pipeline (docs/design.md sec 2):
``screenshot -> Position (JSON, faithful) -> SVG``, plus a lossy SGF export for
interop with other Go tools. ``Position`` (board.py) is the human-editable
intermediate that makes the pipeline correctable: extract a screenshot, notice
the OCR misread a move number, hand-edit the JSON, re-render.

This top-level module re-exports the handful of names most callers need
without reaching into submodules (docs/interfaces.md); everything else --
:class:`~goban_svg.board.Mark`'s companion types, the PNG codec internals, the
extractor's tuning constants, and so on -- stays in its owning module. The CLI
(``goban-svg`` console script / ``python -m goban_svg``) is `cli.main`, wired
via ``[project.scripts]`` in pyproject.toml rather than re-exported here.
"""

from __future__ import annotations

from goban_svg.board import Mark, Point, Position, ascii_diagram
from goban_svg.extract import extract_position
from goban_svg.png_codec import load_image
from goban_svg.render import render_png, render_svg

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "Mark",
    "Point",
    "Position",
    "ascii_diagram",
    "extract_position",
    "load_image",
    "render_png",
    "render_svg",
]
