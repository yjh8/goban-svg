"""Command-line entry point: `goban-svg convert / extract / photo / render`.

This is the top of the dependency DAG (docs/interfaces.md) -- it imports every
other module and wires them into four subcommands:

- ``convert IMAGE`` -- the common case, extract + render in one step. Reads a
  screenshot, writes the clean SVG plus a JSON sidecar (the human-editable
  intermediate, design.md sec 2/4), and prints a one-line summary.
- ``extract IMAGE`` -- screenshot to JSON only (and optionally SGF), for when a
  caller wants to inspect or hand-edit the position before rendering.
- ``photo IMAGE --corners ... --size N`` -- EXPERIMENTAL assisted extraction for
  photos of physical boards (docs/photo-mode-design.md): user-supplied corner
  intersections + declared size, stones only, uncalibrated until the real-photo
  corpus exists.
- ``render POS.json|POS.sgf`` -- JSON or SGF back to SVG, the other half of the
  designed correction loop: extract, notice the OCR got a label wrong, edit the
  JSON by hand, re-render.

All three accept ``--ascii`` (print :func:`goban_svg.board.ascii_diagram` for
quick eyeballing without opening an image) and ``convert``/``render`` accept
``--preview OUT.png`` (also paint the app-style raster via
:func:`goban_svg.render.render_png`, for a quick look without an SVG viewer).

Output paths default to the input path with its suffix swapped (``board.png``
-> ``board.svg`` / ``board.json``), matching design.md sec 8's worked example.
An explicit ``-o``/``--json``/``--sgf``/``--preview`` always wins.

Error handling: :func:`main` never lets a user-facing error (a missing file, a
bad SGF, a malformed image, an unreadable JSON) escape as a traceback -- it is
caught, printed to stderr with an ``error:`` prefix, and turned into exit code
1. Extraction *warnings* (a doubtful stone color, an unreadable label) are not
errors: the position is still written, and each warning goes to stderr on its
own line so stdout stays clean for the summary/ASCII output a script might
capture.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

from goban_svg.board import Position, ascii_diagram
from goban_svg.extract import ExtractionError, extract_position
from goban_svg.photo import extract_photo_position
from goban_svg.png_codec import PngError, load_image, write_png
from goban_svg.render import render_png, render_svg
from goban_svg.sgf import SgfError, position_from_sgf, position_to_sgf

__all__ = ["main"]

# Errors a bad but plausible user input can trigger: a missing/unreadable file
# or image (OSError, PngError), a JSON position missing a required key
# (KeyError) or otherwise malformed (ValueError, the base of
# json.JSONDecodeError), or a static-position-only SGF violation (SgfError).
# main() turns every one of these into a clean stderr message + exit code 1
# instead of a traceback -- these are the errors the designed correction loop
# expects a human to hit and fix, not programming bugs.
_USER_ERRORS: tuple[type[Exception], ...] = (ExtractionError, SgfError, PngError, OSError, ValueError, KeyError)


def _default_output(input_path: Path, suffix: str) -> Path:
    """Derive a default output path by swapping the input's file extension."""
    return input_path.with_suffix(suffix)


def _check_no_input_collision(input_path: Path, output_paths: list[Path | None]) -> None:
    """Refuse before writing anything if a planned output IS the input file (C2).

    Compares *resolved* (absolute, symlink-followed) paths, so this catches
    every form of the collision: a default suffix-swap that happens to land
    back on the input (e.g. ``convert`` on a PNG misnamed ``mis.json`` -- the
    default JSON sidecar path is then identical to the input), an explicit
    ``-o`` pointing at the input, and an explicit ``--json``/``--sgf``/
    ``--preview`` doing the same. ``None`` entries (an output the user didn't
    request) are skipped.
    """
    resolved_input = input_path.resolve()
    seen: dict[Path, Path] = {}
    for out_path in output_paths:
        if out_path is None:
            continue
        resolved = out_path.resolve()
        if resolved == resolved_input:
            raise ValueError(f"output {out_path} would overwrite the input file")
        # Outputs must also be distinct from EACH OTHER: `-o same --json same`
        # would silently overwrite the SVG with JSON (photo code review M6).
        if resolved in seen:
            raise ValueError(
                f"output paths {seen[resolved]} and {out_path} are the same file -- outputs must be distinct"
            )
        seen[resolved] = out_path


def _check_sidecar_writable(json_path: Path, new_text: str, *, force: bool) -> None:
    """Refuse to clobber a JSON sidecar whose on-disk content differs from what
    this run would write (C3) -- the sidecar is the designed correction-loop
    artifact (extract, hand-edit, re-render), so silently overwriting a
    human's edits on every ``convert`` re-run is the bug. An identical
    existing sidecar is a silent no-op so idempotent re-runs stay pleasant;
    ``--force`` always proceeds.
    """
    if force or not json_path.exists():
        return
    if json_path.read_text(encoding="utf-8") == new_text:
        return
    raise ValueError(
        f"JSON sidecar {json_path} already exists with edits that differ from this run's output -- "
        f"your edits are preserved; re-render them with 'goban-svg render {json_path}', "
        "write elsewhere with --json, or overwrite with --force"
    )


def _parse_position(text: str) -> Position:
    """Sniff JSON vs SGF: a leading '(' (after whitespace) means SGF (design.md sec 8)."""
    if text.lstrip().startswith("("):
        return position_from_sgf(text)
    return Position.from_json(text)


def _write_preview(position: Position, path: Path, *, coords: bool, cell: float | None) -> None:
    """Render the app-style PNG preview and write it via png_codec.write_png."""
    kwargs: dict[str, object] = {"coords": coords}
    if cell is not None:
        kwargs["cell"] = int(cell)  # render_png's `cell` is pixel-integer, unlike render_svg's float
    path.write_bytes(write_png(render_png(position, **kwargs)))


def _summary_line(position: Position, svg_path: Path) -> str:
    """The one-line convert summary, e.g. "19x19, 34 black, 33 white, 2 marks, 1 label -> board-1.svg"."""
    black = sum(1 for color in position.stones.values() if color == "black")
    white = sum(1 for color in position.stones.values() if color == "white")
    marks, labels = len(position.marks), len(position.labels)
    mark_word = "mark" if marks == 1 else "marks"
    label_word = "label" if labels == 1 else "labels"
    return (
        f"{position.size}×{position.size}, {black} black, {white} white, "
        f"{marks} {mark_word}, {labels} {label_word} → {svg_path}"
    )


def _emit_warnings(warnings: list[str]) -> None:
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)


# --------------------------------------------------------------------------- #
# Subcommand handlers
# --------------------------------------------------------------------------- #


def _cmd_convert(args: argparse.Namespace) -> int:
    image_path = Path(args.image)
    result = extract_position(load_image(image_path))
    _emit_warnings(result.warnings)
    position = result.position

    svg_path: Path = args.output or _default_output(image_path, ".svg")
    json_path: Path = args.json_path or _default_output(image_path, ".json")

    _check_no_input_collision(image_path, [svg_path, json_path, args.sgf, args.preview])

    new_json_text = position.to_json()
    _check_sidecar_writable(json_path, new_json_text, force=args.force)

    svg_kwargs: dict[str, object] = {"coords": args.coords}
    if args.cell is not None:
        svg_kwargs["cell"] = args.cell
    # SVG first, sidecar second: if render_svg (or the write itself) fails,
    # nothing has touched the sidecar yet -- no half-finished output set (C3).
    svg_path.write_text(render_svg(position, **svg_kwargs), encoding="utf-8")
    json_path.write_text(new_json_text, encoding="utf-8")

    if args.sgf is not None:
        args.sgf.write_text(position_to_sgf(position), encoding="utf-8")
    if args.preview is not None:
        _write_preview(position, args.preview, coords=args.coords, cell=args.cell)
    if args.ascii:
        print(ascii_diagram(position))

    print(_summary_line(position, svg_path))
    return 0


def _cmd_extract(args: argparse.Namespace) -> int:
    image_path = Path(args.image)
    result = extract_position(load_image(image_path))
    _emit_warnings(result.warnings)
    position = result.position

    json_path: Path = args.output or _default_output(image_path, ".json")

    _check_no_input_collision(image_path, [json_path, args.sgf])

    json_path.write_text(position.to_json(), encoding="utf-8")

    if args.sgf is not None:
        args.sgf.write_text(position_to_sgf(position), encoding="utf-8")
    if args.ascii:
        print(ascii_diagram(position))

    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    position_path = Path(args.position)
    # utf-8-sig: strips a leading UTF-8 BOM before the JSON-vs-SGF sniff runs,
    # so a BOM'd file of either format loads instead of misrouting into the
    # wrong parser (C4). A non-BOM file decodes identically either way.
    position = _parse_position(position_path.read_text(encoding="utf-8-sig"))

    svg_path: Path = args.output or _default_output(position_path, ".svg")

    _check_no_input_collision(position_path, [svg_path, args.preview])

    svg_kwargs: dict[str, object] = {"coords": args.coords}
    if args.cell is not None:
        svg_kwargs["cell"] = args.cell
    svg_path.write_text(render_svg(position, **svg_kwargs), encoding="utf-8")

    if args.preview is not None:
        _write_preview(position, args.preview, coords=args.coords, cell=args.cell)
    if args.ascii:
        print(ascii_diagram(position))

    return 0


# --------------------------------------------------------------------------- #
# argparse wiring
# --------------------------------------------------------------------------- #


def _cell_type(value: str) -> float:
    """argparse ``type=`` for ``--cell``: a finite float with 0 < cell <= 1000 (C1).

    A bare ``type=float`` let every one of these through to break something
    downstream: 0 wrote a degenerate (zero-area) SVG at exit 0, a negative
    value wrote invalid SVG, 'nan' wrote a literal ``width="nan"`` attribute,
    and 'inf' (or a large negative) fed to ``--preview``'s integer raster path
    surfaced as an OverflowError or struct.error traceback instead of a clean
    error. Rejecting all of that here means every bad value is caught by
    argparse itself, before any subcommand runs.
    """
    try:
        cell = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a valid number: {value!r}") from exc
    if not math.isfinite(cell) or not (0 < cell <= 1000):
        raise argparse.ArgumentTypeError(f"must be a finite number with 0 < cell <= 1000, got {value!r}")
    return cell


def _corner_type(value: str) -> tuple[float, float]:
    """Parse one 'X,Y' corner; argparse-friendly errors for anything else."""
    parts = value.split(",")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(f"corner {value!r} must be 'X,Y' (e.g. 132.5,88)")
    try:
        x, y = float(parts[0]), float(parts[1])
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"corner {value!r} has non-numeric coordinates") from exc
    if not (math.isfinite(x) and math.isfinite(y)) or x < 0 or y < 0:
        raise argparse.ArgumentTypeError(f"corner {value!r} must be finite, non-negative pixel coordinates")
    return (x, y)


def _cmd_photo(args: argparse.Namespace) -> int:
    image_path = Path(args.image)
    print(
        "notice: photo mode is EXPERIMENTAL (uncalibrated against real photos) -- verify the result by hand",
        file=sys.stderr,
    )
    result = extract_photo_position(load_image(image_path), args.corners, args.size)
    _emit_warnings(result.warnings)
    position = result.position

    svg_path: Path = args.output or _default_output(image_path, ".svg")
    json_path: Path = args.json_path or _default_output(image_path, ".json")

    _check_no_input_collision(image_path, [svg_path, json_path, args.sgf, args.preview])

    new_json_text = position.to_json()
    _check_sidecar_writable(json_path, new_json_text, force=args.force)

    svg_kwargs: dict[str, object] = {"coords": args.coords}
    if args.cell is not None:
        svg_kwargs["cell"] = args.cell
    svg_path.write_text(render_svg(position, **svg_kwargs), encoding="utf-8")
    json_path.write_text(new_json_text, encoding="utf-8")

    if args.sgf is not None:
        args.sgf.write_text(position_to_sgf(position), encoding="utf-8")
    if args.preview is not None:
        _write_preview(position, args.preview, coords=args.coords, cell=args.cell)
    if args.ascii:
        print(ascii_diagram(position))

    print(_summary_line(position, svg_path))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="goban-svg",
        description=(
            "Convert images of Go board positions into clean SVG diagrams: automatic "
            "extraction for app screenshots (convert/extract), assisted extraction for "
            "photos of physical boards (photo, experimental), and re-rendering of saved "
            "positions (render)."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    convert = subparsers.add_parser(
        "convert", help="Extract a position from a screenshot and render it as SVG in one step."
    )
    convert.add_argument("image", metavar="IMAGE", help="input screenshot")
    convert.add_argument(
        "-o", "--output", metavar="OUT.svg", type=Path, help="output SVG path (default: IMAGE with .svg extension)"
    )
    convert.add_argument(
        "--json",
        dest="json_path",
        metavar="PATH",
        type=Path,
        help="JSON sidecar path (default: IMAGE with .json extension)",
    )
    convert.add_argument("--sgf", metavar="PATH", type=Path, help="also write an SGF export to this path")
    convert.add_argument("--coords", action="store_true", help="draw coordinate letters/numbers")
    convert.add_argument("--cell", metavar="N", type=_cell_type, help="grid cell size in output units (0 < N <= 1000)")
    convert.add_argument("--ascii", action="store_true", help="print an ASCII diagram of the extracted position")
    convert.add_argument("--preview", metavar="OUT.png", type=Path, help="also write an app-style PNG preview")
    convert.add_argument(
        "--force",
        action="store_true",
        help="overwrite a JSON sidecar even if its existing content has hand-edits this run would discard",
    )
    convert.set_defaults(func=_cmd_convert)

    extract = subparsers.add_parser("extract", help="Extract a position from a screenshot to JSON.")
    extract.add_argument("image", metavar="IMAGE", help="input screenshot")
    extract.add_argument(
        "-o", "--output", metavar="OUT.json", type=Path, help="output JSON path (default: IMAGE with .json extension)"
    )
    extract.add_argument("--sgf", metavar="PATH", type=Path, help="also write an SGF export to this path")
    extract.add_argument("--ascii", action="store_true", help="print an ASCII diagram of the extracted position")
    extract.set_defaults(func=_cmd_extract)

    photo = subparsers.add_parser(
        "photo",
        help="EXPERIMENTAL: extract a position from a photo of a physical board (assisted; stones only).",
        description=(
            "Assisted extraction for photos of real boards: you supply the four corner "
            "INTERSECTIONS of the grid (not the wooden edge) in TL TR BR BL order, plus the "
            "board size. Stones only -- photos carry no move numbers or marks. EXPERIMENTAL: "
            "the classifier is not yet calibrated on real photos; always verify the output. "
            "JPEG/WebP inputs require the optional Pillow extra (goban-svg[images]); HEIC is "
            "not supported -- convert it to JPEG first."
        ),
    )
    photo.add_argument("image", metavar="IMAGE", help="input photo")
    photo.add_argument(
        "--corners",
        metavar="X,Y",
        type=_corner_type,
        nargs=4,
        required=True,
        help="the four outer grid-line intersections, in TL TR BR BL order (source-image pixels)",
    )
    photo.add_argument("--size", type=int, default=19, help="board size, 2-25 (default: 19)")
    photo.add_argument(
        "-o", "--output", metavar="OUT.svg", type=Path, help="output SVG path (default: IMAGE with .svg extension)"
    )
    photo.add_argument(
        "--json",
        dest="json_path",
        metavar="PATH",
        type=Path,
        help="JSON sidecar path (default: IMAGE with .json extension)",
    )
    photo.add_argument("--sgf", metavar="PATH", type=Path, help="also write an SGF export to this path")
    photo.add_argument("--coords", action="store_true", help="draw coordinate letters/numbers")
    photo.add_argument("--cell", metavar="N", type=_cell_type, help="grid cell size in output units (0 < N <= 1000)")
    photo.add_argument("--ascii", action="store_true", help="print an ASCII diagram of the extracted position")
    photo.add_argument("--preview", metavar="OUT.png", type=Path, help="also write an app-style PNG preview")
    photo.add_argument(
        "--force",
        action="store_true",
        help="overwrite a JSON sidecar even if its existing content has hand-edits this run would discard",
    )
    photo.set_defaults(func=_cmd_photo)

    render = subparsers.add_parser("render", help="Render a saved Position (JSON or SGF) as SVG.")
    render.add_argument("position", metavar="POS.json|POS.sgf", help="input position file (JSON or SGF, sniffed)")
    render.add_argument(
        "-o",
        "--output",
        metavar="OUT.svg",
        type=Path,
        help="output SVG path (default: POSITION with .svg extension)",
    )
    render.add_argument("--coords", action="store_true", help="draw coordinate letters/numbers")
    render.add_argument("--cell", metavar="N", type=_cell_type, help="grid cell size in output units (0 < N <= 1000)")
    render.add_argument("--ascii", action="store_true", help="print an ASCII diagram of the position")
    render.add_argument("--preview", metavar="OUT.png", type=Path, help="also write an app-style PNG preview")
    render.set_defaults(func=_cmd_render)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the goban-svg CLI; returns a process exit code (0 = success)."""
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse itself calls sys.exit() on a bad invocation (e.g. a missing
        # required argument); turn that into a plain return so callers driving
        # main() in-process (tests, __main__.py) get an exit code, not a raise.
        return exc.code if isinstance(exc.code, int) else 1

    try:
        return args.func(args)
    except _USER_ERRORS as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
