"""Tests for goban_svg.png_codec.

Per design.md §9 ("png_codec"): encode->decode round trip; hand-built PNG
streams exercising filters 1-4 (the FORWARD filter is applied here, in the
test, and decode is asserted to restore the original raw bytes); grayscale /
palette / RGBA / 16-bit decode; interlaced -> PngError. Every PNG here is
assembled by hand with ``struct`` + ``zlib`` -- no external fixture files,
and no Pillow dependency (the Pillow-fallback tests are skipped when Pillow
happens to be installed, since their point is to exercise its *absence*).
"""

from __future__ import annotations

import importlib.util
import struct
import zlib

import pytest

from goban_svg.png_codec import Image, PngError, load_image, read_png, write_png

_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_HAS_PIL = importlib.util.find_spec("PIL") is not None


# --------------------------------------------------------------------------
# Hand-built PNG assembly helpers (test-only; independent of png_codec's own
# chunk/filter code, so a round trip through read_png is a real check against
# the PNG spec rather than the module testing its own arithmetic).
# --------------------------------------------------------------------------


def _pattern_bytes(n: int, seed: int) -> bytes:
    """Deterministic pseudo-random-looking byte sequence, no `random` needed."""
    return bytes((seed + 7 * i * i + 13 * i) % 256 for i in range(n))


def _paeth_ref(a: int, b: int, c: int) -> int:
    """Reference Paeth predictor (PNG spec §9.4). Ties resolve a, then b,
    then c -- independently reimplemented here (not imported from
    png_codec) so tests that engineer a tie actually check the production
    module's tie-break, not just agreement with itself."""
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _forward_filter(raw: bytes, width: int, height: int, bpp: int, filter_types: list[int]) -> bytes:
    """Reference (independent) PNG scanline encoder: apply ``filter_types[y]``
    to row y of `raw` (None/Sub/Up/Average/Paeth) and return the
    filter-type-byte + filtered-bytes stream a real encoder would produce.
    """
    row_bytes = width * bpp
    assert len(raw) == row_bytes * height
    out = bytearray()
    prev = bytearray(row_bytes)
    for y in range(height):
        row = bytearray(raw[y * row_bytes : (y + 1) * row_bytes])
        ftype = filter_types[y]
        filt = bytearray(row_bytes)
        for x in range(row_bytes):
            a = row[x - bpp] if x >= bpp else 0
            b = prev[x]
            c = prev[x - bpp] if x >= bpp else 0
            if ftype == 0:
                pred = 0
            elif ftype == 1:
                pred = a
            elif ftype == 2:
                pred = b
            elif ftype == 3:
                pred = (a + b) >> 1
            elif ftype == 4:
                pred = _paeth_ref(a, b, c)
            else:  # pragma: no cover - test bug, not a decoder path
                raise ValueError(f"bad filter type {ftype}")
            filt[x] = (row[x] - pred) & 0xFF
        out.append(ftype)
        out += filt
        prev = row
    return bytes(out)


def _chunk(ctype: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + ctype + data + struct.pack(">I", zlib.crc32(ctype + data))


def _build_png(
    width: int,
    height: int,
    bit_depth: int,
    color_type: int,
    scanlines: bytes,
    *,
    palette: bytes | None = None,
    interlace: int = 0,
    idat_chunks: int = 1,
) -> bytes:
    """Assemble a complete PNG file byte-for-byte from its pieces."""
    ihdr = struct.pack(">IIBBBBB", width, height, bit_depth, color_type, 0, 0, interlace)
    parts = [_SIGNATURE, _chunk(b"IHDR", ihdr)]
    if palette is not None:
        parts.append(_chunk(b"PLTE", palette))
    compressed = zlib.compress(scanlines, 6)
    if idat_chunks <= 1:
        parts.append(_chunk(b"IDAT", compressed))
    else:
        step = max(1, len(compressed) // idat_chunks)
        pos = 0
        while pos < len(compressed):
            parts.append(_chunk(b"IDAT", compressed[pos : pos + step]))
            pos += step
    parts.append(_chunk(b"IEND", b""))
    return b"".join(parts)


# --------------------------------------------------------------------------
# Image
# --------------------------------------------------------------------------


def test_image_new_get_set_fill():
    img = Image.new(3, 2, fill=(1, 2, 3))
    assert (img.width, img.height) == (3, 2)
    assert len(img.pixels) == 3 * 2 * 3
    assert img.get(0, 0) == (1, 2, 3)
    assert img.get(2, 1) == (1, 2, 3)

    img.set(2, 1, (9, 8, 7))
    assert img.get(2, 1) == (9, 8, 7)
    assert img.get(0, 0) == (1, 2, 3)  # unaffected

    img.fill((0, 0, 0))
    assert img.get(2, 1) == (0, 0, 0)
    assert img.get(1, 0) == (0, 0, 0)


def test_image_get_set_out_of_bounds_raises_index_error():
    img = Image.new(2, 2)
    with pytest.raises(IndexError):
        img.get(2, 0)
    with pytest.raises(IndexError):
        img.set(-1, 0, (0, 0, 0))


# --------------------------------------------------------------------------
# write_png / read_png round trip (the core test)
# --------------------------------------------------------------------------


def test_write_png_read_png_round_trip():
    width, height = 6, 5
    img = Image(width, height, bytearray(_pattern_bytes(width * height * 3, seed=3)))
    encoded = write_png(img)
    assert encoded[:8] == _SIGNATURE

    decoded = read_png(encoded)
    assert (decoded.width, decoded.height) == (width, height)
    assert bytes(decoded.pixels) == bytes(img.pixels)


# --------------------------------------------------------------------------
# Filters 1-4, hand-built: apply the forward filter here, assert decode
# restores the original raw bytes.
# --------------------------------------------------------------------------


def test_filter1_sub_round_trip():
    width, height, bpp = 5, 3, 3
    raw = _pattern_bytes(width * height * bpp, seed=11)
    scanlines = _forward_filter(raw, width, height, bpp, [1] * height)
    png = _build_png(width, height, 8, 2, scanlines)
    img = read_png(png)
    assert bytes(img.pixels) == raw


def test_filter2_up_round_trip():
    width, height, bpp = 4, 4, 3
    raw = _pattern_bytes(width * height * bpp, seed=23)
    # row 0 must be something other than Up (row -1 is implicitly zero, so
    # Up on row 0 is a legitimate but less interesting case); use None for
    # row 0 and Up for the rest so later rows genuinely reference a prior
    # reconstructed row.
    filter_types = [0] + [2] * (height - 1)
    scanlines = _forward_filter(raw, width, height, bpp, filter_types)
    png = _build_png(width, height, 8, 2, scanlines)
    img = read_png(png)
    assert bytes(img.pixels) == raw


def test_filter3_average_round_trip():
    width, height, bpp = 4, 4, 3
    raw = _pattern_bytes(width * height * bpp, seed=37)
    filter_types = [0] + [3] * (height - 1)
    scanlines = _forward_filter(raw, width, height, bpp, filter_types)
    png = _build_png(width, height, 8, 2, scanlines)
    img = read_png(png)
    assert bytes(img.pixels) == raw


def test_filter4_paeth_round_trip():
    width, height, bpp = 4, 4, 3
    raw = _pattern_bytes(width * height * bpp, seed=53)
    filter_types = [0] + [4] * (height - 1)
    scanlines = _forward_filter(raw, width, height, bpp, filter_types)
    png = _build_png(width, height, 8, 2, scanlines)
    img = read_png(png)
    assert bytes(img.pixels) == raw


def test_mixed_filters_per_scanline_round_trip():
    """Real encoders pick a filter per row; make sure switching filter type
    row-to-row (with `prev` carried correctly across the switch) works."""
    width, height, bpp = 5, 6, 3
    raw = _pattern_bytes(width * height * bpp, seed=71)
    filter_types = [0, 1, 2, 3, 4, 1]
    scanlines = _forward_filter(raw, width, height, bpp, filter_types)
    png = _build_png(width, height, 8, 2, scanlines)
    img = read_png(png)
    assert bytes(img.pixels) == raw


def test_filter4_16bit_rgb_round_trip():
    """bpp for filtering is 6 for 16-bit RGB (gotcha G6) -- distinct from
    the 8-bit RGB bpp=3 used by the other filter tests above."""
    width, height, bpp = 2, 2, 6
    raw = _pattern_bytes(width * height * bpp, seed=91)
    scanlines = _forward_filter(raw, width, height, bpp, [0, 4])
    png = _build_png(width, height, 16, 2, scanlines)
    img = read_png(png)
    # high byte of each 16-bit sample, in RGB order
    expected = raw[0::2]
    assert bytes(img.pixels) == bytes(expected)


# --------------------------------------------------------------------------
# Paeth tie-break: "prefer a, then b, then c" (gotcha G6). Engineered so a
# wrong comparison (e.g. checking c first, or using strict `<`) selects a
# different byte value and the round trip observably fails.
# --------------------------------------------------------------------------


def _gray_2x2_round_trip(raw: bytes) -> bytes:
    scanlines = _forward_filter(raw, 2, 2, 1, [0, 4])
    png = _build_png(2, 2, 8, 0, scanlines)
    img = read_png(png)
    return bytes(img.pixels)


def test_paeth_tiebreak_prefers_a_over_c():
    # row0 = [c=2, b=3]; row1 = [a=0, target=55].
    # At row1's second pixel: predictor(a=0, b=3, c=2) has pa==pc==1 < pb==2,
    # so a correct implementation must return a (=0), not c (=2).
    raw = bytes([2, 3, 0, 55])
    expected_gray = raw
    got = _gray_2x2_round_trip(raw)
    expected_rgb = b"".join(bytes((g, g, g)) for g in expected_gray)
    assert got == expected_rgb


def test_paeth_tiebreak_prefers_b_over_c():
    # row0 = [c=1, b=3]; row1 = [a=0, target=88].
    # At row1's second pixel: predictor(a=0, b=3, c=1) has pb==pc==1 < pa==2,
    # so a correct implementation must return b (=3), not c (=1).
    raw = bytes([1, 3, 0, 88])
    expected_gray = raw
    got = _gray_2x2_round_trip(raw)
    expected_rgb = b"".join(bytes((g, g, g)) for g in expected_gray)
    assert got == expected_rgb


# --------------------------------------------------------------------------
# Color types: grayscale (0), palette (3), RGBA (6); 16-bit already covered
# above via color type 2.
# --------------------------------------------------------------------------


def test_read_png_grayscale_replicates_to_rgb():
    width, height, bpp = 4, 2, 1
    raw = _pattern_bytes(width * height, seed=17)
    scanlines = _forward_filter(raw, width, height, bpp, [0, 2])
    png = _build_png(width, height, 8, 0, scanlines)
    img = read_png(png)
    expected = b"".join(bytes((g, g, g)) for g in raw)
    assert bytes(img.pixels) == expected


def test_read_png_palette_resolves_via_plte():
    palette_colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
    plte = bytes(c for rgb in palette_colors for c in rgb)
    width, height, bpp = 4, 2, 1
    indices = bytes([0, 1, 2, 3, 3, 2, 1, 0])
    scanlines = _forward_filter(indices, width, height, bpp, [0, 1])
    png = _build_png(width, height, 8, 3, scanlines, palette=plte)
    img = read_png(png)
    expected = b"".join(bytes(palette_colors[idx]) for idx in indices)
    assert bytes(img.pixels) == expected


def test_read_png_palette_without_plte_raises():
    width, height, bpp = 2, 2, 1
    raw = bytes([0, 1, 1, 0])
    scanlines = _forward_filter(raw, width, height, bpp, [0, 0])
    png = _build_png(width, height, 8, 3, scanlines, palette=None)
    with pytest.raises(PngError):
        read_png(png)


def test_read_png_rgba_drops_alpha():
    width, height, bpp = 3, 2, 4
    raw = _pattern_bytes(width * height * bpp, seed=61)
    scanlines = _forward_filter(raw, width, height, bpp, [0, 4])
    png = _build_png(width, height, 8, 6, scanlines)
    img = read_png(png)
    expected = bytearray()
    for i in range(0, len(raw), 4):
        expected += raw[i : i + 3]
    assert bytes(img.pixels) == bytes(expected)


# --------------------------------------------------------------------------
# Multiple IDAT chunks must be concatenated into one zlib stream.
# --------------------------------------------------------------------------


def test_read_png_multiple_idat_chunks_are_concatenated():
    width, height, bpp = 6, 4, 3
    raw = _pattern_bytes(width * height * bpp, seed=41)
    scanlines = _forward_filter(raw, width, height, bpp, [0, 1, 2, 3])
    single = _build_png(width, height, 8, 2, scanlines, idat_chunks=1)
    split = _build_png(width, height, 8, 2, scanlines, idat_chunks=4)
    img_single = read_png(single)
    img_split = read_png(split)
    assert bytes(img_single.pixels) == raw
    assert bytes(img_split.pixels) == raw


# --------------------------------------------------------------------------
# Interlaced (Adam7) is unsupported and must fail clearly.
# --------------------------------------------------------------------------


def test_read_png_interlaced_raises_clear_pngerror():
    png = _build_png(4, 4, 8, 2, scanlines=b"", interlace=1)
    with pytest.raises(PngError) as exc_info:
        read_png(png)
    message = str(exc_info.value).lower()
    assert "re-save" in message
    assert "pillow" in message


def test_read_png_bad_signature_raises():
    with pytest.raises(PngError):
        read_png(b"this is not a png file at all, just plain text bytes")


# --------------------------------------------------------------------------
# load_image: plain-PNG path always works; the Pillow-fallback-absent path
# is only meaningful (and only tested) when Pillow is not installed.
# --------------------------------------------------------------------------


def test_load_image_reads_plain_png(tmp_path):
    width, height = 3, 2
    img = Image.new(width, height, fill=(10, 20, 30))
    img.set(1, 1, (200, 150, 5))
    path = tmp_path / "board.png"
    path.write_bytes(write_png(img))

    loaded = load_image(path)
    assert (loaded.width, loaded.height) == (width, height)
    assert bytes(loaded.pixels) == bytes(img.pixels)

    # str paths must work too, not just Path objects.
    loaded_from_str = load_image(str(path))
    assert bytes(loaded_from_str.pixels) == bytes(img.pixels)


@pytest.mark.skipif(_HAS_PIL, reason="this checks behavior specific to Pillow being absent")
def test_load_image_interlaced_without_pillow_raises_with_install_guidance(tmp_path):
    png = _build_png(3, 3, 8, 2, scanlines=b"", interlace=1)
    path = tmp_path / "interlaced.png"
    path.write_bytes(png)

    with pytest.raises(PngError) as exc_info:
        load_image(path)
    assert "goban-svg[images]" in str(exc_info.value)


@pytest.mark.skipif(_HAS_PIL, reason="this checks behavior specific to Pillow being absent")
def test_load_image_non_png_without_pillow_raises_with_install_guidance(tmp_path):
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"\xff\xd8\xff\xe0not actually a jpeg, just bytes")

    with pytest.raises(PngError) as exc_info:
        load_image(path)
    assert "goban-svg[images]" in str(exc_info.value)
