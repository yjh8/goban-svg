/* Main-thread controller: upload → decode/normalize → worker convert → results.
 *
 * All image work stays in this browser (see docs/webapp-design.md §privacy): the
 * canvas decodes and downscales, packed RGB is TRANSFERRED to the worker, and the
 * only network traffic is this site's own static assets (enforced by the CSP).
 */

const MAX_FILE_BYTES = 25 * 1024 * 1024;
const MAX_LONG_EDGE = 1400; // keeps a 19x19 grid at >= ~60 px spacing after downscale
const MIN_LONG_EDGE = 300;

const $ = (id) => document.getElementById(id);
const statusEl = $("status");
const convertBtn = $("convert-btn");
const fileInput = $("file-input");
const dropzone = $("dropzone");

let worker = null;
let workerReady = false;
let workerAttempts = 0;
let inFlight = false;
let currentJob = 0;
let pendingRGB = null; // {buf, width, height}
let blobUrls = [];
let previewUrl = null;
const MAX_JSON_CHARS = 2_000_000;

/* ---------------------------------------------------------------- status */

function setStatus(text) { statusEl.textContent = text; }

function showError(mainZh, detail) {
  $("error-main").textContent = mainZh;
  $("error-detail").textContent = detail || "";
  $("error-box").hidden = false;
}
function clearError() { $("error-box").hidden = true; }

/* ------------------------------------------------------------ worker boot */

const bootStages = { runtime: "載入辨識引擎中…（首次開啟約需數秒）", package: "載入棋盤辨識程式中…" };
const MAX_WORKER_ATTEMPTS = 3;

function onWorkerMessage(event) {
  const msg = event.data;
  if (msg.type === "boot-progress") { if (!workerReady) setStatus(bootStages[msg.stage] || "載入中…"); return; }
  if (msg.type === "ready") {
    workerReady = true;
    workerAttempts = 0;
    setStatus("辨識引擎已就緒。");
    $("version-line").textContent = `goban-svg v${msg.appVersion} · Pyodide ${msg.pyodideVersion}`;
    updateConvertEnabled();
    return;
  }
  if (msg.type === "boot-error") { workerFailed(msg.message); return; }
  if (msg.id !== currentJob) return; // stale job — a newer request superseded it
  inFlight = false;
  setBusy(false);
  if (msg.type === "error") {
    showError("處理過程發生預期外的錯誤，請重試或換一張圖片。", msg.message);
    return;
  }
  handlePayload(msg.payload);
}

// A crashed or unbootable worker is replaced, bounded, instead of leaving the
// page permanently stuck (review M4).
function workerFailed(detail) {
  workerReady = false;
  inFlight = false;
  setBusy(false);
  if (worker) { worker.terminate(); worker = null; }
  if (workerAttempts < MAX_WORKER_ATTEMPTS) {
    setStatus("辨識引擎載入異常，正在重試…");
    startWorker();
  } else {
    setStatus("");
    showError("辨識引擎載入失敗，請重新整理頁面再試一次。", detail);
  }
}

function startWorker() {
  workerAttempts += 1;
  worker = new Worker("worker.js", { type: "module" });
  worker.onmessage = onWorkerMessage;
  worker.onerror = (e) => workerFailed(e.message || "worker error");
  worker.onmessageerror = () => workerFailed("worker message deserialization failed");
  worker.postMessage({ type: "boot" });
}

startWorker();

/* ------------------------------------------------------------ file intake */

dropzone.addEventListener("dragover", (e) => { e.preventDefault(); dropzone.classList.add("dragover"); });
dropzone.addEventListener("dragleave", () => dropzone.classList.remove("dragover"));
dropzone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropzone.classList.remove("dragover");
  const file = e.dataTransfer.files && e.dataTransfer.files[0];
  if (file) acceptFile(file);
});
dropzone.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } });
fileInput.addEventListener("change", () => { if (fileInput.files[0]) acceptFile(fileInput.files[0]); });

function looksHeic(file) {
  return /\.hei[cf]$/i.test(file.name) || /hei[cf]/i.test(file.type);
}

async function acceptFile(file) {
  clearError();
  pendingRGB = null;
  currentJob += 1; // invalidate any in-flight conversion of the previous image (review M1)
  inFlight = false;
  $("result-section").hidden = true; // a previous image's result must not sit beside a new preview
  updateConvertEnabled();
  if (file.size > MAX_FILE_BYTES) {
    showError("圖片檔案太大（上限 25 MB），請縮小後再試。", `${file.name}: ${(file.size / 1e6).toFixed(1)} MB`);
    return;
  }
  let bitmap;
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
  } catch (err) {
    const hint = looksHeic(file)
      ? "此瀏覽器無法讀取 HEIC 格式，請改用 Safari，或先將照片轉成 JPEG。"
      : "無法讀取這個圖片檔，請確認格式（建議 PNG 或 JPEG）。";
    showError(hint, String(err));
    return;
  }
  const long = Math.max(bitmap.width, bitmap.height);
  if (long < MIN_LONG_EDGE) {
    showError("圖片解析度太低，無法辨識棋盤（長邊至少 300 像素）。", `${bitmap.width}×${bitmap.height}`);
    bitmap.close();
    return;
  }
  const scale = Math.min(1, MAX_LONG_EDGE / long);
  const w = Math.max(1, Math.round(bitmap.width * scale));
  const h = Math.max(1, Math.round(bitmap.height * scale));

  let rgba;
  try {
    // Opaque black base = the same alpha policy as the CLI's PNG codec.
    const canvas = document.createElement("canvas");
    canvas.width = w; canvas.height = h;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, w, h);
    ctx.drawImage(bitmap, 0, 0, w, h);
    rgba = ctx.getImageData(0, 0, w, h).data;
  } catch (err) {
    showError("圖片處理失敗，可能是尺寸或格式問題，請換一張圖片。", String(err));
    return;
  } finally {
    bitmap.close();
  }
  const rgb = new Uint8Array(w * h * 3);
  for (let i = 0, j = 0; j < rgb.length; i += 4, j += 3) {
    rgb[j] = rgba[i]; rgb[j + 1] = rgba[i + 1]; rgb[j + 2] = rgba[i + 2];
  }
  pendingRGB = { buf: rgb.buffer, width: w, height: h };

  const preview = $("preview");
  if (previewUrl) URL.revokeObjectURL(previewUrl); // review M5: one live preview URL only
  previewUrl = URL.createObjectURL(file);
  preview.src = previewUrl;
  preview.hidden = false;
  $("dropzone-text").textContent = `已選擇：${file.name}（辨識尺寸 ${w}×${h}）— 點擊可更換`;
  $("original-img").src = previewUrl;
  setStatus(workerReady ? "可以轉換了。" : "圖片已就緒，等待辨識引擎載入…");
  updateConvertEnabled();
}

function updateConvertEnabled() {
  convertBtn.disabled = !(workerReady && pendingRGB && !inFlight);
}

/* ---------------------------------------------------------------- convert */

function setBusy(busy) {
  convertBtn.disabled = busy || !(workerReady && pendingRGB);
  $("rerender-btn").disabled = busy;
  // Freeze correction inputs during a run so a mid-flight edit can't be
  // silently overwritten by the returning result (review M3).
  $("json-editor").disabled = busy;
  $("json-import").disabled = busy;
  if (busy) setStatus("辨識棋盤中…（依裝置效能約需數秒）");
}

convertBtn.addEventListener("click", () => {
  if (!pendingRGB || inFlight) return; // review M2: state gate, not just the DOM attribute
  clearError();
  currentJob += 1;
  inFlight = true;
  setBusy(true);
  // Copy so the user can convert the same selection again after a rerender.
  const copy = pendingRGB.buf.slice(0);
  worker.postMessage(
    { type: "convert", id: currentJob, width: pendingRGB.width, height: pendingRGB.height, buf: copy },
    [copy],
  );
});

/* ----------------------------------------------------- results + downloads */

const WARNING_MAP = [
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

function zhWarning(raw) {
  for (const [re, fmt] of WARNING_MAP) {
    const m = raw.match(re);
    if (m) return fmt(m);
  }
  return "發現一項需要確認的狀況（原文見「技術訊息」）。";
}

// Order matters: specific messages before generic ones — the size-cap error
// also contains "grid" wording and must not fall into the grid bucket (review m1).
const ERROR_MAP = [
  [/sizes 2-25/i, "辨識出的棋盤大小超出支援範圍（2–25 路）。"],
  [/no board found|contains no wood/i, "無法在圖片中找到棋盤。請使用清晰、正對拍攝的棋盤圖片（App 截圖效果最佳）。"],
  [/looks like a cropped screenshot|lines wide but/i, "棋盤似乎被裁切，兩個方向的線數不一致 — 請重新截取完整棋盤。"],
  [/grid fit|candidate lines|collapsed|lines survived|spacing/i, "無法辨識棋盤格線。實體棋盤照片請盡量正對拍攝、避免陰影。"],
];

function zhExtractError(raw) {
  for (const [re, zh] of ERROR_MAP) if (re.test(raw)) return zh;
  return "棋盤辨識失敗，請換一張更清晰的圖片試試。";
}

function makeDownload(id, text, mime) {
  const url = URL.createObjectURL(new Blob([text], { type: mime }));
  $(id).href = url;
  blobUrls.push(url);
}

function handlePayload(p) {
  if (!p.ok) {
    if (p.kind === "extract") showError(zhExtractError(p.message), p.message);
    else if (p.kind === "invalid") showError("JSON 內容有誤，尚未套用 — 請檢查格式與座標。", p.message);
    else showError("辨識程式發生內部錯誤，請回報這個問題。", p.message);
    setStatus("");
    return;
  }
  // Revoke the previous result's blob URLs before replacing them.
  blobUrls.forEach((u) => URL.revokeObjectURL(u));
  blobUrls = [];

  $("svg-holder").innerHTML = p.svg;
  const parts = [`${p.size}×${p.size} 棋盤：黑 ${p.black} 子、白 ${p.white} 子`];
  if (p.marks) parts.push(`記號 ${p.marks} 個`);
  if (p.labels) parts.push(`手數標記 ${p.labels} 個`);
  $("summary").textContent = parts.join("、");

  const wl = $("warnings-list");
  wl.innerHTML = "";
  if (p.warnings.length) {
    for (const raw of p.warnings) {
      const li = document.createElement("li");
      li.textContent = zhWarning(raw);
      wl.appendChild(li);
    }
    $("warnings-raw").textContent = p.warnings.join("\n");
    $("warnings-box").hidden = false;
  } else {
    $("warnings-box").hidden = true;
  }

  makeDownload("dl-svg", p.svg, "image/svg+xml");
  makeDownload("dl-json", p.json, "application/json");
  makeDownload("dl-sgf", p.sgf, "application/x-go-sgf");
  $("json-editor").value = p.json;

  $("result-section").hidden = false;
  setStatus("轉換完成。請對照原圖確認盤面。");
  $("result-section").scrollIntoView({ behavior: "smooth", block: "start" });
}

/* --------------------------------------------------------- JSON correction */

$("rerender-btn").addEventListener("click", () => {
  if (inFlight) return;
  const text = $("json-editor").value;
  if (text.length > MAX_JSON_CHARS) {
    showError("JSON 內容過大（上限約 2 MB），請檢查是否貼錯內容。", `${text.length} chars`);
    return;
  }
  clearError();
  currentJob += 1;
  inFlight = true;
  setBusy(true);
  setStatus("套用修正、重新產生棋譜圖中…");
  worker.postMessage({ type: "rerender", id: currentJob, json: text });
});

$("json-import").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  if (file.size > MAX_JSON_CHARS) {
    showError("JSON 檔案過大（上限約 2 MB）。", `${file.name}: ${file.size} bytes`);
    return;
  }
  $("json-editor").value = await file.text();
});

/* ------------------------------------------------------- mobile view tabs */

function selectTab(which) {
  const showOriginal = which === "original";
  $("pane-original").hidden = !showOriginal;
  $("pane-diagram").hidden = showOriginal;
  $("tab-original").classList.toggle("active", showOriginal);
  $("tab-diagram").classList.toggle("active", !showOriginal);
  $("tab-original").setAttribute("aria-selected", String(showOriginal));
  $("tab-diagram").setAttribute("aria-selected", String(!showOriginal));
}
$("tab-original").addEventListener("click", () => selectTab("original"));
$("tab-diagram").addEventListener("click", () => selectTab("diagram"));
