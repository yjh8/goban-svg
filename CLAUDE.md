# CLAUDE.md — goban-svg

> Project working memory. Read this first in any session here.

## What this is

Convert an image (photo or screenshot) of a Go board into an SVG file: detect the board grid, detect stone positions and colors, emit a clean scalable diagram.

## Status

- **2026-08-19 — v0.1.0 built, working end-to-end on the real screenshots.** Full package
  (board / png_codec / digits / sgf / render / extract / cli), 303 tests, CI. All three
  `examples/` boards extract perfectly (stones, wedges, squares, labels) and are committed
  as regression fixtures. Codex design review (1 BLOCKER + 15 MAJOR, all triaged and
  addressed) reshaped wedge detection and hardened SGF/PNG/JSON handling — read
  `docs/build-learnings.md` and `docs/design.md` § "Post-migration amendments" before
  touching the extractor. The design doc's original §6 wedge/bbox text is superseded.

## Conventions (fleet defaults that apply here)

- **Python via `uv`** (fleet standard): `uv init` / `uv add <pkg>` / `uv run` — never pip/venv/poetry.
- **Commit + push to `main` as one motion** — this repo is NOT PR-gated.
- **Codex `xhigh` review gate** applies to any substantive code or design change before it lands (docs-only edits exempt).
- New top-level files/dirs get a `## Repository layout` row in `README.md` in the same commit.
- Transient artifacts go in `outputs/` (gitignored). Anything cited as a source of truth lives in `docs/` (tracked).

## Session Changelog

> Keep today only; older entries roll to `session_logs/`.

- 2026-08-19 — Repo created and pushed (scaffold), then the whole v0.1.0 built in one
  session: design handoff committed (`docs/design.md`), 7-agent parallel module build,
  Codex xhigh design review (BLOCKER on wedge-probe geometry — confirmed by pixel
  measurement, detector redesigned to corner-region components), 3-agent fix wave
  (SGF FF[4] hardening, PNG alpha/CRC/matrix, JSON validation), app-font alt-'3' template,
  real-screenshot acceptance pass + regression tests, README gallery. Screenshots arrived
  via MBP → git (`examples/board-{1,2,3}.png`, renumbered to match the design's image
  descriptions). Known debt repaid: commit 12eb6bf briefly put un-reviewed in-progress
  code on main (git add -A during background agents — lesson recorded).
