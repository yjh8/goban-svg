# Interface contract — `goban_svg` modules

Signatures that cross module boundaries, fixed **before** implementation so modules built in
parallel agree (fleet module-independence convention). `docs/design.md` governs *semantics*;
this file governs *names and signatures*. If the two seem to conflict on a name, this file wins;
if on behavior, design.md wins. Implementers: deviate only if genuinely forced, and say so in
your report.

Module dependency DAG (no cycles):

```
board ──────────────┐
png_codec ──┬───────┼── render ──┐
digits ─────┘       ├── extract ─┼── cli
sgf (← board) ──────┘            │
__init__ (re-exports) ←──────────┘
```

## board.py

```python
COLUMN_LETTERS: str  # "ABCDEFGHJKLMNOPQRSTUVWXYZ" with "I" removed — 25 letters, col 1 = "A"

WEDGE_BLUE: str  # "#2b5fe3"  canonical recorded color for the app's blue corner wedge
WEDGE_RED: str  # "#e03c3c"  canonical recorded color for the red corner wedge
# (shared by extract.py, which records them, and render.py, which draws them)


@dataclass(frozen=True)
class Point:
    col: int  # 1-based, col 1 = "A" = left edge
    row: int  # 1-based, row 1 = bottom edge

    @classmethod
    def parse(cls, text: str, size: int = 19) -> "Point": ...  # "D17"; ValueError w/ clear msg
    def notation(self) -> str: ...  # "D17" (skip I)
    def sgf(self, size: int) -> str: ...  # "dc"; sgf y from top: sy = size - row


MARK_TYPES: frozenset[str]  # {"triangle", "square", "circle", "cross"}


@dataclass(frozen=True)
class Mark:
    type: str  # ∈ MARK_TYPES
    color: str | None = None  # "black" | "white" | "#rrggbb" | None (auto-contrast at render)


@dataclass
class Position:
    size: int = 19
    stones: dict[Point, str] = field(default_factory=dict)  # value ∈ {"black", "white"}
    marks: dict[Point, Mark] = field(default_factory=dict)
    labels: dict[Point, str] = field(default_factory=dict)

    def validate(self) -> None: ...  # ValueError on out-of-bounds / bad colors / bad mark types
    def to_json_dict(self) -> dict: ...  # the schema in design.md §4
    @classmethod
    def from_json_dict(cls, d: dict) -> "Position": ...
    def to_json(self, indent: int = 2) -> str: ...
    @classmethod
    def from_json(cls, text: str) -> "Position": ...


def star_points(size: int) -> frozenset[Point]: ...  # hoshi for 9/13/19; empty frozenset otherwise
def ascii_diagram(pos: Position) -> str: ...


# rows size→1 top-to-bottom, "X" black / "O" white / "." empty / "+" hoshi;
# marks/labels listed in a legend below the grid (e.g. "D14: triangle #2b5fe3, label '3'")
```

JSON schema (from design.md — `to_json_dict` emits exactly this shape):

```json
{
  "size": 19,
  "stones": {"black": ["C15"], "white": ["D17"]},
  "marks": [{"point": "D3", "type": "square", "color": "black"}],
  "labels": {"D14": "3"}
}
```

`marks[].color` may be absent/null (None). Point lists are sorted (stable output) — sort by
notation string is fine.

## png_codec.py  (imports nothing from this package)

```python
class PngError(Exception): ...  # unsupported feature (interlaced, exotic depth, unknown critical chunk...)


class PngCorruptError(PngError): ...  # damaged data: CRC mismatch, bad IHDR/PLTE/tRNS, zlib/length errors


@dataclass
class Image:
    width: int
    height: int
    pixels: bytearray  # len == width * height * 3, RGB8 row-major

    @classmethod
    def new(cls, width: int, height: int, fill: tuple[int, int, int] = (0, 0, 0)) -> "Image": ...
    def get(self, x: int, y: int) -> tuple[int, int, int]: ...
    def set(self, x: int, y: int, rgb: tuple[int, int, int]) -> None: ...
    def fill(self, rgb: tuple[int, int, int]) -> None: ...


def read_png(data: bytes) -> Image: ...  # bit depth 8/16, color types 0/2/3/4/6, filters 0–4
def write_png(img: Image) -> bytes: ...  # RGB8, filter 0, zlib level 6
def load_image(path: str | Path) -> Image: ...


# PNG → read_png; on unsupported-feature PngError or non-PNG magic → Pillow fallback if
# installed (the no-Pillow error carries the original reason + install guidance).
# PngCorruptError NEVER falls back: Pillow skips CRC checks and would silently decode
# corrupt bytes, defeating the integrity guarantee. tRNS transparency and alpha channels
# composite over opaque black.
```

## digits.py  (imports Image from png_codec)

```python
TEMPLATES: dict[str, tuple[str, ...]]  # "0"–"9" → 7 row-strings of 5 chars, "1" = ink (design.md §6)
ALT_TEMPLATES: dict[str, tuple[tuple[str, ...], ...]]  # per-digit alternate exemplars measured from
# real app fonts (e.g. the round-top '3'); recognize() scores each digit by its best exemplar,
# stamp() always paints the classic TEMPLATES face


def stamp(img: Image, text: str, cx: int, cy: int, scale: int = 2, color: tuple[int, int, int] = (0, 0, 0)) -> None: ...


# paint text centered at (cx, cy); each glyph 5×7 units at `scale` px/unit, 1-unit inter-glyph gap


def recognize(cells: Sequence[int], *, max_distance: int = 12, min_margin: int = 2) -> str | None: ...


# cells: 35 values (0/1), row-major 5-wide×7-tall coverage grid for ONE glyph.
# Best Hamming match over TEMPLATES; None if best > max_distance or runner-up within min_margin.
```

## sgf.py  (imports board)

```python
class SgfError(Exception): ...


def position_to_sgf(pos: Position) -> str: ...  # AB/AW/SZ + TR/SQ/CR/MA/LB (colors dropped — lossy)
def position_from_sgf(text: str) -> Position: ...


# rejects ANY B/W property (moves incl. passes): SgfError("... static positions ...");
# compressed point lists on AB/AW/AE/TR/SQ/CR/MA; full FF[4] SimpleText escaping in LB;
# one tree, no variations; sizes 2..25 square only; emits (;FF[4]GM[1]CA[UTF-8]SZ[n]...
```

## render.py  (imports board, png_codec, digits)

```python
@dataclass
class BoardGeometry:
    size: int
    cell: float
    coords: bool = False

    # margin = 0.72*cell beyond outer lines; coord gutters (left + bottom) when coords=True
    @property
    def width(self) -> float: ...
    @property
    def height(self) -> float: ...
    def point_xy(self, p: Point) -> tuple[float, float]: ...

    # x grows right from col 1; y grows DOWN from row `size` (row 1 is the bottom line)


def render_svg(pos: Position, *, cell: float = 36.0, coords: bool = False) -> str: ...


@dataclass(frozen=True)
class Palette:
    wood: tuple[int, int, int] = (231, 196, 122)
    line: tuple[int, int, int] = (67, 54, 31)
    # + whatever stone/marker colors the painter needs — painter-internal, name freely


KGS_PALETTE: Palette  # wood=(220, 179, 92)


def render_png(
    pos: Position,
    *,
    cell: int = 32,
    palette: Palette | None = None,
    coords: bool = False,
    noise: int = 0,
    seed: int = 1,
) -> Image: ...


# APP-style painter (fixture generator): triangle marks on stones are painted as SOLID CORNER
# WEDGES tucked into one corner of the stone's cell (chosen deterministically per point, so
# fixtures exercise every quadrant — the real app varies the corner too) in the mark's color; square marks
# on empty points are solid filled squares (half-width 0.22*cell); labels are stamped with
# digits.stamp in auto-contrast color; `noise` = ± per-channel amplitude via a deterministic
# LCG seeded with `seed` (no randomness).
```

## extract.py  (imports board, png_codec, digits)

```python
class ExtractionError(Exception): ...  # no board found / Nx != Ny (cropped) / unusable input


@dataclass
class GridFit:
    xs: list[float]  # fitted vertical-line x coords, left→right (len == size)
    ys: list[float]  # fitted horizontal-line y coords, top→bottom (len == size)
    spacing: float  # d
    bbox: tuple[int, int, int, int]  # x0, y0, x1, y1 wood bbox (inclusive)


@dataclass(frozen=True)
class UncertainPoint:
    point: Point
    kind: str  # "ambiguous-color" | "unreadable-label" (extract.py);
    # "ambiguous" | "warm-bright" | "off-image" | "no-reference" (photo.py)


@dataclass
class ExtractionResult:
    position: Position
    grid: GridFit
    warnings: list[str]
    uncertain: list[UncertainPoint] = field(default_factory=list)  # 0.1.1


def extract_position(img: Image) -> ExtractionResult: ...
```

`uncertain` (added 0.1.1) is the point-addressed mirror of the point-naming `warnings`: one entry
per such warning, same scan order, no duplicate `(point, kind)`. It is APPENDED with a default, so
the three-positional-argument construction stays valid; old pickles are unsupported (none exist —
the wheel is used in-process only). Warnings that name no point (board size, grid anisotropy, crop
margin, photo refinement fallback) get no entry, and **absence of an entry is not a confidence
claim** — see photo-mode-design.md finding #2, where nine white stones were misread silently. The
warning STRINGS are frozen byte for byte (callers regex-parse them); `uncertain` rides alongside
them and never replaces them.

Grid orientation note: `ys[0]` is the TOP image row, which is board row `size`; convert with
`row = size - y_index` when building Points (same convention as `Point.sgf`).

## cli.py  (imports everything)

```python
def main(argv: list[str] | None = None) -> int: ...  # design.md §8; also wired as __main__
```

## __init__.py

Re-exports: `__version__`, `Point`, `Mark`, `Position`, `render_svg`, `render_png`,
`extract_position`, `extract_photo_position`, `load_image`, `ascii_diagram`.

## photo.py  (imports board, extract [result types], png_codec) — EXPERIMENTAL, uncalibrated

```python
Corner = tuple[float, float]  # (x, y) in source-photo pixels


def validate_corners(corners: Sequence[Corner]) -> tuple[Corner, Corner, Corner, Corner]: ...


# contract: TL, TR, BR, BL in the photo's screen orientation; ValueError on crossed/
# concave/mirrored/degenerate quads — NO reordering is attempted


def rectify_board(img: Image, corners: Sequence[Corner], size: int, cell: int = 24) -> Image: ...
def refine_corners(img: Image, corners: Sequence[Corner], size: int) -> tuple[tuple[Corner, ...], bool]: ...


# fail-CLOSED: corners are replaced ONLY on verified convergence; any doubt returns the
# caller's corners with converged=False (skipped silently for size < 5)


@dataclass(frozen=True)
class PhotoArtifact:
    result: ExtractionResult
    canonical: Image  # the rectified image the classifier ACTUALLY read (same object)
    refined: bool  # did auto-refinement verifiably converge?
    corners_used: tuple[Corner, ...]  # the corners the rectification ran on


def extract_photo_artifact(
    img: Image, corners: Sequence[Corner], size: int, *, refine: bool = True
) -> PhotoArtifact: ...


def extract_photo_position(
    img: Image, corners: Sequence[Corner], size: int, *, refine: bool = True
) -> ExtractionResult: ...


# refine=True (default) runs refine_corners first and warns when it fell back;
# extract_photo_position is a thin wrapper returning extract_photo_artifact(...).result
# (0.1.1) — same signature, same warnings, same result


# stones only (photos carry no labels/marks); GridFit coordinates live in the RECTIFIED
# canonical plane (see the generalized GridFit note); every threshold is UNCALIBRATED
# until the real-photo corpus exists (docs/photo-mode-design.md amendments, gate B3)
```

GridFit note (generalized): `xs`/`ys`/`bbox` are in the *classified image plane* — the
input screenshot for `extract_position`, the rectified canonical image for
`extract_photo_position`.

Single-pass rule (webapp-design.md v3 amendment 2): one call to `extract_photo_artifact`
performs exactly ONE classification rectification, and `canonical` is that image itself,
not a copy or a re-derivation — the caller (the web checkpoint) draws its grid overlay on
the very pixels the stones were read from. Rectification measures ~2.5 s against ~0.1 s for
classification, so a second pass would more than double the wait. A regression test counts
`_rectify_masked` calls.
