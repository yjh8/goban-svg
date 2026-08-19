# Build learnings — goban-svg

> What actually went wrong (failure modes, not just fixes), so the next session doesn't
> rediscover them. Newest first.

## 2026-08-19 — What the cloud→local migration carried

The first implementation attempt ran in a Claude Code **cloud** session, which hit two
structural failure modes (documented in its handoff, now `docs/design.md`):

1. **Repo creation blocked (403).** The Claude GitHub App returned 403 for repo creation,
   so the cloud session could not create `goban-svg` on GitHub. This is why the work moved
   to a local session — the repo was then created locally and pushed.
2. **Inputs unreachable from the cloud.** The three Go screenshots exist only on Joseph's
   machine. A cloud session cannot test an extractor against the real inputs, and guessing
   positions from pasted images defeats the point. The cloud session therefore **deliberately
   wrote zero code** and instead shipped a fully worked design (`docs/design.md`) — including
   the eight pre-worked gotchas (G1–G8: wood-brightness vs white stones, specular highlights,
   cell-corner wedges, marker-vs-stone discrimination, wall-suppressed line peaks, PNG
   unfilter `bpp`/Paeth details, SGF's inability to carry mark colors, RGBA/palette PNGs).

Lesson: when the acceptance inputs are machine-local, the cloud session's best move is
design + de-risking, and the local session's best move is to **trust the locked design** and
spend its effort on the real-input acceptance loop instead of re-deriving decisions.

*(Joseph: if the cloud session had additional failure modes beyond what its handoff recorded,
add them here — this file is the migration's history.)*
