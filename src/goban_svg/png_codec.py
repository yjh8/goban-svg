"""Minimal, dependency-free PNG codec plus the flat-RGB8 :class:`Image` type.

This is the lowest layer of goban-svg's image pipeline (design.md §7): it
imports nothing else from this package, so every module that needs pixels
(``digits``, ``render``, ``extract``) depends on it, never the reverse.

``read_png``/``write_png`` implement just enough of the PNG spec (ISO/IEC
15948) to round-trip the screenshots this tool cares about: bit depths 8 and
16, color types 0/2/3/4/6 (grayscale, RGB, palette, grayscale+alpha, RGBA),
the ``tRNS`` transparency chunk, and filter types 0-4. Interlaced (Adam7)
PNGs are deliberately unsupported — de-interlacing well is real complexity
this tool never needs to produce, so ``read_png`` raises a clear
:class:`PngError` and ``load_image`` falls back to Pillow when it is
installed (the optional ``goban-svg[images]`` extra). Every chunk's trailing
CRC-32 is verified against its type+data; a mismatch raises
:class:`PngCorruptError` rather than silently decoding corrupt bytes.

Two failure classes, and the difference is load-bearing:

* :class:`PngCorruptError` — the *bytes are damaged*: a CRC-32 mismatch, a
  structurally bad IHDR/PLTE/tRNS, a truncated chunk, a zlib failure, image
  data that inflates to the wrong length, a missing IEND. ``load_image``
  re-raises these untouched. It must NOT hand them to Pillow, which does not
  verify chunk CRCs and will happily decode bytes this codec rejected —
  falling back there would silently defeat the integrity check this module
  advertises.
* :class:`PngError` (the base class) — the file is *well-formed but uses a
  feature this codec does not implement*: interlacing, bit depths 1/2/4, an
  unknown color type, an unknown *critical* chunk (the PNG spec forbids
  ignoring those), or an image too large to be worth allocating for. Only
  these fall back to Pillow.

Alpha policy: color types 4 and 6 (grayscale+alpha, RGBA) are composited
over opaque black — ``out = fg * alpha // 255`` per channel, on both the
8- and 16-bit decode paths — rather than the naive "drop alpha, keep the
foreground RGB" this codec used to do. PNG explicitly allows arbitrary
"don't care" RGB under zero alpha, so keeping raw foreground values can
paint stray colors from pixels that were meant to be invisible; compositing
over black also matches the dark-UI-frame assumption the rest of goban-svg
makes about its screenshots (design.md: ~950x950 shots of a Go app with a
dark frame around the wood board), so a transparent screenshot pixel
resolves to a color that already belongs to that frame's palette rather than
an arbitrary hue. ``tRNS`` transparency goes through that same composite:
per-entry alpha for palette images, an all-or-nothing color key for
grayscale/truecolor, so a transparent palette entry decodes to black rather
than to the opaque color that happened to sit in its PLTE slot.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Image", "PngCorruptError", "PngError", "load_image", "read_png", "write_png"]


class PngError(Exception):
    """Raised for PNG data this codec cannot decode, or malformed PNG input.

    The base class means "well-formed PNG, feature this codec does not
    implement" — the case ``load_image`` may safely retry with Pillow. Damage
    to the bytes themselves raises the :class:`PngCorruptError` subclass.
    """


class PngCorruptError(PngError):
    """Raised when the PNG bytes are damaged rather than merely exotic.

    A subclass of :class:`PngError` so existing ``except PngError`` handlers
    keep working, but ``load_image`` singles it out: corrupt input is never
    retried through Pillow (which does not verify chunk CRCs), so a file this
    codec rejected as corrupt fails loudly instead of decoding anyway.
    """


_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# Samples-per-pixel for each supported PNG color type. 1, 5, 7 don't exist in
# the PNG spec; anything not in this map is rejected as unsupported.
_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}

# IHDR's fixed payload: width, height, bit depth, color type, compression
# method, filter method, interlace method.
_IHDR_SIZE = 13

# PLTE holds at most 256 RGB triples (the largest index an 8-bit sample can
# name); the 256-entry lookup tables in _to_rgb_bytes depend on that bound.
_MAX_PALETTE_BYTES = 768

# Refuse to allocate for absurd declared dimensions before touching the image
# data: a 12-byte IHDR can claim 4 billion x 4 billion. 100 megapixels is ~35x
# the ~950x950 screenshots this tool is built for, so nothing real trips it.
_MAX_PIXELS = 100_000_000


@dataclass
class Image:
    """A width x height raster as a flat, row-major RGB8 bytearray.

    ``pixels[3*(y*width+x) : 3*(y*width+x)+3]`` is the ``(r, g, b)`` triplet
    for pixel ``(x, y)``. Flat bytearray rather than e.g. a list of tuples so
    PNG decode/encode (and the rest of the pipeline) can move whole channels
    with slice assignment instead of a per-pixel Python loop.
    """

    width: int
    height: int
    pixels: bytearray

    @classmethod
    def new(cls, width: int, height: int, fill: tuple[int, int, int] = (0, 0, 0)) -> Image:
        img = cls(width, height, bytearray(width * height * 3))
        img.fill(fill)
        return img

    def _offset(self, x: int, y: int) -> int:
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise IndexError(f"pixel ({x}, {y}) out of bounds for a {self.width}x{self.height} image")
        return (y * self.width + x) * 3

    def get(self, x: int, y: int) -> tuple[int, int, int]:
        i = self._offset(x, y)
        return (self.pixels[i], self.pixels[i + 1], self.pixels[i + 2])

    def set(self, x: int, y: int, rgb: tuple[int, int, int]) -> None:
        i = self._offset(x, y)
        self.pixels[i], self.pixels[i + 1], self.pixels[i + 2] = rgb

    def fill(self, rgb: tuple[int, int, int]) -> None:
        r, g, b = rgb
        n = self.width * self.height
        self.pixels[0::3] = bytes((r,)) * n
        self.pixels[1::3] = bytes((g,)) * n
        self.pixels[2::3] = bytes((b,)) * n


def _paeth(a: int, b: int, c: int) -> int:
    """PNG Paeth predictor (spec §9.4).

    Ties MUST resolve a, then b, then c (gotcha G6). Get this backwards and
    only *some* filter-4 scanlines decode wrong — whichever ones happen to
    hit an exact tie — which is what made this bite only some screenshots
    and not others in the prior implementation attempt.
    """
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _unfilter(data: bytes, width: int, height: int, bpp: int, row_bytes: int) -> bytearray:
    """Reverse PNG's per-scanline filtering (spec §6) into raw sample bytes.

    `bpp` here is bytes-per-pixel *for filtering purposes* — it depends on
    color type AND bit depth (RGBA8=4, RGB8=3, RGB16=6, grayscale8=1, ...),
    not on the final RGB8 output size. Using the wrong bpp decodes as
    diagonal garbage, and only on scanlines that used Sub/Average/Paeth —
    exactly the "only some screenshots" failure mode gotcha G6 warns about.

    This loop is inherently scalar: each unfiltered byte can depend on the
    previous unfiltered byte in the same row (Sub, Paeth), so unlike the
    channel-shuffle code in ``_to_rgb_bytes`` there's no slice-assignment
    form for it.
    """
    stride = 1 + row_bytes
    out = bytearray(row_bytes * height)
    prev = bytearray(row_bytes)  # implicit all-zero "row -1" for the Up/Paeth of row 0
    pos = 0
    for y in range(height):
        ftype = data[pos]
        row = bytearray(data[pos + 1 : pos + stride])
        pos += stride
        if ftype == 0:  # None
            pass
        elif ftype == 1:  # Sub
            for x in range(bpp, row_bytes):
                row[x] = (row[x] + row[x - bpp]) & 0xFF
        elif ftype == 2:  # Up
            for x in range(row_bytes):
                row[x] = (row[x] + prev[x]) & 0xFF
        elif ftype == 3:  # Average
            for x in range(row_bytes):
                a = row[x - bpp] if x >= bpp else 0
                row[x] = (row[x] + ((a + prev[x]) >> 1)) & 0xFF
        elif ftype == 4:  # Paeth
            for x in range(row_bytes):
                a = row[x - bpp] if x >= bpp else 0
                c = prev[x - bpp] if x >= bpp else 0
                row[x] = (row[x] + _paeth(a, prev[x], c)) & 0xFF
        else:
            raise PngCorruptError(f"corrupt PNG: filter type {ftype} on scanline {y} is not a valid PNG filter (0-4)")
        out[y * row_bytes : (y + 1) * row_bytes] = row
        prev = row
    return out


def _composite_over_black(fg: bytes | bytearray, alpha: bytes | bytearray) -> bytearray:
    """Composite one channel over opaque black: ``out = fg * alpha // 255``.

    Exact (no rounding loss) at alpha=255 since ``255 * f // 255 == f``, and
    collapses to 0 at alpha=0 regardless of what "don't care" garbage `fg`
    holds — see the alpha-policy paragraph in the module docstring. This is
    a per-element Python loop (no slice-assignment trick multiplies two
    varying byte strings together), same tradeoff as ``_unfilter``'s scalar
    loop: the per-byte cost is unavoidable here.
    """
    return bytearray((f * a) // 255 for f, a in zip(fg, alpha, strict=True))


def _color_key_alpha(
    raw: bytes | bytearray,
    n: int,
    channels: int,
    bit_depth: int,
    key: tuple[int, ...],
) -> bytearray:
    """Per-pixel alpha for a tRNS color key: 0 where the pixel *is* the key, else 255.

    The comparison happens on the PRE-downsample samples: at bit depth 16 a
    tRNS field names a full 16-bit value, so 0x1234 must not match a pixel
    whose sample is 0x1256 merely because both downsample to the 0x12 this
    codec keeps. Comparing after the 16-to-8 squeeze would punch holes in
    every pixel that shares a high byte with the key.

    Per channel this builds a 0/255 plane (``bytes.translate`` at bit depth 8,
    where a 256-entry table does the whole comparison in one C pass), then
    combines them with ``max``: a plane is 0 where that channel matches, so
    the max is 0 only when EVERY channel matched. (``min`` would make a pixel
    transparent whenever any single channel happened to match the key —
    e.g. every pure-red pixel in an image keyed on some other red.)
    """
    sample_bytes = 2 if bit_depth == 16 else 1
    bpp = channels * sample_bytes
    planes: list[bytes | bytearray] = []
    for c, channel_key in enumerate(key):
        off = c * sample_bytes
        if bit_depth == 16:
            hi, lo = raw[off::bpp], raw[off + 1 :: bpp]
            planes.append(bytearray(0 if (h << 8 | low) == channel_key else 255 for h, low in zip(hi, lo, strict=True)))
        else:
            table = bytearray(b"\xff" * 256)
            if channel_key < 256:  # a >255 key at bit depth 8 simply matches nothing
                table[channel_key] = 0
            planes.append(raw[off::bpp].translate(bytes(table)))
    alpha = bytearray(planes[0]) if len(planes) == 1 else bytearray(map(max, *planes))
    if len(alpha) != n:  # pragma: no cover - read_png already length-checked the scanline data
        raise PngCorruptError(f"corrupt PNG: {len(alpha)} pixels of sample data for a {n}-pixel image")
    return alpha


def _to_rgb_bytes(
    raw: bytes | bytearray,
    width: int,
    height: int,
    color_type: int,
    bit_depth: int,
    palette: bytes | None,
    transparency: bytes | None = None,
) -> bytearray:
    """Reshuffle unfiltered PNG sample bytes into flat RGB8.

    Every branch but the alpha-carrying ones is a slice assignment (or, for
    palette, a ``bytes.translate`` 256-entry lookup) rather than a per-pixel
    Python loop — the unavoidable per-byte cost already went into
    ``_unfilter``; there's no reason to pay it again here. 16-bit samples
    are big-endian, so e.g. ``raw[0::2]`` takes the high byte of each
    sample — the deliberate 16-to-8-bit downsample this codec uses
    (design.md §7).

    `transparency` is a validated tRNS payload (or None). For palette images
    it folds straight into the lookup tables -- ``entry * alpha // 255`` once
    per palette entry rather than once per pixel -- and for grayscale /
    truecolor it becomes an all-or-nothing color key applied through the same
    composite-over-black that the alpha-channel color types use.
    """
    n = width * height
    out = bytearray(n * 3)
    if color_type == 2:  # RGB
        if bit_depth == 8:
            out[:] = raw
        else:
            out[0::3] = raw[0::6]
            out[1::3] = raw[2::6]
            out[2::3] = raw[4::6]
    elif color_type == 6:  # RGBA -- composited over opaque black (module docstring)
        if bit_depth == 8:
            alpha = raw[3::4]
            out[0::3] = _composite_over_black(raw[0::4], alpha)
            out[1::3] = _composite_over_black(raw[1::4], alpha)
            out[2::3] = _composite_over_black(raw[2::4], alpha)
        else:
            alpha = raw[6::8]
            out[0::3] = _composite_over_black(raw[0::8], alpha)
            out[1::3] = _composite_over_black(raw[2::8], alpha)
            out[2::3] = _composite_over_black(raw[4::8], alpha)
    elif color_type == 0:  # grayscale
        gray = raw[0::2] if bit_depth == 16 else raw
        out[0::3] = gray
        out[1::3] = gray
        out[2::3] = gray
    elif color_type == 4:  # grayscale + alpha -- composited over opaque black
        if bit_depth == 16:
            gray, alpha = raw[0::4], raw[2::4]
        else:
            gray, alpha = raw[0::2], raw[1::2]
        composited = _composite_over_black(gray, alpha)
        out[0::3] = composited
        out[1::3] = composited
        out[2::3] = composited
    elif color_type == 3:  # palette (indexed), resolved via PLTE
        if palette is None:
            raise PngCorruptError("corrupt PNG: indexed-color (color type 3) image has no PLTE chunk")
        r_table, g_table, b_table = bytearray(256), bytearray(256), bytearray(256)
        for i in range(len(palette) // 3):
            # tRNS may be shorter than PLTE; entries it doesn't cover are opaque.
            a = transparency[i] if transparency is not None and i < len(transparency) else 255
            r_table[i] = (palette[3 * i] * a) // 255
            g_table[i] = (palette[3 * i + 1] * a) // 255
            b_table[i] = (palette[3 * i + 2] * a) // 255
        raw_bytes = bytes(raw)
        out[0::3] = raw_bytes.translate(bytes(r_table))
        out[1::3] = raw_bytes.translate(bytes(g_table))
        out[2::3] = raw_bytes.translate(bytes(b_table))
    else:  # pragma: no cover - guarded by the _CHANNELS check in read_png
        raise PngError(f"unsupported PNG color type {color_type}")

    if transparency is not None and color_type in (0, 2):
        # tRNS on grayscale/truecolor is a single "this exact color is fully
        # transparent" key, stored as one 16-bit field per channel whatever
        # the bit depth. Binary alpha, so the composite collapses to
        # "keep the pixel or replace it with black" -- but it runs through
        # _composite_over_black anyway so there is exactly one place in this
        # module that decides what transparency looks like.
        key = tuple(int.from_bytes(transparency[2 * i : 2 * i + 2], "big") for i in range(len(transparency) // 2))
        alpha = _color_key_alpha(raw, n, _CHANNELS[color_type], bit_depth, key)
        out[0::3] = _composite_over_black(out[0::3], alpha)
        out[1::3] = _composite_over_black(out[1::3], alpha)
        out[2::3] = _composite_over_black(out[2::3], alpha)
    return out


def _parse_ihdr(cdata: bytes) -> tuple[int, int, int, int, int]:
    """Validate an IHDR payload structurally, returning its decoded fields.

    Structural damage here is :class:`PngCorruptError` (a 1-byte IHDR is a
    broken file, not an exotic one); unimplementable *values* — a compression
    or filter method this codec doesn't speak — stay plain :class:`PngError`
    so Pillow gets its shot at them.
    """
    if len(cdata) != _IHDR_SIZE:
        raise PngCorruptError(f"corrupt PNG: IHDR chunk must be exactly {_IHDR_SIZE} bytes, got {len(cdata)}")
    try:
        width, height, bit_depth, color_type, comp, filt, interlace = struct.unpack(">IIBBBBB", cdata)
    except struct.error as exc:  # pragma: no cover - the length check above already guarantees 13 bytes
        # Belt and braces: struct.error must never escape read_png as a raw
        # traceback, whatever future edits do to the check above.
        raise PngCorruptError(f"corrupt PNG: unreadable IHDR chunk ({exc})") from exc
    if width < 1 or height < 1:
        raise PngCorruptError(
            f"corrupt PNG: IHDR declares a {width}x{height} image; width and height must both be at least 1"
        )
    if comp != 0:
        raise PngError(f"unsupported PNG compression method {comp} (only method 0, deflate, is defined)")
    if filt != 0:
        raise PngError(f"unsupported PNG filter method {filt} (only method 0 is defined)")
    return width, height, bit_depth, color_type, interlace


def _validate_plte(cdata: bytes) -> bytes:
    """Check a PLTE payload is a nonempty run of at most 256 RGB triples."""
    if len(cdata) == 0 or len(cdata) % 3 != 0:
        raise PngCorruptError(f"corrupt PNG: PLTE chunk is {len(cdata)} bytes, not a nonzero multiple of 3")
    if len(cdata) > _MAX_PALETTE_BYTES:
        raise PngCorruptError(
            f"corrupt PNG: PLTE chunk holds {len(cdata) // 3} entries ({len(cdata)} bytes), "
            f"above the {_MAX_PALETTE_BYTES // 3}-entry maximum an 8-bit index can name"
        )
    return cdata


def _validate_trns(cdata: bytes, color_type: int, palette: bytes | None) -> bytes:
    """Check a tRNS payload against the color type it is qualifying.

    Color types 0 and 2 carry a single color key (one 16-bit field per
    channel); color type 3 carries one alpha byte per palette entry and may
    be *shorter* than PLTE but never longer. Types 4 and 6 already have an
    alpha channel, so tRNS on them is a spec violation.
    """
    if color_type in (4, 6):
        raise PngCorruptError(
            f"corrupt PNG: tRNS chunk is not allowed on color type {color_type}, which already carries an alpha channel"
        )
    if color_type == 0 and len(cdata) != 2:
        raise PngCorruptError(f"corrupt PNG: tRNS chunk on grayscale (color type 0) must be 2 bytes, got {len(cdata)}")
    if color_type == 2 and len(cdata) != 6:
        raise PngCorruptError(f"corrupt PNG: tRNS chunk on truecolor (color type 2) must be 6 bytes, got {len(cdata)}")
    if color_type == 3:
        if palette is None:
            raise PngCorruptError("corrupt PNG: tRNS chunk on an indexed-color image that has no PLTE chunk")
        entries = len(palette) // 3
        if len(cdata) > entries:
            raise PngCorruptError(
                f"corrupt PNG: tRNS chunk holds {len(cdata)} alpha entries for a {entries}-entry palette"
            )
    return cdata


def _inflate_idat(idat_parts: list[bytes], expected: int, width: int, height: int) -> bytes:
    """Inflate the IDAT stream, refusing anything but exactly `expected` bytes.

    Bounded by construction: the inflater is capped at ``expected + 1`` bytes
    of output, so a decompression bomb (a ~1 KB IDAT that expands to
    gigabytes under a 1x1 IHDR) is stopped at the cap instead of being
    allocated and only noticed afterwards. Both directions are errors — too
    much output means the header and the data disagree, too little means the
    stream is truncated — as does an inflater that never reached the end of
    the zlib stream, which is how a missing or damaged Adler-32 trailer
    surfaces here.
    """
    # Multiple IDAT chunks are one logical zlib stream split across chunk
    # boundaries (PNG spec §4.3) -- concatenate before inflating.
    stream = b"".join(idat_parts)
    inflater = zlib.decompressobj()
    try:
        decompressed = inflater.decompress(stream, expected + 1)
    except zlib.error as exc:
        raise PngCorruptError(f"corrupt PNG: failed to decompress image data ({exc})") from exc
    if len(decompressed) > expected:
        raise PngCorruptError(
            f"corrupt PNG: image data inflates past the {expected} bytes a {width}x{height} image "
            "needs (decompression bomb, or an IHDR that disagrees with the image data)"
        )
    if len(decompressed) < expected:
        raise PngCorruptError(
            f"corrupt PNG: image data inflates to {len(decompressed)} bytes, "
            f"but a {width}x{height} image needs {expected}"
        )
    if not inflater.eof:
        raise PngCorruptError("corrupt PNG: image data ends mid-zlib-stream (truncated or damaged IDAT)")
    return decompressed


def read_png(data: bytes) -> Image:
    """Decode PNG bytes into an :class:`Image`.

    Supports bit depths 8 and 16, color types 0/2/3/4/6, tRNS transparency,
    and filters 0-4. Raises :class:`PngCorruptError` for damaged bytes (CRC
    mismatch, malformed IHDR/PLTE/tRNS, truncated chunk, missing IEND, image
    data of the wrong inflated length) and plain :class:`PngError` for a
    well-formed PNG using a feature outside that set (interlacing, bit depths
    1/2/4, an unknown color type or critical chunk, an absurdly large image).
    """
    if data[:8] != _SIGNATURE:
        raise PngError("not a PNG file (bad 8-byte signature)")

    width = height = bit_depth = color_type = None
    interlace = 0
    palette: bytes | None = None
    transparency: bytes | None = None
    idat_parts: list[bytes] = []
    saw_iend = False
    chunk_index = 0

    pos, n = 8, len(data)
    while pos + 8 <= n:
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        ctype = data[pos + 4 : pos + 8]
        cstart = pos + 8
        cend = cstart + length
        if cend + 4 > n:
            raise PngCorruptError(
                f"corrupt PNG: truncated {ctype!r} chunk (declares {length} bytes of data "
                f"plus a CRC, but only {n - cstart} bytes remain in the file)"
            )
        cdata = data[cstart:cend]
        (stored_crc,) = struct.unpack(">I", data[cend : cend + 4])
        computed_crc = zlib.crc32(ctype + cdata) & 0xFFFFFFFF
        if stored_crc != computed_crc:
            raise PngCorruptError(
                f"corrupt PNG: CRC-32 mismatch in {ctype!r} chunk "
                f"(stored {stored_crc:#010x}, computed {computed_crc:#010x})"
            )
        pos = cend + 4

        # IHDR must be the first chunk (PNG spec §5.6): everything else is
        # interpreted relative to it, so a file that leads with anything else
        # is damaged, not merely unusual.
        if chunk_index == 0 and ctype != b"IHDR":
            raise PngCorruptError(f"corrupt PNG: first chunk must be IHDR, got {ctype!r}")
        chunk_index += 1

        if ctype == b"IHDR":
            if width is not None:
                raise PngCorruptError("corrupt PNG: more than one IHDR chunk")
            width, height, bit_depth, color_type, interlace = _parse_ihdr(cdata)
        elif ctype == b"PLTE":
            if palette is not None:
                raise PngCorruptError("corrupt PNG: more than one PLTE chunk")
            palette = _validate_plte(cdata)
        elif ctype == b"tRNS":
            if transparency is not None:
                raise PngCorruptError("corrupt PNG: more than one tRNS chunk")
            transparency = cdata  # length depends on color type; validated below
        elif ctype == b"IDAT":
            idat_parts.append(cdata)
        elif ctype == b"IEND":
            saw_iend = True
            break
        elif not ctype[0] & 0x20:
            # Bit 5 clear in the first byte == uppercase == a CRITICAL chunk.
            # The spec forbids ignoring one you don't understand -- it can
            # change how the image data is to be read -- so this is a hard
            # error, while unknown *ancillary* chunks (gAMA, pHYs, tEXt, ...)
            # fall off the end of this chain and are skipped, as they should be.
            raise PngError(
                f"unsupported critical PNG chunk {ctype!r} -- a decoder must not ignore a "
                "critical chunk it does not understand; re-save the image as a plain PNG, "
                "or install goban-svg[images] to decode it via the Pillow fallback loader."
            )

    if width is None or height is None or bit_depth is None or color_type is None:
        raise PngCorruptError("corrupt PNG: no IHDR chunk")
    if not saw_iend:
        raise PngCorruptError("corrupt PNG: no IEND chunk (file truncated?)")
    if interlace == 1:
        raise PngError(
            "interlaced (Adam7) PNGs are not supported by this codec -- re-save the "
            "image as a non-interlaced PNG, or install goban-svg[images] to decode "
            "it via the Pillow fallback loader."
        )
    if interlace != 0:
        raise PngError(f"unsupported PNG interlace method {interlace}")
    if bit_depth not in (8, 16):
        raise PngError(f"unsupported PNG bit depth {bit_depth} (only 8 and 16 are supported)")
    if color_type not in _CHANNELS:
        raise PngError(f"unsupported PNG color type {color_type}")
    if color_type == 3 and bit_depth != 8:
        raise PngError(f"unsupported PNG: indexed-color (palette) images must be 8-bit, got bit depth {bit_depth}")
    if transparency is not None:
        transparency = _validate_trns(transparency, color_type, palette)
    if width * height > _MAX_PIXELS:
        raise PngError(
            f"unsupported PNG: {width}x{height} is {width * height} pixels, above this codec's "
            f"{_MAX_PIXELS}-pixel limit -- refusing to allocate for it."
        )
    if not idat_parts:
        raise PngCorruptError("corrupt PNG: no IDAT data")

    channels = _CHANNELS[color_type]
    bpp = channels * (2 if bit_depth == 16 else 1)
    row_bytes = width * bpp
    decompressed = _inflate_idat(idat_parts, height * (1 + row_bytes), width, height)

    raw = _unfilter(decompressed, width, height, bpp, row_bytes)
    pixels = _to_rgb_bytes(raw, width, height, color_type, bit_depth, palette, transparency)
    return Image(width=width, height=height, pixels=pixels)


def _chunk(ctype: bytes, cdata: bytes) -> bytes:
    return struct.pack(">I", len(cdata)) + ctype + cdata + struct.pack(">I", zlib.crc32(ctype + cdata))


def write_png(img: Image) -> bytes:
    """Encode an RGB8 :class:`Image` as a PNG.

    Always color type 2 (RGB), bit depth 8, filter type 0 (None) on every
    scanline, zlib level 6 -- this is the painter/preview output path
    (design.md §7), which never needs to produce anything fancier than what
    ``read_png`` can also read back.
    """
    row_bytes = img.width * 3
    raw = bytearray((1 + row_bytes) * img.height)
    for y in range(img.height):
        start = y * (1 + row_bytes) + 1  # leading byte of each scanline stays 0 (filter type None)
        raw[start : start + row_bytes] = img.pixels[y * row_bytes : (y + 1) * row_bytes]
    ihdr = struct.pack(">IIBBBBB", img.width, img.height, 8, 2, 0, 0, 0)
    compressed = zlib.compress(bytes(raw), 6)
    return _SIGNATURE + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", compressed) + _chunk(b"IEND", b"")


def load_image(path: str | Path) -> Image:
    """Load any image file as an RGB8 :class:`Image`.

    Plain PNGs go through this module's own decoder. Anything it can't
    handle -- a non-PNG format, or a PNG *feature* ``read_png`` doesn't
    support (interlacing being the practical case, per G6/G8) -- falls back
    to Pillow's ``PIL.Image.open`` when Pillow is installed (the optional
    ``goban-svg[images]`` extra); otherwise this raises a :class:`PngError`
    that explains why and how to fix it, rather than letting an
    ``ImportError`` leak out to CLI users who never asked for Pillow.

    A :class:`PngCorruptError` is NOT retried through Pillow: Pillow does not
    verify chunk CRCs, so it decodes bytes this codec rejected as damaged.
    Falling back on corruption would quietly undo the integrity check
    ``read_png`` exists to provide -- the caller would get an image built
    from bytes known to be wrong. Corruption re-raises unchanged.
    """
    p = Path(path)
    data = p.read_bytes()
    unsupported: PngError | None = None
    if data[:8] == _SIGNATURE:
        try:
            return read_png(data)
        except PngCorruptError:
            raise  # damaged bytes: never launder them through Pillow
        except PngError as exc:
            unsupported = exc  # a feature we don't implement -- Pillow may well handle it

    try:
        from PIL import Image as _PILImage
    except ImportError as exc:
        # Name what actually went wrong. Without the original message this
        # tells a user whose PNG failed for some reason *other* than
        # interlacing to go re-save a file that was never the problem.
        reason = f" ({unsupported})" if unsupported is not None else ""
        raise PngError(
            f"could not decode {p} with the built-in PNG codec{reason}, and Pillow is not "
            "installed for the fallback loader; re-save as a plain (non-interlaced) "
            "PNG, or install goban-svg[images]."
        ) from (unsupported if unsupported is not None else exc)

    with _PILImage.open(p) as pil_img:
        rgb = pil_img.convert("RGB")
        width, height = rgb.size
        return Image(width=width, height=height, pixels=bytearray(rgb.tobytes()))
