"""Tests for extract.py -- the screenshot -> Position reader.

The core of this file is the **round trip** (design.md sec 9): build a Position by
hand, paint it with ``render.render_png`` (the app-style raster painter, which
exists to be the extractor's fixture generator), read it back with
``extract_position``, and demand the result equal the original *exactly* -- same
stones, same marks with the same canonical colors, same labels, same size. No
external image fixtures are involved, and nothing is random: ``render_png``'s
noise comes from a seeded LCG, so a failure here reproduces byte for byte.

The positions are built to exercise the specific things design.md warns about
rather than to be pretty Go: stones in all four corners and along every edge line,
a nine-stone wall facing another nine-stone wall (gotcha G5 -- walls suppress both
the wood counts and the line peaks), labels on stones of both colors (G2 -- glossy
black stones have a specular highlight that must not read as a glyph), all four
corner-badge colors (G1/G3), and a solid square marker sitting on a star point,
where a hoshi dot and a marker have to be told apart (G4).
"""

from __future__ import annotations

import pytest

from goban_svg.board import WEDGE_BLUE, WEDGE_RED, Mark, Point, Position, ascii_diagram, star_points
from goban_svg.extract import ExtractionError, ExtractionResult, GridFit, UncertainPoint, extract_position
from goban_svg.png_codec import Image
from goban_svg.render import KGS_PALETTE, Palette, render_png

CELL = 32
"""Cell size for every fixture. Big enough that a 5x7 stamped digit survives the
0.30-cell OCR window, small enough that a 19x19 board renders fast."""

PALETTES = [Palette(), KGS_PALETTE]
PALETTE_IDS = ["default-wood", "kgs-wood"]


# --------------------------------------------------------------------------- #
# Fixtures: positions built to hit the algorithm's hard cases
# --------------------------------------------------------------------------- #


def _p(notation: str, size: int) -> Point:
    return Point.parse(notation, size)


def rich_19() -> Position:
    """A 19x19 exercising every feature the extractor claims to read."""
    pos = Position(size=19)

    # All four corners and one stone on the middle of each edge line: the places
    # where the classification disc and ring hang off the board into the margin.
    for notation in ("A1", "T19", "A10", "J1"):
        pos.stones[_p(notation, 19)] = "black"
    for notation in ("A19", "T1", "T10", "J19"):
        pos.stones[_p(notation, 19)] = "white"
    pos.stones[_p("D4", 19)] = "white"  # a stone covering a star point

    # Two facing nine-stone walls (gotcha G5): the columns they occupy lose most
    # of their wood, and the grid lines under them lose most of their length.
    for row in range(4, 13):
        pos.stones[Point(16, row)] = "black"
        pos.stones[Point(17, row)] = "white"

    # Labels: "1", "12" and "3" on stones of both colors.
    for notation, color, text in (
        ("C15", "black", "1"),
        ("H12", "black", "12"),
        ("H9", "black", "3"),
        ("F15", "white", "3"),
        ("K12", "white", "12"),
        ("K9", "white", "1"),
    ):
        point = _p(notation, 19)
        pos.stones[point] = color
        pos.labels[point] = text

    # Corner wedge badges, all four colors. C15/F15 also carry labels, so these
    # cover the case the mask's wedge-quadrant exclusion exists for.
    pos.marks[_p("C15", 19)] = Mark("triangle", WEDGE_RED)
    pos.marks[_p("F15", 19)] = Mark("triangle", WEDGE_BLUE)
    pos.stones[_p("C7", 19)] = "black"
    pos.marks[_p("C7", 19)] = Mark("triangle", "white")
    pos.stones[_p("F7", 19)] = "white"
    pos.marks[_p("F7", 19)] = Mark("triangle", "black")

    # Solid square markers on empty points -- K10 is tengen, a star point, so the
    # marker sits right on top of a hoshi dot (gotcha G4).
    pos.marks[_p("D8", 19)] = Mark("square", "black")
    pos.marks[_p("N5", 19)] = Mark("square", "white")
    pos.marks[_p("K10", 19)] = Mark("square", "black")
    return pos


def rich_13() -> Position:
    """The same feature set on a 13x13, to prove nothing is hard-coded to 19."""
    pos = Position(size=13)
    for notation in ("A1", "N1", "A7", "G1"):
        pos.stones[_p(notation, 13)] = "black"
    for notation in ("N13", "A13", "N7", "G13"):
        pos.stones[_p(notation, 13)] = "white"
    for row in range(3, 12):
        pos.stones[Point(5, row)] = "black"
        pos.stones[Point(6, row)] = "white"
    for notation, color, text in (
        ("L11", "black", "1"),
        ("L5", "black", "3"),
        ("L3", "white", "1"),
        ("L9", "white", "12"),
    ):
        point = _p(notation, 13)
        pos.stones[point] = color
        pos.labels[point] = text
    for notation, color, badge in (
        ("C11", "black", WEDGE_RED),
        ("C9", "white", WEDGE_BLUE),
        ("C5", "black", "white"),
        ("C3", "white", "black"),
    ):
        point = _p(notation, 13)
        pos.stones[point] = color
        pos.marks[point] = Mark("triangle", badge)
    pos.labels[_p("C11", 13)] = "12"
    pos.marks[_p("K7", 13)] = Mark("square", "black")
    pos.marks[_p("G7", 13)] = Mark("square", "white")  # on the center star point
    return pos


def extract(pos: Position, *, palette: Palette | None = None, noise: int = 3, seed: int = 1) -> ExtractionResult:
    return extract_position(render_png(pos, cell=CELL, palette=palette, noise=noise, seed=seed))


def assert_round_trip(pos: Position, result: ExtractionResult) -> None:
    """Every field must come back exactly; on failure, show both diagrams."""
    got = result.position
    context = f"\n--- expected ---\n{ascii_diagram(pos)}\n--- extracted ---\n{ascii_diagram(got)}"
    assert got.size == pos.size, context
    assert got.stones == pos.stones, context
    assert got.marks == pos.marks, context
    assert got.labels == pos.labels, context


# --------------------------------------------------------------------------- #
# The core round trip
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("seed", [1, 7, 23])
@pytest.mark.parametrize("palette", PALETTES, ids=PALETTE_IDS)
def test_round_trip_19x19(palette: Palette, seed: int) -> None:
    """Paint a fully-featured 19x19 and read every stone, mark and label back."""
    pos = rich_19()
    result = extract(pos, palette=palette, noise=3, seed=seed)
    assert_round_trip(pos, result)
    assert result.warnings == []


@pytest.mark.parametrize("seed", [1, 7])
@pytest.mark.parametrize("palette", PALETTES, ids=PALETTE_IDS)
def test_round_trip_13x13(palette: Palette, seed: int) -> None:
    pos = rich_13()
    result = extract(pos, palette=palette, noise=3, seed=seed)
    assert_round_trip(pos, result)
    assert result.warnings == []


@pytest.mark.parametrize("palette", PALETTES, ids=PALETTE_IDS)
def test_round_trip_empty_19x19(palette: Palette) -> None:
    """An empty board: the size still comes out, and nothing is invented.

    The strongest test that hoshi dots and line crossings stay under the
    "something solid is painted here" threshold -- an empty 19x19 has nine star
    points and 361 crossings, and must yield exactly zero marks.
    """
    pos = Position(size=19)
    result = extract(pos, palette=palette, noise=3, seed=5)
    assert_round_trip(pos, result)
    assert result.warnings == []
    assert star_points(19)  # the board really did have hoshi to ignore


def test_round_trip_is_noise_independent() -> None:
    """Different noise seeds -- and no noise at all -- give the identical position."""
    pos = rich_19()
    quiet = extract(pos, noise=0).position
    for seed in (1, 2, 3, 4):
        assert extract(pos, noise=3, seed=seed).position == quiet


# --------------------------------------------------------------------------- #
# Individual stages, isolated
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("size", [9, 13, 19])
def test_grid_fit_recovers_the_painter_geometry(size: int) -> None:
    """The fitted grid must land on the lines the painter actually drew.

    The painter puts the first line at ``frame + margin`` = ``(1 + 0.72) * cell``
    and every next one a whole cell along. Sub-pixel accuracy is not vanity here:
    every later probe (the 0.36-cell ring, the 0.33-cell wedge patch, the
    0.40-cell wedge exclusion) is measured from these coordinates.
    """
    result = extract(Position(size=size), noise=3, seed=2)
    grid = result.grid
    assert len(grid.xs) == size
    assert len(grid.ys) == size
    assert grid.spacing == pytest.approx(CELL, abs=0.05)
    origin = CELL + round(0.72 * CELL)
    for i in range(size):
        assert grid.xs[i] == pytest.approx(origin + i * CELL, abs=0.6)
        assert grid.ys[i] == pytest.approx(origin + i * CELL, abs=0.6)
    x0, y0, x1, y1 = grid.bbox
    assert (x0, y0) == (CELL, CELL)  # the wood starts just inside the UI frame


def test_stones_along_every_edge_line() -> None:
    """The classification disc and ring hang off the board at an edge stone.

    Every point of all four edge lines is occupied at some point across the two
    parities, but never all at once: a solid ring of stones would hide the
    outermost grid lines completely, which is the documented limit of the fit
    (and is covered by :func:`test_cropped_screenshot_is_rejected_not_guessed`'s
    sibling behaviour -- a loud error, not a wrong board).
    """
    for parity in (0, 1):
        pos = Position(size=19)
        for i in range(parity, 19, 2):
            pos.stones[Point(i + 1, 1)] = "black" if i % 4 else "white"
            pos.stones[Point(i + 1, 19)] = "white" if i % 4 else "black"
            pos.stones[Point(1, i + 1)] = "white" if i % 4 else "black"
            pos.stones[Point(19, i + 1)] = "black" if i % 4 else "white"
        assert_round_trip(pos, extract(pos, noise=3, seed=11 + parity))


def test_wall_of_stones_does_not_lose_its_grid_lines() -> None:
    """Gotcha G5: a long wall guts the wood count and the line peak beneath it."""
    pos = Position(size=19)
    for row in range(2, 14):
        pos.stones[Point(4, row)] = "black"
        pos.stones[Point(5, row)] = "white"
    for col in range(6, 18):
        pos.stones[Point(col, 15)] = "black"
        pos.stones[Point(col, 16)] = "white"
    result = extract(pos, noise=3, seed=3)
    assert_round_trip(pos, result)
    assert result.grid.spacing == pytest.approx(CELL, abs=0.05)


def test_every_digit_survives_the_ocr() -> None:
    """Stamp 0-9 and a two-digit label on stones of both colors and read them back.

    A rejected glyph is designed to fail its whole label, so a regression in the
    5x7 resampling shows up here as a missing label, never as a wrong one.
    """
    pos = Position(size=19)
    labels = [*(str(n) for n in range(10)), "12", "40"]
    for i, text in enumerate(labels):
        for j, color in enumerate(("black", "white")):
            point = Point(3 + 3 * (i % 4), 3 + 3 * (i // 4) + 9 * j)
            pos.stones[point] = color
            pos.labels[point] = text
    result = extract(pos, noise=3, seed=4)
    assert_round_trip(pos, result)
    assert result.warnings == []


@pytest.mark.parametrize(
    ("stone", "badge"),
    [
        ("black", WEDGE_BLUE),
        ("black", WEDGE_RED),
        ("black", "white"),
        ("white", WEDGE_BLUE),
        ("white", WEDGE_RED),
        ("white", "black"),
    ],
)
def test_corner_wedge_badges_keep_their_canonical_color(stone: str, badge: str) -> None:
    """A badge is recorded as a triangle in the shared canonical color, and only
    on the stone that owns the cell corner -- never on its diagonal neighbour."""
    pos = Position(size=19)
    owner = Point(10, 10)
    pos.stones[owner] = stone
    pos.marks[owner] = Mark("triangle", badge)
    for neighbour in (Point(9, 11), Point(11, 9), Point(9, 9), Point(11, 11)):
        pos.stones[neighbour] = "white" if stone == "black" else "black"
    result = extract(pos, noise=3, seed=6)
    assert_round_trip(pos, result)
    assert result.position.marks == {owner: Mark("triangle", badge)}


def test_square_marker_on_a_star_point_is_not_a_stone() -> None:
    """Gotcha G4: a solid marker covers the color disc but never reaches the ring,
    so it must not be read as a stone -- even sitting on top of a hoshi dot."""
    pos = Position(size=19)
    pos.marks[Point(10, 10)] = Mark("square", "black")
    pos.marks[Point(4, 4)] = Mark("square", "white")
    pos.marks[Point(16, 16)] = Mark("square", "black")
    result = extract(pos, noise=3, seed=8)
    assert_round_trip(pos, result)
    assert result.position.stones == {}


def test_white_stones_are_not_confused_with_wood() -> None:
    """Gotcha G1: wood is brighter than a white stone, so only the warmth test
    separates them. A board of nothing but white stones would collapse without it."""
    pos = Position(size=13)
    for col in range(2, 13, 2):
        for row in range(2, 13, 2):
            pos.stones[Point(col, row)] = "white"
    for palette in PALETTES:
        assert_round_trip(pos, extract(pos, palette=palette, noise=3, seed=12))


# --------------------------------------------------------------------------- #
# Warnings and failures
# --------------------------------------------------------------------------- #


def test_non_standard_board_size_warns_but_still_extracts() -> None:
    pos = Position(size=11)
    pos.stones[Point(1, 1)] = "black"
    pos.stones[Point(11, 11)] = "white"
    result = extract(pos, noise=3, seed=1)
    assert_round_trip(pos, result)
    assert any("11x11" in warning for warning in result.warnings)


def test_blank_dark_image_has_no_board() -> None:
    with pytest.raises(ExtractionError, match="no board found"):
        extract_position(Image.new(240, 240, (12, 12, 14)))


def test_bare_wood_has_no_grid() -> None:
    """Wood with nothing drawn on it gets past the bbox stage and fails at the fit."""
    with pytest.raises(ExtractionError, match="no board grid"):
        extract_position(Image.new(240, 240, (231, 196, 122)))


def test_cropped_screenshot_is_rejected_not_guessed() -> None:
    """A board cut off on one side fits different line counts per axis. Erroring is
    the point: a 19x19 read as 15 columns would be wrong at nearly every point."""
    full = render_png(rich_19(), cell=CELL, noise=3, seed=1)
    cut = 4 * CELL
    cropped = Image.new(full.width - cut, full.height, (0, 0, 0))
    for y in range(cropped.height):
        src = y * full.width * 3
        cropped.pixels[y * cropped.width * 3 : (y + 1) * cropped.width * 3] = full.pixels[src : src + cropped.width * 3]
    with pytest.raises(ExtractionError, match="cropped"):
        extract_position(cropped)


def _crop(img: Image, x0: int, y0: int, x1: int, y1: int) -> Image:
    """Copy the [x0,x1) x [y0,y1) region into a fresh Image."""
    out = Image.new(x1 - x0, y1 - y0)
    for y in range(y0, y1):
        src = (y * img.width + x0) * 3
        dst = (y - y0) * out.width * 3
        out.pixels[dst : dst + (x1 - x0) * 3] = img.pixels[src : src + (x1 - x0) * 3]
    return out


def test_symmetric_crop_warns_about_missing_margin() -> None:
    # A symmetric crop passes the Nx == Ny check by construction and can land on
    # a standard size, so the missing wood margin is the ONLY tell (reviews
    # A2/B1: a 19x19 cropped to a clean 13x13 previously extracted with zero
    # warnings and every coordinate silently shifted).
    pos = Position(size=19, stones={Point(4, 16): "black", Point(10, 10): "white"})
    img = render_png(pos, cell=24)
    full = extract_position(img)
    xs, ys, d = full.grid.xs, full.grid.ys, full.grid.spacing
    pad = int(0.1 * d)
    cropped = _crop(img, int(xs[3]) - pad, int(ys[3]) - pad, int(xs[15]) + pad + 1, int(ys[15]) + pad + 1)
    result = extract_position(cropped)
    assert result.position.size == 13  # the crop really does look like a smaller board...
    assert any("cropped" in w for w in result.warnings)  # ...and now says so


def test_full_board_reports_no_crop_warning() -> None:
    result = extract_position(render_png(Position(size=19), cell=24))
    assert not any("cropped" in w for w in result.warnings)


def test_oversized_grid_is_rejected_before_notation_breaks() -> None:
    # 28 lines fit a grid but not the 25-letter point notation; without the cap
    # this crashed later with an uncaught IndexError from Point.notation()
    # (reviews A1 / codex-7).
    cell, n, margin = 12, 28, 20
    side = margin * 2 + (n - 1) * cell
    img = Image.new(side, side, (231, 196, 122))
    line = (60, 48, 28)
    for i in range(n):
        p = margin + i * cell
        for t in range(side):
            img.set(p, t, line)
            img.set(t, p, line)
    with pytest.raises(ExtractionError, match="2-25"):
        extract_position(img)


# --------------------------------------------------------------------------- #
# Structured uncertainty (webapp-design.md v2 payload item 2 / v3)
# --------------------------------------------------------------------------- #


def _paint_disc(img: Image, cx: int, cy: int, radius: float, rgb: tuple[int, int, int]) -> None:
    for y in range(int(cy - radius), int(cy + radius) + 1):
        for x in range(int(cx - radius), int(cx + radius) + 1):
            if (x - cx) ** 2 + (y - cy) ** 2 <= radius * radius:
                img.set(x, y, rgb)


def _painted_xy(col: int, row: int, size: int, cell: int) -> tuple[int, int]:
    """Image (x, y) of a board point in a render_png raster (frame 1.0c + margin 0.72c)."""
    origin = max(1, int(round(1.0 * cell))) + int(round(0.72 * cell))
    return (origin + (col - 1) * cell, origin + (size - row) * cell)


def _doubtful_board() -> Image:
    """A 9x9 painted to make the extractor doubt exactly two points, one per kind.

    D4 -- a black stone whose center is overpainted neutral gray: the 0.20c color
    disc lands between the two thresholds while the 0.36c ring still reads black,
    which is the one genuinely ambiguous case `_stone_color` warns about.
    F6 -- a black stone carrying a solid white 5x5 block: big enough to be a glyph
    (>= MIN_GLYPH_PIXELS), shaped like no digit, offset from the center so it can
    neither drag the disc median nor reach far enough to be taken for a wedge.
    """
    pos = Position(size=9, stones={Point(4, 4): "black", Point(6, 6): "black"})
    img = render_png(pos, cell=CELL)
    cx, cy = _painted_xy(4, 4, 9, CELL)
    _paint_disc(img, cx, cy, 0.26 * CELL, (135, 135, 135))
    cx, cy = _painted_xy(6, 6, 9, CELL)
    for y in range(cy - 2, cy + 3):
        for x in range(cx + 2, cx + 7):
            img.set(x, y, (255, 255, 255))
    return img


def test_uncertain_mirrors_the_point_warnings_verbatim() -> None:
    """Every entry pairs with its warning; the warning STRINGS stay frozen.

    The strings are asserted literally, not by substring: blindspot regex-parses
    them and the integration prompt publishes them, so 0.1.1 may append the
    structured mirror but may not touch one byte of the prose (v3 'frozen' rule).
    """
    result = extract_position(_doubtful_board())

    assert result.warnings == [
        "ambiguous stone color at D4 (disc luminance 135); read as black",
        "unreadable label on the black stone at F6 -- check it by hand",
    ]
    assert result.uncertain == [
        UncertainPoint(point=Point(col=4, row=4), kind="ambiguous-color"),
        UncertainPoint(point=Point(col=6, row=6), kind="unreadable-label"),
    ]
    # Pairing, index by index: the i-th entry belongs to the i-th point warning,
    # in the extractor's own scan order (columns left to right).
    for entry, warning in zip(result.uncertain, result.warnings, strict=True):
        assert entry.point.notation() in warning
    assert result.position.stones == {Point(4, 4): "black", Point(6, 6): "black"}
    assert result.position.labels == {}  # the unreadable glyph produced no guess


def test_confident_extraction_has_no_uncertain_entries() -> None:
    result = extract(rich_19(), noise=3, seed=1)
    assert result.warnings == []
    assert result.uncertain == []


def test_non_point_warnings_produce_no_uncertain_entries() -> None:
    """The board-size warning names no point, so it gets no review ring."""
    pos = Position(size=11, stones={Point(1, 1): "black", Point(11, 11): "white"})
    result = extract(pos, noise=3, seed=1)
    assert any("11x11" in w for w in result.warnings)
    assert result.uncertain == []


def test_extraction_result_still_takes_three_positional_args() -> None:
    """v3 backward compatibility: `uncertain` was APPENDED with a default.

    Every 0.1.0 construction site -- ours and any caller's -- passes exactly three
    positional arguments, so the field may not be inserted or made required.
    """
    grid = GridFit(xs=[0.0, 1.0], ys=[0.0, 1.0], spacing=1.0, bbox=(0, 0, 1, 1))
    first = ExtractionResult(Position(size=9), grid, ["a warning"])
    second = ExtractionResult(Position(size=9), grid, [])
    assert first.uncertain == [] and second.uncertain == []
    first.uncertain.append(UncertainPoint(point=Point(1, 1), kind="ambiguous-color"))
    assert second.uncertain == []  # default_factory, not a shared mutable default
