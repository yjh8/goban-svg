# 棋盤辨識整合包 — Build prompt for the 序盤盲區庫 AI

> 給 AI 開發助手的完整建置提示。目標：在既有的 Next.js 應用（序盤盲區庫，Vercel）中
> 加入「棋盤圖片 → 盤面資料 + 棋譜圖」功能。辨識引擎已經完成、經過測試、可直接下載 —
> **你的工作是嵌入它，不是重寫它。**
>
> (Prompt for an AI coding assistant. Goal: add "board image → position data + clean
> diagram" to the existing Next.js app. The recognition engine is finished, tested, and
> downloadable — your job is to EMBED it, not rebuild it.)

---

## 1. What you are integrating

A working converter that turns a Go-app screenshot into:

- **Structured position JSON** (every stone, move-number labels 手數, marker badges 記號)
- **A clean SVG diagram** with coordinate labels (棋譜圖，含座標)
- **SGF export** (static position)

Live reference implementation — open it, use it, view its source; your integration must
match its behavior: **https://goban-svg.pages.dev** (zh-TW UI, runs entirely client-side).

The engine is a **pure-Python, zero-dependency wheel** (works on any Python ≥ 3.10 and
under Pyodide in the browser):

```
URL:    https://goban-svg.pages.dev/wheels/goban_svg-0.1.0-py3-none-any.whl
SHA256: 5964bad5d9f5c0a5c0b8bf47d55a359953d3249e23dbe0d82ea3cb7942c7c836
Size:   65,760 bytes (pure Python — the .py source is inside; read it if unsure)
```

It is backed by 427 tests including pixel-exact regression fixtures on real app
screenshots. **Do not re-implement, port, or "tune" any of it** — every threshold in the
extractor was calibrated against real images; a rewrite will be wrong in invisible ways.

## 2. The Python API surface (everything you need)

```python
from goban_svg.png_codec import Image, load_image, PngError
from goban_svg.extract import extract_position, ExtractionError
from goban_svg.render import render_svg
from goban_svg.sgf import position_to_sgf, SgfError
from goban_svg.board import Position

# Input path 1 — raw pixels you decoded yourself (browser/canvas route):
img = Image(width=w, height=h, pixels=bytearray(rgb))   # packed RGB8, len == w*h*3

# Input path 2 — a PNG file/bytes (server route):
img = load_image("board.png")                            # own PNG codec; JPEG needs Pillow installed

result = extract_position(img)      # raises ExtractionError on non-boards (fail-loud)
pos = result.position               # size, stones, marks, labels
warnings = result.warnings          # list[str] — MUST be shown to the user (see §6)

svg_text  = render_svg(pos, coords=True)   # coords=True is required for verification UX
json_text = pos.to_json()                  # the human-editable interchange format
sgf_text  = position_to_sgf(pos)           # lossy: mark colors are not preserved

# The correction loop (required feature, see §7):
pos2 = Position.from_json(edited_json_text)   # raises ValueError with a clear message
```

JSON schema (`pos.to_json()` emits exactly this shape; columns skip `I`, rows count from
the bottom; sizes 2–25 supported):

```json
{
  "size": 19,
  "stones": {"black": ["C8", "..."], "white": ["D14", "..."]},
  "marks": [
    {"point": "D2",  "type": "square",   "color": "black"},
    {"point": "D14", "type": "triangle", "color": "#2b5fe3"}
  ],
  "labels": {"D14": "3"}
}
```

## 3. Choose ONE integration route

### Route A — client-side in the browser via Pyodide (recommended; this is what the reference site does)

No backend at all; images never leave the user's browser; free at any scale.
Load Pyodide (pin a version; ≥ 0.26 works, the reference uses 314.0.5) in a **module Web
Worker**, install the wheel by URL, run the driver below. First load ≈ 5–8 s (cached
afterwards); a 950 px board converts in ≈ 2–6 s.

Worker driver (this is the reference site's actual bridge, condensed — reuse it):

```js
// worker.js  (module worker)
const DRIVER = `
import json as _json
from goban_svg.board import Position
from goban_svg.extract import ExtractionError, extract_position
from goban_svg.png_codec import Image
from goban_svg.render import render_svg
from goban_svg.sgf import position_to_sgf

def _ok(pos, warnings):
    black = sum(1 for c in pos.stones.values() if c == "black")
    return _json.dumps({"ok": True, "svg": render_svg(pos, coords=True),
        "json": pos.to_json(), "sgf": position_to_sgf(pos), "size": pos.size,
        "black": black, "white": len(pos.stones) - black,
        "marks": len(pos.marks), "labels": len(pos.labels), "warnings": list(warnings)})

def convert_rgb(js_buf, width, height):
    try:
        pixels = bytearray(width * height * 3)
        js_buf.assign_to(pixels)                      # single-copy JS->Python bridge
        result = extract_position(Image(width=width, height=height, pixels=pixels))
        return _ok(result.position, result.warnings)
    except ExtractionError as exc:
        return _json.dumps({"ok": False, "kind": "extract", "message": str(exc)})
    except Exception as exc:
        return _json.dumps({"ok": False, "kind": "internal", "message": str(exc)})

def rerender(text):
    try:
        return _ok(Position.from_json(text), [])
    except ValueError as exc:
        return _json.dumps({"ok": False, "kind": "invalid", "message": str(exc)})
`;

let py;
async function boot() {
  const { loadPyodide } = await import("<your-pyodide>/pyodide.mjs");
  py = await loadPyodide({ indexURL: "<your-pyodide>/" });
  await py.loadPackage("https://goban-svg.pages.dev/wheels/goban_svg-0.1.0-py3-none-any.whl");
  py.runPython(DRIVER);
}
self.onmessage = async ({ data: m }) => {
  if (m.type === "boot") { await boot(); postMessage({ type: "ready" }); return; }
  try {
    let raw;
    if (m.type === "convert") {
      py.globals.set("_RGB", new Uint8Array(m.buf));
      try { raw = py.runPython(`convert_rgb(_RGB, ${m.width|0}, ${m.height|0})`); }
      finally { py.runPython("del _RGB"); }
    } else {  // rerender
      py.globals.set("_TXT", m.json);
      try { raw = py.runPython("rerender(_TXT)"); } finally { py.runPython("del _TXT"); }
    }
    postMessage({ type: "result", id: m.id, payload: JSON.parse(raw) });
  } catch (err) { postMessage({ type: "error", id: m.id, message: String(err) }); }
};
```

Browser-side pixel contract (main thread — every step is load-bearing; the extractor
was calibrated for exactly this normalization):

```js
const bitmap = await createImageBitmap(file, { imageOrientation: "from-image" }); // EXIF!
const long = Math.max(bitmap.width, bitmap.height);
if (long < 300) throw new Error("too small");
const s = Math.min(1, 1400 / long);                    // downscale cap: memory + speed
const w = Math.max(1, Math.round(bitmap.width * s)), h = Math.max(1, Math.round(bitmap.height * s));
const canvas = new OffscreenCanvas(w, h), ctx = canvas.getContext("2d");
ctx.fillStyle = "#000"; ctx.fillRect(0, 0, w, h);       // composite alpha over BLACK
ctx.drawImage(bitmap, 0, 0, w, h); bitmap.close();
const rgba = ctx.getImageData(0, 0, w, h).data;         // sRGB
const rgb = new Uint8Array(w * h * 3);
for (let i = 0, j = 0; j < rgb.length; i += 4, j += 3) {
  rgb[j] = rgba[i]; rgb[j+1] = rgba[i+1]; rgb[j+2] = rgba[i+2];
}
worker.postMessage({ type: "convert", id, width: w, height: h, buf: rgb.buffer }, [rgb.buffer]);
```

Route-A hardening (all shipped on the reference site — replicate): run conversion in a
persistent worker with job IDs (discard stale results); invalidate in-flight jobs when a
new image is selected; disable the JSON editor while a job runs; recreate a crashed
worker (bounded retries); revoke old preview/download Blob URLs; cap JSON input ~2 MB.

### Route B — server-side on Vercel (simpler wiring, images leave the browser)

A Python serverless function: `pip install <wheel-url>` plus `pillow` (for JPEG uploads),
then `load_image` → `extract_position` → return `{svg, json, sgf, warnings}`. Native
extraction takes well under a second. Mind Vercel body-size limits (~4.5 MB — downscale
client-side first anyway) and never return raw tracebacks: catch `ExtractionError` /
`PngError` / `ValueError` and map them per §6.

## 4. UI requirements (zh-TW, 圍棋 terminology — ready-made copy)

Use these strings verbatim (they were written for this audience):

- 上傳區：「拖曳圖片到這裡，或點擊選擇檔案」／「App 截圖效果最佳，實體棋盤照片為實驗性支援」
- 按鈕：「轉換成棋譜圖」；進行中：「辨識棋盤中…」；首次載入（Route A）：「載入辨識引擎中…」
- 結果摘要：「{size}×{size} 棋盤：黑 {black} 子、白 {white} 子、記號 {marks} 個、手數標記 {labels} 個」
- 下載：「下載 SVG 棋譜圖」「下載 JSON（可修正）」「下載 SGF 棋譜（靜態盤面，記號顏色不保留）」
- 修正區：「修正辨識結果（編輯 JSON 後重新產生）」／按鈕「套用修正並重新產生」
- Route A 隱私句（只有全前端才可以寫）：「圖片僅在您的瀏覽器中處理，不會上傳到任何伺服器」

Layout: show the **original image and the diagram side by side** (stacked with a
原圖/棋譜圖 toggle on mobile). The SVG must render with coordinates (`coords=True`) so a
reviewer can locate points named in warnings.

## 5. What it can and cannot do (set user expectations honestly)

- Excellent on **app screenshots**: full boards, 9/13/19 standard (2–25 accepted with a
  warning), stones on edges, long walls, numbered move labels (multi-digit OK), the app's
  colored corner wedge badges (recorded as triangle marks with the badge color), solid
  square markers on empty points.
- **Photos of physical boards**: experimental. Perspective/shadows usually make it fail —
  and it fails LOUD (`ExtractionError`), never with a silently wrong board.
- It reads static positions; it does not replay games or resolve captures.

## 6. Warnings and errors — show them, translated

The engine never guesses silently: uncertain readings surface as English warning strings
naming the exact point. **Always display them** under a 「請人工確認」 heading. Ready
zh-TW mappings (regex → template):

| English pattern | zh-TW |
|---|---|
| `unreadable label on the (black\|white) stone at {PT}` | 「{PT} 上的手數無法辨識，請對照原圖人工確認。」 |
| `ambiguous stone color at {PT} ... read as (black\|white)` | 「{PT} 的棋子顏色不明確（已判讀為黑棋/白棋），請確認。」 |
| `unusual board size {N}x{N}` | 「非標準棋盤大小 {N}×{N}（標準為 9、13、19），請確認截圖完整。」 |
| `almost no wood margin ... cropped mid-board` | 「棋盤外緣留白不足，截圖可能被裁切 — 請確認棋盤大小與座標。」 |
| `grid spacing differs between axes` | 「圖片可能被不等比例縮放，辨識品質可能受影響。」 |
| *(unmatched)* | 「發現一項需要確認的狀況（原文見技術訊息）。」+ 原文放在收合區 |

`ExtractionError` messages likewise: map `sizes 2-25` → 超出支援範圍；`no board found` →
「無法在圖片中找到棋盤…」；`cropped screenshot` → 「棋盤似乎被裁切…」；grid-fit failures →
「無法辨識棋盤格線…」。Show the raw English in a collapsible 「技術詳情」.

## 7. The correction loop is REQUIRED, not optional

The defining workflow: recognition will occasionally misread a move number or stone —
the user fixes it in seconds by editing the JSON and re-rendering (`Position.from_json`
→ `render_svg`). Ship an editable JSON view (or import) with a 「套用修正並重新產生」
action. A download-only JSON is not a correction feature. Validation errors from
`from_json` are precise and name the offending point — surface them.

## 8. Acceptance checklist (verify before calling it done)

1. Convert the same screenshot on your integration AND on https://goban-svg.pages.dev —
   the summary counts (黑/白/記號/手數) must match exactly.
2. Edit a label in the JSON, re-render, and see the diagram change.
3. Upload a non-board image → the friendly zh-TW error appears (no traceback, no crash).
4. Warnings from a slightly-degraded screenshot appear in zh-TW with the point names.
5. (Route A) DevTools console stays free of errors during all of the above.

## 9. Provenance

Built and tested by Joseph Huang's toolchain, 2026-08-19–20; provided for use in
序盤盲區庫. The wheel embeds its full Python source. Questions or wrong-conversion
reports (please include the original image) go back to Joseph — verified cases become
permanent regression tests in the engine.
