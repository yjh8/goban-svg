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
- **Codex `ultra` review gate** (gpt-5.6-sol) applies to any substantive code or design
  change before it lands (docs-only edits exempt). **Always run it via
  `scripts/codex-review.sh`** — per-project `CODEX_HOME` under
  `~/.codex-homes/<basename>-<path-hash>/` (auth is a SYMLINK, never a copy) and
  a default-deny boundary for anything the model runs: filesystem root denied,
  re-granting only the repo, toolchain (minus `/opt/homebrew/var`) and
  `~/.gitconfig`, so `~/.ssh` / `~/.aws` / `~/.codex` are unreadable; command
  network disabled; environment inherited as `core` only with `*TOKEN*`-style
  names excluded; `--ignore-user-config`; `--strict-config`; detached; prompt on
  stdin. Every clause probe-verified (r7–r10). Sharing `~/.codex` with
  ChatGPT.app killed four long runs mid-flight on 2026-08-21.
- New top-level files/dirs get a `## Repository layout` row in `README.md` in the same commit.
- Transient artifacts go in `outputs/` (gitignored). Anything cited as a source of truth lives in `docs/` (tracked).

## Session Changelog

> Keep today only; older entries roll to `session_logs/`.

### 2026-08-21 — staff feedback round 1 → correction editor + photo checkpoint shipped as 0.1.1 (D-007/D-008)

**Morning:** c4615de (fail-closed refine_corners + photo-1 fixture) · 1308c40
(corner-picker fallback, D-006) — deployed.
**Evening — feedback round 1 processed:** two staff first-try failures (both
MONITOR photos) triaged → calibration finding #2 (measured: whites ΔL +8..+16 vs
floor 20, 9 silent deaths; labeled whites flip BLACK at −134; H1–H3 hypotheses
parked for the physical corpus Joseph is collecting) + `examples/board-4` (the
same program's true screenshot extracts perfectly — 4th D-003 fixture).
**Shipped (D-007):** click-to-cycle correction editor (blindspot-pattern, verified
live on their deployment) + staged photo extraction with a rectified-grid
checkpoint + corner-placement guidance, as **0.1.1** with additive
`ExtractionResult.uncertain` + `PhotoArtifact` APIs. Design took TWO Codex ultra
REJECT rounds (25 findings → v3 contract); build = 5-agent workflow + 2 verify
lenses (14 findings fixed); code review r5 REJECT 0-BLOCKER (7 MAJOR + 5 MINOR,
all fixed); Chrome E2E passed twice (pre- and post-fix). 486 tests.
**D-008:** published wheel URLs immutable — tracked `web/wheels/` archive +
manifest; deploy verifies the LIVE site pre-publish (discovered the 0.1.0 pin in
the blindspot prompt was already stale — Joseph to notify the author).
**Infra lesson:** ChatGPT.app's codex rewrites `~/.codex/models_cache.json` →
CLI runs crash mid-flight; fix = isolated `CODEX_HOME` (memory + build-learnings).
**Open:** deploy verification on staff devices; physical-board photo corpus →
H1–H3 calibration; phase-2 auth.
