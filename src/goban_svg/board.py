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
        stone color, or a bad mark type/color.

        Out-of-bounds points are reported by raw (col, row) rather than through
        Point.notation(), which can only render points already inside
        COLUMN_LETTERS's 25-letter range -- calling notation() on the very point
        this check exists to catch could itself raise IndexError.
        """
        if self.size < 1:
            raise ValueError(f"invalid board size: {self.size}")
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
        """Inverse of to_json_dict(). Validates the result, so malformed notation
        or an out-of-bounds point surfaces as ValueError at load time rather than
        silently corrupting downstream rendering or extraction.
        """
        size = d["size"]
        stones: dict[Point, str] = {}
        for color in ("black", "white"):
            for notation in d.get("stones", {}).get(color, []):
                stones[Point.parse(notation, size)] = color
        marks: dict[Point, Mark] = {}
        for entry in d.get("marks", []):
            marks[Point.parse(entry["point"], size)] = Mark(type=entry["type"], color=entry.get("color"))
        labels: dict[Point, str] = {Point.parse(notation, size): text for notation, text in d.get("labels", {}).items()}
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
