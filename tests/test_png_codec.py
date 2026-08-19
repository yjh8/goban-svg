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

from goban_svg.png_codec import Image, PngCorruptError, PngError, load_image, read_png, write_png

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
    trns: bytes | None = None,
    interlace: int = 0,
    idat_chunks: int = 1,
    raw_idat: bytes | None = None,
    lead_chunks: list[tuple[bytes, bytes]] | None = None,
    extra_chunks: list[tuple[bytes, bytes]] | None = None,
    include_iend: bool = True,
) -> bytes:
    """Assemble a complete PNG file byte-for-byte from its pieces.

    `raw_idat` overrides the compression step with a literal IDAT payload
    (for the malformed-zlib cases); `lead_chunks` go BEFORE IHDR and
    `extra_chunks` after it, so tests can exercise chunk-ordering and
    unknown-chunk rules; `include_iend=False` truncates the file.
    """
    ihdr = struct.pack(">IIBBBBB", width, height, bit_depth, color_type, 0, 0, interlace)
    parts = [_SIGNATURE]
    for ctype, cdata in lead_chunks or []:
        parts.append(_chunk(ctype, cdata))
    parts.append(_chunk(b"IHDR", ihdr))
    for ctype, cdata in extra_chunks or []:
        parts.append(_chunk(ctype, cdata))
    if palette is not None:
        parts.append(_chunk(b"PLTE", palette))
    if trns is not None:
        parts.append(_chunk(b"tRNS", trns))
    compressed = zlib.compress(scanlines, 6) if raw_idat is None else raw_idat
    if idat_chunks <= 1:
        parts.append(_chunk(b"IDAT", compressed))
    else:
        step = max(1, len(compressed) // idat_chunks)
        pos = 0
        while pos < len(compressed):
            parts.append(_chunk(b"IDAT", compressed[pos : pos + step]))
            pos += step
    if include_iend:
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


@pytest.mark.parametrize(
    "x,y",
    [
        (-1, 0),  # negative x alone
        (0, -1),  # negative y alone
        (-1, -1),  # both negative
        (-5, -5),  # negative, further out of range
        (2, 0),  # x at width (one past the last valid column)
        (0, 2),  # y at height (one past the last valid row)
        (2, 2),  # both at their respective bound
    ],
)
def test_image_get_set_any_out_of_range_xy_raises_index_error(x, y):
    """F15: get/set must raise on ANY out-of-range x or y, not just the
    positive-overflow case -- negative x and negative y independently, and
    combined, all need their own coverage since a bug could check only one
    axis or only positive overflow."""
    img = Image.new(2, 2)
    with pytest.raises(IndexError):
        img.get(x, y)
    with pytest.raises(IndexError):
        img.set(x, y, (0, 0, 0))


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


def test_read_png_rgba_full_alpha_matches_foreground_channels():
    """F20: alpha is no longer silently dropped -- it's composited over
    opaque black. At alpha=255 that composite is exact (255*f//255 == f for
    every f), so this reproduces the old "drop alpha" expectation as a
    special case rather than a general rule -- see the half/fully
    transparent tests below for the general rule."""
    width, height, bpp = 3, 2, 4
    raw = bytearray(_pattern_bytes(width * height * bpp, seed=61))
    for i in range(3, len(raw), 4):
        raw[i] = 255  # force full opacity on every pixel
    raw = bytes(raw)
    scanlines = _forward_filter(raw, width, height, bpp, [0, 4])
    png = _build_png(width, height, 8, 6, scanlines)
    img = read_png(png)
    expected = bytearray()
    for i in range(0, len(raw), 4):
        expected += raw[i : i + 3]
    assert bytes(img.pixels) == bytes(expected)


# --------------------------------------------------------------------------
# F20: alpha policy -- composite over opaque black (out = fg * alpha // 255)
# rather than silently dropping alpha and keeping whatever foreground RGB a
# transparent pixel happened to carry. Covers color type 6 (RGBA) and color
# type 4 (grayscale+alpha), both the 8-bit and 16-bit decode paths.
# --------------------------------------------------------------------------


def test_read_png_rgba8_half_transparent_white_composites_to_mid_gray():
    width, height, bpp = 1, 1, 4
    raw = bytes([255, 255, 255, 127])  # opaque-white foreground, alpha=127 (~half)
    scanlines = _forward_filter(raw, width, height, bpp, [0])
    png = _build_png(width, height, 8, 6, scanlines)
    img = read_png(png)
    assert img.get(0, 0) == (127, 127, 127)


def test_read_png_rgba8_fully_transparent_garbage_rgb_composites_to_black():
    width, height, bpp = 1, 1, 4
    raw = bytes([200, 77, 133, 0])  # PNG allows arbitrary "don't care" RGB at alpha=0
    scanlines = _forward_filter(raw, width, height, bpp, [0])
    png = _build_png(width, height, 8, 6, scanlines)
    img = read_png(png)
    assert img.get(0, 0) == (0, 0, 0)


def test_read_png_ga8_half_transparent_white_composites_to_mid_gray():
    width, height, bpp = 1, 1, 2
    raw = bytes([255, 127])  # opaque-white gray foreground, alpha=127 (~half)
    scanlines = _forward_filter(raw, width, height, bpp, [0])
    png = _build_png(width, height, 8, 4, scanlines)
    img = read_png(png)
    assert img.get(0, 0) == (127, 127, 127)


def test_read_png_ga8_fully_transparent_garbage_composites_to_black():
    width, height, bpp = 1, 1, 2
    raw = bytes([222, 0])  # garbage gray under alpha=0
    scanlines = _forward_filter(raw, width, height, bpp, [0])
    png = _build_png(width, height, 8, 4, scanlines)
    img = read_png(png)
    assert img.get(0, 0) == (0, 0, 0)


def test_read_png_rgba16_half_transparent_white_composites_to_mid_gray():
    width, height, bpp = 1, 1, 8
    # R=G=B high/low bytes 0xFF (opaque white); alpha's HIGH byte is what the
    # decoder uses for its 16-to-8-bit downsample (design.md's deliberate
    # policy), so alpha high byte=127 is what makes this "~half".
    raw = bytes([0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 127, 0x00])
    scanlines = _forward_filter(raw, width, height, bpp, [0])
    png = _build_png(width, height, 16, 6, scanlines)
    img = read_png(png)
    assert img.get(0, 0) == (127, 127, 127)


def test_read_png_rgba16_fully_transparent_garbage_composites_to_black():
    width, height, bpp = 1, 1, 8
    raw = bytes([0x50, 0x11, 0x22, 0x33, 0x44, 0x55, 0x00, 0x00])  # garbage RGB, alpha=0
    scanlines = _forward_filter(raw, width, height, bpp, [0])
    png = _build_png(width, height, 16, 6, scanlines)
    img = read_png(png)
    assert img.get(0, 0) == (0, 0, 0)


def test_read_png_ga16_half_transparent_white_composites_to_mid_gray():
    width, height, bpp = 1, 1, 4
    raw = bytes([0xFF, 0xFF, 127, 0x00])  # opaque-white gray, alpha high byte=127
    scanlines = _forward_filter(raw, width, height, bpp, [0])
    png = _build_png(width, height, 16, 4, scanlines)
    img = read_png(png)
    assert img.get(0, 0) == (127, 127, 127)


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


def test_read_png_idat_split_into_one_byte_chunks_crosses_adler_boundary():
    """F7: the zlib stream (2-byte header + deflate payload + 4-byte
    Adler-32 trailer) must decode correctly even chopped into 1-byte IDAT
    chunks -- including boundaries that land inside the deflate payload
    partway through a byte-pair AND inside the trailing Adler-32 checksum
    itself, not just at chunk-friendly offsets."""
    width, height, bpp = 8, 6, 3
    raw = _pattern_bytes(width * height * bpp, seed=101)
    scanlines = _forward_filter(raw, width, height, bpp, [0, 1, 2, 3, 4, 1])
    compressed = zlib.compress(scanlines, 6)
    assert len(compressed) > 12  # sanity: long enough to straddle the adler-32 trailer at 1B/chunk
    png = _build_png(width, height, 8, 2, scanlines, idat_chunks=len(compressed))
    img = read_png(png)
    assert bytes(img.pixels) == raw


# --------------------------------------------------------------------------
# Every chunk's CRC-32 is verified; a mismatch must raise PngError rather
# than silently decoding corrupt bytes.
# --------------------------------------------------------------------------


def test_read_png_corrupt_chunk_data_raises_crc_mismatch():
    width, height, bpp = 3, 2, 3
    raw = _pattern_bytes(width * height * bpp, seed=5)
    scanlines = _forward_filter(raw, width, height, bpp, [0, 1])
    png = bytearray(_build_png(width, height, 8, 2, scanlines))
    idat_type_offset = png.find(b"IDAT")
    assert idat_type_offset != -1
    corrupt_at = idat_type_offset + 4  # first byte of the IDAT chunk's data
    png[corrupt_at] ^= 0xFF
    with pytest.raises(PngError) as exc_info:
        read_png(bytes(png))
    assert "crc" in str(exc_info.value).lower()


# --------------------------------------------------------------------------
# F8: table-driven decode matrix -- every (color type, bit depth) combo this
# codec claims to support, crossed with filters 1-4. Alpha-carrying combos
# (GA, RGBA) force full opacity on every pixel so the expected RGB is an
# exact function of the foreground samples (compositing at alpha=255 is
# lossless: 255*f//255 == f) -- the alpha-compositing arithmetic itself is
# covered separately by the F20 tests above, so this matrix stays focused on
# "does every color-type/bit-depth/filter combo decode to the right bytes".
# --------------------------------------------------------------------------

_PALETTE_COLORS = [
    (255, 0, 0),
    (0, 255, 0),
    (0, 0, 255),
    (255, 255, 0),
    (128, 64, 200),
    (10, 20, 30),
    (99, 88, 77),
    (200, 200, 200),
]
_PLTE = bytes(c for rgb in _PALETTE_COLORS for c in rgb)

# (label, color_type, bit_depth) for every valid combo this codec claims.
_MATRIX_COMBOS = [
    ("gray8", 0, 8),
    ("gray16", 0, 16),
    ("ga8", 4, 8),
    ("ga16", 4, 16),
    ("palette8", 3, 8),
    ("rgb8", 2, 8),
    ("rgb16", 2, 16),
    ("rgba8", 6, 8),
    ("rgba16", 6, 16),
]

_MATRIX_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}  # mirrors png_codec._CHANNELS


def _matrix_fixture(color_type: int, bit_depth: int, width: int, height: int, seed: int) -> tuple[bytes, bytes]:
    """Build (raw sample bytes, expected flat RGB8) for one decode-matrix combo."""
    channels = _MATRIX_CHANNELS[color_type]
    sample_bytes = 2 if bit_depth == 16 else 1
    bpp = channels * sample_bytes
    n_bytes = width * height * bpp

    if color_type == 3:  # palette: raw samples are indices, not channel bytes
        raw = bytes(i % len(_PALETTE_COLORS) for i in range(n_bytes))
        expected = b"".join(bytes(_PALETTE_COLORS[idx]) for idx in raw)
        return raw, expected

    raw = bytearray(_pattern_bytes(n_bytes, seed=seed))
    if color_type in (4, 6):  # GA, RGBA: force full alpha on every pixel
        alpha_offset = bpp - sample_bytes
        for base in range(0, n_bytes, bpp):
            raw[base + alpha_offset : base + alpha_offset + sample_bytes] = b"\xff" * sample_bytes
    raw = bytes(raw)

    if color_type == 0:  # grayscale
        gray = raw[0::2] if bit_depth == 16 else raw
        expected = b"".join(bytes((g, g, g)) for g in gray)
    elif color_type == 4:  # grayscale + alpha (forced opaque above)
        gray = raw[0::4] if bit_depth == 16 else raw[0::2]
        expected = b"".join(bytes((g, g, g)) for g in gray)
    elif color_type in (2, 6):  # RGB / RGBA (RGBA forced opaque above)
        if bit_depth == 16:
            step = 6 if color_type == 2 else 8
            r, g, b = raw[0::step], raw[2::step], raw[4::step]
        else:
            step = 3 if color_type == 2 else 4
            r, g, b = raw[0::step], raw[1::step], raw[2::step]
        expected = bytearray(len(r) * 3)
        expected[0::3] = r
        expected[1::3] = g
        expected[2::3] = b
        expected = bytes(expected)
    else:  # pragma: no cover - every _MATRIX_CHANNELS key is handled above
        raise AssertionError(color_type)
    return raw, expected


@pytest.mark.parametrize("label,color_type,bit_depth", _MATRIX_COMBOS)
@pytest.mark.parametrize("filter_type", [1, 2, 3, 4])
def test_decode_matrix_all_combos_all_filters(label, color_type, bit_depth, filter_type):
    width, height = 5, 4
    channels = _MATRIX_CHANNELS[color_type]
    bpp = channels * (2 if bit_depth == 16 else 1)
    seed = 17 + color_type * 11 + bit_depth + filter_type
    raw, expected = _matrix_fixture(color_type, bit_depth, width, height, seed=seed)
    scanlines = _forward_filter(raw, width, height, bpp, [filter_type] * height)
    palette = _PLTE if color_type == 3 else None
    png = _build_png(width, height, bit_depth, color_type, scanlines, palette=palette)
    img = read_png(png)
    assert bytes(img.pixels) == expected, f"{label} filter={filter_type} mismatch"


def test_read_png_palette_16bit_raises():
    """Invalid combo: the PNG spec restricts indexed-color (palette) images
    to bit depths <= 8; 16-bit palette is not a real PNG combo and must be
    rejected, regardless of what the (never-reached) IDAT payload contains."""
    scanlines = _forward_filter(bytes(4), 2, 2, 1, [0, 0])
    png = _build_png(2, 2, 16, 3, scanlines, palette=_PLTE)
    with pytest.raises(PngError):
        read_png(png)


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


# --------------------------------------------------------------------------
# P1: IHDR hardening. The header is 13 fixed bytes that everything after it
# is interpreted against, so a structurally wrong one must fail as a clear
# PngCorruptError -- never as a raw struct.error traceback, which is what a
# CRC-valid 1-byte IHDR used to produce.
# --------------------------------------------------------------------------


def _gray_1x1(value: int = 42) -> bytes:
    """Scanline stream for a 1x1 8-bit grayscale image (filter type None)."""
    return _forward_filter(bytes([value]), 1, 1, 1, [0])


def _ihdr(width: int, height: int, bit_depth: int = 8, color_type: int = 0, interlace: int = 0) -> bytes:
    return struct.pack(">IIBBBBB", width, height, bit_depth, color_type, 0, 0, interlace)


@pytest.mark.parametrize(
    "ihdr_payload,label",
    [
        (b"\x01", "one byte"),
        (b"", "empty"),
        (_ihdr(1, 1)[:12], "one byte short"),
        (_ihdr(1, 1) + b"\x00", "one byte long"),
    ],
)
def test_read_png_wrong_length_ihdr_raises_corrupt_not_struct_error(ihdr_payload, label):
    """P1: the CRC is valid here -- only the LENGTH is wrong -- so nothing
    upstream catches it. Before the fix a 1-byte IHDR reached struct.unpack
    and escaped as struct.error, a traceback no caller of read_png is
    documented to handle."""
    png = b"".join(
        [
            _SIGNATURE,
            _chunk(b"IHDR", ihdr_payload),
            _chunk(b"IDAT", zlib.compress(_gray_1x1(), 6)),
            _chunk(b"IEND", b""),
        ]
    )
    with pytest.raises(PngCorruptError) as exc_info:
        read_png(png)
    message = str(exc_info.value)
    assert "IHDR" in message
    assert "13" in message, f"message should name the required size ({label}): {message}"
    assert str(len(ihdr_payload)) in message, f"message should name the offending size ({label}): {message}"


def test_read_png_ihdr_must_be_the_first_chunk():
    """P1: a chunk ahead of IHDR means everything before the header was
    interpreted with no header -- reject rather than guess."""
    png = _build_png(1, 1, 8, 0, _gray_1x1(), lead_chunks=[(b"gAMA", struct.pack(">I", 45455))])
    with pytest.raises(PngCorruptError) as exc_info:
        read_png(png)
    message = str(exc_info.value)
    assert "IHDR" in message
    assert "gAMA" in message  # names the chunk that was actually found first


def test_read_png_duplicate_ihdr_raises_corrupt():
    """P1: IHDR is unique; a second one silently redefined the geometry."""
    png = _build_png(1, 1, 8, 0, _gray_1x1(), extra_chunks=[(b"IHDR", _ihdr(4, 4))])
    with pytest.raises(PngCorruptError) as exc_info:
        read_png(png)
    assert "IHDR" in str(exc_info.value)


@pytest.mark.parametrize("width,height", [(0, 1), (1, 0), (0, 0)])
def test_read_png_zero_dimension_ihdr_raises_corrupt(width, height):
    """P1: PNG requires width and height >= 1. A zero-dimension IHDR passes
    every downstream length check trivially (0 expected bytes) and would
    otherwise decode to an empty 'image'."""
    png = _build_png(width, height, 8, 0, scanlines=b"")
    with pytest.raises(PngCorruptError) as exc_info:
        read_png(png)
    message = str(exc_info.value)
    assert f"{width}x{height}" in message


# --------------------------------------------------------------------------
# P2: PLTE must be a nonzero multiple of 3, at most 256 entries -- the bound
# the 256-entry decode lookup tables are built on. A 300-entry PLTE used to
# run off the end of those tables with an IndexError.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "plte,label",
    [
        (b"", "empty"),
        (b"\xff\x00\x00\x11", "not a multiple of 3"),
        (b"\xff\x00", "shorter than one entry"),
        (b"\x00" * (300 * 3), "300 entries, past the 256-entry table"),
    ],
)
def test_read_png_malformed_plte_raises_corrupt(plte, label):
    indices = bytes([0, 0, 0, 0])
    scanlines = _forward_filter(indices, 2, 2, 1, [0, 0])
    png = _build_png(2, 2, 8, 3, scanlines, palette=plte)
    with pytest.raises(PngCorruptError) as exc_info:
        read_png(png)
    assert "PLTE" in str(exc_info.value), f"{label}: {exc_info.value}"


def test_read_png_full_256_entry_palette_still_decodes():
    """The 256-entry palette is the largest LEGAL one -- the P2 bound must
    reject 257+, not clip the maximum a real encoder can emit."""
    colors = [(i, (255 - i) % 256, (i * 7) % 256) for i in range(256)]
    plte = bytes(c for rgb in colors for c in rgb)
    indices = bytes([0, 1, 128, 255])
    scanlines = _forward_filter(indices, 4, 1, 1, [0])
    png = _build_png(4, 1, 8, 3, scanlines, palette=plte)
    img = read_png(png)
    assert bytes(img.pixels) == b"".join(bytes(colors[i]) for i in indices)


# --------------------------------------------------------------------------
# P3: the inflated image data must be exactly the size the header implies,
# and the inflater must be bounded so a bomb is stopped at the cap rather
# than allocated first and measured afterwards.
# --------------------------------------------------------------------------


def test_read_png_decompression_bomb_raises_corrupt():
    """P3: ~1 KB of IDAT that inflates to 1 MB under a 1x1 IHDR (2 expected
    bytes). Before the fix only the too-SHORT direction was checked, so this
    inflated in full and decoded as a 1x1 image."""
    png = _build_png(1, 1, 8, 0, scanlines=bytes(1_000_000))
    idat_payload_size = len(zlib.compress(bytes(1_000_000), 6))
    assert idat_payload_size < 2000, "fixture sanity: the bomb's compressed form is tiny"
    with pytest.raises(PngCorruptError) as exc_info:
        read_png(png)
    assert "bomb" in str(exc_info.value).lower()


def test_read_png_image_data_one_byte_too_long_raises_corrupt():
    """P3: the length check is an equality, not just a floor -- one byte of
    excess is still a header that disagrees with its data."""
    png = _build_png(1, 1, 8, 0, scanlines=bytes(3))  # a 1x1 gray image needs exactly 2
    with pytest.raises(PngCorruptError):
        read_png(png)


def test_read_png_image_data_too_short_raises_corrupt():
    png = _build_png(2, 2, 8, 2, scanlines=bytes(5))  # 2x2 RGB8 needs 2 * (1 + 6) == 14
    with pytest.raises(PngCorruptError) as exc_info:
        read_png(png)
    message = str(exc_info.value)
    assert "14" in message and "5" in message


def test_read_png_zlib_stream_missing_its_adler_trailer_raises_corrupt():
    """P3: bounding the inflater must not cost the stream-integrity check --
    a zlib stream whose Adler-32 trailer was cut off still inflates to the
    right bytes, and must still be rejected."""
    width, height, bpp = 4, 3, 3
    raw = _pattern_bytes(width * height * bpp, seed=131)
    scanlines = _forward_filter(raw, width, height, bpp, [0, 1, 2])
    truncated = zlib.compress(scanlines, 6)[:-4]  # drop the 4-byte Adler-32
    png = _build_png(width, height, 8, 2, scanlines, raw_idat=truncated)
    with pytest.raises(PngCorruptError) as exc_info:
        read_png(png)
    assert "zlib" in str(exc_info.value).lower()


def test_read_png_corrupt_zlib_data_raises_corrupt():
    png = _build_png(2, 2, 8, 2, scanlines=bytes(14), raw_idat=b"not a zlib stream at all")
    with pytest.raises(PngCorruptError) as exc_info:
        read_png(png)
    assert "decompress" in str(exc_info.value).lower()


def test_read_png_absurd_pixel_count_is_refused_before_allocating():
    """P3: 100000x100000 is 10 billion pixels -- 30 GB of RGB8. The refusal
    has to come from the HEADER, before any buffer is sized from it. This
    test returning at all (rather than swapping the machine to death) is the
    assertion."""
    png = _build_png(100_000, 100_000, 8, 0, scanlines=b"")
    with pytest.raises(PngError) as exc_info:
        read_png(png)
    message = str(exc_info.value)
    assert "100000x100000" in message
    # Not corruption -- the file is well-formed, we simply refuse it, so
    # load_image is still allowed to try Pillow (which has its own bomb guard).
    assert not isinstance(exc_info.value, PngCorruptError)


# --------------------------------------------------------------------------
# P4: tRNS. Transparency declared out-of-band by a tRNS chunk composites over
# opaque black exactly like a real alpha channel (fg * a // 255) -- before
# the fix a fully transparent palette entry decoded as its opaque PLTE color,
# contradicting the module's own alpha policy.
# --------------------------------------------------------------------------


def test_read_png_palette_trns_transparent_entry_composites_to_black():
    plte = bytes([255, 0, 0, 0, 0, 255])  # entry 0 red, entry 1 blue
    scanlines = _forward_filter(bytes([0, 1]), 2, 1, 1, [0])
    png = _build_png(2, 1, 8, 3, scanlines, palette=plte, trns=bytes([0]))
    img = read_png(png)
    assert img.get(0, 0) == (0, 0, 0)  # transparent red, NOT (255, 0, 0)
    # tRNS is shorter than PLTE: entries it does not cover stay opaque.
    assert img.get(1, 0) == (0, 0, 255)


def test_read_png_palette_trns_partial_alpha_composites_over_black():
    """Palette alpha is a full byte, not a transparent/opaque flag: entry 0
    at alpha=128 composites to (200*128//255, 100*128//255, 50*128//255)."""
    plte = bytes([200, 100, 50, 10, 20, 30])
    scanlines = _forward_filter(bytes([0, 1]), 2, 1, 1, [0])
    png = _build_png(2, 1, 8, 3, scanlines, palette=plte, trns=bytes([128, 255]))
    img = read_png(png)
    assert img.get(0, 0) == (100, 50, 25)
    assert img.get(1, 0) == (10, 20, 30)  # alpha 255 is exact


def test_read_png_gray8_trns_color_key_composites_to_black():
    raw = bytes([0x10, 0x20, 0x30])
    scanlines = _forward_filter(raw, 3, 1, 1, [0])
    png = _build_png(3, 1, 8, 0, scanlines, trns=b"\x00\x20")  # 16-bit field, sample in the low byte
    img = read_png(png)
    assert img.get(0, 0) == (0x10, 0x10, 0x10)
    assert img.get(1, 0) == (0, 0, 0)  # the keyed sample
    assert img.get(2, 0) == (0x30, 0x30, 0x30)


def test_read_png_gray16_trns_compares_before_the_16_to_8_downsample():
    """Both pixels share the high byte 0x12 that survives the downsample;
    only the one whose FULL 16-bit sample equals the key is transparent. A
    decoder that compared post-downsample would blank both."""
    raw = bytes([0x12, 0x34, 0x12, 0x56])
    scanlines = _forward_filter(raw, 2, 1, 2, [0])
    png = _build_png(2, 1, 16, 0, scanlines, trns=b"\x12\x34")
    img = read_png(png)
    assert img.get(0, 0) == (0, 0, 0)
    assert img.get(1, 0) == (0x12, 0x12, 0x12)


def test_read_png_rgb8_trns_color_key_composites_to_black():
    raw = bytes([255, 0, 0, 254, 0, 0])  # the key color, then a near-miss
    scanlines = _forward_filter(raw, 2, 1, 3, [0])
    png = _build_png(2, 1, 8, 2, scanlines, trns=b"\x00\xff\x00\x00\x00\x00")
    img = read_png(png)
    assert img.get(0, 0) == (0, 0, 0)
    assert img.get(1, 0) == (254, 0, 0)  # one channel off the key: fully opaque


def test_read_png_rgb16_trns_compares_before_the_16_to_8_downsample():
    raw = bytes([0xAB, 0xCD, 0x00, 0x01, 0x00, 0x02, 0xAB, 0xFF, 0x00, 0x01, 0x00, 0x02])
    scanlines = _forward_filter(raw, 2, 1, 6, [0])
    png = _build_png(2, 1, 16, 2, scanlines, trns=bytes([0xAB, 0xCD, 0x00, 0x01, 0x00, 0x02]))
    img = read_png(png)
    assert img.get(0, 0) == (0, 0, 0)
    assert img.get(1, 0) == (0xAB, 0x00, 0x00)  # differs only in a low byte -> opaque


def test_read_png_trns_partial_alpha_matrix_entry_survives_filtering():
    """tRNS must survive a non-trivial filter type too -- the alpha lookup is
    applied after unfiltering, not to the filtered bytes."""
    plte = bytes([255, 255, 255, 8, 8, 8])
    indices = bytes([0, 1, 1, 0])
    scanlines = _forward_filter(indices, 2, 2, 1, [1, 4])
    png = _build_png(2, 2, 8, 3, scanlines, palette=plte, trns=bytes([0, 255]))
    img = read_png(png)
    assert img.get(0, 0) == (0, 0, 0)
    assert img.get(1, 0) == (8, 8, 8)
    assert img.get(0, 1) == (8, 8, 8)
    assert img.get(1, 1) == (0, 0, 0)


@pytest.mark.parametrize(
    "color_type,bit_depth,bpp,trns,label",
    [
        (0, 8, 1, b"\x00", "grayscale tRNS shorter than its 2-byte field"),
        (0, 8, 1, b"\x00\x00\x00", "grayscale tRNS longer than its 2-byte field"),
        (2, 8, 3, b"\x00\x00\x00\x00", "truecolor tRNS shorter than its 6-byte field"),
        (2, 8, 3, b"\x00" * 8, "truecolor tRNS longer than its 6-byte field"),
    ],
)
def test_read_png_trns_wrong_length_raises_corrupt(color_type, bit_depth, bpp, trns, label):
    raw = bytes(bpp)
    scanlines = _forward_filter(raw, 1, 1, bpp, [0])
    png = _build_png(1, 1, bit_depth, color_type, scanlines, trns=trns)
    with pytest.raises(PngCorruptError) as exc_info:
        read_png(png)
    message = str(exc_info.value)
    assert "tRNS" in message, f"{label}: {message}"
    assert str(len(trns)) in message, f"{label}: message should name the offending size: {message}"


def test_read_png_palette_trns_longer_than_palette_raises_corrupt():
    plte = bytes([255, 0, 0, 0, 0, 255])  # two entries
    scanlines = _forward_filter(bytes([0, 1]), 2, 1, 1, [0])
    png = _build_png(2, 1, 8, 3, scanlines, palette=plte, trns=bytes([0, 128, 255]))
    with pytest.raises(PngCorruptError) as exc_info:
        read_png(png)
    message = str(exc_info.value)
    assert "tRNS" in message
    assert "3" in message and "2" in message  # names both the tRNS and the PLTE entry counts


@pytest.mark.parametrize("color_type,bpp", [(4, 2), (6, 4)])
def test_read_png_trns_on_a_color_type_that_has_alpha_raises_corrupt(color_type, bpp):
    """The spec forbids tRNS alongside a real alpha channel; the two would
    define transparency twice, with no rule for reconciling them."""
    scanlines = _forward_filter(bytes(bpp), 1, 1, bpp, [0])
    png = _build_png(1, 1, 8, color_type, scanlines, trns=b"\x00\x00")
    with pytest.raises(PngCorruptError) as exc_info:
        read_png(png)
    message = str(exc_info.value)
    assert "tRNS" in message
    assert str(color_type) in message


# --------------------------------------------------------------------------
# P5: corruption vs unsupported feature. load_image may retry an UNSUPPORTED
# file through Pillow; it must never retry a CORRUPT one, because Pillow does
# not verify chunk CRCs and would decode the very bytes this codec rejected.
# --------------------------------------------------------------------------


def test_png_corrupt_error_is_a_png_error():
    """Additive by design: existing `except PngError` callers keep catching
    corruption without knowing the subclass exists."""
    assert issubclass(PngCorruptError, PngError)


def _crc_corrupt_png_bytes() -> bytes:
    width, height, bpp = 3, 2, 3
    raw = _pattern_bytes(width * height * bpp, seed=5)
    scanlines = _forward_filter(raw, width, height, bpp, [0, 1])
    png = bytearray(_build_png(width, height, 8, 2, scanlines))
    idat_type_offset = png.find(b"IDAT")
    assert idat_type_offset != -1
    png[idat_type_offset + 4] ^= 0xFF  # first byte of the IDAT chunk's data
    return bytes(png)


def test_load_image_crc_corrupt_png_raises_corrupt_and_never_falls_back(tmp_path):
    """P5: the CRC check only protects anything if it survives load_image.
    With `except PngError: pass` this file reached Pillow -- which does not
    check CRCs -- and decoded, so the documented integrity guarantee was
    void for every caller who went through the public loader (i.e. all of
    them). Asserting the TYPE is what proves no fallback happened: this must
    hold whether or not Pillow is installed."""
    path = tmp_path / "corrupt.png"
    path.write_bytes(_crc_corrupt_png_bytes())

    with pytest.raises(PngCorruptError) as exc_info:
        load_image(path)
    assert "crc" in str(exc_info.value).lower()


@pytest.mark.skipif(_HAS_PIL, reason="this checks behavior specific to Pillow being absent")
def test_load_image_without_pillow_reports_the_original_reason(tmp_path):
    """P5: 're-save as a plain PNG' is useless advice when the file failed
    for some reason other than interlacing -- the wrapper must carry the
    underlying PngError's message, not replace it."""
    png = _build_png(2, 2, 8, 0, scanlines=b"", extra_chunks=[(b"CRIT", b"")])
    path = tmp_path / "unknown-critical.png"
    path.write_bytes(png)

    with pytest.raises(PngError) as exc_info:
        load_image(path)
    message = str(exc_info.value)
    assert "CRIT" in message  # the original reason, not just generic re-save advice
    assert "goban-svg[images]" in message
    assert isinstance(exc_info.value.__cause__, PngError)


@pytest.mark.skipif(_HAS_PIL, reason="this checks behavior specific to Pillow being absent")
def test_load_image_interlaced_without_pillow_names_interlacing(tmp_path):
    png = _build_png(3, 3, 8, 2, scanlines=b"", interlace=1)
    path = tmp_path / "interlaced.png"
    path.write_bytes(png)

    with pytest.raises(PngError) as exc_info:
        load_image(path)
    assert "interlaced" in str(exc_info.value).lower()


# --------------------------------------------------------------------------
# P6: unknown chunks. A conforming decoder must refuse an unknown CRITICAL
# chunk (it can change how the image data is to be read) and must skip
# unknown ancillary ones.
# --------------------------------------------------------------------------


def test_read_png_unknown_critical_chunk_raises_unsupported():
    scanlines = _forward_filter(bytes([7]), 1, 1, 1, [0])
    png = _build_png(1, 1, 8, 0, scanlines, extra_chunks=[(b"CRIT", b"payload")])
    with pytest.raises(PngError) as exc_info:
        read_png(png)
    message = str(exc_info.value)
    assert "CRIT" in message
    assert "critical" in message.lower()
    # Unsupported, not corrupt: the bytes are fine, we just can't honor them,
    # so load_image is still free to hand this to Pillow.
    assert not isinstance(exc_info.value, PngCorruptError)


def test_read_png_unknown_ancillary_chunks_are_skipped():
    scanlines = _forward_filter(bytes([7, 9]), 2, 1, 1, [0])
    png = _build_png(
        2,
        1,
        8,
        0,
        scanlines,
        extra_chunks=[
            (b"gAMA", struct.pack(">I", 45455)),
            (b"pHYs", struct.pack(">IIB", 2835, 2835, 1)),
            (b"tEXt", b"Comment\x00made by hand"),
        ],
    )
    img = read_png(png)
    assert bytes(img.pixels) == bytes([7, 7, 7, 9, 9, 9])


def test_read_png_missing_iend_raises_corrupt():
    """P6: no IEND means the file stopped early -- whatever IDAT arrived may
    be a prefix of the real image data."""
    scanlines = _forward_filter(bytes([7]), 1, 1, 1, [0])
    png = _build_png(1, 1, 8, 0, scanlines, include_iend=False)
    with pytest.raises(PngCorruptError) as exc_info:
        read_png(png)
    assert "IEND" in str(exc_info.value)


def test_read_png_truncated_chunk_raises_corrupt():
    """A chunk whose declared length runs past the end of the file: the
    length field is attacker-controlled, so this must be a clean error and
    must not try to allocate the declared size."""
    scanlines = _forward_filter(bytes([7]), 1, 1, 1, [0])
    png = bytearray(_build_png(1, 1, 8, 0, scanlines))
    idat_type_offset = png.find(b"IDAT")
    png[idat_type_offset - 4 : idat_type_offset] = struct.pack(">I", 0xFFFF_FFF0)
    with pytest.raises(PngCorruptError) as exc_info:
        read_png(bytes(png))
    assert "truncated" in str(exc_info.value).lower()
