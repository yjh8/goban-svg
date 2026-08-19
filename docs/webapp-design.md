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
