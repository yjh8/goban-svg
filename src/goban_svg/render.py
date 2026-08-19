"""The two renderers: ``Position`` -> clean SVG, and ``Position`` -> app-style PNG.

Both draw the same board through one :class:`BoardGeometry` (cell size ``c``, a
wood margin of ``0.72c`` beyond the outer lines, optional coordinate gutters on
the left and bottom), but they exist for opposite reasons:

``render_svg`` is **the deliverable** (design.md sec 5). It produces the polished,
scalable diagram a human wants: flat wood, hairline grid with a heavier border,
radial-gradient stones, auto-contrasting labels, and marks drawn the way a Go
diagram draws them (the source app's corner "wedge" badge becomes a centered
outline triangle on the stone).

``render_png`` is **the extractor's fixture generator** (design.md sec 5/9). It
deliberately mimics the *source app's* raster look — a dark UI frame around the
wood, glossy black stones, solid corner wedges, solid square markers on empty
points, bitmap-stamped move numbers, optional deterministic noise — so that
``extract.py`` can be tested end to end with zero external image fixtures. That
makes several numbers in the painter load-bearing rather than cosmetic: they are
the geometry ``extract.py`` probes at. Each such constant says so at its
definition; changing one silently breaks the round-trip tests in a way that looks
like an extractor bug. The clean SVG has no such coupling and is free to differ
(and does, e.g. filled corner wedge vs centered outline triangle).

Neither renderer needs a Position to be "real" Go: it draws exactly the stones,
marks and labels it is given.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from xml.sax.saxutils import escape

from goban_svg import digits
from goban_svg.board import COLUMN_LETTERS, Mark, Point, Position, star_points
from goban_svg.png_codec import Image

__all__ = [
    "BLACK_GRADIENT_ID",
    "KGS_PALETTE",
    "WHITE_GRADIENT_ID",
    "BoardGeometry",
    "Palette",
    "render_png",
    "render_svg",
]

# --------------------------------------------------------------------------- #
# Shared geometry ratios (fractions of one cell). See design.md sec 5.
# --------------------------------------------------------------------------- #

MARGIN_RATIO = 0.72
"""Wood extends this far beyond the outermost grid line, on every side."""

COORD_GUTTER_RATIO = 0.8
"""Extra band (left + bottom) that holds the coordinate letters/numbers.

design.md fixes the wood margin but leaves the gutter width to the renderer;
0.8c comfortably fits a 0.42c-tall glyph plus breathing room on both sides.
"""

STONE_RADIUS_RATIO = 0.47
HOSHI_RADIUS_RATIO = 0.105


# --------------------------------------------------------------------------- #
# Geometry
# --------------------------------------------------------------------------- #


@dataclass
class BoardGeometry:
    """Maps board coordinates to canvas coordinates for a ``size``x``size`` board.

    Layout on each axis is ``[gutter?][margin][ (size-1) * cell ][margin]``, with
    the gutter present only on the left and bottom, and only when ``coords`` is
    set.

    The y axis is flipped relative to the board model: ``Point.row`` 1 is the
    *bottom* line of the board but the *largest* y on the canvas, since SVG (and
    every raster format here) counts y downward from the top. That flip lives
    here and only here -- callers should never do their own row arithmetic.
    """

    size: int
    cell: float
    coords: bool = False

    @property
    def margin(self) -> float:
        """Wood beyond the outer lines, on all four sides."""
        return MARGIN_RATIO * self.cell

    @property
    def gutter(self) -> float:
        """Coordinate band on the left and bottom (0 when ``coords`` is off)."""
        return COORD_GUTTER_RATIO * self.cell if self.coords else 0.0

    @property
    def line_span(self) -> float:
        """Distance from the first grid line to the last, on either axis."""
        return (self.size - 1) * self.cell

    @property
    def origin_x(self) -> float:
        """Canvas x of the column-1 (leftmost) grid line."""
        return self.gutter + self.margin

    @property
    def origin_y(self) -> float:
        """Canvas y of the row-``size`` (topmost) grid line."""
        return self.margin

    @property
    def width(self) -> float:
        return self.origin_x + self.line_span + self.margin

    @property
    def height(self) -> float:
        return self.origin_y + self.line_span + self.margin + self.gutter

    def point_xy(self, p: Point) -> tuple[float, float]:
        """Canvas (x, y) of a board point; row 1 lands at the bottom of the board."""
        return (
            self.origin_x + (p.col - 1) * self.cell,
            self.origin_y + (self.size - p.row) * self.cell,
        )


def _point_key(p: Point) -> tuple[int, int]:
    """Sort key giving both renderers a stable, diffable draw order."""
    return (p.col, p.row)


# --------------------------------------------------------------------------- #
# SVG renderer -- the deliverable
# --------------------------------------------------------------------------- #

WOOD_COLOR = "#e6c37a"
LINE_COLOR = "#43361f"
WHITE_STONE_STROKE = "#444444"
"""White stones and white square marks are outlined so they read against wood."""

LABEL_ON_BLACK = "#f5f5f5"
LABEL_ON_WHITE = "#151515"
MARK_BLACK = "#1a1a1a"
MARK_WHITE = "#ffffff"

BLACK_GRADIENT_ID = "stone-black"
WHITE_GRADIENT_ID = "stone-white"

_LINE_WIDTH_RATIO = 0.038
_BORDER_WIDTH_RATIO = 0.066
_STONE_STROKE_RATIO = 0.03
_MARK_STROKE_RATIO = 0.06
_SQUARE_MARK_HALF_RATIO = 0.21
_TRIANGLE_RADIUS_RATIO = 0.36
_CIRCLE_MARK_RADIUS_RATIO = 0.30
_CROSS_MARK_RADIUS_RATIO = 0.26
_BACKING_DISC_RATIO = 0.42
"""Wood-colored disc painted under a hollow mark on an empty point, so the grid
lines do not run through it (design.md sec 5)."""

_COORD_FONT_RATIO = 0.42
_FONT_FAMILY = "Helvetica, Arial, sans-serif"

# Label font size by character count (design.md sec 5): a 3-character label has
# to fit inside the same 0.94c-wide stone as a 1-character one.
_LABEL_FONT_RATIOS = {1: 0.52, 2: 0.46}
_LABEL_FONT_RATIO_LONG = 0.36


def _f(value: float) -> str:
    """Format a coordinate for SVG: 3 decimals, trailing zeros trimmed."""
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _gradient(gradient_id: str, stops: tuple[str, str, str]) -> str:
    """A stone's radial gradient: highlight offset up-left, per design.md sec 5."""
    inner, mid, outer = stops
    return (
        f'<radialGradient id="{gradient_id}" cx="36%" cy="32%" r="65%">'
        f'<stop offset="0%" stop-color="{inner}"/>'
        f'<stop offset="55%" stop-color="{mid}"/>'
        f'<stop offset="100%" stop-color="{outer}"/>'
        "</radialGradient>"
    )


def _label_font_size(text: str, cell: float) -> float:
    return _LABEL_FONT_RATIOS.get(len(text), _LABEL_FONT_RATIO_LONG) * cell


def _svg_text(x: float, y: float, text: str, font_size: float, fill: str) -> str:
    """A centered text element.

    ``dy="0.35em"`` is the vertical-centering trick used throughout: SVG's y is
    the text baseline, and shifting it down by ~0.35em puts the optical center of
    a digit on y (SVG 1.1 has no ``dominant-baseline`` support worth relying on
    across renderers).
    """
    return (
        f'<text x="{_f(x)}" y="{_f(y)}" font-family="{_FONT_FAMILY}" font-size="{_f(font_size)}" '
        f'text-anchor="middle" dy="0.35em" fill="{fill}">{escape(text)}</text>'
    )


def _resolve_mark_color(mark: Mark, stone: str | None) -> str:
    """CSS color for a mark: its recorded color, else auto-contrast with the stone."""
    if mark.color == "black":
        return MARK_BLACK
    if mark.color == "white":
        return MARK_WHITE
    if mark.color is not None:
        return mark.color  # "#rrggbb", validated by Position.validate()
    if stone == "black":
        return LABEL_ON_BLACK
    return LABEL_ON_WHITE


def _svg_mark(mark: Mark, stone: str | None, x: float, y: float, cell: float) -> list[str]:
    """Draw one mark; hollow marks on an empty point get a wood backing disc first."""
    color = _resolve_mark_color(mark, stone)
    stroke_width = _MARK_STROKE_RATIO * cell
    parts: list[str] = []

    filled_square = mark.type == "square" and stone is None
    if stone is None and not filled_square:
        parts.append(f'<circle cx="{_f(x)}" cy="{_f(y)}" r="{_f(_BACKING_DISC_RATIO * cell)}" fill="{WOOD_COLOR}"/>')

    if mark.type == "triangle":
        r = _TRIANGLE_RADIUS_RATIO * cell
        points = []
        for degrees in (-90.0, 30.0, 150.0):
            theta = math.radians(degrees)
            points.append(f"{_f(x + r * math.cos(theta))},{_f(y + r * math.sin(theta))}")
        parts.append(
            f'<polygon points="{" ".join(points)}" fill="none" stroke="{color}" '
            f'stroke-width="{_f(stroke_width)}" stroke-linejoin="round"/>'
        )
    elif mark.type == "square":
        half = _SQUARE_MARK_HALF_RATIO * cell
        rect = f'<rect x="{_f(x - half)}" y="{_f(y - half)}" width="{_f(2 * half)}" height="{_f(2 * half)}"'
        if filled_square:
            # On an empty point the app's square marker is solid; a white one needs
            # an outline or it disappears into the wood (design.md sec 5).
            stroke = f' stroke="{WHITE_STONE_STROKE}" stroke-width="{_f(_STONE_STROKE_RATIO * cell)}"'
            parts.append(f'{rect} fill="{color}"{stroke if color == MARK_WHITE else ""}/>')
        else:
            parts.append(f'{rect} fill="none" stroke="{color}" stroke-width="{_f(stroke_width)}"/>')
    elif mark.type == "circle":
        parts.append(
            f'<circle cx="{_f(x)}" cy="{_f(y)}" r="{_f(_CIRCLE_MARK_RADIUS_RATIO * cell)}" '
            f'fill="none" stroke="{color}" stroke-width="{_f(stroke_width)}"/>'
        )
    elif mark.type == "cross":
        r = _CROSS_MARK_RADIUS_RATIO * cell
        for dx, dy in ((r, r), (r, -r)):
            parts.append(
                f'<line x1="{_f(x - dx)}" y1="{_f(y - dy)}" x2="{_f(x + dx)}" y2="{_f(y + dy)}" '
                f'stroke="{color}" stroke-width="{_f(stroke_width)}" stroke-linecap="round"/>'
            )
    return parts


def render_svg(pos: Position, *, cell: float = 36.0, coords: bool = False) -> str:
    """Render a position as a standalone SVG document (design.md sec 5).

    ``cell`` is the grid spacing in user units; everything else scales off it, so
    the output is resolution-independent and the caller only picks one number.
    ``coords`` adds column letters along the bottom and row numbers down the left
    (skipping "I", per Go convention), which widens and heightens the canvas.
    """
    pos.validate()
    geo = BoardGeometry(size=pos.size, cell=cell, coords=coords)
    c = float(cell)
    span = geo.line_span
    x0, y0 = geo.origin_x, geo.origin_y

    out: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_f(geo.width)}" height="{_f(geo.height)}" '
        f'viewBox="0 0 {_f(geo.width)} {_f(geo.height)}">',
        "<defs>",
        _gradient(BLACK_GRADIENT_ID, ("#6f6f6f", "#2a2a2a", "#000000")),
        _gradient(WHITE_GRADIENT_ID, ("#ffffff", "#f2f2f2", "#d4d4d4")),
        "</defs>",
        # The whole canvas is wood, so the coordinate gutters read as board edge
        # rather than as transparent page.
        f'<rect x="0" y="0" width="{_f(geo.width)}" height="{_f(geo.height)}" fill="{WOOD_COLOR}"/>',
        f'<g stroke="{LINE_COLOR}" stroke-width="{_f(_LINE_WIDTH_RATIO * c)}" stroke-linecap="square">',
    ]
    for i in range(pos.size):
        y = y0 + i * c
        out.append(f'<line x1="{_f(x0)}" y1="{_f(y)}" x2="{_f(x0 + span)}" y2="{_f(y)}"/>')
    for i in range(pos.size):
        x = x0 + i * c
        out.append(f'<line x1="{_f(x)}" y1="{_f(y0)}" x2="{_f(x)}" y2="{_f(y0 + span)}"/>')
    out.append("</g>")
    out.append(
        f'<rect x="{_f(x0)}" y="{_f(y0)}" width="{_f(span)}" height="{_f(span)}" fill="none" '
        f'stroke="{LINE_COLOR}" stroke-width="{_f(_BORDER_WIDTH_RATIO * c)}"/>'
    )

    for p in sorted(star_points(pos.size), key=_point_key):
        x, y = geo.point_xy(p)
        out.append(f'<circle cx="{_f(x)}" cy="{_f(y)}" r="{_f(HOSHI_RADIUS_RATIO * c)}" fill="{LINE_COLOR}"/>')

    radius = _f(STONE_RADIUS_RATIO * c)
    for p in sorted(pos.stones, key=_point_key):
        x, y = geo.point_xy(p)
        if pos.stones[p] == "black":
            out.append(f'<circle cx="{_f(x)}" cy="{_f(y)}" r="{radius}" fill="url(#{BLACK_GRADIENT_ID})"/>')
        else:
            out.append(
                f'<circle cx="{_f(x)}" cy="{_f(y)}" r="{radius}" fill="url(#{WHITE_GRADIENT_ID})" '
                f'stroke="{WHITE_STONE_STROKE}" stroke-width="{_f(_STONE_STROKE_RATIO * c)}"/>'
            )

    for p in sorted(pos.marks, key=_point_key):
        x, y = geo.point_xy(p)
        out.extend(_svg_mark(pos.marks[p], pos.stones.get(p), x, y, c))

    for p in sorted(pos.labels, key=_point_key):
        text = pos.labels[p]
        if not text:
            continue
        x, y = geo.point_xy(p)
        stone = pos.stones.get(p)
        if stone is None and p not in pos.marks:
            # A label on an empty point sits on a wood disc for the same reason a
            # hollow mark does: grid lines must not run through the glyph.
            #
            # Only when the point has NO mark, though. Labels are painted after
            # marks, so an unconditional disc here would paint over the mark drawn
            # a moment ago and erase it -- and a marked point needs no disc of its
            # own anyway: _svg_mark already laid one under a hollow mark, and a
            # filled mark IS the backing.
            out.append(f'<circle cx="{_f(x)}" cy="{_f(y)}" r="{_f(_BACKING_DISC_RATIO * c)}" fill="{WOOD_COLOR}"/>')
        fill = LABEL_ON_BLACK if stone == "black" else LABEL_ON_WHITE
        out.append(_svg_text(x, y, text, _label_font_size(text, c), fill))

    if coords:
        font_size = _COORD_FONT_RATIO * c
        letter_y = y0 + span + geo.margin + geo.gutter / 2.0
        for col in range(1, pos.size + 1):
            x = x0 + (col - 1) * c
            out.append(_svg_text(x, letter_y, COLUMN_LETTERS[col - 1], font_size, LINE_COLOR))
        number_x = geo.gutter / 2.0
        for row in range(1, pos.size + 1):
            _, y = geo.point_xy(Point(1, row))
            out.append(_svg_text(number_x, y, str(row), font_size, LINE_COLOR))

    out.append("</svg>")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- #
# PNG painter -- the extractor's fixture generator
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Palette:
    """Colors for :func:`render_png`.

    Defaults approximate the source app; ``KGS_PALETTE`` is a second, visibly
    different wood so extractor round-trip tests can prove they are not tuned to
    one exact shade (design.md sec 9). Every color here must keep the properties
    the extractor's classifiers rely on (design.md gotcha G1): wood stays warm
    (``r - b`` well above 45) and bright, stones stay near-neutral.
    """

    wood: tuple[int, int, int] = (231, 196, 122)
    line: tuple[int, int, int] = (67, 54, 31)  # == the SVG's LINE_COLOR #43361f
    frame: tuple[int, int, int] = (30, 30, 34)
    black_stone: tuple[int, int, int] = (25, 25, 25)
    white_stone: tuple[int, int, int] = (240, 240, 240)
    stone_rim: tuple[int, int, int] = (70, 70, 70)
    highlight: tuple[int, int, int] = (238, 238, 238)
    mark_black: tuple[int, int, int] = (20, 20, 20)
    mark_white: tuple[int, int, int] = (240, 240, 240)


KGS_PALETTE = Palette(wood=(220, 179, 92))

# Painter ratios. The ones marked EXTRACTOR-COUPLED are probed for by extract.py
# at exactly these offsets -- see design.md sec 6 and the module docstring.
_FRAME_RATIO = 1.0
"""Dark UI chrome around the wood, so the extractor's wood-bbox stage has a real
border to find rather than a board that runs to the image edge."""

_GRID_WIDTH_RATIO = 0.04
_PNG_HOSHI_RATIO = 0.10
_STONE_RIM_RATIO = 0.045
_HIGHLIGHT_RADIUS_RATIO = 0.08
_HIGHLIGHT_DISTANCE_RATIO = 0.17
"""Specular highlight center distance from the stone center, up-left along the
diagonal (gotcha G2: the app's black stones are glossy and the highlight sits at
~0.15-0.2 cell, well inside the 0.33-cell wedge patch)."""

_WEDGE_LEG_RATIO = 0.36
"""EXTRACTOR-COUPLED. Legs of the solid corner wedge, along the two cell edges
from a corner of the stone's cell. The real app's badges measure 0.31c-0.45c
(2026-08-19, the three committed screenshots); 0.36c sits in that band. The
extractor accepts a quadrant-pure component reaching >= 0.38c from the stone
center on one axis, which the corner-hugging triangle satisfies at any leg in
the band -- do not shrink below ~0.20c or the component drops under the
extractor's minimum area."""

_PNG_WEDGE_CORNERS: tuple[tuple[int, int], ...] = ((-1, -1), (1, -1), (-1, 1), (1, 1))
"""The real app puts the badge in whichever cell corner suits it (all three
screenshots differ). The clean Position model deliberately does not record the
corner, so the painter picks one deterministically per point -- which also makes
the round-trip fixtures exercise every quadrant of the extractor."""

_PNG_SQUARE_HALF_RATIO = 0.22
"""EXTRACTOR-COUPLED. Half-width of a solid square marker on an empty point:
big enough to cover the extractor's 0.20c classification disc completely, small
enough never to reach its 0.36c ring -- which is exactly how gotcha G4 tells a
marker apart from a stone."""

_LABEL_WIDTH_RATIO = 0.55
"""EXTRACTOR-COUPLED. Target label width as a fraction of a cell. The stamped
glyphs must stay inside extract.py's OCR mask window (half-width 0.30c), so the
per-unit scale is derived from this rather than chosen freely."""

_PNG_CIRCLE_MARK_RATIO = 0.22
_PNG_CROSS_MARK_RATIO = 0.22


def _clamp_byte(value: int) -> int:
    return 0 if value < 0 else (255 if value > 255 else value)


def _fill_rect(img: Image, x0: int, y0: int, x1: int, y1: int, rgb: tuple[int, int, int]) -> None:
    """Fill an inclusive integer rectangle, clipped to the image.

    Rows are written with one slice assignment each; the painter fills large
    areas (wood, stones) and a per-pixel Python loop would dominate its runtime.
    """
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(img.width - 1, x1), min(img.height - 1, y1)
    if x1 < x0 or y1 < y0:
        return
    row = bytes(rgb) * (x1 - x0 + 1)
    stride = img.width * 3
    for y in range(y0, y1 + 1):
        start = y * stride + x0 * 3
        img.pixels[start : start + len(row)] = row


def _fill_disc(img: Image, cx: float, cy: float, r: float, rgb: tuple[int, int, int]) -> None:
    """Fill a disc as one horizontal span per scanline (pixel centers on integers)."""
    if r <= 0:
        return
    rr = r * r
    for y in range(int(math.floor(cy - r)), int(math.ceil(cy + r)) + 1):
        dy = y - cy
        if abs(dy) > r:
            continue
        half = math.sqrt(max(0.0, rr - dy * dy))
        _fill_rect(img, int(math.ceil(cx - half)), y, int(math.floor(cx + half)), y, rgb)


def _fill_corner_wedge(
    img: Image, x0: float, y0: float, leg: float, rgb: tuple[int, int, int], dirx: int = 1, diry: int = 1
) -> None:
    """Fill the right triangle with its square corner at (x0, y0) and legs of
    the given length running toward (dirx, diry) -- i.e. from a cell corner in
    toward the stone center, whichever corner it is."""
    for k in range(int(leg) + 1):
        remaining = leg - k
        y = int(round(y0)) + k * diry
        xa = int(round(x0))
        xb = int(round(x0 + dirx * remaining))
        _fill_rect(img, min(xa, xb), y, max(xa, xb), y, rgb)


def _stroke_rect(img: Image, cx: float, cy: float, half: float, width: int, rgb: tuple[int, int, int]) -> None:
    """Outline a square centered on (cx, cy) with the given half-width."""
    x0, y0 = int(round(cx - half)), int(round(cy - half))
    x1, y1 = int(round(cx + half)), int(round(cy + half))
    _fill_rect(img, x0, y0, x1, y0 + width - 1, rgb)
    _fill_rect(img, x0, y1 - width + 1, x1, y1, rgb)
    _fill_rect(img, x0, y0, x0 + width - 1, y1, rgb)
    _fill_rect(img, x1 - width + 1, y0, x1, y1, rgb)


def _paint_specular(img: Image, cx: float, cy: float, cell: float, palette: Palette) -> None:
    """Paint a glossy highlight on a black stone (gotcha G2).

    Brightness falls off from the center rather than being a flat bright blob:
    that is both what a real specular looks like and what keeps the highlight from
    impersonating a glyph in extract.py's on-stone label mask (which keeps pixels
    with lum >= 180 and drops connected specks under 8 px). A flat blob of radius
    0.08c is ~20 px at typical cell sizes -- large enough to survive that filter
    and wreck OCR on every labelled black stone.
    """
    radius = _HIGHLIGHT_RADIUS_RATIO * cell
    if radius < 1.0:
        return
    offset = _HIGHLIGHT_DISTANCE_RATIO * cell / math.sqrt(2.0)
    hx, hy = cx - offset, cy - offset
    base = palette.black_stone
    peak = palette.highlight
    rr = radius * radius
    for y in range(int(math.floor(hy - radius)), int(math.ceil(hy + radius)) + 1):
        for x in range(int(math.floor(hx - radius)), int(math.ceil(hx + radius)) + 1):
            if not (0 <= x < img.width and 0 <= y < img.height):
                continue
            d2 = (x - hx) ** 2 + (y - hy) ** 2
            if d2 > rr:
                continue
            t = (1.0 - d2 / rr) ** 2
            img.set(
                x,
                y,
                (
                    _clamp_byte(int(base[0] + (peak[0] - base[0]) * t)),
                    _clamp_byte(int(base[1] + (peak[1] - base[1]) * t)),
                    _clamp_byte(int(base[2] + (peak[2] - base[2]) * t)),
                ),
            )


def _resolve_png_color(color: str | None, stone: str | None, palette: Palette) -> tuple[int, int, int]:
    """Resolve a mark color to RGB: named -> neutral dark/bright, "#rrggbb" -> bytes,
    None -> auto-contrast against the stone underneath."""
    if color is None:
        return palette.mark_white if stone == "black" else palette.mark_black
    if color == "black":
        return palette.mark_black
    if color == "white":
        return palette.mark_white
    text = color.lstrip("#")
    return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))


@dataclass(frozen=True)
class _RasterGeometry:
    """Integer pixel geometry for the painter (the SVG's float geometry plus the
    dark UI frame, and rounded to whole pixels)."""

    size: int
    cell: int
    frame: int
    margin: int

    @property
    def origin(self) -> int:
        """Pixel x of the column-1 line / pixel y of the row-``size`` line."""
        return self.frame + self.margin

    @property
    def side(self) -> int:
        return (self.size - 1) * self.cell + 2 * self.margin + 2 * self.frame

    def xy(self, p: Point) -> tuple[int, int]:
        return (self.origin + (p.col - 1) * self.cell, self.origin + (self.size - p.row) * self.cell)


def _apply_noise(img: Image, amplitude: int, seed: int) -> None:
    """Add +/-``amplitude`` per-channel noise using a deterministic LCG.

    Deliberately not ``random``: fixtures must be byte-identical across runs,
    machines and Python versions, so the generator is spelled out here (the
    classic glibc constants) instead of depending on the stdlib's stream staying
    stable. Same seed -> same image, always.
    """
    if amplitude <= 0:
        return
    span = 2 * amplitude + 1
    # lut[v + r] == clamp(v + r - amplitude) for v in 0..255, r in 0..2*amplitude:
    # one lookup replaces an add, two comparisons and a clamp per channel byte.
    lut = [_clamp_byte(i - amplitude) for i in range(256 + 2 * amplitude)]
    pixels = img.pixels
    state = (seed & 0x7FFFFFFF) or 1
    for i in range(len(pixels)):
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        pixels[i] = lut[pixels[i] + (state >> 8) % span]


def render_png(
    pos: Position,
    *,
    cell: int = 32,
    palette: Palette | None = None,
    coords: bool = False,
    noise: int = 0,
    seed: int = 1,
) -> Image:
    """Paint a position in the *source app's* raster style (design.md sec 5).

    This is the fixture generator behind the extractor's round-trip tests, so it
    mimics the app rather than the clean SVG: a dark UI frame around the wood,
    glossy black stones, triangle marks as solid corner wedges tucked into one
    corner of the stone's cell -- which corner varies per point, deterministically
    (see :data:`_PNG_WEDGE_CORNERS`), because the app's own choice varies and the
    fixtures should exercise every quadrant of the extractor -- square marks on
    empty points as solid squares, and labels stamped with the 5x7 bitmap font
    from :mod:`goban_svg.digits`.

    ``noise`` adds +/-n per-channel deterministic noise (see :func:`_apply_noise`)
    so extractor thresholds get exercised against something less than perfect.

    ``coords`` is accepted for signature symmetry with :func:`render_svg` but is a
    **no-op**: the bitmap font is digits-only, so there are no coordinate letters
    to stamp. Use the SVG renderer when you want coordinates.

    Labels containing anything other than 0-9 are skipped (again, digits-only
    font); the SVG renderer draws them in full.
    """
    pos.validate()
    palette = palette or Palette()
    cell = int(cell)
    geo = _RasterGeometry(
        size=pos.size,
        cell=cell,
        frame=max(1, int(round(_FRAME_RATIO * cell))),
        margin=int(round(MARGIN_RATIO * cell)),
    )
    span = (pos.size - 1) * cell

    img = Image.new(geo.side, geo.side, palette.frame)
    _fill_rect(img, geo.frame, geo.frame, geo.side - geo.frame - 1, geo.side - geo.frame - 1, palette.wood)

    grid_width = max(1, int(round(_GRID_WIDTH_RATIO * cell)))
    if grid_width % 2 == 0:
        # EXTRACTOR-COUPLED. An even-width band cannot be centered on a pixel: it
        # straddles the intersection (v-1..v), putting the lines half a pixel off
        # the stones, hoshi and marks, which are all centered on v. extract.py
        # locates intersections from the lines and then probes geometry drawn
        # around the stones, so that half pixel becomes a half-pixel error in
        # every probe -- enough to slide the wedge-exclusion quadrant off the
        # wedge and let its edge read as a digit. Round to odd and the band is
        # symmetric about v again.
        grid_width -= 1
    half_width = grid_width // 2
    for i in range(pos.size):
        v = geo.origin + i * cell
        _fill_rect(img, geo.origin, v - half_width, geo.origin + span, v - half_width + grid_width - 1, palette.line)
        _fill_rect(img, v - half_width, geo.origin, v - half_width + grid_width - 1, geo.origin + span, palette.line)

    hoshi_r = max(1.0, _PNG_HOSHI_RATIO * cell)
    for p in sorted(star_points(pos.size), key=_point_key):
        x, y = geo.xy(p)
        _fill_disc(img, x, y, hoshi_r, palette.line)

    stone_r = STONE_RADIUS_RATIO * cell
    rim = max(1.0, _STONE_RIM_RATIO * cell)
    for p in sorted(pos.stones, key=_point_key):
        x, y = geo.xy(p)
        if pos.stones[p] == "black":
            _fill_disc(img, x, y, stone_r, palette.black_stone)
            _paint_specular(img, x, y, cell, palette)
        else:
            # Rim first, then the face inside it: a thin dark outline is all the
            # app shows, and it keeps white stones from merging with the wood.
            _fill_disc(img, x, y, stone_r, palette.stone_rim)
            _fill_disc(img, x, y, stone_r - rim, palette.white_stone)

    stroke = max(1, int(round(_GRID_WIDTH_RATIO * cell)))
    for p in sorted(pos.marks, key=_point_key):
        mark = pos.marks[p]
        stone = pos.stones.get(p)
        x, y = geo.xy(p)
        color = _resolve_png_color(mark.color, stone, palette)
        if mark.type == "triangle":
            leg = _WEDGE_LEG_RATIO * cell
            sx, sy = _PNG_WEDGE_CORNERS[(p.col * 7 + p.row * 13) % 4]
            _fill_corner_wedge(img, x + sx * cell / 2.0, y + sy * cell / 2.0, leg, color, dirx=-sx, diry=-sy)
        elif mark.type == "square":
            half = _PNG_SQUARE_HALF_RATIO * cell
            if stone is None:
                _fill_rect(
                    img,
                    int(round(x - half)),
                    int(round(y - half)),
                    int(round(x + half)),
                    int(round(y + half)),
                    color,
                )
            else:
                # The app never puts a square on a stone; draw it hollow so it
                # cannot swamp the extractor's 0.20c color disc.
                _stroke_rect(img, x, y, half, stroke, color)
        elif mark.type == "circle":
            r = _PNG_CIRCLE_MARK_RATIO * cell
            # A ring is painted as a filled disc with the *background* punched back
            # out of its middle, so the background has to be whatever is actually
            # under the mark: the stone's own face on a stone, wood only on an
            # empty point. Punching wood into a white stone would leave it holed.
            interior: tuple[int, int, int]
            if stone is None:
                interior = palette.wood
            elif stone == "black":
                interior = palette.black_stone
            else:
                interior = palette.white_stone
            _fill_disc(img, x, y, r, color)
            _fill_disc(img, x, y, r - stroke, interior)
        elif mark.type == "cross":
            r = _PNG_CROSS_MARK_RATIO * cell
            for dy in range(int(-r), int(r) + 1):
                _fill_rect(img, x + dy - stroke // 2, y + dy, x + dy + stroke // 2, y + dy, color)
                _fill_rect(img, x + dy - stroke // 2, y - dy, x + dy + stroke // 2, y - dy, color)

    for p in sorted(pos.labels, key=_point_key):
        text = pos.labels[p]
        if not text or not text.isdigit():
            continue
        x, y = geo.xy(p)
        stone = pos.stones.get(p)
        color = palette.mark_white if stone == "black" else palette.mark_black
        # Width budget / width in font units -> px per unit. max(7, ...) keeps a
        # 1-digit label from being scaled by its 5-unit width alone, which would
        # make the 7-unit-tall glyph overflow the OCR window vertically.
        scale = max(1, int(_LABEL_WIDTH_RATIO * cell) // max(7, 6 * len(text) - 1))
        digits.stamp(img, text, x, y, scale=scale, color=color)

    _apply_noise(img, noise, seed)
    return img
