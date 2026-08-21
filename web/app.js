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
let photoModeActive = false; // last successful result came from photo mode
let pickerCorners = null; // [[x,y] x4] in decoded-bitmap px, TL TR BR BL
let decodedCanvas = null; // THE normalized raster: same pixels pendingRGB was packed from (review B1)
let currentJobKind = null; // 'convert' | 'photo' | 'rerender' — provenance for the banner (review M3)
const MAX_JSON_CHARS = 2_000_000;

/* ---------------------------------------------------------------- status */

function setStatus(text) { statusEl.textContent = text; }

function showError(mainZh, detail) {
  $("error-main").textContent = mainZh;
  $("error-detail").textContent = detail || "";
  $("error-box").hidden = false;
}
function clearError() {
  $("error-box").hidden = true;
  $("photo-fallback-btn").hidden = true;
}

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
    refreshPhotoConvertEnabled();
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
  decodedCanvas = null;
  currentJob += 1; // invalidate any in-flight conversion of the previous image (review M1)
  inFlight = false;
  $("result-section").hidden = true; // a previous image's result must not sit beside a new preview
  // Photo UI resets FIRST: a failed decode must not leave a stale picker or
  // an apparently-working photo link behind (review m6).
  photoModeActive = false;
  pickerCorners = null;
  $("picker-section").hidden = true;
  $("photo-mode-link").hidden = true;
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
    // Opaque black base = the same alpha policy as the CLI's PNG codec. This
    // canvas is KEPT: the corner picker must draw the exact raster pendingRGB
    // came from, never a re-decode of the blob (review B1: Safari can apply
    // EXIF differently between createImageBitmap and <img>).
    const canvas = document.createElement("canvas");
    canvas.width = w; canvas.height = h;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, w, h);
    ctx.drawImage(bitmap, 0, 0, w, h);
    rgba = ctx.getImageData(0, 0, w, h).data;
    decodedCanvas = canvas;
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
  $("photo-mode-link").hidden = false;
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
  // Photo controls are part of the same machine (picker review M2): a running
  // job freezes corner/size mutation so the picker always shows what was sent.
  $("photo-size").disabled = busy;
  $("photo-mode-link").disabled = busy;
  $("photo-fallback-btn").disabled = busy;
  refreshPhotoConvertEnabled();
  if (busy) setStatus("辨識棋盤中…（依裝置效能約需數秒）");
}

function refreshPhotoConvertEnabled() {
  const usable = pickerCorners && quadIsUsable(pickerCorners);
  $("photo-convert-btn").disabled = inFlight || !workerReady || !pendingRGB || !usable;
}

convertBtn.addEventListener("click", () => {
  if (!pendingRGB || inFlight) return; // review M2: state gate, not just the DOM attribute
  clearError();
  currentJob += 1;
  currentJobKind = "convert";
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
  [/ambiguous point at (\S+)/,
    (m) => `${m[1]} 判讀不明確（已留空），請對照原圖人工確認。`],
  [/point (\S+) lies \(partly\) outside/,
    (m) => `${m[1]} 超出照片範圍（已留空）。`],
  [/no reliable wood reference around (\S+)/,
    (m) => `${m[1]} 附近找不到可靠的棋盤底色（陰影或棋子邊緣？），已留空請確認。`],
  [/bright point at (\S+) is as warm/,
    (m) => `${m[1]} 偏亮但色調與棋盤相近（反光？），已留空請確認。`],
  [/auto-refinement could not verify/,
    () => "自動校正無法確認格線 — 已依你點選的角點辨識；若棋子位置有偏移，請重新點角。"],
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
  [/too small in the photo/i, "照片中的棋盤太小 — 請靠近一點拍攝，或先裁切再上傳。"],
  [/lies outside the photo/i, "有角點超出照片範圍，請重新點選四個角。"],
  [/counter-clockwise|crossed or concave|same point/i, "角點順序或位置有誤：請依 1 左上 → 2 右上 → 3 右下 → 4 左下 重新點選。"],
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
    if (p.kind === "extract") {
      showError(zhExtractError(p.message), p.message);
      // A failed automatic read on a selected image is the "slanted photo?"
      // signal: offer assisted corner picking (photo mode).
      if (pendingRGB) $("photo-fallback-btn").hidden = false;
    } else if (p.kind === "corners") showError(zhExtractError(p.message), p.message);
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

  // Provenance follows the JOB, not a sticky flag: a screenshot conversion
  // clears photo provenance, a photo job sets it, rerender preserves it (M3).
  if (currentJobKind === "photo") photoModeActive = true;
  else if (currentJobKind === "convert") photoModeActive = false;
  $("experimental-banner").hidden = !photoModeActive;
  $("result-section").hidden = false;
  setStatus(photoModeActive ? "轉換完成（照片模式・實驗性）。請逐子對照原圖確認。" : "轉換完成。請對照原圖確認盤面。");
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
  currentJobKind = "rerender";
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

/* ------------------------------------------------- photo mode: corner picker */

const pickerCanvas = $("picker-canvas");
let dragIndex = -1;
let activePointerId = null; // review m8: one pointer owns a drag
let selectedHandle = 0; // keyboard/tap-to-place target (review M5)
let drawQueued = false;

function openPicker() {
  if (!pendingRGB || !decodedCanvas || inFlight) return; // M2: never mid-job
  clearError();
  // The prior result stays visible below the picker (review M4): 返回 merely
  // hides this section again, losing nothing.
  $("picker-section").hidden = false;
  pickerCanvas.width = decodedCanvas.width;
  pickerCanvas.height = decodedCanvas.height;
  if (!pickerCorners) {
    const ix = pickerCanvas.width * 0.12, iy = pickerCanvas.height * 0.12;
    pickerCorners = [
      [ix, iy],
      [pickerCanvas.width - ix, iy],
      [pickerCanvas.width - ix, pickerCanvas.height - iy],
      [ix, pickerCanvas.height - iy],
    ];
  }
  selectHandle(0);
  drawPicker();
  $("picker-section").scrollIntoView({ behavior: "smooth", block: "start" });
}

function quadIsUsable(c) {
  // Mirror of the engine's convexity/winding rule (final say stays in Python).
  const cross = [];
  for (let i = 0; i < 4; i++) {
    const [ax, ay] = c[i], [bx, by] = c[(i + 1) % 4], [dx, dy] = c[(i + 2) % 4];
    cross.push((bx - ax) * (dy - ay) - (by - ay) * (dx - ax));
  }
  return cross.every((v) => v > 0);
}

function scheduleDraw() {
  // rAF-coalesced: raw pointermove floods must not redraw a 1400px canvas each
  // event on low-end phones (review m10).
  if (drawQueued) return;
  drawQueued = true;
  requestAnimationFrame(() => { drawQueued = false; drawPicker(); });
}

function drawPicker() {
  if (!decodedCanvas || !pickerCorners) return;
  const ctx = pickerCanvas.getContext("2d");
  const { width, height } = pickerCanvas;
  ctx.clearRect(0, 0, width, height);
  ctx.drawImage(decodedCanvas, 0, 0); // the SAME raster pendingRGB came from (B1)
  const ok = quadIsUsable(pickerCorners);
  ctx.strokeStyle = ok ? "#2b8a3e" : "#c92a2a";
  ctx.lineWidth = Math.max(2, width / 400);
  ctx.beginPath();
  pickerCorners.forEach(([x, y], i) => (i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)));
  ctx.closePath();
  ctx.stroke();
  const r = Math.max(14, width / 45);
  pickerCorners.forEach(([x, y], i) => {
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fillStyle = i === selectedHandle ? "rgba(255,235,170,0.92)" : "rgba(255,255,255,0.85)";
    ctx.fill();
    ctx.strokeStyle = i === selectedHandle ? "#8a6430" : "#333";
    ctx.lineWidth = i === selectedHandle ? 3 : 1.5;
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(x - r, y); ctx.lineTo(x + r, y);
    ctx.moveTo(x, y - r); ctx.lineTo(x, y + r);
    ctx.strokeStyle = "#c92a2a";
    ctx.lineWidth = 1.5;
    ctx.stroke();
    ctx.fillStyle = "#1a1a1a";
    ctx.font = `bold ${Math.round(r)}px sans-serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(String(i + 1), x + r * 1.4, y - r * 1.2);
  });
  // Offset magnifier while dragging: the exact point stays visible even under
  // a finger (review m9) — a 2.5x view floating up-left of the active handle.
  if (dragIndex >= 0) {
    const [hx, hy] = pickerCorners[dragIndex];
    const src = 36, dst = 110;
    const lx = Math.min(Math.max(hx - 140, dst / 2 + 6), width - dst / 2 - 6);
    const ly = Math.max(hy - 140, dst / 2 + 6);
    ctx.save();
    ctx.beginPath();
    ctx.arc(lx, ly, dst / 2, 0, Math.PI * 2);
    ctx.clip();
    ctx.drawImage(decodedCanvas, hx - src / 2, hy - src / 2, src, src, lx - dst / 2, ly - dst / 2, dst, dst);
    ctx.restore();
    ctx.beginPath();
    ctx.arc(lx, ly, dst / 2, 0, Math.PI * 2);
    ctx.strokeStyle = "#8a6430";
    ctx.lineWidth = 3;
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(lx - 10, ly); ctx.lineTo(lx + 10, ly);
    ctx.moveTo(lx, ly - 10); ctx.lineTo(lx, ly + 10);
    ctx.strokeStyle = "#c92a2a";
    ctx.lineWidth = 1.5;
    ctx.stroke();
  }
  refreshPhotoConvertEnabled();
  $("picker-status").textContent = quadIsUsable(pickerCorners)
    ? "拖曳（或點選）控制點到棋盤四個角的交叉點；也可用方向鍵微調選取的控制點。"
    : "角點順序有誤（需 1 左上 → 2 右上 → 3 右下 → 4 左下，且不可交叉）。";
}

function selectHandle(i) {
  selectedHandle = i;
  document.querySelectorAll(".handle-select button").forEach((b, j) => {
    b.setAttribute("aria-pressed", String(j === i));
  });
}

function pickerPointFromEvent(e) {
  const rect = pickerCanvas.getBoundingClientRect();
  return [
    ((e.clientX - rect.left) / rect.width) * pickerCanvas.width,
    ((e.clientY - rect.top) / rect.height) * pickerCanvas.height,
  ];
}

function clampToCanvas([x, y]) {
  return [
    Math.min(Math.max(x, 0), pickerCanvas.width - 1),
    Math.min(Math.max(y, 0), pickerCanvas.height - 1),
  ];
}

pickerCanvas.addEventListener("pointerdown", (e) => {
  if (inFlight || dragIndex >= 0) return; // M2 + m8: one job, one pointer
  const [x, y] = pickerPointFromEvent(e);
  const rect = pickerCanvas.getBoundingClientRect();
  const grabPx = 30 * (pickerCanvas.width / rect.width); // ~30 CSS px hit radius
  let best = -1, bestDist = Infinity;
  pickerCorners.forEach(([cx, cy], i) => {
    const d = Math.hypot(cx - x, cy - y);
    if (d < bestDist) { bestDist = d; best = i; }
  });
  if (bestDist <= grabPx) {
    dragIndex = best;
    selectHandle(best);
    activePointerId = e.pointerId;
    pickerCanvas.setPointerCapture(e.pointerId);
    e.preventDefault();
    scheduleDraw();
  } else {
    // Tap-to-place: the selected handle jumps to the tapped point — the
    // drag-free operation path (review M5 / WCAG dragging).
    pickerCorners[selectedHandle] = clampToCanvas([x, y]);
    scheduleDraw();
  }
});
pickerCanvas.addEventListener("pointermove", (e) => {
  if (dragIndex < 0 || e.pointerId !== activePointerId) return;
  pickerCorners[dragIndex] = clampToCanvas(pickerPointFromEvent(e));
  scheduleDraw();
});
const endDrag = (e) => {
  if (dragIndex >= 0 && e.pointerId === activePointerId) {
    try { pickerCanvas.releasePointerCapture(e.pointerId); } catch { /* already released */ }
    dragIndex = -1;
    activePointerId = null;
    scheduleDraw();
  }
};
pickerCanvas.addEventListener("pointerup", endDrag);
pickerCanvas.addEventListener("pointercancel", endDrag);
pickerCanvas.addEventListener("lostpointercapture", endDrag);

// Keyboard path (review M5): pick a handle with the numbered buttons (or the
// canvas focused), nudge with arrows — Shift for coarse steps.
pickerCanvas.addEventListener("keydown", (e) => {
  const step = e.shiftKey ? 10 : 2;
  const moves = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
  if (moves[e.key] && pickerCorners && !inFlight) {
    const [dx, dy] = moves[e.key];
    const [x, y] = pickerCorners[selectedHandle];
    pickerCorners[selectedHandle] = clampToCanvas([x + dx, y + dy]);
    e.preventDefault();
    scheduleDraw();
  }
});
document.querySelectorAll(".handle-select button").forEach((btn, i) => {
  btn.addEventListener("click", () => { selectHandle(i); scheduleDraw(); pickerCanvas.focus(); });
});

$("photo-mode-link").addEventListener("click", openPicker);
$("photo-fallback-btn").addEventListener("click", openPicker);
$("picker-cancel-btn").addEventListener("click", () => { $("picker-section").hidden = true; });

$("photo-convert-btn").addEventListener("click", () => {
  if (!pendingRGB || inFlight || !quadIsUsable(pickerCorners)) return;
  clearError();
  currentJob += 1;
  currentJobKind = "photo";
  inFlight = true;
  setBusy(true);
  setStatus("照片模式辨識中…（含自動校正，依裝置效能約需半分鐘）");
  const copy = pendingRGB.buf.slice(0);
  worker.postMessage(
    {
      type: "photo",
      id: currentJob,
      width: pendingRGB.width,
      height: pendingRGB.height,
      buf: copy,
      corners: pickerCorners.map(([x, y]) => [x, y]),
      size: parseInt($("photo-size").value, 10),
    },
    [copy],
  );
});
