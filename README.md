# goban-svg

Convert an image (photo / screenshot) of a Go board (goban) into a clean SVG diagram — board grid, stone positions, and eventually annotations.

Status: genesis 2026-08-19. Initial implementation is being migrated in from a prior Claude Code cloud session.

## Repository layout

| Path | Tracked | Purpose |
|------|---------|---------|
| `README.md` | tracked | This file — repo map + purpose |
| `CLAUDE.md` | tracked | Project working memory for AI sessions |
| `.gitignore` | tracked | Root-anchored ignore rules (Python/uv + transient outputs) |
| `pyproject.toml` | tracked | Package metadata (hatchling, src-layout), ruff + pytest config, uv dev deps |
| `uv.lock` | tracked | uv lockfile (dev tooling; the package itself has zero runtime deps) |
| `src/goban_svg/` | tracked | The package: board model, PNG codec, digit OCR, SGF, renderers, extractor, CLI |
| `tests/` | tracked | pytest suite — synthetic-fixture round-trip tests, no external fixtures |
| `docs/` | tracked | Canonical docs: `design.md` (locked design handoff), `interfaces.md` (module contract), `build-learnings.md` |
| `screenshots/` | **gitignored** | Raw input inbox for Joseph's board screenshots (curated copies go to `examples/`) |
| `.github/workflows/` | tracked | CI: ruff check + format + pytest on Python 3.10/3.13 |

> Rule: any new top-level file or directory gets a row here **in the same commit** (fleet taxonomy standard).
