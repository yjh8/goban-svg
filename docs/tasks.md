# Tasks — goban-svg

## In Progress

1. **Staff verification round** — Joseph shared https://goban-svg.pages.dev with the
   海峰棋院 team (2026-08-19 evening). Collect: more test examples + correctness
   verification (Joseph doesn't read Go positions). Every verified case → `examples/`
   regression fixture (D-003); every failure → original image + what's wrong.
2. **Web app code-review follow-ups** — Codex review of `web/` ran at landing; apply
   any findings and redeploy (same URL).

## Backlog

1. **Web app, phase 2 (auth)** — Cloudflare Access + Google IdP over production AND
   preview hostnames (matrix in webapp-design.md amendment 9). ONLY after the team
   confirms phase 1 works (D-004 sequencing).
2. Warning i18n hardening — stable warning codes from the extractor instead of the
   zh-TW regex map in app.js (drift risk, design amendment 11).
3. **Photo mode, phase 2 (BLOCKED on real photos)** — phase 1 shipped 2026-08-20
   (engine `photo.py` + `goban-svg photo` CLI, assisted 4-corner + homography +
   adaptive classifier; design + amendments in docs/photo-mode-design.md). Phase 2 =
   calibrate the UNCALIBRATED thresholds on real staff photos (ask Joseph: varied
   lighting/angles/woods, empty + dense boards, corner stones), commit verified ones
   as `examples/photo-*` fixtures (D-003), THEN build the corner-picker web UI and
   release. Do not ship the UI on synthetic-only calibration (design review B3).
4. (Deferred, small) Codex re-verification round for a clean `APPROVE-0` receipt on
   the v0.1.0 CLI diff — round 1 + fixes are stamped; the re-run was stopped mid-flight.

## Done

- 2026-08-19 (evening) — **Web app phase 1 shipped**: goban-svg.pages.dev live —
  static Pages + self-hosted Pyodide, zh-TW UI, in-page JSON correction loop, strict
  CSP, E2E-verified in Chrome (D-005). Design review APPROVE-10 folded in pre-build.
- 2026-08-19 — v0.1.0: full pipeline (board/png_codec/digits/sgf/render/extract/cli),
  427 tests + CI green, acceptance passed on all three real screenshots,
  design+code review cascade findings all fixed, examples/ gallery committed.
