# 棋盤辨識整合包 v2 — Build spec for the 序盤盲區庫 AI (Sonnet)

You are integrating a **finished, tested Go-board-recognition engine** into the existing
序盤盲區庫 Next.js app. This document is a complete specification: every file you need is
included in full, every decision is already made. **Follow it literally. Where this spec
and your instincts disagree, the spec wins.**

## §0 — Hard rules (read first)

**MUST NOT:**
1. Do NOT re-implement, port, or modify any recognition logic. The engine ships as a
   Python wheel with 427 tests behind it; every threshold was calibrated on real
   screenshots. You call it; you never edit it.
2. Do NOT change any string given in §7 (warning/error regexes) or §3–§5 (code files)
   except where a line is explicitly marked `// ADAPT:`.
3. Do NOT skip `coords=True` in the SVG render call. Do NOT hide or drop `warnings`.
4. Do NOT rename any JSON schema key (`size`, `stones`, `black`, `white`, `marks`,
   `point`, `type`, `color`, `labels`).
5. Do NOT fetch the wheel at build time or vendor it into the repo — it is loaded at
   runtime by URL (CORS is enabled on the host).
6. Do NOT return or display raw Python tracebacks to users.

**MUST:**
1. Ship the correction loop (§5's editor + 「套用修正並重新產生」). A JSON download alone
   is NOT a correction feature.
2. Show every warning under 「請人工確認」, translated per §7.
3. Pass the acceptance checklist in §9 before reporting done.

## §1 — Fixed facts (copy exactly)

| Item | Value |
|---|---|
| Engine wheel URL (runtime, CORS-enabled) | `https://goban-svg.pages.dev/wheels/goban_svg-0.1.0-py3-none-any.whl` |
| Wheel SHA-256 | `02b158f7145f6ac2aee2d0680651e2dba26671e7c9320439aa7a207beb074cb3` |
| Expected `goban_svg.__version__` | `0.1.0` |
| Pyodide version (pinned) | `314.0.5` |
| Pyodide module URL | `https://cdn.jsdelivr.net/pyodide/v314.0.5/full/pyodide.mjs` |
| Pyodide indexURL | `https://cdn.jsdelivr.net/pyodide/v314.0.5/full/` |
| Live reference implementation | `https://goban-svg.pages.dev` (your integration must match its outputs exactly) |

If the app has a Content-Security-Policy, it must allow:
`script-src: https://cdn.jsdelivr.net 'wasm-unsafe-eval'` and
`connect-src: https://cdn.jsdelivr.net https://goban-svg.pages.dev`.
If there is no CSP (default for a plain Next.js app), nothing to do.

> **2026-08-21 — corrected wheel hash + wheel URLs are immutable from now on.**
> The SHA-256 published in earlier copies of this spec (`5964bad5…`, 65,760 bytes) is
> **stale**: the bytes at that URL were replaced by a later redeploy, back when the
> deploy script rebuilt and overwrote the wheel in place. The value in the table above
> (`02b158f7…`, 78,112 bytes) is the sha256 of the bytes
> `…/wheels/goban_svg-0.1.0-py3-none-any.whl` serves today — verify with
> `shasum -a 256 <downloaded wheel>`. The two builds are **not** byte-identical: the
> first was built from the 2026-08-19 source (commit `07a1ccb`), the current one from
> 2026-08-21 (`c4615de`). The original wheel's bytes were never archived, but **both
> source generations are in git, so the difference is fully known**: the current build
> adds the `photo.py` module (physical-board photo mode, 656 lines), a `photo` CLI
> subcommand, and an EXIF-orientation fix in `load_image` for phone JPEGs.
> **The screenshot recognition path is unchanged between them** — `extract_position`
> gained only an internal helper used by photo mode, and `board.py`, `render.py`,
> `sgf.py` and `digits.py` were not touched at all. Nothing in this integration
> verifies the hash at runtime (§3 asserts `goban_svg.__version__`, not bytes), so
> nothing breaks either way. But if you pinned the old hash anywhere, update it.
>
> **From now on a published wheel URL never changes bytes.** Every released wheel is
> kept byte-for-byte in a tracked archive with a `SHA256SUMS` manifest; the deploy
> script refuses to replace an existing wheel filename with different bytes, and the
> post-deploy smoke check re-downloads *every* published wheel and verifies its
> sha256. So the URL + hash in the table above stay valid indefinitely.
>
> **New versions get a new URL.** `0.1.1` **is now published** (2026-08-22) at
> `https://goban-svg.pages.dev/wheels/goban_svg-0.1.1-py3-none-any.whl`, sha256
> `ce7b3cb18c8622bd727d2f3cb2c03f9e19fca86153ce9a04778599e1ab800a5c`, 80,727 bytes —
> verified live against the tracked `SHA256SUMS` manifest. It adds an
> `ExtractionResult.uncertain` list of `{point, kind}` review points alongside the
> unchanged `warnings`, plus a `PhotoArtifact` / `extract_photo_artifact()` API for
> photo mode. Both are reached as `from goban_svg.extract import …` /
> `from goban_svg.photo import …`; the top-level `goban_svg` exports are unchanged.
> Every warning string in §7 is frozen byte-for-byte across versions — re-verified
> 0.1.0 → 0.1.1 on the published wheels. Upgrading is your own atomic switch of
> URL + hash + `EXPECTED_VERSION`, on your schedule — staying on 0.1.0 keeps working.
>
> **One caveat if you do upgrade.** 0.1.1 is additive in its API surface, but it also
> *tightens* `Position.from_json` validation: it now rejects JSON containing a
> duplicate object key (0.1.0 silently kept the last value and discarded the rest)
> and a lone UTF-16 surrogate inside a label. Both raise `ValueError`, which §3's
> `rerender()` already catches generically — so no code change is required, and the
> visible effect is that a hand-edited paste which used to fail *silently* now shows
> the existing 「JSON 內容有誤，尚未套用」 message. Worth one manual test of the JSON
> correction box after switching.

## §2 — Deliverables (exact file manifest)

Create exactly these files in the Next.js repo (App Router assumed; adapt paths only if
the repo uses the Pages Router):

```
public/goban/worker.js            ← §3, verbatim
components/GobanConverter.tsx     ← §4, verbatim except lines marked ADAPT
components/goban-converter.css    ← §5, adapt design tokens to the app's palette
app/blindspot/new/…               ← mount <GobanConverter onConfirm={...}/> wherever a
                                    new 錯題/盤面 is entered (ADAPT to the app's flow)
```

The component calls `onConfirm(position)` with the final JSON (after any user
corrections) — wire that into the app's existing save flow for a problem entry.

## §3 — `public/goban/worker.js` (complete, verbatim)

```js
/* Module Web Worker hosting the goban_svg recognition engine via Pyodide.
 * Protocol:
 *  in : {type:"boot"}
 *  in : {type:"convert",  id, width, height, buf}  // buf: transferred ArrayBuffer, packed RGB8
 *  in : {type:"rerender", id, json}                // json: edited Position JSON text
 *  out: {type:"boot-progress", stage}              // stage: "runtime" | "package"
 *  out: {type:"ready", appVersion}
 *  out: {type:"boot-error", message}
 *  out: {type:"result", id, payload}               // payload: see DRIVER _ok/_err below
 *  out: {type:"error", id, message}
 */

const PYODIDE_INDEX = "https://cdn.jsdelivr.net/pyodide/v314.0.5/full/";
const WHEEL_URL = "https://goban-svg.pages.dev/wheels/goban_svg-0.1.0-py3-none-any.whl";
const EXPECTED_VERSION = "0.1.0";

const DRIVER = `
import json as _json
from goban_svg.board import Position
from goban_svg.extract import ExtractionError, extract_position
from goban_svg.png_codec import Image
from goban_svg.render import render_svg
from goban_svg.sgf import position_to_sgf

def _ok(pos, warnings):
    black = sum(1 for c in pos.stones.values() if c == "black")
    return _json.dumps({
        "ok": True,
        "svg": render_svg(pos, coords=True),
        "json": pos.to_json(),
        "sgf": position_to_sgf(pos),
        "size": pos.size,
        "black": black,
        "white": len(pos.stones) - black,
        "marks": len(pos.marks),
        "labels": len(pos.labels),
        "warnings": list(warnings),
    })

def _err(kind, exc):
    return _json.dumps({"ok": False, "kind": kind, "message": str(exc)})

def convert_rgb(js_buf, width, height):
    try:
        pixels = bytearray(width * height * 3)
        js_buf.assign_to(pixels)
        img = Image(width=width, height=height, pixels=pixels)
        result = extract_position(img)
        return _ok(result.position, result.warnings)
    except ExtractionError as exc:
        return _err("extract", exc)
    except Exception as exc:
        return _err("internal", exc)

def rerender(text):
    try:
        pos = Position.from_json(text)
        return _ok(pos, [])
    except ValueError as exc:
        return _err("invalid", exc)
    except Exception as exc:
        return _err("internal", exc)
`;

let bootPromise = null;

async function boot() {
  postMessage({ type: "boot-progress", stage: "runtime" });
  const { loadPyodide } = await import(PYODIDE_INDEX + "pyodide.mjs");
  const py = await loadPyodide({ indexURL: PYODIDE_INDEX });
  postMessage({ type: "boot-progress", stage: "package" });
  await py.loadPackage(WHEEL_URL);
  py.runPython(DRIVER);
  const version = py.runPython("import goban_svg; goban_svg.__version__");
  if (version !== EXPECTED_VERSION) {
    throw new Error(`wheel version ${version} != expected ${EXPECTED_VERSION}`);
  }
  return py;
}

function ensureBoot() {
  if (!bootPromise) bootPromise = boot();
  return bootPromise;
}

self.onmessage = async (event) => {
  const msg = event.data;
  if (msg.type === "boot") {
    try {
      await ensureBoot();
      postMessage({ type: "ready", appVersion: EXPECTED_VERSION });
    } catch (err) {
      bootPromise = null; // allow a retry
      postMessage({ type: "boot-error", message: String(err) });
    }
    return;
  }
  if (msg.type === "convert" || msg.type === "rerender") {
    try {
      const py = await ensureBoot();
      let raw;
      if (msg.type === "convert") {
        py.globals.set("_RGB_JS", new Uint8Array(msg.buf));
        try {
          raw = py.runPython(`convert_rgb(_RGB_JS, ${msg.width | 0}, ${msg.height | 0})`);
        } finally {
          py.runPython("del _RGB_JS");
        }
      } else {
        py.globals.set("_JSON_TEXT", msg.json);
        try {
          raw = py.runPython("rerender(_JSON_TEXT)");
        } finally {
          py.runPython("del _JSON_TEXT");
        }
      }
      postMessage({ type: "result", id: msg.id, payload: JSON.parse(raw) });
    } catch (err) {
      postMessage({ type: "error", id: msg.id, message: String(err) });
    }
  }
};
```

## §4 — `components/GobanConverter.tsx` (complete; only ADAPT-marked lines may change)

```tsx
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import "./goban-converter.css";

/* ------------------------------------------------------------------ types */

type Payload = {
  ok: boolean;
  kind?: "extract" | "invalid" | "internal";
  message?: string;
  svg?: string;
  json?: string;
  sgf?: string;
  size?: number;
  black?: number;
  white?: number;
  marks?: number;
  labels?: number;
  warnings?: string[];
};

type Props = {
  /** Called with the confirmed Position JSON (post-correction). ADAPT: wire to app save flow. */
  onConfirm?: (positionJson: string, svg: string, sgf: string) => void;
};

/* ------------------------------------------------------- fixed constants */

const MAX_FILE_BYTES = 25 * 1024 * 1024;
const MAX_LONG_EDGE = 1400; // keeps a 19x19 grid at >= ~60px spacing; do not raise
const MIN_LONG_EDGE = 300;
const MAX_JSON_CHARS = 2_000_000;
const MAX_WORKER_ATTEMPTS = 3;

/* zh-TW warning translations — regexes match the engine's EXACT strings (§7).
   Do not "improve" the regexes; they are pinned to the engine's wording. */
const WARNING_MAP: Array<[RegExp, (m: RegExpMatchArray) => string]> = [
  [/unreadable label on the (?:black|white) stone at (\S+)/,
    (m) => `${m[1]} 上的手數無法辨識，請對照原圖人工確認。`],
  [/ambiguous stone color at (\S+).*read as (black|white)/,
    (m) => `${m[1]} 的棋子顏色不明確（已判讀為${m[2] === "black" ? "黑棋" : "白棋"}），請確認。`],
  [/unusual board size (\d+)x\d+/,
    (m) => `非標準棋盤大小 ${m[1]}×${m[1]}（標準為 9、13、19），請確認截圖完整。`],
  [/almost no wood margin|cropped mid-board/,
    () => "棋盤外緣留白不足，截圖可能被裁切 — 請確認棋盤大小與座標是否正確。"],
  [/grid spacing differs/,
    () => "圖片可能被不等比例縮放，辨識品質可能受影響。"],
];

/* Order matters: specific before generic (the size-cap error also mentions "grid"). */
const ERROR_MAP: Array<[RegExp, string]> = [
  [/sizes 2-25/i, "辨識出的棋盤大小超出支援範圍（2–25 路）。"],
  [/no board found|contains no wood|spans too few/i,
    "無法在圖片中找到棋盤。請使用清晰、正對拍攝的棋盤圖片（App 截圖效果最佳）。"],
  [/looks like a cropped screenshot|lines wide but/i,
    "棋盤似乎被裁切，兩個方向的線數不一致 — 請重新截取完整棋盤。"],
  [/grid fit|candidate lines|collapsed|lines survived|spacing/i,
    "無法辨識棋盤格線。實體棋盤照片請盡量正對拍攝、避免陰影。"],
];

function zhWarning(raw: string): string {
  for (const [re, fmt] of WARNING_MAP) {
    const m = raw.match(re);
    if (m) return fmt(m);
  }
  return "發現一項需要確認的狀況（原文見「技術訊息」）。";
}

function zhExtractError(raw: string): string {
  for (const [re, zh] of ERROR_MAP) if (re.test(raw)) return zh;
  return "棋盤辨識失敗，請換一張更清晰的圖片試試。";
}

/* ------------------------------------------------------------ component */

export default function GobanConverter({ onConfirm }: Props) {
  const workerRef = useRef<Worker | null>(null);
  const attemptsRef = useRef(0);
  const jobRef = useRef(0);
  const pendingRef = useRef<{ buf: ArrayBuffer; width: number; height: number } | null>(null);
  const blobUrlsRef = useRef<string[]>([]);
  const previewUrlRef = useRef<string | null>(null);

  const [engineReady, setEngineReady] = useState(false);
  const [status, setStatus] = useState("載入辨識引擎中…（首次開啟約需數秒）");
  const [fileLabel, setFileLabel] = useState("拖曳圖片到這裡，或點擊選擇檔案");
  const [previewSrc, setPreviewSrc] = useState<string | null>(null);
  const [canConvert, setCanConvert] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ main: string; detail: string } | null>(null);
  const [result, setResult] = useState<Payload | null>(null);
  const [downloads, setDownloads] = useState<{ svg: string; json: string; sgf: string } | null>(null);
  const [editorText, setEditorText] = useState("");

  const refreshCanConvert = useCallback((ready: boolean, isBusy: boolean) => {
    setCanConvert(ready && !!pendingRef.current && !isBusy);
  }, []);

  /* ------------------------------------------------------------- worker */

  const startWorker = useCallback(function start() {
    attemptsRef.current += 1;
    const w = new Worker("/goban/worker.js", { type: "module" });
    workerRef.current = w;

    const fail = (detail: string) => {
      setEngineReady(false);
      setBusy(false);
      w.terminate();
      workerRef.current = null;
      if (attemptsRef.current < MAX_WORKER_ATTEMPTS) {
        setStatus("辨識引擎載入異常，正在重試…");
        start();
      } else {
        setStatus("");
        setError({ main: "辨識引擎載入失敗，請重新整理頁面再試一次。", detail });
      }
    };

    w.onerror = (e) => fail(e.message || "worker error");
    w.onmessageerror = () => fail("worker message deserialization failed");
    w.onmessage = (event: MessageEvent) => {
      const msg = event.data;
      if (msg.type === "boot-progress") {
        setStatus(msg.stage === "package" ? "載入棋盤辨識程式中…" : "載入辨識引擎中…（首次開啟約需數秒）");
        return;
      }
      if (msg.type === "ready") {
        attemptsRef.current = 0;
        setEngineReady(true);
        setStatus("辨識引擎已就緒。");
        refreshCanConvert(true, false);
        return;
      }
      if (msg.type === "boot-error") { fail(msg.message); return; }
      if (msg.id !== jobRef.current) return; // stale result — superseded
      setBusy(false);
      refreshCanConvert(true, false);
      if (msg.type === "error") {
        setError({ main: "處理過程發生預期外的錯誤，請重試或換一張圖片。", detail: msg.message });
        setStatus("");
        return;
      }
      handlePayload(msg.payload as Payload);
    };
    w.postMessage({ type: "boot" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    startWorker();
    return () => {
      workerRef.current?.terminate();
      blobUrlsRef.current.forEach((u) => URL.revokeObjectURL(u));
      if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* -------------------------------------------------------- file intake */

  async function acceptFile(file: File) {
    setError(null);
    setResult(null);
    pendingRef.current = null;
    jobRef.current += 1; // invalidate any in-flight job for the previous image
    refreshCanConvert(engineReady, false);

    if (file.size > MAX_FILE_BYTES) {
      setError({ main: "圖片檔案太大（上限 25 MB），請縮小後再試。", detail: `${file.name}: ${file.size} bytes` });
      return;
    }
    let bitmap: ImageBitmap;
    try {
      bitmap = await createImageBitmap(file, { imageOrientation: "from-image" }); // EXIF-aware
    } catch (err) {
      const heic = /\.hei[cf]$/i.test(file.name) || /hei[cf]/i.test(file.type);
      setError({
        main: heic
          ? "此瀏覽器無法讀取 HEIC 格式，請改用 Safari，或先將照片轉成 JPEG。"
          : "無法讀取這個圖片檔，請確認格式（建議 PNG 或 JPEG）。",
        detail: String(err),
      });
      return;
    }
    const long = Math.max(bitmap.width, bitmap.height);
    if (long < MIN_LONG_EDGE) {
      setError({ main: "圖片解析度太低，無法辨識棋盤（長邊至少 300 像素）。", detail: `${bitmap.width}×${bitmap.height}` });
      bitmap.close();
      return;
    }
    const s = Math.min(1, MAX_LONG_EDGE / long);
    const w = Math.max(1, Math.round(bitmap.width * s));
    const h = Math.max(1, Math.round(bitmap.height * s));
    let rgba: Uint8ClampedArray;
    try {
      const canvas = document.createElement("canvas");
      canvas.width = w; canvas.height = h;
      const ctx = canvas.getContext("2d", { willReadFrequently: true })!;
      ctx.fillStyle = "#000";           // composite alpha over BLACK — matches the engine's policy
      ctx.fillRect(0, 0, w, h);
      ctx.drawImage(bitmap, 0, 0, w, h);
      rgba = ctx.getImageData(0, 0, w, h).data; // sRGB
    } catch (err) {
      setError({ main: "圖片處理失敗，可能是尺寸或格式問題，請換一張圖片。", detail: String(err) });
      return;
    } finally {
      bitmap.close();
    }
    const rgb = new Uint8Array(w * h * 3);
    for (let i = 0, j = 0; j < rgb.length; i += 4, j += 3) {
      rgb[j] = rgba[i]; rgb[j + 1] = rgba[i + 1]; rgb[j + 2] = rgba[i + 2];
    }
    pendingRef.current = { buf: rgb.buffer, width: w, height: h };
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    previewUrlRef.current = URL.createObjectURL(file);
    setPreviewSrc(previewUrlRef.current);
    setFileLabel(`已選擇：${file.name}（辨識尺寸 ${w}×${h}）— 點擊可更換`);
    setStatus(engineReady ? "可以轉換了。" : "圖片已就緒，等待辨識引擎載入…");
    refreshCanConvert(engineReady, false);
  }

  /* ----------------------------------------------------------- convert */

  function convert() {
    const pending = pendingRef.current;
    if (!pending || busy || !engineReady) return;
    setError(null);
    jobRef.current += 1;
    setBusy(true);
    setCanConvert(false);
    setStatus("辨識棋盤中…（依裝置效能約需數秒）");
    const copy = pending.buf.slice(0); // keep the original so the user can convert again
    workerRef.current!.postMessage(
      { type: "convert", id: jobRef.current, width: pending.width, height: pending.height, buf: copy },
      [copy],
    );
  }

  function rerender() {
    if (busy || !engineReady) return;
    if (editorText.length > MAX_JSON_CHARS) {
      setError({ main: "JSON 內容過大（上限約 2 MB），請檢查是否貼錯內容。", detail: `${editorText.length} chars` });
      return;
    }
    setError(null);
    jobRef.current += 1;
    setBusy(true);
    setStatus("套用修正、重新產生棋譜圖中…");
    workerRef.current!.postMessage({ type: "rerender", id: jobRef.current, json: editorText });
  }

  function handlePayload(p: Payload) {
    if (!p.ok) {
      if (p.kind === "extract") setError({ main: zhExtractError(p.message ?? ""), detail: p.message ?? "" });
      else if (p.kind === "invalid") setError({ main: "JSON 內容有誤，尚未套用 — 請檢查格式與座標。", detail: p.message ?? "" });
      else setError({ main: "辨識程式發生內部錯誤，請回報這個問題。", detail: p.message ?? "" });
      setStatus("");
      return;
    }
    blobUrlsRef.current.forEach((u) => URL.revokeObjectURL(u));
    const mk = (text: string, mime: string) => {
      const url = URL.createObjectURL(new Blob([text], { type: mime }));
      blobUrlsRef.current.push(url);
      return url;
    };
    blobUrlsRef.current = [];
    setDownloads({
      svg: mk(p.svg!, "image/svg+xml"),
      json: mk(p.json!, "application/json"),
      sgf: mk(p.sgf!, "application/x-go-sgf"),
    });
    setEditorText(p.json!);
    setResult(p);
    setStatus("轉換完成。請對照原圖確認盤面。");
  }

  /* ---------------------------------------------------------------- UI */

  const summary = result?.ok
    ? [`${result.size}×${result.size} 棋盤：黑 ${result.black} 子、白 ${result.white} 子`,
       result.marks ? `記號 ${result.marks} 個` : "",
       result.labels ? `手數標記 ${result.labels} 個` : ""].filter(Boolean).join("、")
    : "";

  return (
    <div className="gsvg">
      <label className="gsvg-dropzone">
        <input
          type="file"
          accept="image/*"
          onChange={(e) => e.target.files?.[0] && acceptFile(e.target.files[0])}
        />
        <span>{fileLabel}</span>
        {previewSrc && <img src={previewSrc} alt="已選擇的棋盤圖片預覽" />}
      </label>

      <button className="gsvg-convert" disabled={!canConvert} onClick={convert}>
        轉換成棋譜圖
      </button>
      <p className="gsvg-status" role="status" aria-live="polite">{status}</p>

      {error && (
        <div className="gsvg-error" role="alert">
          <p>{error.main}</p>
          <details><summary>技術詳情</summary><pre>{error.detail}</pre></details>
        </div>
      )}

      {result?.ok && (
        <section className="gsvg-result">
          <p className="gsvg-summary">{summary}</p>

          {result.warnings && result.warnings.length > 0 && (
            <div className="gsvg-warnings">
              <h4>請人工確認</h4>
              <ul>{result.warnings.map((raw, i) => <li key={i}>{zhWarning(raw)}</li>)}</ul>
              <details><summary>技術訊息（原文）</summary><pre>{result.warnings.join("\n")}</pre></details>
            </div>
          )}

          <div className="gsvg-compare">
            <figure>
              {previewSrc && <img src={previewSrc} alt="原始棋盤圖片" />}
              <figcaption>原圖</figcaption>
            </figure>
            <figure>
              {/* SVG is generated by the engine from validated data; labels are XML-escaped inside it. */}
              <div dangerouslySetInnerHTML={{ __html: result.svg! }} />
              <figcaption>棋譜圖（含座標，供對照確認）</figcaption>
            </figure>
          </div>

          {downloads && (
            <div className="gsvg-downloads">
              <a href={downloads.svg} download="goban-board.svg">下載 SVG 棋譜圖</a>
              <a href={downloads.json} download="goban-board.json">下載 JSON（可修正）</a>
              <a href={downloads.sgf} download="goban-board.sgf">下載 SGF 棋譜（靜態盤面，記號顏色不保留）</a>
            </div>
          )}

          <details className="gsvg-editor">
            <summary>修正辨識結果（編輯 JSON 後重新產生）</summary>
            <p>棋子座標格式如 <code>"D14"</code>（直行 A–T 略過 I，橫列由下往上數）。</p>
            <textarea
              rows={12}
              spellCheck={false}
              value={editorText}
              disabled={busy}
              onChange={(e) => setEditorText(e.target.value)}
              aria-label="盤面 JSON 編輯器"
            />
            <div>
              <button disabled={busy} onClick={rerender}>套用修正並重新產生</button>
            </div>
          </details>

          {onConfirm && (
            <button
              className="gsvg-confirm"
              disabled={busy}
              onClick={() => onConfirm(result.json!, result.svg!, result.sgf!)}
            >
              確認盤面，加入錯題庫 {/* ADAPT: wording to match the app's flow */}
            </button>
          )}

          <p className="gsvg-privacy">圖片僅在您的瀏覽器中處理，不會上傳到任何伺服器。</p>
        </section>
      )}
    </div>
  );
}
```

## §5 — `components/goban-converter.css` (adapt tokens to the app's dark palette)

```css
/* ADAPT: swap the custom-property values to the app's design tokens. Keep structure. */
.gsvg { --gsvg-panel: #16181f; --gsvg-line: #2c3040; --gsvg-ink: #e8e6df;
        --gsvg-accent: #4048e8; --gsvg-warn: #d8b74a; --gsvg-danger: #e06050;
        color: var(--gsvg-ink); display: flex; flex-direction: column; gap: .8rem; }
.gsvg-dropzone { display: flex; flex-direction: column; align-items: center; gap: .6rem;
  min-height: 8rem; justify-content: center; padding: 1rem; cursor: pointer;
  background: var(--gsvg-panel); border: 2px dashed var(--gsvg-line); border-radius: 10px; }
.gsvg-dropzone input { position: absolute; width: 1px; height: 1px; opacity: 0; }
.gsvg-dropzone img { max-width: 100%; max-height: 16rem; border-radius: 6px; }
.gsvg-convert, .gsvg-editor button, .gsvg-confirm { min-height: 44px; padding: .55rem 1.1rem;
  border-radius: 10px; border: none; background: var(--gsvg-accent); color: #fff;
  font-size: 1rem; cursor: pointer; }
.gsvg-convert:disabled, .gsvg-editor button:disabled { opacity: .45; cursor: not-allowed; }
.gsvg-status { min-height: 1.4em; opacity: .75; }
.gsvg-error { border: 1px solid var(--gsvg-danger); border-radius: 10px; padding: .7rem 1rem; }
.gsvg-error > p { color: var(--gsvg-danger); font-weight: 600; margin: 0 0 .3rem; }
.gsvg-warnings { border: 1px solid var(--gsvg-warn); border-radius: 10px; padding: .6rem 1rem; }
.gsvg-compare { display: flex; gap: 1rem; flex-wrap: wrap; }
.gsvg-compare figure { flex: 1 1 20rem; min-width: 0; margin: 0; }
.gsvg-compare img, .gsvg-compare svg { width: 100%; height: auto; background: #fff; border-radius: 6px; }
.gsvg-downloads { display: flex; gap: .6rem; flex-wrap: wrap; }
.gsvg-downloads a { background: var(--gsvg-panel); border: 1px solid var(--gsvg-line);
  color: var(--gsvg-ink); padding: .5rem .9rem; border-radius: 10px; text-decoration: none;
  min-height: 44px; display: inline-flex; align-items: center; }
.gsvg-editor textarea { width: 100%; font-family: ui-monospace, Menlo, monospace;
  font-size: .8rem; background: var(--gsvg-panel); color: var(--gsvg-ink);
  border: 1px solid var(--gsvg-line); border-radius: 6px; padding: .5rem; }
.gsvg-editor summary { min-height: 44px; display: flex; align-items: center; cursor: pointer; }
.gsvg-privacy { opacity: .6; font-size: .85rem; }
pre { overflow-x: auto; }
```

## §6 — Fallback route (ONLY if the client-side route is impossible)

Use only if the app cannot run Web Workers/WASM (e.g., a hard CSP that cannot change).
Vercel Python function — `api/convert.py`:

```python
# requirements.txt:
#   https://goban-svg.pages.dev/wheels/goban_svg-0.1.0-py3-none-any.whl
#   pillow
from http.server import BaseHTTPRequestHandler
import json

from goban_svg.extract import ExtractionError, extract_position
from goban_svg.png_codec import Image, PngError, read_png
from goban_svg.render import render_svg
from goban_svg.sgf import position_to_sgf


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        try:
            try:
                img = read_png(body)  # PNG fast path
            except PngError:
                from io import BytesIO
                from PIL import Image as PILImage  # JPEG/WebP via Pillow

                pil = PILImage.open(BytesIO(body)).convert("RGB")
                img = Image(width=pil.width, height=pil.height, pixels=bytearray(pil.tobytes()))
            result = extract_position(img)
            pos = result.position
            black = sum(1 for c in pos.stones.values() if c == "black")
            out = {
                "ok": True,
                "svg": render_svg(pos, coords=True),
                "json": pos.to_json(),
                "sgf": position_to_sgf(pos),
                "size": pos.size,
                "black": black,
                "white": len(pos.stones) - black,
                "marks": len(pos.marks),
                "labels": len(pos.labels),
                "warnings": list(result.warnings),
            }
        except ExtractionError as exc:
            out = {"ok": False, "kind": "extract", "message": str(exc)}
        except Exception as exc:
            out = {"ok": False, "kind": "internal", "message": str(exc)}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)
```

Client still performs §4's downscale before uploading (Vercel body limit ≈ 4.5 MB), and
still uses the same WARNING_MAP/ERROR_MAP and correction loop. The payload shape is
identical, so §4's component works with a fetch() in place of the worker.

## §7 — Exact engine strings (authoritative; regexes in §4 are derived from these)

Warnings (`result.warnings` entries; `{PT}` is a point like `D14`):

```
unreadable label on the {black|white} stone at {PT} -- check it by hand
ambiguous stone color at {PT} (disc luminance {N}); read as {black|white}
unusual board size {N}x{N} (expected one of 9, 13, 19)
grid spacing differs between axes ({X}px wide vs {Y}px tall) -- the screenshot looks non-uniformly resized; stone classification may suffer
almost no wood margin beyond the outer grid line ({sides}) -- the screenshot may be cropped mid-board; verify the board really is {N}x{N} before trusting coordinates
```

`ExtractionError` messages (str(exc); the ones users will actually hit):

```
no board found: the image contains no wood-colored pixels
no board found: the wood region spans too few pixels along {x|y}
no board grid found along {x|y}: only {N} candidate lines
no board grid found along {x|y}: fewer than 2 lines survived the fit
grid fit failed: all detected lines collapsed onto one position
the grid is {N} lines wide but {M} lines tall -- this looks like a cropped screenshot; re-capture the whole board
fitted a {N}x{N} grid, but positions support sizes 2-25 (the point notation has 25 column letters) -- ...
```

`Position.from_json` raises `ValueError` with messages that name the offending key/point
(e.g. `label at D14 must be a non-empty string`). Show them behind the zh-TW lead
「JSON 內容有誤，尚未套用 — 請檢查格式與座標。」.

## §8 — Capability boundary (tell users the truth)

- **App screenshots: excellent.** Full boards; 9/13/19 standard (2–25 accepted with a
  warning); stones on edges; long walls; multi-digit move numbers; colored corner wedge
  badges → triangle marks carrying the badge color (`#2b5fe3` blue, `#e03c3c` red,
  `white`, `black`); solid square markers on empty points.
- **Photos of physical boards: experimental.** Perspective/shadows usually raise
  `ExtractionError` — loud failure, never a silently wrong board. Do not promise photo
  support in UI copy — this component does not implement photo mode at all (see the
  static-positions-only bullet below), so there is no photo wording to get right.
- Static positions only — no game replay, no capture resolution.

## §9 — Acceptance checklist (run ALL before reporting done)

1. **Parity**: convert the same screenshot in your integration AND at
   https://goban-svg.pages.dev. The four counts (黑/白/記號/手數) MUST match exactly.
   If they differ, your pixel pipeline deviates from §4 — fix yours, not the engine.
2. **Correction loop**: change one label in the JSON editor (e.g. `"3"` → `"99"`),
   apply — the diagram updates and shows `99` on that stone.
3. **Invalid JSON**: type `{"size": 19, "stones": {"Black": []}}` — a zh-TW validation
   error appears (unknown stone bucket), nothing crashes.
4. **Non-board image**: upload any photo without a board — the friendly zh-TW error
   appears; DevTools console shows no uncaught errors.
5. **Stale-job race**: select image A, click convert, immediately select image B —
   A's result must NOT appear beside B's preview.
6. **Mobile**: the two figures stack; buttons are ≥ 44px tall; everything remains usable.
7. **Version assert**: temporarily change `EXPECTED_VERSION` to `9.9.9` — boot must fail
   with the version-mismatch error (then change it back).

## §10 — Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Worker never sends `ready` | CDN blocked or CSP missing `cdn.jsdelivr.net` + `'wasm-unsafe-eval'` (§1) |
| `loadPackage` fails on the wheel | Network/CORS — the wheel host sends `Access-Control-Allow-Origin: *`; check DevTools Network tab for the real status |
| Counts differ from the reference site | Your canvas pipeline skipped a step: EXIF option, black fill, downscale cap, or RGBA→RGB packing (§4 `acceptFile`) |
| Rotated phone photos come out sideways | `createImageBitmap` missing `{ imageOrientation: "from-image" }` |
| Diagram has no coordinates | You dropped `coords=True` in the worker DRIVER — restore it |
| Intermittent wrong-image results | Job-ID gating removed — restore `jobRef` checks |

## §11 — Provenance

Engine built and tested by Joseph Huang's toolchain (2026-08-19/20); provided for
序盤盲區庫. The wheel embeds its full Python source. Report wrong conversions to Joseph
**with the original image** — verified cases become permanent regression tests in the
engine, which is how it gets better for everyone.
