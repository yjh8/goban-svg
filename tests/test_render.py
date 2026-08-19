"""Tests for goban_svg.render: the SVG deliverable and the app-style PNG painter.

The SVG half is checked structurally (parse with xml.etree, then count/inspect
elements) rather than by string matching, so cosmetic reordering of the document
does not break the suite but a wrong radius, font size or viewBox does.

The PNG half is checked against the *extractor's* probe geometry, because that is
what the painter exists for (design.md sec 5/9): the assertions here mirror the
samples extract.py takes -- the 0.20c color disc, the 0.36c ring, the (0.23c,
0.23c) wedge confirm probe, the on-stone label mask -- so a change that would
silently break extraction fails here first, in a test that says why.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from goban_svg.board import Mark, Point, Position
from goban_svg.png_codec import Image
from goban_svg.render import (
    BLACK_GRADIENT_ID,
    KGS_PALETTE,
    WHITE_GRADIENT_ID,
    BoardGeometry,
    Palette,
    render_png,
    render_svg,
)

SVG_NS = "{http://www.w3.org/2000/svg}"

# Ratios restated from design.md rather than imported, so a typo in render.py's
# constants cannot make these tests agree with the bug.
MARGIN = 0.72
GUTTER = 0.8
STONE_R = 0.47
HOSHI_R = 0.105
FRAME = 1.0


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _root(svg: str) -> ET.Element:
    return ET.fromstring(svg)


def _find(root: ET.Element, tag: str) -> list[ET.Element]:
    return list(root.iter(f"{SVG_NS}{tag}"))


def _close(a: float, b: float) -> bool:
    return abs(a - b) < 1e-6


def _lum(rgb: tuple[int, int, int]) -> int:
    r, g, b = rgb
    return (299 * r + 587 * g + 114 * b) // 1000


def _disc_lums(img: Image, cx: int, cy: int, r: float) -> list[int]:
    """Luminances of every pixel within `r` of (cx, cy) -- extract.py's disc sample."""
    out: list[int] = []
    rr = r * r
    for y in range(int(cy - r), int(cy + r) + 1):
        for x in range(int(cx - r), int(cx + r) + 1):
            if (x - cx) ** 2 + (y - cy) ** 2 <= rr:
                out.append(_lum(img.get(x, y)))
    return sorted(out)


def _median(values: list[int]) -> int:
    assert values
    return values[len(values) // 2]


def _png_geometry(size: int, cell: int) -> tuple[int, int]:
    """(side, origin) restated from the painter's documented layout:
    dark frame (1.0c) + wood margin (0.72c) + (size-1) cells + margin + frame."""
    margin = int(round(MARGIN * cell))
    frame = max(1, int(round(FRAME * cell)))
    return (size - 1) * cell + 2 * margin + 2 * frame, frame + margin


def _png_xy(size: int, cell: int, p: Point) -> tuple[int, int]:
    _, origin = _png_geometry(size, cell)
    return origin + (p.col - 1) * cell, origin + (size - p.row) * cell


# --------------------------------------------------------------------------- #
# BoardGeometry
# --------------------------------------------------------------------------- #


def test_geometry_dimensions_without_coords() -> None:
    geo = BoardGeometry(size=19, cell=36.0)
    expected = 2 * MARGIN * 36.0 + 18 * 36.0
    assert _close(geo.width, expected)
    assert _close(geo.height, expected)


def test_geometry_coords_add_gutters_left_and_bottom() -> None:
    plain = BoardGeometry(size=19, cell=36.0)
    with_coords = BoardGeometry(size=19, cell=36.0, coords=True)
    gutter = GUTTER * 36.0
    assert _close(with_coords.width, plain.width + gutter)
    assert _close(with_coords.height, plain.height + gutter)
    # The gutter is on the left and bottom only: the board slides right, not down.
    assert _close(with_coords.point_xy(Point(1, 1))[0], plain.point_xy(Point(1, 1))[0] + gutter)
    assert _close(with_coords.point_xy(Point(1, 1))[1], plain.point_xy(Point(1, 1))[1])


def test_geometry_row_one_is_at_the_bottom() -> None:
    geo = BoardGeometry(size=19, cell=36.0)
    margin = MARGIN * 36.0
    span = 18 * 36.0
    for point, expected in (
        (Point(1, 19), (margin, margin)),  # A19: top-left
        (Point(1, 1), (margin, margin + span)),  # A1: bottom-left
        (Point(19, 1), (margin + span, margin + span)),  # T1: bottom-right
    ):
        x, y = geo.point_xy(point)
        assert _close(x, expected[0]) and _close(y, expected[1]), point.notation()


# --------------------------------------------------------------------------- #
# render_svg
# --------------------------------------------------------------------------- #


def test_svg_viewbox_math_13_vs_19() -> None:
    for size in (13, 19):
        svg = render_svg(Position(size=size), cell=36.0)
        root = _root(svg)
        expected = 2 * MARGIN * 36.0 + (size - 1) * 36.0
        x0, y0, w, h = (float(v) for v in root.get("viewBox", "").split())
        assert (x0, y0) == (0.0, 0.0)
        assert abs(w - expected) < 1e-6, f"size={size}"
        assert abs(h - expected) < 1e-6, f"size={size}"
        assert abs(float(root.get("width", "0")) - expected) < 1e-6


def test_svg_grid_lines_and_border() -> None:
    cell = 36.0
    svg = render_svg(Position(size=19), cell=cell)
    root = _root(svg)
    assert len(_find(root, "line")) == 2 * 19
    # Exactly one border rect (fill=none) on top of the full-canvas wood rect.
    rects = _find(root, "rect")
    borders = [r for r in rects if r.get("fill") == "none"]
    assert len(borders) == 1
    assert float(borders[0].get("stroke-width", "0")) > float(_find(root, "line")[0].get("stroke-width", "0") or 0)


def test_svg_hoshi_radius_and_count() -> None:
    cell = 36.0
    svg = render_svg(Position(size=19), cell=cell)
    root = _root(svg)
    hoshi = [c for c in _find(root, "circle") if abs(float(c.get("r", "0")) - HOSHI_R * cell) < 1e-6]
    assert len(hoshi) == 9


def test_svg_counts_stone_circles_by_gradient_fill() -> None:
    cell = 36.0
    pos = Position(
        size=19,
        stones={
            Point(1, 1): "black",
            Point(4, 4): "black",
            Point(16, 16): "black",
            Point(10, 10): "white",
            Point(19, 19): "white",
        },
    )
    root = _root(render_svg(pos, cell=cell))
    circles = _find(root, "circle")
    black = [c for c in circles if c.get("fill") == f"url(#{BLACK_GRADIENT_ID})"]
    white = [c for c in circles if c.get("fill") == f"url(#{WHITE_GRADIENT_ID})"]
    assert len(black) == 3
    assert len(white) == 2
    for c in black + white:
        assert abs(float(c.get("r", "0")) - STONE_R * cell) < 1e-6
    # White stones carry an outline so they read against the wood; black do not.
    assert all(c.get("stroke") for c in white)
    assert all(c.get("stroke") is None for c in black)
    # Both gradients are declared.
    ids = {g.get("id") for g in _find(root, "radialGradient")}
    assert ids == {BLACK_GRADIENT_ID, WHITE_GRADIENT_ID}


def test_svg_coords_toggle_changes_viewbox_and_adds_texts() -> None:
    cell = 36.0
    pos = Position(size=19)
    plain = _root(render_svg(pos, cell=cell, coords=False))
    with_coords = _root(render_svg(pos, cell=cell, coords=True))

    plain_w = float(plain.get("viewBox", "").split()[2])
    coords_w = float(with_coords.get("viewBox", "").split()[2])
    assert abs(coords_w - (plain_w + GUTTER * cell)) < 1e-6

    assert _find(plain, "text") == []
    texts = [t.text for t in _find(with_coords, "text")]
    assert len(texts) == 2 * 19  # 19 column letters + 19 row numbers
    assert "I" not in texts, "Go coordinates skip the letter I"
    assert "J" in texts and "A" in texts and "T" in texts
    assert "1" in texts and "19" in texts


def test_svg_label_text_is_present_and_xml_escaped() -> None:
    pos = Position(size=19, stones={Point(4, 4): "black"}, labels={Point(4, 4): "<3&"})
    svg = render_svg(pos, cell=36.0)
    assert "&lt;3&amp;" in svg
    assert "<3&" not in svg  # the raw text would make the document ill-formed
    texts = [t.text for t in _find(_root(svg), "text")]
    assert texts == ["<3&"]


def test_svg_label_font_size_shrinks_with_length() -> None:
    cell = 40.0
    pos = Position(
        size=19,
        stones={Point(4, 4): "black", Point(4, 10): "white", Point(4, 16): "black"},
        labels={Point(4, 4): "1", Point(4, 10): "12", Point(4, 16): "123"},
    )
    root = _root(render_svg(pos, cell=cell))
    by_text = {t.text: t for t in _find(root, "text")}
    assert abs(float(by_text["1"].get("font-size", "0")) - 0.52 * cell) < 1e-6
    assert abs(float(by_text["12"].get("font-size", "0")) - 0.46 * cell) < 1e-6
    assert abs(float(by_text["123"].get("font-size", "0")) - 0.36 * cell) < 1e-6
    for element in by_text.values():
        assert element.get("text-anchor") == "middle"
        assert element.get("dy") == "0.35em"


def test_svg_labels_auto_contrast_against_the_stone() -> None:
    pos = Position(
        size=19,
        stones={Point(4, 4): "black", Point(16, 4): "white"},
        labels={Point(4, 4): "1", Point(16, 4): "2"},
    )
    by_text = {t.text: t.get("fill") for t in _find(_root(render_svg(pos)), "text")}
    assert _lum(_hex_rgb(by_text["1"])) > 200, "label on a black stone must be near-white"
    assert _lum(_hex_rgb(by_text["2"])) < 60, "label on a white stone must be near-black"


def _hex_rgb(text: str | None) -> tuple[int, int, int]:
    assert text and text.startswith("#") and len(text) == 7
    return int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16)


def test_svg_square_mark_on_empty_point_is_filled() -> None:
    cell = 36.0
    pos = Position(
        size=19,
        marks={Point(4, 3): Mark("square", "black"), Point(16, 3): Mark("square", "white")},
    )
    root = _root(render_svg(pos, cell=cell))
    marks = [r for r in _find(root, "rect") if r.get("fill") not in (None, "none", "#e6c37a")]
    assert len(marks) == 2
    for rect in marks:
        assert abs(float(rect.get("width", "0")) - 2 * 0.21 * cell) < 1e-6
    white = [r for r in marks if _lum(_hex_rgb(r.get("fill"))) > 200]
    black = [r for r in marks if _lum(_hex_rgb(r.get("fill"))) < 60]
    assert len(white) == len(black) == 1
    assert white[0].get("stroke") == "#444444", "a white square needs an outline on wood"
    assert black[0].get("stroke") is None


def test_svg_triangle_mark_is_a_hollow_polygon_in_the_mark_color() -> None:
    pos = Position(size=19, stones={Point(4, 14): "white"}, marks={Point(4, 14): Mark("triangle", "#2b5fe3")})
    root = _root(render_svg(pos, cell=36.0))
    polygons = _find(root, "polygon")
    assert len(polygons) == 1
    assert polygons[0].get("fill") == "none"
    assert polygons[0].get("stroke") == "#2b5fe3"
    assert len(polygons[0].get("points", "").split()) == 3


def test_svg_circle_mark_on_a_stone_paints_no_wood_over_it() -> None:
    # R2's SVG half: the ring is hollow (fill="none"), so the stone underneath
    # shows through. Nothing may paint wood onto a stone at any radius.
    pos = Position(size=19, stones={Point(10, 10): "white"}, marks={Point(10, 10): Mark("circle", "black")})
    root = _root(render_svg(pos, cell=36.0))
    circles = _find(root, "circle")

    rings = [c for c in circles if c.get("stroke") == "#1a1a1a"]
    assert len(rings) == 1
    assert rings[0].get("fill") == "none", "a filled ring would hide the stone it annotates"
    assert [c for c in circles if c.get("fill") == "#e6c37a"] == [], "no wood disc belongs on a stone"


def test_svg_hollow_mark_on_empty_point_gets_a_wood_backing_disc() -> None:
    pos = Position(size=19, marks={Point(4, 4): Mark("triangle", "black")})
    root = _root(render_svg(pos, cell=36.0))
    backing = [c for c in _find(root, "circle") if c.get("fill") == "#e6c37a"]
    assert len(backing) == 1, "grid lines must not run through a hollow mark"


# --------------------------------------------------------------------------- #
# R1 -- a label and a mark on the SAME empty point must both survive. Labels are
# drawn after marks, so the label's wood backing disc used to be painted over
# the mark, erasing it while the file still validated and the tool reported
# success.
# --------------------------------------------------------------------------- #


def _indexed(root: ET.Element, tag: str) -> list[tuple[int, ET.Element]]:
    """(document position, element) for every direct child of `root` with `tag`."""
    return [(i, e) for i, e in enumerate(root) if e.tag == f"{SVG_NS}{tag}"]


def test_svg_label_on_an_empty_point_does_not_bury_a_hollow_mark() -> None:
    point = Point(4, 4)
    pos = Position(size=19, marks={point: Mark("triangle", "black")}, labels={point: "7"})
    root = _root(render_svg(pos, cell=36.0))

    polygons = _indexed(root, "polygon")
    texts = _indexed(root, "text")
    wood_discs = [(i, e) for i, e in _indexed(root, "circle") if e.get("fill") == "#e6c37a"]

    assert len(polygons) == 1, "the triangle must still be in the document"
    assert [e.text for _, e in texts] == ["7"], "the label must still be in the document"
    assert len(wood_discs) == 1, "one backing disc for the point -- not one per layer"
    assert wood_discs[0][0] < polygons[0][0] < texts[0][0], (
        "draw order must go backing disc -> mark -> label; a disc after the mark erases it"
    )


def test_svg_label_on_an_empty_point_does_not_bury_a_filled_mark() -> None:
    # A filled square on an empty point never had a backing disc of its own (it
    # IS its own backing), so the label must not add one either.
    cell = 36.0
    point = Point(4, 3)
    pos = Position(size=19, marks={point: Mark("square", "black")}, labels={point: "7"})
    root = _root(render_svg(pos, cell=cell))

    squares = [(i, e) for i, e in _indexed(root, "rect") if e.get("fill") not in (None, "none", "#e6c37a")]
    texts = _indexed(root, "text")
    wood_discs = [(i, e) for i, e in _indexed(root, "circle") if e.get("fill") == "#e6c37a"]

    assert len(squares) == 1
    assert abs(float(squares[0][1].get("width", "0")) - 2 * 0.21 * cell) < 1e-6, "the square is intact, not shrunk"
    assert [e.text for _, e in texts] == ["7"]
    assert wood_discs == [], "a disc over a filled mark would erase it"
    assert squares[0][0] < texts[0][0]


def test_svg_label_on_a_bare_empty_point_still_gets_its_backing_disc() -> None:
    # The fix must not throw the disc away in the case it exists for: with no
    # mark to hide behind, grid lines would otherwise run through the glyph.
    pos = Position(size=19, labels={Point(4, 4): "7"})
    root = _root(render_svg(pos, cell=36.0))
    assert len([c for c in _find(root, "circle") if c.get("fill") == "#e6c37a"]) == 1


def test_svg_is_wellformed_for_a_rich_position() -> None:
    pos = Position(
        size=13,
        stones={Point(1, 1): "black", Point(13, 13): "white", Point(7, 7): "black"},
        marks={
            Point(7, 7): Mark("triangle", "white"),
            Point(4, 4): Mark("square", "black"),
            Point(10, 4): Mark("circle"),
            Point(4, 10): Mark("cross", "#e03c3c"),
        },
        labels={Point(1, 1): "1", Point(13, 13): "42"},
    )
    root = _root(render_svg(pos, cell=28.0, coords=True))
    assert root.tag == f"{SVG_NS}svg"


# --------------------------------------------------------------------------- #
# render_png -- checked at the extractor's probe geometry
# --------------------------------------------------------------------------- #


def test_png_dimensions_include_wood_margin_and_dark_frame() -> None:
    for size, cell in ((19, 32), (13, 24)):
        img = render_png(Position(size=size), cell=cell)
        side, _ = _png_geometry(size, cell)
        assert (img.width, img.height) == (side, side), f"size={size} cell={cell}"


def test_png_has_a_dark_ui_frame_around_the_wood() -> None:
    cell = 32
    palette = Palette()
    img = render_png(Position(size=19), cell=cell, palette=palette)
    frame = max(1, int(round(FRAME * cell)))
    assert img.get(0, 0) == palette.frame
    assert img.get(frame - 2, img.height // 2) == palette.frame
    # ...and wood immediately inside it (still in the margin, before the first line).
    assert img.get(frame + 3, frame + 3) == palette.wood


def test_png_wood_is_bright_and_warm_for_both_palettes() -> None:
    # Gotcha G1: the extractor separates wood from white stones by warmth, so any
    # palette shipped here has to stay strongly warm (r - b well over 45).
    for palette in (Palette(), KGS_PALETTE):
        cell = 24
        img = render_png(Position(size=13), cell=cell, palette=palette)
        r, g, b = img.get(img.width // 2, max(1, int(round(FRAME * cell))) + 3)
        assert (r, g, b) == palette.wood
        assert r >= 140 and (r - b) >= 25 and (g - b) >= 5 and _lum((r, g, b)) >= 100


def test_png_stone_disc_medians_are_dark_and_bright() -> None:
    cell = 32
    size = 19
    pos = Position(size=size, stones={Point(4, 16): "black", Point(16, 16): "white"})
    img = render_png(pos, cell=cell)
    disc_r = 0.20 * cell  # extract.py's classification disc

    bx, by = _png_xy(size, cell, Point(4, 16))
    wx, wy = _png_xy(size, cell, Point(16, 16))
    ex, ey = _png_xy(size, cell, Point(10, 10))

    assert _median(_disc_lums(img, bx, by, disc_r)) < 118, "black stone disc median"
    assert _median(_disc_lums(img, wx, wy, disc_r)) > 150, "white stone disc median"
    empty = img.get(ex + 3, ey + 3)
    assert empty[0] - empty[2] >= 25, "an empty point stays warm wood"


def test_png_black_stone_is_glossy_but_the_highlight_is_not_a_phantom_glyph() -> None:
    # Two constraints at once (gotcha G2): the app's black stones ARE glossy, so
    # there must be a >200-lum specular; but extract.py's on-stone label mask keeps
    # lum >= 180 and only drops specks under 8 px, so the bright core has to stay
    # smaller than that or every labelled black stone would OCR as garbage.
    cell = 32
    size = 19
    pos = Position(size=size, stones={Point(10, 10): "black"})
    img = render_png(pos, cell=cell)
    x, y = _png_xy(size, cell, Point(10, 10))

    window = int(0.30 * cell)  # the OCR mask half-width
    lums = [
        _lum(img.get(px, py)) for py in range(y - window, y + window + 1) for px in range(x - window, x + window + 1)
    ]
    assert max(lums) > 200, "black stones must show a specular highlight"
    assert sum(1 for value in lums if value >= 180) < 8, "the bright core must stay under the speck floor"


def _wedge_corner(point: Point) -> tuple[int, int]:
    """The painter's deterministic per-point corner choice (render.py)."""
    return ((-1, -1), (1, -1), (-1, 1), (1, 1))[(point.col * 7 + point.row * 13) % 4]


def test_png_triangle_mark_is_a_corner_wedge_the_extractor_can_confirm() -> None:
    # The load-bearing geometry (measured from the real screenshots): the badge is
    # a solid blob confined to ONE quadrant of the stone's cell, with some pixel
    # reaching >= 0.38c from the stone center on an axis -- that is what
    # extract.py's component test accepts. Pixels near the center (the specular
    # highlight) are excluded here the way quadrant purity excludes them there.
    cell = 32
    size = 19
    point = Point(10, 10)
    pos = Position(size=size, stones={point: "black"}, marks={point: Mark("triangle", "white")})
    img = render_png(pos, cell=cell)
    x, y = _png_xy(size, cell, point)
    sx, sy = _wedge_corner(point)

    def badge_pixels(qx: int, qy: int) -> list[tuple[int, int]]:
        win = int(0.55 * cell)
        out = []
        for dy in range(-win, win + 1):
            for dx in range(-win, win + 1):
                if qx * dx < 0 or qy * dy < 0 or max(abs(dx), abs(dy)) <= int(0.25 * cell):
                    continue
                r, g, b = img.get(x + dx, y + dy)
                if _lum((r, g, b)) > 195 and abs(r - b) < 45:
                    out.append((dx, dy))
        return out

    wedge = badge_pixels(sx, sy)
    assert len(wedge) >= int(0.015 * cell * cell), "badge must clear the extractor's minimum-area floor"
    assert any(max(abs(dx), abs(dy)) >= 0.38 * cell for dx, dy in wedge), "badge must reach toward its corner"
    assert badge_pixels(-sx, -sy) == [], "the opposite quadrant must stay bare -- one corner only"


def _is_badge_pixel(rgb: tuple[int, int, int]) -> bool:
    """extract.py's per-pixel test for a bright, neutral badge (wood is warm)."""
    r, _g, b = rgb
    return _lum(rgb) > 195 and abs(r - b) < 45


def test_png_wedge_corner_varies_per_point_as_documented() -> None:
    # R3: the painter deliberately picks a different cell corner per point (the
    # real app's badge moves around, and the fixtures should exercise every
    # quadrant of the extractor). The docstring used to promise "the top-left
    # corner of the stone's cell", which is a claim about behavior that is not
    # true of any of these four points as a group.
    assert "top-left" not in (render_png.__doc__ or ""), "the painter varies the corner; the docstring must not fix it"

    cell = 32
    size = 19
    points = [Point(4, 4), Point(5, 4), Point(4, 5), Point(5, 5)]
    assert len({_wedge_corner(p) for p in points}) > 1, "these points must not all land on one corner"

    pos = Position(
        size=size,
        stones=dict.fromkeys(points, "black"),
        marks={p: Mark("triangle", "white") for p in points},
    )
    img = render_png(pos, cell=cell)
    off = int(round(0.40 * cell))  # inside the wedge for any leg in the real 0.31c-0.45c band
    for point in points:
        x, y = _png_xy(size, cell, point)
        sx, sy = _wedge_corner(point)
        assert _is_badge_pixel(img.get(x + sx * off, y + sy * off)), f"badge missing at {point.notation()}"
        assert not _is_badge_pixel(img.get(x - sx * off, y - sy * off)), f"badge in two corners at {point.notation()}"


def test_png_wedge_colors_survive_hex_and_named_marks() -> None:
    cell = 32
    size = 13
    blue, red = Point(4, 10), Point(10, 10)
    pos = Position(
        size=size,
        stones={blue: "white", red: "black"},
        marks={blue: Mark("triangle", "#2b5fe3"), red: Mark("triangle", "#e03c3c")},
    )
    img = render_png(pos, cell=cell)

    # 0.40c diagonal into the painted corner is inside the triangle for any leg
    # in the real 0.31c-0.45c band (0.10c + 0.10c from the corner < leg).
    off = int(round(0.40 * cell))
    for pt, check, name in (
        (blue, lambda r, g, b: b - r > 50 and b > 120, "blue"),
        (red, lambda r, g, b: r - g > 80 and r > 140, "red"),
    ):
        x, y = _png_xy(size, cell, pt)
        sx, sy = _wedge_corner(pt)
        r, g, b = img.get(x + sx * off, y + sy * off)
        assert check(r, g, b), f"{name} wedge at {pt.notation()} per extract.py's per-pixel classifier"


def test_png_square_mark_on_empty_covers_the_disc_but_not_the_ring() -> None:
    # Gotcha G4 in assertion form: a solid marker fills the 0.20c disc and leaves
    # the 0.36c ring on wood, which is what tells it apart from a stone.
    cell = 32
    size = 19
    point = Point(4, 3)
    palette = Palette()
    pos = Position(size=size, marks={point: Mark("square", "black")})
    img = render_png(pos, cell=cell, palette=palette)
    x, y = _png_xy(size, cell, point)

    assert _median(_disc_lums(img, x, y, 0.20 * cell)) < 60
    ring = int(round(0.36 * cell / 2**0.5))  # diagonal ring samples miss the grid lines
    for dx, dy in ((ring, ring), (-ring, ring), (ring, -ring), (-ring, -ring)):
        assert img.get(x + dx, y + dy) == palette.wood


def test_png_square_mark_on_a_stone_stays_hollow() -> None:
    # The app never draws this, but it must not swamp the extractor's color disc.
    cell = 32
    size = 19
    point = Point(10, 10)
    pos = Position(size=size, stones={point: "white"}, marks={point: Mark("square", "black")})
    img = render_png(pos, cell=cell)
    x, y = _png_xy(size, cell, point)
    assert _median(_disc_lums(img, x, y, 0.20 * cell)) > 150


def test_png_circle_mark_interior_is_whatever_lies_under_it() -> None:
    # R2: the ring is painted as a filled disc with the background punched back
    # out of its middle, so the punch color has to match what is actually there.
    # Punching wood into a white stone leaves the stone with a wood-colored hole.
    cell = 32
    size = 19
    circle_r = 0.22  # restated from design.md rather than imported
    palette = Palette()
    black_point, white_point, empty_point = Point(4, 16), Point(10, 16), Point(16, 16)
    pos = Position(
        size=size,
        stones={black_point: "black", white_point: "white"},
        marks={p: Mark("circle", "black") for p in (black_point, white_point, empty_point)},
    )
    img = render_png(pos, cell=cell, palette=palette)

    for point, expected, why in (
        (white_point, palette.white_stone, "a circle on a white stone must not punch a wood hole in it"),
        (black_point, palette.black_stone, "a circle on a black stone keeps the stone's face"),
        (empty_point, palette.wood, "on an empty point the background really is wood"),
    ):
        x, y = _png_xy(size, cell, point)
        assert img.get(x, y) == expected, f"{why} (at {point.notation()})"
        # ...and the ring itself is still drawn around that interior.
        assert img.get(x + int(circle_r * cell), y) == palette.mark_black, f"ring missing at {point.notation()}"


def test_png_labels_are_stamped_in_auto_contrast() -> None:
    cell = 32
    size = 19
    black_point, white_point = Point(4, 16), Point(16, 16)
    pos = Position(
        size=size,
        stones={black_point: "black", white_point: "white"},
        labels={black_point: "1", white_point: "12"},
    )
    img = render_png(pos, cell=cell)
    window = int(0.30 * cell)

    def _mask_count(point: Point, predicate) -> int:
        x, y = _png_xy(size, cell, point)
        return sum(
            1
            for py in range(y - window, y + window + 1)
            for px in range(x - window, x + window + 1)
            if predicate(_lum(img.get(px, py)))
        )

    # extract.py's mask thresholds: lum >= 180 on black stones, lum <= 90 on white.
    assert _mask_count(black_point, lambda v: v >= 180) > 20
    assert _mask_count(white_point, lambda v: v <= 90) > 20


def test_png_label_glyphs_stay_inside_the_ocr_window() -> None:
    # The stamped label must fit inside the 0.30c half-width mask extract.py reads,
    # for one- and two-digit numbers alike -- a glyph clipped by that window is an
    # unrecognizable glyph. Only pixels ON the stone count: beyond the stone the
    # bright pixels are wood, which the mask never sees.
    cell = 32
    size = 19
    point = Point(10, 10)
    window = int(0.30 * cell)
    stone_r2 = (STONE_R * cell) ** 2
    for text in ("1", "12"):
        pos = Position(size=size, stones={point: "black"}, labels={point: text})
        img = render_png(pos, cell=cell)
        x, y = _png_xy(size, cell, point)
        outside = [
            (px - x, py - y)
            for py in range(y - cell // 2, y + cell // 2)
            for px in range(x - cell // 2, x + cell // 2)
            if (px - x) ** 2 + (py - y) ** 2 <= stone_r2
            and max(abs(px - x), abs(py - y)) > window
            and _lum(img.get(px, py)) >= 180
        ]
        assert outside == [], f"label {text!r} spills outside the OCR window at {outside[:3]}"


def test_png_non_digit_labels_are_skipped_not_crashed() -> None:
    # digits.stamp only knows 0-9; the painter documents that it skips the rest.
    pos = Position(size=13, stones={Point(7, 7): "black"}, labels={Point(7, 7): "A"})
    img = render_png(pos, cell=24)
    assert isinstance(img, Image)


def test_png_noise_is_deterministic_in_the_seed() -> None:
    pos = Position(size=13, stones={Point(4, 4): "black", Point(10, 10): "white"})
    a = render_png(pos, cell=16, noise=2, seed=7)
    b = render_png(pos, cell=16, noise=2, seed=7)
    c = render_png(pos, cell=16, noise=2, seed=8)
    assert a.pixels == b.pixels, "same seed must reproduce the fixture byte for byte"
    assert a.pixels != c.pixels, "a different seed must produce different noise"

    clean = render_png(pos, cell=16, noise=0, seed=7)
    assert clean.pixels != a.pixels
    assert clean.pixels == render_png(pos, cell=16, noise=0, seed=99).pixels


def test_png_noise_stays_within_amplitude() -> None:
    pos = Position(size=13)
    clean = render_png(pos, cell=16, noise=0)
    noisy = render_png(pos, cell=16, noise=3, seed=5)
    deltas = [abs(a - b) for a, b in zip(clean.pixels, noisy.pixels, strict=True)]
    assert max(deltas) <= 3
    assert any(d > 0 for d in deltas)


def test_png_smoke_all_mark_types_and_kgs_palette() -> None:
    pos = Position(
        size=13,
        stones={Point(4, 4): "black", Point(10, 10): "white"},
        marks={
            Point(4, 4): Mark("triangle", "white"),
            Point(7, 7): Mark("square", "white"),
            Point(10, 4): Mark("circle", "black"),
            Point(4, 10): Mark("cross", "#e03c3c"),
        },
        labels={Point(10, 10): "3"},
    )
    img = render_png(pos, cell=24, palette=KGS_PALETTE, coords=True, noise=1, seed=3)
    side, _ = _png_geometry(13, 24)
    assert (img.width, img.height) == (side, side)
