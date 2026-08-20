# 棋盤辨識整合包 — 給「序盤盲區庫」AI 的建置提示（v1 中文版）

> **教學範例** — 這是「棋盤辨識整合包」第一版提示的完整中文版，保留作為
> 「結構良好的 AI 建置提示」示範：任務框架 → 固定事實 → API 介面 → 路線選擇 →
> 現成文案 → 能力邊界 → 驗收清單。實際送出的是 v2（為 Sonnet 移除了所有
> 「留給 AI 判斷」的空隙：路線改為指定、程式碼從片段變成完整檔案、加上
> MUST NOT 硬規則與疑難排解表）。比較 v1 與 v2，就是一堂「提示精確度」的課。

---

> 給 AI 開發助手的完整建置提示。目標：在既有的 Next.js 應用（序盤盲區庫，Vercel）
> 中加入「棋盤圖片 → 盤面資料 + 棋譜圖」功能。辨識引擎已經完成、經過測試、
> 可直接下載 — **你的工作是嵌入它，不是重寫它。**

## 1. 你要整合的東西

一個已經能用的轉換器：把圍棋 App 截圖變成 —

- **結構化盤面 JSON**（每顆棋子、手數標記、記號徽章）
- **含座標的 SVG 棋譜圖**（乾淨、可縮放）
- **SGF 匯出**（靜態盤面）

線上參考實作 — 打開它、使用它、檢視它的原始碼；你的整合行為必須與它一致：
**https://goban-svg.pages.dev**（繁體中文介面，完全在瀏覽器端執行）。

引擎是一顆**純 Python、零相依套件的 wheel**（任何 Python ≥ 3.10 都能跑，
瀏覽器裡用 Pyodide 也能跑）：

```
URL:    https://goban-svg.pages.dev/wheels/goban_svg-0.1.0-py3-none-any.whl
SHA256: 5964bad5d9f5c0a5c0b8bf47d55a359953d3249e23dbe0d82ea3cb7942c7c836
大小:   65,760 bytes（純 Python — 原始碼就在 wheel 裡，不確定就打開看）
```

它背後有 427 個測試，包括對真實 App 截圖的像素級迴歸測試。
**不要重新實作、移植或「微調」任何部分** — 抽取器裡的每個閾值都是對照真實圖片
校準出來的；重寫的版本會以看不見的方式出錯。

## 2. Python API 介面（你需要的全部）

```python
from goban_svg.png_codec import Image, load_image, PngError
from goban_svg.extract import extract_position, ExtractionError
from goban_svg.render import render_svg
from goban_svg.sgf import position_to_sgf, SgfError
from goban_svg.board import Position

# 輸入路徑 1 — 自己解碼好的像素（瀏覽器 canvas 路線）：
img = Image(width=w, height=h, pixels=bytearray(rgb))   # packed RGB8, len == w*h*3

# 輸入路徑 2 — PNG 檔案／bytes（伺服器路線）：
img = load_image("board.png")            # 內建 PNG 解碼器；JPEG 需另裝 Pillow

result = extract_position(img)   # 非棋盤圖 → 拋出 ExtractionError（大聲失敗）
pos = result.position            # size、stones、marks、labels
warnings = result.warnings       # list[str] — 一定要顯示給使用者（見 §6）

svg_text  = render_svg(pos, coords=True)   # coords=True 是驗證體驗的必要條件
json_text = pos.to_json()                  # 可人工編輯的交換格式
sgf_text  = position_to_sgf(pos)           # 有損：記號顏色不保留

# 修正迴圈（必要功能，見 §7）：
pos2 = Position.from_json(edited_json_text)   # 錯誤時拋 ValueError，訊息會指名座標
```

JSON 結構（`pos.to_json()` 輸出的正是這個形狀；直行字母跳過 `I`，
橫列由下往上數；支援 2–25 路）：

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

## 3. 二選一的整合路線

### 路線 A — 瀏覽器端 Pyodide 執行（推薦；參考站就是這樣做的）

完全不需要後端；圖片不離開使用者的瀏覽器；任何流量都免費。
在 **module Web Worker** 裡載入 Pyodide（鎖定版本；≥ 0.26 可用，參考站用
314.0.5），用 URL 安裝 wheel，執行下面的 driver。首次載入約 5–8 秒
（之後有快取）；950 px 的棋盤約 2–6 秒轉完。

Worker driver（參考站實際使用的橋接程式，濃縮版 — 直接沿用）：

```js
// worker.js（module worker）
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
        js_buf.assign_to(pixels)                      # JS→Python 單次複製
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
  const { loadPyodide } = await import("<你的-pyodide>/pyodide.mjs");
  py = await loadPyodide({ indexURL: "<你的-pyodide>/" });
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

瀏覽器端像素合約（主執行緒 — **每一步都是必要的**，引擎是照這個正規化校準的）：

```js
const bitmap = await createImageBitmap(file, { imageOrientation: "from-image" }); // EXIF！
const long = Math.max(bitmap.width, bitmap.height);
if (long < 300) throw new Error("圖片太小");
const s = Math.min(1, 1400 / long);                    // 縮圖上限：記憶體 + 速度
const w = Math.max(1, Math.round(bitmap.width * s)), h = Math.max(1, Math.round(bitmap.height * s));
const canvas = new OffscreenCanvas(w, h), ctx = canvas.getContext("2d");
ctx.fillStyle = "#000"; ctx.fillRect(0, 0, w, h);       // 透明度合成到「黑色」底
ctx.drawImage(bitmap, 0, 0, w, h); bitmap.close();
const rgba = ctx.getImageData(0, 0, w, h).data;         // sRGB
const rgb = new Uint8Array(w * h * 3);
for (let i = 0, j = 0; j < rgb.length; i += 4, j += 3) {
  rgb[j] = rgba[i]; rgb[j+1] = rgba[i+1]; rgb[j+2] = rgba[i+2];
}
worker.postMessage({ type: "convert", id, width: w, height: h, buf: rgb.buffer }, [rgb.buffer]);
```

路線 A 強化清單（參考站都已實作 — 請照做）：persistent worker + job ID
（丟棄過期結果）、換圖時作廢進行中的任務、轉換中鎖住 JSON 編輯器、
worker 崩潰後有上限地重建、汰換舊的 preview／下載 Blob URL、JSON 輸入上限約 2 MB。

### 路線 B — Vercel 伺服器端（接線較簡單；圖片會離開瀏覽器）

Python serverless function：`pip install <wheel-url>` 加 `pillow`（收 JPEG 上傳用），
然後 `load_image → extract_position`，回傳 `{svg, json, sgf, warnings}`。
原生執行不到一秒。注意 Vercel body 上限（約 4.5 MB — 反正請先在前端縮圖），
且絕不回傳原始 traceback：捕捉 `ExtractionError / PngError / ValueError`，
照 §6 對應成友善訊息。

## 4. UI 要求（繁體中文，圍棋用語 — 現成文案）

以下字串請直接使用（就是為這個對象寫的）：

- 上傳區：「拖曳圖片到這裡，或點擊選擇檔案」／「App 截圖效果最佳，實體棋盤照片為實驗性支援」
- 按鈕：「轉換成棋譜圖」；進行中：「辨識棋盤中…」；首次載入（路線 A）：「載入辨識引擎中…」
- 結果摘要：「{size}×{size} 棋盤：黑 {black} 子、白 {white} 子、記號 {marks} 個、手數標記 {labels} 個」
- 下載：「下載 SVG 棋譜圖」「下載 JSON（可修正）」「下載 SGF 棋譜（靜態盤面，記號顏色不保留）」
- 修正區：「修正辨識結果（編輯 JSON 後重新產生）」／按鈕「套用修正並重新產生」
- 路線 A 隱私句（只有全前端才可以寫）：「圖片僅在您的瀏覽器中處理，不會上傳到任何伺服器」

版面：**原圖與棋譜圖並排**（手機用 原圖／棋譜圖 切換）。SVG 必須帶座標
（`coords=True`），讓審閱者找得到警告點名的位置。

## 5. 能力邊界（誠實設定使用者期望）

- **App 截圖：非常好** — 整盤、9／13／19 路標準（2–25 路接受但警告）、貼邊棋子、
  長城對峙、多位數手數、彩色角落徽章（→ 三角記號含顏色）、空點上的實心方塊記號。
- **實體棋盤照片：實驗性** — 透視／陰影通常會失敗，而且是**大聲失敗**
  （`ExtractionError`），絕不會靜靜給出錯的盤面。
- 只讀靜態盤面；不重播棋局、不處理提子。

## 6. 警告與錯誤 — 一定要顯示，並翻譯

引擎從不靜默猜測：不確定的判讀會以英文警告字串指名座標。
放在「請人工確認」標題下。現成對照（regex → 中文模板）：

| 英文 pattern | 繁體中文 |
|---|---|
| `unreadable label on the (black\|white) stone at {PT}` | 「{PT} 上的手數無法辨識，請對照原圖人工確認。」 |
| `ambiguous stone color at {PT} ... read as (black\|white)` | 「{PT} 的棋子顏色不明確（已判讀為黑棋/白棋），請確認。」 |
| `unusual board size {N}x{N}` | 「非標準棋盤大小 {N}×{N}（標準為 9、13、19），請確認截圖完整。」 |
| `almost no wood margin ... cropped mid-board` | 「棋盤外緣留白不足，截圖可能被裁切 — 請確認棋盤大小與座標。」 |
| `grid spacing differs between axes` | 「圖片可能被不等比例縮放，辨識品質可能受影響。」 |
| （未匹配者） | 「發現一項需要確認的狀況（原文見技術訊息）。」＋原文放收合區 |

`ExtractionError` 同理：`sizes 2-25` → 超出支援範圍；`no board found` →
「無法在圖片中找到棋盤…」；`cropped screenshot` → 「棋盤似乎被裁切…」；
格線相關 → 「無法辨識棋盤格線…」。原始英文放收合的「技術詳情」。

## 7. 修正迴圈是「必要功能」，不是選配

辨識偶爾會讀錯手數或棋子 — 使用者花幾秒改 JSON、重新產生就好
（`Position.from_json → render_svg`）。請做可編輯的 JSON 檢視（或匯入）
加「套用修正並重新產生」按鈕。**只給 JSON 下載不算修正功能。**
`from_json` 的驗證錯誤很精確、會指名座標 — 請呈現出來。

## 8. 驗收清單（完成前逐項確認）

1. 同一張截圖在你的整合與 https://goban-svg.pages.dev 各轉一次 —
   摘要數字（黑／白／記號／手數）必須完全一致。
2. 改 JSON 裡的一個手數、重新產生，棋譜圖跟著變。
3. 上傳非棋盤圖片 → 出現友善的中文錯誤（沒有 traceback、沒有 crash）。
4. 品質較差的截圖 → 警告以中文出現、帶座標點名。
5. （路線 A）以上過程 DevTools console 保持乾淨。

## 9. 出處

由 Joseph Huang 的工具鏈建置與測試（2026-08-19–20），提供給序盤盲區庫使用。
wheel 內含完整 Python 原始碼。轉換錯誤回報（**請附原圖**）→ Joseph —
驗證過的案例會成為引擎的永久迴歸測試，引擎因此對所有人越來越準。
