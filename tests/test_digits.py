"""Tests for goban_svg.digits: stamp() -> pixel mask -> resample -> recognize() round trips.

Mirrors the pipeline extract.py will run on real screenshots (find glyph
bbox on a contrast mask, resample to a 5x7 coverage grid at cell-fill
>= 0.35, Hamming-match against TEMPLATES) so a passing round trip here is
real evidence the font and the matcher agree with each other, not just that
recognize() can find its own templates.
"""

from __future__ import annotations

from goban_svg.digits import TEMPLATES, recognize, stamp
from goban_svg.png_codec import Image

_BACKGROUND = (255, 255, 255)
_INK = (0, 0, 0)
_GLYPH_COLS = 5
_GLYPH_ROWS = 7
_CELL_FILL_THRESHOLD = 0.35


def _ink_bbox(img: Image, x_range: range, y_range: range) -> tuple[int, int, int, int] | None:
    """Bounding box (x0, y0, x1, y1 inclusive) of non-background pixels within the given ranges."""
    x0 = y0 = None
    x1 = y1 = None
    for y in y_range:
        for x in x_range:
            if img.get(x, y) != _BACKGROUND:
                if x0 is None or x < x0:
                    x0 = x
                if x1 is None or x > x1:
                    x1 = x
                if y0 is None or y < y0:
                    y0 = y
                if y1 is None or y > y1:
                    y1 = y
    if x0 is None:
        return None
    assert x1 is not None and y0 is not None and y1 is not None
    return x0, y0, x1, y1


def _resample_to_cells(img: Image, bbox: tuple[int, int, int, int]) -> list[int]:
    """Resample the ink pixels inside `bbox` to a 5x7 row-major coverage grid.

    A cell is "filled" (1) when the fraction of non-background pixels inside
    it is >= 0.35, matching design.md section 6's extraction threshold.
    """
    x0, y0, x1, y1 = bbox
    width = x1 - x0 + 1
    height = y1 - y0 + 1
    cells: list[int] = []
    for row in range(_GLYPH_ROWS):
        cy0 = y0 + (row * height) // _GLYPH_ROWS
        cy1 = y0 + ((row + 1) * height) // _GLYPH_ROWS
        cy1 = max(cy1, cy0 + 1)
        for col in range(_GLYPH_COLS):
            cx0 = x0 + (col * width) // _GLYPH_COLS
            cx1 = x0 + ((col + 1) * width) // _GLYPH_COLS
            cx1 = max(cx1, cx0 + 1)
            total = 0
            ink = 0
            for y in range(cy0, cy1):
                for x in range(cx0, cx1):
                    total += 1
                    if img.get(x, y) != _BACKGROUND:
                        ink += 1
            fraction = ink / total if total else 0.0
            cells.append(1 if fraction >= _CELL_FILL_THRESHOLD else 0)
    return cells


def _split_columns(img: Image, bbox: tuple[int, int, int, int]) -> list[tuple[int, int, int, int]]:
    """Split a multi-glyph ink bbox into per-glyph bboxes at fully-empty columns."""
    x0, y0, x1, y1 = bbox
    ink_columns = []
    for x in range(x0, x1 + 1):
        has_ink = any(img.get(x, y) != _BACKGROUND for y in range(y0, y1 + 1))
        ink_columns.append(has_ink)

    runs: list[tuple[int, int]] = []
    run_start: int | None = None
    for i, has_ink in enumerate(ink_columns):
        if has_ink and run_start is None:
            run_start = i
        elif not has_ink and run_start is not None:
            runs.append((run_start, i - 1))
            run_start = None
    if run_start is not None:
        runs.append((run_start, len(ink_columns) - 1))

    bboxes = []
    for start, end in runs:
        gx0, gx1 = x0 + start, x0 + end
        sub = _ink_bbox(img, range(gx0, gx1 + 1), range(y0, y1 + 1))
        assert sub is not None
        bboxes.append(sub)
    return bboxes


def test_templates_shape() -> None:
    assert set(TEMPLATES) == set("0123456789")
    for digit, rows in TEMPLATES.items():
        assert len(rows) == _GLYPH_ROWS, digit
        for row in rows:
            assert len(row) == _GLYPH_COLS, digit
            assert set(row) <= {"0", "1"}, digit


def test_stamp_recognize_round_trip_all_digits() -> None:
    for scale in (2, 3):
        for digit in "0123456789":
            width_px = (_GLYPH_COLS + 4) * scale * 3
            height_px = _GLYPH_ROWS * scale * 3
            img = Image.new(width_px, height_px, fill=_BACKGROUND)
            cx, cy = width_px // 2, height_px // 2
            stamp(img, digit, cx, cy, scale=scale, color=_INK)

            bbox = _ink_bbox(img, range(width_px), range(height_px))
            assert bbox is not None, f"digit={digit} scale={scale}: nothing painted"
            cells = _resample_to_cells(img, bbox)
            result = recognize(cells)
            assert result == digit, f"scale={scale}: expected {digit!r}, got {result!r} (cells={cells})"


def test_stamp_recognize_multi_digit_split() -> None:
    scale = 3
    width_px = 200
    height_px = 80
    img = Image.new(width_px, height_px, fill=_BACKGROUND)
    cx, cy = width_px // 2, height_px // 2
    stamp(img, "12", cx, cy, scale=scale, color=_INK)

    bbox = _ink_bbox(img, range(width_px), range(height_px))
    assert bbox is not None
    glyph_bboxes = _split_columns(img, bbox)
    assert len(glyph_bboxes) == 2, f"expected 2 glyphs, got {len(glyph_bboxes)}: {glyph_bboxes}"

    recognized = [recognize(_resample_to_cells(img, gb)) for gb in glyph_bboxes]
    assert recognized == ["1", "2"]


def test_stamp_recognize_narrow_bar_is_one() -> None:
    # The narrow-bar (width/height < 0.34) shortcut is an extract.py heuristic
    # for a real screenshot's thin "1" stroke — the 5x7 TEMPLATES "1" glyph
    # itself has a serif foot (row 6 is "01110"), so its own bbox ratio is
    # ~0.43, not < 0.34. This test only confirms recognize() gets "1" right
    # through the normal resample-and-match path; extract.py's shortcut is
    # out of scope for this module's tests.
    scale = 4
    width_px = 60
    height_px = 60
    img = Image.new(width_px, height_px, fill=_BACKGROUND)
    stamp(img, "1", width_px // 2, height_px // 2, scale=scale, color=_INK)

    bbox = _ink_bbox(img, range(width_px), range(height_px))
    assert bbox is not None
    cells = _resample_to_cells(img, bbox)
    assert recognize(cells) == "1"


def test_recognize_blank_grid_is_ambiguous() -> None:
    # An all-empty coverage grid sits close to "1" (10) and "7" (11) in Hamming
    # distance — a 1-unit gap, under the default min_margin=2 — so it must be
    # rejected as ambiguous rather than silently guessed as either digit.
    cells = [0] * 35
    assert recognize(cells) is None


def test_recognize_full_grid_exceeds_max_distance() -> None:
    # A fully-inked coverage grid is far (>= 16) from every template under the
    # default max_distance=12, so it must be rejected outright.
    cells = [1] * 35
    assert recognize(cells) is None


def test_recognize_wrong_length_raises() -> None:
    try:
        recognize([0] * 34)
    except ValueError:
        pass
    else:
        raise AssertionError("recognize() should reject a non-35-length cells sequence")
