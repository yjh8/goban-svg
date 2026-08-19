"""Minimal, dependency-free PNG codec plus the flat-RGB8 :class:`Image` type.

This is the lowest layer of goban-svg's image pipeline (design.md §7): it
imports nothing else from this package, so every module that needs pixels
(``digits``, ``render``, ``extract``) depends on it, never the reverse.

``read_png``/``write_png`` implement just enough of the PNG spec (ISO/IEC
15948) to round-trip the screenshots this tool cares about: bit depths 8 and
16, color types 0/2/3/4/6 (grayscale, RGB, palette, grayscale+alpha, RGBA —
alpha is always dropped, never composited), and filter types 0-4. Interlaced
(Adam7) PNGs are deliberately unsupported — de-interlacing well is real
complexity this tool never needs to produce, so ``read_png`` raises a clear
:class:`PngError` and ``load_image`` falls back to Pillow when it is
installed (the optional ``goban-svg[images]`` extra).
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Image", "PngError", "load_image", "read_png", "write_png"]


class PngError(Exception):
    """Raised for PNG data this codec cannot decode, or malformed PNG input."""


_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# Samples-per-pixel for each supported PNG color type. 1, 5, 7 don't exist in
# the PNG spec; anything not in this map is rejected as unsupported.
_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


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
            raise PngError(f"unsupported PNG filter type {ftype} on scanline {y}")
        out[y * row_bytes : (y + 1) * row_bytes] = row
        prev = row
    return out


def _to_rgb_bytes(
    raw: bytes | bytearray,
    width: int,
    height: int,
    color_type: int,
    bit_depth: int,
    palette: bytes | None,
) -> bytearray:
    """Reshuffle unfiltered PNG sample bytes into flat RGB8.

    Every branch is a slice assignment (or, for palette, a
    ``bytes.translate`` 256-entry lookup) rather than a per-pixel Python
    loop — the unavoidable per-byte cost already went into ``_unfilter``;
    there's no reason to pay it again here. 16-bit samples are big-endian,
    so e.g. ``raw[0::2]`` takes the high byte of each sample — the
    deliberate 16-to-8-bit downsample this codec uses (design.md §7).
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
    elif color_type == 6:  # RGBA -- alpha dropped
        if bit_depth == 8:
            out[0::3] = raw[0::4]
            out[1::3] = raw[1::4]
            out[2::3] = raw[2::4]
        else:
            out[0::3] = raw[0::8]
            out[1::3] = raw[2::8]
            out[2::3] = raw[4::8]
    elif color_type == 0:  # grayscale
        gray = raw[0::2] if bit_depth == 16 else raw
        out[0::3] = gray
        out[1::3] = gray
        out[2::3] = gray
    elif color_type == 4:  # grayscale + alpha -- alpha dropped
        stride = 4 if bit_depth == 16 else 2
        gray = raw[0::stride]
        out[0::3] = gray
        out[1::3] = gray
        out[2::3] = gray
    elif color_type == 3:  # palette (indexed), resolved via PLTE
        if palette is None:
            raise PngError("indexed-color (color type 3) PNG has no PLTE chunk")
        r_table, g_table, b_table = bytearray(256), bytearray(256), bytearray(256)
        for i in range(len(palette) // 3):
            r_table[i], g_table[i], b_table[i] = palette[3 * i], palette[3 * i + 1], palette[3 * i + 2]
        raw_bytes = bytes(raw)
        out[0::3] = raw_bytes.translate(bytes(r_table))
        out[1::3] = raw_bytes.translate(bytes(g_table))
        out[2::3] = raw_bytes.translate(bytes(b_table))
    else:  # pragma: no cover - guarded by the _CHANNELS check in read_png
        raise PngError(f"unsupported PNG color type {color_type}")
    return out


def read_png(data: bytes) -> Image:
    """Decode PNG bytes into an :class:`Image`.

    Supports bit depths 8 and 16, color types 0/2/3/4/6, and filters 0-4.
    Raises :class:`PngError` for interlaced (Adam7) PNGs, missing PLTE on a
    palette image, or any other feature outside that set.
    """
    if data[:8] != _SIGNATURE:
        raise PngError("not a PNG file (bad 8-byte signature)")

    width = height = bit_depth = color_type = None
    interlace = 0
    palette: bytes | None = None
    idat_parts: list[bytes] = []

    pos, n = 8, len(data)
    while pos + 8 <= n:
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        ctype = data[pos + 4 : pos + 8]
        cstart = pos + 8
        cend = cstart + length
        if cend + 4 > n:
            raise PngError(f"truncated {ctype!r} chunk")
        cdata = data[cstart:cend]
        pos = cend + 4  # skip the trailing 4-byte CRC; not verified
        if ctype == b"IHDR":
            width, height, bit_depth, color_type, comp, filt, interlace = struct.unpack(">IIBBBBB", cdata)
            if comp != 0 or filt != 0:
                raise PngError("unsupported PNG compression or filter method")
        elif ctype == b"PLTE":
            palette = cdata
        elif ctype == b"IDAT":
            idat_parts.append(cdata)
        elif ctype == b"IEND":
            break

    if width is None or height is None or bit_depth is None or color_type is None:
        raise PngError("PNG has no IHDR chunk")
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
        raise PngError("indexed-color (palette) PNGs must be 8-bit")
    if not idat_parts:
        raise PngError("PNG has no IDAT data")

    try:
        # Multiple IDAT chunks are one logical zlib stream split across
        # chunk boundaries (PNG spec §4.3) -- concatenate before inflating.
        decompressed = zlib.decompress(b"".join(idat_parts))
    except zlib.error as exc:
        raise PngError(f"failed to decompress PNG image data: {exc}") from exc

    channels = _CHANNELS[color_type]
    bpp = channels * (2 if bit_depth == 16 else 1)
    row_bytes = width * bpp
    if len(decompressed) < height * (1 + row_bytes):
        raise PngError("truncated or corrupt PNG scanline data")

    raw = _unfilter(decompressed, width, height, bpp, row_bytes)
    pixels = _to_rgb_bytes(raw, width, height, color_type, bit_depth, palette)
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
    handle -- a non-PNG format, or a PNG feature ``read_png`` doesn't
    support (interlacing being the practical case, per G6/G8) -- falls back
    to Pillow's ``PIL.Image.open`` when Pillow is installed (the optional
    ``goban-svg[images]`` extra); otherwise this raises a :class:`PngError`
    that explains why and how to fix it, rather than letting an
    ``ImportError`` leak out to CLI users who never asked for Pillow.
    """
    p = Path(path)
    data = p.read_bytes()
    if data[:8] == _SIGNATURE:
        try:
            return read_png(data)
        except PngError:
            pass  # fall through to the Pillow fallback below

    try:
        from PIL import Image as _PILImage
    except ImportError as exc:
        raise PngError(
            f"could not decode {p} with the built-in PNG codec, and Pillow is not "
            "installed for the fallback loader; re-save as a plain (non-interlaced) "
            "PNG, or install goban-svg[images]."
        ) from exc

    with _PILImage.open(p) as pil_img:
        rgb = pil_img.convert("RGB")
        width, height = rgb.size
        return Image(width=width, height=height, pixels=bytearray(rgb.tobytes()))
