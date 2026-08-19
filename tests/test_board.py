"""Tests for goban_svg.board: Point/Mark/Position, star points, ascii diagram.

Covers design.md sec 9 "board" bullet: notation parsing including the I-skip
(col 9 == "J"), bounds errors raising ValueError, JSON round trip preserving
stones/marks/labels/size, and sgf coordinate math (row 1 == bottom -> sy = size
- row).
"""

from __future__ import annotations

import pytest

from goban_svg.board import (
    COLUMN_LETTERS,
    MARK_TYPES,
    WEDGE_BLUE,
    WEDGE_RED,
    Mark,
    Point,
    Position,
    ascii_diagram,
    star_points,
)

# --------------------------------------------------------------------------- #
# Module-level constants
# --------------------------------------------------------------------------- #


def test_column_letters_skip_i() -> None:
    assert "I" not in COLUMN_LETTERS
    assert len(COLUMN_LETTERS) == 25
    assert COLUMN_LETTERS[0] == "A"
    assert COLUMN_LETTERS[-1] == "Z"


def test_wedge_color_constants() -> None:
    assert WEDGE_BLUE == "#2b5fe3"
    assert WEDGE_RED == "#e03c3c"


def test_mark_types_contents() -> None:
    assert frozenset({"triangle", "square", "circle", "cross"}) == MARK_TYPES


# --------------------------------------------------------------------------- #
# Point.parse / notation -- the I-skip convention
# --------------------------------------------------------------------------- #


def test_point_parse_basic() -> None:
    assert Point.parse("D17") == Point(col=4, row=17)


def test_point_parse_col9_is_j() -> None:
    # The 9th column letter is "J", not "I" -- design.md's explicit example.
    point = Point.parse("J9", size=19)
    assert point == Point(col=9, row=9)
    assert point.notation() == "J9"


def test_point_parse_rejects_i() -> None:
    with pytest.raises(ValueError, match="skipped"):
        Point.parse("I5", size=19)


def test_point_parse_last_column_is_t_on_19() -> None:
    # Well-known Go fact used as a cross-check: A..T skipping I on a 19x19 board.
    assert Point.parse("T19", size=19) == Point(col=19, row=19)
    assert Point.parse("T1", size=19) == Point(col=19, row=1)


@pytest.mark.parametrize("text", ["", "17", "D", "D-1", "Dxx", "D17Q"])
def test_point_parse_malformed_raises(text: str) -> None:
    with pytest.raises(ValueError):
        Point.parse(text, size=19)


def test_point_parse_out_of_bounds_row_raises() -> None:
    with pytest.raises(ValueError):
        Point.parse("T20", size=19)


def test_point_parse_out_of_bounds_col_raises() -> None:
    # "K" is column 10 (I skipped) -- out of bounds on a 9x9 board.
    with pytest.raises(ValueError):
        Point.parse("K5", size=9)


def test_point_parse_lowercase_accepted() -> None:
    assert Point.parse("d17", size=19) == Point(col=4, row=17)


def test_point_notation_round_trip() -> None:
    for text in ("A1", "J9", "T19", "H8", "K10"):
        assert Point.parse(text, size=19).notation() == text


def test_point_is_frozen_and_hashable() -> None:
    point = Point(col=4, row=17)
    with pytest.raises(AttributeError):
        point.col = 5  # type: ignore[misc]
    assert {point: "black"}[Point(col=4, row=17)] == "black"


# --------------------------------------------------------------------------- #
# Point.sgf -- coordinate math, and the "sgf alphabet doesn't skip I" gotcha
# --------------------------------------------------------------------------- #


def test_point_sgf_matches_design_example() -> None:
    # design.md sec 4: Point("D17").sgf(19) -> "dc"
    assert Point(col=4, row=17).sgf(19) == "dc"


def test_point_sgf_row1_is_bottom() -> None:
    # row 1 (bottom) -> sy = size - row = 19 - 1 = 18 -> 's' (0-based)
    assert Point(col=1, row=1).sgf(19) == "as"


def test_point_sgf_row_size_is_top() -> None:
    # row == size (top) -> sy = size - size = 0 -> 'a'
    assert Point(col=1, row=19).sgf(19) == "aa"


def test_point_sgf_alphabet_does_not_skip_i() -> None:
    # Unlike human notation, SGF letters are a plain base-26 a-z encoding: col 9
    # ("J" in human notation) is 'i' in SGF, not skipped.
    assert Point(col=9, row=9).sgf(9) == "ia"


# --------------------------------------------------------------------------- #
# Mark
# --------------------------------------------------------------------------- #


def test_mark_defaults_to_no_color() -> None:
    mark = Mark(type="triangle")
    assert mark.color is None


def test_mark_is_frozen_and_hashable() -> None:
    mark = Mark(type="square", color="black")
    with pytest.raises(AttributeError):
        mark.type = "circle"  # type: ignore[misc]
    assert hash(mark) == hash(Mark(type="square", color="black"))


# --------------------------------------------------------------------------- #
# Position.validate -- bounds and value errors
# --------------------------------------------------------------------------- #


def test_position_default_is_empty_and_valid() -> None:
    pos = Position()
    assert pos.size == 19
    assert pos.stones == {}
    assert pos.marks == {}
    assert pos.labels == {}
    pos.validate()  # must not raise


def test_position_validate_accepts_well_formed_position() -> None:
    pos = Position(
        size=19,
        stones={Point(3, 15): "black", Point(4, 17): "white"},
        marks={Point(4, 3): Mark(type="square", color="black")},
        labels={Point(4, 14): "3"},
    )
    pos.validate()  # must not raise


def test_position_validate_stone_out_of_bounds() -> None:
    pos = Position(size=19, stones={Point(20, 1): "black"})
    with pytest.raises(ValueError):
        pos.validate()


def test_position_validate_mark_out_of_bounds() -> None:
    pos = Position(size=9, marks={Point(1, 15): Mark(type="circle")})
    with pytest.raises(ValueError):
        pos.validate()


def test_position_validate_label_out_of_bounds() -> None:
    pos = Position(size=9, labels={Point(1, 15): "1"})
    with pytest.raises(ValueError):
        pos.validate()


def test_position_validate_bad_stone_color() -> None:
    pos = Position(size=19, stones={Point(1, 1): "red"})
    with pytest.raises(ValueError):
        pos.validate()


def test_position_validate_bad_mark_type() -> None:
    pos = Position(size=19, marks={Point(1, 1): Mark(type="hexagon")})
    with pytest.raises(ValueError):
        pos.validate()


def test_position_validate_bad_mark_color() -> None:
    pos = Position(size=19, marks={Point(1, 1): Mark(type="square", color="purple")})
    with pytest.raises(ValueError):
        pos.validate()


def test_position_validate_bad_size() -> None:
    pos = Position(size=0)
    with pytest.raises(ValueError):
        pos.validate()


# --------------------------------------------------------------------------- #
# JSON schema / round trip
# --------------------------------------------------------------------------- #


def _rich_position() -> Position:
    return Position(
        size=19,
        stones={
            Point(3, 15): "black",
            Point(4, 17): "white",
            Point(1, 1): "black",
            Point(19, 19): "white",
        },
        marks={
            Point(4, 3): Mark(type="square", color="black"),
            Point(4, 14): Mark(type="triangle", color=WEDGE_BLUE),
            Point(2, 2): Mark(type="circle"),  # no color
        },
        labels={Point(4, 14): "3", Point(1, 2): "12"},
    )


def test_to_json_dict_matches_schema() -> None:
    pos = Position(
        size=19,
        stones={Point(3, 15): "black", Point(4, 17): "white"},
        marks={Point(4, 3): Mark(type="square", color="black")},
        labels={Point(4, 14): "3"},
    )
    d = pos.to_json_dict()
    assert d == {
        "size": 19,
        "stones": {"black": ["C15"], "white": ["D17"]},
        "marks": [{"point": "D3", "type": "square", "color": "black"}],
        "labels": {"D14": "3"},
    }


def test_to_json_dict_sorts_stone_lists_and_marks() -> None:
    pos = Position(
        size=19,
        stones={Point(19, 19): "black", Point(1, 1): "black"},
        marks={Point(19, 19): Mark(type="cross"), Point(1, 1): Mark(type="circle")},
    )
    d = pos.to_json_dict()
    assert d["stones"]["black"] == ["A1", "T19"]
    assert [m["point"] for m in d["marks"]] == ["A1", "T19"]


def test_to_json_dict_emits_null_for_uncolored_mark() -> None:
    pos = Position(size=19, marks={Point(1, 1): Mark(type="circle")})
    d = pos.to_json_dict()
    assert d["marks"] == [{"point": "A1", "type": "circle", "color": None}]


def test_from_json_dict_round_trip_preserves_everything() -> None:
    original = _rich_position()
    restored = Position.from_json_dict(original.to_json_dict())
    assert restored.size == original.size
    assert restored.stones == original.stones
    assert restored.marks == original.marks
    assert restored.labels == original.labels


def test_to_json_from_json_text_round_trip() -> None:
    original = _rich_position()
    restored = Position.from_json(original.to_json())
    assert restored.size == original.size
    assert restored.stones == original.stones
    assert restored.marks == original.marks
    assert restored.labels == original.labels


def test_from_json_dict_rejects_invalid_point_notation() -> None:
    with pytest.raises(ValueError):
        Position.from_json_dict({"size": 19, "stones": {"black": ["I5"], "white": []}, "marks": [], "labels": {}})


def test_from_json_dict_rejects_out_of_bounds_point() -> None:
    with pytest.raises(ValueError):
        Position.from_json_dict({"size": 9, "stones": {"black": ["T19"], "white": []}, "marks": [], "labels": {}})


def test_empty_position_json_round_trip() -> None:
    original = Position(size=13)
    restored = Position.from_json(original.to_json())
    assert restored.size == 13
    assert restored.stones == {}
    assert restored.marks == {}
    assert restored.labels == {}


# --------------------------------------------------------------------------- #
# star_points
# --------------------------------------------------------------------------- #


def test_star_points_19() -> None:
    expected = {Point(c, r) for c in (4, 10, 16) for r in (4, 10, 16)}
    assert star_points(19) == frozenset(expected)
    assert len(star_points(19)) == 9


def test_star_points_13() -> None:
    expected = {Point(4, 4), Point(4, 10), Point(10, 4), Point(10, 10), Point(7, 7)}
    assert star_points(13) == frozenset(expected)
    assert len(star_points(13)) == 5


def test_star_points_9() -> None:
    expected = {Point(3, 3), Point(3, 7), Point(7, 3), Point(7, 7), Point(5, 5)}
    assert star_points(9) == frozenset(expected)
    assert len(star_points(9)) == 5


def test_star_points_other_size_is_empty() -> None:
    assert star_points(21) == frozenset()
    assert star_points(1) == frozenset()


# --------------------------------------------------------------------------- #
# ascii_diagram
# --------------------------------------------------------------------------- #


def test_ascii_diagram_full_layout() -> None:
    pos = Position(
        size=9,
        stones={Point(3, 3): "black", Point(7, 7): "white"},
        marks={Point(5, 5): Mark(type="triangle", color=WEDGE_BLUE)},
        labels={Point(5, 5): "3"},
    )
    expected = "\n".join(
        [
            "9 . . . . . . . . .",
            "8 . . . . . . . . .",
            "7 . . + . . . O . .",
            "6 . . . . . . . . .",
            "5 . . . . + . . . .",
            "4 . . . . . . . . .",
            "3 . . X . . . + . .",
            "2 . . . . . . . . .",
            "1 . . . . . . . . .",
            "  A B C D E F G H J",
            "",
            "E5: triangle #2b5fe3, label '3'",
        ]
    )
    assert ascii_diagram(pos) == expected


def test_ascii_diagram_no_legend_when_no_marks_or_labels() -> None:
    pos = Position(size=9)
    diagram = ascii_diagram(pos)
    assert "\n\n" not in diagram
    assert diagram.splitlines()[-1] == "  A B C D E F G H J"


def test_ascii_diagram_label_only_point_in_legend() -> None:
    pos = Position(size=9, labels={Point(1, 1): "7"})
    diagram = ascii_diagram(pos)
    assert diagram.splitlines()[-1] == "A1: label '7'"


def test_ascii_diagram_stone_overrides_star_point_glyph() -> None:
    pos = Position(size=9, stones={Point(3, 3): "black"})
    diagram = ascii_diagram(pos)
    row3 = next(line for line in diagram.splitlines() if line.startswith("3 "))
    assert row3 == "3 . . X . . . + . ."
