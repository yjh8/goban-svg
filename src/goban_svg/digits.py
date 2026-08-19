"""5x7 bitmap digit font used to stamp and recognize move-number labels.

This module supplies the tiny fixed-width digit font the app-style raster
painter (`render.render_png`) uses to stamp move-number labels onto stones,
and the OCR half that `extract.py` uses to read those labels back off a real
screenshot. The font and the recognizer are deliberately paired: `stamp()`
paints exactly the glyphs `recognize()` was built to match, so the round trip
(paint digit -> threshold pixels -> resample to a 5x7 coverage grid ->
recognize) is lossless for clean synthetic fixtures and tolerant of the
antialiasing / JPEG-ish noise a real screenshot introduces.

Each glyph lives on a 5-column x 7-row grid ("1" = ink, "0" = background) —
see docs/design.md section 6 for the canonical bitmap table (transcribed
verbatim into `TEMPLATES` below; do not hand-edit the bit patterns without
updating that doc too, they are meant to stay in lockstep).

`recognize()` does not do fuzzy image matching itself — it operates purely on
a caller-supplied 5x7 coverage grid (0/1 per cell) and returns the closest
template by Hamming distance, or `None` when the match is unreliable (too far
from every template, or too close to call between the best two). Building
that coverage grid from a screenshot's pixels — finding the glyph bounding
box, resampling it to 5x7, thresholding cell fill — is `extract.py`'s job;
this module only owns the font and the distance metric.
"""

from __future__ import annotations

from collections.abc import Sequence

from goban_svg.png_codec import Image

# The classic 5x7 digit bitmaps, transcribed verbatim from docs/design.md
# section 6. Each value is 7 row-strings of 5 characters ('1' = ink, '0' =
# background), read top-to-bottom. Keep in lockstep with that doc.
TEMPLATES: dict[str, tuple[str, ...]] = {
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00110", "01000", "10000", "11111"),
    "3": ("11111", "00010", "00100", "00010", "00001", "10001", "01110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "01100"),
}

_GLYPH_COLS = 5
_GLYPH_ROWS = 7
_GLYPH_GAP = 1  # inter-glyph gap, in font units, per the interface contract


def _flatten(template: tuple[str, ...]) -> list[int]:
    """Row-major 5x7 -> a flat 35-length 0/1 list, matching recognize()'s cells layout."""
    return [1 if ch == "1" else 0 for row in template for ch in row]


# Precomputed once: avoids re-flattening all ten templates on every recognize() call.
_FLAT_TEMPLATES: dict[str, list[int]] = {digit: _flatten(bits) for digit, bits in TEMPLATES.items()}


def stamp(
    img: Image,
    text: str,
    cx: int,
    cy: int,
    scale: int = 2,
    color: tuple[int, int, int] = (0, 0, 0),
) -> None:
    """Paint `text` (digits only) onto `img`, centered at pixel (cx, cy).

    Each glyph occupies a 5x7 grid of `scale`x`scale` px blocks, with a
    1-unit (i.e. `scale`px) gap between consecutive glyphs. The whole text
    block — width = (5*len(text) + (len(text)-1)) * scale, height = 7*scale —
    is centered on (cx, cy) using floor division, matching the pixel-grid
    nature of the font (there is no meaningful sub-pixel centering here).

    Pixels outside `img`'s bounds are silently skipped, so callers may stamp
    labels near a screenshot's edge without bounds-checking first.
    """
    if not text:
        return
    n = len(text)
    width_units = _GLYPH_COLS * n + _GLYPH_GAP * (n - 1)
    width_px = width_units * scale
    height_px = _GLYPH_ROWS * scale
    x0 = cx - width_px // 2
    y0 = cy - height_px // 2

    for i, ch in enumerate(text):
        template = TEMPLATES.get(ch)
        if template is None:
            raise ValueError(f"stamp(): {ch!r} is not a recognized digit (0-9 only)")
        glyph_x0 = x0 + i * (_GLYPH_COLS + _GLYPH_GAP) * scale
        for row in range(_GLYPH_ROWS):
            bits = template[row]
            for col in range(_GLYPH_COLS):
                if bits[col] != "1":
                    continue
                px0 = glyph_x0 + col * scale
                py0 = y0 + row * scale
                for dy in range(scale):
                    py = py0 + dy
                    if py < 0 or py >= img.height:
                        continue
                    for dx in range(scale):
                        px = px0 + dx
                        if px < 0 or px >= img.width:
                            continue
                        img.set(px, py, color)


def recognize(cells: Sequence[int], *, max_distance: int = 12, min_margin: int = 2) -> str | None:
    """Match a 5x7 coverage grid against `TEMPLATES` by Hamming distance.

    `cells` is 35 values (any truthy/falsy 0/1-ish values), row-major over a
    5-wide x 7-tall grid — one glyph's worth of "is this cell mostly ink"
    booleans, as produced by resampling a screenshot glyph's bounding box
    (see docs/design.md section 6: cell filled if coverage >= 0.35).

    Returns the best-matching digit, or `None` when the match can't be
    trusted: either the best distance exceeds `max_distance` (doesn't look
    like any digit), or the runner-up is within `min_margin` of the best
    (looks like two digits about equally well — genuinely ambiguous, e.g. a
    noise/garbage grid). Never guesses in either case: a rejected glyph is
    meant to surface as a warning upstream, not a silently wrong label.
    """
    if len(cells) != _GLYPH_COLS * _GLYPH_ROWS:
        raise ValueError(f"recognize(): expected {_GLYPH_COLS * _GLYPH_ROWS} cells, got {len(cells)}")
    bits = [1 if c else 0 for c in cells]

    # Rank all ten templates by Hamming distance; tie-break on digit string so
    # the result is deterministic regardless of dict iteration order.
    ranked = sorted(
        ((sum(a != b for a, b in zip(bits, flat, strict=True)), digit) for digit, flat in _FLAT_TEMPLATES.items()),
        key=lambda pair: (pair[0], pair[1]),
    )
    best_distance, best_digit = ranked[0]
    if best_distance > max_distance:
        return None
    runner_up_distance = ranked[1][0]
    if runner_up_distance - best_distance <= min_margin:
        return None
    return best_digit
