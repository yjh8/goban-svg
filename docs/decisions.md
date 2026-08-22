# Decisions — goban-svg

> Temporal decision log. Newest first. Minor decisions are single bullets.

## D-009 — go-blindspot is a source fork; outbound changes ship as a merge spec (2026-08-22)

**Status:** ACTIVE
**Valid from:** 2026-08-22

### Context
Carry #2 ("notify the blindspot author their pinned hash is stale") was written from
`integration-prompt-for-blindspot.md`, which models a runtime-Pyodide integration:
the app fetches the published wheel and asserts `goban_svg.__version__`. That model
is wrong for the actual recipient. 夏大銘 (夏老師) at 海峰棋院 built the first,
non-functional version of the feature; Joseph then built the working engine and let
him **adopt the source** on Thursday 2026-08-20, the last day of their class. 夏老師
has modified his copy since. D-008's context said as much in one line ("its
deployment vendors our module sources") but the spec loudly says the opposite, so a
session reading only the spec gives confidently wrong advice.

Verified while triaging: the pinned sha256 is **not enforced anywhere in code**
(§3 asserts `__version__`, not bytes; no `sha256`/`integrity` reference exists in
the 760-line spec), so the stale hash never had a runtime failure mode.

### Decision
1. **Treat blindspot as a source fork.** Outbound advice is a *cherry-pick into a
   diverged tree*, never a `URL + hash + EXPECTED_VERSION` swap. Lead with bug fixes
   in the generation he adopted; treat new APIs as opt-in; mark every change
   liftable-alone or entangled.
2. **Ship a merge spec, not prose.** His first version was built with Claude, so the
   real reader is an agent. `docs/upgrade-prompt-for-blindspot.md` carries hard
   MUST-NOT rules, exact FIND/REPLACE hunks, a self-contained verification with
   expected output, and a troubleshooting table — the same discipline v2 applied to
   the build spec after v1 "left gaps for the AI to judge."
3. **Trial any AI-executable spec by having an AI execute it** before it leaves the
   building (see build-learnings 2026-08-22b).
4. **His baseline is `07a1ccb`** (Thu 2026-08-20, pre-`photo.py`), so his delta is
   larger than 0.1.0 → 0.1.1: it also includes the `load_image` EXIF fix. The merge
   spec's §1 baseline check confirms this empirically rather than by inference.

### Consequence for D-008
D-008's immutability chain is now demonstrable on both published wheels (re-fetched
and hash-matched against `SHA256SUMS` on 2026-08-22). Separately, the two builds that
both shipped as `0.1.0` are now *identified*: build 1 = `07a1ccb` (65,760 B,
`5964bad5…`), build 2 = `c4615de` (78,112 B, `02b158f7…`). The wheel bytes of build 1
are gone, but both source generations are in git, so the delta is knowable — the
integration spec's earlier claim that it could not be characterised was an overclaim
and has been corrected.

## D-008 — Published wheel URLs are immutable; tracked archive + manifest (2026-08-21)

**Status:** ACTIVE
**Valid from:** 2026-08-21

### Context
The round-2 design review flagged that redeploys rebuild the wheel and overwrite
`/wheels/goban_svg-<ver>-*.whl` in place — and measurement showed it had ALREADY
happened: the live 0.1.0 wheel serves sha256 `02b158f7…` while the blindspot
integration prompt pins `5964bad5…` (the original bytes are gone; blindspot is
unaffected in practice because its deployment vendors our module sources, but the
published contract was silently broken).

### Decision
1. `web/wheels/` is a **tracked immutable archive**: every published wheel's exact
   bytes + a `SHA256SUMS` manifest (supersedes webapp-design amendment 10's
   "wheels are never committed"). Seeded 2026-08-21 with the live 0.1.0 bytes.
2. `deploy-web.sh` verifies the archive pre-stage, hard-fails on any
   same-filename/different-bytes collision, archives new versions with a loud
   commit-both note, and stages the WHOLE archive; `smoke-web.sh` re-downloads and
   hash-verifies every manifest wheel post-deploy.
3. Any change to the package's public surface ships as a NEW version (0.1.1 for
   `ExtractionResult.uncertain` + `PhotoArtifact`). Version bumps land in
   pyproject.toml + `__init__.py` + uv.lock together.
4. The integration prompt's pinned hash is corrected with a dated note; Joseph
   notifies the blindspot author (their copy is stale).

## D-007 — Correction editor + staged photo checkpoint (2026-08-21)

**Status:** ACTIVE (implemented; Codex ultra code cascade before deploy)
**Valid from:** 2026-08-21

### Context
Staff feedback round 1: both testers failed first-try on monitor photos
(calibration finding #2). Failure decomposition: corner placement is a precision
cliff, failures are silent until the end, and recovery required editing raw JSON.

### Decision
The trio in webapp-design.md § 2026-08-21 (v2 + v3 amendments — the v3 text is
the implementation contract, forged by TWO Codex ultra design-review rounds,
both REJECT, all 25 findings folded in):
1. **§A corner-placement guidance** — size-independent placement rule + captioned
   正確/錯誤 real-photo thumbnails + 拍螢幕請直接截圖 hints.
2. **§B staged photo extraction** — ONE extraction runs at preview time (round-2
   measured classify ≈ 0.1 s vs rectify ≈ 2.5 s, so the checkpoint is free);
   the user verifies the rectified board + canonical grid before the staged
   result is committed (closed 5-message worker protocol bound to
   epoch/revision/generation/token). The human is the verifier that fail-closed
   `refine_corners` lacks.
3. **§C click-to-cycle editor** — blindspot-pattern interaction (verified live on
   their deployment) over the existing rerender loop: stones-only mutations,
   kind-aware review rings + point inspector (never blind-cycle a flagged
   point), client-owned review lifecycle, inverse-patch undo, role=grid
   keyboard editing + coordinate-form fallback.
Engine additions are additive only: `ExtractionResult.uncertain`
(warning-mirroring structured points; warning STRINGS stay byte-frozen) and
`extract_photo_artifact()` returning `{result, canonical, refined,
corners_used}` in one pass; `geom` payload comes from render.py's own
`BoardGeometry`. E2E-verified locally in Chrome before the cascade (both modes,
cycle + undo + checkpoint + inspector + ring lifecycle).

## D-006 — Photo mode ships to the web as a fallback picker (B3 gate consciously overridden) (2026-08-21)

**Status:** ACTIVE (shipped — goban-svg.pages.dev)
**Valid from:** 2026-08-21
**Supersedes:** the photo design review's release gate B3 ("no user-visible photo mode
before a physical-board corpus"), by owner decision.

### Context
Joseph explicitly asked for a UI step offering corner-picking when a slanted board
photo is detected. Since B3 was written, the evidence changed: calibration finding #1
showed the classifier thresholds sound on the first real photo, and fail-closed
auto-refinement now absorbs rough corner placement.

### Decision
Photo mode is a FALLBACK flow, not a mode toggle: when automatic extraction fails on a
selected image, the error panel offers 「點四個角試試（實驗性）」 (plus an
always-visible link). Corner picker = canvas with 4 numbered draggable handles +
convexity gating + size select; results carry a sticky 實驗性 banner and zh-TW
translations of the photo warnings. The physical-board calibration corpus remains an
open ask — the mode stays labeled experimental until it lands.

## D-005 — Web app phase 1: static Cloudflare Pages + client-side Pyodide, zh-TW UI (2026-08-19)

**Status:** ACTIVE (shipped — https://goban-svg.pages.dev)
**Valid from:** 2026-08-19
**Supersedes:** —
**Phase:** execute
**Confidence:** high

### Context
D-004's phase 1: a shareable web converter for the 海峰棋院 staff, no auth yet. The
package is pure-stdlib Python; the audience uses phones and desktops in zh-TW.

### Decision
No backend at all: Cloudflare Pages serves a static site; **Pyodide (pinned 314.0.5,
self-hosted)** runs the real `goban_svg` wheel in the visitor's browser inside a
persistent Web Worker; the browser's canvas decodes any uploadable format (incl. EXIF
rotation) into raw RGB handed to Python. UI is 繁體中文 with 圍棋 terminology (棋譜圖、
手數、記號). The CLI's correction loop exists in-page (JSON editor → 套用修正並重新產生).
Strict CSP (`connect-src 'self'`) makes 「圖片不會上傳到任何伺服器」 enforced, not
aspirational. Deploys via `scripts/deploy-web.sh` (deterministic wheel+version staging).
Full rationale + review amendments: `docs/webapp-design.md` (Codex design review
APPROVE-10, all findings folded in before implementation).

### Alternatives considered
- Cloudflare Python Workers — CPU limits vs multi-second extraction, beta runtime.
- Python server elsewhere — moving parts + images leave the browser for nothing.
- JS port — drift from the regression-fixed Python.

### Implementation
`web/` + `scripts/deploy-web.sh`; E2E-verified live (boot under CSP, board-1 exact
summary, JSON re-render, zh-TW error paths). Phase 2 = Cloudflare Access with Google
IdP over production + preview hostnames (matrix in webapp-design.md amendment 9).

## D-004 — Web app with Google authentication (planned; auth deferred) (2026-08-19)

**Status:** ACTIVE (planned — next workstream)
**Valid from:** 2026-08-19
**Supersedes:** —
**Phase:** brainstorm
**Confidence:** high (direction), low (implementation shape — not yet designed)

### Context
Joseph wants to share the converter with others (stated 2026-08-19 at session
handoff; likely audience relates to his Go circle — not confirmed). That means a
web app, and sharing implies access control.

### Decision
1. Turn the CLI tool into a web app (upload screenshot → SVG/JSON in the browser).
2. Authentication will be **Google sign-in**.
3. **Sequencing is the load-bearing part:** build and fully test the web app
   first with NO auth; add Google auth only after the app is functional and
   tested. Do not scaffold OAuth early.

### Alternatives considered
- Not yet — the web stack, hosting, and auth wiring are next-session design
  work (Joseph's fleet default stack is Cloudflare Workers/Pages; treat as the
  starting hypothesis, not a decision).

## D-003 — Real-screenshot regression fixtures break the circular oracle (2026-08-19)

**Status:** ACTIVE
**Valid from:** 2026-08-19

### Context
The extractor's round-trip tests use its own painter (`render_png`) as fixture
generator — painter and extractor share geometry assumptions, so a shared wrong
assumption passes (proven: the original wedge-probe design was green
synthetically and missed all three real wedges — design review F5/BLOCKER).

### Decision
The committed screenshots + their verified `.json` sidecars in `examples/` are
permanent regression fixtures (`tests/test_real_examples.py`, exact-equality +
zero warnings) — originally three, board-4 added 2026-08-21 (verified via a
class-ring overlay diff). An extractor change that alters a real reading must
be re-verified visually and the sidecar regenerated deliberately.

## D-002 — Wedge detection: corner-region connected components (2026-08-19)

**Status:** ACTIVE (supersedes design.md §6's fixed-probe wedge spec)
**Valid from:** 2026-08-19

### Context
Measured on the real screenshots: badges are 0.31c–0.45c right triangles in
*varying* cell corners, sometimes entirely off the stone face. The design's
fixed probes + rim-overlap assumption missed all three.

### Decision
Per-stone, per-cell-quadrant connected components of badge-class pixels;
accept iff quadrant-pure (slack 0.06c), area ∈ [0.015, 0.16]·d², reach ≥ 0.38c,
tip ≤ 0.42c (Chebyshev). Ownership is free (the window is the stone's own
cell). Constants + rationale live on the constants in `extract.py`; the
measurement record is in `docs/build-learnings.md`.

## Minor decisions

- [2026-08-19] `digits.ALT_TEMPLATES` carries per-digit alternate exemplars
  measured from real app fonts (first: round-top '3'); `recognize()` scores each
  digit by its best exemplar, `stamp()` paints the classic face. (active)
- [2026-08-19] PNG corruption (`PngCorruptError`) never falls back to Pillow —
  Pillow skips CRC checks; only unsupported-feature errors fall back. (active)
- [2026-08-19] `convert` refuses to overwrite a *differing* JSON sidecar
  without `--force` — the sidecar is the correction-loop artifact. (active)
- [2026-08-19] Board sizes clamped to 2–25 everywhere (25-letter notation
  limit); SGF beyond that errors clearly. (active)
- [2026-08-19] `screenshots/` = gitignored inbox; curated originals + verified
  outputs live tracked in `examples/`. (active)
