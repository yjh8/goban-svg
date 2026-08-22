# Build learnings — goban-svg

> What actually went wrong (failure modes, not just fixes), so the next session doesn't
> rediscover them. Newest first.

## Codex reviews need a PROJECT-LOCAL CODEX_HOME (2026-08-21/22)

Four consecutive ultra code reviews died mid-flight (40 min, 4 min, 30 min, ~2 min)
and were reported as "stopped/killed" — no API error, memory at 50% free, jetsam
logs clean. Cause: every codex surface on the Mac (Homebrew CLI, ChatGPT.app's
embedded codex, Codex Computer Use) shares `~/.codex`. Once the app started
rewriting `models_cache.json` in a newer schema, the CLI's periodic cache-TTL
renewal failed — `ERROR codex_models_manager::manager: failed to renew cache TTL:
missing field 'base_instructions'` repeated in the output right before each death.
A fifth run survived only when both isolated AND detached.

Standing fix: `scripts/codex-review.sh` — per-project `CODEX_HOME` under
`~/.codex-homes/<project>/`, detached via python3 `start_new_session` (macOS has
no `setsid`), prompt fed on stdin via `codex exec -` (dodges ARG_MAX and gives
stdin a clean EOF), `--ignore-user-config` so no personal MCP/plugins load.
**Credential handling took three review rounds to get right** (all against the
wrapper itself): first `auth.json` was copied INTO the repo — gitignoring stops
commits, not reads, and the tree is exactly what the agent is pointed at; moving
the copy outside helped but the next round probed it and showed a same-UID
`--sandbox read-only` agent can read it anywhere. First resolution: create no second copy at
all — `auth.json` is a SYMLINK to `~/.codex/auth.json`. Then round 5 refuted the
claim (mine) that the remaining same-UID exposure was unavoidable: it read the
Codex docs and found PERMISSION PROFILES in the installed CLI. Replacing
`--sandbox read-only` (which bypasses `default_permissions`) with a default-deny
filesystem profile makes `~/.ssh`, `~/.aws`, and the original `auth.json`
"Operation not permitted" to anything the model runs, while the repo and
toolchain stay readable — verified by probe, twice, including that `node`,
`pytest` and `git` still work (git needs `~/.gitconfig` explicitly, or it fails
with a bare "permission denied"). Round 6 closed the rest of the boundary:
Codex 0.144.1 defaults to `inherit=all` with name-based excludes OFF, so every
exported `*_TOKEN`/`*_KEY` in the launching shell was readable by a model
command via `env` — proved with a canary variable. The policy is now
`inherit="core"` + excludes on, command network off, `--strict-config` so a
malformed `-c` fails loudly instead of degrading into a review that reports
false findings, and the toolchain grant denies `/opt/homebrew/var` (service
data) while keeping `/opt/homebrew/etc` (node loads `openssl.cnf` from it — a
first attempt denied both and broke node).

Two lessons beyond the mechanism: **"that risk is inherent" is a claim to CHECK
against current docs, not to assert** — the reviewer found a real mitigation
shipping in the version already installed; and every tightening of a sandbox
needs a PROBE to discover what it broke, because the failure mode of a
too-strict profile is a review that silently cannot run its own verification
commands.

Diagnostic order for a review that dies for no reason:
(1) grep the output for `failed to renew cache TTL`, (2) check whether a session
file was ever created (absent = the stdin hang instead), (3) only then suspect
quotas or memory. Note: there is NO one-ultra-at-a-time limit and no subagent cap —
concurrent reviews just share the subscription's rolling usage budget.

## Author CSS silently defeats [hidden] (2026-08-21)

`button, .btn { display: inline-block }` — an ordinary author rule — beats the
UA's `[hidden] { display: none }` (author origin wins over UA origin), so every
`hidden` `<button>` on the page rendered anyway: the point inspector showed
確認目前判讀/移除記號 on every point, and two photo entry buttons leaked at
load. Caught only by a verify lens running a live Chrome probe
(`{hidden:true, rects:1}` is the tell). Fix: a global `[hidden]{display:none}`
kept LAST in the stylesheet (ID-specificity overrides stay deliberate).
Rule: any stylesheet that sets `display` on element/class selectors needs the
global `[hidden]` reset — and "it has the hidden attribute" is not evidence of
invisibility; probe `getClientRects()`.

## Two review-REJECT rounds changed the architecture, not the prose (2026-08-21)

The editor/photo-UX design took two Codex ultra REJECT rounds (4+4 BLOCKERs).
The keepers: (1) the reviewer MEASURED Pyodide phases (classify ≈ 0.1 s vs
rectify ≈ 2.5 s) and inverted the two-step design into "preview runs the whole
extraction, commit is free" — a better architecture no amount of prose review
would have found; (2) it caught that a redeploy had ALREADY silently replaced
the published 0.1.0 wheel bytes the integration prompt pins (D-008 exists
because of this); (3) cycling-to-empty would have destroyed board-4's own
K8 square — the counterexample was sitting in our fixtures. Reviews that only
bless prose are cheap; reviews that run measurements against the actual tree
are the ones worth 30 minutes.

## Browser-automation taps race smooth scrolling (2026-08-21)

Synthetic corner taps computed client coordinates from `getBoundingClientRect()`
while `openPicker`'s `scrollIntoView({behavior:"smooth"})` was still animating —
every tap landed ~500 px off and the quad went concave, with no error anywhere
(the click handler's guard just returned). Also: a status line read ~400 ms
after a rAF-coalesced redraw can still show the PREVIOUS draw's text. When
driving pages by synthetic events: wait for scroll to settle before measuring
rects, and verify state visually (screenshot), not by reading UI text mid-frame.

## Web arc (2026-08-19c): port the workflow, not just the function

Both Codex reviews of the web app converged on one lesson. The design review's
BLOCKER was that the CLI's defining workflow — edit the JSON, re-render — existed
as a *download* in the web design but not as an *action*: the function was ported,
the workflow wasn't. When wrapping a tool in a new surface, walk each documented
user loop end-to-end in the new surface before calling the design done.

Smaller ones worth keeping: (1) pure-stdlib Python + Pyodide on static Pages is a
zero-backend architecture where the privacy story ("images never leave the
browser") becomes CSP-*enforceable* rather than aspirational — a fleet-reusable
pattern for local-compute tools; (2) `pyodide-core` self-hosts in ~15 MB and boots
under `script-src 'self' 'wasm-unsafe-eval'` — no 'unsafe-eval' needed (314.0.5);
(3) the Chrome-extension `file_upload` on a stale element ref can silently reset
the page — re-`find` refs after any DOM-changing step, and treat "state looks
freshly booted" as the tell.

## ruff's format gate covers markdown code blocks (2026-08-19b)

CI went red after shipping because `ruff format --check .` (ruff 0.16) formats
the ```python blocks inside `docs/*.md` — hand-written doc snippets with
non-standard comment spacing fail the gate even when all real code is clean.
Rule: after editing any doc containing python code fences, run
`uv run ruff format .` before committing. (Also the reminder that bit twice
today: local "ruff clean" runs BEFORE later doc edits prove nothing about them.)

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

## Photo calibration #1: fix the geometry before touching thresholds (2026-08-20b)

The first real photo made every white stone vanish, and the reflex was "the
thresholds are wrong." They weren't — the hand-placed corners were ~0.4 cells
off, discs sampled stone edges, and the weakest class (white-on-wood) died
first. Fixing geometry (iterative corner auto-refinement reusing the proven
robust grid fitter) recovered a perfect board with zero threshold changes.
Rule: when a classifier degrades on new inputs, verify the sampling geometry
against ground truth FIRST — a rectified-image overlay showed the misalignment
in seconds. Also recorded: the naive refinements both failed (first/last raw
peaks → moiré poisoned; local crossing centroids → moiré-biased) — only the
full robust fit (median-gap + residual rejection) survived, which is an
argument for reusing battle-tested estimators over writing quick local ones.

## Backgrounded codex needs `< /dev/null` (2026-08-21)

Two review runs hung 45+ minutes at "Reading additional input from stdin...":
a backgrounded shell hands codex an open stdin pipe that never EOFs, and codex
waits for piped input BEFORE creating its session (so no session file appears —
that absence is the diagnostic tell). Append `< /dev/null` to every
non-interactive codex invocation. Diagnosis path that worked: session-file
absence → smoke tests proving model/config healthy → unswallowing the streamed
output revealed the stdin prompt. The codex-gate skill's own docs already
prescribe this; reading them first would have saved an hour.
