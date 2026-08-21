"""Tests for cli.py -- the `goban-svg convert / extract / render` subcommands.

`main(argv)` is exercised in-process (never via subprocess) with explicit argv
lists and pytest's `capsys`, per design.md sec 8 / docs/interfaces.md. Two
input styles feed the `convert`/`extract` tests: a screenshot is synthesized
with `render.render_png` + `png_codec.write_png` (the same fixture-generation
trick `test_extract.py` uses -- no external image files needed), so these
tests double as a light end-to-end smoke test of the whole pipeline through
the CLI's own file I/O and argument wiring, not a re-test of extraction
accuracy (that belongs to test_extract.py).
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

import pytest

from goban_svg.board import Mark, Point, Position, ascii_diagram
from goban_svg.cli import main
from goban_svg.png_codec import read_png, write_png
from goban_svg.render import render_png
from goban_svg.sgf import position_to_sgf

SVG_NS = "{http://www.w3.org/2000/svg}"


def _root(svg_text: str) -> ET.Element:
    return ET.fromstring(svg_text)


def _fixture_position() -> Position:
    """A small 9x9 position exercising stones + both mark kinds + a label,
    at points already proven to round-trip through render_png/extract_position
    in test_extract.py's richer fixtures (a stone AND a wedge on a hoshi point,
    a bare square marker on a different hoshi point)."""
    pos = Position(size=9)
    pos.stones[Point(3, 3)] = "black"
    pos.stones[Point(7, 3)] = "white"
    pos.stones[Point(5, 5)] = "black"
    pos.marks[Point(5, 5)] = Mark("triangle", "white")
    pos.marks[Point(3, 7)] = Mark("square", "black")
    pos.labels[Point(5, 5)] = "3"
    return pos


def _write_fixture_image(path) -> Position:
    """Paint `_fixture_position()` as an app-style PNG at `path`; return the position."""
    pos = _fixture_position()
    path.write_bytes(write_png(render_png(pos, cell=32)))
    return pos


# --------------------------------------------------------------------------- #
# render: JSON and SGF input, sniffed
# --------------------------------------------------------------------------- #


def test_render_json_input_produces_a_parseable_svg(tmp_path) -> None:
    pos = _fixture_position()
    json_path = tmp_path / "pos.json"
    json_path.write_text(pos.to_json(), encoding="utf-8")
    svg_path = tmp_path / "out.svg"

    code = main(["render", str(json_path), "-o", str(svg_path)])

    assert code == 0
    assert svg_path.exists()
    root = _root(svg_path.read_text(encoding="utf-8"))
    assert root.tag == f"{SVG_NS}svg"


def test_render_sgf_input_is_sniffed_by_leading_paren(tmp_path) -> None:
    pos = _fixture_position()
    sgf_path = tmp_path / "pos.sgf"
    # position_to_sgf's output already starts with "(" -- prepend whitespace to
    # exercise "leading '(' after whitespace" from design.md sec 8, not just
    # a bare leading paren.
    sgf_path.write_text("  \n" + position_to_sgf(pos), encoding="utf-8")

    code = main(["render", str(sgf_path)])

    assert code == 0
    default_svg = sgf_path.with_suffix(".svg")
    assert default_svg.exists(), "default output swaps the input's extension for .svg"
    root = _root(default_svg.read_text(encoding="utf-8"))
    assert root.tag == f"{SVG_NS}svg"
    # Mark colors are lossy through SGF (design.md G7): stone count survives.
    circles = list(root.iter(f"{SVG_NS}circle"))
    stone_radii = {c.get("r") for c in circles}
    assert len(stone_radii) >= 1


def test_render_coords_and_cell_flags_are_forwarded(tmp_path) -> None:
    pos = Position(size=9)
    json_path = tmp_path / "pos.json"
    json_path.write_text(pos.to_json(), encoding="utf-8")
    plain_svg, coords_svg = tmp_path / "plain.svg", tmp_path / "coords.svg"

    assert main(["render", str(json_path), "-o", str(plain_svg), "--cell", "20"]) == 0
    assert main(["render", str(json_path), "-o", str(coords_svg), "--cell", "20", "--coords"]) == 0

    plain_w = float(_root(plain_svg.read_text()).get("viewBox", "").split()[2])
    coords_w = float(_root(coords_svg.read_text()).get("viewBox", "").split()[2])
    assert coords_w > plain_w, "--coords must widen the canvas for the coordinate gutter"
    assert _root(coords_svg.read_text()).iter(f"{SVG_NS}text") is not None


def test_render_preview_writes_a_valid_png(tmp_path) -> None:
    pos = _fixture_position()
    json_path = tmp_path / "pos.json"
    json_path.write_text(pos.to_json(), encoding="utf-8")
    svg_path, preview_path = tmp_path / "out.svg", tmp_path / "preview.png"

    code = main(["render", str(json_path), "-o", str(svg_path), "--preview", str(preview_path)])

    assert code == 0
    assert preview_path.exists()
    img = read_png(preview_path.read_bytes())
    assert img.width > 0 and img.height > 0


def test_render_ascii_prints_the_board_diagram(tmp_path, capsys) -> None:
    pos = _fixture_position()
    json_path = tmp_path / "pos.json"
    json_path.write_text(pos.to_json(), encoding="utf-8")
    svg_path = tmp_path / "out.svg"

    code = main(["render", str(json_path), "-o", str(svg_path), "--ascii"])

    assert code == 0
    out = capsys.readouterr().out
    assert out == ascii_diagram(pos) + "\n"
    assert "X" in out and "O" in out  # black / white glyphs, per board.ascii_diagram


# --------------------------------------------------------------------------- #
# convert: extract + render in one step
# --------------------------------------------------------------------------- #


def test_convert_writes_svg_and_json_sidecar_with_defaults(tmp_path, capsys) -> None:
    image_path = tmp_path / "board.png"
    original = _write_fixture_image(image_path)

    code = main(["convert", str(image_path)])

    assert code == 0
    svg_path = tmp_path / "board.svg"
    json_path = tmp_path / "board.json"
    assert svg_path.exists(), "convert's default SVG output swaps IMAGE's extension for .svg"
    assert json_path.exists(), "convert always writes a JSON sidecar alongside the SVG"

    root = _root(svg_path.read_text(encoding="utf-8"))
    assert root.tag == f"{SVG_NS}svg"

    extracted = Position.from_json(json_path.read_text(encoding="utf-8"))
    assert extracted.size == original.size
    assert extracted.stones == original.stones

    out = capsys.readouterr().out.strip()
    black = sum(1 for c in extracted.stones.values() if c == "black")
    white = sum(1 for c in extracted.stones.values() if c == "white")
    marks, labels = len(extracted.marks), len(extracted.labels)
    mark_word = "mark" if marks == 1 else "marks"
    label_word = "label" if labels == 1 else "labels"
    expected = f"{extracted.size}×{extracted.size}, {black} black, {white} white, {marks} {mark_word}, {labels} {label_word} → {svg_path}"
    assert out == expected


def test_convert_honors_explicit_output_json_and_sgf_paths(tmp_path) -> None:
    image_path = tmp_path / "shot.png"
    _write_fixture_image(image_path)
    svg_path = tmp_path / "out" / "diagram.svg"
    svg_path.parent.mkdir()
    json_path = tmp_path / "sidecar.json"
    sgf_path = tmp_path / "export.sgf"

    code = main(
        [
            "convert",
            str(image_path),
            "-o",
            str(svg_path),
            "--json",
            str(json_path),
            "--sgf",
            str(sgf_path),
        ]
    )

    assert code == 0
    assert svg_path.exists()
    assert json_path.exists()
    assert sgf_path.exists()
    assert sgf_path.read_text(encoding="utf-8").startswith("(;")
    # The default board.svg/board.json next to the image must NOT have been written.
    assert not (tmp_path / "shot.svg").exists()
    assert not (tmp_path / "shot.json").exists()


def test_convert_warnings_go_to_stderr(tmp_path) -> None:
    # A blank board still extracts cleanly (no warnings expected), but this
    # confirms warnings -- when there are any -- land on stderr, not stdout,
    # by checking stdout stays exactly the one summary line.
    image_path = tmp_path / "blank.png"
    image_path.write_bytes(write_png(render_png(Position(size=9), cell=32)))

    code = main(["convert", str(image_path)])
    assert code == 0


# --------------------------------------------------------------------------- #
# extract: screenshot -> JSON (+ optional SGF)
# --------------------------------------------------------------------------- #


def test_extract_writes_json_with_default_name(tmp_path) -> None:
    image_path = tmp_path / "shot.png"
    original = _write_fixture_image(image_path)

    code = main(["extract", str(image_path)])

    assert code == 0
    json_path = tmp_path / "shot.json"
    assert json_path.exists()
    extracted = Position.from_json(json_path.read_text(encoding="utf-8"))
    assert extracted.stones == original.stones
    assert extracted.size == original.size


def test_extract_ascii_flag_prints_diagram(tmp_path, capsys) -> None:
    image_path = tmp_path / "shot.png"
    _write_fixture_image(image_path)

    code = main(["extract", str(image_path), "--ascii"])

    assert code == 0
    out = capsys.readouterr().out
    assert "X" in out and "O" in out


# --------------------------------------------------------------------------- #
# Error handling: bad inputs exit nonzero with a clean stderr message
# --------------------------------------------------------------------------- #


def test_convert_missing_image_file_is_a_clean_error(tmp_path, capsys) -> None:
    missing = tmp_path / "does-not-exist.png"

    code = main(["convert", str(missing)])

    assert code != 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error:" in captured.err.lower()


def test_render_missing_position_file_is_a_clean_error(tmp_path, capsys) -> None:
    missing = tmp_path / "nope.json"

    code = main(["render", str(missing)])

    assert code != 0
    assert "error:" in capsys.readouterr().err.lower()


def test_render_rejects_move_sgf_with_a_clear_message(tmp_path, capsys) -> None:
    sgf_path = tmp_path / "game.sgf"
    sgf_path.write_text("(;GM[1]FF[4]SZ[19];B[pd])", encoding="utf-8")

    code = main(["render", str(sgf_path)])

    assert code != 0
    err = capsys.readouterr().err
    assert "error:" in err.lower()
    assert "static positions" in err.lower()


def test_render_malformed_json_is_a_clean_error_not_a_traceback(tmp_path, capsys) -> None:
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{not json", encoding="utf-8")

    code = main(["render", str(bad_json)])

    assert code != 0
    assert "error:" in capsys.readouterr().err.lower()


def test_render_json_missing_size_key_is_a_clean_error(tmp_path, capsys) -> None:
    incomplete = tmp_path / "incomplete.json"
    incomplete.write_text('{"stones": {"black": [], "white": []}}', encoding="utf-8")

    code = main(["render", str(incomplete)])

    assert code != 0
    assert "error:" in capsys.readouterr().err.lower()


# --------------------------------------------------------------------------- #
# C1: --cell must be a finite, positive, bounded float (argparse-level, exit 2)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad_cell", ["0", "-5", "nan", "inf", "-inf", "1e12"])
def test_render_rejects_invalid_cell_values_with_exit_2_and_no_traceback(tmp_path, capsys, bad_cell) -> None:
    pos = _fixture_position()
    json_path = tmp_path / "pos.json"
    json_path.write_text(pos.to_json(), encoding="utf-8")

    code = main(["render", str(json_path), "--cell", bad_cell])

    assert code == 2
    err = capsys.readouterr().err
    assert "traceback" not in err.lower()
    assert "--cell" in err
    assert not (tmp_path / "pos.svg").exists(), "no SVG should be written for a rejected --cell"


@pytest.mark.parametrize("bad_cell", ["0", "-5", "nan", "inf", "1e12"])
def test_convert_rejects_invalid_cell_values_with_exit_2_and_no_traceback(tmp_path, capsys, bad_cell) -> None:
    image_path = tmp_path / "board.png"
    _write_fixture_image(image_path)

    code = main(["convert", str(image_path), "--cell", bad_cell])

    assert code == 2
    err = capsys.readouterr().err
    assert "traceback" not in err.lower()
    assert "--cell" in err
    assert not (tmp_path / "board.svg").exists()
    assert not (tmp_path / "board.json").exists()


def test_render_accepts_boundary_cell_values(tmp_path) -> None:
    pos = _fixture_position()
    json_path = tmp_path / "pos.json"
    json_path.write_text(pos.to_json(), encoding="utf-8")

    for cell in ("1000", "0.001"):
        svg_path = tmp_path / f"cell-{cell}.svg"
        assert main(["render", str(json_path), "-o", str(svg_path), "--cell", cell]) == 0
        assert svg_path.exists()


# --------------------------------------------------------------------------- #
# C2: convert must never let an output path resolve to the input file
# --------------------------------------------------------------------------- #


def test_convert_default_json_sidecar_colliding_with_input_is_a_clean_error(tmp_path, capsys) -> None:
    # The screenshot happens to be misnamed with a .json extension, so
    # convert's default JSON-sidecar path (IMAGE with its suffix swapped for
    # .json) lands right back on the input file itself.
    image_path = tmp_path / "mis.json"
    _write_fixture_image(image_path)
    original_bytes = image_path.read_bytes()

    code = main(["convert", str(image_path)])

    assert code != 0
    captured = capsys.readouterr()
    assert captured.out == ""
    err = captured.err.lower()
    assert "error:" in err
    assert "would overwrite the input file" in err
    assert image_path.read_bytes() == original_bytes, "the input image must survive untouched"
    assert not (tmp_path / "mis.svg").exists(), "nothing should be written once a collision is detected"


def test_convert_explicit_output_colliding_with_input_is_a_clean_error(tmp_path, capsys) -> None:
    image_path = tmp_path / "a.png"
    _write_fixture_image(image_path)
    original_bytes = image_path.read_bytes()

    code = main(["convert", str(image_path), "-o", str(image_path)])

    assert code != 0
    err = capsys.readouterr().err.lower()
    assert "error:" in err
    assert "would overwrite the input file" in err
    assert image_path.read_bytes() == original_bytes


def test_convert_explicit_json_sidecar_colliding_with_input_is_a_clean_error(tmp_path, capsys) -> None:
    image_path = tmp_path / "a.png"
    _write_fixture_image(image_path)
    original_bytes = image_path.read_bytes()

    code = main(["convert", str(image_path), "--json", str(image_path)])

    assert code != 0
    err = capsys.readouterr().err.lower()
    assert "error:" in err
    assert "would overwrite the input file" in err
    assert image_path.read_bytes() == original_bytes
    assert not (tmp_path / "a.svg").exists(), "nothing should be written once a collision is detected"


def test_render_output_colliding_with_input_is_a_clean_error(tmp_path, capsys) -> None:
    pos = _fixture_position()
    json_path = tmp_path / "pos.json"
    json_path.write_text(pos.to_json(), encoding="utf-8")
    original_text = json_path.read_text(encoding="utf-8")

    code = main(["render", str(json_path), "-o", str(json_path)])

    assert code != 0
    err = capsys.readouterr().err.lower()
    assert "error:" in err
    assert "would overwrite the input file" in err
    assert json_path.read_text(encoding="utf-8") == original_text


# --------------------------------------------------------------------------- #
# C3: convert must not silently clobber hand-edits in the JSON sidecar
# --------------------------------------------------------------------------- #


def test_convert_refuses_to_clobber_a_hand_edited_sidecar_then_force_then_idempotent(tmp_path, capsys) -> None:
    image_path = tmp_path / "board.png"
    _write_fixture_image(image_path)

    assert main(["convert", str(image_path)]) == 0
    svg_path = tmp_path / "board.svg"
    json_path = tmp_path / "board.json"
    svg_before = svg_path.read_bytes()
    fresh_json = json_path.read_text(encoding="utf-8")

    # Simulate a hand-edit to the sidecar: the correction-loop artifact this
    # protection exists for. The fixture's only label is "3" -- retarget it.
    assert '"3"' in fresh_json
    edited = fresh_json.replace('"3"', '"7"')
    json_path.write_text(edited, encoding="utf-8")

    # Re-running convert (no --force) must refuse, leaving both files intact.
    code = main(["convert", str(image_path)])
    assert code != 0
    captured = capsys.readouterr()
    err_lower = captured.err.lower()
    assert "error:" in err_lower
    assert "preserved" in err_lower
    assert "--force" in captured.err
    assert f"render {json_path}" in captured.err
    assert json_path.read_text(encoding="utf-8") == edited, "hand-edit must survive a refused convert"
    assert svg_path.read_bytes() == svg_before, "SVG must not change either when the sidecar write is refused"

    # --force overwrites the hand-edit with the fresh extraction.
    code = main(["convert", str(image_path), "--force"])
    assert code == 0
    forced_json = json_path.read_text(encoding="utf-8")
    assert forced_json == fresh_json
    assert forced_json != edited

    # Immediately re-running (content now matches what this run would write)
    # is idempotent and needs no --force.
    code = main(["convert", str(image_path)])
    assert code == 0
    assert json_path.read_text(encoding="utf-8") == fresh_json


def test_convert_idempotent_rerun_with_untouched_sidecar_needs_no_force(tmp_path) -> None:
    image_path = tmp_path / "board.png"
    _write_fixture_image(image_path)

    assert main(["convert", str(image_path)]) == 0
    json_path = tmp_path / "board.json"
    first_run = json_path.read_text(encoding="utf-8")

    # Same image, same extraction, same sidecar content -> proceeds silently.
    assert main(["convert", str(image_path)]) == 0
    assert json_path.read_text(encoding="utf-8") == first_run


# --------------------------------------------------------------------------- #
# C4: a UTF-8 BOM must not misroute render's JSON-vs-SGF sniff
# --------------------------------------------------------------------------- #


def test_render_bom_prefixed_sgf_input_renders_successfully(tmp_path) -> None:
    pos = _fixture_position()
    sgf_path = tmp_path / "bom.sgf"
    sgf_path.write_text("\ufeff" + position_to_sgf(pos), encoding="utf-8")
    assert sgf_path.read_bytes().startswith(b"\xef\xbb\xbf"), "fixture must actually carry a UTF-8 BOM"

    code = main(["render", str(sgf_path)])

    assert code == 0
    default_svg = sgf_path.with_suffix(".svg")
    assert default_svg.exists()
    root = _root(default_svg.read_text(encoding="utf-8"))
    assert root.tag == f"{SVG_NS}svg"


def test_render_bom_prefixed_json_input_renders_successfully(tmp_path) -> None:
    pos = _fixture_position()
    json_path = tmp_path / "bom.json"
    json_path.write_text("\ufeff" + pos.to_json(), encoding="utf-8")
    assert json_path.read_bytes().startswith(b"\xef\xbb\xbf"), "fixture must actually carry a UTF-8 BOM"

    code = main(["render", str(json_path)])

    assert code == 0
    default_svg = json_path.with_suffix(".svg")
    assert default_svg.exists()
    root = _root(default_svg.read_text(encoding="utf-8"))
    assert root.tag == f"{SVG_NS}svg"


# --------------------------------------------------------------------------- #
# photo subcommand (photo code review M9)
# --------------------------------------------------------------------------- #


def _photo_fixture(tmp_path):
    """A flat painted 9x9 board saved as a 'photo', with its outer-intersection
    corners (axis-aligned is a valid quad; keeps CLI tests fast)."""
    from goban_svg.board import Point, Position
    from goban_svg.png_codec import write_png
    from goban_svg.render import render_png

    pos = Position(size=9, stones={Point(3, 3): "black", Point(7, 7): "white"})
    cell = 24
    img = render_png(pos, cell=cell)
    margin = int(round(0.72 * cell))
    frame = max(1, int(round(1.0 * cell)))
    lo = frame + margin
    hi = lo + 8 * cell
    path = tmp_path / "shot.png"
    path.write_bytes(write_png(img))
    corners = [f"{lo},{lo}", f"{hi},{lo}", f"{hi},{hi}", f"{lo},{hi}"]
    return path, corners, pos


def test_photo_happy_path_emits_notice_and_outputs(tmp_path, capsys):
    path, corners, pos = _photo_fixture(tmp_path)
    out_svg = tmp_path / "shot.svg"
    rc = main(["photo", str(path), "--corners", *corners, "--size", "9"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "notice:" in captured.err and "EXPERIMENTAL" in captured.err
    assert out_svg.exists()
    sidecar = json.loads((tmp_path / "shot.json").read_text(encoding="utf-8"))
    assert sidecar["stones"]["black"] == ["C3"]
    assert sidecar["stones"]["white"] == ["G7"]


def test_photo_bad_corner_syntax_exits_2(tmp_path, capsys):
    path, corners, _ = _photo_fixture(tmp_path)
    rc = main(["photo", str(path), "--corners", "1;2", corners[1], corners[2], corners[3], "--size", "9"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "X,Y" in captured.err


def test_photo_output_aliasing_rejected(tmp_path, capsys):
    path, corners, _ = _photo_fixture(tmp_path)
    same = tmp_path / "same.out"
    rc = main(["photo", str(path), "--corners", *corners, "--size", "9", "-o", str(same), "--json", str(same)])
    captured = capsys.readouterr()
    assert rc == 1
    assert "must be distinct" in captured.err


def test_photo_sidecar_protection_and_force(tmp_path, capsys):
    path, corners, _ = _photo_fixture(tmp_path)
    assert main(["photo", str(path), "--corners", *corners, "--size", "9"]) == 0
    sidecar = tmp_path / "shot.json"
    sidecar.write_text(sidecar.read_text(encoding="utf-8").replace("C3", "D4"), encoding="utf-8")
    rc = main(["photo", str(path), "--corners", *corners, "--size", "9"])
    captured = capsys.readouterr()
    assert rc == 1 and "preserved" in captured.err
    assert main(["photo", str(path), "--corners", *corners, "--size", "9", "--force"]) == 0


def test_photo_no_refine_flag_forwards(monkeypatch, tmp_path):
    # r2 m7: --no-refine must wire through as refine=False (and default True).
    import goban_svg.cli as cli_mod
    from goban_svg.board import Position
    from goban_svg.extract import ExtractionResult, GridFit

    path, corners, _ = _photo_fixture(tmp_path)
    seen = []

    def fake(img, corner_list, size, *, refine=True):
        seen.append(refine)
        grid = GridFit(xs=[0.0], ys=[0.0], spacing=1.0, bbox=(0, 0, 1, 1))
        return ExtractionResult(position=Position(size=size), grid=grid, warnings=[])

    monkeypatch.setattr(cli_mod, "extract_photo_position", fake)
    assert main(["photo", str(path), "--corners", *corners, "--size", "9", "--force"]) == 0
    assert main(["photo", str(path), "--corners", *corners, "--size", "9", "--no-refine", "--force"]) == 0
    assert seen == [True, False]
