"""Minimal SGF (Smart Game Format) reader/writer for STATIC Go positions.

goban-svg's faithful intermediate is JSON (see `board.py`); SGF is an interop
*export* format, offered because every Go tool speaks it. SGF cannot express
everything JSON can: mark COLORS have no SGF property (`TR[dc]` is just "there
is a triangle at dc" — no room for "and it's blue"). Round-tripping a
`Position` through SGF is therefore lossy on marks by design (see
docs/design.md gotcha G7); mark colors always come back `None` after
`position_from_sgf(position_to_sgf(pos))`.

This module only understands STATIC positions — a single setup node built
from `AB`/`AW`/`SZ`/`TR`/`SQ`/`CR`/`MA`/`LB`. It deliberately does not
implement a general SGF game-tree parser (variations, move sequences, game
info properties): the tool's job is "load/save a board position", not "play
out a game". Any `B[...]`/`W[...]` MOVE property found anywhere in the input
text is rejected with `SgfError` — see `position_from_sgf`.
"""

from __future__ import annotations

from goban_svg.board import Mark, Point, Position

# SGF mark-property idents <-> goban_svg.board.Mark.type values. Order here
# also fixes the emission order in position_to_sgf (stable, cosmetic only).
_MARK_PROP_TO_TYPE: dict[str, str] = {"TR": "triangle", "SQ": "square", "CR": "circle", "MA": "cross"}
_MARK_TYPE_TO_PROP: dict[str, str] = {v: k for k, v in _MARK_PROP_TO_TYPE.items()}

# SGF property idents that place a stone by playing a MOVE, as opposed to AB/AW
# which SET UP a static position. goban-svg only supports the latter (design.md
# §8: "SGF loader must reject game SGFs containing B[]/W[] moves ... static
# positions only"). Deliberately just "B"/"W" — NOT "BL"/"WL" (time left) or
# "AB"/"AW" (setup), which are unrelated properties that happen to share a
# letter.
_MOVE_PROPS = frozenset({"B", "W"})


class SgfError(Exception):
    """Raised for SGF text goban-svg cannot parse, or a static-position violation."""


def _escape_sgf_text(text: str) -> str:
    """Escape a value for embedding inside an SGF `[...]` bracket.

    SGF's only two escape-worthy characters for our purposes are the ones that
    would otherwise be mistaken for value syntax: `\\` (the escape character
    itself) and `]` (the value terminator). Backslash MUST be escaped first —
    escaping `]` before `\\` would double-escape the backslash it just inserted.
    """
    return text.replace("\\", "\\\\").replace("]", "\\]")


def _unescape_sgf_text(raw: str) -> str:
    """Reverse `_escape_sgf_text`: a backslash makes the following char literal."""
    out: list[str] = []
    i = 0
    n = len(raw)
    while i < n:
        ch = raw[i]
        if ch == "\\" and i + 1 < n:
            out.append(raw[i + 1])
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _point_to_sgf_coord(p: Point, size: int) -> str:
    return p.sgf(size)


def _point_from_sgf_coord(coord: str, size: int) -> Point:
    """Inverse of `Point.sgf`: SGF coords are 0-based letters, y counted from top."""
    if len(coord) != 2 or not coord[0].isalpha() or not coord[1].isalpha():
        raise SgfError(f"malformed SGF point {coord!r}")
    sx = ord(coord[0]) - ord("a")
    sy = ord(coord[1]) - ord("a")
    if not (0 <= sx < 26 and 0 <= sy < 26):
        raise SgfError(f"malformed SGF point {coord!r}")
    return Point(col=sx + 1, row=size - sy)


def _parse_point_list(raw_values: list[str], size: int) -> list[Point]:
    """Expand a property's bracket values into Points, expanding `aa:cd` rectangles."""
    points: list[Point] = []
    for raw in raw_values:
        if raw == "":
            continue
        parts = raw.split(":")
        if len(parts) == 1:
            points.append(_point_from_sgf_coord(parts[0], size))
        elif len(parts) == 2:
            p1 = _point_from_sgf_coord(parts[0], size)
            p2 = _point_from_sgf_coord(parts[1], size)
            col_lo, col_hi = sorted((p1.col, p2.col))
            row_lo, row_hi = sorted((p1.row, p2.row))
            for col in range(col_lo, col_hi + 1):
                for row in range(row_lo, row_hi + 1):
                    points.append(Point(col=col, row=row))
        else:
            raise SgfError(f"malformed compressed point list value {raw!r}")
    return points


def position_to_sgf(pos: Position) -> str:
    """Render `pos` as a single-node static-position SGF string.

    Mark COLORS are dropped (SGF has no property for them — see module
    docstring / design.md G7); only mark TYPE and LOCATION survive.
    """
    parts: list[str] = ["GM[1]", "FF[4]", f"SZ[{pos.size}]"]

    black = sorted((p for p, color in pos.stones.items() if color == "black"), key=lambda p: p.notation())
    white = sorted((p for p, color in pos.stones.items() if color == "white"), key=lambda p: p.notation())
    if black:
        parts.append("AB" + "".join(f"[{_point_to_sgf_coord(p, pos.size)}]" for p in black))
    if white:
        parts.append("AW" + "".join(f"[{_point_to_sgf_coord(p, pos.size)}]" for p in white))

    by_type: dict[str, list[Point]] = {}
    for p, mark in pos.marks.items():
        by_type.setdefault(mark.type, []).append(p)
    for mark_type, prop in _MARK_TYPE_TO_PROP.items():
        pts = by_type.get(mark_type)
        if pts:
            pts_sorted = sorted(pts, key=lambda p: p.notation())
            parts.append(prop + "".join(f"[{_point_to_sgf_coord(p, pos.size)}]" for p in pts_sorted))

    if pos.labels:
        label_items = sorted(pos.labels.items(), key=lambda kv: kv[0].notation())
        parts.append(
            "LB" + "".join(f"[{_point_to_sgf_coord(p, pos.size)}:{_escape_sgf_text(text)}]" for p, text in label_items)
        )

    return "(;" + "".join(parts) + ")"


def _tokenize_sgf(text: str) -> list[tuple[str, list[str]]]:
    """Flatten SGF text into `(property_ident, [raw_bracket_values])` pairs.

    Deliberately does NOT model SGF's game-tree structure (`(` / `)` for
    variations, `;` for node boundaries) — for a static-position loader every
    property in the file is either setup data we want or a move property we
    must reject, regardless of which node it sits in. Whitespace/newlines
    between properties are tolerated (skipped); whitespace *inside* a bracket
    value is preserved verbatim. Bracket values are returned RAW (escape
    sequences like `\\]` are NOT resolved here) because how a value should be
    unescaped/split is property-specific (see `_parse_point_list` vs the LB
    handling in `position_from_sgf`).
    """
    props: list[tuple[str, list[str]]] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace() or ch in "();":
            i += 1
            continue
        if ch.isalpha() and ch.isupper():
            start = i
            while i < n and text[i].isalpha() and text[i].isupper():
                i += 1
            ident = text[start:i]
            while i < n and text[i].isspace():
                i += 1
            if i >= n or text[i] != "[":
                raise SgfError(f"property {ident!r} has no value (position {start})")
            values: list[str] = []
            while i < n and text[i] == "[":
                i += 1  # consume '['
                raw_chars: list[str] = []
                while i < n and text[i] != "]":
                    if text[i] == "\\" and i + 1 < n:
                        raw_chars.append(text[i])
                        raw_chars.append(text[i + 1])
                        i += 2
                    else:
                        raw_chars.append(text[i])
                        i += 1
                if i >= n:
                    raise SgfError(f"unterminated value for property {ident!r}")
                i += 1  # consume ']'
                values.append("".join(raw_chars))
                while i < n and text[i].isspace():
                    i += 1
            props.append((ident, values))
        else:
            raise SgfError(f"unexpected character {ch!r} at position {i}")
    return props


def _parse_size(raw: str) -> int:
    raw = raw.strip()
    if ":" in raw:
        cols, rows = (part.strip() for part in raw.split(":", 1))
        if cols != rows:
            raise SgfError(f"non-square SZ[{raw}] boards are not supported")
        raw = cols
    try:
        return int(raw)
    except ValueError as exc:
        raise SgfError(f"malformed SZ value {raw!r}") from exc


def position_from_sgf(text: str) -> Position:
    """Parse static-position SGF text into a `Position`.

    Only `AB`/`AW` (setup stones), `SZ` (board size), `TR`/`SQ`/`CR`/`MA`
    (marks — recorded with color=None, SGF cannot carry mark color), and `LB`
    (labels) are understood. Any other property is ignored. Compressed point
    lists (`AB[aa:cd]`) are expanded to every point in the rectangle.

    Raises `SgfError` if the text contains a `B[...]`/`W[...]` MOVE property
    anywhere — goban-svg only supports static positions, not played-out games.
    """
    props = _tokenize_sgf(text)

    for ident, _values in props:
        if ident in _MOVE_PROPS:
            raise SgfError(
                f"found {ident}[...] move property — goban-svg only supports static "
                "positions (AB/AW setup stones), not played-out games; "
                "playing out captures is out of scope"
            )

    size = 19
    for ident, values in props:
        if ident == "SZ" and values:
            size = _parse_size(values[0])
            break

    stones: dict[Point, str] = {}
    marks: dict[Point, Mark] = {}
    labels: dict[Point, str] = {}

    for ident, values in props:
        if ident == "AB":
            for p in _parse_point_list(values, size):
                stones[p] = "black"
        elif ident == "AW":
            for p in _parse_point_list(values, size):
                stones[p] = "white"
        elif ident in _MARK_PROP_TO_TYPE:
            mark_type = _MARK_PROP_TO_TYPE[ident]
            for p in _parse_point_list(values, size):
                marks[p] = Mark(type=mark_type, color=None)
        elif ident == "LB":
            for raw in values:
                if len(raw) < 3 or raw[2] != ":":
                    raise SgfError(f"malformed LB value {raw!r}")
                point = _point_from_sgf_coord(raw[:2], size)
                labels[point] = _unescape_sgf_text(raw[3:])
        # any other property (GM, FF, CA, GN, ...) is intentionally ignored.

    pos = Position(size=size, stones=stones, marks=marks, labels=labels)
    try:
        pos.validate()
    except ValueError as exc:
        raise SgfError(str(exc)) from exc
    return pos
