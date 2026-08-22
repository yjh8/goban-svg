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
- **2026-08-22 — v0.1.1 LIVE: correction editor + photo checkpoint (D-007/D-008).**
  Click an intersection to fix a stone; ringed points open a 判讀 inspector; undo,
  keyboard editing and a coordinate form throughout. Photo mode now shows the
  rectified board + detected grid for confirmation BEFORE committing a result.
  Re-running recognition over corrections asks first. Published wheel URLs are
  immutable (`web/wheels/` archive + manifest; deploy verifies the live site).
  Shipped after a 12-round Codex review arc — see `session_logs/2026-08-22-session.md`
  for what that caught and what it cost.
- **2026-08-19 evening — web app phase 1 LIVE at https://goban-svg.pages.dev (D-005):**
  static Cloudflare Pages + self-hosted Pyodide runs the wheel in-browser; zh-TW 圍棋
  UI; in-page JSON correction loop; strict CSP. Shared with the 海峰棋院 staff for
  verification. Phase 2 (D-004): Cloudflare Access + Google IdP after they confirm.
  Deploy: `scripts/deploy-web.sh --deploy` (sha256-pinned Pyodide, auto smoke check).

## Conventions (fleet defaults that apply here)

- **Python via `uv`** (fleet standard): `uv init` / `uv add <pkg>` / `uv run` — never pip/venv/poetry.
- **Commit + push to `main` as one motion** — this repo is NOT PR-gated.
- **Codex `xhigh` review gate** (gpt-5.6-sol; was `ultra` — lowered 2026-08-22 for token cost) applies to any substantive code or design
  change before it lands (docs-only edits exempt). **Always run it via
  `scripts/codex-review.sh`** — per-project `CODEX_HOME` under
  `~/.codex-homes/<basename>-<path-hash>/` (auth is a SYMLINK, never a copy) and
  a default-deny boundary for anything the model runs: filesystem root denied,
  re-granting only the repo, toolchain (minus `/opt/homebrew/var`) and
  `~/.gitconfig`, so `~/.ssh` / `~/.aws` / `~/.codex` are unreadable; command
  network disabled; environment inherited as `core` only with `*TOKEN*`-style
  names excluded; `--ignore-user-config`; `--strict-config`; detached; prompt on
  stdin. Every clause probe-verified (r7–r11). Known residual: global temp stays
  readable via `:minimal` — don't leave secrets in `/tmp`. Sharing `~/.codex` with
  ChatGPT.app killed four long runs mid-flight on 2026-08-21.
- New top-level files/dirs get a `## Repository layout` row in `README.md` in the same commit.
- Transient artifacts go in `outputs/` (gitignored). Anything cited as a source of truth lives in `docs/` (tracked).

## Session Changelog

> Keep today only; older entries roll to `session_logs/`.

### 2026-08-22 (b) — carry #2 closed: blindspot is a source fork (D-009)

Triaging "notify the blindspot author their hash is stale" turned up three things that
each reframed the job. **The hash is not enforced anywhere in code** (`worker.js`
asserts `__version__`, not bytes) — so it was a record correction, not an incident.
**`0.1.0` was published twice** — build 1 `5964bad5…` (65,760 B, commit `07a1ccb`) vs
build 2 `02b158f7…` (78,112 B, `c4615de`); the wheel bytes of build 1 are gone but both
source generations are in git, so the delta IS knowable (photo mode landing 08-20 20:45
without a version bump = the whole +12,352 B). And **blindspot is a source FORK** —
夏大銘 老師 adopted the code Thu 2026-08-20 and modified it since, so "upgrade" is a
cherry-pick, never a URL/hash swap.
**Shipped `5dd21a1`:** integration spec corrected (overclaim removed, 0.1.1 published,
phantom string deleted) + **`docs/upgrade-prompt-for-blindspot.md`** — a merge spec for
his AI, since his first version was built with Claude.
**The lesson (build-learnings):** a spec written for an AI must be **trialled by an
AI**. Executing it blind exposed a blocker — an ordering note that recommended the one
order that breaks, yielding `NameError` on every `from_json()` call — plus a prose
anchor matching 3 places and a verification that structurally could not fail. Fixed and
re-verified.
**Not sent:** the zh-TW note is at `outputs/blindspot-notification-2026-08-22.md`
(gitignored, this machine only). Joseph owns the send.
**Paused here** — capability demo, token-constrained week.

### 2026-08-22 — v0.1.1 SHIPPED: correction editor + photo checkpoint live (D-007/D-008)

**Live** at goban-svg.pages.dev; smoke green (both published wheels hash-verified);
E2E confirmed on the deployed site (board-4 exact, click-to-edit, re-extract guard,
undo). Gate: **APPROVE-1** at xhigh after a 12-round review arc — receipt stamped.
**Shipped:** click-to-cycle editor (kind-aware review rings, point inspector, undo,
role=grid keyboard + coordinate form) · staged photo extraction with a
rectified-grid checkpoint · corner-placement guidance · additive engine APIs
(`uncertain`, `PhotoArtifact`, `geom`) · duplicate-key + surrogate rejection in
board.py · D-008 immutable published wheel URLs (archive + manifest + live
pre-deploy verification + `--rearchive`).
**Review infra:** `scripts/codex-review.sh` — per-project out-of-tree CODEX_HOME,
symlinked auth, default-deny permission profile, filtered env, detached, stdin
prompt. Effort default ultra → **xhigh** (Joseph, token burn).
**Cost lesson (recorded):** the product was clean by review round 3; rounds 4–9
were almost entirely about the review wrapper written mid-session. Decouple
infrastructure hardening from a user-facing ship next time.
**Open:** staff photos arriving tonight → H1–H3 calibration; staff verification of
the editor; notify go-blindspot author (stale 0.1.0 pin); phase-2 auth.
