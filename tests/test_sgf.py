"""Tests for goban_svg.sgf: static-position SGF read/write.

Covers exactly the cases design.md §9 calls out for this module: the
position -> sgf -> position round trip (including the G7 lossy-mark-color
behavior), rejection of move-based ("game") SGF, compressed point-list
("rectangle") expansion, and LB label escaping.
"""

from __future__ import annotations

import pytest

from goban_svg.board import Mark, Point, Position
from goban_svg.sgf import SgfError, position_from_sgf, position_to_sgf


def test_round_trip_preserves_stones_and_labels_but_drops_mark_color() -> None:
    pos = Position(
        size=19,
        stones={
            Point(1, 1): "black",
            Point(19, 19): "white",
            Point(4, 16): "black",
            Point(16, 4): "white",
        },
        marks={
            Point(4, 16): Mark(type="triangle", color="#2b5fe3"),
            Point(3, 3): Mark(type="square", color="black"),
            Point(10, 10): Mark(type="circle", color="white"),
            Point(16, 4): Mark(type="cross", color="#e03c3c"),
        },
        labels={Point(4, 16): "3", Point(10, 10): "12"},
    )

    text = position_to_sgf(pos)
    assert text.startswith("(;")
    assert text.endswith(")")

    round_tripped = position_from_sgf(text)

    assert round_tripped.size == pos.size
    assert round_tripped.stones == pos.stones
    assert round_tripped.labels == pos.labels

    # G7: SGF has no property for mark color, so position_to_sgf drops it —
    # every mark must come back with color=None regardless of what it went
    # in with. Assert this explicitly rather than just comparing dicts,
    # since a naive `marks == pos.marks` would (correctly) FAIL here.
    assert round_tripped.marks.keys() == pos.marks.keys()
    for point, original_mark in pos.marks.items():
        recovered = round_tripped.marks[point]
        assert recovered.type == original_mark.type
        assert recovered.color is None


def test_position_from_sgf_rejects_move_properties() -> None:
    game_sgf = "(;GM[1]FF[4]SZ[19]AB[pd];B[pp];W[dd])"

    with pytest.raises(SgfError, match="static positions"):
        position_from_sgf(game_sgf)


def test_position_from_sgf_rejects_move_property_even_without_setup() -> None:
    with pytest.raises(SgfError, match="static positions"):
        position_from_sgf("(;GM[1]FF[4]SZ[9];W[ee])")


def test_compressed_point_list_expands_rectangle() -> None:
    # "aa:cd": columns a..c (sx 0..2 -> col 1..3, 3 columns) x rows for
    # sy 0..3 -> at size=19, row = 19-sy = 19,18,17,16 (4 rows).
    # 3 columns * 4 rows = 12 points — NOT the 8 a naive "count corners"
    # guess might suggest; compute it, don't assume it.
    text = "(;GM[1]FF[4]SZ[19]AB[aa:cd])"

    pos = position_from_sgf(text)

    expected_points = {Point(col, row) for col in (1, 2, 3) for row in (16, 17, 18, 19)}
    assert len(expected_points) == 12
    assert set(pos.stones.keys()) == expected_points
    assert len(pos.stones) == 12
    assert all(color == "black" for color in pos.stones.values())


def test_compressed_point_list_round_trips_through_writer_reader() -> None:
    # position_to_sgf doesn't need to emit compressed lists itself (design.md
    # only requires the *reader* to support them); this checks the reader
    # handles diagonally-opposite corners given in either diagonal order.
    # "jm" = (col 10, row 1), "mj" = (col 13, row 4) at size=13.
    text = "(;GM[1]FF[4]SZ[13]AW[jm:mj])"

    pos = position_from_sgf(text)

    expected_points = {Point(col, row) for col in (10, 11, 12, 13) for row in (1, 2, 3, 4)}
    assert set(pos.stones.keys()) == expected_points
    assert all(color == "white" for color in pos.stones.values())


def test_label_escaping_round_trip() -> None:
    tricky_text = "x]y\\z"  # contains both characters SGF values must escape
    pos = Position(
        size=19,
        stones={},
        marks={},
        labels={Point(4, 4): tricky_text, Point(16, 16): "A"},
    )

    text = position_to_sgf(pos)

    # the raw SGF text must actually contain the escaped forms, not the bare
    # special characters (a bare ']' inside a value would terminate it early).
    assert "x\\]y\\\\z" in text

    round_tripped = position_from_sgf(text)
    assert round_tripped.labels == pos.labels


def test_label_escaping_tolerates_whitespace_and_newlines_around_properties() -> None:
    text = "(;GM[1]\nFF[4]\n  SZ[19]\nLB[aa:hi\\]there]\n)"

    pos = position_from_sgf(text)

    assert pos.labels == {Point(1, 19): "hi]there"}


def test_position_from_sgf_ignores_unknown_properties() -> None:
    text = "(;GM[1]FF[4]CA[UTF-8]GN[some game]SZ[19]AB[aa])"

    pos = position_from_sgf(text)

    assert pos.size == 19
    assert pos.stones == {Point(1, 19): "black"}
