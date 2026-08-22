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
# B1 -- label VALUES are validated too: non-empty str, no control characters.
# Unvalidated labels used to reach the renderer, where a non-str died with a
# TypeError far from its cause and a control character produced an SVG file no
# XML parser will read -- with the tool reporting success either way.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("value", [3, None, 1.5, True, ["3"], {"text": "3"}])
def test_position_validate_rejects_non_string_label(value: object) -> None:
    pos = Position(size=19, labels={Point(4, 17): value})  # type: ignore[dict-item]
    with pytest.raises(ValueError, match="D17"):
        pos.validate()


def test_position_validate_rejects_empty_label() -> None:
    pos = Position(size=19, labels={Point(4, 17): ""})
    with pytest.raises(ValueError, match="D17"):
        pos.validate()


@pytest.mark.parametrize("char", ["\x00", "\n", "\r", "\t", "\x1f", "\x0b"])
def test_position_validate_rejects_control_characters_in_a_label(char: str) -> None:
    pos = Position(size=19, labels={Point(4, 17): f"a{char}b"})
    with pytest.raises(ValueError, match="D17"):
        pos.validate()


def test_position_validate_label_error_names_the_point_and_the_character() -> None:
    pos = Position(size=19, labels={Point(1, 1): "x\x00y"})
    with pytest.raises(ValueError, match=r"A1.*U\+0000"):
        pos.validate()


@pytest.mark.parametrize("text", ["3", "12", "A", "劫", "a b", " ", "x!", " "])
def test_position_validate_accepts_ordinary_label_text(text: str) -> None:
    # The rejection is narrow on purpose: only C0 controls (ord < 0x20) and the
    # empty string. Non-ASCII, punctuation and plain spaces stay legal labels.
    Position(size=19, labels={Point(4, 17): text}).validate()  # must not raise


# --------------------------------------------------------------------------- #
# Position.validate -- size cap (F6): 2..25, naming the 25-column notation limit
# --------------------------------------------------------------------------- #


def test_position_validate_size_below_cap_rejected() -> None:
    # size=1 used to be accepted (old check was `size < 1`); the cap tightens
    # the lower bound to 2, since a 0/1-sized board holds no position.
    pos = Position(size=1)
    with pytest.raises(ValueError, match="between 2 and 25"):
        pos.validate()


def test_position_validate_size_above_cap_rejected() -> None:
    pos = Position(size=26)
    with pytest.raises(ValueError, match="between 2 and 25"):
        pos.validate()


def test_position_validate_size_cap_message_names_column_limit() -> None:
    pos = Position(size=30)
    with pytest.raises(ValueError, match="25 columns"):
        pos.validate()


@pytest.mark.parametrize("size", [2, 25])
def test_position_validate_size_cap_boundaries_accepted(size: int) -> None:
    Position(size=size).validate()  # must not raise


def test_point_parse_column_letter_cannot_exceed_25() -> None:
    # COLUMN_LETTERS has exactly 25 entries, so even with an oversized `size`
    # argument, Point.parse can never hand back col > 25: a single letter tops
    # out at "Z" (col 25), and any multi-letter column code is rejected as
    # malformed before a col value is even computed.
    assert Point.parse("Z1", size=30) == Point(col=25, row=1)
    with pytest.raises(ValueError):
        Point.parse("AA1", size=30)


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
# from_json_dict -- F16: conflicting/duplicate source data rejected BEFORE
# dict-flattening would hide it, and wrong-typed input raises ValueError, not
# a raw TypeError/AttributeError/KeyError traceback.
# --------------------------------------------------------------------------- #


def test_from_json_dict_rejects_point_in_both_colors() -> None:
    d = {"size": 19, "stones": {"black": ["D17"], "white": ["D17"]}, "marks": [], "labels": {}}
    with pytest.raises(ValueError, match="D17"):
        Position.from_json_dict(d)


def test_from_json_dict_rejects_duplicate_stone_in_one_color() -> None:
    d = {"size": 19, "stones": {"black": ["D17", "D17"], "white": []}, "marks": [], "labels": {}}
    with pytest.raises(ValueError, match="D17"):
        Position.from_json_dict(d)


def test_from_json_dict_rejects_duplicate_stone_case_insensitive() -> None:
    # Point.parse normalizes case, so "d17" and "D17" are the same point --
    # the duplicate check must compare parsed Points, not raw notation text.
    d = {"size": 19, "stones": {"black": ["d17", "D17"], "white": []}, "marks": [], "labels": {}}
    with pytest.raises(ValueError, match="D17"):
        Position.from_json_dict(d)


def test_from_json_dict_rejects_two_marks_on_same_point() -> None:
    d = {
        "size": 19,
        "stones": {"black": [], "white": []},
        "marks": [{"point": "D3", "type": "square"}, {"point": "D3", "type": "circle"}],
        "labels": {},
    }
    with pytest.raises(ValueError, match="D3"):
        Position.from_json_dict(d)


def test_from_json_dict_rejects_missing_size() -> None:
    with pytest.raises(ValueError, match="size"):
        Position.from_json_dict({"stones": {"black": [], "white": []}})


def test_from_json_dict_rejects_non_int_size() -> None:
    with pytest.raises(ValueError):
        Position.from_json_dict({"size": "19"})


def test_from_json_dict_rejects_bool_size() -> None:
    # bool is an int subclass in Python -- must not silently accept True/False.
    with pytest.raises(ValueError):
        Position.from_json_dict({"size": True})


def test_from_json_dict_rejects_top_level_not_a_dict() -> None:
    with pytest.raises(ValueError):
        Position.from_json_dict(["not", "a", "dict"])  # type: ignore[arg-type]


def test_from_json_dict_rejects_stones_not_a_dict() -> None:
    with pytest.raises(ValueError, match="'stones'"):
        Position.from_json_dict({"size": 19, "stones": ["D17"]})


def test_from_json_dict_rejects_stone_color_list_not_a_list() -> None:
    with pytest.raises(ValueError, match="stones.black"):
        Position.from_json_dict({"size": 19, "stones": {"black": "D17", "white": []}})


def test_from_json_dict_rejects_stone_entry_wrong_type() -> None:
    with pytest.raises(ValueError, match="stones.black"):
        Position.from_json_dict({"size": 19, "stones": {"black": [17], "white": []}})


def test_from_json_dict_rejects_marks_not_a_list() -> None:
    with pytest.raises(ValueError, match="'marks'"):
        Position.from_json_dict({"size": 19, "marks": {"point": "D3", "type": "square"}})


def test_from_json_dict_rejects_marks_entry_not_a_dict() -> None:
    with pytest.raises(ValueError, match="'marks'"):
        Position.from_json_dict({"size": 19, "marks": ["D3"]})


def test_from_json_dict_rejects_marks_entry_missing_point() -> None:
    with pytest.raises(ValueError, match="point"):
        Position.from_json_dict({"size": 19, "marks": [{"type": "square"}]})


def test_from_json_dict_rejects_marks_entry_missing_type() -> None:
    with pytest.raises(ValueError, match="type"):
        Position.from_json_dict({"size": 19, "marks": [{"point": "D3"}]})


def test_from_json_dict_rejects_marks_entry_color_wrong_type() -> None:
    with pytest.raises(ValueError, match="color"):
        Position.from_json_dict({"size": 19, "marks": [{"point": "D3", "type": "square", "color": 5}]})


def test_from_json_dict_rejects_labels_not_a_dict() -> None:
    with pytest.raises(ValueError, match="'labels'"):
        Position.from_json_dict({"size": 19, "labels": ["D14: 3"]})


def test_from_json_dict_rejects_label_key_wrong_type() -> None:
    with pytest.raises(ValueError, match="'labels'"):
        Position.from_json_dict({"size": 19, "labels": {1: "3"}})


def test_from_json_dict_rejects_non_string_label_value() -> None:
    # B1: {"labels": {"A1": 3}} used to load, then die inside the SVG renderer
    # with a TypeError that named neither the point nor the schema.
    with pytest.raises(ValueError, match="A1"):
        Position.from_json_dict({"size": 19, "labels": {"A1": 3}})


def test_from_json_dict_rejects_null_label_value() -> None:
    # ...and a null used to ROUND TRIP: json null -> None -> written back out.
    with pytest.raises(ValueError, match="A1"):
        Position.from_json_dict({"size": 19, "labels": {"A1": None}})


def test_from_json_dict_rejects_empty_label_value() -> None:
    with pytest.raises(ValueError, match="A1"):
        Position.from_json_dict({"size": 19, "labels": {"A1": ""}})


def test_from_json_dict_rejects_control_character_in_a_label_value() -> None:
    with pytest.raises(ValueError, match="A1"):
        Position.from_json('{"size": 19, "labels": {"A1": "\\u0000"}}')


def test_from_json_dict_rejects_canonically_duplicate_label_keys() -> None:
    # B2: Point.parse normalizes case, so "a1" and "A1" are ONE point. The
    # second key used to overwrite the first without a word -- the same conflict
    # that stones and marks already reject.
    with pytest.raises(ValueError, match="A1"):
        Position.from_json_dict({"size": 19, "labels": {"a1": "3", "A1": "8"}})


def test_from_json_dict_rejects_duplicate_label_keys_differing_only_in_padding() -> None:
    # Point.parse also strips surrounding whitespace, so " A1 " collides too.
    with pytest.raises(ValueError, match="A1"):
        Position.from_json_dict({"size": 19, "labels": {"A1": "3", " A1 ": "8"}})


def test_from_json_dict_keeps_distinct_label_points() -> None:
    pos = Position.from_json_dict({"size": 19, "labels": {"a1": "3", "A2": "8"}})
    assert pos.labels == {Point(1, 1): "3", Point(1, 2): "8"}


def test_from_json_dict_rejects_unknown_stone_bucket() -> None:
    # B3: "Black" is not "black". The bucket used to be dropped in silence --
    # a whole color's stones vanishing from a position that loaded "fine".
    d = {"size": 19, "stones": {"Black": ["D17"], "white": []}, "marks": [], "labels": {}}
    with pytest.raises(ValueError, match="'Black'"):
        Position.from_json_dict(d)


@pytest.mark.parametrize("bucket", ["Black", "WHITE", "blak", "empty", "1"])
def test_from_json_dict_rejects_any_non_color_stone_bucket(bucket: str) -> None:
    with pytest.raises(ValueError, match="stones"):
        Position.from_json_dict({"size": 19, "stones": {bucket: ["D17"]}})


def test_from_json_dict_accepts_the_two_documented_stone_buckets_only() -> None:
    # The rejection must not be so eager that the real schema stops loading, and
    # an omitted bucket stays legal (it means "no stones of that color").
    pos = Position.from_json_dict({"size": 19, "stones": {"black": ["D17"], "white": ["C15"]}})
    assert pos.stones == {Point(4, 17): "black", Point(3, 15): "white"}
    assert Position.from_json_dict({"size": 19, "stones": {"black": ["D17"]}}).stones == {Point(4, 17): "black"}
    assert Position.from_json_dict({"size": 19, "stones": {}}).stones == {}


def test_from_json_dict_ignores_unknown_top_level_keys() -> None:
    d = {
        "size": 9,
        "stones": {"black": ["C3"], "white": []},
        "marks": [],
        "labels": {},
        "comment": "forward-compat field from a future schema version",
        "source": {"tool": "some-other-app"},
    }
    pos = Position.from_json_dict(d)
    assert pos.stones == {Point(3, 3): "black"}


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


def test_from_json_rejects_duplicate_keys() -> None:
    # Valid JSON, but plain json.loads keeps only the last bucket and the stones
    # in the first one vanish before validate() can object (code review r13).
    text = '{"size": 9, "stones": {"black": ["C3"], "black": ["D4"]}}'
    with pytest.raises(ValueError, match="duplicate key"):
        Position.from_json(text)


def test_from_json_rejects_duplicate_top_level_keys() -> None:
    text = '{"size": 9, "size": 19, "stones": {"black": ["C3"]}}'
    with pytest.raises(ValueError, match="duplicate key"):
        Position.from_json(text)


def test_label_rejects_lone_surrogate() -> None:
    # Survives Python and reaches render_svg, but a browser Blob rewrites it to
    # U+FFFD -- the downloaded SVG would not match its JSON (code review r13).
    pos = Position(size=9)
    pos.labels[Point.parse("C3")] = "\ud800"
    with pytest.raises(ValueError, match="U\\+D800"):
        pos.validate()
