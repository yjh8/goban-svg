# Web app design — goban-svg phase 1 (no auth)

> Status: DESIGN (2026-08-19). Scope per D-004/D-005: upload → convert → clean visual +
> downloads, published on Joseph's Cloudflare, shareable with the 海峰棋院 staff for
> verification. Authentication (Google) deliberately deferred until this is functional
> and tested.

## Architecture: static Cloudflare Pages + Pyodide (all client-side)

The converter is pure-stdlib Python. That makes the cheapest, most robust web
architecture *no backend at all*:

- **Cloudflare Pages** serves a static site (`web/` in this repo, deployed via
  `wrangler pages deploy`).
- **Pyodide** (CPython on WebAssembly, pinned version, loaded from the official CDN)
  runs the actual `goban_svg` package **in the visitor's browser**. The package ships as
  a pure-Python wheel built by `uv build` and served as a static asset; the page installs
  it into Pyodide at load time.
- **Image decoding is the browser's job**: the page decodes ANY format the browser can
  display (PNG/JPEG/WebP/HEIC-on-Safari — i.e. staff phone photos work as inputs) via
  `createImageBitmap` → canvas → `ImageData`, and hands raw RGB bytes + dimensions to
  Python, which builds a `png_codec.Image` directly. Our PNG codec and the Pillow
  fallback are never needed in the browser path.
- Conversion runs `extract_position` → `render_svg` + `to_json` + `position_to_sgf` in
  Pyodide; results come back as strings.

Why not the alternatives:
- *Cloudflare Python Workers*: CPU-time limits (10ms free / 30s paid) are hostile to a
  multi-second pure-Python extraction, Pyodide-in-Workers is beta, and it adds a server
  where none is needed.
- *A Python server elsewhere*: more moving parts, hosting cost, and images leave the
  user's machine for no benefit.
- *JS port of the extractor*: a rewrite that would immediately drift from the tested
  Python (the real-image regression fixtures only guard the Python).

Auth later (phase 2): **Cloudflare Access** in front of the Pages project with Google as
IdP — config-only, zero app-code change, which is exactly D-004's sequencing.

## UX (single page, zh-TW, 圍棋 terminology)

Flow: 上傳 → 轉換 → 結果.

1. **Upload zone** (drag-drop + click): 「拖曳或點擊上傳棋盤圖片（App 截圖效果最佳，
   照片為實驗性支援）」. Preview thumbnail after selection.
2. **Convert button**: 「轉換成棋譜圖」. Disabled until an image is selected. Progress
   state 「辨識棋盤中…」 (first visit also shows 「載入辨識引擎中…」 while Pyodide
   boots, ~5–10 s, cached afterwards).
3. **Result panel**:
   - The clean SVG rendered inline (the 棋譜圖), side by side with the uploaded image
     on wide screens.
   - Summary line in the 圍棋 register: 「19×19 棋盤：黑 20 子、白 20 子、記號 2 個、
     手數標記 3 個」.
   - Download buttons (Blob URLs): 「下載 SVG 棋譜圖」 「下載 JSON（可手動修正後重新
     轉換）」 「下載 SGF 棋譜」.
   - Warnings under 「請人工確認」: phase 1 maps the extractor's known warning
     patterns to zh-TW client-side (regex on the English strings, point names kept
     as-is, e.g. 「D14 的手數無法辨識，請對照原圖確認」); unmatched warnings show
     verbatim. Full Python-side i18n is a later refinement.
   - Errors (no board found / cropped) in friendly zh-TW with the suggestion to
     re-capture; raw message in a collapsible detail.
4. **Footer**: version, 「圖片僅在您的瀏覽器中處理，不會上傳到任何伺服器」 (true, and
   worth saying), link to the JSON-correction explanation.

Language: all UI copy 繁體中文 (zh-TW) using standard 圍棋 vocabulary (棋盤、棋譜、
黑棋/白棋、手數、星位、記號). `<html lang="zh-Hant-TW">`. The CLI/README stay English
(developer-facing; fleet convention).

## Repo layout addition

```
web/
  index.html      # single page, zh-TW
  app.js          # pyodide bootstrap, image decode, convert pipeline, downloads
  style.css
  wheels/goban_svg-<ver>-py3-none-any.whl   # built by `uv build`, committed or built in deploy script
scripts/deploy-web.sh   # uv build → copy wheel → wrangler pages deploy web/
```

## Risks / open items

- **Real-board photos**: the extractor assumes screenshot-flat geometry; photos with
  perspective will fail with warnings/errors rather than wrong output (fail-loud design).
  Staff test examples will tell us whether a photo-rectification stage is worth building.
- **Pyodide performance**: extraction is a few seconds natively; expect ~5–20 s under
  WASM on a ~950 px image. Mitigations if needed: downscale huge inputs client-side
  before handing to Python (bounded d≥~20 px), run in a Web Worker to keep the UI live.
- **Warning i18n drift**: the zh-TW regex map lives in app.js and can lag extractor
  wording; acceptable for phase 1, noted for phase 2.
- Pages project name: `goban-svg` → `https://goban-svg.pages.dev` (shareable link).

## Amendments (2026-08-19, post Codex design review — APPROVE-10, all accepted)

1. **Correction loop is in phase 1** (was the BLOCKER): expandable JSON editor prefilled
   from extraction + JSON file import + 「套用修正並重新產生」 button → `Position.from_json`
   → re-render SVG/SGF in the worker. Validation errors surface with a zh-TW lead + raw
   detail collapsible.
2. **Persistent module Web Worker is mandatory**, prewarmed on page load; RGB buffer is
   transferred (not copied); job IDs discard stale results.
3. **Input bounds**: files > 25 MB rejected; decoded bitmaps downscaled during canvas draw
   to ≤ 1400 px long edge (keeps 19×19 grid spacing ≥ ~60 px); < 300 px rejected as too
   small. All with zh-TW messages.
4. **Pixel contract**: `createImageBitmap(file, {imageOrientation: "from-image"})` →
   draw on an opaque **black** canvas (matches the CLI's alpha-over-black policy) at
   target size → `getImageData` (sRGB) → pack RGBA→RGB in JS → transfer to worker →
   single copy into a Python `bytearray`; destroy every proxy after use.
5. **Format reality**: decode failures are distinguished from extraction failures; HEIC
   on non-Safari gets 「此瀏覽器無法讀取 HEIC，請改用 Safari 或先轉成 JPEG」.
6. **Verification UI**: web renders use `render_svg(coords=True)`; mobile gets a
   full-width 原圖/棋譜圖 toggle instead of two narrow columns; warnings name points that
   are now findable via the coordinate labels.
7. **Testing**: Chrome E2E via browser automation against the three real fixtures
   (upload → summary + SVG + downloads + JSON re-render), a deployed smoke check
   (headers, wheel fetch, boot), and staff-device acceptance for Safari/mobile.
   Full Playwright matrix deferred — recorded as a deliberate phase-1 gap.
8. **Privacy enforced**: Pyodide core is **self-hosted** (pinned version, each file
   < 25 MiB Pages limit) and `_headers` ships a strict CSP (`default-src 'self'`,
   `script-src 'self' 'wasm-unsafe-eval'`, `connect-src 'self'`, `img-src 'self' blob:
   data:`, `frame-ancestors 'none'`, `Referrer-Policy: no-referrer`) plus
   `X-Robots-Tag: noindex`. If Pyodide boot requires 'unsafe-eval', add it with a note.
9. **Phase-2 Access matrix recorded**: production + preview (+ any custom domain) need
   separate Access policies; custom-domain-before-Access sequencing; and phase 1 makes
   the app/wheel publicly copyable — Access later gates the hosted service only.
10. **Wheel/version determinism**: `scripts/deploy-web.sh` builds the wheel fresh, stages
    `web/` + wheel + pyodide into `web-dist/` (gitignored), writes `gen/config.js` with
    the exact wheel filename + versions; the worker asserts `goban_svg.__version__`.
    Wheels are never committed.
11. Minors accepted: Blob URL lifecycle (revoke prior, ASCII filenames goban-board.svg/
    .json/.sgf, correct MIME); zh-TW generic fallback for unmapped warnings (raw English
    in 技術詳情 only); SGF button notes lossiness 「（靜態盤面，記號顏色不保留）」;
    stale Cloudflare rationale lines corrected (paid Workers CPU is configurable to 5 min;
    Workers Static Assets is CF's newer recommendation — Pages remains fine here);
    a11y basics (labelled file input, keyboard operable, aria-live status, 44 px targets).

---

## Correction editor + photo-confidence flow (2026-08-21 design — staff feedback round 1)

> Status: DESIGN. Trigger: first staff feedback — two testers, both failed first try on
> *monitor photos* (measured post-mortem: photo-mode-design.md § Calibration log,
> finding #2). The failures decompose into (a) corner placement is a precision cliff,
> (b) failures are silent until the very end, (c) recovery requires editing raw JSON —
> a programmer's surface. Three UI changes + two additive payload fields close (a)–(c).
> Classifier thresholds are deliberately NOT touched: calibration waits for the
> physical-board corpus (Joseph is collecting; finding #2 records the hypotheses).

### A. Corner-placement guidance (photo mode)

The picker's instruction line becomes a placement *rule*, illustrated:

- Copy: 「把控制點放在**最外圈格線的交叉點**上（例：左上角 = 第19路橫線與第1路直線的
  交點）。不要放在木框邊緣、視窗外框或座標數字上。」
- A right/wrong thumbnail pair next to the copy (two small static images in
  `web/assets/`, cropped from a real photo: handle ON the corner intersection ✓ /
  handle out on the frame ✗). Staff evidence: both failures placed corners on
  window chrome or outside the outer lines.
- Anti-goal guard: when the selected image *is a screen capture territory* we say so
  up front — the photo entry points (picker section + fallback button area) gain one
  hint line: 「拍攝螢幕畫面？直接截圖用自動辨識，效果遠比拍照好。」(finding #2:
  identical content extracts perfectly as a screenshot, catastrophically as a photo).

### B. Rectified-grid confirmation step (photo mode)

Fail-closed refinement currently has *no verifier* when it declines — the user waits
~35 s and gets a wrong board plus a warning at the bottom. The fix mirrors how
calibration itself is done: look at the rectified image with the grid drawn on it.

- New worker op `{type:"photo-preview", id, width, height, buf, corners, size}`:
  runs `refine_corners` + `rectify_board` ONLY (~seconds, no classification) and
  returns `{rectifiedRGBA (transferred), rectSize, gridXs, gridYs, corners, refined}`
  where `corners` are the (possibly refined) source-space corners and `refined` says
  whether auto-refinement verified.
- UI (replaces the picker's direct 開始辨識): after corner confirm → preview panel
  shows the rectified board with the canonical grid overlaid (canvas), plus an honest
  status line: 自動微調完成 ✓ / 「自動微調無法確認格線，將依你點的角點辨識」⚠.
  Buttons: 「格線有對齊 → 開始辨識」 and 「沒對齊 → 重新點角」.
- Confirm sends the normal `photo` job with the *returned* corners and a new
  `refine:false` flag (worker passes `refine=False` through) — refinement never runs
  twice, so the two-step flow costs one extra rectify (~1–2 s) and splits the long
  wait into two legible halves.
- The human becomes the verifier the fail-closed design lacks; a misaligned grid is
  visible in about two seconds (the exact diagnostic that solved findings #1 and #2).

### C. Click-to-cycle correction editor (both modes)

The CLI's correction loop was ported as a JSON textarea — the function, not the
workflow (the same lesson as the 2026-08-19 BLOCKER). The staff's actual loop is
"look at the board, tap the wrong point". Pattern proven by the blindspot player
(海峰 staff member's app, built on our wheel): click an intersection to cycle
空 → 黑 → 白 → 空 — verified live on their deployment, including instant re-render.

- One click handler on the inline SVG's container (`#svg-holder`); map click →
  SVG viewBox coords → nearest intersection; accept within 0.42·cell (a miss does
  nothing — never a wrong edit). No per-intersection DOM nodes.
- The edit mutates the SAME JSON that `#json-editor` holds (it stays the single
  source of truth), then triggers the existing `rerender` job — busy-freeze,
  job-id staleness, provenance (`photoModeActive`), and Python-side validation are
  all inherited unchanged. The JSON textarea stays for power users (labels, bulk).
- Cycling a point to empty also deletes that point's label and mark (no orphaned
  annotations); marks additionally get blindspot-style ✕ overlay chips
  (「點 ✕ 移除記號」).
- **Uncertain points are pre-marked**: dashed rings on the board at every point the
  extractor left empty or flagged (from the structured `uncertain` payload below),
  so the 「請人工確認」 list becomes visible geography instead of a text wall.
  Tapping a ringed point cycles it like any other; its ring clears once edited.
- Single-level undo stack (復原, JSON snapshots, cap ~50) — protects against
  mis-taps, which blindspot's editor lacks.
- Mobile: the 0.42·cell hit radius is ~8 CSS px on a 350 px board — tight but
  fail-safe (recorded limitation; native pinch-zoom still works on the pane).

### Payload additions (both additive; `warnings` strings unchanged)

1. `geom: {cell, x0, y0}` — intersection (col i, row r) sits at
   `(x0 + (i-1)·cell, y0 + (size-r)·cell)` in SVG user units. Computed in the worker
   driver from render.py's own layout constants — the client never re-derives the
   geometry. (Motivated by observing the blindspot integration hard-code a
   re-implementation of render_svg's margins against cell=54 — a coupling that
   breaks silently if our layout changes. Our own UI must not repeat that.)
2. `uncertain: [{point, kind}]` — new additive `ExtractionResult.uncertain` field
   emitted by both extractors wherever they currently emit a point-naming warning
   (kinds: ambiguous, off-image, no-reference, warm-bright, unreadable-label,
   ambiguous-color). The zh-TW warning regex map is untouched (its retirement is
   the existing stable-warning-codes backlog item); the editor keys on this
   structured list only — never on parsed warning strings.

### Out of scope (deliberate)

- Classifier threshold changes (white floor, glyph-robust white statistic,
  38-line least-squares homography) — blocked on the physical-board corpus,
  hypotheses recorded in finding #2.
- Label/mark *creation* UI (JSON editor covers it; photo mode has none by design).
- Editing marks' colors, SGF round-trip of edits (inherits existing lossiness note).
