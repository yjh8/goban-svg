"""Tests for photo.py -- assisted extraction from photos of physical boards.

Oracle independence (design amendment 11): the synthetic "photo" fixtures are
generated with a TEST-SIDE homography derived from the closed-form square->quad
formulas plus an analytic 3x3 adjugate inverse -- a different derivation path
from production's normalized-DLT linear solve. Production helpers are not
imported for the oracle; only the public photo.py API is exercised.

Real-photo fixtures are the phase-2 acceptance gate (design amendments, B3) --
nothing here claims to prove the classifier constants against reality.
"""

from __future__ import annotations

import math

import pytest

from goban_svg.board import Point, Position
from goban_svg.extract import ExtractionError
from goban_svg.photo import (
    MIN_CELL_SCALE_PX,
    extract_photo_position,
    rectify_board,
    validate_corners,
)
from goban_svg.png_codec import Image
from goban_svg.render import render_png

# --------------------------------------------------------------------------- #
# Test-side homography (closed-form; independent of production's DLT solve)
# --------------------------------------------------------------------------- #


def _square_to_quad(quad):
    """Unit square (0,0),(1,0),(1,1),(0,1) -> quad, via the classic closed form."""
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = quad
    dx1, dy1 = x1 - x2, y1 - y2
    dx2, dy2 = x3 - x2, y3 - y2
    sx, sy = x0 - x1 + x2 - x3, y0 - y1 + y2 - y3
    denom = dx1 * dy2 - dy1 * dx2
    g = (sx * dy2 - sy * dx2) / denom
    h = (dx1 * sy - dy1 * sx) / denom
    a, b, c = x1 - x0 + g * x1, x3 - x0 + h * x3, x0
    d, e, f = y1 - y0 + g * y1, y3 - y0 + h * y3, y0
    return [[a, b, c], [d, e, f], [g, h, 1.0]]


def _mat_apply(m, u, v):
    w = m[2][0] * u + m[2][1] * v + m[2][2]
    return (
        (m[0][0] * u + m[0][1] * v + m[0][2]) / w,
        (m[1][0] * u + m[1][1] * v + m[1][2]) / w,
    )


def _mat_inverse(m):
    """Analytic 3x3 adjugate inverse."""
    (a, b, c), (d, e, f), (g, h, i) = m
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    adj = [
        [e * i - f * h, c * h - b * i, b * f - c * e],
        [f * g - d * i, a * i - c * g, c * d - a * f],
        [d * h - e * g, b * g - a * h, a * e - b * d],
    ]
    return [[x / det for x in row] for row in adj]


def _test_bilinear(img: Image, x: float, y: float):
    x = min(max(x, 0.0), img.width - 1.0)
    y = min(max(y, 0.0), img.height - 1.0)
    x0, y0 = int(x), int(y)
    x1, y1 = min(x0 + 1, img.width - 1), min(y0 + 1, img.height - 1)
    fx, fy = x - x0, y - y0
    p00, p10, p01, p11 = img.get(x0, y0), img.get(x1, y0), img.get(x0, y1), img.get(x1, y1)
    return tuple(
        (p00[ch] * (1 - fx) + p10[ch] * fx) * (1 - fy) + (p01[ch] * (1 - fx) + p11[ch] * fx) * fy for ch in range(3)
    )


def _painted_outer_rect(size: int, cell: int):
    """The painter's outer-intersection rectangle, restated from its documented
    layout (frame 1.0c + margin 0.72c), matching tests/test_render.py."""
    margin = int(round(0.72 * cell))
    frame = max(1, int(round(1.0 * cell)))
    origin = frame + margin
    span = (size - 1) * cell
    return origin, origin + span


def _make_photo(
    pos: Position,
    quad,
    photo_w: int,
    photo_h: int,
    *,
    cell: int = 24,
    gradient: float = 0.0,
    noise: int = 0,
    seed: int = 1,
):
    """Paint a flat board, then forward-warp it into a synthetic photo whose
    outer intersections land exactly on `quad` (TL,TR,BR,BL)."""
    flat = render_png(pos, cell=cell)
    lo, hi = _painted_outer_rect(pos.size, cell)
    span = hi - lo
    h_mat = _square_to_quad(quad)
    h_inv = _mat_inverse(h_mat)
    photo = Image.new(photo_w, photo_h, (40, 38, 34))
    state = seed & 0xFFFFFFFF
    for y in range(photo_h):
        for x in range(photo_w):
            u, v = _mat_apply(h_inv, x + 0.5, y + 0.5)
            if not (-0.12 <= u <= 1.12 and -0.12 <= v <= 1.12):
                continue  # leave the dark backdrop
            fx = lo + u * span
            fy = lo + v * span
            r, g, b = _test_bilinear(flat, fx, fy)
            if gradient:
                factor = 1.0 + gradient * (2.0 * x / photo_w - 1.0)
                r, g, b = r * factor, g * factor, b * factor
            if noise:
                state = (1103515245 * state + 12345) & 0xFFFFFFFF
                dn = (state >> 16) % (2 * noise + 1) - noise
                r, g, b = r + dn, g + dn, b + dn
            photo.set(x, y, (int(min(max(r, 0), 255)), int(min(max(g, 0), 255)), int(min(max(b, 0), 255))))
    return photo


QUAD = ((110.0, 70.0), (560.0, 90.0), (520.0, 470.0), (140.0, 440.0))


def _stones_9() -> Position:
    return Position(
        size=9,
        stones={
            Point(1, 1): "black",
            Point(9, 9): "white",
            Point(1, 9): "white",
            Point(9, 1): "black",
            Point(5, 5): "black",
            Point(3, 7): "white",
            Point(7, 3): "white",
            Point(2, 5): "black",
        },
    )


# --------------------------------------------------------------------------- #
# validate_corners
# --------------------------------------------------------------------------- #


def test_corner_count_and_finiteness():
    with pytest.raises(ValueError, match="4 corners"):
        validate_corners([(0, 0), (1, 0), (1, 1)])
    with pytest.raises(ValueError, match="finite"):
        validate_corners([(0, 0), (float("nan"), 0), (1, 1), (0, 1)])


def test_duplicate_corners_rejected():
    with pytest.raises(ValueError, match="same point"):
        validate_corners([(0, 0), (0.5, 0.5), (100, 100), (0.4, 0.4)])


def test_crossed_quad_rejected():
    # TL,TR swapped with BR -> self-crossing bowtie
    with pytest.raises(ValueError, match="crossed or concave"):
        validate_corners([(0, 0), (100, 100), (100, 0), (0, 100)])


def test_counter_clockwise_rejected_as_mirror():
    with pytest.raises(ValueError, match="mirror"):
        validate_corners([(0, 0), (0, 100), (100, 100), (100, 0)])


def test_valid_quad_passes_unchanged():
    quad = [(10.0, 12.0), (200.0, 15.0), (190.0, 180.0), (12.0, 170.0)]
    assert validate_corners(quad) == tuple(quad)


# --------------------------------------------------------------------------- #
# Geometry gates and analytic invariants
# --------------------------------------------------------------------------- #


def test_size_range_gate():
    img = Image.new(400, 400, (200, 170, 110))
    with pytest.raises(ExtractionError, match="2-25"):
        extract_photo_position(img, [(10, 10), (390, 10), (390, 390), (10, 390)], size=26)


def test_resolution_gate_names_the_fix():
    img = Image.new(400, 400, (200, 170, 110))
    tiny = [
        (10, 10),
        (10 + MIN_CELL_SCALE_PX * 18 * 0.5, 10),
        (10 + MIN_CELL_SCALE_PX * 18 * 0.5, 10 + MIN_CELL_SCALE_PX * 18 * 0.5),
        (10, 10 + MIN_CELL_SCALE_PX * 18 * 0.5),
    ]
    with pytest.raises(ExtractionError, match="closer"):
        extract_photo_position(img, tiny, size=19)


def test_diagonal_intersection_invariant():
    # Any homography maps the canonical center to the intersection of the
    # quad's diagonals. Put a dark dot there; the rectified center must be dark.
    quad = QUAD
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = quad
    # intersection of (TL->BR) and (TR->BL), solved parametrically
    d1 = (x2 - x0, y2 - y0)
    d2 = (x3 - x1, y3 - y1)
    denom = d1[0] * d2[1] - d1[1] * d2[0]
    t = ((x1 - x0) * d2[1] - (y1 - y0) * d2[0]) / denom
    cx, cy = x0 + t * d1[0], y0 + t * d1[1]

    img = Image.new(700, 560, (225, 195, 130))
    for y in range(int(cy) - 6, int(cy) + 7):
        for x in range(int(cx) - 6, int(cx) + 7):
            img.set(x, y, (10, 10, 10))
    out = rectify_board(img, quad, size=9)
    mid = out.width // 2
    assert sum(out.get(mid, mid)) < 150, "canonical center must land on the diagonal-intersection dot"


def test_parallelogram_maps_affinely():
    # For a parallelogram quad the homography is affine: the top-edge midpoint
    # of the board must land midway along the quad's top edge.
    quad = ((100.0, 80.0), (500.0, 120.0), (560.0, 420.0), (160.0, 380.0))  # parallelogram
    img = Image.new(700, 520, (225, 195, 130))
    mx, my = (quad[0][0] + quad[1][0]) / 2, (quad[0][1] + quad[1][1]) / 2
    for y in range(int(my) - 5, int(my) + 6):
        for x in range(int(mx) - 5, int(mx) + 6):
            img.set(x, y, (10, 10, 10))
    out = rectify_board(img, quad, size=9)
    margin = out.width // 10  # PHOTO_MARGIN_CELLS of 10 cells total span
    top_mid = out.get(out.width // 2, margin)
    assert sum(top_mid) < 150


# --------------------------------------------------------------------------- #
# Round trips through the independent test-side warp
# --------------------------------------------------------------------------- #


def test_round_trip_9x9_exact():
    pos = _stones_9()
    photo = _make_photo(pos, QUAD, 660, 520)
    result = extract_photo_position(photo, QUAD, size=9)
    assert result.position.stones == pos.stones
    assert result.position.size == 9


def test_round_trip_with_lighting_gradient_and_noise():
    pos = _stones_9()
    photo = _make_photo(pos, QUAD, 660, 520, gradient=0.30, noise=3, seed=7)
    result = extract_photo_position(photo, QUAD, size=9)
    assert result.position.stones == pos.stones


def test_round_trip_survives_corner_jitter():
    pos = _stones_9()
    photo = _make_photo(pos, QUAD, 660, 520)
    jittered = [(x + dx, y + dy) for (x, y), (dx, dy) in zip(QUAD, ((2, -2), (-2, 1), (1, 2), (-1, -2)), strict=True)]
    result = extract_photo_position(photo, jittered, size=9)
    assert result.position.stones == pos.stones


def test_empty_board_has_no_stones_and_no_warnings():
    # Amendment 1: an empty board must be silent -- not 81 ambiguity warnings.
    photo = _make_photo(Position(size=9), QUAD, 660, 520, gradient=0.2, noise=2)
    result = extract_photo_position(photo, QUAD, size=9)
    assert result.position.stones == {}
    assert result.warnings == []


def test_dense_walls_13x13():
    stones = {}
    for col in range(2, 12):
        stones[Point(col, 6)] = "black"
        stones[Point(col, 8)] = "white"
    pos = Position(size=13, stones=stones)
    photo = _make_photo(pos, QUAD, 660, 520)
    result = extract_photo_position(photo, QUAD, size=13)
    assert result.position.stones == pos.stones


def test_round_trip_19x19():
    stones = {
        Point(4, 4): "black",
        Point(16, 16): "white",
        Point(4, 16): "white",
        Point(16, 4): "black",
        Point(10, 10): "black",
        Point(1, 1): "white",
        Point(19, 19): "black",
        Point(3, 17): "black",
        Point(17, 3): "white",
    }
    pos = Position(size=19, stones=stones)
    photo = _make_photo(pos, ((80, 50), (600, 70), (570, 500), (100, 480)), 680, 550)
    result = extract_photo_position(photo, ((80, 50), (600, 70), (570, 500), (100, 480)), size=19)
    assert result.position.stones == pos.stones


def test_grid_fit_is_canonical():
    photo = _make_photo(Position(size=9), QUAD, 660, 520)
    result = extract_photo_position(photo, QUAD, size=9)
    g = result.grid
    assert len(g.xs) == len(g.ys) == 9
    assert g.spacing > 0
    assert math.isclose(g.xs[1] - g.xs[0], g.spacing)


# --------------------------------------------------------------------------- #
# Code-review regressions (B1/B2, M3, M4, M8)
# --------------------------------------------------------------------------- #


def test_board_touching_image_edges_does_not_fabricate_stones():
    # B1 regression: corners on the image boundary -> the canonical margin maps
    # outside the photo; edge-clamp replication of the dark outer grid lines
    # must NOT become phantom stones.
    pos = Position(size=9)
    flat = render_png(pos, cell=24)
    lo, hi = _painted_outer_rect(9, 24)
    crop = Image.new(hi - lo + 1, hi - lo + 1)
    for y in range(lo, hi + 1):
        for x in range(lo, hi + 1):
            crop.set(x - lo, y - lo, flat.get(x, y))
    side = crop.width - 1.0
    quad = ((0.0, 0.0), (side, 0.0), (side, side), (0.0, side))
    result = extract_photo_position(crop, quad, size=9)
    assert result.position.stones == {}, "edge-clamped margins must never classify as stones"


def test_corners_outside_photo_rejected():
    img = Image.new(300, 300, (200, 170, 110))
    with pytest.raises(ExtractionError, match="outside the photo"):
        extract_photo_position(img, [(-20, 10), (290, 10), (290, 290), (10, 290)], size=9)


def test_resolution_gate_uses_minimum_singular_value():
    # B2 regression: a 30-degree-rotated square whose axis norms overstate the
    # true minimum scale. sigma_min = side/(18 cells) must be judged, not the
    # axis-projection norms (which are larger for rotated quads).
    img = Image.new(400, 400, (200, 170, 110))
    # side chosen so sigma_min*cell ~ 6.93 px/cell < 7 while axis norms ~ 7.48
    c, s_ = math.cos(math.radians(30)), math.sin(math.radians(30))
    side = 6.928 * 18  # true px per cell * 18 cells
    cxc, cyc = 200.0, 200.0
    half = side / 2.0
    base = [(-half, -half), (half, -half), (half, half), (-half, half)]
    quad = tuple((cxc + x * c - y * s_, cyc + x * s_ + y * c) for x, y in base)
    with pytest.raises(ExtractionError, match="closer"):
        extract_photo_position(img, quad, size=19)


def test_resolution_gate_is_per_corner_not_average():
    # M8: a trapezoid whose near edge is huge and far edge tiny -- the AVERAGE
    # scale passes easily; the far corners must still be rejected.
    img = Image.new(800, 600, (200, 170, 110))
    # Far (top) edge: 60px over 18 cells ~ 3.3 px/cell; near (bottom) edge:
    # 600px over 18 cells ~ 33 px/cell. The AVERAGE sails past the gate; the
    # far corners must still be rejected.
    quad = ((370.0, 120.0), (430.0, 120.0), (700.0, 500.0), (100.0, 500.0))
    with pytest.raises(ExtractionError, match="closer"):
        extract_photo_position(img, quad, size=19)


def test_bilinear_exact_values_and_edge_clamp():
    from goban_svg.photo import _bilinear

    img = Image.new(2, 2)
    img.set(0, 0, (0, 0, 0))
    img.set(1, 0, (100, 100, 100))
    img.set(0, 1, (200, 200, 200))
    img.set(1, 1, (40, 40, 40))
    center = _bilinear(img, 0.5, 0.5)
    assert center[0] == pytest.approx((0 + 100 + 200 + 40) / 4)
    exact = _bilinear(img, 1.0, 0.0)
    assert exact[0] == pytest.approx(100)
    clamped = _bilinear(img, -5.0, 0.5)
    assert clamped[0] == pytest.approx((0 + 200) / 2)  # == column x=0 midpoint


def test_classifier_decision_table():
    from goban_svg.photo import (
        BLACK_MIN,
        LOWREF_WIDEN,
        MIN_EMPTY_BAND,
        T_EMPTY,
        WHITE_MIN,
        WHITE_NEUTRALITY_MARGIN,
        _classify_point,
    )

    wood_warmth = 60.0
    neutral = wood_warmth - WHITE_NEUTRALITY_MARGIN - 1
    warm = wood_warmth - WHITE_NEUTRALITY_MARGIN + 1
    # boundaries, normal-confidence
    assert _classify_point(BLACK_MIN, 0, wood_warmth, False, T_EMPTY) == ("black", None)
    assert _classify_point(BLACK_MIN + 0.1, 0, wood_warmth, False, T_EMPTY) == (None, "ambiguous")
    assert _classify_point(WHITE_MIN, neutral, wood_warmth, False, T_EMPTY) == ("white", None)
    assert _classify_point(WHITE_MIN, warm, wood_warmth, False, T_EMPTY) == (None, "warm-bright")
    assert _classify_point(WHITE_MIN - 0.1, neutral, wood_warmth, False, T_EMPTY) == (None, "ambiguous")
    assert _classify_point(T_EMPTY, 0, wood_warmth, False, T_EMPTY) == (None, None)
    assert _classify_point(-T_EMPTY, 0, wood_warmth, False, T_EMPTY) == (None, None)
    # low-ref widens ambiguity BOTH ways (M3): stone floors move out...
    assert _classify_point(BLACK_MIN, 0, wood_warmth, True, T_EMPTY) == (None, "ambiguous")
    assert _classify_point(BLACK_MIN - LOWREF_WIDEN, 0, wood_warmth, True, T_EMPTY) == ("black", None)
    # ...and the confident-empty band SHRINKS (was inverted before the review)
    shrunk = max(T_EMPTY - LOWREF_WIDEN, MIN_EMPTY_BAND)
    assert _classify_point(shrunk + 0.5, 0, wood_warmth, True, T_EMPTY) == (None, "ambiguous")
    assert _classify_point(shrunk - 0.5, 0, wood_warmth, True, T_EMPTY) == (None, None)


def test_grid_bbox_is_canonical_interior():
    # M4: bbox must span outer line to outer line, not the cosmetic margin.
    photo = _make_photo(Position(size=9), QUAD, 660, 520)
    g = extract_photo_position(photo, QUAD, size=9).grid
    assert g.bbox == (int(g.xs[0]), int(g.ys[0]), int(g.xs[-1]), int(g.ys[-1]))


# --------------------------------------------------------------------------- #
# Expanded synthetic matrix (M10)
# --------------------------------------------------------------------------- #


def _vignette(photo: Image, strength: float = 0.25) -> Image:
    cx, cy = photo.width / 2, photo.height / 2
    rmax = math.hypot(cx, cy)
    for y in range(photo.height):
        for x in range(photo.width):
            f = 1.0 - strength * (math.hypot(x - cx, y - cy) / rmax) ** 2
            r, g, b = photo.get(x, y)
            photo.set(x, y, (int(r * f), int(g * f), int(b * f)))
    return photo


def test_round_trip_kgs_palette_negative_gradient_vignette():
    from goban_svg.render import KGS_PALETTE

    pos = _stones_9()
    flat_quad = QUAD
    photo = _make_photo(pos, flat_quad, 660, 520, gradient=-0.35, noise=2, seed=11)
    # painter palette variation: regenerate with KGS wood
    flat = render_png(pos, cell=24, palette=KGS_PALETTE)
    lo, hi = _painted_outer_rect(9, 24)
    span = hi - lo
    h_inv = _mat_inverse(_square_to_quad(flat_quad))
    for y in range(photo.height):
        for x in range(photo.width):
            u, v = _mat_apply(h_inv, x + 0.5, y + 0.5)
            if -0.12 <= u <= 1.12 and -0.12 <= v <= 1.12:
                r, g, b = _test_bilinear(flat, lo + u * span, lo + v * span)
                f = 1.0 - 0.35 * (2.0 * x / photo.width - 1.0)
                photo.set(
                    x, y, (int(min(max(r * f, 0), 255)), int(min(max(g * f, 0), 255)), int(min(max(b * f, 0), 255)))
                )
    _vignette(photo)
    result = extract_photo_position(photo, flat_quad, size=9)
    assert result.position.stones == pos.stones


def test_round_trip_occupied_majority_one_color():
    # M10: empty points are the MINORITY and one color dominates -- the
    # zero-anchored floors must not need an empty-majority assumption.
    stones = {}
    for col in range(1, 14):
        for row in range(1, 14):
            if (col + row) % 2 == 0 or row <= 6:
                stones[Point(col, row)] = "black" if row != 7 else "white"
    pos = Position(size=13, stones=stones)
    assert len(stones) > 169 // 2
    photo = _make_photo(pos, QUAD, 660, 520)
    result = extract_photo_position(photo, QUAD, size=13)
    assert result.position.stones == pos.stones


def test_round_trip_large_corner_jitter():
    # M10: jitter at ~0.15 source-cells (QUAD spans ~430px over 8 cells -> ~8px)
    pos = _stones_9()
    photo = _make_photo(pos, QUAD, 660, 520)
    jit = ((8, -7), (-8, 6), (7, 8), (-6, -8))
    jittered = [(x + dx, y + dy) for (x, y), (dx, dy) in zip(QUAD, jit, strict=True)]
    result = extract_photo_position(photo, jittered, size=9)
    assert result.position.stones == pos.stones


def test_resolution_gate_discriminates_shear_from_axis_norms():
    # Verification-round M2: an affine shear whose per-cell Jacobian is
    # [[8, 6], [0, 8]] -- both axis-norm scales (8 and 10 px/cell) pass the
    # old gate, but the true minimum singular value is ~5.54 px/cell. Only a
    # sigma-min implementation rejects this quad.
    img = Image.new(420, 300, (200, 170, 110))
    tl = (50.0, 50.0)
    u = (8.0 * 18, 0.0 * 18)  # 18 cells along the top edge
    v = (6.0 * 18, 8.0 * 18)  # shear component
    quad = (
        tl,
        (tl[0] + u[0], tl[1] + u[1]),
        (tl[0] + u[0] + v[0], tl[1] + u[1] + v[1]),
        (tl[0] + v[0], tl[1] + v[1]),
    )
    with pytest.raises(ExtractionError, match="closer"):
        extract_photo_position(img, quad, size=19)


def test_edge_cropped_board_is_symmetric():
    # Verification-round M1: with pixel-center geometry everywhere, a board
    # cropped exactly at its outer lines must treat all four edges the SAME --
    # identical stones at mirrored positions may not silently diverge.
    pos = Position(
        size=9,
        stones={Point(1, 5): "black", Point(9, 5): "black", Point(5, 1): "black", Point(5, 9): "black"},
    )
    flat = render_png(pos, cell=24)
    lo, hi = _painted_outer_rect(9, 24)
    crop = Image.new(hi - lo + 1, hi - lo + 1)
    for y in range(lo, hi + 1):
        for x in range(lo, hi + 1):
            crop.set(x - lo, y - lo, flat.get(x, y))
    side = crop.width - 1.0
    quad = ((0.0, 0.0), (side, 0.0), (side, side), (0.0, side))
    result = extract_photo_position(crop, quad, size=9)
    found = set(result.position.stones)
    mirrored_pairs = [
        (Point(1, 5), Point(9, 5)),
        (Point(5, 1), Point(5, 9)),
    ]
    for a, b in mirrored_pairs:
        assert (a in found) == (b in found), f"edge asymmetry: {a.notation()} vs {b.notation()}"
