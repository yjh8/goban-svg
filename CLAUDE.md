# CLAUDE.md — goban-svg

> Project working memory. Read this first in any session here.

## What this is

Convert an image (photo or screenshot) of a Go board into an SVG file: detect the board grid, detect stone positions and colors, emit a clean scalable diagram.

## Status

- **2026-08-19 — v0.1.0 shipped: working end-to-end on the real screenshots, CI green.**
  Full package (board / png_codec / digits / sgf / render / extract / cli), 427 tests. All
  three `examples/` boards extract perfectly (stones, wedges, squares, labels) and are
  committed as regression fixtures (D-003). Codex design review (1 BLOCKER + 15 MAJOR) and
  code-review cascade (0 BLOCKER + 8 MAJOR; Codex + two independent lenses) — all findings
  addressed. Read `docs/build-learnings.md`, `docs/decisions.md`, and `docs/design.md`
  § "Post-migration amendments" before touching the extractor; the design doc's original
  §6 wedge/bbox text is superseded by D-002.
- **Next workstream (D-004):** hands-on testing → web app phase 1 (no auth) → Google
  sign-in only after the app is functional and tested.

## Conventions (fleet defaults that apply here)

- **Python via `uv`** (fleet standard): `uv init` / `uv add <pkg>` / `uv run` — never pip/venv/poetry.
- **Commit + push to `main` as one motion** — this repo is NOT PR-gated.
- **Codex `xhigh` review gate** applies to any substantive code or design change before it lands (docs-only edits exempt).
- New top-level files/dirs get a `## Repository layout` row in `README.md` in the same commit.
- Transient artifacts go in `outputs/` (gitignored). Anything cited as a source of truth lives in `docs/` (tracked).

## Session Changelog

> Keep today only; older entries roll to `session_logs/`.

### 2026-08-19 — v0.1.0 genesis → shipped (Claude Code/Fable 5, joseph-macmini)

**Commits:** b894722 (bootstrap) · 12eb6bf (screenshots + accidental early code) ·
07a1ccb (v0.1.0, review-hardened) · 22726b9 (docs format) — all pushed, CI green.
**Decisions:** D-002 wedge redesign · D-003 real-image fixtures · D-004 web app
planned, Google auth deferred (docs/decisions.md created).
**Docs synced:** design.md (+amendments) · interfaces.md · build-learnings.md ·
TESTING.md, tasks.md, decisions.md (new) · session_logs/2026-08-19-session.md.
**Learnings:** unmeasured thresholds are hypotheses · git add -A vs background
agents · ruff formats md code blocks.
**Files:** full package + 427 tests + examples/ gallery (~8k lines).
**Open:** next session = hands-on testing → web app phase 1 (no auth) → Google
auth after tested (D-004); deferred: clean APPROVE-0 re-verification round.
