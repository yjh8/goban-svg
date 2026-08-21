# Starter prompt — goban-svg: process today's staff feedback (picker is live)

Paste this into a fresh session in `~/GitHub/goban-svg`.

## Where the project stands

Everything is live at **https://goban-svg.pages.dev**, `main` = CI-green:

1. **Screenshot mode** — automatic, reads stones + wedge badges + squares + move
   numbers. The three `examples/board-*` fixtures pin it.
2. **Photo mode (實驗性), NOW USER-VISIBLE** (D-006): when automatic extraction fails
   on a selected image, the error panel offers 「點四個角試試」; an always-visible link
   serves users who know it's a photo. Corner picker: numbered draggable handles with
   tap-to-place, arrow-key nudging, an offset magnifier; corners are auto-refined
   **fail-closed** (`refine_corners`: they move only on verified convergence — every
   guard has a dedicated test). `examples/photo-1` (Joseph's monitor photo) is the
   first real-photo fixture, keyed to rough corners. Photo results carry a sticky
   實驗性 banner; stones only.

**This session exists to process 海峰棋院 staff feedback** (Joseph expects it today).

## Mission

1. **Triage feedback.** Wrong conversions: get the original image; screenshots →
   reproduce via `uv run goban-svg convert <img> --ascii`; photos → `goban-svg photo
   <img> --corners TL TR BR BL --size N` (Pillow via `uv run --with pillow` for JPEG).
   Fix → verified boards become `examples/` fixtures (D-003 — non-negotiable).
2. **Physical-board photos are the treasure**: every one calibrates the UNCALIBRATED
   photo thresholds (docs/photo-mode-design.md § Calibration log — finding #1 says
   geometry, not thresholds, was the first killer; check sampling geometry FIRST via
   a rectified-image overlay when photos misread).
3. Change requests: substantive UI/engine changes go through the Codex gate —
   **ultra on gpt-5.6-sol, and backgrounded codex invocations REQUIRE `< /dev/null`**
   (two 45-min hangs taught this; the gate hook now says it too).
   Redeploy: `scripts/deploy-web.sh --deploy` (same URL, seamless).
4. When the team confirms → phase 2 auth: Cloudflare Access + Google IdP
   (webapp-design.md amendment 9: production AND preview hostnames need policies).

## Key files

- `docs/decisions.md` — D-006 (picker ships, B3 overridden) … D-002; read D-006 first
- `docs/photo-mode-design.md` — design + 2 amendment rounds + Calibration log
- `docs/tasks.md` — ordered backlog (perf: photo ≈35 s in-browser is a known item)
- `docs/build-learnings.md` — top 3 entries (codex stdin · geometry-first · web arc)
- `web/app.js` (picker + state machine) · `src/goban_svg/photo.py` (engine)
- `.claude/` — the cloud continuity kit (SessionStart hook) from Joseph's cloud
  session; changes to `.claude/` in this PUBLIC repo execute in sessions — scrutinize
  any PR touching it

## Gotchas

- The dev venv has NO Pillow on purpose (fallback tests); use `uv run --with pillow`
  for JPEG work.
- ruff formats python code fences in docs/*.md (CI gate) — `uv run ruff format .`
  after doc edits.
- Never `git add -A` while background agents write; stage explicit paths.
- Cloud sessions may land `claude/*` PRs (like PR #1) — check for open ones before
  doc writes (the SessionStart hook surfaces this automatically).

## First action

Start by running `session-init`.
