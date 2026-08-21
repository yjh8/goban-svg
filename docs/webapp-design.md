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

## Correction editor + photo-confidence flow (2026-08-21 design v2 — staff feedback round 1)

> Status: DESIGN v2. Trigger: first staff feedback — two testers, both failed first try
> on *monitor photos* (measured post-mortem: photo-mode-design.md § Calibration log,
> finding #2). The failures decompose into (a) corner placement is a precision cliff,
> (b) failures are silent until the very end, (c) recovery requires editing raw JSON —
> a programmer's surface. Three UI changes + additive payload fields close (a)–(c).
> Classifier thresholds are deliberately NOT touched: calibration waits for the
> physical-board corpus (Joseph is collecting; finding #2 records the hypotheses).
> v2 folds in ALL 14 findings of the Codex ultra design review round 1 (REJECT:
> 4 BLOCKER + 8 MAJOR + 2 MINOR — receipt in outputs/, summarized at the end).

### Release prerequisite (review F1): version bump + wheel-URL immutability

`ExtractionResult` gains a field (below), so the app ships as **0.1.1** and published
wheel URLs are *immutable*: `scripts/deploy-web.sh` gains a wheel archive
(`web/wheels/` keeps every previously published wheel byte-for-byte; the smoke check
asserts the OLD 0.1.0 URL still serves its original sha256 after deploy). go-blindspot
currently *vendors* our module sources (observed live 2026-08-21), so nothing breaks at
runtime — but the pinned 0.1.0 URL+hash in the integration prompt stays valid forever,
and their upgrade to 0.1.1 is their own atomic URL+hash+sources switch, on their
schedule. Never replace bytes at a published wheel URL.

### A. Corner-placement guidance (photo mode)

- Picker copy becomes a size-independent placement rule (review F14):
  「把控制點放在**最外圈格線的交叉點**上 — 左上角＝最上方橫線與最左方直線的交點。
  不要放在木框邊緣、視窗外框或座標數字上。」
- A captioned 正確／錯誤 thumbnail pair beside the copy (static images in
  `web/assets/`, self-hosted, CSP-compatible): readable crops from a real photo,
  handle ON the corner intersection (綠框＋「正確」) vs out on the frame
  (紅框＋「錯誤」) — non-color cues (captions + ✓/✗ glyphs) as well as color,
  descriptive `alt` text, stacked vertically on narrow screens.
- Monitor hint at both photo entry points (review F14 wording):
  「要辨識螢幕上的棋盤？請直接截圖，再使用自動辨識；效果會比拍攝螢幕好很多。」

### B. Staged photo extraction with a rectified-grid checkpoint

Fail-closed refinement currently has *no verifier* when it declines. The fix mirrors
how calibration is done by hand: look at the rectified board with the grid drawn on it.
Review F7 measured classification at ~0.1 s vs rectify ~2.5 s, so the preview stage
runs the ENTIRE extraction and stages the result — the checkpoint costs nothing extra:

- New worker op `{type:"photo-preview", id, revision, width, height, buf, corners, size}`:
  runs `extract_photo_position` ONCE (refine + rectify + classify), keeps the full
  result payload staged worker-side under an opaque `token`, and replies with a
  dedicated envelope (review F5) `{type:"photo-preview-result", id, revision, token,
  ok, refined, rectifiedRGBA (transferred), rectW, rectH, gridXs, gridYs}`
  (RGB→RGBA bridged in the worker JS; every Pyodide proxy destroyed; 480² RGBA
  ≈ 0.9 MiB). Errors reuse the existing `{ok:false, kind, message}` classification.
- **Snapshot binding (review F2)**: the client keeps a `selectionEpoch` (bumped on
  every `acceptFile`) and a `photoInputRevision` (bumped on ANY corner nudge, size
  change, or reselect). The preview request carries `{epoch, revision}`; the reply's
  token is valid only while both still match, and 「重新點角」, reselect, size change,
  corner mutation, or a worker restart all invalidate it (stale tokens are refused by
  the worker, which stages at most ONE result and drops it on any newer message).
- UI: after corner confirm → checkpoint panel shows the rectified board with the
  canonical grid overlaid (canvas) + honest refinement status: 自動微調完成 ✓ /
  「自動微調無法確認格線，將依你點的角點辨識」⚠. Buttons: 「格線有對齊 → 顯示結果」
  (commits the staged result — instant) and 「沒對齊 → 重新點角」 (discards it).
- **Provenance is client-owned** (review F5): the `refined` flag from the preview is
  kept in the editor state and shown in the result panel; it survives rerenders (the
  engine's refinement warning never re-fires on the commit path because extraction
  already ran).
- Perf truth (review F7): the flow costs the same one extraction as today; the
  checkpoint merely splits WAITING from READING. No timing promises in UI copy;
  real-phone numbers go in the calibration log when staff devices report.

### C. Click-to-cycle correction editor (both modes)

Pattern proven live on the blindspot deployment (a 海峰 staff member's app built on
our engine): click an intersection to cycle the stone. v2 semantics per review F3/F9:

- **Stone edits mutate ONLY `stones`** — marks and labels are independent collections
  and are NEVER implicitly deleted (empty-point marks/labels are valid and tested;
  board-4's K8 square lives on an empty point). Cycle: 空 → 黑 → 白 → 空.
- **One interaction SOT** (review F9): `lastAppliedPosition` + its `geom` (from the
  last successful worker result) drive all board interaction. The JSON textarea is a
  *dirty buffer*: any manual edit or import marks it dirty, which disables board
  editing until 套用修正 succeeds (dirty state is visible: 「JSON 已修改，尚未套用」).
  Every edit path — board click, mark ✕, undo, manual Apply — goes through ONE
  guarded transaction: `busy || dirty-mismatch || stale-epoch → no-op`; on success the
  new payload updates SVG + textarea + geom + history atomically.
- Click mapping: one listener on `#svg-holder`; click → SVG user coords via the
  viewBox transform → nearest intersection from `geom`; accept within 0.42·cell (a
  miss does nothing — never a wrong edit).
- Marks get blindspot-style ✕ chips (44 px hit area, `stopPropagation`, labelled
  「移除 K8 的記號」) — explicit, independently undoable removal (review F3).
- **Review rings** (review F4, renamed from `uncertain`): dashed rings mark
  *warning-backed* review points only, and the panel says plainly
  「沒有圈記的點也可能有誤 — 請整體對照原圖」 (finding #2 measured 9 silent white
  misses: absence of a ring is NOT confidence). Kind-aware affordances:
  - stone-classification kinds (`ambiguous`, `ambiguous-color`, `warm-bright`) →
    actionable ring; tapping cycles the stone; the acted-on ring clears;
    a ring can also be dismissed as-correct (「確認目前判讀」 in the point popover).
  - `unreadable-label` → informational ring on the stone; popover says the label
    needs the JSON editor (label creation stays out of scope).
  - geometry kinds (`off-image`, `no-reference`) → not stone-actionable; listed in
    the warning panel with 「重新點角」 guidance (photo mode).
- **Editor state is client-owned** (review F8): `{appliedPosition, geom, reviewPoints,
  warnings, refined, history}`. Rerender results REPLACE position/geom/SVG but
  `reviewPoints`/`warnings`/`refined` live client-side: a board edit resolves only its
  own entry; a manual JSON Apply diffs old→new positions and resolves rings only at
  changed points; a new image or new extraction resets everything.
- **Undo** (review F12): semantic inverse patches (`{point, prevStone}` /
  `{point, restoredMark}` / for manual Apply: the full previous JSON, one entry),
  pushed only after a successful rerender commit; cap 100 entries AND ~2 MB total
  (bytes counted); history resets on new image, new extraction, or provenance change.
  復原 button + Ctrl/Cmd-Z when the board has focus.
- **A11y + mobile (review F11)**: `#svg-holder` becomes a focusable editing surface
  (`tabindex=0`, `role=application` with an aria-label explaining the keys): arrow
  keys move a visible cursor ring intersection-by-intersection, Enter/Space cycles,
  Esc leaves editing; every action announced via the existing `aria-live` status
  (「K8 改為黑棋」). Touch: an edit-mode **zoom toggle** (2× CSS transform inside a
  pannable overflow container) brings effective targets to ~32–44 px; the tight
  0.42-cell snap stays fail-safe at 1×. Edit mode shows a persistent small 原圖
  thumbnail beside/above the board (mobile currently hides the source behind a tab —
  review F11), tappable to swap panes. Mark ✕ chips meet the 44 px contract via
  padded hit areas.

### Payload + engine additions (review F10/F13 — additive, `warnings` strings frozen)

1. `geom: {cell, x0, y0}` — intersection (col i, row r) at
   `(x0 + (i-1)·cell, y0 + (size-r)·cell)` in SVG user units. The worker driver
   instantiates the *exported* `render.BoardGeometry(size, cell, coords)` with the
   SAME cell/coords values it passes to `render_svg` and emits
   `geo.cell/origin_x/origin_y` — one instantiation, no re-derived ratios (F13).
   Recomputed on every successful (re)render; a round-trip test asserts ring/hit
   positions against the returned SVG's own line coordinates.
2. `ExtractionResult.uncertain: list[UncertainPoint]` — appended field with
   `field(default_factory=list)`; `UncertainPoint` is a frozen dataclass
   `{point: Point, kind: str}` (kinds: ambiguous, ambiguous-color, warm-bright,
   unreadable-label, off-image, no-reference). Emitted in the same deterministic
   order as the warnings they mirror (scan order), no duplicates per (point, kind).
   All existing constructor call sites remain valid (three positional args);
   pickles of old results are declared unsupported (none exist in the wild — the
   wheel is used in-process only). The worker serializes it explicitly as
   `[{point: "K8", kind: "..."}]`; tests pair every structured entry with its exact
   warning string and assert both text and order are unchanged from 0.1.0.
3. `warnings` strings: byte-for-byte frozen (blindspot regex-parses them; the
   integration prompt publishes them). The zh-TW regex map in app.js is untouched
   (its retirement remains the stable-warning-codes backlog item).

### Job & buffer discipline (review F6)

File intake joins the busy freeze: dropzone + file input + photo entry points are
disabled while a job runs (the existing `setBusy` surface grows to cover them), so at
most one transferred source buffer is in flight and `acceptFile` can never race a
running conversion (its async decode also checks `selectionEpoch` before committing
state — the review's decode-race). The worker stages at most one preview result and
frees every Pyodide proxy in `finally` blocks (existing convention).

### Out of scope (deliberate)

- Classifier threshold changes (H1–H3) — blocked on the physical-board corpus.
- Label/mark *creation* UI (JSON editor covers it; photo mode has none by design).
- Editing mark colors; SGF round-trip of edits (inherits existing lossiness note).
- Pan/zoom beyond the 2× edit-mode toggle; native pinch still works.

### Design-review round 1 (2026-08-21, Codex ultra on gpt-5.6-sol — REJECT, all folded in)

4 BLOCKER: wheel-URL immutability/version bump (F1) · preview snapshot-binding
token (F2) · stone-cycle annotation destruction (F3) · uncertainty affordance
honesty + kind-awareness (F4). 8 MAJOR: preview protocol envelope + provenance
(F5) · job/buffer discipline (F6) · staged-extraction perf model — measured
refine 5.3 s / rectify 2.5 s / classify 0.1 s (F7) · client-owned review-point
lifecycle (F8) · dirty-buffer vs interaction SOT (F9) · truly-additive API detail
(F10) · a11y/mobile implementability (F11) · undo semantics + memory ceiling
(F12). 2 MINOR: geom single-instantiation (F13) · size-independent copy + image
presentation contract (F14). This v2 section IS the amended design; round 2
verdict recorded below when it lands.

### v3 amendments (2026-08-21, design-review round 2 — REJECT: 4 BLOCKER + 6 MAJOR + 1 MINOR, all folded in)

Round 2 receipt: `outputs/design-review-editor-round2-20260821.md`. These amendments
SUPERSEDE conflicting v2 text and are the implementation contract. The code-review
cascade must check each item below against the actual diff.

1. **Release mechanics (R2-F1).** Measured reality: the live
   `/wheels/goban_svg-0.1.0-py3-none-any.whl` already serves sha256 `02b158f7…` —
   the integration prompt's pinned `5964bad5…` bytes were ALREADY overwritten by an
   earlier redeploy (the exact hazard F1 named, discovered post-hoc; blindspot is
   unaffected in practice — they vendor sources — but the published pin is stale and
   Joseph should notify the author; the repo's prompt copy gets the corrected hash).
   Fix, from now on: a **tracked `web/wheels/` immutable archive** seeded with the
   current live 0.1.0 bytes + a tracked `web/wheels/SHA256SUMS` manifest. This
   supersedes amendment 10's "Wheels are never committed". `deploy-web.sh`: builds
   the new version's wheel, REFUSES a same-filename/different-bytes collision against
   the manifest, stages the whole archive (not just the fresh wheel), and verifies
   every manifest entry pre-deploy; `smoke-web.sh` GETs and sha-verifies EVERY
   manifest wheel post-deploy (not just HTTP 200 on the current one). Version bump
   0.1.0→0.1.1 lands in `pyproject.toml` + `src/goban_svg/__init__.py` + `uv.lock`
   in the same commit.
2. **Photo artifact API (R2-F2).** New frozen dataclass in photo.py:
   `PhotoArtifact { result: ExtractionResult, canonical: Image, refined: bool,
   corners_used: tuple[Corner, ...] }`, returned by a new
   `extract_photo_artifact(img, corners, size, *, refine=True)`.
   `extract_photo_position()` becomes a thin wrapper returning `.result` (signature
   and behavior unchanged). The classification pass reuses the SAME canonical image
   the artifact carries — a regression test counts `_rectify_masked` calls via
   monkeypatch: exactly ONE classification rectification (total calls =
   refinement passes + 1; refinement legitimately rectifies once per pass).
3. **Closed preview protocol (R2-F3/F6).** Worker routes and exact envelopes —
   every photo-stage message carries `{id, epoch, revision, generation, token?}`:
   - `photo-preview {type, id, epoch, revision, width, height, buf, corners, size}`
     → runs `extract_photo_artifact` once, stages `{token, epoch, revision,
     payload, rectW, rectH, gridXs, gridYs, refined}` (**plain clone-safe data
     only — no PyProxy, no source RGB; the rectified pixels are NOT staged —
     they are transferred to the client in the reply and are client-owned from
     then on**; proxies destroyed in
     `finally`), replies `photo-preview-result {type, id, epoch, revision, token,
     ok, refined, rectifiedRGBA (transferred), rectW, rectH, gridXs, gridYs}` or
     `{ok:false, kind, message}` (nothing staged).
   - `photo-commit {type, id, epoch, revision, token}` → if the stage matches
     token+epoch+revision: atomically consume it and reply
     `photo-commit-result {type, id, ok:true, payload}` (the FULL staged result
     payload — svg/json/sgf/geom/uncertain/warnings/mode/refined); any mismatch or
     empty stage replies `photo-commit-result {ok:false, kind:"stale-preview"}`.
   - `photo-discard {type}` → clears the stage, no reply. The stage is also
     cleared by: a newer `photo-preview`, any `convert`, worker termination.
   - Client: `workerGeneration` increments on every `startWorker()`; replies from
     an older generation are dropped. One `invalidatePreview(reason)` path sends
     `photo-discard` (when the worker is alive), clears token/canvas/ImageData,
     hides the checkpoint UI, and returns focus to the picker; it fires on corner
     nudge, size change, reselect, 「重新點角」, worker failure/restart, and commit
     error.
4. **Per-operation guards (R2-F4).** One transaction entry point with
   operation-specific predicates (all also require `!workerOccupied` and current
   `selectionEpoch`/`editorRevision`):
   - board edit / mark ✕ / review dismissal / undo: `!dirty`
   - manual Apply (套用修正): `dirty` (its success clears dirty and bumps
     `editorRevision`)
   - JSON import: re-checks epoch after its `await file.text()` before touching
     the textarea; popover/chip controls `stopPropagation` so they never fall
     through to a board click.
5. **Review-point interaction (R2-F5).** A ringed point NEVER blind-cycles: tap
   (or Enter) opens a point inspector popover with explicit 空／黑／白 +
   「確認目前判讀」; the review item resolves only when a chosen state commits
   successfully or the user explicitly confirms. Un-ringed points cycle directly
   (空→黑→白→空), stones-only.
6. **Worker occupancy invariant (R2-F7).** `workerOccupied` is set at every
   enqueue and cleared ONLY by a terminal reply of the current generation or
   worker termination. Every intake path checks it in code — drop handler,
   file-input change, dropzone keyboard activation, `acceptFile()` itself, picker
   entry, and every postMessage site. `disabled`/`aria-disabled`/styling are
   feedback, not the guard.
7. **Review-state model (R2-F8).** Client keeps immutable `originalWarnings`
   (exact strings, exact order) and `reviewPoints:
   [{id, warningIndex, point, kind, status: open|resolved|confirmed}]`.
   Resolution rules by kind: classification kinds resolve on a stone change at
   that point or explicit 確認; `unreadable-label` resolves only on a label
   change (i.e. via Apply-diff at that point's label); geometry kinds
   (`off-image`, `no-reference`) never auto-resolve. Manual Apply diffs
   old→new Position per-point and per-collection (stones vs labels vs marks —
   a mark-only change never resolves a stone warning). A board-size change via
   Apply is a provenance reset (full editor-state reset, history cleared).
   Every history entry carries the review-status inverse of its action.
8. **History policy (R2-F9).** Entries are semantic inverse patches
   `{op, point, prevStone?, restoredMark?, reviewInverse?}`; manual Apply pushes
   `{op:"apply", prevJson}`. Budget: canonical UTF-8 bytes via `TextEncoder`,
   cap 100 entries AND 2,000,000 bytes total, oldest-first eviction — but an
   Apply whose `prevJson` snapshot cannot fit the budget alone is REJECTED with
   「JSON 過大，無法保留復原步驟」 (the applied-JSON limit stays 2 M chars, so
   the guard is explicit, not incidental). Push only after a successful commit.
9. **A11y/mobile editor contract (R2-F10).** The board editing surface is
   `role=grid` with `aria-activedescendant` over virtual gridcell ids (no 361 DOM
   nodes; one live cell element updated per move is acceptable): initial cursor =
   first open review point (else tengen); arrows move with edge clamping;
   Home/End jump to row ends; Tab exits normally to the next control; Enter/Space
   opens the inspector (which is also how review points resolve). A visible
   **coordinate form** (text input 「K8」+ 空/黑/白 buttons) is the equivalent
   non-pointer path and doubles as the smallest-screen fallback. Two dedicated
   `aria-live` regions: cursor announcements 「K8，白棋，有方形記號，需要確認」
   and mutation announcements 「K8：白棋改為黑棋；方形記號保留」 (separate from
   the boot/job status region). Touch: edit-mode zoom is computed dynamically so
   the effective cell target is ≥ 44 px (scale = max(2, 44/cellCssPx)) inside a
   pannable overflow container; the 原圖 thumbnail is a labelled button
   (「查看原圖」) that swaps panes. Mark ✕ chips get ≥ 44 px padded hit areas.
10. **Geometry wire contract (R2-F11).** Exactly `geom: {cell, originX, originY}`
    in every result payload, emitted from one `BoardGeometry(size, cell, coords)`
    instantiated with the same values passed to `render_svg`; the round-trip test
    parses the returned SVG's first/last grid-line coordinates and asserts they
    equal `originX/originY` + `(size-1)·cell`.

### Code-review round (2026-08-21, Codex ultra on gpt-5.6-sol — REJECT: 0 BLOCKER + 7 MAJOR + 5 MINOR, all fixed pre-landing)

Receipt: `outputs/code-review-editor-20260821-r5.md` (the run itself survived only
detached — see build-learnings on the shared-`~/.codex` cache crash). Fixed: live
checkpoint now invalidated by every editor mutation, worker stage cleared on
rerender too (M1) · deploy requires a git-clean archive AND pre-verifies every
published wheel URL against the live site, 404 legal only for the new current
wheel (M2) · mark chips are pointer-inert badges; clicking a marked point opens
the inspector, whose 移除記號 is the accessible control (M3) · inspector clamps
with its measured size in px (M4) · edit-zoom scales by the 0.84-cell hit
diameter so taps truly reach 44 px (M5) · virtual grid exposes
aria-rowcount/colcount + row/colindex (M6) · JSON import guarded by a buffer
revision across its await (M7) · smoke tolerates a manifest without a trailing
newline (m8) · coordinate copy is size-independent (m9) · stage schema documents
the transferred, client-owned pixels (m10) · rectification-count wording (m11) ·
fixture docs say four boards and the 0.1.1 wheel was rebuilt with the updated
README (m12).
