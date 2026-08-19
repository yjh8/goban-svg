"""board.py -- the pure-data core model for a Go board position.

This is the base of the goban-svg dependency graph (see docs/interfaces.md): every
other module (png_codec, digits, sgf, render, extract, cli) either imports this
module directly or transitively, and it imports nothing from the rest of the
package.

Defines:
- ``Point``: a 1-based board coordinate with SGF conversion and the human "D17"
  notation used throughout the tool (column letters skip "I", per Go convention,
  so the 9th column is "J", not "I").
- ``Mark``: a marker at a point (triangle/square/circle/cross), used both for
  extracted annotations (e.g. the source app's corner "wedge" badges, recorded as
  triangles) and for hand-authored diagrams.
- ``Position``: the mutable, JSON-serializable position model that is the faithful
  intermediate format in the screenshot -> Position -> SVG pipeline (design.md
  sec 2/4). JSON is the primary interchange format here because SGF -- the export
  format in sgf.py -- cannot carry mark colors (design.md gotcha G7).
- ``star_points()``: standard hoshi (star point) locations for 9/13/19 boards.
- ``ascii_diagram()``: a plain-text rendering of a Position, backing the CLI's
  ``--ascii`` flag for quickly eyeballing a position without opening an image.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

COLUMN_LETTERS = "ABCDEFGHJKLMNOPQRSTUVWXYZ"
"""Column letters for human point notation: 25 letters, col 1 = 'A'.

Go notation conventionally skips "I" (too easily confused with "1" on a printed
diagram), so column 9 is "J", not "I". Every column-index<->letter conversion for
human notation must go through this constant rather than chr(ord('A') + col - 1).
"""

WEDGE_BLUE = "#2b5fe3"
"""Canonical recorded color for the source app's blue corner "wedge" badge.

Shared with render.py (which paints it) and extract.py (which detects and records
it), so the two stay in lockstep -- see design.md sec 6.
"""

WEDGE_RED = "#e03c3c"
"""Canonical recorded color for the source app's red corner "wedge" badge."""

_SGF_ALPHABET = "abcdefghijklmnopqrstuvwxyz"
# SGF point coordinates are a plain base-26 a-z encoding of a 0-based index and,
# unlike COLUMN_LETTERS, do NOT skip "i". Reusing COLUMN_LETTERS here would quietly
# shift every SGF coordinate from column 9 onward, so this alphabet is kept
# deliberately separate even though both are "letters for a column index".

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass(frozen=True)
class Point:
    """A 1-based board coordinate: col 1 = 'A' = left edge, row 1 = bottom edge."""

    col: int
    row: int

    @classmethod
    def parse(cls, text: str, size: int = 19) -> Point:
        """Parse human notation like "D17" into a Point, validated against `size`.

        Raises ValueError (naming the offending text) on malformed notation, an
        unrecognized column letter -- including "I", which is never valid, since
        Go notation skips it -- or a coordinate outside the `size`x`size` board.
        """
        stripped = text.strip()
        i = 0
        while i < len(stripped) and stripped[i].isalpha():
            i += 1
        letters, digits = stripped[:i], stripped[i:]
        if not letters or not digits or not digits.isdigit():
            raise ValueError(f"invalid point notation {text!r} (expected e.g. 'D17')")
        letter = letters.upper()
        if len(letter) != 1 or letter not in COLUMN_LETTERS:
            raise ValueError(f"invalid column letter in {text!r}: {letters!r} ('I' is skipped -- use A-H, J-Z)")
        col = COLUMN_LETTERS.index(letter) + 1
        row = int(digits)
        if not (1 <= col <= size) or not (1 <= row <= size):
            raise ValueError(f"point {text!r} is out of bounds for a {size}x{size} board")
        return cls(col=col, row=row)

    def notation(self) -> str:
        """Human notation, e.g. Point(4, 17).notation() == "D17" (col 9 -> "J")."""
        return f"{COLUMN_LETTERS[self.col - 1]}{self.row}"

    def sgf(self, size: int) -> str:
        """SGF point text, e.g. Point(4, 17).sgf(19) == "dc".

        SGF's y axis counts from the top of the board, so sy = size - row: row 1
        (the bottom line) becomes the largest sy. See design.md sec 4.
        """
        sx, sy = self.col - 1, size - self.row
        if not (0 <= sx < len(_SGF_ALPHABET)) or not (0 <= sy < len(_SGF_ALPHABET)):
            raise ValueError(f"point {self.notation()} has no SGF representation for size {size}")
        return f"{_SGF_ALPHABET[sx]}{_SGF_ALPHABET[sy]}"


MARK_TYPES: frozenset[str] = frozenset({"triangle", "square", "circle", "cross"})


@dataclass(frozen=True)
class Mark:
    """A marker at a point.

    `type` is checked against MARK_TYPES by Position.validate(), not at
    construction time, so marks can be built incrementally before the owning
    Position is complete/validated.
    """

    type: str
    color: str | None = None  # "black" | "white" | "#rrggbb" | None (auto-contrast at render)


def _is_valid_mark_color(color: str) -> bool:
    return color in ("black", "white") or bool(_HEX_COLOR_RE.match(color))


def _control_char(text: str) -> str | None:
    """First C0 control character in `text` (ord < 0x20), or None if there is none."""
    return next((ch for ch in text if ord(ch) < 0x20), None)


@dataclass
class Position:
    """A Go board position: stones, marks, and text labels on a `size`x`size` grid.

    This is the faithful JSON intermediate in the pipeline (design.md sec 2) --
    mutable and hand-editable, unlike the frozen Point/Mark it is built from.
    """

    size: int = 19
    stones: dict[Point, str] = field(default_factory=dict)
    marks: dict[Point, Mark] = field(default_factory=dict)
    labels: dict[Point, str] = field(default_factory=dict)

    def validate(self) -> None:
        """Raise ValueError on an invalid size, an out-of-bounds point, a bad
        stone color, a bad mark type/color, or a label that is not renderable
        text.

        `size` must be between 2 and 25 inclusive: human point notation (see
        COLUMN_LETTERS) only has 25 column letters, so a bigger board has no
        valid notation for its rightmost columns, and a 0/1-sized board has no
        meaningful position to hold.

        A label value must be a NON-EMPTY `str` containing no C0 control
        character (ord < 0x20). Each half of that is a real failure the model is
        the only place to stop: a non-str label (`3`, `None`) reaches the SVG
        renderer's text pass and dies there with a TypeError far from its cause
        -- or, via JSON, round-trips as a schema violation -- while a control
        character is simply not expressible in XML 1.0, so a label containing one
        would emit an SVG file that no parser will read while the tool reports
        success. Both are rejected here, naming the point.

        Out-of-bounds points are reported by raw (col, row) rather than through
        Point.notation(), which can only render points already inside
        COLUMN_LETTERS's 25-letter range -- calling notation() on the very point
        this check exists to catch could itself raise IndexError. Every later
        check may use notation() freely: the bounds loop has already run.
        """
        if not (2 <= self.size <= 25):
            raise ValueError(
                f"invalid board size {self.size}: must be between 2 and 25 "
                f"(human point notation supports at most {len(COLUMN_LETTERS)} columns, A-Z skipping I)"
            )
        for point in (*self.stones, *self.marks, *self.labels):
            if not (1 <= point.col <= self.size) or not (1 <= point.row <= self.size):
                raise ValueError(
                    f"point (col={point.col}, row={point.row}) is out of bounds for a {self.size}x{self.size} board"
                )
        for point, color in self.stones.items():
            if color not in ("black", "white"):
                raise ValueError(f"invalid stone color {color!r} at (col={point.col}, row={point.row})")
        for point, mark in self.marks.items():
            if mark.type not in MARK_TYPES:
                raise ValueError(f"invalid mark type {mark.type!r} at (col={point.col}, row={point.row})")
            if mark.color is not None and not _is_valid_mark_color(mark.color):
                raise ValueError(f"invalid mark color {mark.color!r} at (col={point.col}, row={point.row})")
        for point, text in self.labels.items():
            if not isinstance(text, str):
                raise ValueError(f"label at {point.notation()} must be a string, got {type(text).__name__}")
            if not text:
                raise ValueError(f"empty label at {point.notation()}: a label must be non-empty text")
            control = _control_char(text)
            if control is not None:
                raise ValueError(
                    f"label at {point.notation()} contains control character U+{ord(control):04X} "
                    f"(not expressible in XML): {text!r}"
                )

    def to_json_dict(self) -> dict:
        """Serialize to the schema documented in design.md sec 4 / interfaces.md.

        Point lists/keys are always sorted by notation string for stable, diffable
        output. Validates first, so a malformed Position never gets a chance to
        silently produce malformed JSON.
        """
        self.validate()
        black = sorted(p.notation() for p, color in self.stones.items() if color == "black")
        white = sorted(p.notation() for p, color in self.stones.items() if color == "white")
        marks = []
        for point in sorted(self.marks, key=lambda p: p.notation()):
            mark = self.marks[point]
            marks.append({"point": point.notation(), "type": mark.type, "color": mark.color})
        labels = {point.notation(): self.labels[point] for point in sorted(self.labels, key=lambda p: p.notation())}
        return {
            "size": self.size,
            "stones": {"black": black, "white": white},
            "marks": marks,
            "labels": labels,
        }

    @classmethod
    def from_json_dict(cls, d: dict) -> Position:
        """Inverse of to_json_dict(). Validates the result, so malformed notation,
        an out-of-bounds point, or an out-of-range size surfaces as ValueError at
        load time rather than silently corrupting downstream rendering or
        extraction.

        Conflicting or duplicate source data is rejected *before* it is
        flattened into the stones/marks/labels dicts -- a later duplicate entry
        silently overwriting an earlier one in a dict would hide the conflict
        entirely:

        - the same point listed under both "black" and "white" -> ValueError
          naming the point and both colors.
        - the same point repeated within one color's list -> ValueError naming
          the point and color.
        - two "marks" entries recorded at the same point -> ValueError naming
          the point.
        - two "labels" keys that name the SAME point once parsed ("a1" and
          "A1") -> ValueError naming the point. Point.parse normalizes case, so
          textually distinct keys can collide; without this check the second
          silently wins.

        Structurally wrong types anywhere in `d` (e.g. "stones" not an object,
        a color's point list not a list, a "marks" entry not an object, a label
        value not a string, "size" not an int) raise ValueError with a message
        naming the offending key -- never a bare TypeError/AttributeError/
        KeyError escaping from deep inside parsing.

        Unknown keys are treated differently at the two levels, deliberately.
        Unknown TOP-LEVEL keys in `d` are ignored, for forward compatibility
        with future schema additions. An unknown key inside "stones" is an
        ERROR: the schema's only buckets are "black" and "white", so anything
        else ("Black", "empty", a typo) is a bucket of stones that would be
        silently dropped from the loaded position -- data loss reported as
        success.
        """
        if not isinstance(d, dict):
            raise ValueError(f"position JSON must be an object, got {type(d).__name__}")
        if "size" not in d:
            raise ValueError("position JSON is missing required key 'size'")
        size = d["size"]
        if not isinstance(size, int) or isinstance(size, bool):
            raise ValueError(f"'size' must be an int, got {type(size).__name__}")

        stones_raw = d.get("stones", {})
        if not isinstance(stones_raw, dict):
            raise ValueError(f"'stones' must be an object with 'black'/'white' lists, got {type(stones_raw).__name__}")
        unknown_buckets = sorted(repr(key) for key in stones_raw if key not in ("black", "white"))
        if unknown_buckets:
            raise ValueError(
                f"unknown key(s) in 'stones': {', '.join(unknown_buckets)} -- "
                f"the only stone colors are 'black' and 'white' (keys are case-sensitive)"
            )
        stones: dict[Point, str] = {}
        for color in ("black", "white"):
            notations = stones_raw.get(color, [])
            if not isinstance(notations, list):
                raise ValueError(f"'stones.{color}' must be a list of point notations, got {type(notations).__name__}")
            for notation in notations:
                if not isinstance(notation, str):
                    raise ValueError(f"'stones.{color}' entries must be strings, got {notation!r}")
                point = Point.parse(notation, size)
                if point in stones:
                    if stones[point] == color:
                        raise ValueError(f"duplicate stone at {point.notation()} in 'stones.{color}'")
                    raise ValueError(f"point {point.notation()} is listed as both black and white")
                stones[point] = color

        marks_raw = d.get("marks", [])
        if not isinstance(marks_raw, list):
            raise ValueError(f"'marks' must be a list, got {type(marks_raw).__name__}")
        marks: dict[Point, Mark] = {}
        for entry in marks_raw:
            if not isinstance(entry, dict):
                raise ValueError(f"each 'marks' entry must be an object, got {type(entry).__name__}")
            if "point" not in entry:
                raise ValueError(f"'marks' entry missing required key 'point': {entry!r}")
            if "type" not in entry:
                raise ValueError(f"'marks' entry missing required key 'type': {entry!r}")
            point_notation = entry["point"]
            if not isinstance(point_notation, str):
                raise ValueError(f"'marks' entry 'point' must be a string, got {point_notation!r}")
            mark_type = entry["type"]
            if not isinstance(mark_type, str):
                raise ValueError(f"'marks' entry 'type' must be a string, got {mark_type!r} at {point_notation!r}")
            mark_color = entry.get("color")
            if mark_color is not None and not isinstance(mark_color, str):
                raise ValueError(
                    f"'marks' entry 'color' must be a string or null, got {mark_color!r} at {point_notation!r}"
                )
            point = Point.parse(point_notation, size)
            if point in marks:
                raise ValueError(f"two marks recorded at {point.notation()}")
            marks[point] = Mark(type=mark_type, color=mark_color)

        labels_raw = d.get("labels", {})
        if not isinstance(labels_raw, dict):
            raise ValueError(f"'labels' must be an object mapping point -> text, got {type(labels_raw).__name__}")
        labels: dict[Point, str] = {}
        for notation, text in labels_raw.items():
            if not isinstance(notation, str):
                raise ValueError(f"'labels' keys must be point-notation strings, got {notation!r}")
            if not isinstance(text, str):
                raise ValueError(f"'labels' values must be strings, got {text!r} at {notation!r}")
            point = Point.parse(notation, size)
            if point in labels:
                raise ValueError(f"duplicate label at {point.notation()} (two 'labels' keys name the same point)")
            labels[point] = text

        position = cls(size=size, stones=stones, marks=marks, labels=labels)
        position.validate()
        return position

    def to_json(self, indent: int = 2) -> str:
        """Render to JSON text (see to_json_dict for the schema)."""
        return json.dumps(self.to_json_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> Position:
        """Parse JSON text produced by to_json() (or hand-authored to the schema)."""
        return cls.from_json_dict(json.loads(text))


def star_points(size: int) -> frozenset[Point]:
    """Standard hoshi (star point) locations for 9/13/19 boards; empty otherwise.

    19x19: columns/rows {4, 10, 16} crossed (9 points; the center 10,10 already
    falls out of the cross product). 13x13: {4, 10} crossed, plus an explicit
    center at (7, 7). 9x9: {3, 7} crossed, plus an explicit center at (5, 5).
    """
    if size == 19:
        edge, center = (4, 10, 16), 10
    elif size == 13:
        edge, center = (4, 10), 7
    elif size == 9:
        edge, center = (3, 7), 5
    else:
        return frozenset()
    points = {Point(c, r) for c in edge for r in edge}
    points.add(Point(center, center))
    return frozenset(points)


def ascii_diagram(pos: Position) -> str:
    """Plain-text rendering: rows `size`..1 top-to-bottom, "X" black / "O" white /
    "." empty / "+" hoshi (a star point only shows through when it holds no
    stone), a column-letter header (skipping "I"), and -- when any marks or
    labels exist -- a blank line followed by one legend line per annotated point,
    e.g. "D14: triangle #2b5fe3, label '3'". Marks/labels never change the grid
    glyph; they are eyeballed via the legend. Backs the CLI's `--ascii` flag.
    """
    stars = star_points(pos.size)
    width = len(str(pos.size))
    lines: list[str] = []
    for row in range(pos.size, 0, -1):
        cells = []
        for col in range(1, pos.size + 1):
            point = Point(col, row)
            color = pos.stones.get(point)
            if color == "black":
                cells.append("X")
            elif color == "white":
                cells.append("O")
            elif point in stars:
                cells.append("+")
            else:
                cells.append(".")
        lines.append(f"{row:>{width}} " + " ".join(cells))
    lines.append(" " * (width + 1) + " ".join(COLUMN_LETTERS[c - 1] for c in range(1, pos.size + 1)))

    legend_points = sorted(set(pos.marks) | set(pos.labels), key=lambda p: p.notation())
    if legend_points:
        lines.append("")
        for point in legend_points:
            parts = []
            mark = pos.marks.get(point)
            if mark is not None:
                parts.append(f"{mark.type} {mark.color}" if mark.color else mark.type)
            label = pos.labels.get(point)
            if label is not None:
                parts.append(f"label '{label}'")
            lines.append(f"{point.notation()}: {', '.join(parts)}")
    return "\n".join(lines)
