"""Real-screenshot regression tests.

The synthetic round trip (paint -> extract) is circular by construction: the
painter and the extractor share geometry assumptions, so a shared wrong
assumption passes it (design review F5, 2026-08-19 -- exactly what happened to
the original wedge-probe design). These tests break the circle: the committed
screenshots in examples/ are real app output, and their .json sidecars were
verified against the pixels stone by stone (marks and labels included) --
boards 1-3 by hand on 2026-08-19, board-4 via a class-ring overlay diff on
2026-08-21. The extractor must keep reproducing them exactly.

If an extractor change legitimately improves a reading, re-verify the affected
board visually and regenerate its sidecar with:
    uv run goban-svg extract examples/board-N.png -o examples/board-N.json
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from goban_svg import Mark, Point, extract_position, load_image
from goban_svg.board import WEDGE_BLUE, WEDGE_RED

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


@pytest.mark.parametrize("name", ["board-1", "board-2", "board-3", "board-4"])
def test_committed_screenshot_extracts_to_committed_json(name: str) -> None:
    result = extract_position(load_image(EXAMPLES / f"{name}.png"))
    expected = json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))
    assert result.position.to_json_dict() == expected
    assert result.warnings == []


def test_board1_acceptance_highlights() -> None:
    # design.md sec 12: white (3) at D14 under a blue wedge, black (2), white (1),
    # and the black square marker in the lower left.
    pos = extract_position(load_image(EXAMPLES / "board-1.png")).position
    assert pos.size == 19
    assert pos.labels[Point.parse("D14")] == "3"
    assert pos.stones[Point.parse("D14")] == "white"
    assert pos.marks[Point.parse("D14")] == Mark("triangle", WEDGE_BLUE)
    assert pos.labels[Point.parse("C8")] == "2"
    assert pos.labels[Point.parse("J3")] == "1"
    assert pos.marks[Point.parse("D2")] == Mark("square", "black")


def test_board2_and_board3_acceptance_highlights() -> None:
    # Red wedge on black (1); white wedge on a black stone; white square markers.
    pos2 = extract_position(load_image(EXAMPLES / "board-2.png")).position
    assert pos2.marks[Point.parse("P6")] == Mark("triangle", WEDGE_RED)
    assert pos2.stones[Point.parse("P6")] == "black"
    assert pos2.labels[Point.parse("P6")] == "1"
    assert pos2.marks[Point.parse("L9")] == Mark("square", "white")

    pos3 = extract_position(load_image(EXAMPLES / "board-3.png")).position
    assert pos3.marks[Point.parse("C3")] == Mark("triangle", "white")
    assert pos3.stones[Point.parse("C3")] == "black"
    assert pos3.marks[Point.parse("C15")] == Mark("square", "white")
    assert not pos3.labels


def test_board4_acceptance_highlights() -> None:
    # First staff-supplied fixture (2026-08-21): a DIFFERENT Go program's
    # screenshot (new palette, stone style, and chrome). Verified stone-by-stone
    # via a class-ring overlay diff against the original pixels. Highlights: a
    # labeled white stone under a blue triangle, and a black square marker on an
    # empty point (not a stone).
    pos = extract_position(load_image(EXAMPLES / "board-4.png")).position
    assert pos.size == 19
    assert pos.stones[Point.parse("D11")] == "white"
    assert pos.labels[Point.parse("D11")] == "1"
    assert pos.marks[Point.parse("D11")].type == "triangle"
    assert pos.marks[Point.parse("K8")] == Mark("square", "black")
    assert Point.parse("K8") not in pos.stones


def test_photo1_real_monitor_photo_with_rough_corners() -> None:
    # The first real-photo calibration fixture (2026-08-20): a phone photo of a
    # monitor -- moire banding, glare, perspective tilt. The corners given here
    # are the ORIGINAL rough hand estimates (up to ~0.4 cells off), so this
    # test also pins the corner auto-refinement path that recovered them
    # (calibration finding #1). Verified by hand against the rectified image.
    from goban_svg.photo import extract_photo_position

    result = extract_photo_position(
        load_image(EXAMPLES / "photo-1.png"),
        [(261, 124), (1228, 112), (1119, 951), (340, 956)],
        size=19,
    )
    expected = json.loads((EXAMPLES / "photo-1.json").read_text(encoding="utf-8"))
    assert result.position.to_json_dict() == expected
    # Edge moire produces a handful of honest ambiguity warnings; what must
    # NEVER appear is a refinement fallback (the grid fit works on this photo).
    assert not any("auto-refinement" in w for w in result.warnings)
