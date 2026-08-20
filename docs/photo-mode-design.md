# Photo mode design — real-life 棋盤 photos (2026-08-20)

> Status: DESIGN. Trigger: staff feedback via Joseph — "handle real life photos of 棋盤".
> Scope: a new *assisted* extraction mode for photos of physical boards, alongside the
> existing automatic screenshot mode. Ships labeled 實驗性 until calibrated on real
> staff photos (which we do not yet have — see §7 Risks).

## 1. Why not extend the automatic pipeline

The screenshot extractor assumes: axis-aligned uniform grid, globally constant wood
color, bright neutral app rendering. Real photos break all three (perspective
convergence, lighting gradients/shadows, wood-colored tables around the board).
Auto-detecting a board quadrilateral under those conditions is the failure-prone part —
while a human can mark the 4 corners in ~5 seconds with near-zero error. So:

**Design decision D-P1: photo mode is user-assisted.** The user taps the 4 corner
*intersections* (最外側格線的交點, not the wooden edge), picks the board size
(19/13/9 — photos can't be trusted to self-report size), and the pipeline is
deterministic from there. Automatic corner detection can layer on later as a
"suggestion" that pre-places the handles; it never has to be trusted.

## 2. Pipeline

```
photo + 4 corners + size
  → order corners (centroid-angle sort → TL,TR,BR,BL)
  → homography H: canonical board plane → photo quad   (8×8 linear solve, DLT)
  → rectify: canonical Image (cell d=24px, margin d), bilinear sampling      [photo.py]
  → adaptive per-intersection classification (§3)
  → Position (stones only; no labels/marks — real boards have none)
  → existing render/json/sgf pipeline unchanged
```

Canonical size for 19×19: (18·24 + 2·24)² = 480×480 px → ~230k bilinear samples,
≈1–2 s under Pyodide, <0.1 s native.

## 3. Adaptive classification (the load-bearing part)

Fixed thresholds cannot survive real lighting. Two structural insights replace them:

1. **Cell centers are always bare wood.** Stones sit on intersections (r ≈ 0.5d);
   the centers of the 4 cells around an intersection are ≥ 0.7d from any stone
   center — visible wood even in dense positions. So every intersection gets a
   *local* wood reference: `wood_L(i,j)` = median luminance over small patches
   (r = 0.15d) at its ≤4 diagonal cell-centers. This neutralizes lighting
   gradients and shadows by construction (both stone and its reference sit in
   the same lighting).
2. **The photo calibrates its own cutoffs.** For every intersection compute
   `ΔL = disc_median_L − wood_L(i,j)` (disc r = 0.33d, median — glare-robust).
   Empty points cluster tightly around ΔL ≈ 0; black stones far negative; white
   stones positive. Take the empty cluster's spread as MAD of the values within
   ±12 of the median-of-all-ΔL, then:
   `black ⇔ ΔL < −max(6·MAD, 25)` · `white ⇔ ΔL > +max(6·MAD, 18)`
   (floors keep an empty board from classifying noise as stones; the white floor is
   lower because white-stone-vs-wood contrast is weaker than black's).
   Ambiguous band → empty + warning naming the point (fail-loud, as ever).
3. **Neutrality as a tiebreaker, locally referenced.** Wood is warm; stones are
   neutral. `warmth = r − b`. A candidate white stone must be *less warm than its
   local wood* by a margin (warmth < wood_warmth(i,j) − 15); this separates sunlit
   pale wood from white stones. Same-lighting comparison, not a global constant.

All constants live as module-level tunables with docstrings (same convention as
extract.py) — they WILL move when real staff photos arrive.

## 4. Engine API (photo.py — new module; nothing existing changes)

```python
Corner = tuple[float, float]                     # (x, y) in source-photo pixels

def order_corners(corners: Sequence[Corner]) -> tuple[Corner, Corner, Corner, Corner]
    # any order in → (TL, TR, BR, BL); ValueError on collinear/degenerate quads

def rectify_board(img: Image, corners: Sequence[Corner], size: int, cell: int = 24) -> Image
    # homography warp to the canonical flat board (margin = cell)

def extract_photo_position(img: Image, corners: Sequence[Corner], size: int) -> ExtractionResult
    # full pipeline; warnings include the standing 實驗性 notice + per-point ambiguity;
    # GridFit carries the canonical grid (xs/ys in canonical px, spacing=cell)
```

`ExtractionError` for: degenerate quads, size not in 2–25, quad too small
(< ~8 px/cell in the source → tell the user to re-shoot closer).

## 5. CLI

`goban-svg photo IMAGE --corners "x1,y1 x2,y2 x3,y3 x4,y4" --size 19 [-o OUT.svg]
[--json PATH] [--sgf PATH] [--ascii] [--preview OUT.png]` — same output conventions as
`convert`. Exists primarily so the engine is testable/scriptable without the UI.

## 6. Web UI (worker gains one op; page gains a mode)

- Mode selector after image choose: 「App 截圖（自動辨識）」 / 「實體棋盤照片（實驗性）」.
- Photo mode reveals: size select (19/13/9), and a **corner-picker overlay** — the
  photo on a canvas with 4 draggable handles (initialized at 12% insets), lines
  connecting them into a quad, and a 2.5× loupe following the active handle (finger
  precision on phones). Confirm button enabled when the quad is convex.
- Worker: new message `{type:"photo", id, width, height, buf, corners, size}` calling
  `extract_photo_position`; result payload shape unchanged (photo mode payloads carry
  `"mode": "photo"`).
- Corner coordinates sent in *decoded-bitmap* pixel space (same downscale pipeline as
  screenshot mode; the overlay works on the same ≤1400px bitmap the engine sees, so no
  coordinate mapping mismatch is possible).
- zh-TW copy: 「請依序點擊棋盤四個角的交叉點（可拖曳微調）」, 「棋盤大小」,
  photo results carry the standing warning 「照片模式為實驗性功能，請務必人工核對盤面」.

## 7. Testing & risks

- **Synthetic fixtures**: paint a flat board with the existing painter → forward-warp
  it into a synthetic "photo" (perspective + lighting gradient + vignette + noise) using
  a **test-side warp implemented independently** (forward mapping in the test vs the
  module's inverse mapping — a shared-bug oracle would defeat the test) → run
  `extract_photo_position` with the known corners → exact stone equality. Matrix:
  sizes 9/13/19, two palettes, lighting gradients up to ±35%, corner jitter ±0.15d
  (user imprecision tolerance), empty board (zero false stones), dense wall boards.
- **Round-trip guards**: order_corners invariance (all 24 orderings), degenerate quad
  rejection, identity homography ≈ crop.
- **The known unknown**: no real photos exist in the repo yet. The adaptive design
  minimizes fixed-threshold risk, but §3's constants are hypotheses until staff photos
  arrive (ask via Joseph — different lighting, angles, board woods; verified ones become
  `examples/photo-*` regression fixtures like D-003). Until then the UI labels the mode
  實驗性 and every photo result carries the standing warning.
- Stones only: real boards have no printed move numbers/badges; labels/marks/wedge
  stages are intentionally skipped (a design fact, not a gap — document in UI copy that
  photo mode reads 棋子 only).

---

## Amendments (2026-08-20, post Codex design review — APPROVE-13, all accepted)

**Release gating (B3):** phase 1 builds engine + CLI + tests only; the deployed UI is
unchanged. The classifier constants are UNCALIBRATED hypotheses until hand-verified
real staff photos exist (matrix at the review's end: empty/dense/one-color boards,
corner stones, pale+dark woods, mixed lighting, ≥2 cameras, steepest supported angle).
Phase 2 (corner-picker UI, release) is BLOCKED on that corpus per D-003.

1. **Two-stage classifier (B1/B2):** occupancy first, then color. Confidently empty =
   |ΔL| ≤ T_EMPTY (no warning — fixes the 361-warnings absurdity). Occupied-black =
   ΔL ≤ BLACK_MIN; occupied-white = ΔL ≥ WHITE_MIN AND locally-referenced neutrality
   (disc warmth ≤ local wood warmth − margin). Points between bands → empty + warning
   naming the point. Cutoffs are ZERO-ANCHORED fixed floors (ΔL is defined against
   quality-checked local wood, so empty sits near 0 by construction — no assumption
   that empty points are the majority); the MAD refinement only *widens* T_EMPTY and
   only when the near-zero cluster holds ≥30% of points. All constants tagged
   UNCALIBRATED in code.
2. **Reference quality gates (M6):** each cell-center patch is scored; a patch whose
   luminance deviates > 30 from its siblings' median is discarded (stone bleed /
   shadow); < 2 surviving refs → widened ambiguity band + low-confidence warning.
   Corner/edge intersections naturally have 1–2 refs (interior-side only).
3. **Ordered corners (M1):** input contract is TL→TR→BR→BL (screen orientation of the
   photo). `validate_corners` checks finiteness, distinctness, convexity, and winding
   (signed area; reversed/mirrored winding is REJECTED with a fixable message —
   silent correction was re-amended away after the code review: rejection matches
   the no-reordering philosophy). No centroid-angle guessing.
4. **Homography hardening (M2):** source-coordinate normalization before the 8×8
   solve, Gaussian elimination with partial pivoting, reprojection residual check
   (< 0.5 px on all 4 corners), |w| denominator guard during sampling, out-of-source
   samples edge-clamped (only the cosmetic margin can leave the photo; classification
   uses interior pixels only).
5. **Resolution gate (M3):** minimum *local* projected cell scale from the analytic
   Jacobian at all four corners must be ≥ 7 source px/cell, else ExtractionError
   telling the user to shoot closer — an average is never used.
6. **Anti-aliasing (M4):** 2×2 supersampled bilinear per canonical pixel (adequate for
   the gated ≥7 px/cell input; benchmark before promising runtimes — MINOR2).
7. **Capture envelope (M5):** documented support = near-overhead shots (≲30° tilt);
   UI copy will say 請盡量從正上方拍攝. Per-intersection stone-center search and
   grid-residual checks are phase-2 items driven by real-photo calibration.
8. **Contracts (M9, clarified post code-review M11):** the `"mode":"photo"` field
   belongs to the PHASE-2 WORKER MESSAGE only; phase-1 artifacts (ExtractionResult,
   JSON sidecar) carry no mode field. GridFit docstring generalized — coordinates live in the
   *classified image plane* (input screenshot, or rectified canonical image for photo
   mode); photo bbox = canonical interior bounds. Payload gains `"mode"`. The 實驗性
   notice is UI/CLI provenance (stderr notice), not an extraction warning, so it
   survives rerender semantics unchanged.
9. **CLI conventions (M10):** `--corners X,Y X,Y X,Y X,Y` (nargs=4, typed validator,
   documented TL TR BR BL order), reusing convert's output helpers: JSON sidecar
   default, --force, input-collision guard, stderr warnings, exit codes. Help text
   states JPEG/HEIC need the [images] extra (MINOR3).
10. **Size contract (MINOR1):** API/CLI accept 2–25; the future UI offers the 9/13/19
    subset (documented as a UI choice, not an engine limit).
11. **Test oracles (MINOR4):** analytic fixtures (diagonal-intersection invariant:
    the square's center must map to the quad's diagonal intersection; parallelogram
    → affine equivalence), exact tiny-raster bilinear expectations, and a test-side
    forward warp derived via the closed-form square→quad formulas + analytic 3×3
    inverse — different derivation path from production's DLT solve, and production
    helpers are not importable in the oracle.

### Code-review round (2026-08-20, Codex xhigh — APPROVE-13, all fixed pre-landing)

Two BLOCKERs reproduced and fixed: (1) edge-clamped bilinear sampling fabricated
content outside the photo and fed it to the classifier — a board touching the image
edge produced 37 phantom stones with zero warnings; fixed with a per-pixel validity
mask (classification only counts pixels whose supersamples all mapped inside the
photo; mostly-fabricated discs warn, contaminated references are dropped) plus an
explicit corners-inside-the-photo gate. (2) The resolution gate used Jacobian axis
norms, overstating sheared views; now the true minimum singular value. Also fixed:
low-reference handling now widens the ambiguity band on BOTH sides (it previously
increased confidence), discarded wood references are never resurrected (a
no-trustworthy-reference point reports instead of guessing), GridFit.bbox is the
canonical interior, CLI output paths must be pairwise distinct, HEIC help-text
honesty, MAD literals hoisted to UNCALIBRATED tunables, and the test matrix was
expanded (asymmetric resolution quad, exact bilinear/clamp micro-tests, decision-table
unit tests, ±35% gradients + vignette, KGS palette, occupied-majority board, 0.15-cell
corner jitter, CLI photo negative tests).
