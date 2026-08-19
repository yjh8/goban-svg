# Starter prompt — goban-svg: process staff feedback on the live web app

Paste this into a fresh session in `~/GitHub/goban-svg`.

## Where the project stands

Two ships landed on 2026-08-19, both on `main`, CI green:

1. **v0.1.0 CLI** — pure-stdlib screenshot → SVG converter, 427 tests, all three real
   boards in `examples/` extract perfectly and are regression fixtures.
2. **Web app phase 1 — LIVE at https://goban-svg.pages.dev** (D-005): static
   Cloudflare Pages + self-hosted Pyodide 314.0.5 running the real wheel in the
   visitor's browser (zero backend; CSP-enforced "images never leave the browser"),
   zh-TW 圍棋 UI (棋譜圖/手數/記號), in-page JSON correction loop
   (修正辨識結果 → 套用修正並重新產生), downloads (SVG/JSON/SGF).
   Both Codex xhigh gates ran pre-landing (design APPROVE-10, code APPROVE-10, all
   findings fixed). Deploy: `scripts/deploy-web.sh --deploy` (sha256-pinned Pyodide,
   auto smoke check `scripts/smoke-web.sh`). E2E procedure: docs/TESTING.md § Web app.

**Joseph shared the URL with the 海峰棋院 staff. This session exists to process their
feedback and change requests** (expected 2026-08-20). Joseph can't read Go positions —
the staff are the correctness oracle.

## This session's mission

1. **Triage staff feedback.** For every "the conversion is wrong" report: get the
   original image, reproduce via CLI (`uv run goban-svg convert <img> --ascii`),
   fix the extractor, and add the verified board to `examples/` + a case to
   `tests/test_real_examples.py` (D-003 — every verified case becomes a fixture;
   sidecar regeneration is deliberate, never automatic).
2. **Change requests**: judge scope; substantive UI/UX changes get a quick design
   note in docs/webapp-design.md; any substantive code change goes through the
   Codex xhigh gate before commit+push (fleet rule — the hook enforces it).
   Redeploys are seamless to the same URL.
3. **When staff confirm it works** → phase 2: Cloudflare Access + Google IdP.
   Read webapp-design.md amendment 9 FIRST (production AND preview hostnames need
   separate policies; custom-domain-before-Access sequencing).

## Key files

- `docs/webapp-design.md` — web architecture + BOTH review-amendment lists
- `docs/decisions.md` — D-005 (web architecture), D-004 (auth sequencing), D-003
  (fixture rule), D-002 (wedge detector)
- `docs/tasks.md` — In Progress = staff verification round; backlog ordered
- `docs/TESTING.md` — CLI harness + web E2E/smoke procedures + known phase-1 gaps
- `web/` (app.js state machine, worker.js Pyodide bridge) · `scripts/deploy-web.sh`
- `docs/build-learnings.md` — read the top two entries (web-arc lessons + the
  reality-vs-design failure modes)

## Gotchas

- Photos of physical boards likely fail (perspective) — fail-loud with zh-TW
  guidance; whether to build rectification depends on what staff actually send.
- Warning zh-TW map in app.js is regex-on-English — extractor wording changes break
  it silently (backlog: stable warning codes).
- ruff formats python code fences in docs/*.md (CI gate); `uv run ruff format .`
  after doc edits.
- Never `git add -A` while background agents write the tree.
- The web app loads the wheel by exact version — after ANY Python change, redeploy
  via the script (it rebuilds + reasserts versions); never hand-copy files.

## First action

Start by running `session-init`.
