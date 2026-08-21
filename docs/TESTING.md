# TESTING — goban-svg

## Current status (2026-08-21)

- **486 tests, all green**; ruff check + format clean; CI green on Python 3.10 + 3.13
  (ubuntu). Local dev runs on 3.14 — the suite is verified on a real 3.10 too.
- New since 0.1.0: board-4 real-screenshot fixture; uncertain↔warning pairing
  tests (strings byte-frozen); PhotoArtifact single-classification-rectification
  counter tests; BoardGeometry↔SVG round-trip.
- Known-broken: nothing.

## How to run

```bash
uv sync --dev
uv run pytest                 # whole suite, ~10s
uv run pytest tests/test_extract.py -q     # one module
uv run ruff check . && uv run ruff format --check .
```

## Harness shape (what a new session must know)

- **Synthetic round-trips** are the core: `render_png` (app-style painter) is the
  extractor's fixture generator — paint a `Position`, extract it, assert exact
  equality. Zero external fixtures, zero randomness (seeded LCG noise).
- **The synthetic loop is circular by construction** (painter and extractor share
  assumptions). The anti-circularity gate is `tests/test_real_examples.py`: the
  four committed screenshots (boards 1–3 hand-verified, board-4 verified via a
  class-ring overlay diff) must extract byte-identically to their verified
  `examples/*.json`. If an extractor change legitimately improves a reading,
  re-verify visually and regenerate the sidecar deliberately (D-003).
- **Painter geometry constants are load-bearing**, not cosmetic — each is marked
  `EXTRACTOR-COUPLED` in `render.py`. Changing one makes round-trip failures look
  like extractor bugs.
- **ruff formats the ```python blocks inside docs/*.md** — CI's format gate covers
  docs too. Run `uv run ruff format .` after editing docs with code blocks.
- pytest quirk: none known. Tests never touch the network or absolute paths.

## Web app (added 2026-08-19)

- **Deployed smoke check**: `scripts/smoke-web.sh [url] [wheel]` — asserts CSP +
  noindex headers, wheel/pyodide fetchable, config coherence. Runs automatically at
  the end of `scripts/deploy-web.sh --deploy`.
- **Browser E2E procedure** (Chrome automation or by hand, after any web change):
  load the site → wait for 「辨識引擎已就緒」 → upload `examples/board-1.png` →
  轉換 → expect exactly 「19×19 棋盤：黑 20 子、白 20 子、記號 2 個、手數標記 3 個」,
  coordinates on the SVG, no warnings box → edit the JSON (D14 label) → 套用修正 →
  the SVG updates → upload a non-board image → friendly zh-TW error. Console must
  stay error-free (CSP violations show there).
- **Editor E2E** (added 2026-08-21, verified locally pre-deploy): after a board-1
  convert — click an empty intersection → black stone + summary 黑 21 → 復原 →
  黑 20; the honesty note 「沒有圈記的點也可能有誤」 shows even with zero warnings;
  mark ✕ chips on D14/D2; desktop hides the 原圖 thumbnail button.
- **Photo checkpoint E2E** (added 2026-08-21): upload `screenshots/photo-1.jpeg` →
  照片模式 → place 4 corners → 依角點辨識 → 先確認格線 → checkpoint shows the
  rectified board + red canonical grid + honest refine status → 格線有對齊 →
  result carries 實驗性 banner + refined ⚠ line + kind-aware review rings →
  tapping a ringed point opens the inspector (確認目前判讀 only on classification
  kinds; 移除記號 only when a mark exists) → choosing 白 places the stone and
  clears ONLY that ring. Automation gotcha: wait for smooth scrolling to settle
  before computing tap coordinates (build-learnings 2026-08-21).
- **Known phase-1 gaps** (deliberate, from the web code review): no committed
  Playwright matrix (Firefox/WebKit coverage = staff-device acceptance); per-label
  length inside the 2 MB JSON cap is unbounded; warning zh-TW map is regex-based and
  can drift from extractor wording (backlog: stable warning codes).
