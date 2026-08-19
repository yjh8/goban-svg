# Decisions — goban-svg

> Temporal decision log. Newest first. Minor decisions are single bullets.

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
