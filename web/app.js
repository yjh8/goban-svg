/* Main-thread controller: upload → decode/normalize → worker convert → results
 * → click-to-correct editor.
 *
 * All image work stays in this browser (see docs/webapp-design.md §privacy): the
 * canvas decodes and downscales, packed RGB is TRANSFERRED to the worker, and the
 * only network traffic is this site's own static assets (enforced by the CSP).
 *
 * Structure (webapp-design.md 2026-08-21 design v2 + v3 amendments):
 *   - §A corner guidance lives in index.html (copy + the 正確／錯誤 figure pair).
 *   - §B staged photo extraction: photo-preview → rectified-grid checkpoint →
 *     photo-commit, bound to {selectionEpoch, photoInputRevision, workerGeneration}.
 *   - §C click-to-cycle editor over ONE interaction SOT (lastAppliedPosition +
 *     lastGeom), a dirty JSON buffer, client-owned review points, and inverse-patch
 *     undo.
 * Every worker job goes through enqueue(), the single place that enforces the
 * workerOccupied invariant (v3 amendment 6).
 */

const MAX_FILE_BYTES = 25 * 1024 * 1024;
const MAX_LONG_EDGE = 1400; // keeps a 19x19 grid at >= ~60 px spacing after downscale
const MIN_LONG_EDGE = 300;
const MAX_JSON_CHARS = 2_000_000;
const HISTORY_MAX_ENTRIES = 100;
const HISTORY_MAX_BYTES = 2_000_000;
const HIT_RADIUS_CELLS = 0.42; // a miss does nothing — never a wrong edit
const MIN_TOUCH_PX = 44;
const COLUMN_LETTERS = "ABCDEFGHJKLMNOPQRSTUVWXYZ"; // Go notation skips "I"
const SVG_NS = "http://www.w3.org/2000/svg";
const CLASSIFICATION_KINDS = new Set(["ambiguous", "ambiguous-color", "warm-bright"]);
const GEOMETRY_KINDS = new Set(["off-image", "no-reference"]);
const MARK_ZH = { triangle: "三角", square: "方形", circle: "圓形", cross: "叉形" };
const TEXT_ENCODER = new TextEncoder();

const $ = (id) => document.getElementById(id);
const statusEl = $("status");
const convertBtn = $("convert-btn");
const fileInput = $("file-input");
const dropzone = $("dropzone");

/* ------------------------------------------------------------------ state */

let worker = null;
let workerReady = false;
let workerAttempts = 0;
let workerGeneration = 0; // bumped per startWorker(); replies from older generations are dropped
let workerOccupied = false; // THE job invariant: set at enqueue, cleared only by a terminal reply
let currentJob = 0;
let currentJobKind = null; // 'convert' | 'photo' | 'photo-preview' | 'photo-commit' | 'rerender'
let pendingRGB = null; // {buf, width, height}
let blobUrls = [];
let previewUrl = null;
let selectionEpoch = 0; // bumped on every acceptFile — binds decodes and jobs to ONE selection
let photoInputRevision = 0; // bumped on ANY corner nudge, size change or reselect

let photoModeActive = false; // last successful result came from photo mode
let pickerCorners = null; // [[x,y] x4] in decoded-bitmap px, TL TR BR BL
let decodedCanvas = null; // THE normalized raster: same pixels pendingRGB was packed from (review B1)

// Staged-preview binding (v3 amendment 3). The token is opaque and only valid
// while epoch+revision still match what the preview ran on.
let previewToken = null;
let previewRefined = null;
let previewEpoch = -1;
let previewRevision = -1;

// Editor state — client-owned (v2 review F8, v3 amendments 7/8).
let lastAppliedPosition = null; // parsed Position JSON: THE interaction SOT
let lastAppliedJson = ""; // its canonical text (undo snapshots + dirty comparison)
let lastGeom = null; // {cell, originX, originY} from the SAME render as the SVG
let boardSize = 0;
let editorRevision = 0; // bumped on every successful Apply / new extraction
let jsonBufferRevision = 0; // bumped on EVERY textarea-buffer change (r5 M7)
let jsonDirty = false; // the textarea is a dirty buffer: board editing pauses
let originalWarnings = []; // immutable: exact strings, exact order
let reviewPoints = []; // [{id, warningIndex, point, kind, status}]
let editorProvenance = null; // 'photo' | 'convert' | null
let photoRefined = null; // client-owned refinement provenance (never re-derived)
let history = []; // [{entry, bytes}] inverse patches, oldest first
let historyBytes = 0;
let pendingTx = null; // the transaction awaiting its rerender reply
let stoneAt = new Map();
let markAt = new Map();
let labelAt = new Map();
let cursorPoint = null; // notation of the virtual grid cursor
let inspectorPoint = null;
let editZoom = false;
let reviewSeq = 0;

/* ---------------------------------------------------------------- status */

function setStatus(text) { statusEl.textContent = text; }

function announceCursor(text) { $("board-live-cursor").textContent = text; }
function announceMutation(text) { $("board-live-mutation").textContent = text; }

function showError(mainZh, detail) {
  $("error-main").textContent = mainZh;
  $("error-detail").textContent = detail || "";
  $("error-box").hidden = false;
}
function clearError() {
  $("error-box").hidden = true;
  $("photo-fallback-btn").hidden = true;
  $("monitor-hint-error").hidden = true;
}

/* ------------------------------------------------------------ worker boot */

const bootStages = { runtime: "載入辨識引擎中…（首次開啟約需數秒）", package: "載入棋盤辨識程式中…" };
const MAX_WORKER_ATTEMPTS = 3;

function onWorkerMessage(event) {
  const msg = event.data;
  // Belt and braces beside the per-generation handler closure: a reply that
  // names an older generation is never acted on (v3 amendment 3).
  if (msg.generation !== undefined && msg.generation !== workerGeneration) return;
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

  if (msg.type === "photo-preview-result") { handlePreviewResult(msg); return; }
  if (msg.type === "photo-commit-result") { handleCommitResult(msg); return; }

  if (msg.type !== "result" && msg.type !== "error") return;
  // A terminal reply of the current generation: the worker is free again.
  workerOccupied = false;
  setBusy(false);
  if (msg.id !== currentJob) return; // stale job — a newer request superseded it
  if (msg.type === "error") {
    pendingTx = null;
    showError("處理過程發生預期外的錯誤，請重試或換一張圖片。", msg.message);
    setStatus("");
    return;
  }
  handleJobResult(msg.payload);
}

// A crashed or unbootable worker is replaced, bounded, instead of leaving the
// page permanently stuck (review M4). Worker termination is the other thing that
// clears occupancy (v3 amendment 6).
function workerFailed(detail) {
  workerReady = false;
  workerOccupied = false;
  pendingTx = null;
  if (worker) { worker.terminate(); worker = null; }
  invalidatePreview("worker"); // the stage died with the worker
  setBusy(false);
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
  workerGeneration += 1;
  const generation = workerGeneration;
  worker = new Worker("worker.js", { type: "module" });
  // Replies from an older generation are dropped, never acted on and never
  // allowed to clear the occupancy of the current one (v3 amendment 3).
  worker.onmessage = (event) => { if (generation === workerGeneration) onWorkerMessage(event); };
  worker.onerror = (e) => { if (generation === workerGeneration) workerFailed(e.message || "worker error"); };
  worker.onmessageerror = () => { if (generation === workerGeneration) workerFailed("worker message deserialization failed"); };
  // A brand-new worker owes nothing: termination above already cleared occupancy.
  workerOccupied = false;
  worker.postMessage({ type: "boot", generation });
}

/* THE single enqueue point. Every worker job passes through here, so the
 * occupancy invariant is enforced in CODE (v3 amendment 6) — disabled
 * attributes and styling are feedback, never the guard. */
function enqueue(msg, transfer, statusText) {
  if (workerOccupied || !worker || !workerReady) return false;
  workerOccupied = true;
  msg.generation = workerGeneration;
  worker.postMessage(msg, transfer || []);
  setBusy(true);
  if (statusText) setStatus(statusText);
  return true;
}

/* photo-discard is fire-and-forget (no reply, no job): it may only be sent when
 * the worker is idle, so it can never race a running preview's staging. */
function sendDiscard() {
  if (workerOccupied || !worker || !workerReady) return;
  worker.postMessage({ type: "photo-discard", generation: workerGeneration });
}

startWorker();

/* ------------------------------------------------------------ file intake */

function intakeAllowed() {
  // v3 amendment 6: every intake path checks the invariant in code.
  if (workerOccupied) {
    setStatus("辨識中，請等目前這張圖跑完再換圖。");
    return false;
  }
  return true;
}

dropzone.addEventListener("dragover", (e) => { e.preventDefault(); dropzone.classList.add("dragover"); });
dropzone.addEventListener("dragleave", () => dropzone.classList.remove("dragover"));
dropzone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropzone.classList.remove("dragover");
  if (!intakeAllowed()) return;
  const file = e.dataTransfer.files && e.dataTransfer.files[0];
  if (file) acceptFile(file);
});
// The label would otherwise open the file dialog even while a job runs.
dropzone.addEventListener("click", (e) => { if (!intakeAllowed()) e.preventDefault(); });
dropzone.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    if (!intakeAllowed()) return;
    fileInput.click();
  }
});
fileInput.addEventListener("change", () => {
  if (!fileInput.files[0]) return;
  if (!intakeAllowed()) { fileInput.value = ""; return; }
  acceptFile(fileInput.files[0]);
});

function looksHeic(file) {
  return /\.hei[cf]$/i.test(file.name) || /hei[cf]/i.test(file.type);
}

async function acceptFile(file) {
  if (!intakeAllowed()) return; // the guard, again, at the function itself
  clearError();
  pendingRGB = null;
  decodedCanvas = null;
  selectionEpoch += 1; // invalidates every in-flight decode AND any staged preview
  const epoch = selectionEpoch;
  photoInputRevision += 1;
  $("result-section").hidden = true; // a previous image's result must not sit beside a new preview
  resetEditorState();
  // Photo UI resets FIRST: a failed decode must not leave a stale picker or
  // an apparently-working photo link behind (review m6).
  photoModeActive = false;
  pickerCorners = null;
  $("picker-section").hidden = true;
  $("photo-mode-link").hidden = true;
  $("monitor-hint-upload").hidden = true;
  invalidatePreview("reselect");
  updateConvertEnabled();
  if (file.size > MAX_FILE_BYTES) {
    showError("圖片檔案太大（上限 25 MB），請縮小後再試。", `${file.name}: ${(file.size / 1e6).toFixed(1)} MB`);
    return;
  }
  let bitmap;
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
  } catch (err) {
    if (epoch !== selectionEpoch) return; // a newer selection won while we decoded
    const hint = looksHeic(file)
      ? "此瀏覽器無法讀取 HEIC 格式，請改用 Safari，或先將照片轉成 JPEG。"
      : "無法讀取這個圖片檔，請確認格式（建議 PNG 或 JPEG）。";
    showError(hint, String(err));
    return;
  }
  if (epoch !== selectionEpoch) { bitmap.close(); return; } // decode-race guard (review F6)
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
    if (epoch !== selectionEpoch) return;
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
  if (epoch !== selectionEpoch) return; // last check before state is committed
  pendingRGB = { buf: rgb.buffer, width: w, height: h };

  const preview = $("preview");
  if (previewUrl) URL.revokeObjectURL(previewUrl); // review M5: one live preview URL only
  previewUrl = URL.createObjectURL(file);
  preview.src = previewUrl;
  preview.hidden = false;
  $("dropzone-text").textContent = `已選擇：${file.name}（辨識尺寸 ${w}×${h}）— 點擊可更換`;
  $("original-img").src = previewUrl;
  $("thumb-img").src = previewUrl;
  $("photo-mode-link").hidden = false;
  $("monitor-hint-upload").hidden = false;
  setStatus(workerReady ? "可以轉換了。" : "圖片已就緒，等待辨識引擎載入…");
  updateConvertEnabled();
}

function updateConvertEnabled() {
  convertBtn.disabled = !(workerReady && pendingRGB && !workerOccupied);
}

/* ---------------------------------------------------------------- convert */

function setBusy(busy) {
  const frozen = busy || jsonDirty;
  convertBtn.disabled = busy || !(workerReady && pendingRGB);
  $("rerender-btn").disabled = busy || !jsonDirty || !lastAppliedPosition;
  // Freeze correction inputs during a run so a mid-flight edit can't be
  // silently overwritten by the returning result (review M3).
  $("json-editor").disabled = busy;
  $("json-import").disabled = busy;
  // File intake joins the busy freeze (review F6): at most one transferred
  // source buffer is ever in flight.
  fileInput.disabled = busy;
  dropzone.classList.toggle("busy", busy);
  dropzone.setAttribute("aria-disabled", String(busy));
  // Photo controls are part of the same machine (picker review M2): a running
  // job freezes corner/size mutation so the picker always shows what was sent.
  $("photo-size").disabled = busy;
  $("photo-mode-link").disabled = busy;
  $("photo-fallback-btn").disabled = busy;
  $("checkpoint-accept").disabled = busy || !previewToken;
  $("checkpoint-retry").disabled = busy;
  $("undo-btn").disabled = frozen || history.length === 0;
  for (const id of ["coord-empty", "coord-black", "coord-white"]) {
    $(id).disabled = frozen || !lastAppliedPosition;
  }
  $("coord-input").disabled = busy;
  for (const id of ["inspector-empty", "inspector-black", "inspector-white", "inspector-confirm", "inspector-unmark"]) {
    $(id).disabled = frozen;
  }
  $("board-grid").setAttribute("aria-disabled", String(frozen || !lastAppliedPosition));
  $("board-wrap").classList.toggle("frozen", frozen);
  refreshPhotoConvertEnabled();
}

function refreshPhotoConvertEnabled() {
  const usable = pickerCorners && quadIsUsable(pickerCorners);
  $("photo-convert-btn").disabled = workerOccupied || !workerReady || !pendingRGB || !usable;
}

convertBtn.addEventListener("click", () => {
  if (!pendingRGB || workerOccupied) return; // review M2: state gate, not just the DOM attribute
  clearError();
  // The worker drops its stage on any convert; the CLIENT-side checkpoint must
  // die with it, or its Accept button would re-enable into a guaranteed
  // stale-preview failure (verify lens, 2026-08-21).
  invalidatePreview("superseded");
  currentJob += 1;
  currentJobKind = "convert";
  // Copy so the user can convert the same selection again after a rerender.
  const copy = pendingRGB.buf.slice(0);
  const sent = enqueue(
    { type: "convert", id: currentJob, width: pendingRGB.width, height: pendingRGB.height, buf: copy },
    [copy],
    "辨識棋盤中…（依裝置效能約需數秒）",
  );
  if (!sent) currentJob -= 1;
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

// Which warning does a structured review point mirror? The engine emits both in
// the same scan order, so the FIRST unused warning that names this point with
// this kind's wording is the one (the strings are frozen — v3 payload item 3).
const KIND_WARNING_RE = {
  "ambiguous-color": /ambiguous stone color at (\S+)/,
  "unreadable-label": /unreadable label on the (?:black|white) stone at (\S+)/,
  "ambiguous": /ambiguous point at (\S+)/,
  "warm-bright": /bright point at (\S+) is as warm/,
  "off-image": /point (\S+) lies \(partly\) outside/,
  "no-reference": /no reliable wood reference around (\S+)/,
};

function buildReviewPoints(warnings, uncertain) {
  const used = new Set();
  const out = [];
  for (const entry of uncertain || []) {
    const re = KIND_WARNING_RE[entry.kind];
    let warningIndex = -1;
    if (re) {
      for (let i = 0; i < warnings.length; i++) {
        if (used.has(i)) continue;
        const m = warnings[i].match(re);
        if (m && m[1] === entry.point) { warningIndex = i; used.add(i); break; }
      }
    }
    reviewSeq += 1;
    out.push({ id: `rv${reviewSeq}`, warningIndex, point: entry.point, kind: entry.kind, status: "open" });
  }
  return out;
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

function showPayloadError(p) {
  if (p.kind === "extract") {
    showError(zhExtractError(p.message), p.message);
    // A failed automatic read on a selected image is the "slanted photo?"
    // signal: offer assisted corner picking (photo mode).
    if (pendingRGB) {
      $("photo-fallback-btn").hidden = false;
      $("monitor-hint-error").hidden = false;
    }
  } else if (p.kind === "corners") showError(zhExtractError(p.message), p.message);
  else if (p.kind === "invalid") showError("JSON 內容有誤，尚未套用 — 請檢查格式與座標。", p.message);
  else showError("辨識程式發生內部錯誤，請回報這個問題。", p.message);
  setStatus("");
}

function handleJobResult(p) {
  const tx = pendingTx;
  pendingTx = null;
  if (!p.ok) {
    showPayloadError(p);
    if (tx) announceMutation("修正未套用，盤面維持原狀。");
    return;
  }
  if (tx) { commitTransaction(tx, p); return; }
  // A fresh extraction: provenance follows the JOB, not a sticky flag (M3).
  handlePayload(p, {
    reset: true,
    provenance: currentJobKind === "photo" ? "photo" : "convert",
    refined: currentJobKind === "photo" && typeof p.refined === "boolean" ? p.refined : null,
  });
}

function resetEditorState() {
  lastAppliedPosition = null;
  lastAppliedJson = "";
  lastGeom = null;
  boardSize = 0;
  originalWarnings = [];
  reviewPoints = [];
  history = [];
  historyBytes = 0;
  editorProvenance = null;
  photoRefined = null;
  cursorPoint = null;
  stoneAt = new Map();
  markAt = new Map();
  labelAt = new Map();
  setDirty(false);
  closeInspector();
  editorRevision += 1;
}

/* Everything a successful payload replaces: SVG, downloads, textarea, and the
 * interaction SOT (position + geom). Review state is NOT touched here — it is
 * client-owned and only the caller knows whether this was a new extraction. */
function applyResultPayload(p) {
  blobUrls.forEach((u) => URL.revokeObjectURL(u));
  blobUrls = [];

  $("svg-holder").innerHTML = p.svg;
  const svg = currentSvg();
  if (svg) svg.setAttribute("aria-hidden", "true"); // the board speaks through the grid + live regions

  const parts = [`${p.size}×${p.size} 棋盤：黑 ${p.black} 子、白 ${p.white} 子`];
  if (p.marks) parts.push(`記號 ${p.marks} 個`);
  if (p.labels) parts.push(`手數標記 ${p.labels} 個`);
  $("summary").textContent = parts.join("、");

  makeDownload("dl-svg", p.svg, "image/svg+xml");
  makeDownload("dl-json", p.json, "application/json");
  makeDownload("dl-sgf", p.sgf, "application/x-go-sgf");
  setEditorJson(p.json);

  lastAppliedJson = p.json;
  lastAppliedPosition = JSON.parse(p.json);
  lastGeom = p.geom || null;
  boardSize = p.size;
  // Virtual-grid dimensions for AT: one DOM row/cell + activedescendant would
  // otherwise read as a 1×1 grid (code review r5 M6).
  $("board-grid").setAttribute("aria-rowcount", String(boardSize));
  $("board-grid").setAttribute("aria-colcount", String(boardSize));
  reindexPosition();
}

function reindexPosition() {
  stoneAt = new Map();
  markAt = new Map();
  labelAt = new Map();
  const pos = lastAppliedPosition;
  if (!pos) return;
  const stones = pos.stones || {};
  for (const n of stones.black || []) stoneAt.set(n, "black");
  for (const n of stones.white || []) stoneAt.set(n, "white");
  for (const m of pos.marks || []) markAt.set(m.point, m);
  for (const [n, text] of Object.entries(pos.labels || {})) labelAt.set(n, text);
}

function handlePayload(p, opts) {
  const o = opts || {};
  if (o.reset) {
    originalWarnings = p.warnings.slice();
    reviewPoints = buildReviewPoints(p.warnings, p.uncertain || []);
    history = [];
    historyBytes = 0;
    editorProvenance = o.provenance || null;
    photoRefined = o.refined === undefined ? null : o.refined;
    editorRevision += 1;
    setDirty(false);
    closeInspector();
    cursorPoint = null;
  }
  applyResultPayload(p);
  renderWarnings();

  photoModeActive = editorProvenance === "photo";
  $("experimental-banner").hidden = !photoModeActive;
  renderRefinedLine();
  $("result-section").hidden = false;
  if (!cursorPoint) cursorPoint = initialCursor();
  renderOverlay();
  updateUndoButton();
  setBusy(workerOccupied);
  if (o.reset) {
    setStatus(photoModeActive ? "轉換完成（照片模式・實驗性）。請逐子對照原圖確認。" : "轉換完成。請對照原圖確認盤面。");
    $("result-section").scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function renderRefinedLine() {
  const line = $("refined-line");
  if (!photoModeActive || photoRefined === null) { line.hidden = true; line.textContent = ""; return; }
  line.hidden = false;
  line.className = photoRefined ? "refine-status ok" : "refine-status warn";
  line.textContent = photoRefined
    ? "自動微調完成 ✓（格線已依棋盤自動對齊）"
    : "自動微調無法確認格線，已依你點的角點辨識 ⚠";
}

function renderWarnings() {
  const wl = $("warnings-list");
  wl.innerHTML = "";
  if (!originalWarnings.length) { $("warnings-box").hidden = true; return; }
  originalWarnings.forEach((raw, index) => {
    const li = document.createElement("li");
    li.textContent = zhWarning(raw);
    const mine = reviewPoints.filter((r) => r.warningIndex === index);
    if (mine.length && mine.every((r) => r.status !== "open")) {
      li.classList.add("resolved");
      const tag = document.createElement("span");
      tag.className = "review-tag";
      tag.textContent = mine.some((r) => r.status === "confirmed") ? "（已確認）" : "（已修正）";
      li.appendChild(tag);
    } else if (mine.some((r) => GEOMETRY_KINDS.has(r.kind)) && editorProvenance === "photo") {
      const tag = document.createElement("span");
      tag.className = "review-tag geometry";
      tag.textContent = "（棋盤定位問題 — 建議「重新點角」後重新辨識）";
      li.appendChild(tag);
    }
    wl.appendChild(li);
  });
  $("warnings-raw").textContent = originalWarnings.join("\n");
  $("warnings-box").hidden = false;
}

/* -------------------------------------------------------- board geometry */

function currentSvg() { return $("svg-holder").querySelector("svg"); }

function colIndex(letter) { return COLUMN_LETTERS.indexOf(letter) + 1; }

function parseNotation(text) {
  const t = String(text || "").trim().toUpperCase();
  const m = t.match(/^([A-Z])(\d{1,2})$/);
  if (!m) return null;
  const col = colIndex(m[1]);
  const row = parseInt(m[2], 10);
  if (col < 1 || col > boardSize || row < 1 || row > boardSize) return null;
  return { col, row, notation: `${m[1]}${row}` };
}

function notationOf(col, row) { return `${COLUMN_LETTERS[col - 1]}${row}`; }

function pointXY(notation) {
  if (!lastGeom || !boardSize) return null;
  const p = parseNotation(notation);
  if (!p) return null;
  return {
    x: lastGeom.originX + (p.col - 1) * lastGeom.cell,
    y: lastGeom.originY + (boardSize - p.row) * lastGeom.cell,
  };
}

function viewBox() {
  const svg = currentSvg();
  if (!svg || !svg.viewBox || !svg.viewBox.baseVal) return null;
  const vb = svg.viewBox.baseVal;
  if (!vb.width || !vb.height) return null;
  return vb;
}

function pointPercent(notation, dx, dy) {
  const xy = pointXY(notation);
  const vb = viewBox();
  if (!xy || !vb) return null;
  return {
    left: ((xy.x + (dx || 0)) / vb.width) * 100,
    top: ((xy.y + (dy || 0)) / vb.height) * 100,
  };
}

function hitTest(event) {
  const svg = currentSvg();
  const vb = viewBox();
  if (!svg || !vb || !lastGeom || !boardSize) return null;
  const rect = svg.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;
  const ux = ((event.clientX - rect.left) / rect.width) * vb.width;
  const uy = ((event.clientY - rect.top) / rect.height) * vb.height;
  const col = Math.round((ux - lastGeom.originX) / lastGeom.cell) + 1;
  const row = boardSize - Math.round((uy - lastGeom.originY) / lastGeom.cell);
  if (col < 1 || col > boardSize || row < 1 || row > boardSize) return null;
  const notation = notationOf(col, row);
  const xy = pointXY(notation);
  if (!xy) return null;
  const dist = Math.hypot(ux - xy.x, uy - xy.y);
  return dist <= HIT_RADIUS_CELLS * lastGeom.cell ? notation : null;
}

/* ------------------------------------------------------- overlay + cursor */

function openReviewsAt(notation) {
  return reviewPoints.filter((r) => r.point === notation && r.status === "open");
}

function renderOverlay() {
  const svg = currentSvg();
  const overlay = $("board-overlay");
  overlay.innerHTML = "";
  if (!svg || !lastGeom || !lastAppliedPosition) { hideCursorCell(); return; }

  const stale = svg.querySelector("g.review-rings");
  if (stale) stale.remove();
  const g = document.createElementNS(SVG_NS, "g");
  g.setAttribute("class", "review-rings");
  g.setAttribute("fill", "none");
  for (const r of reviewPoints) {
    if (r.status !== "open") continue;
    const xy = pointXY(r.point);
    if (!xy) continue;
    const circle = document.createElementNS(SVG_NS, "circle");
    circle.setAttribute("cx", String(xy.x));
    circle.setAttribute("cy", String(xy.y));
    circle.setAttribute("r", String(lastGeom.cell * 0.46));
    circle.setAttribute("stroke", CLASSIFICATION_KINDS.has(r.kind) ? "#c92a2a" : "#4a5b6b");
    circle.setAttribute("stroke-width", String(lastGeom.cell * 0.09));
    circle.setAttribute("stroke-dasharray", `${lastGeom.cell * 0.2} ${lastGeom.cell * 0.14}`);
    g.appendChild(circle);
  }
  svg.appendChild(g);

  for (const mark of lastAppliedPosition.marks || []) {
    const chip = makeMarkChip(mark);
    if (chip) overlay.appendChild(chip);
  }
  updateCursorCell(false);
  updateZoom();
}

function makeMarkChip(mark) {
  // A pointer-INERT badge: a 44px interactive chip half a cell off-point covers
  // the intersection itself once cells drop under ~31 CSS px, stealing the tap
  // (code review r5 M3). Removal lives in the point inspector — clicking a
  // marked point opens it (like a ringed point), and the inspector's 移除記號
  // is the 44px accessible control.
  const pct = pointPercent(mark.point, lastGeom.cell * 0.5, -lastGeom.cell * 0.5);
  if (!pct) return null;
  const badge = document.createElement("span");
  badge.className = "mark-chip";
  badge.setAttribute("aria-hidden", "true");
  badge.textContent = "✕";
  badge.style.setProperty("left", `${pct.left}%`);
  badge.style.setProperty("top", `${pct.top}%`);
  return badge;
}

function hideCursorCell() {
  $("cursor-cell").hidden = true;
  $("board-grid").removeAttribute("aria-activedescendant");
}

function updateCursorCell(announce) {
  const cell = $("cursor-cell");
  const vb = viewBox();
  if (!cursorPoint || !lastGeom || !vb) { hideCursorCell(); return; }
  const pct = pointPercent(cursorPoint);
  if (!pct) { hideCursorCell(); return; }
  cell.hidden = false;
  cell.style.setProperty("left", `${pct.left}%`);
  cell.style.setProperty("top", `${pct.top}%`);
  cell.style.setProperty("width", `${(lastGeom.cell / vb.width) * 100}%`);
  const text = describePoint(cursorPoint);
  cell.setAttribute("aria-label", text);
  const parsed = parseNotation(cursorPoint);
  if (parsed && boardSize) {
    // aria row 1 = the TOP row (board row `boardSize`) — r5 M6.
    $("cursor-row").setAttribute("aria-rowindex", String(boardSize - parsed.row + 1));
    cell.setAttribute("aria-colindex", String(parsed.col));
  }
  $("board-grid").setAttribute("aria-activedescendant", "cursor-cell");
  if (announce) announceCursor(text);
}

function describePoint(notation) {
  const parts = [notation];
  const stone = stoneAt.get(notation);
  parts.push(stone === "black" ? "黑棋" : stone === "white" ? "白棋" : "空點");
  const mark = markAt.get(notation);
  if (mark) parts.push(`有${MARK_ZH[mark.type] || "特殊"}記號`);
  const label = labelAt.get(notation);
  if (label) parts.push(`手數「${label}」`);
  if (openReviewsAt(notation).length) parts.push("需要確認");
  return parts.join("，");
}

function initialCursor() {
  const open = reviewPoints.find((r) => r.status === "open" && parseNotation(r.point));
  if (open) return open.point;
  if (!boardSize) return null;
  const mid = Math.ceil(boardSize / 2);
  return notationOf(mid, mid);
}

function setCursor(notation, announce) {
  cursorPoint = notation;
  updateCursorCell(announce !== false);
}

/* Edit-mode zoom: the effective cell target must reach 44 px (v3 amendment 9).
 * The board is WIDENED inside a pannable container rather than CSS-transformed,
 * so hit-testing and the percentage overlay keep working untouched. */
function updateZoom() {
  const wrap = $("board-wrap");
  if (!editZoom) { wrap.style.setProperty("--edit-zoom", "1"); return; }
  const vb = viewBox();
  const baseWidth = $("board-zoom").clientWidth;
  if (!vb || !lastGeom || !baseWidth) { wrap.style.setProperty("--edit-zoom", "2"); return; }
  const cellCssPx = baseWidth * (lastGeom.cell / vb.width);
  // The tappable circle's DIAMETER is 2·HIT_RADIUS_CELLS·cell (0.84 cell), so
  // the cell itself must reach 44/0.84 ≈ 52 px for a true 44 px target — zooming
  // the cell to only 44 px left a 37 px target (code review r5 M5).
  const scale = Math.max(2, MIN_TOUCH_PX / (2 * HIT_RADIUS_CELLS) / Math.max(cellCssPx, 1));
  wrap.style.setProperty("--edit-zoom", String(Math.round(scale * 100) / 100));
}

$("edit-zoom-btn").addEventListener("click", () => {
  editZoom = !editZoom;
  $("edit-zoom-btn").setAttribute("aria-pressed", String(editZoom));
  $("edit-zoom-btn").textContent = editZoom ? "離開放大" : "放大編輯";
  $("board-zoom").classList.toggle("zoomed", editZoom);
  updateZoom();
  renderOverlay();
});

window.addEventListener("resize", () => { if (lastGeom) { updateZoom(); updateCursorCell(false); } });

/* ------------------------------------------------------------ transactions */

/* ONE transaction entry point with per-operation predicates (v3 amendment 4).
 * ops: 'stone' | 'mark' | 'apply' | 'undo'  (a pure review confirmation commits
 * locally — it changes no JSON, so it never queues a job). */
function canRun(op) {
  if (workerOccupied) return false;
  if (!worker || !workerReady) return false;
  if (op === "apply") return jsonDirty && !!lastAppliedPosition;
  return !jsonDirty && !!lastAppliedPosition && !!lastGeom;
}

function blockedNotice() {
  if (workerOccupied) { setStatus("辨識中，請稍候。"); return; }
  if (jsonDirty) {
    setStatus("JSON 已修改，尚未套用 — 請先按「套用修正並重新產生」，或改回原內容。");
    announceMutation("JSON 已修改，尚未套用，棋盤點按暫停。");
    return;
  }
  if (!workerReady) setStatus("辨識引擎尚未就緒，請稍候再修正。");
}

function beginTransaction(tx) {
  if (!canRun(tx.op)) { blockedNotice(); return false; }
  // A live photo checkpoint must not survive an editor mutation: accepting it
  // later would silently replace the edits (code review r5 M1). The worker
  // clears its stage on rerender too — this is the client half.
  invalidatePreview("editor-mutation");
  clearError();
  currentJob += 1;
  currentJobKind = "rerender";
  const record = { ...tx, epoch: selectionEpoch, editorRev: editorRevision, jobId: currentJob };
  const sent = enqueue({ type: "rerender", id: currentJob, json: tx.json }, [], tx.status || "套用修正中…");
  if (!sent) { currentJob -= 1; return false; }
  pendingTx = record;
  return true;
}

function commitTransaction(tx, p) {
  // The transaction was bound to one selection and one editor revision.
  if (tx.epoch !== selectionEpoch || tx.editorRev !== editorRevision) return;
  const prevSize = boardSize;
  applyResultPayload(p);

  if (tx.op === "apply") {
    const sizeChanged = p.size !== prevSize;
    setDirty(false);
    editorRevision += 1;
    if (sizeChanged) {
      // A board-size change is a provenance reset (v3 amendment 7).
      originalWarnings = [];
      reviewPoints = [];
      history = [];
      historyBytes = 0;
      editorProvenance = null;
      photoRefined = null;
      photoModeActive = false;
      $("experimental-banner").hidden = true;
      cursorPoint = null;
      announceMutation("棋盤大小已變更，已重設判讀確認狀態與復原紀錄。");
    } else {
      applyDiffResolutions(tx.prevPosition, lastAppliedPosition);
      if (tx.historyEntry) pushHistory(tx.historyEntry);
      announceMutation("已套用 JSON 修正。");
    }
  } else if (tx.op === "undo") {
    const top = history.pop();
    if (top) historyBytes -= top.bytes;
    restoreReview(tx.reviewRestore);
    if (tx.wasApply) setDirty(false);
    announceMutation(tx.announce || "已復原上一步。");
  } else {
    resolveReviews(tx.resolveIds, "resolved");
    if (tx.historyEntry) pushHistory(tx.historyEntry);
    announceMutation(tx.announce || "已更新盤面。");
  }

  if (!cursorPoint) cursorPoint = initialCursor();
  renderWarnings();
  renderRefinedLine();
  renderOverlay();
  updateUndoButton();
  setBusy(false);
  setStatus(tx.op === "undo" ? "已復原上一步。" : "修正已套用。");
}

function resolveReviews(ids, status) {
  if (!ids || !ids.length) return;
  const wanted = new Set(ids);
  for (const r of reviewPoints) if (wanted.has(r.id) && r.status === "open") r.status = status;
}

function restoreReview(inverse) {
  if (!inverse) return;
  const byId = new Map(reviewPoints.map((r) => [r.id, r]));
  for (const item of inverse) {
    const r = byId.get(item.id);
    if (r) r.status = item.status;
  }
}

function reviewSnapshot(ids) {
  const wanted = ids ? new Set(ids) : null;
  return reviewPoints
    .filter((r) => !wanted || wanted.has(r.id))
    .map((r) => ({ id: r.id, status: r.status }));
}

/* Manual Apply resolves rings ONLY at points whose OWN collection changed
 * (v3 amendment 7): a mark-only edit never resolves a stone warning. */
function applyDiffResolutions(oldPos, newPos) {
  if (!oldPos || !newPos) return;
  const oldStones = collectionMap(oldPos, "stones");
  const newStones = collectionMap(newPos, "stones");
  const oldLabels = new Map(Object.entries(oldPos.labels || {}));
  const newLabels = new Map(Object.entries(newPos.labels || {}));
  for (const r of reviewPoints) {
    if (r.status !== "open") continue;
    if (CLASSIFICATION_KINDS.has(r.kind)) {
      if ((oldStones.get(r.point) || null) !== (newStones.get(r.point) || null)) r.status = "resolved";
    } else if (r.kind === "unreadable-label") {
      if ((oldLabels.get(r.point) || null) !== (newLabels.get(r.point) || null)) r.status = "resolved";
    }
    // geometry kinds never auto-resolve.
  }
}

function collectionMap(pos, which) {
  const map = new Map();
  if (which === "stones") {
    const stones = pos.stones || {};
    for (const n of stones.black || []) map.set(n, "black");
    for (const n of stones.white || []) map.set(n, "white");
  }
  return map;
}

/* ------------------------------------------------------------- mutations */

function clonePosition() { return JSON.parse(JSON.stringify(lastAppliedPosition)); }

function positionText(pos) { return JSON.stringify(pos, null, 2); }

function withStone(notation, color) {
  const pos = clonePosition();
  pos.stones = pos.stones || {};
  pos.stones.black = (pos.stones.black || []).filter((n) => n !== notation);
  pos.stones.white = (pos.stones.white || []).filter((n) => n !== notation);
  if (color) pos.stones[color].push(notation);
  return pos; // marks and labels are untouched — stone edits mutate stones ONLY
}

function withoutMark(notation) {
  const pos = clonePosition();
  pos.marks = (pos.marks || []).filter((m) => m.point !== notation);
  return pos;
}

function withMark(mark) {
  const pos = clonePosition();
  pos.marks = (pos.marks || []).filter((m) => m.point !== mark.point);
  pos.marks.push({ ...mark });
  return pos;
}

function colorZh(color) { return color === "black" ? "黑棋" : color === "white" ? "白棋" : "空點"; }

function stoneChangeText(notation, prev, next) {
  let text = `${notation}：${colorZh(prev)}改為${colorZh(next)}`;
  const mark = markAt.get(notation);
  if (mark) text += `；${MARK_ZH[mark.type] || "特殊"}記號保留`;
  const label = labelAt.get(notation);
  if (label) text += `；手數「${label}」保留`;
  return text;
}

function setStoneAt(notation, color) {
  if (!canRun("stone")) { blockedNotice(); return false; }
  const prev = stoneAt.get(notation) || null;
  if (prev === color) { announceMutation(`${notation} 已經是${colorZh(color)}。`); return false; }
  const resolveIds = openReviewsAt(notation).filter((r) => CLASSIFICATION_KINDS.has(r.kind)).map((r) => r.id);
  const entry = {
    op: "stone",
    point: notation,
    prevStone: prev,
    reviewInverse: reviewSnapshot(resolveIds),
  };
  return beginTransaction({
    op: "stone",
    json: positionText(withStone(notation, color)),
    historyEntry: entry,
    resolveIds,
    announce: stoneChangeText(notation, prev, color),
    status: "更新盤面中…",
  });
}

function cycleStoneAt(notation) {
  const current = stoneAt.get(notation) || null;
  const next = current === null ? "black" : current === "black" ? "white" : null;
  return setStoneAt(notation, next);
}

function removeMarkAt(notation) {
  if (!canRun("mark")) { blockedNotice(); return false; }
  const mark = markAt.get(notation);
  if (!mark) return false;
  const entry = { op: "mark", point: notation, restoredMark: { ...mark }, reviewInverse: [] };
  return beginTransaction({
    op: "mark",
    json: positionText(withoutMark(notation)),
    historyEntry: entry,
    resolveIds: [],
    announce: `${notation}：已移除${MARK_ZH[mark.type] || "特殊"}記號`,
    status: "移除記號中…",
  });
}

/* An explicit 「確認目前判讀」 changes no JSON, so it commits locally — but it is
 * still a history step, with the same review inverse every other entry carries. */
function confirmReviewAt(notation) {
  if (!canRun("stone")) { blockedNotice(); return; }
  invalidatePreview("editor-mutation"); // same rule as beginTransaction (r5 M1)
  const ids = openReviewsAt(notation).filter((r) => CLASSIFICATION_KINDS.has(r.kind)).map((r) => r.id);
  if (!ids.length) return;
  const entry = { op: "review", point: notation, reviewInverse: reviewSnapshot(ids) };
  resolveReviews(ids, "confirmed");
  pushHistory(entry);
  renderWarnings();
  renderOverlay();
  updateUndoButton();
  announceMutation(`${notation}：已確認目前判讀。`);
  setStatus("已確認這個點的判讀。");
}

/* --------------------------------------------------------------- history */

function historyEntryBytes(entry) { return TEXT_ENCODER.encode(JSON.stringify(entry)).length; }

function pushHistory(entry) {
  const bytes = historyEntryBytes(entry);
  history.push({ entry, bytes });
  historyBytes += bytes;
  while (history.length > HISTORY_MAX_ENTRIES) {
    const dropped = history.shift();
    historyBytes -= dropped.bytes;
  }
  while (historyBytes > HISTORY_MAX_BYTES && history.length > 1) {
    const dropped = history.shift();
    historyBytes -= dropped.bytes;
  }
  updateUndoButton();
}

function updateUndoButton() {
  $("undo-btn").disabled = workerOccupied || jsonDirty || history.length === 0;
}

function undo() {
  if (!canRun("undo")) { blockedNotice(); return; }
  closeInspector(); // an open popover would otherwise display pre-undo state
  const top = history[history.length - 1];
  if (!top) return;
  const entry = top.entry;
  if (entry.op === "review") {
    history.pop();
    historyBytes -= top.bytes;
    restoreReview(entry.reviewInverse);
    renderWarnings();
    renderOverlay();
    updateUndoButton();
    announceMutation(`已復原：${entry.point} 的確認狀態。`);
    setStatus("已復原上一步。");
    return;
  }
  let json = null;
  let announce = "已復原上一步。";
  if (entry.op === "apply") {
    json = entry.prevJson;
    announce = "已復原：JSON 套用前的盤面。";
  } else if (entry.op === "stone") {
    json = positionText(withStone(entry.point, entry.prevStone));
    announce = `已復原：${entry.point} 改回${colorZh(entry.prevStone)}。`;
  } else if (entry.op === "mark") {
    json = positionText(withMark(entry.restoredMark));
    announce = `已復原：${entry.point} 的記號已放回。`;
  }
  if (json === null) return;
  beginTransaction({
    op: "undo",
    json,
    reviewRestore: entry.reviewInverse,
    wasApply: entry.op === "apply",
    announce,
    status: "復原中…",
  });
}

$("undo-btn").addEventListener("click", undo);

/* ---------------------------------------------------- board interaction */

const boardGrid = $("board-grid");

boardGrid.addEventListener("click", (e) => {
  if (!lastAppliedPosition || !lastGeom) return;
  const hit = hitTest(e);
  if (!hit) return; // a miss does nothing
  boardGrid.focus();
  setCursor(hit, true);
  if (!canRun("stone")) { blockedNotice(); return; }
  // Ringed points AND marked points open the inspector instead of blind-cycling
  // (v3-5; r5 M3 — the inspector is now the pointer path for mark removal too).
  if (openReviewsAt(hit).length || markAt.has(hit)) { openInspector(hit); return; }
  closeInspector();
  cycleStoneAt(hit);
});

boardGrid.addEventListener("keydown", (e) => {
  // The activedescendant pattern keeps focus ON the grid element itself; any
  // key event whose target is something else (belt beside the restructure that
  // moved the inspector out of the grid) is not ours to handle.
  if (e.target !== boardGrid) return;
  if (!lastAppliedPosition || !lastGeom || !boardSize) return;
  if ((e.key === "z" || e.key === "Z") && (e.metaKey || e.ctrlKey)) {
    e.preventDefault();
    undo();
    return;
  }
  if (e.key === "Escape" && !$("inspector").hidden) { e.preventDefault(); closeInspector(true); return; }
  const cur = parseNotation(cursorPoint || "") || parseNotation(initialCursor() || "");
  if (!cur) return;
  let col = cur.col;
  let row = cur.row;
  let moved = false;
  if (e.key === "ArrowLeft") { col -= 1; moved = true; }
  else if (e.key === "ArrowRight") { col += 1; moved = true; }
  else if (e.key === "ArrowUp") { row += 1; moved = true; }
  else if (e.key === "ArrowDown") { row -= 1; moved = true; }
  else if (e.key === "Home") { col = 1; moved = true; }
  else if (e.key === "End") { col = boardSize; moved = true; }
  else if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    if (!canRun("stone")) { blockedNotice(); return; }
    openInspector(cursorPoint);
    return;
  } else return; // Tab and everything else leaves normally
  e.preventDefault();
  col = Math.min(Math.max(col, 1), boardSize); // edge clamping
  row = Math.min(Math.max(row, 1), boardSize);
  if (moved) setCursor(notationOf(col, row), true);
});

boardGrid.addEventListener("focus", () => { if (cursorPoint) updateCursorCell(true); });

/* -------------------------------------------------------- point inspector */

function openInspector(notation) {
  if (!notation || !lastAppliedPosition) return;
  inspectorPoint = notation;
  const reviews = openReviewsAt(notation);
  const box = $("inspector");
  $("inspector-title").textContent = `${notation} — 判讀確認`;
  const reasons = [];
  for (const r of reviews) {
    const raw = r.warningIndex >= 0 ? originalWarnings[r.warningIndex] : null;
    if (raw) reasons.push(zhWarning(raw));
  }
  if (reviews.some((r) => r.kind === "unreadable-label")) {
    reasons.push("手數標記請用下方「修正辨識結果」的 JSON 編輯器修改。");
  }
  if (reviews.some((r) => GEOMETRY_KINDS.has(r.kind))) {
    reasons.push("這個點的棋盤定位不可靠 — 建議按「重新點角」重新辨識。");
  }
  if (!reasons.length) reasons.push(describePoint(notation));
  $("inspector-reason").textContent = reasons.join("　");

  const stone = stoneAt.get(notation) || null;
  $("inspector-empty").setAttribute("aria-pressed", String(stone === null));
  $("inspector-black").setAttribute("aria-pressed", String(stone === "black"));
  $("inspector-white").setAttribute("aria-pressed", String(stone === "white"));
  $("inspector-confirm").hidden = !reviews.some((r) => CLASSIFICATION_KINDS.has(r.kind));
  $("inspector-unmark").hidden = !markAt.has(notation);
  $("inspector-unmark").setAttribute("aria-label", `移除 ${notation} 的記號`);

  // Clamped with the dialog's MEASURED size (after unhiding), so its edges can
  // never leave the board box on narrow screens (code review r5 M4 — a center
  // clamped to 12% puts an 88%-wide dialog's left edge at −32%).
  box.hidden = false;
  const pct = pointPercent(notation, 0, lastGeom ? lastGeom.cell * 0.6 : 0);
  if (pct) {
    const wrap = $("board-wrap");
    const ww = wrap.clientWidth;
    const wh = wrap.clientHeight;
    const bw = box.offsetWidth;
    const bh = box.offsetHeight;
    const half = bw / 2 + 4;
    const cx = Math.min(Math.max((pct.left / 100) * ww, half), Math.max(ww - half, half));
    const ty = Math.min(Math.max((pct.top / 100) * wh, 4), Math.max(wh - bh - 4, 4));
    box.style.setProperty("left", `${cx}px`);
    box.style.setProperty("top", `${ty}px`);
  }
  $("inspector-black").focus();
}

function closeInspector(returnFocus) {
  const box = $("inspector");
  if (!box) return;
  const wasOpen = !box.hidden;
  box.hidden = true;
  inspectorPoint = null;
  if (wasOpen && returnFocus) $("board-grid").focus();
}

function wireInspectorButton(id, handler) {
  const btn = $(id);
  btn.addEventListener("pointerdown", (e) => e.stopPropagation());
  btn.addEventListener("click", (e) => {
    e.stopPropagation(); // never falls through to a board click (v3 amendment 4)
    e.preventDefault();
    handler();
  });
}

wireInspectorButton("inspector-empty", () => { const p = inspectorPoint; closeInspector(true); setStoneAt(p, null); });
wireInspectorButton("inspector-black", () => { const p = inspectorPoint; closeInspector(true); setStoneAt(p, "black"); });
wireInspectorButton("inspector-white", () => { const p = inspectorPoint; closeInspector(true); setStoneAt(p, "white"); });
wireInspectorButton("inspector-confirm", () => { const p = inspectorPoint; closeInspector(true); confirmReviewAt(p); });
wireInspectorButton("inspector-unmark", () => { const p = inspectorPoint; closeInspector(true); removeMarkAt(p); });
wireInspectorButton("inspector-close", () => closeInspector(true));
$("inspector").addEventListener("click", (e) => e.stopPropagation());
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("inspector").hidden) closeInspector(true); });

/* ------------------------------------------------------- coordinate form */

function coordAction(color) {
  const parsed = parseNotation($("coord-input").value);
  if (!parsed) {
    setStatus("座標格式有誤 — 請輸入像 K8 這樣的座標（直行 A–T 略過 I）。");
    announceMutation("座標格式有誤。");
    return;
  }
  setCursor(parsed.notation, true);
  setStoneAt(parsed.notation, color);
}

$("coord-empty").addEventListener("click", () => coordAction(null));
$("coord-black").addEventListener("click", () => coordAction("black"));
$("coord-white").addEventListener("click", () => coordAction("white"));
$("coord-input").addEventListener("keydown", (e) => {
  if (e.key !== "Enter") return;
  e.preventDefault();
  const parsed = parseNotation($("coord-input").value);
  if (!parsed) { setStatus("座標格式有誤 — 請輸入像 K8 這樣的座標（直行 A–T 略過 I）。"); return; }
  setCursor(parsed.notation, true);
  if (openReviewsAt(parsed.notation).length) openInspector(parsed.notation);
});

/* --------------------------------------------------------- JSON correction */

let settingEditor = false;

function setEditorJson(text) {
  settingEditor = true;
  $("json-editor").value = text;
  settingEditor = false;
  jsonBufferRevision += 1;
  setDirty(false);
}

function setDirty(value) {
  jsonDirty = value;
  $("dirty-badge").hidden = !value;
  $("rerender-btn").disabled = workerOccupied || !value || !lastAppliedPosition;
  updateUndoButton();
  $("board-wrap").classList.toggle("frozen", value || workerOccupied);
  $("board-grid").setAttribute("aria-disabled", String(value || workerOccupied || !lastAppliedPosition));
  for (const id of ["coord-empty", "coord-black", "coord-white"]) {
    $(id).disabled = value || workerOccupied || !lastAppliedPosition;
  }
  if (value) closeInspector();
}

$("json-editor").addEventListener("input", () => {
  if (settingEditor) return;
  jsonBufferRevision += 1;
  setDirty(true);
});

$("rerender-btn").addEventListener("click", () => {
  if (workerOccupied) return;
  if (!jsonDirty) { setStatus("JSON 沒有變更，不需要套用。"); return; }
  const text = $("json-editor").value;
  if (text.length > MAX_JSON_CHARS) {
    showError("JSON 內容過大（上限約 2 MB），請檢查是否貼錯內容。", `${text.length} chars`);
    return;
  }
  const entry = { op: "apply", prevJson: lastAppliedJson, reviewInverse: reviewSnapshot(null) };
  if (historyEntryBytes(entry) > HISTORY_MAX_BYTES) {
    showError("JSON 過大，無法保留復原步驟", `${historyEntryBytes(entry)} bytes`);
    return;
  }
  beginTransaction({
    op: "apply",
    json: text,
    historyEntry: entry,
    prevPosition: lastAppliedPosition,
    status: "套用修正、重新產生棋譜圖中…",
  });
});

$("json-import").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  if (workerOccupied) { e.target.value = ""; setStatus("辨識中，請稍候再匯入 JSON。"); return; }
  if (file.size > MAX_JSON_CHARS) {
    showError("JSON 檔案過大（上限約 2 MB）。", `${file.name}: ${file.size} bytes`);
    e.target.value = ""; // else re-picking the same (shrunk) file fires no change event
    return;
  }
  const epoch = selectionEpoch;
  const bufferRev = jsonBufferRevision;
  const text = await file.text();
  // Re-guard after the await: a new image, a running job, OR any buffer change
  // (newer import / manual typing) since the read started must win over this
  // slow import (r5 M7).
  if (epoch !== selectionEpoch) { e.target.value = ""; return; }
  if (workerOccupied) {
    e.target.value = "";
    setStatus("匯入已取消（辨識進行中），請稍後再匯入一次。");
    return;
  }
  if (bufferRev !== jsonBufferRevision) {
    e.target.value = "";
    setStatus("匯入已取消（JSON 內容在讀取期間已變更），請再匯入一次。");
    return;
  }
  settingEditor = true;
  $("json-editor").value = text;
  settingEditor = false;
  jsonBufferRevision += 1;
  setDirty(true); // an imported buffer is dirty until 套用修正 succeeds
  e.target.value = ""; // let the same file be imported again after an edit
  setStatus("已匯入 JSON — 請按「套用修正並重新產生」。");
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
$("thumb-btn").addEventListener("click", () => {
  selectTab("original");
  $("pane-original").scrollIntoView({ behavior: "smooth", block: "nearest" });
});

/* ------------------------------------------------- photo mode: corner picker */

const pickerCanvas = $("picker-canvas");
let dragIndex = -1;
let activePointerId = null; // review m8: one pointer owns a drag
let selectedHandle = 0; // keyboard/tap-to-place target (review M5)
let drawQueued = false;

function openPicker() {
  if (!pendingRGB || !decodedCanvas || workerOccupied) return; // M2: never mid-job
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

/* ANY corner nudge / size change / reselect invalidates the staged preview and
 * bumps the revision the next preview will be bound to (v3 amendment 3). */
function touchPhotoInput(reason) {
  photoInputRevision += 1;
  invalidatePreview(reason);
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
    ? "把控制點放在最外圈格線的交叉點上；可拖曳、點選，或用方向鍵微調選取的控制點。"
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
  if (workerOccupied || dragIndex >= 0) return; // M2 + m8: one job, one pointer
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
    touchPhotoInput("corner");
    scheduleDraw();
  }
});
pickerCanvas.addEventListener("pointermove", (e) => {
  if (dragIndex < 0 || e.pointerId !== activePointerId) return;
  pickerCorners[dragIndex] = clampToCanvas(pickerPointFromEvent(e));
  touchPhotoInput("corner");
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
  if (moves[e.key] && pickerCorners && !workerOccupied) {
    const [dx, dy] = moves[e.key];
    const [x, y] = pickerCorners[selectedHandle];
    pickerCorners[selectedHandle] = clampToCanvas([x + dx, y + dy]);
    touchPhotoInput("corner");
    e.preventDefault();
    scheduleDraw();
  }
});
document.querySelectorAll(".handle-select button").forEach((btn, i) => {
  btn.addEventListener("click", () => { selectHandle(i); scheduleDraw(); pickerCanvas.focus(); });
});

$("photo-size").addEventListener("change", () => touchPhotoInput("size"));

$("photo-mode-link").addEventListener("click", openPicker);
$("photo-fallback-btn").addEventListener("click", openPicker);
$("picker-cancel-btn").addEventListener("click", () => { $("picker-section").hidden = true; });

/* --------------------------------------- photo mode: preview → checkpoint */

let checkpointLive = false; // whether the checkpoint canvas currently shows a preview

/* The ONE invalidation path (v3 amendment 3): discard worker-side, clear the
 * token, drop the image, hide the checkpoint. Focus returns to the picker only
 * when the checkpoint owned it (or the user asked to re-place corners) — moving
 * focus out from under a size <select> would be a focus-steal. */
function invalidatePreview(reason) {
  const section = $("checkpoint-section");
  const hadToken = previewToken !== null;
  // Nothing staged and nothing shown: a corner drag must not do DOM work on
  // every pointermove.
  if (!hadToken && section.hidden && !checkpointLive) return;
  const focusWasInside = section.contains(document.activeElement);
  if (hadToken) sendDiscard();
  previewToken = null;
  previewRefined = null;
  previewEpoch = -1;
  previewRevision = -1;
  checkpointLive = false;
  const canvas = $("checkpoint-canvas");
  const ctx = canvas.getContext("2d");
  if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
  canvas.width = 0;
  canvas.height = 0;
  section.hidden = true;
  $("checkpoint-status").textContent = "";
  if (reason === "retry" || focusWasInside) {
    if (pendingRGB && decodedCanvas) {
      $("picker-section").hidden = false;
      drawPicker();
      pickerCanvas.focus();
      if (reason === "retry") $("picker-section").scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }
}

$("photo-convert-btn").addEventListener("click", () => {
  if (!pendingRGB || workerOccupied || !quadIsUsable(pickerCorners)) return;
  clearError();
  invalidatePreview("newer"); // the worker drops the old stage too, on the newer preview
  currentJob += 1;
  currentJobKind = "photo-preview";
  const copy = pendingRGB.buf.slice(0);
  const epoch = selectionEpoch;
  const revision = photoInputRevision;
  const sent = enqueue(
    {
      type: "photo-preview",
      id: currentJob,
      epoch,
      revision,
      width: pendingRGB.width,
      height: pendingRGB.height,
      buf: copy,
      corners: pickerCorners.map(([x, y]) => [x, y]),
      size: parseInt($("photo-size").value, 10),
    },
    [copy],
    "照片辨識中…（含自動校正）",
  );
  if (!sent) currentJob -= 1;
});

function handlePreviewResult(msg) {
  workerOccupied = false;
  setBusy(false);
  if (msg.id !== currentJob) return;
  if (!msg.ok) {
    previewToken = null;
    if (msg.kind === "extract" || msg.kind === "corners") showError(zhExtractError(msg.message), msg.message);
    else showError("照片辨識發生內部錯誤，請回報這個問題。", msg.message);
    setStatus("");
    return;
  }
  if (msg.epoch !== selectionEpoch || msg.revision !== photoInputRevision) {
    // The corners/size/image moved while the preview ran: its token is void.
    previewToken = msg.token;
    invalidatePreview("stale");
    setStatus("設定已變更，請再按一次「依角點辨識 → 先確認格線」。");
    return;
  }
  previewToken = msg.token;
  previewRefined = msg.refined;
  previewEpoch = msg.epoch;
  previewRevision = msg.revision;
  showCheckpoint(msg);
}

function showCheckpoint(msg) {
  const canvas = $("checkpoint-canvas");
  canvas.width = msg.rectW;
  canvas.height = msg.rectH;
  const ctx = canvas.getContext("2d");
  // Painted once and released — the canvas keeps the pixels; retaining the
  // ImageData would hold ~0.9 MB purely as a boolean (verify lens, 2026-08-21).
  ctx.putImageData(new ImageData(new Uint8ClampedArray(msg.rectifiedRGBA), msg.rectW, msg.rectH), 0, 0);
  checkpointLive = true;
  const xs = msg.gridXs || [];
  const ys = msg.gridYs || [];
  if (xs.length && ys.length) {
    ctx.strokeStyle = "rgba(201,42,42,0.9)";
    ctx.lineWidth = Math.max(1, msg.rectW / 700);
    const x0 = xs[0], x1 = xs[xs.length - 1], y0 = ys[0], y1 = ys[ys.length - 1];
    ctx.beginPath();
    for (const x of xs) { ctx.moveTo(x + 0.5, y0 + 0.5); ctx.lineTo(x + 0.5, y1 + 0.5); }
    for (const y of ys) { ctx.moveTo(x0 + 0.5, y + 0.5); ctx.lineTo(x1 + 0.5, y + 0.5); }
    ctx.stroke();
  }
  const refine = $("checkpoint-refine");
  refine.className = msg.refined ? "refine-status ok" : "refine-status warn";
  refine.textContent = msg.refined
    ? "自動微調完成 ✓（格線已依棋盤自動對齊）"
    : "自動微調無法確認格線，將依你點的角點辨識 ⚠";
  $("checkpoint-section").hidden = false;
  $("checkpoint-accept").disabled = workerOccupied;
  $("checkpoint-status").textContent = msg.refined
    ? "已產生拉正預覽，自動微調完成 — 請確認紅色格線是否對齊棋盤。"
    : "已產生拉正預覽，自動微調無法確認格線 — 請仔細確認紅色格線是否對齊棋盤。";
  setStatus("請確認紅色格線是否對齊棋盤。");
  $("checkpoint-section").scrollIntoView({ behavior: "smooth", block: "start" });
  $("checkpoint-accept").focus();
}

$("checkpoint-accept").addEventListener("click", () => {
  if (workerOccupied || !previewToken) return;
  if (previewEpoch !== selectionEpoch || previewRevision !== photoInputRevision) {
    showError("預覽已失效（圖片或角點已變更），請重新點角並再試一次。", "epoch/revision mismatch");
    invalidatePreview("stale");
    return;
  }
  clearError();
  currentJob += 1;
  currentJobKind = "photo-commit";
  const sent = enqueue(
    { type: "photo-commit", id: currentJob, epoch: previewEpoch, revision: previewRevision, token: previewToken },
    [],
    "產生棋譜圖中…",
  );
  if (!sent) currentJob -= 1;
});

$("checkpoint-retry").addEventListener("click", () => {
  if (workerOccupied) return;
  invalidatePreview("retry");
  setStatus("請重新調整四個角點，再按「依角點辨識 → 先確認格線」。");
});

function handleCommitResult(msg) {
  workerOccupied = false;
  setBusy(false);
  if (msg.id !== currentJob) return;
  if (!msg.ok) {
    showError("預覽已失效，請重新點角並再試一次。", msg.kind || "stale-preview");
    invalidatePreview("commit-error");
    setStatus("");
    return;
  }
  const refined = previewRefined;
  // The stage was consumed worker-side, so this is a plain local reset — NOT
  // invalidatePreview(), which would re-open the picker because focus currently
  // sits on the checkpoint's own button.
  previewToken = null;
  previewRefined = null;
  previewEpoch = -1;
  previewRevision = -1;
  checkpointLive = false;
  const canvas = $("checkpoint-canvas");
  canvas.width = 0;
  canvas.height = 0;
  $("checkpoint-section").hidden = true;
  $("checkpoint-status").textContent = "";
  $("picker-section").hidden = true;
  handlePayload(msg.payload, { reset: true, provenance: "photo", refined });
}
