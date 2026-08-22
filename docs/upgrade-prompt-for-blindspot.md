# 棋盤辨識引擎更新包 — Merge spec for the 序盤盲區庫 AI

> 給 AI 開發助手的更新提示。序盤盲區庫在 2026-08-20 取用了 goban-svg 的引擎原始碼，
> 之後那份程式又自己改過。這份文件不是「重新整合」，是**把上游修正合併進一份已經
> 分岔的程式**。每一項都給了完整的 FIND / REPLACE 片段和可執行的驗收指令。
>
> A merge spec, not a build spec. The target codebase adopted this engine's sources on
> 2026-08-20 and has since diverged. Apply by TEXT ANCHOR, never by line number.

**Baseline assumed:** the engine as it stood 2026-08-20 (before photo mode existed).
**Source of these hunks:** goban-svg 0.1.1, published and verified 2026-08-22.

---

## §0 — Hard rules (read first)

1. **MUST NOT** replace any file wholesale, or copy a whole file from upstream. The target
   has local modifications that must survive. Apply only the hunks below.
2. **MUST** locate every change by the FIND text given, not by line number. Line numbers in
   the target will not match.
3. **MUST NOT** reformat, re-wrap, re-order imports, or "tidy" any code you touch. Change
   exactly the characters shown.
4. **MUST NOT** alter any warning or error string other than the one **§3/B2** explicitly
   changes. §2 adds a new message but rewrites none.
   Downstream code matches these strings with regexes.
5. **MUST** run §6 verification after applying, and report the actual output. Do not report
   success without running it.
6. If a FIND block does not appear **exactly once** in the target file, **STOP** and report
   which file and which block. Do not guess at a fuzzy match.
7. **MUST** copy the whole `goban_svg/` package directory somewhere safe *before* editing
   anything. §6's regression check compares against it, and it is your only clean undo.

---

## §1 — Confirm the baseline before changing anything

Run this first. All four lines must print `OK`.

```bash
cd <the directory containing the goban_svg package>
grep -q 'return next((ch for ch in text if ord(ch) < 0x20), None)' goban_svg/board.py && echo "OK board.py control-char" || echo "MISMATCH board.py control-char"
grep -q 'return cls.from_json_dict(json.loads(text))' goban_svg/board.py && echo "OK board.py from_json" || echo "MISMATCH board.py from_json"
grep -q 'rgb = pil_img.convert("RGB")' goban_svg/png_codec.py && echo "OK png_codec.py" || echo "MISMATCH png_codec.py"
grep -q 'return ExtractionResult(position=position, grid=grid, warnings=warnings)' goban_svg/extract.py && echo "OK extract.py" || echo "MISMATCH extract.py"
```

Any `MISMATCH` means that specific change was already applied, or that area was modified
locally. **Report it and stop** — do not force the change.

---

## §2 — Change A: reject duplicate JSON keys  ·  `goban_svg/board.py`  ·  REQUIRED

**Why.** `Position.from_json()` used plain `json.loads()`. JSON keeps only the last value for
a repeated key, so `{"black": ["B2"], "black": ["C3"]}` parses "successfully" with **B2
silently deleted** — before any validation runs. The user sees a board missing a stone and
gets no error. (Verified: on the 2026-08-20 baseline that input yields only C3.)

### A1 — add the hook

**FIND** (the last line of the existing `_control_char` function).

> ⚠ **ORDER IS MANDATORY: if you are taking both Change A and Change B, apply A1 BEFORE B1.**
> §3's B1 rewrites this exact `return` line as part of rewriting the whole function. If B1
> has already run, the FIND text below appears **0 times** — and that 0-match does **not**
> mean "already applied" (see §8). It means you went out of order. Undo B1, apply A1, then
> re-apply B1. In the correct order both hunks apply cleanly by literal text match.

```python
    return next((ch for ch in text if ord(ch) < 0x20), None)
```

**REPLACE WITH** — the same line, then a blank-line-separated new function:

```python
    return next((ch for ch in text if ord(ch) < 0x20), None)


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """json object hook: a repeated key is an error, not a silent last-wins.

    ``{"black": ["C3"], "black": ["D4"]}`` is valid JSON, and plain json.loads
    would keep only the second bucket -- discarding stones before validation
    ever sees them.
    """
    seen: dict[str, object] = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError(f"duplicate key {key!r} in JSON object -- board data would be silently discarded")
        seen[key] = value
    return seen
```

### A2 — wire it in

**FIND:**

```python
        return cls.from_json_dict(json.loads(text))
```

**REPLACE WITH:**

```python
        return cls.from_json_dict(json.loads(text, object_pairs_hook=_no_duplicate_keys))
```

The hook applies recursively to **every** object in the document, not just the top level.

---

## §3 — Change B: reject lone UTF-16 surrogates in labels  ·  `goban_svg/board.py`  ·  REQUIRED

**Why.** The XML-safety check only rejected C0 control characters. A lone UTF-16 surrogate
(U+D800–U+DFFF) is a legal Python character but illegal in XML. It passed validation and
reached `render_svg()`. In a browser's `Blob`/`TextEncoder` path it is then **silently
rewritten to U+FFFD** — the downloaded SVG differs from the JSON that produced it, with no
error anywhere. (Verified on the baseline: `render_svg()` succeeds and the result cannot be
UTF-8 encoded.)

### B1 — widen the check

**FIND:**

```python
def _control_char(text: str) -> str | None:
    """First C0 control character in `text` (ord < 0x20), or None if there is none."""
    return next((ch for ch in text if ord(ch) < 0x20), None)
```

**REPLACE WITH:**

```python
def _control_char(text: str) -> str | None:
    """First character in `text` that XML cannot carry, or None.

    C0 controls (ord < 0x20) plus lone UTF-16 surrogates (U+D800-U+DFFF). The
    surrogates matter because they survive Python and reach render_svg, but a
    browser's Blob/TextEncoder silently rewrites them to U+FFFD -- the SVG the
    user downloads would differ from the JSON that produced it, with no error
    anywhere.
    """
    return next((ch for ch in text if ord(ch) < 0x20 or 0xD800 <= ord(ch) <= 0xDFFF), None)
```

### B2 — generalize the message (a surrogate is not a "control character")

**FIND:**

```python
                    f"label at {point.notation()} contains control character U+{ord(control):04X} "
```

**REPLACE WITH:**

```python
                    f"label at {point.notation()} contains character U+{ord(control):04X} "
```

> This is the **only** string this spec changes. It is not one of the frozen
> `extract_position` warning strings; it is a `ValueError` message, surfaced through the
> generic invalid-JSON path rather than matched by a regex.

---

## §4 — Change C: honour EXIF orientation  ·  `goban_svg/png_codec.py`  ·  REQUIRED if you accept photos

**Why.** Phone cameras store JPEGs in the sensor's native rotation plus an EXIF orientation
tag; viewers apply the tag, `PIL.Image.open()` does not. So a photo that looks upright to the
user was loaded sideways, and any coordinates supplied in **display** orientation addressed a
differently rotated raster. This matters for any phone-captured image — including someone
photographing a screen, which is a real observed usage.

**FIND:**

```python
    with _PILImage.open(p) as pil_img:
        rgb = pil_img.convert("RGB")
```

**REPLACE WITH:**

```python
    from PIL import ImageOps as _PILImageOps

    with _PILImage.open(p) as pil_img:
        # Apply the EXIF orientation tag first: phone JPEGs are stored rotated
        # with a display hint, and corner coordinates are given in DISPLAY
        # orientation -- without this, corners address a differently rotated raster.
        upright = _PILImageOps.exif_transpose(pil_img) or pil_img
        rgb = upright.convert("RGB")
```

Screenshots (PNG) carry no EXIF orientation and are unaffected.

---

## §5 — Change D: point-addressed review points  ·  `goban_svg/extract.py`  ·  OPTIONAL

**Why.** `extract_position()` already reports doubt as prose warnings that name a point. This
adds a parallel machine-readable list so a UI can ring the exact intersection instead of
regex-ing the sentence. Warning strings are unchanged — this rides alongside them.

Take this only if you want that capability. It is self-contained; skipping it changes nothing.

### D1 — import `field`

> ⚠ **Scope this edit to `goban_svg/extract.py` only.** The line below also appears in
> `png_codec.py`. It occurs exactly once *within extract.py*, but a project-wide
> search-and-replace would edit `png_codec.py` too, leaving an unused import there and
> violating §0 rule 1.

**FIND** (in `goban_svg/extract.py`): `from dataclasses import dataclass`
**REPLACE WITH:** `from dataclasses import dataclass, field`

### D2 — export the new type

**FIND:**

```python
__all__ = ["ExtractionError", "ExtractionResult", "GridFit", "extract_position"]
```

**REPLACE WITH:**

```python
__all__ = ["ExtractionError", "ExtractionResult", "GridFit", "UncertainPoint", "extract_position"]
```

### D3a — add the type and the helper

**FIND** — this exact **two-line** decorator+class pair, which occurs once. The bare
`@dataclass` decorator alone is **NOT** a safe anchor: it occurs three times in this file
(above `GridFit`, above `ExtractionResult`, and above `_Pixels`).

```python
@dataclass
class ExtractionResult:
```

**REPLACE WITH** — the new type and helper, then the original two lines unchanged:

```python
@dataclass(frozen=True)
class UncertainPoint:
    """One doubt, addressed to a specific point, in machine-readable form.

    The same doubt the matching ``warnings`` entry states in prose, with its point
    and reason already parsed out. ``kind`` is ``"ambiguous-color"`` or
    ``"unreadable-label"`` for screenshot extraction.
    """

    point: Point
    kind: str


def _note_uncertain(entries: list[UncertainPoint], seen: set[tuple[Point, str]], point: Point, kind: str) -> None:
    """Record one review point, suppressing an exact ``(point, kind)`` repeat."""
    key = (point, kind)
    if key in seen:
        return
    seen.add(key)
    entries.append(UncertainPoint(point=point, kind=kind))


@dataclass
class ExtractionResult:
```

### D3b — add the new field

**FIND** (the three fields of `ExtractionResult`; occurs once):

```python
    position: Position
    grid: GridFit
    warnings: list[str]
```

**REPLACE WITH:**

```python
    position: Position
    grid: GridFit
    warnings: list[str]
    uncertain: list[UncertainPoint] = field(default_factory=list)
    """Point-addressed mirror of the point-naming ``warnings``, same scan order.

    Appended with a default, so every existing three-positional-argument
    construction keeps working. Not every warning has an entry, and absence of an
    entry is NOT a confidence claim.
    """
```

### D4 — initialise the accumulators

**FIND:**

```python
    position = Position(size=size)
```

**REPLACE WITH:**

```python
    position = Position(size=size)
    uncertain: list[UncertainPoint] = []
    seen: set[tuple[Point, str]] = set()
```

### D5 — the two record sites

`warnings.append(warning)` appears **twice** in this function. Disambiguate by the line that
follows each.

**Site 1 — FIND** (followed by the wedge call):

```python
                    warnings.append(warning)
                wedge = _detect_wedge(px, cx, cy, d, color)
```

**REPLACE WITH:**

```python
                    warnings.append(warning)
                    _note_uncertain(uncertain, seen, point, "ambiguous-color")
                wedge = _detect_wedge(px, cx, cy, d, color)
```

**Site 2 — FIND** (followed by the `elif`):

```python
                    warnings.append(warning)
            elif non_wood >= MARK_MIN_NONWOOD:
```

**REPLACE WITH:**

```python
                    warnings.append(warning)
                    _note_uncertain(uncertain, seen, point, "unreadable-label")
            elif non_wood >= MARK_MIN_NONWOOD:
```

### D6 — return it

**FIND:**

```python
    return ExtractionResult(position=position, grid=grid, warnings=warnings)
```

**REPLACE WITH:**

```python
    return ExtractionResult(position=position, grid=grid, warnings=warnings, uncertain=uncertain)
```

---

## §6 — Verification (run it; report the real output)

Save as `verify_upgrade.py` beside the `goban_svg` package and run `python3 verify_upgrade.py`.

```python
from goban_svg.board import Position, Point
from goban_svg.render import render_svg

DUP = '{"size":9,"stones":{"black":["B2"],"black":["C3"]},"marks":[],"labels":{}}'
print("A duplicate-key:", end=" ")
try:
    pos = Position.from_json(DUP)
    print("STILL BROKEN - accepted, stones now:", sorted(p.notation() for p in pos.stones))
except ValueError as e:
    print("FIXED -", e)

print("B lone-surrogate:", end=" ")
try:
    p = Position(size=9)
    p.labels[Point(2, 2)] = "A\ud800B"
    svg = render_svg(p)
    try:
        svg.encode("utf-8")
        print("STILL BROKEN - rendered and encoded")
    except UnicodeEncodeError:
        print("STILL BROKEN - rendered, but the SVG cannot be UTF-8 encoded")
except ValueError as e:
    print("FIXED -", e)
```

**If you applied §4 (EXIF), also run this** — a PNG can never exercise the EXIF code path
(`read_png` handles PNGs by signature; only non-PNG data reaches Pillow), so the screenshot
regression check below cannot detect a broken §4. This one can:

```python
import io, os, tempfile
from PIL import Image as _PILImage
from goban_svg.png_codec import load_image

print("C EXIF-orientation:", end=" ")
src = _PILImage.new("RGB", (40, 20), (255, 0, 0))
exif = _PILImage.Exif()
exif[0x0112] = 6  # Orientation 6: display upright requires a 90-degree rotation
buf = io.BytesIO()
src.save(buf, format="JPEG", exif=exif)
d = tempfile.mkdtemp()
path = os.path.join(d, "_exif_check.jpg")
with open(path, "wb") as f:
    f.write(buf.getvalue())
img = load_image(path)
print(
    "STILL BROKEN - loaded %dx%d, EXIF ignored" % (img.width, img.height)
    if (img.width, img.height) == (40, 20)
    else "FIXED - loaded %dx%d, EXIF applied" % (img.width, img.height)
)
os.remove(path)
os.rmdir(d)
```

Expected: `C EXIF-orientation: FIXED - loaded 20x40, EXIF applied`

**Expected after §2 and §3:**

```
A duplicate-key: FIXED - duplicate key 'black' in JSON object -- board data would be silently discarded
B lone-surrogate: FIXED - label at B2 contains character U+D800 (not expressible in XML): 'A\ud800B'
```

> The script above imports `goban_svg.render`, which §7 says not to modify — it must still be
> present and importable for the verification to run.

**Then confirm nothing else moved.** This check needs no fixture image — the engine paints
its own test board and must read it back exactly:

```python
from goban_svg.board import Position, Point, Mark
from goban_svg.render import render_png
from goban_svg.extract import extract_position

p = Position(size=9)
for name, colour in (("C3", "black"), ("G7", "white"), ("E5", "black"), ("C7", "white")):
    x = ord(name[0]) - ord("A")
    x -= 1 if name[0] > "I" else 0  # Go boards skip the letter I
    p.stones[Point(x, int(name[1:]) - 1)] = colour
p.marks[Point(4, 2)] = Mark(type="square", color="black")
p.labels[Point(6, 6)] = "12"

result = extract_position(render_png(p))
print(
    "D round-trip:", "OK - painted board recognised exactly" if result.position.to_json() == p.to_json() else "MISMATCH"
)
print("   warnings:", result.warnings)
```

Expected, before and after the merge alike:

```
D round-trip: OK - painted board recognised exactly
   warnings: []
```

A `MISMATCH`, or any warning appearing here, means recognition changed — which none of these
changes should cause. Stop and report it.

If you also have a real screenshot with a known-good result, run it through both the edited
package and the untouched backup from §0 rule 7 and diff the position JSON and warnings; they
must be identical. If you have neither, the round-trip above is sufficient. Run the target's
own test suite too if it has one — this spec does not assume tests live inside the package
directory. They must be **identical** — these changes add
validation and optional data; they do not change what gets recognised.

If §5 was applied, also check `ExtractionResult(position, grid, warnings)` still constructs
with three positional arguments (the new field is defaulted).

---

## §7 — What NOT to take

- **Do not** copy `photo.py`, `cli.py`, or `__init__.py` from upstream. `photo.py` is the
  physical-board photo engine and depends on the §5 additions plus its own module; the CLI
  changes are irrelevant to a web integration; `__init__.py` only carries a version string
  that would then misdescribe this codebase.
- **Do not** take `render.py`, `sgf.py`, or `digits.py` — they have **not changed at all**
  since 2026-08-20. Any difference you see there is a local modification worth keeping.

---

## §8 — Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| §2 A1's FIND appears 0 times **and you already applied §3/B1** | out-of-order: B1 rewrote A1's anchor line | Undo B1, apply A1, re-apply B1. Do **not** read this as "already applied" |
| A FIND block appears 0 times (any other case) | already applied, or locally rewritten | Stop; report the file and block |
| `Position.from_json()` raises `NameError: _no_duplicate_keys` | A2 was applied but A1 was skipped — usually the out-of-order trap above | Apply A1; re-run §6 |
| A FIND block appears more than once | the anchor was shortened | Use the full block including its following line, as printed |
| `NameError: _no_duplicate_keys` | A2 applied without A1 | Apply A1 |
| `NameError: field` | D3 applied without D1 | Apply D1 |
| `NameError: UncertainPoint` | D4/D6 applied before D3 | Apply D3 first |
| `ImportError: ImageOps` | Pillow too old | `ImageOps.exif_transpose` needs Pillow ≥ 6.0 |
| A previously-working JSON file now raises `duplicate key` | that file really does have a repeated key and was silently losing data | Fix the file; the error is correct |

---

## §9 — Provenance

Hunks taken from goban-svg 0.1.1, verified against the published wheel
(`sha256 ce7b3cb1…`, 80,727 bytes) on 2026-08-22. Every "verified" claim in this document was
produced by executing both engine versions side by side, not by reading code. Questions, or
a wrong conversion (send the original image) → Joseph.
