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
- **2026-08-19 evening — web app phase 1 LIVE at https://goban-svg.pages.dev (D-005):**
  static Cloudflare Pages + self-hosted Pyodide runs the wheel in-browser; zh-TW 圍棋
  UI; in-page JSON correction loop; strict CSP. Shared with the 海峰棋院 staff for
  verification. Phase 2 (D-004): Cloudflare Access + Google IdP after they confirm.
  Deploy: `scripts/deploy-web.sh --deploy` (sha256-pinned Pyodide, auto smoke check).

## Conventions (fleet defaults that apply here)

- **Python via `uv`** (fleet standard): `uv init` / `uv add <pkg>` / `uv run` — never pip/venv/poetry.
- **Commit + push to `main` as one motion** — this repo is NOT PR-gated.
- **Codex `xhigh` review gate** applies to any substantive code or design change before it lands (docs-only edits exempt).
- New top-level files/dirs get a `## Repository layout` row in `README.md` in the same commit.
- Transient artifacts go in `outputs/` (gitignored). Anything cited as a source of truth lives in `docs/` (tracked).

## Session Changelog

> Keep today only; older entries roll to `session_logs/`.

### 2026-08-21 — fail-closed refinement landed · corner-picker UI live (D-006)

**Commits:** c4615de (fail-closed refine_corners + examples/photo-1 fixture, ultra
review ×2 rounds) · 1308c40 (web corner-picker fallback, ultra APPROVE-10 all fixed).
CI green; deployed to goban-svg.pages.dev.
**Decisions:** D-006 (picker ships as fallback; B3 consciously overridden by owner).
**Gate migration:** reviews now ultra on gpt-5.6-sol; backgrounded codex requires
`< /dev/null` (two 45-min hangs; lesson propagated: codex-gate + global CLAUDE.md +
gstack already had it, personal memory added).
**Absorbed:** cloud continuity kit (PR #1, hook audited before rebase).
**Open:** staff feedback expected later today (new session); physical-board photo
corpus; web photo perf (~35 s); phase-2 auth.
