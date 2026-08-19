# CLAUDE.md — goban-svg

> Project working memory. Read this first in any session here.

## What this is

Convert an image (photo or screenshot) of a Go board into an SVG file: detect the board grid, detect stone positions and colors, emit a clean scalable diagram.

## Status

- **2026-08-19 — repo genesis (scaffold only, zero implementation code).** The first implementation is being migrated in from a prior Claude Code cloud session that ran into repeated issues. When that code lands, record what actually went wrong in the cloud session (the failure modes, not just the fixes) in `docs/build-learnings.md` — that history is the most valuable thing the migration carries.

## Conventions (fleet defaults that apply here)

- **Python via `uv`** (fleet standard): `uv init` / `uv add <pkg>` / `uv run` — never pip/venv/poetry.
- **Commit + push to `main` as one motion** — this repo is NOT PR-gated.
- **Codex `xhigh` review gate** applies to any substantive code or design change before it lands (docs-only edits exempt).
- New top-level files/dirs get a `## Repository layout` row in `README.md` in the same commit.
- Transient artifacts go in `outputs/` (gitignored). Anything cited as a source of truth lives in `docs/` (tracked).

## Session Changelog

> Keep today only; older entries roll to `session_logs/`.

- 2026-08-19 — Repo created and pushed (scaffold: README + .gitignore + this file). Next session: paste in the cloud session's prior work and take stock.
