# Design — goban-svg (canonical)

> Provenance: this is the design handoff written by the prior Claude Code cloud session (2026-08), committed verbatim below as the project's canonical design. Decisions marked "locked" were made with Joseph's context; don't re-litigate. Interface-level signatures live in docs/interfaces.md.

# Starter prompt — build `goban-svg`, a Go-board screenshot → SVG converter

You are Claude Code in a fresh local folder that will become the `goban-svg` repository.
Your job: build a small, dependency-free Python tool that converts screenshots of Go
(圍棋/baduk) board positions into clean SVG diagrams, then run it on Joseph's three
screenshots and commit the results as examples.

This prompt is a handoff from a previous Claude Code (remote) session. That session did
the design work, environment probing, and de-risking, but deliberately did not write the
code, because the screenshots only exist on Joseph's machine — a local session can test
against the real inputs. Everything below is decided and verified reasoning; you can
start building immediately. Where this prompt says "locked", treat it as a decision
already made with Joseph's context in mind — don't re-litigate, but do flag genuine
blockers.

---

## 1. First: get the inputs

Ask Joseph to drop the three Go screenshots into `screenshots/` in this folder (any
filenames, e.g. `board-1.png`, `board-2.png`, `board-3.png`). They are screenshots from a
Go app (~950×950 px, 19×19 board, wood background, dark UI frame around the board).
**Do not start guessing positions from memory or from pasted images — the whole point of
doing this locally is that the extractor reads the real files.**

What the three screenshots contain (so you know what features must work):

- **Numbered move labels** on stones: white ① , black ② , white ③ in image 1; black ① in image 2.
- **Corner "wedge" badges** — a small colored triangle tucked into the top-left corner of
  a stone's cell (the app's last-move/highlight marker): blue on white ③ (image 1), red
  on black ① (image 2), white wedge on a black stone (image 3).
- **Solid square markers on empty points**: a black filled square (image 1, lower left),
  a white filled square (images 2 and 3, on open areas — likely a "whose turn" marker).
- Full-board mid-game positions: ~30–60 stones each, including contact fights, stones on
  edge lines, and long walls (image 3 has a huge right-side pushing battle).

## 2. Repo bootstrap

1. `git init -b main` in this folder.
2. Create the GitHub repo (the remote session could NOT do this — the Claude GitHub App
   returned 403 for repo creation, which is why the work moved here). Preferred:
   `gh repo create yjh8/goban-svg --private --source=. --push` once there's a first
   commit; otherwise Joseph creates it at github.com/new and you `git remote add origin`.
   Name suggestion: **goban-svg** (Joseph approved creating a new repo; any name he
   prefers is fine).
3. Work on a feature branch and open a PR if Joseph wants review; direct-to-main is
   acceptable for the initial import if he says so.
4. Note for later: if Joseph wants claude.ai web sessions to see this repo, he must grant
   the Claude GitHub App access to it (claude.ai Settings → Connectors / the app's
   repository list).

## 3. Locked architecture decisions

1. **Pure stdlib Python, zero runtime dependencies.** Verified feasible: PNG decode/encode
   is ~150 lines over `zlib`; all image analysis works on plain `bytearray` rasters. This
   makes the tool runnable by any Python ≥3.10 on any of Joseph's Macs with no venv
   ceremony. Pillow is an *optional* extra (`goban-svg[images]`) used only as a fallback
   loader for non-PNG/exotic inputs (JPEG, WebP, interlaced PNG).
2. **Three-stage pipeline with a human-editable intermediate:**
   `screenshot → Position (JSON, faithful) → SVG`, plus **SGF export** for interop.
   JSON is the faithful intermediate (it keeps mark colors, which SGF cannot express);
   SGF is lossy-but-standard (TR/SQ/CR/MA/LB/AB/AW/SZ). If OCR misreads a label, the user
   edits the JSON and re-renders — that's the designed correction loop.
3. **`src/` layout, package `goban_svg`**, console script `goban-svg` via
   `[project.scripts]`. Build backend hatchling. `requires-python >= 3.10`.
4. Joseph's environment conventions: he is a uv shop (see supertitle D-220) — document
   commands as `uv run goban-svg …` / `uv tool install .`; never bare `pip install` in
   docs. Dev tooling: pytest + ruff (line-length 120, select E,F,I,UP,B,SIM, ignore E501).
   Add a minimal GitHub Actions CI (ubuntu, Python 3.10 + 3.13 matrix: ruff check, ruff
   format --check, pytest).

## 4. Package layout

```
src/goban_svg/
  __init__.py    # __version__, re-export Position, render_svg, extract_position
  __main__.py    # python -m goban_svg
  board.py       # Point, Mark, Position, star_points(), JSON (de)serialization, ascii_diagram()
  png_codec.py   # stdlib PNG read/write + Image (flat bytearray RGB8) + load_image() w/ Pillow fallback
  digits.py      # 5x7 bitmap digit templates: stamp() for painting, recognize() for OCR
  sgf.py         # minimal static-position SGF read/write
  render.py      # Position → SVG (the polished output) AND Position → PNG (app-style raster painter)
  extract.py     # screenshot Image → ExtractionResult(Position, GridFit, warnings)
  cli.py         # argparse: convert / extract / render
tests/           # pytest; see §9
examples/        # the three converted boards: board-N.png (original), board-N.json, board-N.svg (+ .sgf)
```

### board.py model

- `Point(col, row)` frozen dataclass, 1-based; col 1 = `A` (left), row 1 = bottom.
  Notation `"D17"`, columns `"ABCDEFGHJKLMNOPQRST…"` — **skip I** (Go convention).
  `Point.sgf(size)` → `"dc"` style (SGF y counts from top: `sy = size - row`).
- `Mark(type, color)` — type ∈ triangle|square|circle|cross; color is `"black"`,
  `"white"`, `"#rrggbb"`, or None (auto-contrast at render time).
- `Position(size=19, stones: dict[Point,str], marks: dict[Point,Mark], labels: dict[Point,str])`.
- JSON schema (the interchange format — document it in the README):

```json
{
  "size": 19,
  "stones": {"black": ["C15", "..."], "white": ["D17", "..."]},
  "marks": [{"point": "D3", "type": "square", "color": "black"},
            {"point": "D14", "type": "triangle", "color": "#2b5fe3"}],
  "labels": {"D14": "3"}
}
```

## 5. Rendering spec (render.py)

Two renderers sharing one `BoardGeometry` (cell size `c`, wood margin `0.72c` beyond the
outer lines, optional coordinate gutters left+bottom):

**SVG (the deliverable):** flat wood `#e6c37a`; lines `#43361f` (inner width `0.038c`,
outer border `0.066c`); hoshi r `0.105c`; stones r `0.47c` with subtle radialGradients
(black `#6f6f6f→#2a2a2a→#000`, cx/cy ≈ 36%/32%; white `#fff→#f2f2f2→#d4d4d4` + stroke
`#444`); labels auto-contrast (near-white text on black stones, near-black on white),
font-size `0.52c`/`0.46c`/`0.36c` for 1/2/3 chars, `text-anchor=middle` + `dy≈0.35em`,
generic sans stack. Marks: triangle = centered outline triangle on the stone in the
mark's color (this is how the app's corner wedge is represented in the clean diagram);
square-on-empty = **filled** small square (half-width `0.21c`) in its recorded
black/white color (white squares get a `#444` stroke so they read on wood); hollow marks
on empty points get a wood-colored backing disc first so grid lines don't cross them.
Optional `--coords` (letters bottom, numbers left, skip I). Escape label text for XML.

**PNG painter (`render_png`)** — deliberately mimics the *app's* raster style (corner
wedges as actual corner triangles, solid squares, stamped digit labels via
`digits.stamp`, optional deterministic ±noise). It has two jobs: (a) synthetic fixtures
for extractor round-trip tests, (b) quick previews. Takes a `Palette` dataclass so tests
can vary wood/stone colors (default wood ≈ `(231,196,122)`, KGS-ish `(220,179,92)`).

## 6. Extraction algorithm (extract.py) — the worked-out design

Precompute per-pixel boolean maps in one pass (lum = `(299r+587g+114b)//1000`):

- `wood`: `r ≥ 140 and (r−b) ≥ 25 and (g−b) ≥ 5 and lum ≥ 100`
- `dark`: `lum < 110`

**Board bbox:** per-column/per-row wood counts; keep the longest contiguous run above
`max(4, 0.15·max_count)`. (Threshold must be LOW: a column through a long stone wall can
drop to ~30% of an empty column's wood count — image 3 has exactly this.)

**Grid lines:** per-axis "line-ness" projection inside the bbox: count dark pixels that
have wood within ±4 px perpendicular (thin-dark-on-wood ⇒ grid line; stones don't
qualify). Smooth (window 3), find local maxima ≥ `0.18·max`, non-max-suppress within
6 px. Fit a uniform grid robustly: median of consecutive peak gaps (filtered to
[0.7,1.3]×median) → assign integer line indices → least-squares fit `p ≈ a + k·d` →
reject residuals > `0.25d` → refit. Extend `k` to the bbox but trim any extrapolated
outermost line that has no peak within `0.35d` of it (guards against wide-margin
phantoms). Board size N = line count (warn if not 9/13/19; error with a clear message if
Nx ≠ Ny, e.g. a cropped screenshot).

**Intersection classification** at each grid point (spacing `d`):

- Disc sample: pixels within radius `0.20d` → **median** luminance (median, not mean —
  digit labels and specular highlights must not flip the color call) + non-wood fraction.
- Ring sample: 16 angles at radius `0.36d`, each a 3×3 mean. A ring sample is "stone-ish"
  if dark (lum<115) or bright-and-neutral (lum≥160 **and |r−b|<45** — the neutrality
  check is load-bearing, see gotcha G1). Stone ⇔ ≥ ~72% of in-bounds ring samples
  stone-ish; color from disc median (<118 black, >150 white, else decide by ring median +
  warning).
- Not a stone but disc non-wood fraction ≥ 0.55 ⇒ **solid mark on an empty point**
  (record as `square`, color black/white by disc median). Hoshi dots and line crossings
  stay well under this fraction — verified geometrically (a 4 px star dot is ~16% of a
  10 px-radius disc).

**Corner wedge badges (→ triangle marks):** the wedge lives at the *cell corner*, partly
beyond the stone's radius (`0.5d·√2 ≈ 0.71d` from center vs stone r `0.47d`). For each of
the stone's 4 corners, sample a patch at diagonal offset `0.33d` (radius `0.10d`) and
classify **per-pixel** (never by patch mean — partial coverage dilutes means):
blue `b−r>50, b>120`; red `r−g>80, r>140`; white-badge (on black stones)
`lum>195 and |r−b|<45`; black-badge (on white stones) `lum<55`. Wedge ⇔ ≥40% of one
class. Then confirm with an inner probe at `0.23d` diagonal (the app's wedge overlaps the
stone's rim) — this disambiguates which of two diagonally-adjacent stones owns a wedge at
their shared cell corner. Canonical recorded colors: blue `#2b5fe3`, red `#e03c3c`,
`"white"`, `"black"` (share these constants with the renderer).

**Label OCR:** on-stone contrast mask in a centered square of half-width `0.30d`
(black stones: lum ≥ 180; white stones: lum ≤ 90). If a wedge was found at corner
(sx,sy), exclude pixels with `sx·dx>0, sy·dy>0, sx·dx+sy·dy > 0.40d` (a white wedge's
inner tip can otherwise leak into the mask). Split the mask into glyphs at empty columns,
drop <8 px specks; per glyph: if width/height < 0.34 ⇒ `"1"`, else resample the bbox to a
5×7 coverage grid (cell ≥ 0.35 filled) and Hamming-match against `digits.TEMPLATES`;
reject if best distance > ~12 or the runner-up is within 2 — a rejected glyph fails the
whole label with a warning naming the point (the user fixes the JSON; never emit a
guessed label silently). Classic 5×7 digit bitmaps:

```
0: 01110 10001 10011 10101 11001 10001 01110      5: 11111 10000 11110 00001 00001 10001 01110
1: 00100 01100 00100 00100 00100 00100 01110      6: 00110 01000 10000 11110 10001 10001 01110
2: 01110 10001 00001 00110 01000 10000 11111      7: 11111 00001 00010 00100 01000 01000 01000
3: 11111 00010 00100 00010 00001 10001 01110      8: 01110 10001 10001 01110 10001 10001 01110
4: 00010 00110 01010 10010 11111 00010 00010      9: 01110 10001 10001 01111 00001 00010 01100
```

Return `ExtractionResult(position, grid_fit, warnings)`; print warnings to stderr in the
CLI. Expected runtime a few seconds per ~950px screenshot in pure Python — fine.

## 7. PNG codec (png_codec.py)

- `Image`: `width/height` + flat `bytearray` RGB8, `get/set/fill`.
- `read_png`: chunk walk; support bit depth 8 & 16 (16 → high byte), color types
  0/2/3/4/6 (palette via PLTE; alpha dropped), filters 0–4 with correct `bpp` offsets
  (Sub/Up/Average/Paeth — Paeth tie-break order a,b,c). Raise a clear `PngError` for
  interlaced (Adam7) with "re-save or install pillow" guidance; `load_image()` falls back
  to Pillow when available (`PIL.Image.open(...).convert("RGB")`).
- `write_png`: RGB8, filter 0, zlib level 6 — used by the painter/preview.
- Fast paths: use slice assignment for channel shuffles (`px[0::3] = raw[0::4]` etc.);
  only the per-byte unfilter loops need to be scalar.

## 8. CLI (cli.py)

```
goban-svg convert  IMAGE [-o OUT.svg] [--json PATH] [--sgf PATH] [--coords] [--cell N] [--ascii] [--preview OUT.png]
goban-svg extract  IMAGE [-o OUT.json] [--sgf PATH] [--ascii]
goban-svg render   POS.json|POS.sgf [-o OUT.svg] [--coords] [--cell N] [--ascii] [--preview OUT.png]
```

`convert` = extract + render; defaults: `board.png → board.svg` + `board.json` sidecar
(the correction loop). Print a one-line summary (`19×19, 34 black, 33 white, 2 marks,
1 label → board-1.svg`) + warnings to stderr. `--ascii` prints the position as a text
diagram (rows 19→1, `X`/`O`/`.`, hoshi `+`, plus a legend for marks/labels) — useful for
eyeballing. `render` accepts JSON or SGF (sniff: leading `(` ⇒ SGF). SGF loader must
reject game SGFs containing `B[]`/`W[]` moves with a clear "static positions only"
message (playing out captures is out of scope); do support compressed point lists
(`AB[aa:cd]`) and escaped `]` in LB values.

## 9. Testing strategy (pytest)

The painter makes the extractor fully testable with **zero external fixtures**:

- **Round-trip test (the core):** build rich `Position`s (stones on A1/T19/edges, a wall
  cluster, labels "1"/"12"/"3", blue+red+white wedges, black+white square marks) →
  `render_png(pos, cell≈32, palette=P, noise=3)` → `extract_position` → assert stones,
  marks (canonical colors), labels, size all equal. Run for ≥2 palettes and 19×19 +
  13×13 + an empty board. Vary the LCG noise seed deterministically — no randomness.
- **png_codec:** encode→decode round trip; hand-built streams exercising filters 1–4
  (apply the forward filter in the test, assert decode restores the raw bytes); gray /
  palette / RGBA / 16-bit decode; interlaced → PngError.
- **digits:** stamp→recognize for all 10 digits at 2 scales + multi-digit "12"; narrow
  vertical bar ⇒ "1".
- **sgf:** round trip; move-SGF rejection; rectangle expansion; LB escaping.
- **render:** parse the SVG with `xml.etree`, count stone circles by gradient fill,
  coords on/off, label text present, viewBox math for 13 vs 19.
- **board:** notation parsing (I skipped: col 9 = J), bounds errors, JSON round trip.

## 10. Gotchas already worked out — do not rediscover these the hard way

- **G1 — wood is BRIGHT.** Typical board wood (`#e9c47e`) has luminance ≈ 199, above any
  sane "white stone" threshold. Every bright-classification must also require
  near-neutrality (`|r−b| < 45`); wood is strongly warm (r−b ≈ 90–130). This single check
  is what makes white-stone vs wood reliable.
- **G2 — glossy black stones have specular highlights** (the app's stones are shiny).
  That's why disc classification uses the *median*, why the label mask threshold on black
  is high (≥180), and why the white-wedge detector needs the per-pixel 40% count + the
  0.33d patch position (the specular sits at ~0.15–0.2d, the wedge at the cell corner).
- **G3 — the wedge is at the CELL corner, not on the stone face** — beyond the stone
  radius. Patch means get diluted by stone/wood pixels; classify per-pixel and count.
- **G4 — square markers vs stones:** a solid square marker (half-width ≈ 0.22d) fully
  covers the 0.20d disc but never reaches the 0.36d ring; a stone covers both. Disc
  "solid non-wood" + ring "wood" ⇒ marker. Hoshi dots cover neither.
- **G5 — long stone walls suppress both wood counts and line peaks.** Keep the bbox
  threshold at 0.15·max and the peak threshold at 0.18·max; rely on the robust fit
  (median gap + residual rejection) rather than expecting all 19 peaks.
- **G6 — PNG unfiltering:** `bpp` is bytes-per-pixel *for the filter*, so RGBA=4, RGB=3,
  16-bit RGB=6; Paeth tie-break must prefer a, then b, then c. Get this wrong and images
  decode as diagonal garbage only on *some* screenshots (whichever filters the encoder
  chose).
- **G7 — SGF cannot carry mark colors** (a white square vs black square on an empty point
  both become `SQ[]`). That's why JSON is the primary intermediate and SGF an export.
- **G8 — screenshots may be RGBA or palette PNGs** depending on the capture tool; the
  codec must handle color types 0/2/3/4/6, not just RGB.

## 11. Workflow requirements

- After the tool builds and its tests pass, run `goban-svg convert` on each of the three
  screenshots, then **verify each output visually against the original** (render the SVG
  or the `--preview` PNG and compare stone-by-stone, region by region — corners first,
  then edges, then center; check marker positions, wedge colors, and the ①②③ labels).
  Fix extractor thresholds if reality disagrees with the synthetic fixtures — the real
  screenshots are the acceptance test.
- Commit `examples/board-{1,2,3}.png` (originals), `.json`, `.svg` (and `.sgf`) and embed
  the SVGs in the README with a short gallery section. (Confirm with Joseph that
  committing the screenshots themselves is fine — they're his own game positions.)
- README: what/why, install (`uv tool install`, `uvx`, optional `[images]` extra), the
  3-command usage, the JSON schema, the correction loop (edit JSON → re-render), SGF
  support + limits, the gallery, development (pytest/ruff/CI badge).
- Keep the code fully typed and ruff-clean; every module gets a docstring explaining its
  role (Joseph reads these repos later — write for the future reader).

## 12. Acceptance checklist

- [ ] `pytest` green, `ruff check` + `ruff format --check` clean, CI workflow present
- [ ] `goban-svg convert screenshots/board-1.png` → correct SVG (verified against the
      original: all stones, D14-area white ③ with blue triangle, black ② left side,
      white ① bottom, black square marker lower-left)
- [ ] boards 2 & 3 likewise (red wedge on black ①; white wedge on the black stone in the
      lower-left of board 3; white square markers on empty points)
- [ ] `--ascii`, `--coords`, `--sgf`, JSON sidecar + re-render loop all work
- [ ] examples/ committed, README gallery renders on GitHub
- [ ] repo pushed to GitHub (private) — created via `gh` or by Joseph on the web

Start by asking Joseph for the screenshots (§1), then bootstrap (§2), then build modules
in this order: board → png_codec → digits → sgf → render → extract → cli → tests as you
go. Good luck — the design is solid; the fun part is watching the first real screenshot
come back as a clean position.
