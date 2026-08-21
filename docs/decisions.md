# Decisions — goban-svg

> Temporal decision log. Newest first. Minor decisions are single bullets.

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
The three committed screenshots + their hand-verified `.json` sidecars in
`examples/` are permanent regression fixtures (`tests/test_real_examples.py`,
exact-equality + zero warnings). An extractor change that alters a real reading
must be re-verified visually and the sidecar regenerated deliberately.

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
