"""Assisted extraction for real-life photos of physical boards (docs/photo-mode-design.md).

The screenshot pipeline (extract.py) assumes an axis-aligned grid and globally
constant wood color; photos have neither. This module takes the user-supplied 4
corner intersections (ordered TL, TR, BR, BL in the photo's screen orientation)
and a declared board size, rectifies the board plane through a homography, and
classifies each intersection ADAPTIVELY against its own local wood -- never
against global constants, because real lighting varies across a single photo.

Two structural facts carry the classifier (design section 3, as amended):

* Cell centers are bare wood (stones sit on intersections), so every
  intersection has nearby same-lighting wood references.
* Delta-L against those references is zero-anchored for empty points BY
  CONSTRUCTION, so the empty/stone cutoffs are floors around zero rather than
  assumptions about which class is the majority on the board.

Stones only: physical boards carry no printed move numbers or badge marks, so
the label/mark stages do not exist here.

CALIBRATION STATUS: every threshold below is tagged UNCALIBRATED -- they are
hypotheses derived from the screenshot pipeline's experience, awaiting the
real-photo corpus (design amendments, release gate B3). Do not trust them; do
not ship a user-visible mode on them.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from statistics import median

from goban_svg.board import Point, Position
from goban_svg.extract import ExtractionError, ExtractionResult, GridFit
from goban_svg.png_codec import Image

__all__ = ["extract_photo_position", "rectify_board", "validate_corners"]

Corner = tuple[float, float]

# --------------------------------------------------------------------------- #
# Tunables (UNCALIBRATED -- see module docstring)
# --------------------------------------------------------------------------- #

PHOTO_CELL = 24
"""Canonical rectified cell size in px. Big enough for a 0.33-cell median disc
to hold ~200 samples; small enough that a 19x19 rectification stays interactive
under Pyodide. UNCALIBRATED."""
PHOTO_MARGIN_CELLS = 1.0
"""Canonical margin beyond the outer lines, in cells. Cosmetic (preview) --
classification never trusts it (see the validity mask)."""
DISC_RADIUS_RATIO = 0.33
"""Classification disc radius (cells). Median-sampled, so specular glare on
stones does not flip the read. UNCALIBRATED."""
REF_OFFSET_RATIO = 0.5
REF_RADIUS_RATIO = 0.16
"""Local wood references: patches at the diagonal cell-centers around each
intersection, interior-side only (edge intersections get 2, corners 1). The
clearance to an ideal stone rim is small, hence the outlier gate below.
UNCALIBRATED."""
REF_OUTLIER_DELTA = 30.0
"""A reference patch whose luminance deviates this much from its siblings'
median is contaminated (stone bleed, hard shadow edge) and is discarded --
NEVER resurrected: a point whose references all disagree has no trustworthy
wood baseline and is reported, not guessed (code review M3). UNCALIBRATED."""
T_EMPTY = 12.0
"""|dL| at or below this is CONFIDENTLY empty -- no warning (amendment 1: an
empty board must produce zero warnings). UNCALIBRATED."""
T_EMPTY_MAD_CAP = 16.0
"""The MAD refinement may widen T_EMPTY up to this cap, and only widen -- never
narrow -- and only when enough points sit near zero. UNCALIBRATED."""
MAD_MIN_CLUSTER_FRACTION = 0.3
"""The MAD refinement runs only when at least this fraction of all points sit
inside the +/-T_EMPTY band -- otherwise the photo has no trustworthy empty
cluster to learn a noise floor from. UNCALIBRATED."""
MAD_SIGMA_MULT = 3.0
"""Empty band = this many (robust) sigmas of the near-zero cluster.
UNCALIBRATED."""
_MAD_TO_SIGMA = 1.4826  # scale factor from MAD to sigma for a normal distribution
BLACK_MIN = -28.0
WHITE_MIN = 20.0
"""Occupancy floors (dL against local wood). White's floor is lower than
black's magnitude: white-stone-vs-wood contrast is intrinsically weaker.
UNCALIBRATED."""
WHITE_NEUTRALITY_MARGIN = 12.0
"""A white candidate must be less WARM (r-b) than its local wood by this
margin -- sunlit pale wood is bright but warm, stones are neutral. Locally
referenced, per the same-lighting principle. UNCALIBRATED."""
LOWREF_WIDEN = 6.0
"""Points with <2 surviving wood references get their AMBIGUITY band widened by
this much on BOTH sides: the stone floors move outward AND the confident-empty
band shrinks, so low-reference points warn more, never less (code review M3
fixed the earlier inverted logic). UNCALIBRATED."""
MIN_EMPTY_BAND = 6.0
"""The confident-empty band never shrinks below this, so ordinary empty corner
points (which naturally have one reference) stay silent. UNCALIBRATED."""
MIN_CELL_SCALE_PX = 7.0
"""Resolution gate: the LOCAL projected cell scale at every corner must reach
this many source px per cell. Measured as the Jacobian's MINIMUM SINGULAR
VALUE times the cell -- axis norms overstate the scale of sheared/tilted views
(code review B2)."""
DISC_MIN_VALID_FRACTION = 0.5
REF_MIN_VALID_FRACTION = 0.6
"""Classification samples only count pixels whose source position lay INSIDE
the photo (the validity mask). A disc mostly fed by edge-clamped fabricated
pixels is unreliable; a reference patch even moderately fabricated is dropped
(code review B1: clamped margins turned 1px grid lines into 37 phantom
stones). UNCALIBRATED."""
_REPROJECTION_TOLERANCE = 0.5  # px; corner round-trip error above this = degenerate solve
_MIN_CORNER_SEPARATION = 2.0  # px


# --------------------------------------------------------------------------- #
# Corner validation
# --------------------------------------------------------------------------- #


def validate_corners(corners: Sequence[Corner]) -> tuple[Corner, Corner, Corner, Corner]:
    """Check a TL,TR,BR,BL corner quad and return it as a tuple.

    The order is a CONTRACT with the caller (numbered handles in the UI, the
    documented order in the CLI) -- no reordering is attempted, and a
    counter-clockwise (mirrored) order is REJECTED with a fixable message
    rather than silently corrected (design amendment 3, as re-amended after
    the code review). Raises ValueError otherwise.
    """
    if len(corners) != 4:
        raise ValueError(f"expected 4 corners, got {len(corners)}")
    pts = []
    for i, c in enumerate(corners):
        x, y = float(c[0]), float(c[1])
        if not (math.isfinite(x) and math.isfinite(y)):
            raise ValueError(f"corner {i + 1} is not a finite coordinate: {c!r}")
        pts.append((x, y))
    for i in range(4):
        for j in range(i + 1, 4):
            if math.dist(pts[i], pts[j]) < _MIN_CORNER_SEPARATION:
                raise ValueError(f"corners {i + 1} and {j + 1} are (nearly) the same point")
    # Convexity + orientation: consecutive-edge cross products must all be
    # positive in y-down screen coordinates for the TL,TR,BR,BL order.
    crosses = []
    for i in range(4):
        ax, ay = pts[i]
        bx, by = pts[(i + 1) % 4]
        cx, cy = pts[(i + 2) % 4]
        crosses.append((bx - ax) * (cy - ay) - (by - ay) * (cx - ax))
    if any(cr <= 0 for cr in crosses) and any(cr >= 0 for cr in crosses):
        raise ValueError("the four corners form a crossed or concave quad -- re-check the click order (TL, TR, BR, BL)")
    if crosses[0] < 0:
        raise ValueError(
            "the corners run counter-clockwise -- they would mirror the board; click TL, TR, BR, BL in that order"
        )
    return (pts[0], pts[1], pts[2], pts[3])


# --------------------------------------------------------------------------- #
# Homography (canonical board plane -> source photo)
# --------------------------------------------------------------------------- #


def _solve_linear(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting (amendment 4)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-12:
            raise ExtractionError("corner geometry is degenerate (homography solve failed)")
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(col + 1, n):
            f = m[r][col] / m[col][col]
            for c in range(col, n + 1):
                m[r][c] -= f * m[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (m[r][n] - sum(m[r][c] * x[c] for c in range(r + 1, n))) / m[r][r]
    return x


class _Homography:
    """3x3 projective map from canonical-plane px to source-photo px."""

    def __init__(self, h: list[float]):
        self.h = h  # h11..h32; h33 == 1

    @classmethod
    def from_corners(cls, canonical: Sequence[Corner], source: Sequence[Corner]) -> _Homography:
        # Normalize source coordinates for conditioning (amendment 4): shift the
        # centroid to the origin and scale the mean radius to sqrt(2).
        cx = sum(p[0] for p in source) / 4.0
        cy = sum(p[1] for p in source) / 4.0
        mean_r = sum(math.dist(p, (cx, cy)) for p in source) / 4.0
        s = math.sqrt(2.0) / max(mean_r, 1e-9)
        norm = [((p[0] - cx) * s, (p[1] - cy) * s) for p in source]

        a: list[list[float]] = []
        b: list[float] = []
        for (u, v), (x, y) in zip(canonical, norm, strict=True):
            a.append([u, v, 1.0, 0.0, 0.0, 0.0, -x * u, -x * v])
            b.append(x)
            a.append([0.0, 0.0, 0.0, u, v, 1.0, -y * u, -y * v])
            b.append(y)
        hn = _solve_linear(a, b)
        # Denormalize: H = T_inv . Hn, where T_inv maps normalized -> source px.
        h11, h12, h13, h21, h22, h23, h31, h32 = hn
        inv_s = 1.0 / s
        h = [
            inv_s * h11 + cx * h31,
            inv_s * h12 + cx * h32,
            inv_s * h13 + cx,
            inv_s * h21 + cy * h31,
            inv_s * h22 + cy * h32,
            inv_s * h23 + cy,
            h31,
            h32,
        ]
        homography = cls(h)
        for can, src in zip(canonical, source, strict=True):
            mx, my = homography.map(can[0], can[1])
            if math.dist((mx, my), src) > _REPROJECTION_TOLERANCE:
                raise ExtractionError("corner geometry is degenerate (reprojection check failed)")
        return homography

    def map(self, u: float, v: float) -> tuple[float, float]:
        h = self.h
        w = h[6] * u + h[7] * v + 1.0
        if abs(w) < 1e-9:  # |w| guard: a pole can only occur outside a sane convex quad
            raise ExtractionError("corner geometry is degenerate (projective pole inside the board)")
        return (
            (h[0] * u + h[1] * v + h[2]) / w,
            (h[3] * u + h[4] * v + h[5]) / w,
        )

    def local_cell_scale(self, u: float, v: float, cell: float) -> float:
        """Source px per board cell at (u, v): the Jacobian's MINIMUM SINGULAR
        VALUE times the cell. Axis norms overstate sheared/tilted views (code
        review B2) -- the smallest singular value is the true worst-direction
        scale."""
        h = self.h
        w = h[6] * u + h[7] * v + 1.0
        x_num = h[0] * u + h[1] * v + h[2]
        y_num = h[3] * u + h[4] * v + h[5]
        dxdu = (h[0] * w - x_num * h[6]) / (w * w)
        dydu = (h[3] * w - y_num * h[6]) / (w * w)
        dxdv = (h[1] * w - x_num * h[7]) / (w * w)
        dydv = (h[4] * w - y_num * h[7]) / (w * w)
        e = dxdu * dxdu + dydu * dydu
        g = dxdv * dxdv + dydv * dydv
        f = dxdu * dxdv + dydu * dydv
        eig_min = (e + g - math.sqrt((e - g) ** 2 + 4.0 * f * f)) / 2.0
        return math.sqrt(max(eig_min, 0.0)) * cell


def _bilinear(img: Image, x: float, y: float) -> tuple[float, float, float]:
    """Edge-clamped bilinear sample. Cosmetic only: classification must consult
    the validity mask, because clamping FABRICATES content outside the photo
    (code review B1)."""
    x = min(max(x, 0.0), img.width - 1.0)
    y = min(max(y, 0.0), img.height - 1.0)
    x0, y0 = int(x), int(y)
    x1, y1 = min(x0 + 1, img.width - 1), min(y0 + 1, img.height - 1)
    fx, fy = x - x0, y - y0
    p00 = img.get(x0, y0)
    p10 = img.get(x1, y0)
    p01 = img.get(x0, y1)
    p11 = img.get(x1, y1)
    out = []
    for c in range(3):
        top = p00[c] * (1 - fx) + p10[c] * fx
        bot = p01[c] * (1 - fx) + p11[c] * fx
        out.append(top * (1 - fy) + bot * fy)
    return (out[0], out[1], out[2])


def _build_homography(img: Image, corners: Sequence[Corner], size: int, cell: int) -> tuple[_Homography, float]:
    ordered = validate_corners(corners)
    for i, (x, y) in enumerate(ordered):
        if not (0.0 <= x <= img.width - 1.0 and 0.0 <= y <= img.height - 1.0):
            raise ExtractionError(f"corner {i + 1} ({x:.0f},{y:.0f}) lies outside the photo ({img.width}x{img.height})")
    if not 2 <= size <= 25:
        raise ExtractionError(f"board size {size} is outside the supported 2-25 range")
    margin = PHOTO_MARGIN_CELLS * cell
    span = (size - 1) * cell
    canonical = [
        (margin, margin),
        (margin + span, margin),
        (margin + span, margin + span),
        (margin, margin + span),
    ]
    homography = _Homography.from_corners(canonical, ordered)
    min_scale = min(homography.local_cell_scale(u, v, cell) for u, v in canonical)
    if min_scale < MIN_CELL_SCALE_PX:
        raise ExtractionError(
            f"the board is too small in the photo (least-resolved corner has ~{min_scale:.1f}px per cell, "
            f"need {MIN_CELL_SCALE_PX:.0f}) -- move the camera closer or crop before uploading"
        )
    return homography, margin


def _rectify_masked(img: Image, corners: Sequence[Corner], size: int, cell: int) -> tuple[Image, bytearray, float]:
    """Rectify AND record, per canonical pixel, whether every supersample came
    from inside the photo. Classification trusts only valid pixels (B1)."""
    homography, margin = _build_homography(img, corners, size, cell)
    side = int(round((size - 1) * cell + 2 * margin))
    out = Image.new(side, side)
    valid = bytearray(side * side)
    offsets = ((-0.25, -0.25), (0.25, -0.25), (-0.25, 0.25), (0.25, 0.25))
    w_max = img.width - 1.0
    h_max = img.height - 1.0
    for oy in range(side):
        for ox in range(side):
            r = g = b = 0.0
            in_bounds = True
            for du, dv in offsets:
                sx, sy = homography.map(ox + 0.5 + du, oy + 0.5 + dv)
                if not (0.0 <= sx <= w_max and 0.0 <= sy <= h_max):
                    in_bounds = False
                pr, pg, pb = _bilinear(img, sx, sy)
                r += pr
                g += pg
                b += pb
            out.set(ox, oy, (int(r / 4), int(g / 4), int(b / 4)))
            if in_bounds:
                valid[oy * side + ox] = 1
    return out, valid, margin


def rectify_board(img: Image, corners: Sequence[Corner], size: int, cell: int = PHOTO_CELL) -> Image:
    """Warp the photo's board plane to a flat canonical image (2x2 supersampled
    bilinear -- plain bilinear is not an anti-aliasing filter, amendment 6)."""
    canonical, _valid, _margin = _rectify_masked(img, corners, size, cell)
    return canonical


# --------------------------------------------------------------------------- #
# Adaptive classification
# --------------------------------------------------------------------------- #


def _luminance(rgb: tuple[int, int, int]) -> float:
    return (299 * rgb[0] + 587 * rgb[1] + 114 * rgb[2]) / 1000.0


def _patch_stats(img: Image, valid: bytearray, cx: float, cy: float, radius: float) -> tuple[float, float, float]:
    """(median luminance, median warmth, valid fraction) over the disc's VALID
    pixels. Pixels fabricated by edge clamping never vote (B1)."""
    lums: list[float] = []
    warmths: list[float] = []
    total = 0
    rr = radius * radius
    # Pixel-CENTER convention throughout: pixel (x, y) is the sample at
    # (x+0.5, y+0.5), matching the rectifier's supersample geometry -- mixing
    # conventions made edge validity asymmetric (verification round, M1).
    for y in range(int(cy - radius - 0.5), int(cy + radius + 0.5) + 1):
        if not 0 <= y < img.height:
            continue
        dy2 = (y + 0.5 - cy) ** 2
        for x in range(int(cx - radius - 0.5), int(cx + radius + 0.5) + 1):
            if not 0 <= x < img.width or (x + 0.5 - cx) ** 2 + dy2 > rr:
                continue
            total += 1
            if not valid[y * img.width + x]:
                continue
            rgb = img.get(x, y)
            lums.append(_luminance(rgb))
            warmths.append(rgb[0] - rgb[2])
    if total == 0:
        raise ExtractionError("classification sample fell outside the rectified image")
    if not lums:
        return (0.0, 0.0, 0.0)
    return (median(lums), median(warmths), len(lums) / total)


def _classify_point(
    delta_l: float, warmth: float, wood_warmth: float, low_ref: bool, t_empty: float
) -> tuple[str | None, str | None]:
    """The pure decision table: (stone color | None, warning kind | None).

    Low-reference points get the ambiguity band widened on BOTH sides: stone
    floors move outward AND the confident-empty band shrinks (never below
    MIN_EMPTY_BAND), so less-trustworthy points warn more, never less (M3).
    """
    widen = LOWREF_WIDEN if low_ref else 0.0
    if delta_l <= BLACK_MIN - widen:
        return ("black", None)
    if delta_l >= WHITE_MIN + widen:
        if warmth <= wood_warmth - WHITE_NEUTRALITY_MARGIN:
            return ("white", None)
        return (None, "warm-bright")
    if abs(delta_l) <= max(t_empty - widen, MIN_EMPTY_BAND):
        return (None, None)
    return (None, "ambiguous")


def extract_photo_position(img: Image, corners: Sequence[Corner], size: int) -> ExtractionResult:
    """Photo -> Position (stones only), via rectification + adaptive classification."""
    cell = PHOTO_CELL
    canonical, valid, margin = _rectify_masked(img, corners, size, cell)
    span = (size - 1) * cell
    disc_r = DISC_RADIUS_RATIO * cell
    ref_r = REF_RADIUS_RATIO * cell
    ref_off = REF_OFFSET_RATIO * cell

    # Pass 1 -- per-intersection stats with quality-gated local wood references.
    stats: list[tuple[Point, float, float, float, bool] | tuple[Point, None, str, None, None]] = []
    for j in range(size):
        for i in range(size):
            cx = margin + i * cell
            cy = margin + j * cell
            pt = Point(col=i + 1, row=size - j)
            disc_l, disc_warmth, disc_valid = _patch_stats(canonical, valid, cx, cy, disc_r)
            if disc_valid < DISC_MIN_VALID_FRACTION:
                stats.append((pt, None, "off-image", None, None))
                continue
            refs: list[tuple[float, float]] = []
            for du in (-ref_off, ref_off):
                for dv in (-ref_off, ref_off):
                    # Interior-side only: the reference must sit between grid
                    # lines, never in the cosmetic margin (amendment 2).
                    bu = i * cell + du
                    bv = j * cell + dv
                    if 0 <= bu <= span and 0 <= bv <= span:
                        rl, rw, rvalid = _patch_stats(canonical, valid, cx + du, cy + dv, ref_r)
                        if rvalid >= REF_MIN_VALID_FRACTION:
                            refs.append((rl, rw))
            if len(refs) >= 2:
                med = median([r[0] for r in refs])
                kept = [r for r in refs if abs(r[0] - med) <= REF_OUTLIER_DELTA]
            else:
                kept = refs
            if not kept:
                # All references were contaminated or off-image: no trustworthy
                # wood baseline exists -- report, never resurrect outliers (M3).
                stats.append((pt, None, "no-reference", None, None))
                continue
            low_ref = len(kept) < 2
            wood_l = median([r[0] for r in kept])
            wood_warmth = median([r[1] for r in kept])
            stats.append((pt, disc_l - wood_l, disc_warmth, wood_warmth, low_ref))

    # Global refinement: widen (never narrow) the empty band when the photo's
    # own near-zero cluster says the noise floor is higher (amendment 1).
    deltas = [s[1] for s in stats if isinstance(s[1], float)]
    near_zero = [d for d in deltas if abs(d) <= T_EMPTY]
    t_empty = T_EMPTY
    if deltas and len(near_zero) >= MAD_MIN_CLUSTER_FRACTION * len(deltas):
        med0 = median(near_zero)
        mad = median([abs(d - med0) for d in near_zero])
        t_empty = max(T_EMPTY, min(MAD_SIGMA_MULT * _MAD_TO_SIGMA * mad, T_EMPTY_MAD_CAP))

    # Pass 2 -- two-stage decision: occupancy, then color.
    position = Position(size=size)
    warnings: list[str] = []
    for entry in stats:
        pt = entry[0]
        if entry[1] is None:
            kind = entry[2]
            if kind == "off-image":
                warnings.append(
                    f"point {pt.notation()} lies (partly) outside the photo -- left empty; check it by hand"
                )
            else:
                warnings.append(
                    f"no reliable wood reference around {pt.notation()} (shadow or stone bleed?) -- "
                    "left empty; check it by hand"
                )
            continue
        _, delta_l, disc_warmth, wood_warmth, low_ref = entry
        color, warn_kind = _classify_point(delta_l, disc_warmth, wood_warmth, low_ref, t_empty)
        if color is not None:
            position.stones[pt] = color
        elif warn_kind == "warm-bright":
            warnings.append(
                f"bright point at {pt.notation()} is as warm as the surrounding wood -- glare or a "
                "pale marking? left empty; check it by hand"
            )
        elif warn_kind == "ambiguous":
            warnings.append(
                f"ambiguous point at {pt.notation()} (contrast {delta_l:+.0f} against its local wood) -- "
                "left empty; check it by hand"
            )
        # else: confidently empty, silently.

    xs = [margin + i * cell for i in range(size)]
    interior = int(round(margin))
    grid = GridFit(
        xs=xs,
        ys=list(xs),
        spacing=float(cell),
        bbox=(interior, interior, interior + span, interior + span),
    )
    return ExtractionResult(position=position, grid=grid, warnings=warnings)
