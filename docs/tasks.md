# Tasks — goban-svg

## In Progress

_(nothing in flight — v0.1.0 shipped 2026-08-19)_

## Backlog (next session first)

1. **Hands-on testing round** — Joseph tries the tool on fresh screenshots
   (`uv run goban-svg convert <file>`); fix anything reality turns up. New
   verified boards should ALSO land in `examples/` as regression fixtures (D-003).
2. **Web app, phase 1 (no auth)** — upload screenshot → SVG/JSON/SGF in the
   browser. Design session first: stack + hosting (fleet default hypothesis:
   Cloudflare Workers/Pages; note the extractor is pure stdlib Python — runtime
   choice matters), UI shape, correction-loop UX (edit JSON → re-render).
   Codex design review before implementing (fleet gate).
3. **Web app, phase 2 (auth)** — Google sign-in, ONLY after phase 1 is fully
   functional and tested (D-004 sequencing).
4. (Deferred, small) Codex re-verification round for a clean `APPROVE-0`
   receipt — round 1 + fixes are stamped; the re-run was stopped mid-flight
   on 2026-08-19.

## Done

- 2026-08-19 — v0.1.0: full pipeline (board/png_codec/digits/sgf/render/extract/cli),
  427 tests + CI green, acceptance passed on all three real screenshots,
  design+code review cascade findings all fixed, examples/ gallery committed.
