"""Tests for goban_svg.sgf: static-position SGF read/write.

Covers the cases design.md §9 calls out for this module -- the position -> sgf ->
position round trip (including the G7 lossy-mark-color behavior), rejection of
move-based ("game") SGF, compressed point-list ("rectangle") expansion, and LB
label escaping -- plus the FF[4] conformance rules the adversarial review added:
a conforming root node, full SimpleText escaping, compressed lists on *every*
point property, single-tree/multi-node semantics, and the 2-25 board-size cap.
"""

from __future__ import annotations

import pytest

from goban_svg import sgf as sgf_module
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

    # G7: SGF has no property for mark color, so position_to_sgf drops it --
    # every mark must come back with color=None regardless of what it went
    # in with. Assert this explicitly rather than just comparing dicts,
    # since a naive `marks == pos.marks` would (correctly) FAIL here.
    assert round_tripped.marks.keys() == pos.marks.keys()
    for point, original_mark in pos.marks.items():
        recovered = round_tripped.marks[point]
        assert recovered.type == original_mark.type
        assert recovered.color is None


# ---------------------------------------------------------------------------
# F9 -- move rejection is by property IDENT, so it covers every move spelling
# ---------------------------------------------------------------------------


def test_position_from_sgf_rejects_move_properties() -> None:
    game_sgf = "(;GM[1]FF[4]SZ[19]AB[pd];B[pp];W[dd])"

    with pytest.raises(SgfError, match="static positions"):
        position_from_sgf(game_sgf)


def test_position_from_sgf_rejects_move_property_even_without_setup() -> None:
    with pytest.raises(SgfError, match="static positions"):
        position_from_sgf("(;GM[1]FF[4]SZ[9];W[ee])")


@pytest.mark.parametrize(
    "move",
    [
        "B[]",  # FF[4] pass
        "W[]",
        "B[tt]",  # old-style pass on a <=19 board
        "B[pd]",
        "W[aa]",
        "B [pd]",  # whitespace between ident and value is legal SGF
        "B[pd][qq]",  # (malformed) multi-value move -- still a move
    ],
)
def test_move_rejection_covers_any_b_or_w_value(move: str) -> None:
    with pytest.raises(SgfError, match="static positions"):
        position_from_sgf(f"(;FF[4]GM[1]SZ[19]AB[aa];{move})")


def test_properties_that_merely_start_with_b_or_w_are_not_moves() -> None:
    # AB/AW are setup, BL/WL are time-left, BR/WR are ranks. A substring-based
    # move check would reject this file; an ident-based one must not.
    text = "(;FF[4]GM[1]SZ[19]AB[aa]AW[bb]BL[120.5]WL[95.0]BR[5d]WR[6d]PB[me]PW[you])"

    pos = position_from_sgf(text)

    assert pos.stones == {Point(1, 19): "black", Point(2, 18): "white"}


# ---------------------------------------------------------------------------
# F10 -- FF[4]-conforming root node
# ---------------------------------------------------------------------------


def test_position_to_sgf_emits_conforming_ff4_root() -> None:
    text = position_to_sgf(Position(size=13, stones={Point(4, 4): "black"}))

    assert text.startswith("(;FF[4]GM[1]CA[UTF-8]SZ[13]")
    assert text.endswith(")")
    # setup data follows the identification properties, not the other way round
    assert text.index("SZ[13]") < text.index("AB[")


def test_position_from_sgf_accepts_and_ignores_ff_gm_ca() -> None:
    pos = position_from_sgf("(;FF[4]GM[1]CA[UTF-8]AP[somewriter:1.0]SZ[9]AB[aa])")

    assert pos.size == 9
    assert pos.stones == {Point(1, 9): "black"}


def test_position_from_sgf_rejects_non_go_game_type() -> None:
    with pytest.raises(SgfError, match="GM\\[2\\]"):
        position_from_sgf("(;FF[4]GM[2]SZ[19]AB[aa])")


def test_position_from_sgf_tolerates_empty_gm_value() -> None:
    # GM[] carries no game type; SGF's default (Go) applies.
    assert position_from_sgf("(;FF[4]GM[]SZ[19]AB[aa])").stones == {Point(1, 19): "black"}


# ---------------------------------------------------------------------------
# F12 -- compressed point lists on EVERY point property
# ---------------------------------------------------------------------------


def test_compressed_point_list_expands_rectangle() -> None:
    # "aa:cd": columns a..c (sx 0..2 -> col 1..3, 3 columns) x rows for
    # sy 0..3 -> at size=19, row = 19-sy = 19,18,17,16 (4 rows).
    # 3 columns * 4 rows = 12 points -- NOT the 8 a naive "count corners"
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
    # only requires the *reader* to support them). "jj" = (col 10, row 4) and
    # "mm" = (col 13, row 1) at size=13, i.e. the upper-left and lower-right
    # corners of the same 4x4 block.
    text = "(;GM[1]FF[4]SZ[13]AW[jj:mm])"

    pos = position_from_sgf(text)

    expected_points = {Point(col, row) for col in (10, 11, 12, 13) for row in (1, 2, 3, 4)}
    assert set(pos.stones.keys()) == expected_points
    assert all(color == "white" for color in pos.stones.values())


def test_compressed_triangle_list_expands_to_nine_marks() -> None:
    pos = position_from_sgf("(;FF[4]GM[1]SZ[19]TR[aa:cc])")

    expected_points = {Point(col, row) for col in (1, 2, 3) for row in (17, 18, 19)}
    assert set(pos.marks.keys()) == expected_points
    assert len(pos.marks) == 9
    assert all(mark == Mark(type="triangle", color=None) for mark in pos.marks.values())


def test_compressed_point_lists_work_on_every_point_property() -> None:
    # AB fills a 2x2 block, a LATER NODE's AE clears one of its points, and each
    # mark property gets its own rectangle -- all four mark idents share one
    # parser. AE lives in its own node because AB and AE may not claim the same
    # point inside one node (see the within-node conflict tests below).
    text = "(;FF[4]GM[1]SZ[19]AB[aa:bb]SQ[dd:ed]CR[gg:hg]MA[rs:ss];AE[aa])"

    pos = position_from_sgf(text)

    assert set(pos.stones) == {Point(1, 18), Point(2, 19), Point(2, 18)}
    assert {p: m.type for p, m in pos.marks.items()} == {
        Point(4, 16): "square",
        Point(5, 16): "square",
        Point(7, 13): "circle",
        Point(8, 13): "circle",
        Point(18, 1): "cross",
        Point(19, 1): "cross",
    }


def test_degenerate_rectangle_is_a_single_point() -> None:
    assert position_from_sgf("(;FF[4]SZ[19]AB[aa:aa])").stones == {Point(1, 19): "black"}


@pytest.mark.parametrize(
    "value",
    [
        "cc:aa",  # both axes inverted (lower-right : upper-left)
        "jm:mj",  # y inverted only (lower-left : upper-right)
        "ca:ac",  # x inverted only (upper-right : lower-left)
    ],
)
def test_inverted_rectangles_are_rejected(value: str) -> None:
    with pytest.raises(SgfError, match="inverted compressed point list"):
        position_from_sgf(f"(;FF[4]SZ[19]AB[{value}])")


def test_inverted_rectangle_error_names_the_corrected_form() -> None:
    with pytest.raises(SgfError, match=r"\[jj:mm\]"):
        position_from_sgf("(;FF[4]SZ[13]AW[jm:mj])")


def test_malformed_point_list_value_is_rejected() -> None:
    with pytest.raises(SgfError, match="malformed"):
        position_from_sgf("(;FF[4]SZ[19]AB[aa:bb:cc])")


# ---------------------------------------------------------------------------
# F11 -- SimpleText escaping in both directions
# ---------------------------------------------------------------------------


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


def test_label_with_colons_backslash_and_bracket_round_trips_exactly() -> None:
    label = "C:\\tmp:a]"  # C:\tmp:a]
    pos = Position(size=19, labels={Point(1, 19): label})

    text = position_to_sgf(pos)

    # ':' is the composed-value separator, so it must be escaped in the LABEL
    # half or the value would resplit at the wrong place on read.
    assert "[aa:C\\:\\\\tmp\\:a\\]]" in text

    assert position_from_sgf(text).labels == {Point(1, 19): label}


def test_soft_line_break_in_label_is_removed() -> None:
    # backslash + newline is FF[4]'s soft line break: BOTH characters vanish.
    pos = position_from_sgf("(;FF[4]SZ[19]LB[aa:foo\\\nbar])")

    assert pos.labels == {Point(1, 19): "foobar"}


def test_hard_line_break_in_label_becomes_a_space() -> None:
    pos = position_from_sgf("(;FF[4]SZ[19]LB[aa:foo\nbar])")

    assert pos.labels == {Point(1, 19): "foo bar"}


def test_crlf_hard_line_break_counts_as_one_space() -> None:
    pos = position_from_sgf("(;FF[4]SZ[19]LB[aa:foo\r\nbar])")

    assert pos.labels == {Point(1, 19): "foo bar"}


def test_other_whitespace_in_label_is_normalized_to_space() -> None:
    pos = position_from_sgf("(;FF[4]SZ[19]LB[aa:a\tb])")

    assert pos.labels == {Point(1, 19): "a b"}


def test_escaped_colon_survives_a_hand_written_label() -> None:
    pos = position_from_sgf("(;FF[4]SZ[19]LB[aa:12\\:30])")

    assert pos.labels == {Point(1, 19): "12:30"}


def test_label_escaping_tolerates_whitespace_and_newlines_around_properties() -> None:
    text = "(;GM[1]\nFF[4]\n  SZ[19]\nLB[aa:hi\\]there]\n)"

    pos = position_from_sgf(text)

    assert pos.labels == {Point(1, 19): "hi]there"}


def test_label_without_a_separator_is_rejected() -> None:
    with pytest.raises(SgfError, match="expected 'point:text'"):
        position_from_sgf("(;FF[4]SZ[19]LB[aa])")


# ---------------------------------------------------------------------------
# F13 -- tree semantics: one tree, sequential nodes, no variations
# ---------------------------------------------------------------------------


def test_ae_in_a_later_node_removes_the_stone() -> None:
    assert position_from_sgf("(;AB[aa];AE[aa])").stones == {}


def test_ae_removes_the_stone_but_leaves_marks_and_labels() -> None:
    pos = position_from_sgf("(;FF[4]SZ[19]AB[aa]TR[aa]LB[aa:1];AE[aa])")

    assert pos.stones == {}
    assert pos.marks == {Point(1, 19): Mark(type="triangle", color=None)}
    assert pos.labels == {Point(1, 19): "1"}


def test_setup_properties_apply_in_node_order() -> None:
    # the later node repaints the same point -- last write wins
    assert position_from_sgf("(;FF[4]SZ[19]AB[aa];AW[aa])").stones == {Point(1, 19): "white"}


# ---------------------------------------------------------------------------
# S1 -- WITHIN one node, AB/AW/AE point sets may not overlap. FF[4] gives
# property order inside a node no meaning, so an overlap used to be resolved by
# source order alone: (;AB[aa]AW[aa]) yielded white and (;AW[aa]AB[aa]) black,
# from files that say the same thing.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "(;FF[4]SZ[19]AB[aa]AW[aa])",
        "(;FF[4]SZ[19]AW[aa]AB[aa])",  # the same file with its properties swapped
        "(;FF[4]SZ[19]AB[aa]AE[aa])",
        "(;FF[4]SZ[19]AE[aa]AB[aa])",
        "(;FF[4]SZ[19]AW[aa]AE[aa])",
    ],
)
def test_overlapping_setup_properties_in_one_node_are_rejected(text: str) -> None:
    with pytest.raises(SgfError, match="A19"):
        position_from_sgf(text)


def test_within_node_conflict_names_both_properties_and_the_point() -> None:
    with pytest.raises(SgfError, match=r"point A19 appears in both AB\[\] and AW\[\] in the same node"):
        position_from_sgf("(;FF[4]SZ[19]AB[aa]AW[aa])")


def test_within_node_conflict_is_caught_inside_a_compressed_rectangle() -> None:
    # AB[aa:bb] covers B18; the naive check (comparing raw values) would miss it.
    with pytest.raises(SgfError, match="B18"):
        position_from_sgf("(;FF[4]SZ[19]AB[aa:bb]AW[bb])")


def test_a_setup_property_repeating_its_own_point_is_not_a_conflict() -> None:
    # AB[aa][aa] is redundant, not contradictory: no ordering of it produces a
    # different board, so the conflict check deliberately stays out of its way.
    assert position_from_sgf("(;FF[4]SZ[19]AB[aa][aa])").stones == {Point(1, 19): "black"}
    assert position_from_sgf("(;FF[4]SZ[19]AB[aa]AB[aa])").stones == {Point(1, 19): "black"}


def test_a_mark_or_label_may_share_a_point_with_a_setup_stone() -> None:
    # Marks and labels are independent overlays, not setup properties -- they
    # must stay welcome on a point that also carries a stone.
    pos = position_from_sgf("(;FF[4]SZ[19]AB[aa]TR[aa]LB[aa:1])")

    assert pos.stones == {Point(1, 19): "black"}
    assert pos.marks == {Point(1, 19): Mark(type="triangle", color=None)}
    assert pos.labels == {Point(1, 19): "1"}


def test_setup_conflict_across_nodes_is_still_last_wins() -> None:
    # ACROSS nodes the order IS the meaning; only the within-node case is
    # ambiguous. Both directions must keep working.
    assert position_from_sgf("(;FF[4]SZ[19]AB[aa];AW[aa])").stones == {Point(1, 19): "white"}
    assert position_from_sgf("(;FF[4]SZ[19]AW[aa];AB[aa])").stones == {Point(1, 19): "black"}
    assert position_from_sgf("(;FF[4]SZ[19]AB[aa];AE[aa];AW[aa])").stones == {Point(1, 19): "white"}


def test_variations_are_rejected() -> None:
    with pytest.raises(SgfError, match="variations not supported"):
        position_from_sgf("(;AB[aa](;TR[bb])(;SQ[cc]))")


def test_a_single_nested_variation_is_still_rejected() -> None:
    with pytest.raises(SgfError, match="variations not supported"):
        position_from_sgf("(;FF[4]SZ[19]AB[aa](;AW[bb]))")


def test_move_rejection_wins_over_variation_rejection() -> None:
    # a real game record trips both checks; "it has moves" is the message that
    # actually explains why goban-svg won't load it.
    with pytest.raises(SgfError, match="static positions"):
        position_from_sgf("(;AB[aa](;B[pp]))")


def test_more_than_one_game_tree_is_rejected() -> None:
    with pytest.raises(SgfError, match="more than one game tree"):
        position_from_sgf("(;FF[4]SZ[19]AB[aa])(;FF[4]SZ[19]AB[bb])")


def test_unterminated_game_tree_is_rejected() -> None:
    with pytest.raises(SgfError, match="unterminated game tree"):
        position_from_sgf("(;FF[4]SZ[19]AB[aa]")


def test_property_outside_a_node_is_rejected() -> None:
    with pytest.raises(SgfError, match="outside any node"):
        position_from_sgf("(FF[4]SZ[19];AB[aa])")


def test_trailing_properties_after_the_tree_are_rejected() -> None:
    # the last node is still "open" in the scanner's hand -- without a depth
    # guard these stones would be folded silently into the position
    with pytest.raises(SgfError, match="outside any node"):
        position_from_sgf("(;FF[4]SZ[19]AB[aa])AW[bb]")


def test_trailing_node_after_the_tree_is_rejected() -> None:
    with pytest.raises(SgfError, match="outside any game tree"):
        position_from_sgf("(;FF[4]SZ[19]AB[aa]);AW[bb]")


def test_non_sgf_text_is_rejected_with_a_clear_message() -> None:
    with pytest.raises(SgfError, match="expected a leading"):
        position_from_sgf('{"size": 19}')


def test_empty_text_is_rejected() -> None:
    with pytest.raises(SgfError, match="empty SGF text"):
        position_from_sgf("   \n ")


# ---------------------------------------------------------------------------
# F6 (scope) -- board-size cap
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("size", [2, 9, 13, 19, 25])
def test_supported_board_sizes_round_trip(size: int) -> None:
    pos = Position(size=size, stones={Point(1, 1): "black", Point(size, size): "white"})

    assert position_from_sgf(position_to_sgf(pos)).stones == pos.stones


@pytest.mark.parametrize("raw", ["26", "1", "0", "99"])
def test_out_of_range_sz_is_rejected_naming_the_limit(raw: str) -> None:
    with pytest.raises(SgfError, match="2-25 points"):
        position_from_sgf(f"(;FF[4]GM[1]SZ[{raw}])")


def test_rectangular_sz_is_rejected() -> None:
    with pytest.raises(SgfError, match="non-square"):
        position_from_sgf("(;FF[4]GM[1]SZ[13:19]AB[aa])")


def test_square_composed_sz_is_accepted() -> None:
    # SZ[19:19] is the composed form of a square board -- odd, but not wrong.
    pos = position_from_sgf("(;FF[4]GM[1]SZ[19:19]AB[aa])")

    assert pos.size == 19
    assert pos.stones == {Point(1, 19): "black"}


def test_malformed_sz_is_rejected() -> None:
    with pytest.raises(SgfError, match="malformed SZ"):
        position_from_sgf("(;FF[4]GM[1]SZ[big])")


def test_position_to_sgf_refuses_an_unrepresentable_board_size() -> None:
    with pytest.raises(SgfError, match="2-25 points"):
        position_to_sgf(Position(size=26))


# ---------------------------------------------------------------------------
# S2 -- the WRITER validates. It used to emit whatever it was handed: an
# out-of-bounds stone became a coordinate this module's own reader rejects, and
# an invalid color or mark type was dropped from the file in silence.
# ---------------------------------------------------------------------------


def test_position_to_sgf_rejects_an_out_of_bounds_stone() -> None:
    pos = Position(size=19, stones={Point(20, 1): "black"})

    with pytest.raises(SgfError, match=r"col=20"):
        position_to_sgf(pos)


def test_position_to_sgf_rejects_an_invalid_stone_color() -> None:
    with pytest.raises(SgfError, match="red"):
        position_to_sgf(Position(size=19, stones={Point(1, 1): "red"}))


def test_position_to_sgf_rejects_an_invalid_mark_type() -> None:
    with pytest.raises(SgfError, match="hexagon"):
        position_to_sgf(Position(size=19, marks={Point(1, 1): Mark(type="hexagon")}))


def test_position_to_sgf_rejects_an_unwritable_label() -> None:
    with pytest.raises(SgfError, match="A1"):
        position_to_sgf(Position(size=19, labels={Point(1, 1): "a\x00b"}))


def test_position_to_sgf_still_writes_every_valid_position() -> None:
    # The new gate must reject only what board.py rejects.
    pos = Position(
        size=9,
        stones={Point(1, 1): "black", Point(9, 9): "white"},
        marks={Point(5, 5): Mark(type="circle", color="#2b5fe3")},
        labels={Point(3, 3): "12"},
    )

    assert position_from_sgf(position_to_sgf(pos)).stones == pos.stones


# ---------------------------------------------------------------------------
# S3 -- the SGF round trip is lossy on label WHITESPACE too, not just on mark
# colors (LB values are FF[4] SimpleText, which is single-line by definition).
# ---------------------------------------------------------------------------


def test_label_whitespace_is_normalized_on_the_round_trip() -> None:
    # A no-break space is the case that survives Position.validate (which bans
    # C0 controls outright), so it is the one that proves the lossiness on a
    # full write-then-read cycle rather than on hand-written SGF.
    pos = Position(size=19, labels={Point(1, 19): "a\u00a0b"})  # a NO-BREAK space

    assert position_from_sgf(position_to_sgf(pos)).labels == {Point(1, 19): "a b"}  # ...comes back plain


def test_module_docstring_documents_both_lossy_axes() -> None:
    doc = (sgf_module.__doc__ or "").lower()

    assert "mark color" in doc, "the mark-color lossiness must stay documented"
    assert "whitespace" in doc and "simpletext" in doc, "the label-whitespace lossiness must be documented too"


def test_missing_sz_defaults_to_19() -> None:
    assert position_from_sgf("(;FF[4]GM[1]AB[aa])").size == 19


def test_out_of_bounds_point_for_the_declared_size_is_rejected() -> None:
    with pytest.raises(SgfError, match="out of bounds"):
        position_from_sgf("(;FF[4]GM[1]SZ[9]AB[ss])")


# ---------------------------------------------------------------------------
# BOM / leading whitespace tolerance
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prefix",
    ["\ufeff", "\ufeff\n  ", "\n  \ufeff", "  \n\t"],
)
def test_bom_and_leading_whitespace_are_tolerated(prefix: str) -> None:
    pos = position_from_sgf(prefix + "(;FF[4]GM[1]SZ[19]AB[aa])\n")

    assert pos.stones == {Point(1, 19): "black"}


def test_position_from_sgf_ignores_unknown_properties() -> None:
    text = "(;GM[1]FF[4]CA[UTF-8]GN[some game]SZ[19]AB[aa])"

    pos = position_from_sgf(text)

    assert pos.size == 19
    assert pos.stones == {Point(1, 19): "black"}
