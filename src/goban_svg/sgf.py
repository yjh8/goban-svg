"""Minimal SGF (Smart Game Format) reader/writer for STATIC Go positions.

goban-svg's faithful intermediate is JSON (see `board.py`); SGF is an interop
*export* format, offered because every Go tool speaks it. SGF cannot express
everything JSON can, so `position_from_sgf(position_to_sgf(pos))` is lossy in
two documented ways:

- **Mark colors are dropped.** There is no SGF property for them (`TR[dc]` is
  just "there is a triangle at dc" — no room for "and it's blue"), so every
  mark comes back with `color=None` (docs/design.md gotcha G7).
- **Label text is whitespace-normalized.** `LB` values are FF[4] SimpleText,
  which is single-line by definition: on the way back in, every line break
  becomes one space and every other whitespace character (tab, form feed, a
  no-break space, ...) also becomes a plain space — so the label
  `"a\\u00a0b"` comes back as `"a b"`. (A label can never carry a raw newline
  or tab in the FIRST place: `Position.validate` rejects C0 control characters
  in label text, since they are not expressible in the SVG this tool exists to
  emit. The normalization is what remains visible for the non-control
  whitespace that IS a legal label, and for hand-written SGF read from disk.)

What this module implements, deliberately narrowly:

- **Writer** — one FF[4]-conforming game tree with a single node:
  `(;FF[4]GM[1]CA[UTF-8]SZ[n]AB[..]AW[..]TR[..]SQ[..]CR[..]MA[..]LB[..:..])`.
- **Reader** — exactly ONE game tree, any number of *sequential* nodes.
  Setup properties are applied in node order, so a later node can edit what an
  earlier one placed: `AB`/`AW` add stones, `AE` removes them. WITHIN one node
  the three setup point sets must not overlap — FF[4] says property order
  inside a node is not significant, so `(;AB[aa]AW[aa])` has no defined
  meaning and is an `SgfError`, not a coin flip decided by which property the
  file happens to list second. ACROSS nodes the order IS the meaning, and
  last-wins stands. Marks
  (`TR`/`SQ`/`CR`/`MA`) and labels (`LB`) are independent overlays — `AE`
  clears the STONE at a point and deliberately leaves any mark/label there
  alone (SGF has no "erase annotation" property, and a diagram that keeps its
  circle after the stone under it is lifted is the useful reading).
  `FF`/`GM`/`CA` are accepted and ignored, except that a non-Go `GM` is an
  error. Every other property is ignored.
- **Not** implemented, by design: variations (a branching tree), move
  properties (`B`/`W`), and everything about playing out a game. The tool's
  job is "load/save a board position", not "replay a game", so each of those
  is rejected with an `SgfError` that says so.

Board sizes are capped at `_MAX_BOARD_SIZE` (25) because that is how far the
rest of goban-svg reaches: human point notation is one letter from
`board.COLUMN_LETTERS`, which holds 25 letters (Go notation skips "I"). A
26x26 SGF would parse here and then be unrepresentable in JSON, so it is
refused at the door instead.
"""

from __future__ import annotations

from dataclasses import dataclass

from goban_svg.board import COLUMN_LETTERS, Mark, Point, Position

# SGF mark-property idents <-> goban_svg.board.Mark.type values. Order here
# also fixes the emission order in position_to_sgf (stable, cosmetic only).
_MARK_PROP_TO_TYPE: dict[str, str] = {"TR": "triangle", "SQ": "square", "CR": "circle", "MA": "cross"}
_MARK_TYPE_TO_PROP: dict[str, str] = {v: k for k, v in _MARK_PROP_TO_TYPE.items()}

# SGF property idents that place a stone by playing a MOVE, as opposed to AB/AW
# which SET UP a static position. goban-svg only supports the latter (design.md
# §8: "SGF loader must reject game SGFs containing B[]/W[] moves ... static
# positions only"). Matching is by property IDENT, so every spelling of a move
# is caught -- B[], B[tt] (an old-style pass), B[pd] alike -- while unrelated
# properties that merely start with the same letter (AB/AW setup, BL/WL time
# left, BR/WR rank) are not.
_MOVE_PROPS = frozenset({"B", "W"})

# The three SETUP properties: they edit the same one piece of state (the stone at
# a point), which is why FF[4] forbids their point sets from overlapping inside a
# single node -- see position_from_sgf's within-node conflict check.
_SETUP_PROPS = frozenset({"AB", "AW", "AE"})

# Every property whose values are points or point RECTANGLES ("aa:cd"). They all
# go through _parse_point_list, so compressed lists work uniformly -- an AE or TR
# rectangle is exactly as valid as the AB rectangle design.md happens to name.
_POINT_LIST_PROPS = _SETUP_PROPS | frozenset(_MARK_PROP_TO_TYPE)

_DEFAULT_SIZE = 19
_MIN_BOARD_SIZE = 2
_MAX_BOARD_SIZE = len(COLUMN_LETTERS)  # 25 -- the reach of goban-svg's point notation

_SGF_A = ord("a")

# One property: its ident and its RAW bracket values (escape sequences intact).
_Property = tuple[str, list[str]]
# One node: the properties of a single ";" node, in source order.
_Node = list[_Property]


class SgfError(Exception):
    """Raised for SGF text goban-svg cannot parse, or a static-position violation."""


@dataclass(frozen=True)
class _ParsedSgf:
    """A structurally-scanned SGF file: its nodes plus the shape violations found.

    Violations are carried rather than raised on the spot so that
    `position_from_sgf` can report the most useful one first: a real game record
    with variations is far better described as "it has moves" than as "it has
    variations", and that ordering is only possible once the whole file is read.
    """

    nodes: list[_Node]
    has_variation: bool
    has_extra_tree: bool


def _escape_simple_text(text: str, *, escape_colon: bool = False) -> str:
    """Escape a value for embedding inside an SGF `[...]` bracket.

    `\\` and `]` must always be escaped: the first is the escape character
    itself, the second would terminate the value. `:` must additionally be
    escaped inside a COMPOSED value (`LB[point:label]`), where an unescaped
    colon is the field separator — a label like "C:\\tmp" would otherwise
    reparse as a different point/label split.

    Order is load-bearing: backslash FIRST, since escaping `]` or `:` inserts
    backslashes that a later backslash pass would double.
    """
    out = text.replace("\\", "\\\\").replace("]", "\\]")
    if escape_colon:
        out = out.replace(":", "\\:")
    return out


def _unescape_simple_text(raw: str) -> str:
    """Reverse `_escape_simple_text`, applying FF[4] SimpleText rules.

    - `\\` makes the next character literal (so `\\]`, `\\\\` and `\\:` come
      back as `]`, `\\` and `:`).
    - A backslash immediately followed by a newline is a SOFT line break: both
      characters vanish, joining the two halves with nothing between them.
    - Any other line break is a HARD one, which SimpleText renders as a single
      space; a CR/LF pair counts as one break, not two.
    - Any remaining whitespace (tab, form feed, ...) also becomes a space.
    """
    out: list[str] = []
    i = 0
    n = len(raw)
    while i < n:
        ch = raw[i]
        if ch == "\\":
            if i + 1 >= n:
                i += 1  # a lone trailing backslash escapes nothing; drop it
                continue
            nxt = raw[i + 1]
            if nxt in "\r\n":
                i = _skip_line_break(raw, i + 1)  # soft break: backslash + newline both vanish
                continue
            out.append(nxt)
            i += 2
            continue
        if ch in "\r\n":
            out.append(" ")  # hard break -> space (SimpleText is single-line)
            i = _skip_line_break(raw, i)
            continue
        out.append(" " if ch.isspace() else ch)
        i += 1
    return "".join(out)


def _skip_line_break(raw: str, i: int) -> int:
    """Index just past the line break at `raw[i]`, treating CRLF/LFCR as one break."""
    nxt = i + 1
    if nxt < len(raw) and raw[nxt] in "\r\n" and raw[nxt] != raw[i]:
        return nxt + 1
    return nxt


def _split_composed(raw: str, ident: str) -> tuple[str, str]:
    """Split a composed value at its first UNESCAPED colon (`LB[aa:label]`).

    Scanning for the separator has to respect escapes, or a label containing an
    escaped colon (`LB[aa:C\\:\\\\tmp]`) would split in the wrong place.
    """
    i = 0
    n = len(raw)
    while i < n:
        if raw[i] == "\\":
            i += 2
            continue
        if raw[i] == ":":
            return raw[:i], raw[i + 1 :]
        i += 1
    raise SgfError(f"malformed {ident} value {raw!r} — expected 'point:text', e.g. 'aa:1'")


def _sgf_xy(coord: str) -> tuple[int, int]:
    """Decode an SGF point into 0-based (x, y), x left->right and y TOP->bottom."""
    if len(coord) != 2 or not ("a" <= coord[0] <= "z") or not ("a" <= coord[1] <= "z"):
        raise SgfError(f"malformed SGF point {coord!r} (expected two lowercase letters, e.g. 'dc')")
    return ord(coord[0]) - _SGF_A, ord(coord[1]) - _SGF_A


def _point_to_sgf_coord(p: Point, size: int) -> str:
    return p.sgf(size)


def _describe_point(p: Point) -> str:
    """Human name for a point in an error message, safe for out-of-range columns.

    `Point.notation` indexes `COLUMN_LETTERS` (25 entries), so it raises for a
    column an SGF file can perfectly well spell (`z` -> col 26). Error paths that
    run BEFORE `Position.validate` has rejected such a point must not themselves
    blow up while describing it.
    """
    if 1 <= p.col <= len(COLUMN_LETTERS):
        return p.notation()
    return f"(col={p.col}, row={p.row})"


def _point_from_sgf_coord(coord: str, size: int) -> Point:
    """Inverse of `Point.sgf`: SGF coords are 0-based letters, y counted from top."""
    sx, sy = _sgf_xy(coord)
    return Point(col=sx + 1, row=size - sy)


def _parse_point_list(raw_values: list[str], size: int) -> list[Point]:
    """Expand a point property's bracket values, expanding `ul:lr` rectangles.

    Shared by every point-list property (`_POINT_LIST_PROPS`), so compressed
    lists behave identically on AB/AW/AE and on the mark properties.
    """
    points: list[Point] = []
    for raw in raw_values:
        if raw == "":
            continue
        parts = raw.split(":")
        if len(parts) == 1:
            points.append(_point_from_sgf_coord(parts[0], size))
        elif len(parts) == 2:
            x1, y1 = _sgf_xy(parts[0])
            x2, y2 = _sgf_xy(parts[1])
            if x2 < x1 or y2 < y1:
                # FF[4] fixes the corner order (upper-left first). Accepting an
                # inverted pair by silently sorting would also swallow a genuinely
                # transposed coordinate, so name the fix instead of guessing.
                fixed = f"{chr(_SGF_A + min(x1, x2))}{chr(_SGF_A + min(y1, y2))}"
                fixed += f":{chr(_SGF_A + max(x1, x2))}{chr(_SGF_A + max(y1, y2))}"
                raise SgfError(
                    f"inverted compressed point list [{raw}] — an SGF rectangle runs "
                    f"upper-left:lower-right (x and y both increasing); did you mean [{fixed}]?"
                )
            for x in range(x1, x2 + 1):
                for y in range(y1, y2 + 1):
                    points.append(Point(col=x + 1, row=size - y))
        else:
            raise SgfError(f"malformed compressed point list value {raw!r}")
    return points


def _check_board_size(size: int, source: str) -> None:
    if not (_MIN_BOARD_SIZE <= size <= _MAX_BOARD_SIZE):
        raise SgfError(
            f"{source}: goban-svg supports square boards of {_MIN_BOARD_SIZE}-{_MAX_BOARD_SIZE} points "
            f"(its point notation has {_MAX_BOARD_SIZE} column letters)"
        )


def position_to_sgf(pos: Position) -> str:
    """Render `pos` as a single-node static-position SGF string.

    The root node carries the FF[4] identification properties every conforming
    reader expects — `FF[4]GM[1]CA[UTF-8]SZ[n]` — before the setup data.

    Mark COLORS are dropped (SGF has no property for them — see module
    docstring / design.md G7); only mark TYPE and LOCATION survive.

    Raises `SgfError` for a board size this module cannot write, and for any
    position `Position.validate` rejects. Validating on the way OUT matters as
    much as on the way in: without it an out-of-bounds stone is silently written
    as a coordinate this module's own reader then refuses, and an invalid stone
    color or mark type is silently dropped from the file — the writer would
    manufacture a broken SGF and report success.
    """
    _check_board_size(pos.size, f"cannot write SZ[{pos.size}]")
    # After the size check, so an out-of-range SZ keeps this module's own
    # "2-25 points" wording rather than board.py's phrasing of the same limit.
    try:
        pos.validate()
    except ValueError as exc:
        raise SgfError(f"cannot write this position as SGF: {exc}") from exc

    parts: list[str] = ["FF[4]", "GM[1]", "CA[UTF-8]", f"SZ[{pos.size}]"]

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
            "LB"
            + "".join(
                f"[{_point_to_sgf_coord(p, pos.size)}:{_escape_simple_text(text, escape_colon=True)}]"
                for p, text in label_items
            )
        )

    return "(;" + "".join(parts) + ")"


def _scan_values(src: str, i: int, ident: str) -> tuple[list[str], int]:
    """Read the `[...]` values that follow a property ident; return them and the new index.

    Values are returned RAW (escape sequences like `\\]` are NOT resolved)
    because how a value should be unescaped/split is property-specific — see
    `_parse_point_list` versus the LB handling in `position_from_sgf`. Only the
    escape *structure* is honoured here, so that an escaped `]` does not end
    the value early.
    """
    n = len(src)
    values: list[str] = []
    while i < n and src[i] == "[":
        i += 1  # consume '['
        chars: list[str] = []
        while i < n and src[i] != "]":
            if src[i] == "\\" and i + 1 < n:
                chars.append(src[i])
                chars.append(src[i + 1])
                i += 2
            else:
                chars.append(src[i])
                i += 1
        if i >= n:
            raise SgfError(f"unterminated value for property {ident!r}")
        i += 1  # consume ']'
        values.append("".join(chars))
        while i < n and src[i].isspace():
            i += 1
    return values, i


def _parse_sgf(text: str) -> _ParsedSgf:
    """Scan SGF text into nodes, recording (but not raising on) shape violations.

    Unlike a general SGF parser this keeps no tree: goban-svg accepts exactly
    one linear node sequence, so a nested `(` (a variation) or a second
    top-level tree is a violation to report, not a structure to model.
    Whitespace between tokens is skipped; whitespace *inside* a bracket value is
    preserved verbatim for `_unescape_simple_text` to normalize.
    """
    # A file saved by a Windows editor can start with a BOM, and pipelines add
    # stray leading newlines; neither is a parse error. Strip twice so the BOM is
    # found whether it precedes or follows the whitespace.
    src = text.strip().lstrip("\ufeff").strip()
    if not src:
        raise SgfError("empty SGF text")
    if src[0] != "(":
        raise SgfError(f"not an SGF game tree: expected a leading '(', found {src[0]!r}")

    nodes: list[_Node] = []
    current: _Node | None = None
    depth = 0
    closed_root = False
    has_variation = False
    has_extra_tree = False

    i = 0
    n = len(src)
    while i < n:
        ch = src[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "(":
            if depth == 0:
                has_extra_tree = has_extra_tree or closed_root
            else:
                has_variation = True
            depth += 1
            i += 1
            continue
        if ch == ")":
            if depth == 0:
                raise SgfError(f"unbalanced ')' at position {i}")
            depth -= 1
            closed_root = closed_root or depth == 0
            i += 1
            continue
        if ch == ";":
            if depth == 0:
                raise SgfError(f"node ';' at position {i} sits outside any game tree")
            current = []
            nodes.append(current)
            i += 1
            continue
        if ch.isalpha() and ch.isupper():
            start = i
            while i < n and src[i].isalpha() and src[i].isupper():
                i += 1
            ident = src[start:i]
            while i < n and src[i].isspace():
                i += 1
            if i >= n or src[i] != "[":
                raise SgfError(f"property {ident!r} has no value (position {start})")
            if current is None or depth == 0:
                # Guarding on depth too: after the tree's ')' the last node is
                # still `current`, so trailing properties would otherwise be
                # silently folded into the position.
                raise SgfError(
                    f"property {ident!r} appears outside any node — properties must follow a ';' inside the game tree"
                )
            values, i = _scan_values(src, i, ident)
            current.append((ident, values))
            continue
        raise SgfError(f"unexpected character {ch!r} at position {i}")

    if depth != 0:
        raise SgfError("unterminated game tree: missing ')'")
    return _ParsedSgf(nodes=nodes, has_variation=has_variation, has_extra_tree=has_extra_tree)


def _parse_size(raw: str) -> int:
    """Parse an SZ value into a board size, enforcing square + in-range."""
    text = raw.strip()
    if ":" in text:
        cols, rows = (part.strip() for part in text.split(":", 1))
        if cols != rows:
            raise SgfError(
                f"non-square SZ[{text}] boards are not supported — goban-svg handles square boards of "
                f"{_MIN_BOARD_SIZE}-{_MAX_BOARD_SIZE} points"
            )
        text = cols
    try:
        size = int(text)
    except ValueError as exc:
        raise SgfError(f"malformed SZ value {raw!r}") from exc
    _check_board_size(size, f"SZ[{text}] is out of range")
    return size


def _check_game_type(raw: str) -> None:
    """Reject a non-Go record. An absent or empty GM means "Go" by SGF default."""
    game = raw.strip()
    if game and game != "1":
        raise SgfError(f"GM[{game}] is not a Go record — goban-svg only reads Go SGF (GM[1])")


def position_from_sgf(text: str) -> Position:
    """Parse static-position SGF text into a `Position`.

    Accepts exactly one game tree, whose nodes are applied in order: `AB`/`AW`
    place stones, `AE` removes them, `TR`/`SQ`/`CR`/`MA` add marks (always with
    color=None — SGF cannot carry mark color) and `LB` adds labels. Every point
    property accepts compressed rectangles (`AB[aa:cd]`, `TR[aa:cc]`, ...).
    `FF`/`GM`/`CA` are accepted and ignored (a non-Go `GM` is an error); all
    other properties are ignored. A leading BOM or whitespace is tolerated.

    Raises `SgfError` for a `B[...]`/`W[...]` MOVE property anywhere (goban-svg
    only supports static positions, not played-out games), for a branching tree
    (variations), for more than one game tree, for a board size outside 2-25,
    and for two setup properties (`AB`/`AW`/`AE`) claiming the same point within
    a SINGLE node — property order inside a node is not significant in FF[4], so
    `(;AB[aa]AW[aa])` is ambiguous rather than "white wins".
    """
    parsed = _parse_sgf(text)

    # Move rejection comes first on purpose: a real game record usually trips
    # several of these checks at once, and "it contains moves" is the message
    # that actually explains why goban-svg will not load it.
    for node in parsed.nodes:
        for ident, _values in node:
            if ident in _MOVE_PROPS:
                raise SgfError(
                    f"found {ident}[...] move property — goban-svg only supports static "
                    "positions (AB/AW setup stones), not played-out games; "
                    "playing out captures is out of scope"
                )

    if parsed.has_variation:
        raise SgfError(
            "variations not supported: this SGF branches into alternative lines, but goban-svg "
            "reads one static position — export a single line (or the position you want) instead"
        )
    if parsed.has_extra_tree:
        raise SgfError(
            "more than one game tree found — goban-svg reads a single position per file, not an SGF collection"
        )

    size = _DEFAULT_SIZE
    size_found = False
    for node in parsed.nodes:
        for ident, values in node:
            if ident == "GM" and values:
                _check_game_type(values[0])
            elif ident == "SZ" and values and not size_found:
                size = _parse_size(values[0])
                size_found = True

    stones: dict[Point, str] = {}
    marks: dict[Point, Mark] = {}
    labels: dict[Point, str] = {}

    for node in parsed.nodes:
        # Which setup property claimed each point IN THIS NODE. Reset per node:
        # the same point may legitimately be re-set by a later node (last wins),
        # but two setup properties inside ONE node contradict each other.
        setup_owner: dict[Point, str] = {}
        for ident, values in node:
            if ident in _POINT_LIST_PROPS:
                points = _parse_point_list(values, size)
                if ident in _SETUP_PROPS:
                    for point in points:
                        owner = setup_owner.get(point)
                        if owner is not None and owner != ident:
                            raise SgfError(
                                f"point {_describe_point(point)} appears in both {owner}[] and {ident}[] "
                                "in the same node — FF[4] setup point sets (AB/AW/AE) must not overlap "
                                "within one node, where property order carries no meaning; put the later "
                                "edit in its own node (';') if that is what you meant"
                            )
                        setup_owner[point] = ident
                if ident == "AB":
                    for point in points:
                        stones[point] = "black"
                elif ident == "AW":
                    for point in points:
                        stones[point] = "white"
                elif ident == "AE":
                    # AE lifts the STONE only; any mark/label at that point is an
                    # independent overlay and stays (see module docstring).
                    for point in points:
                        stones.pop(point, None)
                else:
                    mark = Mark(type=_MARK_PROP_TO_TYPE[ident], color=None)
                    for point in points:
                        marks[point] = mark
            elif ident == "LB":
                for raw in values:
                    if raw == "":
                        continue
                    point_text, label_text = _split_composed(raw, ident)
                    labels[_point_from_sgf_coord(point_text, size)] = _unescape_simple_text(label_text)
            # any other property (FF, CA, GN, AP, ...) is intentionally ignored.

    pos = Position(size=size, stones=stones, marks=marks, labels=labels)
    try:
        pos.validate()
    except ValueError as exc:
        raise SgfError(str(exc)) from exc
    return pos
