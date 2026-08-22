# Starter prompt — goban-svg: calibrate photo mode with the real staff photos

Paste this into a fresh session in `~/GitHub/goban-svg`.

## Where the project stands

**v0.1.1 is LIVE** at https://goban-svg.pages.dev (deployed 2026-08-22, smoke
green, E2E-verified on the deployed site). It shipped the response to staff
feedback round 1, where two testers both failed on their first try:

1. **Correction editor** — click an intersection on the 棋譜圖 to cycle
   空 → 黑 → 白 → 空. Points the extractor was unsure about carry a dashed ring
   and open a 判讀 inspector instead of blind-cycling; there is undo, keyboard
   editing (`role=grid`), and a coordinate form for phones. The JSON editor
   remains for labels/bulk work.
2. **Photo checkpoint** — after picking corners you see the *rectified* board
   with the detected grid drawn on it and confirm 「格線有對齊」 before any
   result is produced. This is the fix for the actual failure mode: bad corner
   placement used to stay invisible until a wrong board appeared ~35 s later.
   Plus placement guidance with right/wrong example images.
3. **Work protection** — re-extracting over corrections asks first; a rejected
   image no longer wipes the current board; unapplied JSON edits can't be
   silently overwritten.

**D-008**: published wheel URLs are immutable (`web/wheels/` archive + manifest;
deploy verifies the live site before publishing).

## This session's mission — the photo corpus

Joseph was collecting **real physical-board photos** from staff on the evening of
2026-08-21/22. They are the blocker for everything below.

1. **Triage each photo.** CLI: `uv run --with pillow goban-svg photo <img>
   --corners TL TR BR BL --size 19 --ascii` (the dev venv has no Pillow on
   purpose). When a photo misreads, **check sampling geometry FIRST** — render a
   rectified overlay before touching any threshold. That rule comes from
   calibration finding #1, and finding #2 confirmed it again.
2. **Calibrate.** `docs/photo-mode-design.md` § Calibration log holds three
   measured hypotheses waiting on this corpus:
   - **H1** adaptive white cutoff from the photo's own ΔL gap (finding #2
     measured true whites at ΔL +8…+16 against a floor of 20 — structurally
     unrecoverable, and 9 of them died *silently*).
   - **H2** glyph-robust white statistic (app move-numbers drag a white stone's
     disc median to −134 → misread as BLACK). Physical boards shouldn't have
     this, so H2 may be unnecessary — the corpus decides.
   - **H3** least-squares homography from all 38 fitted lines, if residual
     grid error persists with careful corners.
   Every verified photo becomes an `examples/photo-*` fixture (D-003) and
   retires the `UNCALIBRATED` tags in `photo.py`.
3. **Staff verification of the editor** — nobody outside this machine has used
   the new correction UI yet.

## Key files

- `docs/decisions.md` — **D-007** (editor + checkpoint), **D-008** (wheel
  immutability); D-006 back to D-002 for prior context
- `docs/photo-mode-design.md` — design + amendments + **Calibration log
  (findings #1 and #2)** ← read before touching thresholds
- `docs/build-learnings.md` — 7 lessons from 2026-08-21/22; the top three save
  real time
- `src/goban_svg/photo.py` (engine, UNCALIBRATED constants) ·
  `web/app.js` (editor + checkpoint state machine) · `web/worker.js` (protocol)
- `session_logs/2026-08-22-session.md` — the honest account of a 12-round review
  arc, including what it cost

## Gotchas

- **Reviews:** run them via `scripts/codex-review.sh` (out-of-tree `CODEX_HOME`,
  detached, stdin prompt, default-deny profile). Effort default is now **xhigh**
  (fleet floor as of D-016); `max` for security/data-loss/durability diffs;
  `ultra` only if Joseph asks.
- **Deploy flow:** stage → commit the wheel + `SHA256SUMS` → `--deploy`. A source
  change between archiving and deploying needs `--rearchive` (it refuses unless
  the wheel is provably unpublished). Never redirect the script's output — a
  correct refusal looks like success.
- **Cloudflare Pages returns 200 + index.html for missing paths** — never trust a
  status code for existence; check content.
- A served-file `fetch()` probe is **not** evidence of what the page is running
  (stale ES module cache burned two verification attempts). Use a cache-busting
  URL.
- `git add -A` is unsafe while background agents write the tree.

## Open carries

1. Physical-board photo corpus → H1–H3 (the mission above).
2. **Notify the go-blindspot author**: their pinned 0.1.0 wheel hash is stale (an
   earlier redeploy replaced the bytes pre-D-008). 0.1.1 is live at its own URL
   with additive `uncertain` + `PhotoArtifact`.
3. Phase-2 auth (Cloudflare Access + Google IdP) once staff confirm phase 1.
4. Backlog: warning i18n via stable codes; photo-mode perf (~35 s under Pyodide);
   the deferred `APPROVE-0` re-verification on the v0.1.0 CLI diff.

## First action

Start by running `session-init`.
