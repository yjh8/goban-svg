"""extract.py -- turn a screenshot of a Go board back into a :class:`Position`.

This is the reading half of the pipeline (design.md sec 6): ``screenshot ->
Position -> SVG``. It gets a decoded :class:`~goban_svg.png_codec.Image` and
returns the stones, the app's corner "wedge" badges (recorded as triangle
marks), solid square markers on empty points, and OCR'd move-number labels,
together with the grid it fitted and every doubt it had along the way.

The pipeline is five stages, each feeding the next:

1. **Per-pixel maps** -- one pass computing luminance plus two booleans per
   pixel: ``wood`` (bright *and warm*) and ``dark``.
2. **Board bbox** -- the longest contiguous run of wood-rich columns/rows.
3. **Grid fit** -- a "line-ness" projection per axis (dark pixels with wood just
   beside them on *both* sides, i.e. thin dark lines lying on wood -- a stone is
   dark too, but has wood only on its outward side), then a robust uniform-grid
   fit over the projection's peaks. Board size is the fitted line count.
4. **Per-intersection classification** -- a small disc for color, a larger ring
   for "is there a stone here at all", which also separates a solid square
   marker (covers the disc, never reaches the ring) from a stone (covers both).
5. **Annotations** -- corner wedge badges by per-pixel color voting, then label
   OCR against the 5x7 bitmap font in :mod:`goban_svg.digits`.

Known limit, found by fuzzing the round trip: a wall of stones sitting *on* the
board's outermost line, covering more than about 85% of it, leaves too little of
that line visible for stage 3 to find or confirm, and the axes then disagree on
the board size. That surfaces as an :class:`ExtractionError` naming the mismatch
-- loud and correctable, never a silently smaller board. Ordinary walls, up to a
dozen stones on the edge line of a 19x19, are fine.

Every threshold in this module is a named module-level constant. They are the
tuning surface: they were derived from the three reference screenshots' geometry
(design.md gotchas G1-G5) and will be re-tuned against more real screenshots, so
each one says at its definition *why* it has the value it has. Nothing here
guesses: an unreadable glyph fails its whole label with a warning naming the
point rather than emitting a plausible-looking wrong move number, because the
designed correction loop is "human edits the JSON", and a silently wrong label
is one a human will not know to correct.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from statistics import median

from goban_svg import digits
from goban_svg.board import WEDGE_BLUE, WEDGE_RED, Mark, Point, Position
from goban_svg.png_codec import Image

__all__ = ["ExtractionError", "ExtractionResult", "GridFit", "extract_position"]


# --------------------------------------------------------------------------- #
# Stage 1 -- per-pixel maps
# --------------------------------------------------------------------------- #

WOOD_MIN_RED = 140
WOOD_MIN_RED_MINUS_BLUE = 25
"""Wood must be *warm*, not merely bright (gotcha G1).

Board wood (#e9c47e) has luminance ~199 -- brighter than any sane "white stone"
threshold -- so brightness alone cannot separate the two. What can is the warmth:
wood runs r-b ~ 90-130 while a white stone is near-neutral. This one comparison
is what makes white-stone-vs-wood reliable, and it is repeated as a neutrality
*veto* wherever something is classified by being bright (see NEUTRAL_MAX_RB).
"""
WOOD_MIN_GREEN_MINUS_BLUE = 5
WOOD_MIN_LUM = 100
DARK_MAX_LUM = 110
"""Grid lines, black stones and dark UI chrome all land under this."""


# --------------------------------------------------------------------------- #
# Stage 2 -- board bounding box
# --------------------------------------------------------------------------- #

BBOX_MIN_FRACTION = 0.15
"""A column/row counts as "inside the board" at only 15% of the best column's
wood count (gotcha G5). The threshold has to be this low because a column drawn
straight through a long stone wall loses most of its wood -- reference
screenshot 3 has a right-side pushing battle that takes a column down to ~30% of
an empty one. A "reasonable" 50% would cut the board in half there.
"""
BBOX_MIN_COUNT = 4
"""Floor for tiny/odd images, so the fraction rule cannot admit near-empty runs."""
BBOX_MIN_SPAN = 8
"""Anything narrower than this is not a board; fail loudly instead of fitting
a grid to noise."""
BBOX_SUPPORT_WINDOW = 5
"""A column/row is judged by the best wood count within this many pixels of it.

Not cosmetic, and a maximum rather than an average on purpose. A grid line is 1-3
px wide and perfectly axis-aligned, so the column it sits in is *line* all the way
from the top outer line to the bottom one: its own wood count is just the two
margins, ~7% of a clear column and far under BBOX_MIN_FRACTION. Taken literally,
"the longest contiguous run above the threshold" therefore stops at the first grid
line and returns one *cell* instead of the board. Averaging instead of maximising
only half-fixes it: a line running under a nine-stone wall has stone-covered
neighbours too, and the local mean lands within a percent of the threshold either
way. The real question this stage asks is "does the board still reach here", which
a maximum answers and a mean does not. It reaches up to this far into the UI frame,
which the raw-count trim in :func:`_longest_run` takes straight back off.
"""


# --------------------------------------------------------------------------- #
# Stage 3 -- grid fit
# --------------------------------------------------------------------------- #

LINE_WOOD_RADIUS = 4
"""A dark pixel counts toward "line-ness" only if wood lies within this many
pixels *perpendicular* to the line direction, on both sides (see
:func:`_flanked_by_wood`). That is the whole trick of this projection: a grid
line is thin dark *on* wood, so wood flanks it; a stone is dark but sits on wood
only around its outside."""
LINE_SMOOTH_WINDOW = 3
LINE_PEAK_FRACTION = 0.18
"""Local maxima below 18% of the strongest peak are not lines (gotcha G5).

Deliberately low for the same reason as BBOX_MIN_FRACTION: a line buried under a
wall of stones keeps only a fraction of its pixels. Missing peaks are recovered
by the robust fit below, so it is cheaper to admit a few weak peaks (outliers get
rejected) than to lose real lines.
"""
LINE_NMS_RADIUS = 6
"""Non-maximum suppression window, in pixels: one line cannot produce two peaks."""
GAP_FILTER_LO = 0.7
GAP_FILTER_HI = 1.3
"""Consecutive-peak gaps outside [0.7, 1.3] x median are a missed or doubled line,
not a measurement of the spacing; they are dropped before the spacing estimate."""
FIT_RESIDUAL_RATIO = 0.25
"""A peak more than a quarter of a cell off the fitted grid is not on that grid."""
EDGE_TRIM_RATIO = 0.35
"""How close a peak must be to support an outermost grid line, in cells.

The guard against phantom lines on boards whose wood margin is as wide as a cell.
A line with no peak this close is trimmed unless the projection still shows
line-like structure at its position -- see :func:`_fit_grid`."""
STANDARD_SIZES = (9, 13, 19)
GRID_ANISOTROPY_TOLERANCE = 0.05
"""The x- and y-axis grids are fitted independently but every later probe
(discs, rings, badge windows) assumes square cells of their mean spacing. A
screenshot resized on one axis breaks that quietly, so disagreement beyond this
fraction warns (design review F19, 2026-08-19)."""
CROP_MARGIN_MIN_RATIO = 0.30
"""A real board carries ~0.72c of bare wood beyond each outer grid line; a
screenshot cropped mid-board leaves essentially none on the cut sides. Less
margin than this fraction of a cell on any side warns -- it is the only signal
that catches a SYMMETRIC crop, which fools the Nx == Ny check by construction
and can land on a standard size (19x19 cropped to a clean 13x13 previously
extracted with zero warnings; reviews A2/B1, 2026-08-19)."""


# --------------------------------------------------------------------------- #
# Stage 4 -- intersection classification
# --------------------------------------------------------------------------- #

DISC_RADIUS_RATIO = 0.20
"""Radius (in cells) of the color-sampling disc at an intersection."""
RING_RADIUS_RATIO = 0.36
"""Radius (in cells) of the presence-testing ring. Between the disc and the ring
lies the whole marker-vs-stone distinction (gotcha G4): a solid square marker
(half-width ~0.22c) completely covers the 0.20c disc but never reaches the 0.36c
ring, while a stone (r 0.47c) covers both, and a hoshi dot covers neither."""
RING_SAMPLES = 16
RING_DARK_MAX_LUM = 115
RING_BRIGHT_MIN_LUM = 160
NEUTRAL_MAX_RB = 45
"""|r-b| ceiling for "this bright thing is a stone, not wood" (gotcha G1). Wood is
warm (r-b ~ 90-130); white stones and white badges are near-neutral."""
STONE_RING_FRACTION = 0.72
"""Share of in-bounds ring samples that must look stone-ish. Not 100%: a couple of
the 16 samples routinely land on a grid line or a neighbour's corner wedge."""
BLACK_DISC_MAX_LUM = 118
WHITE_DISC_MIN_LUM = 150
AMBIGUOUS_DISC_SPLIT = (BLACK_DISC_MAX_LUM + WHITE_DISC_MIN_LUM) / 2
"""Fallback split for a disc median between the two thresholds -- always paired
with a warning, never used silently."""
MARK_MIN_NONWOOD = 0.55
"""Non-wood share of the disc that means "something solid is painted here" on a
point with no stone. Verified geometrically against the two things that must not
trip it: a hoshi dot is ~16% of the disc, and a bare line crossing ~20%."""


# --------------------------------------------------------------------------- #
# Stage 5a -- corner wedge badges
# --------------------------------------------------------------------------- #

WEDGE_WINDOW_RATIO = 0.55
"""Half-width (in cells) of the per-stone search window for a corner badge. The
badge lives inside the stone's own cell (half-width 0.5c); the margin absorbs grid
jitter and antialiasing. Measured on the three committed screenshots (2026-08-19):
badges are solid right triangles tucked into ONE corner of the cell, legs
0.31c-0.45c, tip pointing at the stone center -- the corner varies per badge, and
the badge may sit entirely off the stone face. The original design's fixed
diagonal probes assumed a rim-overlapping top-corner badge and missed all three
real ones (design review BLOCKER, 2026-08-19)."""
WEDGE_MIN_AREA_RATIO = 0.015
"""Minimum badge component area as a share of d^2. A legs-0.31c triangle is
~0.05 d^2; antialiasing fragments, grid-line slivers and a diagonal neighbour's
badge poking over the shared cell corner all stay far below this."""
WEDGE_MAX_AREA_RATIO = 0.16
"""Maximum area share: a legs-0.5c triangle is 0.125 d^2. Anything bigger inside
the window is a stone face or a marker, not a badge."""
WEDGE_MIN_REACH_RATIO = 0.38
"""A badge hugs its cell corner: some pixel of the component must reach at least
this far from the stone center on one axis. Digits and specular highlights
cluster within ~0.25c of the center and never get here."""
WEDGE_QUADRANT_SLACK_RATIO = 0.06
"""A badge component must lie in ONE quadrant of the cell; this much crossing of
the center axes is tolerated for antialiased edge pixels. Digits, speculars, a
neighbouring stone's face lens and the stone's own rim ring all cross the axes
by far more and are rejected here regardless of their size."""
WEDGE_MAX_TIP_RATIO = 0.42
"""The badge triangle's tip points at the stone center: its nearest pixel must
come within this Chebyshev distance (in cells) of the center. A neighbouring
stone's badge poking over the shared cell edge appears only beyond ~0.45c and is
rejected here -- without this, the 0.55c search window's overlap into the next
cell let a 2 px sliver of board-2's real P6 wedge stamp a phantom badge on O6.
Not lower: a legs-0.31c badge's hypotenuse midpoint (its nearest point) sits at
0.345c ideal, ~0.37c after antialiasing eats the fringe -- 0.35 rejected the
real D14/P6 badges."""
BLUE_MIN_B_MINUS_R = 50
BLUE_MIN_B = 120
RED_MIN_R_MINUS_G = 80
RED_MIN_R = 140
WHITE_BADGE_MIN_LUM = 195
"""A white badge is only looked for on *black* stones, and a black badge only on
white ones (design.md sec 6). Skipping that pairing is not a small loss of
precision: a black stone's own face passes "lum < 55" over half of every corner
patch, so an unpaired black-badge test would stamp four phantom badges on every
black stone."""
BLACK_BADGE_MAX_LUM = 55


# --------------------------------------------------------------------------- #
# Stage 5b -- label OCR
# --------------------------------------------------------------------------- #

LABEL_WINDOW_RATIO = 0.30
"""Half-width (in cells) of the square on-stone window the label is read from.

Truncated to whole pixels rather than rounded, and that is load-bearing: the
window's *corner* is at 0.30c*sqrt(2) ~ 0.424c from the center while a white
stone's face ends at 0.425c. Rounding up puts the stone's dark rim inside the
window, where it reads as ink.
"""
LABEL_ON_BLACK_MIN_LUM = 180
"""Labels on black stones are near-white ink; the bar is high because black stones
are glossy and their specular highlight is bright too (gotcha G2). Highlight
remnants that do survive are removed by MIN_GLYPH_PIXELS."""
LABEL_ON_WHITE_MAX_LUM = 90
WEDGE_MASK_DILATION = 1
"""When a wedge was found, its exact pixels (dilated by this many pixels of
Chebyshev radius, for antialiased fringe) are subtracted from the label mask:
a badge's tip reaches into the OCR window and would otherwise read as an extra
unrecognizable glyph, failing the whole label -- exactly what happened to the
real board-1's D14 before wedge detection worked."""
MIN_GLYPH_PIXELS = 8
"""Connected components smaller than this are specks (a specular remnant, a stray
antialiased pixel), not glyphs."""
NARROW_GLYPH_RATIO = 0.34
"""A glyph this much narrower than it is tall is a bare vertical bar: "1". Sent
straight to the answer because resampling a 1-2 px wide stem onto a 5-wide grid
smears it into something that matches nothing."""
GLYPH_COLS = 5
GLYPH_ROWS = 7
GLYPH_CELL_FILL = 0.35
"""Coverage at which a resampled 5x7 cell counts as ink."""
OCR_MAX_DISTANCE = 12
OCR_MIN_MARGIN = 2


# --------------------------------------------------------------------------- #
# Public types
# --------------------------------------------------------------------------- #


class ExtractionError(Exception):
    """Raised when the image cannot yield a position at all.

    Reserved for genuinely unusable input -- no board found, or a grid whose two
    axes disagree on the board size (a cropped screenshot). Anything merely
    *doubtful* becomes a warning on the result instead, so the caller still gets
    a position to correct by hand.
    """


@dataclass
class GridFit:
    """The fitted board grid, in image pixel coordinates.

    ``ys[0]`` is the TOP image row, which is board row ``size`` -- convert with
    ``row = size - y_index`` (the same flip as :meth:`Point.sgf`).
    """

    xs: list[float]
    ys: list[float]
    spacing: float
    bbox: tuple[int, int, int, int]


@dataclass
class ExtractionResult:
    """What :func:`extract_position` found, plus everything it was unsure about."""

    position: Position
    grid: GridFit
    warnings: list[str]


def _smooth(values: Sequence[float], window: int) -> list[float]:
    """Moving average over a 1-D profile, clipped at the ends.

    Keeps one-pixel jitter from creating or hiding a maximum in the line-ness
    projection.
    """
    half = window // 2
    n = len(values)
    out: list[float] = []
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        out.append(sum(values[lo:hi]) / (hi - lo))
    return out


def _max_filter(values: Sequence[float], window: int) -> list[float]:
    """Sliding maximum over a 1-D profile, clipped at the ends."""
    half = window // 2
    n = len(values)
    return [max(values[max(0, i - half) : min(n, i + half + 1)]) for i in range(n)]


# --------------------------------------------------------------------------- #
# Stage 1 -- per-pixel maps
# --------------------------------------------------------------------------- #


@dataclass
class _Pixels:
    """The image, pre-chewed into the per-channel and per-predicate views the
    later stages index by ``y * width + x``.

    Computed once in a single pass because every stage below reads the same maps
    many times over, and in pure Python re-deriving ``lum`` per probe would cost
    more than the whole rest of the extractor.
    """

    width: int
    height: int
    red: bytes
    green: bytes
    blue: bytes
    lum: list[int]
    wood: bytes
    dark: bytes


def _pixel_maps(img: Image) -> _Pixels:
    """Split the RGB8 raster into channels and compute the luminance/wood/dark maps."""
    px = img.pixels
    red = bytes(px[0::3])
    green = bytes(px[1::3])
    blue = bytes(px[2::3])
    lum = [(299 * r + 587 * g + 114 * b) // 1000 for r, g, b in zip(red, green, blue, strict=True)]
    wood = bytes(
        1
        if (
            r >= WOOD_MIN_RED
            and r - b >= WOOD_MIN_RED_MINUS_BLUE
            and g - b >= WOOD_MIN_GREEN_MINUS_BLUE
            and lv >= WOOD_MIN_LUM
        )
        else 0
        for r, g, b, lv in zip(red, green, blue, lum, strict=True)
    )
    dark = bytes(1 if lv < DARK_MAX_LUM else 0 for lv in lum)
    return _Pixels(width=img.width, height=img.height, red=red, green=green, blue=blue, lum=lum, wood=wood, dark=dark)


# --------------------------------------------------------------------------- #
# Stage 2 -- board bounding box
# --------------------------------------------------------------------------- #


def _column_sums(flags: bytes, width: int, height: int) -> list[int]:
    """Per-column totals of a 0/1 map."""
    totals = [0] * width
    for y in range(height):
        row = flags[y * width : (y + 1) * width]
        totals = [t + v for t, v in zip(totals, row, strict=True)]
    return totals


def _row_sums(flags: bytes, width: int, height: int) -> list[int]:
    """Per-row totals of a 0/1 map."""
    return [sum(flags[y * width : (y + 1) * width]) for y in range(height)]


def _longest_run(counts: Sequence[int], axis: str) -> tuple[int, int]:
    """Longest contiguous run of indices whose count clears the board threshold."""
    if max(counts, default=0) <= 0:
        raise ExtractionError("no board found: the image contains no wood-colored pixels")
    profile = _max_filter(counts, BBOX_SUPPORT_WINDOW)
    threshold = max(BBOX_MIN_COUNT, BBOX_MIN_FRACTION * max(profile))
    lo, hi = 0, -1
    start = None
    for i, value in enumerate(profile):
        if value >= threshold:
            if start is None:
                start = i
            if i - start > hi - lo or hi < lo:
                lo, hi = start, i
        else:
            start = None
    # Smoothing bridges the interior dips it exists for, but it also drags the
    # run a couple of pixels into the dark UI frame -- and frame pixels are dark
    # with wood just beside them, i.e. indistinguishable from a grid line to the
    # next stage. Pull both ends back to columns/rows that clear the threshold on
    # their own.
    while lo <= hi and counts[lo] < threshold:
        lo += 1
    while hi >= lo and counts[hi] < threshold:
        hi -= 1
    if hi - lo + 1 < BBOX_MIN_SPAN:
        raise ExtractionError(f"no board found: the wood region spans too few pixels along {axis}")
    return (lo, hi)


def _wood_bbox(px: _Pixels) -> tuple[int, int, int, int]:
    """Bounding box of the board's wood, as (x0, y0, x1, y1) inclusive."""
    x0, x1 = _longest_run(_column_sums(px.wood, px.width, px.height), "x")
    y0, y1 = _longest_run(_row_sums(px.wood, px.width, px.height), "y")
    return (x0, y0, x1, y1)


# --------------------------------------------------------------------------- #
# Stage 3 -- grid fit
# --------------------------------------------------------------------------- #


def _flanked_by_wood(flags: bytes, radius: int) -> bytes:
    """Mark positions with wood within ``radius`` on BOTH sides.

    Both sides, not either side, and that is the whole discrimination: a grid line
    is thin dark *on* wood, so wood flanks it left and right; a stone's flank is
    equally dark and equally close to wood, but only on the outward side. Testing
    either side alone scores ~10 px per stone per column, which over a nine-stone
    wall builds a false peak as tall as a real line buried under that same wall.

    Done with big-integer shifts rather than a Python loop: this runs once per
    scanline of the board, and OR-ing 0/1 bytes can never carry, so shifting one
    big int is exactly a 1-D dilation. (``>> 8k`` sees ``radius`` bytes to the
    left, ``<< 8k`` to the right, since byte 0 is the most significant.)
    """
    n = len(flags)
    if n == 0:
        return flags
    value = int.from_bytes(flags, "big")
    left = right = 0
    for k in range(1, radius + 1):
        shift = 8 * k
        left |= value >> shift
        right |= value << shift
    return (left & right & ((1 << (8 * n)) - 1)).to_bytes(n, "big")


def _line_projection(px: _Pixels, bbox: tuple[int, int, int, int], axis: str) -> list[int]:
    """ "Line-ness" per column (axis "x") or per row (axis "y") inside the bbox.

    Counts dark pixels that have wood within LINE_WOOD_RADIUS *perpendicular* to
    the line being looked for -- so a vertical grid line scores its full height,
    a horizontal one scores zero in this projection (its neighbours along x are
    more line, not wood), and stone interiors score nothing at all.
    """
    x0, y0, x1, y1 = bbox
    w = px.width
    if axis == "x":
        counts = [0] * (x1 - x0 + 1)
        for y in range(y0, y1 + 1):
            base = y * w
            row_dark = px.dark[base + x0 : base + x1 + 1]
            near_wood = _flanked_by_wood(px.wood[base + x0 : base + x1 + 1], LINE_WOOD_RADIUS)
            counts = [c + (d & m) for c, d, m in zip(counts, row_dark, near_wood, strict=True)]
        return counts
    counts = [0] * (y1 - y0 + 1)
    for x in range(x0, x1 + 1):
        col_dark = px.dark[y0 * w + x : y1 * w + x + 1 : w]
        near_wood = _flanked_by_wood(px.wood[y0 * w + x : y1 * w + x + 1 : w], LINE_WOOD_RADIUS)
        counts = [c + (d & m) for c, d, m in zip(counts, col_dark, near_wood, strict=True)]
    return counts


def _find_peaks(projection: Sequence[int], origin: int) -> list[float]:
    """Grid-line candidates: smoothed local maxima, thresholded, then NMS'd.

    Returns absolute image coordinates (``origin`` is the bbox edge the
    projection starts at), strongest-first suppression but sorted ascending.
    """
    smoothed = _smooth(projection, LINE_SMOOTH_WINDOW)
    peak = max(smoothed, default=0.0)
    if peak <= 0:
        return []
    threshold = LINE_PEAK_FRACTION * peak
    candidates: list[tuple[float, int]] = []
    for i, value in enumerate(smoothed):
        if value < threshold:
            continue
        left = smoothed[i - 1] if i > 0 else -1.0
        right = smoothed[i + 1] if i + 1 < len(smoothed) else -1.0
        if value >= left and value >= right:
            candidates.append((value, i))
    accepted: list[int] = []
    for _, i in sorted(candidates, key=lambda pair: (-pair[0], pair[1])):
        if all(abs(i - j) > LINE_NMS_RADIUS for j in accepted):
            accepted.append(i)
    # Smoothing finds peaks; the raw projection places them, to sub-pixel
    # precision. Two reasons this matters. A line with an asymmetric shoulder --
    # the outermost one, which collects every perpendicular line's endpoint on one
    # side only -- has its smoothed maximum a pixel off the line itself. And a grid
    # line 2 px wide has no single maximum at all: its true center is the boundary
    # *between* two pixels, so any integer answer is half a pixel wrong, which is
    # enough to push the wedge-exclusion quadrant off the wedge it must exclude.
    half = LINE_SMOOTH_WINDOW // 2
    refined: list[float] = []
    for i in accepted:
        best, best_value = i, projection[i]
        for j in range(max(0, i - half), min(len(projection), i + half + 1)):
            if projection[j] > best_value:
                best, best_value = j, projection[j]
        window = range(max(0, best - half), min(len(projection), best + half + 1))
        weight = sum(projection[j] for j in window)
        refined.append(sum(j * projection[j] for j in window) / weight if weight else float(best))
    return sorted(origin + i for i in refined)


def _least_squares(indices: Sequence[int], positions: Sequence[float]) -> tuple[float, float]:
    """Fit ``p ~= a + k*d``; returns (a, d)."""
    n = len(indices)
    sum_k = sum(indices)
    sum_p = sum(positions)
    sum_kk = sum(k * k for k in indices)
    sum_kp = sum(k * p for k, p in zip(indices, positions, strict=True))
    denominator = n * sum_kk - sum_k * sum_k
    if denominator == 0:
        raise ExtractionError("grid fit failed: all detected lines collapsed onto one position")
    d = (n * sum_kp - sum_k * sum_p) / denominator
    a = (sum_p - d * sum_k) / n
    return a, d


def _line_response(projection: Sequence[int], origin: int, position: float) -> int:
    """Strongest raw line-ness within a pixel of ``position`` (an image coordinate).

    Both slice bounds are clamped to >= 0: for a position far left of the origin
    a negative upper bound would wrap around and return the max of nearly the
    whole projection -- a confident answer from the wrong place (review A15;
    latent today, since callers only pass positions >= origin).
    """
    i = int(round(position)) - origin
    return max(projection[max(0, i - 1) : max(0, min(len(projection), i + 2))], default=0)


def _fit_grid(projection: Sequence[int], origin: int, axis: str) -> tuple[list[float], float]:
    """Fit a uniform line grid to one axis' projection and extend it across the bbox.

    Robust by construction (gotcha G5): the spacing comes from the *median* gap
    between consecutive peaks with implausible gaps filtered out, so a line lost
    under a wall of stones (a doubled gap) does not stretch the grid; peaks that
    then miss the fitted grid are rejected and the fit repeated without them.
    """
    peaks = _find_peaks(projection, origin)
    if len(peaks) < 3:
        raise ExtractionError(f"no board grid found along {axis}: only {len(peaks)} candidate lines")
    gaps = [b - a for a, b in zip(peaks, peaks[1:], strict=False)]
    rough = median(gaps)
    plausible = [g for g in gaps if GAP_FILTER_LO * rough <= g <= GAP_FILTER_HI * rough]
    spacing = float(median(plausible or gaps))
    if spacing <= 0:
        raise ExtractionError(f"no board grid found along {axis}: line spacing collapsed to zero")

    indices = [round((p - peaks[0]) / spacing) for p in peaks]
    a, d = _least_squares(indices, [float(p) for p in peaks])
    inliers = [
        (k, p) for k, p in zip(indices, peaks, strict=True) if abs(p - (a + k * d)) <= FIT_RESIDUAL_RATIO * abs(d)
    ]
    if len(inliers) >= 3:
        a, d = _least_squares([k for k, _ in inliers], [float(p) for _, p in inliers])
    if d <= 0:
        raise ExtractionError(f"no board grid found along {axis}: fitted line spacing is not positive")

    lo, hi = origin, origin + len(projection) - 1
    lines = [a + k * d for k in range(math.ceil((lo - a) / d), math.floor((hi - a) / d) + 1)]

    # Trim extrapolated outermost lines: a wood margin a full cell wide leaves room
    # for a phantom line inside the bbox. A line is kept if a peak sits within
    # EDGE_TRIM_RATIO of it -- or, failing that, if the projection still shows
    # line-like structure there. That second test is what saves a real outermost
    # line buried under a wall of stones: at 15% of the strongest line it misses
    # the peak threshold, but it is nothing like the flat blank wood of a margin,
    # which is where a phantom would have to live.
    tolerance = EDGE_TRIM_RATIO * d
    floor_response = LINE_PEAK_FRACTION * median([_line_response(projection, origin, p) for _, p in inliers] or [0])

    def supported(position: float) -> bool:
        if any(abs(p - position) <= tolerance for p in peaks):
            return True
        return _line_response(projection, origin, position) >= floor_response

    while lines and not supported(lines[0]):
        lines.pop(0)
    while lines and not supported(lines[-1]):
        lines.pop()
    if len(lines) < 2:
        raise ExtractionError(f"no board grid found along {axis}: fewer than 2 lines survived the fit")
    return lines, d


# --------------------------------------------------------------------------- #
# Stage 4 -- intersection sampling
# --------------------------------------------------------------------------- #


def _disc_stats(px: _Pixels, cx: float, cy: float, radius: float) -> tuple[float, float]:
    """(median luminance, non-wood fraction) over the disc at (cx, cy).

    The *median*, never the mean (gotcha G2): a digit label or a specular
    highlight covers a good slice of the disc, and either would drag a mean far
    enough to flip the stone's color call. A median only cares which side of the
    50% line the stone's own face is on -- and for the labels ink-heavy enough to
    cross even that line, see the ring veto in :func:`_stone_color`.
    """
    lums: list[int] = []
    non_wood = 0
    rr = radius * radius
    for y in range(int(math.floor(cy - radius)), int(math.ceil(cy + radius)) + 1):
        if not 0 <= y < px.height:
            continue
        dy2 = (y - cy) ** 2
        for x in range(int(math.floor(cx - radius)), int(math.ceil(cx + radius)) + 1):
            if not 0 <= x < px.width or (x - cx) ** 2 + dy2 > rr:
                continue
            i = y * px.width + x
            lums.append(px.lum[i])
            if not px.wood[i]:
                non_wood += 1
    if not lums:
        return (0.0, 0.0)
    return (float(median(lums)), non_wood / len(lums))


def _mean_rgb(px: _Pixels, cx: int, cy: int) -> tuple[float, float, float] | None:
    """Mean (r, g, b) of the 3x3 block at (cx, cy); None if fully out of bounds."""
    r = g = b = 0
    n = 0
    for y in range(cy - 1, cy + 2):
        if not 0 <= y < px.height:
            continue
        for x in range(cx - 1, cx + 2):
            if not 0 <= x < px.width:
                continue
            i = y * px.width + x
            r += px.red[i]
            g += px.green[i]
            b += px.blue[i]
            n += 1
    if n == 0:
        return None
    return (r / n, g / n, b / n)


def _ring_samples(px: _Pixels, cx: float, cy: float, radius: float) -> list[tuple[float, float, float]]:
    """RING_SAMPLES 3x3 means evenly spaced around the ring; out-of-image ones dropped."""
    samples: list[tuple[float, float, float]] = []
    for i in range(RING_SAMPLES):
        theta = 2.0 * math.pi * i / RING_SAMPLES
        sx = int(round(cx + radius * math.cos(theta)))
        sy = int(round(cy + radius * math.sin(theta)))
        block = _mean_rgb(px, sx, sy)
        if block is not None:
            samples.append(block)
    return samples


def _luminance(rgb: tuple[float, float, float]) -> float:
    r, g, b = rgb
    return (299 * r + 587 * g + 114 * b) / 1000


def _is_stone_ish(rgb: tuple[float, float, float]) -> bool:
    """Dark, or bright *and near-neutral* -- the neutrality half is what keeps wood
    (bright but strongly warm) from reading as a white stone (gotcha G1)."""
    lum = _luminance(rgb)
    if lum < RING_DARK_MAX_LUM:
        return True
    return lum >= RING_BRIGHT_MIN_LUM and abs(rgb[0] - rgb[2]) < NEUTRAL_MAX_RB


# --------------------------------------------------------------------------- #
# Stage 5a -- corner wedge badges
# --------------------------------------------------------------------------- #

_ClassPredicate = Callable[[int, int, int, int], bool]

_CLASS_PREDICATES: dict[str, _ClassPredicate] = {
    "blue": lambda r, g, b, lum: b - r > BLUE_MIN_B_MINUS_R and b > BLUE_MIN_B,
    "red": lambda r, g, b, lum: r - g > RED_MIN_R_MINUS_G and r > RED_MIN_R,
    "white": lambda r, g, b, lum: lum > WHITE_BADGE_MIN_LUM and abs(r - b) < NEUTRAL_MAX_RB,
    "black": lambda r, g, b, lum: lum < BLACK_BADGE_MAX_LUM,
}

# Which badge colors are looked for on which stone -- see WHITE_BADGE_MIN_LUM for
# why the pairing is mandatory rather than an optimization.
_CLASSES_BY_STONE: dict[str, tuple[str, ...]] = {
    "black": ("blue", "red", "white"),
    "white": ("blue", "red", "black"),
}

_WEDGE_COLORS: dict[str, str] = {
    "blue": WEDGE_BLUE,
    "red": WEDGE_RED,
    "white": "white",
    "black": "black",
}

_CORNERS: tuple[tuple[int, int], ...] = ((-1, -1), (1, -1), (-1, 1), (1, 1))


def _detect_wedge(
    px: _Pixels, cx: float, cy: float, d: float, stone: str
) -> tuple[tuple[int, int], str, set[tuple[int, int]]] | None:
    """Find the stone's corner badge: ((sx, sy), class name, badge pixels).

    Badge pixels come back as (dx, dy) offsets from the rounded stone center so
    the label mask can subtract them exactly.

    The original design probed two fixed diagonal patches and required the badge
    to overlap the stone's rim; measured against the real screenshots neither
    assumption holds (see WEDGE_WINDOW_RATIO). What IS invariant: the badge is a
    solid one-quadrant blob that reaches nearly to the cell corner, while every
    other blob that can appear on or near a stone -- digits, specular highlights,
    the stone's own rim ring, an orthogonal neighbour's face lens -- crosses the
    cell's center axes. Classification stays per pixel (gotcha G3); geometry does
    the disambiguation, and ownership is free because the search window is the
    stone's own cell.
    """
    ix, iy = int(round(cx)), int(round(cy))
    win = int(WEDGE_WINDOW_RATIO * d)
    min_area = WEDGE_MIN_AREA_RATIO * d * d
    max_area = WEDGE_MAX_AREA_RATIO * d * d
    min_reach = WEDGE_MIN_REACH_RATIO * d
    slack = WEDGE_QUADRANT_SLACK_RATIO * d
    names = _CLASSES_BY_STONE[stone]
    class_pixels: dict[str, set[tuple[int, int]]] = {name: set() for name in names}
    for dy in range(-win, win + 1):
        y = iy + dy
        if not 0 <= y < px.height:
            continue
        for dx in range(-win, win + 1):
            x = ix + dx
            if not 0 <= x < px.width:
                continue
            i = y * px.width + x
            r, g, b, lum = px.red[i], px.green[i], px.blue[i], px.lum[i]
            for name in names:
                if _CLASS_PREDICATES[name](r, g, b, lum):
                    class_pixels[name].add((dx, dy))
    # Priority order (blue, red, then the stone-appropriate badge): the blue and
    # red badges are dark enough to double-match the black-badge class, so the
    # specific colors must win over the generic ones.
    for name in names:
        best: tuple[int, tuple[int, int], set[tuple[int, int]]] | None = None
        for comp in _components(class_pixels[name]):
            if not min_area <= len(comp) <= max_area:
                continue
            if max(max(abs(p[0]) for p in comp), max(abs(p[1]) for p in comp)) < min_reach:
                continue
            if min(max(abs(p[0]), abs(p[1])) for p in comp) > WEDGE_MAX_TIP_RATIO * d:
                continue
            for sx, sy in _CORNERS:
                if all(sx * p[0] >= -slack and sy * p[1] >= -slack for p in comp):
                    if best is None or len(comp) > best[0]:
                        best = (len(comp), (sx, sy), comp)
                    break
        if best is not None:
            return (best[1], name, best[2])
    return None


# --------------------------------------------------------------------------- #
# Stage 5b -- label OCR
# --------------------------------------------------------------------------- #


def _label_mask(
    px: _Pixels, cx: float, cy: float, d: float, stone: str, wedge_pixels: set[tuple[int, int]] | None
) -> set[tuple[int, int]]:
    """On-stone ink pixels, as offsets from the intersection center."""
    half = int(LABEL_WINDOW_RATIO * d)
    ix, iy = int(round(cx)), int(round(cy))
    excluded: set[tuple[int, int]] = set()
    if wedge_pixels:
        rng = range(-WEDGE_MASK_DILATION, WEDGE_MASK_DILATION + 1)
        excluded = {(wx + ox, wy + oy) for wx, wy in wedge_pixels for ox in rng for oy in rng}
    mask: set[tuple[int, int]] = set()
    for dy in range(-half, half + 1):
        y = iy + dy
        if not 0 <= y < px.height:
            continue
        for dx in range(-half, half + 1):
            x = ix + dx
            if not 0 <= x < px.width or (dx, dy) in excluded:
                continue
            lum = px.lum[y * px.width + x]
            if stone == "black":
                if lum < LABEL_ON_BLACK_MIN_LUM:
                    continue
            elif lum > LABEL_ON_WHITE_MAX_LUM:
                continue
            mask.add((dx, dy))
    return mask


def _components(mask: set[tuple[int, int]]) -> list[set[tuple[int, int]]]:
    """8-connected components of the mask."""
    remaining = set(mask)
    groups: list[set[tuple[int, int]]] = []
    while remaining:
        seed = remaining.pop()
        group = {seed}
        stack = [seed]
        while stack:
            x, y = stack.pop()
            for ny in range(y - 1, y + 2):
                for nx in range(x - 1, x + 2):
                    neighbour = (nx, ny)
                    if neighbour in remaining:
                        remaining.discard(neighbour)
                        group.add(neighbour)
                        stack.append(neighbour)
        groups.append(group)
    return groups


def _split_glyphs(mask: set[tuple[int, int]]) -> list[set[tuple[int, int]]]:
    """Drop specks, then cut what is left into glyphs at empty columns.

    Specks go first, before the column cut rather than after: a black stone's
    specular remnant sits at the upper left of the *same columns* as the glyph, so
    cutting first would fold it into the glyph's bounding box instead of dropping
    it (gotcha G2).
    """
    kept: set[tuple[int, int]] = set()
    for group in _components(mask):
        if len(group) >= MIN_GLYPH_PIXELS:
            kept |= group
    if not kept:
        return []
    columns = sorted({x for x, _ in kept})
    runs: list[tuple[int, int]] = []
    start = previous = columns[0]
    for x in columns[1:]:
        if x > previous + 1:
            runs.append((start, previous))
            start = x
        previous = x
    runs.append((start, previous))
    return [{(x, y) for x, y in kept if lo <= x <= hi} for lo, hi in runs]


def _coverage_cells(glyph: set[tuple[int, int]]) -> list[int]:
    """Resample a glyph's bounding box onto the 5x7 coverage grid digits.recognize wants.

    Area coverage, not point sampling: glyph boxes are rarely a whole multiple of
    5x7, and nearest-neighbour sampling of a 3 px wide stem onto 5 columns
    duplicates strokes that are not there.
    """
    xs = [x for x, _ in glyph]
    ys = [y for _, y in glyph]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    w = (x1 - x0 + 1) / GLYPH_COLS
    h = (y1 - y0 + 1) / GLYPH_ROWS
    cell_area = w * h
    cells: list[int] = []
    for j in range(GLYPH_ROWS):
        ya, yb = y0 + j * h, y0 + (j + 1) * h
        for i in range(GLYPH_COLS):
            xa, xb = x0 + i * w, x0 + (i + 1) * w
            area = 0.0
            for px_x, px_y in glyph:
                ox = min(xb, px_x + 1) - max(xa, px_x)
                if ox <= 0:
                    continue
                oy = min(yb, px_y + 1) - max(ya, px_y)
                if oy > 0:
                    area += ox * oy
            cells.append(1 if area / cell_area >= GLYPH_CELL_FILL else 0)
    return cells


def _read_glyph(glyph: set[tuple[int, int]]) -> str | None:
    """One glyph -> one digit, or None when the match cannot be trusted."""
    xs = [x for x, _ in glyph]
    ys = [y for _, y in glyph]
    width = max(xs) - min(xs) + 1
    height = max(ys) - min(ys) + 1
    if height > 0 and width / height < NARROW_GLYPH_RATIO:
        return "1"
    return digits.recognize(_coverage_cells(glyph), max_distance=OCR_MAX_DISTANCE, min_margin=OCR_MIN_MARGIN)


def _read_label(
    px: _Pixels, cx: float, cy: float, d: float, stone: str, wedge_pixels: set[tuple[int, int]] | None, point: Point
) -> tuple[str | None, str | None]:
    """Read an on-stone move number. Returns (label, warning); at most one is set.

    A single unreadable glyph fails the *whole* label. Emitting "1" for what might
    have been "12" would be worse than emitting nothing: the user is told to check
    this point, instead of being handed a wrong number that looks right.
    """
    glyphs = _split_glyphs(_label_mask(px, cx, cy, d, stone, wedge_pixels))
    if wedge_pixels:
        # The badge's antialiased fringe doesn't all pass the badge color class,
        # so dark/bright residue can survive the mask subtraction and split off
        # as a phantom "glyph" (real board-1, D14). Anything living entirely
        # inside the badge's expanded bbox is residue, not a digit.
        bx0 = min(p[0] for p in wedge_pixels) - 2
        bx1 = max(p[0] for p in wedge_pixels) + 2
        by0 = min(p[1] for p in wedge_pixels) - 2
        by1 = max(p[1] for p in wedge_pixels) + 2
        glyphs = [g for g in glyphs if not all(bx0 <= dx <= bx1 and by0 <= dy <= by1 for dx, dy in g)]
    if not glyphs:
        return (None, None)
    text = ""
    for glyph in glyphs:
        digit = _read_glyph(glyph)
        if digit is None:
            return (None, f"unreadable label on the {stone} stone at {point.notation()} -- check it by hand")
        text += digit
    return (text, None)


# --------------------------------------------------------------------------- #
# The extractor
# --------------------------------------------------------------------------- #


def _verdict(luminance: float) -> str | None:
    """ "black" / "white" / None when the luminance is between the two thresholds."""
    if luminance < BLACK_DISC_MAX_LUM:
        return "black"
    if luminance > WHITE_DISC_MIN_LUM:
        return "white"
    return None


def _stone_color(disc_median: float, ring: list[tuple[float, float, float]], point: Point) -> tuple[str, str | None]:
    """Stone color: the disc median, with the ring as both tie-breaker and veto.

    The disc is the primary sample (design.md sec 6) and the *median* is what
    keeps a label or a specular highlight from dragging it (gotcha G2) -- but a
    median only holds while the ink stays under half the disc, and it does not
    always. A single-character label is ~0.44 cells tall against a disc 0.40
    cells across, so an ink-heavy digit ("0", "8") stamped on a black stone puts
    51% of the disc under white ink and flips the median outright. Everything
    downstream then goes with it: a black stone read as white gets probed for a
    *black* badge, and its own face answers yes at all four corners.

    The ring cannot be fooled the same way -- it samples at 0.36 cells, outside
    the label window entirely -- so an unambiguous ring verdict overrules an
    unambiguous disc verdict that contradicts it. That is not a guess needing a
    warning: it is the same measurement taken where the label is not. Only a disc
    that lands *between* the thresholds is genuinely doubtful, and that one warns.
    """
    ring_median = median([_luminance(sample) for sample in ring]) if ring else disc_median
    disc_says, ring_says = _verdict(disc_median), _verdict(ring_median)
    if disc_says is None:
        color = ring_says or ("black" if ring_median < AMBIGUOUS_DISC_SPLIT else "white")
        return (
            color,
            f"ambiguous stone color at {point.notation()} (disc luminance {disc_median:.0f}); read as {color}",
        )
    if ring_says is not None and ring_says != disc_says:
        return (ring_says, None)
    return (disc_says, None)


def extract_position(img: Image) -> ExtractionResult:
    """Read a board position out of a screenshot (design.md sec 6).

    Raises :class:`ExtractionError` when the image has no findable board, or when
    the two axes disagree on the board size -- the signature of a cropped
    screenshot, where silently returning the smaller of the two would produce a
    position that is wrong everywhere rather than obviously broken.
    """
    px = _pixel_maps(img)
    bbox = _wood_bbox(px)
    x0, y0 = bbox[0], bbox[1]

    xs, dx = _fit_grid(_line_projection(px, bbox, "x"), x0, "x")
    ys, dy = _fit_grid(_line_projection(px, bbox, "y"), y0, "y")

    warnings: list[str] = []
    if len(xs) != len(ys):
        raise ExtractionError(
            f"the grid is {len(xs)} lines wide but {len(ys)} lines tall -- this looks like a cropped "
            "screenshot; re-capture the whole board"
        )
    size = len(xs)
    if not 2 <= size <= 25:
        raise ExtractionError(
            f"fitted a {size}x{size} grid, but positions support sizes 2-25 (the point notation has 25 "
            "column letters) -- if this really is a Go board, the grid fit has gone wrong; re-capture "
            "the screenshot at higher resolution"
        )
    if size not in STANDARD_SIZES:
        warnings.append(f"unusual board size {size}x{size} (expected one of {', '.join(map(str, STANDARD_SIZES))})")
    d = (dx + dy) / 2.0
    if abs(dx - dy) > GRID_ANISOTROPY_TOLERANCE * d:
        warnings.append(
            f"grid spacing differs between axes ({dx:.1f}px wide vs {dy:.1f}px tall) -- the screenshot "
            "looks non-uniformly resized; stone classification may suffer"
        )
    margin = CROP_MARGIN_MIN_RATIO * d
    thin_sides = [
        side
        for side, gap in (
            ("left", xs[0] - bbox[0]),
            ("right", bbox[2] - xs[-1]),
            ("top", ys[0] - bbox[1]),
            ("bottom", bbox[3] - ys[-1]),
        )
        if gap < margin
    ]
    if thin_sides:
        warnings.append(
            f"almost no wood margin beyond the outer grid line ({'/'.join(thin_sides)}) -- the screenshot "
            f"may be cropped mid-board; verify the board really is {size}x{size} before trusting coordinates"
        )

    disc_radius = DISC_RADIUS_RATIO * d
    ring_radius = RING_RADIUS_RATIO * d
    position = Position(size=size)

    for xi, cx in enumerate(xs):
        for yi, cy in enumerate(ys):
            point = Point(col=xi + 1, row=size - yi)
            disc_median, non_wood = _disc_stats(px, cx, cy, disc_radius)
            ring = _ring_samples(px, cx, cy, ring_radius)
            stone_ish = sum(1 for sample in ring if _is_stone_ish(sample))
            if ring and stone_ish >= STONE_RING_FRACTION * len(ring):
                color, warning = _stone_color(disc_median, ring, point)
                position.stones[point] = color
                if warning:
                    warnings.append(warning)
                wedge = _detect_wedge(px, cx, cy, d, color)
                wedge_pixels = None
                if wedge is not None:
                    _corner, badge, wedge_pixels = wedge
                    position.marks[point] = Mark(type="triangle", color=_WEDGE_COLORS[badge])
                label, warning = _read_label(px, cx, cy, d, color, wedge_pixels, point)
                if label:
                    position.labels[point] = label
                if warning:
                    warnings.append(warning)
            elif non_wood >= MARK_MIN_NONWOOD:
                # Solid, no ring: a marker painted on an empty point (gotcha G4).
                color = "black" if disc_median < AMBIGUOUS_DISC_SPLIT else "white"
                position.marks[point] = Mark(type="square", color=color)

    grid = GridFit(xs=xs, ys=ys, spacing=d, bbox=bbox)
    return ExtractionResult(position=position, grid=grid, warnings=warnings)


def _fit_flat_axes(img: Image) -> tuple[list[float], float, list[float], float]:
    """INTERNAL SHARED CONTRACT: robust per-axis grid fit of a flat board image.

    Consumed by photo.py's corner auto-refinement (which runs it on rectified
    photos) in addition to this module's own pipeline. Anyone retuning the
    bbox/line/fit stages must keep this function's behavior or update photo
    refinement in the same change -- the coupling is deliberate and named here
    so it cannot be silently broken (photo ultra-review m8).

    Returns (xs, dx, ys, dy): fitted line coordinates and spacing per axis.
    """
    px = _pixel_maps(img)
    bbox = _wood_bbox(px)
    xs, dx = _fit_grid(_line_projection(px, bbox, "x"), bbox[0], "x")
    ys, dy = _fit_grid(_line_projection(px, bbox, "y"), bbox[1], "y")
    return list(xs), dx, list(ys), dy
