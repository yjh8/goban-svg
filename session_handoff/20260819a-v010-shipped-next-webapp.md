# Starter prompt — goban-svg: test v0.1.0, then build the web app (no auth yet)

Paste this into a fresh session in `~/GitHub/goban-svg`.

## Where the project stands

v0.1.0 shipped yesterday-you-don't-remember (2026-08-19), from empty scaffold to
CI-green in one session: a pure-stdlib Python CLI (`goban-svg convert/extract/render`)
that turns Go-app screenshots into clean SVG diagrams via an editable JSON
intermediate, plus SGF export. All three real screenshots in `examples/` extract
**perfectly** — every stone, the colored corner-wedge badges, square markers, and
move-number labels — and are committed as regression fixtures. 427 tests, ruff clean,
CI green on Python 3.10/3.13, `main` = `origin/main`, everything through the Codex
design + code review cascade with all findings fixed.

## This session's mission (Joseph, at handoff)

1. **Hands-on testing round.** Joseph wants to try the tool himself — support him
   (`uv run goban-svg convert <file>`; drop new screenshots in `screenshots/`,
   gitignored inbox). If reality breaks anything: fix, and add the verified board
   to `examples/` as a new regression fixture (that's D-003 — the anti-circularity
   rule; sidecar regeneration is deliberate, never automatic).
2. **Web app.** Turn the converter into a web app Joseph can share. **Design
   session first** — stack/hosting is genuinely open: the extractor is pure-stdlib
   *Python*, so the fleet-default Cloudflare Workers stack needs a Python story
   (Pyodide in browser? Python worker runtime? a small server?). Run the Codex
   `xhigh` design review before implementing (fleet hard rule). Then build
   **phase 1 with NO auth** and get it fully functional and tested.
3. **Google authentication comes only after phase 2's gate**: D-004's sequencing
   is deliberate — do NOT scaffold OAuth early. Wait until the app is functional
   and tested, then add Google sign-in.

## Key files

- `docs/decisions.md` — D-002 (wedge detector redesign), D-003 (real-image
  fixtures), **D-004 (web app + deferred Google auth — read this first)**
- `docs/tasks.md` — the ordered backlog matching the mission above
- `docs/design.md` — the CLI's canonical design **+ "Post-migration amendments"
  section, which supersedes the original §6 wedge/bbox text**
- `docs/build-learnings.md` — the failure-mode history; headline lesson:
  *thresholds designed without the target pixels are hypotheses, not specs*
- `docs/TESTING.md` — harness shape; `docs/interfaces.md` — module contracts
- `src/goban_svg/` — board, png_codec, digits, sgf, render, extract, cli
- `examples/` — 3 screenshots + verified .json/.svg/.sgf (gallery + fixtures)

## Gotchas that will bite you otherwise

- **ruff formats the ```python blocks inside docs/*.md** — CI's format gate covers
  docs. Run `uv run ruff format .` after doc edits with code fences.
- **Painter constants marked `EXTRACTOR-COUPLED` in render.py are load-bearing**
  — changing one makes round-trip failures look like extractor bugs.
- Never `git add -A` while background agents write the tree — explicit paths.
- Repo conventions: uv only (never pip), commit+push to `main` as one motion
  (not PR-gated), Codex `xhigh` review before any substantive code/design lands.
- Small deferred item: a clean `APPROVE-0` Codex re-verification round (round-1
  review + all fixes are stamped; the re-run was stopped mid-flight).

## First action

Start by running `session-init`.
