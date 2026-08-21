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

def extract_photo_position(img: Image, corners: Sequence[Corner], size: int,
                           *, refine: bool = True) -> ExtractionResult
    # refine=True (default) auto-refines the corners first (fail-closed; see
    # refine_corners in the Calibration log) and warns when it fell back;
    # GridFit carries the canonical grid (xs/ys in canonical px, spacing=cell)
```

`ExtractionError` for: degenerate quads, size not in 2–25, quad too small
(< ~8 px/cell in the source → tell the user to re-shoot closer).

## 5. CLI

`goban-svg photo IMAGE --corners X1,Y1 X2,Y2 X3,Y3 X4,Y4 --size 19 [--no-refine]
[-o OUT.svg] [--json PATH] [--sgf PATH] [--ascii] [--preview OUT.png] [--force]` — four
separate corner arguments (TL TR BR BL), same output conventions as `convert`; corners
are auto-refined by default, `--no-refine` trusts them exactly. Exists primarily so the
engine is testable/scriptable without the UI.

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

## Calibration log

### Finding #1 — photo-1 (2026-08-20, phone photo of a monitor; Joseph)

Stress profile: moiré banding, glare gradients, perspective tilt, screen bezel.
- **The classifier thresholds were blameless.** With well-placed corners the
  UNCALIBRATED thresholds read the board perfectly (9 black, 8 white, zero false
  stones; only honest edge-moiré warnings). No threshold was changed.
- **Corner placement error was the dominant failure mode**: a ±20 px (~0.4-cell)
  hand estimate collapsed white detection to 1/8 (discs sampled stone edges;
  whites' ΔL slid under the floor while blacks survived on raw contrast).
- **Structural fix shipped — `refine_corners` (default on)**: rectify with rough
  corners → run the screenshot pipeline's robust grid fitter on the flat image
  (it survives moiré that defeats naive peak-picking; a naive first/last-peak
  version made things WORSE) → extend a short axis by mirroring the other axis'
  span → back-project → iterate ≤3 passes (real photo: 14.5 px → 0.4 px in two).
  Fail-CLOSED (tightened by the ultra review): corners move ONLY on verified
  convergence (a pass whose proposal moved < 1 px). Fit failure at any pass,
  oscillation through the pass budget, an off-image proposal, an unresolved
  extension tie, or cumulative drift beyond 0.6 of any corner's local cell
  scale measured against the ORIGINAL corners — all return the caller's
  corners unchanged, and extraction warns. There is no best-effort middle
  state. Boards < 5 lines skip refinement silently (the fitter needs 3+
  lines). A local corner-crossing centroid measurement was tried and REJECTED
  (moiré biases the centroid — recorded so nobody re-invents it).
- **Measured envelope**: exact recovery to ~0.15-cell corner error on a harsh
  synthetic quad and from 0.4-cell error on the real photo's gentler
  perspective; beyond that the iteration fails to converge and the FAIL-CLOSED
  contract applies — the caller's corners are used unchanged with a warning
  (equivalent to refine=False), never an unverified drifted state. Deeper least-squares homography updates from all 38
  fitted lines remain an option if staff photos need it.
- Fixture: `examples/photo-1.{png,json,svg}` + a regression test that feeds the
  ROUGH corners, pinning the refinement path end to end.
- Still needed from staff: true PHYSICAL-board photos (wood grain, real stones,
  shadows) — a monitor photo shares the app's rendered palette, so wood/stone
  chroma constants remain unproven on real materials.

### Finding #2 — 172444 (2026-08-21, staff feedback round 1: two first-try failures)

Inputs: `screenshots/172444.jpg` (dim phone photo of a 星陣 game on a monitor;
staff test #1, reproduced by Joseph and by this session) and
`screenshots/S__158048260/61.jpg` (staff test #2's own failure artifacts).
All numbers below measured with carefully re-derived corners (±2–3 px, verified
against a rectified-overlay render).

1. **Corner placement is the first killer, again — now with staff evidence.**
   Test #2's screenshots show the handles placed on the app window's chrome,
   well outside the outer grid lines: the sampling grid stretched onto bezel
   and toolbar, reading edge rows/columns as WHITE walls (bright chrome) —
   黑6子/白40子 garbage. The uncertainty list in Joseph's own run clustered on
   rows 16–19 + edge columns: the outward-offset signature (error ∝ distance
   from board center). UX consequences → webapp-design.md 2026-08-21 §A/§B.
2. **`refine_corners` fail-closed on this photo from every corner set tried**
   (finding #1's monitor photo converged; this one is dimmer with stronger
   moiré). Fail-closed behaved as designed — but it means user precision was
   load-bearing, which motivated §B's human-verified rectified-grid preview.
3. **Dim monitor photo compresses white contrast BELOW the white floor.**
   Measured ΔL with good geometry: true whites span **+8.0 … +16.3** (19
   candidates); empty cluster |ΔL| < 8 with spread ≈ 2; blacks ≤ −118. With
   `WHITE_MIN = 20`, zero whites are structurally recoverable on this photo;
   the 9 whites under `T_EMPTY = 12` died SILENTLY (no warning at all), the
   rest were honestly warned. A clean gap separates the empty cluster from the
   white band → **calibration hypothesis H1: adaptive white cutoff from the
   photo's own ΔL gap** (guard against false whites on truly empty boards
   before adopting; needs the physical corpus).
4. **App-rendered move numbers break the disc median on white stones.** The
   dark glyph covers enough of the disc that: B17 (white "5") → ΔL **−133.8 →
   misread as a BLACK stone**; E18 (white + gold dot) → −29.7 → black; whites
   "8"/"10"/"14" → +4.7…+7.5 → silently empty. Photo mode's stones-only
   assumption ("real boards have no printed numbers") is *violated by monitor
   photos specifically* — physical boards don't have this failure class.
   **Hypothesis H2: a glyph-robust white statistic** (e.g. upper-quartile disc
   luminance for the white test) if monitor photos stay a real use case.
5. **The same content as a true screenshot extracts perfectly.** Staff test
   #2's program, captured as an actual screenshot (`examples/board-4.png`, now
   a committed fixture): 31 black + 31 white + 2 marks + 1 label, zero
   warnings, verified stone-by-stone via a class-ring overlay diff. Product
   guidance follows: 螢幕內容請直接截圖；照片模式留給實體棋盤 (§A hint).
6. **Residual spatially-varying grid error persists even with careful manual
   corners** — two corner sets 10–15 px apart flipped *which* whites fell in
   the warned band (lens distortion / rolling shutter / my hand precision).
   When the corpus lands, finding #1's deferred option — least-squares
   homography update from all 38 fitted lines — is the right escalation (H3).
7. The 星陣 analysis overlay (winrate circle at B16) reads as a black stone —
   inherent to photographing an app UI; another §A reason to prefer screenshots.

Assets: `172444.jpg` stays in `screenshots/` as a calibration asset — NOT
promoted to `examples/` (extraction ≠ truth until H1/H2 land; promotion
criterion: whites recovered with no false stones). H1–H3 are UNCALIBRATED
hypotheses awaiting the physical-board corpus Joseph is collecting.
