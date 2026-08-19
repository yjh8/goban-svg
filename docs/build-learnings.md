# Build learnings — goban-svg

> What actually went wrong (failure modes, not just fixes), so the next session doesn't
> rediscover them. Newest first.

## 2026-08-19 — First implementation: what reality disagreed with

The full package was built in one session (7-agent parallel build to the locked design,
then a fix wave). The synthetic round-trip suite went green on the first integration run —
and the real screenshots then broke exactly the things the design had *assumed* instead of
measured. The list, because the pattern matters more than the items:

1. **The design's wedge-probe geometry was wrong** (Codex design-review BLOCKER, confirmed
   by measurement). It assumed a rim-overlapping corner badge probed at two fixed diagonal
   offsets; the real badges are 0.31c–0.45c corner triangles in *varying* corners, sometimes
   entirely off the stone face. Fixed-probe detection missed all three real wedges while
   passing every synthetic test — because the painter painted the same wrong assumption
   (the "circular oracle" problem, review F5). Fix: per-cell corner-region connected
   components (quadrant purity + corner reach + tip-toward-center), painter repainted to
   measured geometry with per-point corner variation, and real-screenshot regression tests
   (`tests/test_real_examples.py`) so the circle can't close again.
2. **The design's bbox rule self-destructed on 1px grid lines**: a vertical grid line
   zeroes its whole column's wood count, chopping the "longest contiguous run" to one cell.
   Found independently twice — by the session brain measuring the real images, and by the
   extract agent when the *synthetic* painter reproduced it. Fix: sliding-max support
   window before thresholding.
3. **Line-ness needed wood on BOTH sides** (extract agent): with "either side", stone
   flanks qualify and two facing walls forge phantom grid peaks (fitted spacing 20px
   instead of 32 on a fixture).
4. **The app's font beats classic templates**: its '3' is round-topped; the classic 5×7
   flat-top '3' template landed nearer '0'/'8' and the margin rule (rightly) refused to
   guess. Fix: per-digit alternate exemplars (`digits.ALT_TEMPLATES`), measured from the
   real pixels.
5. **A '3'-labelled stone under a blue wedge broke OCR twice**: first the undetected wedge
   became a phantom glyph; after detection, the wedge's antialiased fringe (not all
   blue-class) still survived mask subtraction. Fix: exact wedge-pixel subtraction plus a
   residue rule (glyphs entirely inside the wedge's bbox are fringe, not digits).

Meta-lesson: **thresholds designed without the target pixels are hypotheses, not specs.**
The cloud design's *reasoning* all held; its *constants and geometric assumptions* were
wrong wherever they met unmeasured reality. Budget for a calibration loop against real
inputs — and commit those inputs as regression fixtures the moment they're verified.

Process failure worth remembering: a `git add -A` meant for a docs commit swept the
build agents' in-progress files onto main un-reviewed (commit 12eb6bf) — while background
agents write the tree, stage explicit paths only.

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
