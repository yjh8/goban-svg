# TESTING — goban-svg

## Current status (2026-08-19)

- **427 tests, all green**; ruff check + format clean; CI green on Python 3.10 + 3.13
  (ubuntu). Local dev runs on 3.14 — the suite is verified on a real 3.10 too.
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
  three committed screenshots must extract byte-identically to their hand-verified
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
- **Known phase-1 gaps** (deliberate, from the web code review): no committed
  Playwright matrix (Firefox/WebKit coverage = staff-device acceptance); per-label
  length inside the 2 MB JSON cap is unbounded; warning zh-TW map is regex-based and
  can drift from extractor wording (backlog: stable warning codes).
