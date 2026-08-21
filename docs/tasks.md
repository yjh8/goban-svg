# Tasks — goban-svg

## In Progress

1. **Staff verification round** — round 1 processed 2026-08-21: two first-try failures,
   both on MONITOR photos (not physical boards). Triaged → calibration finding #2
   (photo-mode-design.md) + `examples/board-4` fixture (the same program's true
   screenshot extracts perfectly). Joseph is now collecting REAL physical-board photos
   from staff — that corpus unblocks the H1–H3 calibration hypotheses.
2. **Correction editor + photo-confidence UX trio (D-007)** — IMPLEMENTED +
   locally E2E-verified. Design survived two Codex ultra REJECT rounds (25
   findings folded into the v3 contract); build ran as a 5-agent workflow with
   two independent verify lenses (2 MAJOR + 12 MINOR, all fixed). Ships as
   0.1.1 with the D-008 immutable wheel archive. Remaining: Codex ultra code
   cascade (in flight) → address findings → commit+push → deploy → post-deploy
   smoke + live E2E.

## Backlog

1. **Web app, phase 2 (auth)** — Cloudflare Access + Google IdP over production AND
   preview hostnames (matrix in webapp-design.md amendment 9). ONLY after the team
   confirms phase 1 works (D-004 sequencing).
2. Warning i18n hardening — stable warning codes from the extractor instead of the
   zh-TW regex map in app.js (drift risk, design amendment 11).
3. **Photo mode calibration (corpus incoming)** — real PHYSICAL-board photos are being
   collected by Joseph (varied lighting/angles/woods, empty + dense boards, corner
   stones) — every verified one becomes an `examples/photo-*` fixture (D-003) and
   retires the UNCALIBRATED tags. Measured hypotheses waiting on the corpus
   (finding #2): H1 adaptive white cutoff from the photo's own ΔL gap · H2
   glyph-robust white statistic (monitor photos) · H3 least-squares homography from
   all 38 fitted lines. Perf backlog: photo mode ≈35 s under Pyodide (refine passes
   rectify up to 4x) — consider a coarse-cell refinement pass if staff complain.
4. (Deferred, small) Codex re-verification round for a clean `APPROVE-0` receipt on
   the v0.1.0 CLI diff — round 1 + fixes are stamped; the re-run was stopped mid-flight.

## Done

- 2026-08-19 (evening) — **Web app phase 1 shipped**: goban-svg.pages.dev live —
  static Pages + self-hosted Pyodide, zh-TW UI, in-page JSON correction loop, strict
  CSP, E2E-verified in Chrome (D-005). Design review APPROVE-10 folded in pre-build.
- 2026-08-19 — v0.1.0: full pipeline (board/png_codec/digits/sgf/render/extract/cli),
  427 tests + CI green, acceptance passed on all three real screenshots,
  design+code review cascade findings all fixed, examples/ gallery committed.
