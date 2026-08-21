/* Module Web Worker: hosts Pyodide + the goban_svg package.
 *
 * Protocol (postMessage):
 *   in : {type:"boot"}
 *   in : {type:"convert",  id, generation, width, height, buf}
 *                                                    buf = transferred ArrayBuffer, packed RGB
 *   in : {type:"photo",    id, generation, width, height, buf, corners, size}
 *                                                    corners = [[x,y] x4] TL,TR,BR,BL in
 *                                                    decoded-bitmap px (auto-refined engine-side).
 *                                                    LEGACY single-shot route, CLI parity: kept
 *                                                    working, no longer used by the UI (the UI
 *                                                    goes through preview + commit below).
 *   in : {type:"rerender", id, generation, json}     json = edited Position JSON text
 *   out: {type:"boot-progress", stage}
 *   out: {type:"ready", appVersion, pyodideVersion}
 *   out: {type:"boot-error", message}
 *   out: {type:"result", id, generation, payload}    payload = parsed driver JSON (ok or error)
 *   out: {type:"error", id, generation, message}     unexpected JS/bridge failure
 *
 * Staged photo protocol (webapp-design.md v3 amendment 3) — CLOSED set of routes,
 * every stage message carrying {id, epoch, revision, generation, token?}:
 *   in : {type:"photo-preview", id, epoch, revision, generation, width, height, buf, corners, size}
 *   out: {type:"photo-preview-result", id, epoch, revision, generation, token, ok:true, refined,
 *         rectifiedRGBA (transferred), rectW, rectH, gridXs, gridYs}
 *      | {type:"photo-preview-result", id, epoch, revision, generation, ok:false, kind, message}
 *   in : {type:"photo-commit",  id, epoch, revision, generation, token}
 *   out: {type:"photo-commit-result", id, epoch, revision, generation, ok:true, payload}
 *      | {type:"photo-commit-result", id, epoch, revision, generation, ok:false, kind:"stale-preview"}
 *   in : {type:"photo-discard"}                     no reply
 *
 * At most ONE preview is staged. The stage holds plain, clone-safe data only —
 * never a PyProxy and never the source RGB — and is cleared by: a newer
 * photo-preview, any convert/photo extraction, a commit (atomic consume),
 * photo-discard, and worker termination. A commit whose token/epoch/revision
 * does not match the stage is refused with kind "stale-preview" and does NOT
 * clear the stage (a late duplicate must not destroy a valid newer preview).
 *
 * The Python driver ALWAYS returns a JSON string: {"ok":true, svg, json, sgf, size,
 * black, white, marks, labels, warnings[], uncertain[], geom{}} or {"ok":false,
 * kind:"extract"|"corners"|"invalid"|"internal", message}. Classification happens in
 * Python where the exception types live.
 */

const DRIVER = `
import json as _json

from goban_svg.board import Position
from goban_svg.extract import ExtractionError, extract_position
from goban_svg.photo import (
    PHOTO_CELL,
    PHOTO_MARGIN_CELLS,
    extract_photo_artifact,
    extract_photo_position,
)
from goban_svg.png_codec import Image
from goban_svg.render import BoardGeometry, render_svg
from goban_svg.sgf import position_to_sgf

# ONE pair of render parameters for the whole driver: the geometry the payload
# reports must be the geometry the SVG was drawn with (v3 amendment 10), so both
# render_svg and BoardGeometry read these same two names -- never a re-derived
# ratio, never a second literal.
_CELL = 36.0
_COORDS = True

# The canonical (rectified) image of the LAST photo artifact, handed to JS once
# and then dropped. Module-level rather than returned through JSON because it is
# ~0.7 MB of pixels; JS reads it through the buffer protocol.
_CANONICAL = {"rgb": None}


def _payload(pos, warnings, uncertain=()):
    geo = BoardGeometry(size=pos.size, cell=_CELL, coords=_COORDS)
    black = sum(1 for c in pos.stones.values() if c == "black")
    return {
        "ok": True,
        "svg": render_svg(pos, cell=_CELL, coords=_COORDS),
        "json": pos.to_json(),
        "sgf": position_to_sgf(pos),
        "size": pos.size,
        "black": black,
        "white": len(pos.stones) - black,
        "marks": len(pos.marks),
        "labels": len(pos.labels),
        "warnings": list(warnings),
        # Point-addressed mirror of the point-naming warnings, serialized
        # explicitly (never a dataclass repr crossing the bridge).
        "uncertain": [{"point": u.point.notation(), "kind": u.kind} for u in uncertain],
        "geom": {"cell": geo.cell, "originX": geo.origin_x, "originY": geo.origin_y},
    }


def _ok(pos, warnings, uncertain=()):
    return _json.dumps(_payload(pos, warnings, uncertain))


def _err(kind, exc):
    return _json.dumps({"ok": False, "kind": kind, "message": str(exc)})


def convert_rgb(js_buf, width, height):
    try:
        # assign_to copies the JS buffer straight into a preallocated bytearray --
        # one copy instead of the to_bytes()+bytearray() double copy (review M6).
        pixels = bytearray(width * height * 3)
        js_buf.assign_to(pixels)
        img = Image(width=width, height=height, pixels=pixels)
        result = extract_position(img)
        return _ok(result.position, result.warnings, result.uncertain)
    except ExtractionError as exc:
        return _err("extract", exc)
    except Exception as exc:  # never let a traceback cross the bridge unlabelled
        return _err("internal", exc)


def rerender(text):
    try:
        pos = Position.from_json(text)
        return _ok(pos, [], [])
    except ValueError as exc:
        return _err("invalid", exc)
    except Exception as exc:
        return _err("internal", exc)


def photo_rgb(js_buf, width, height, corners_json, size):
    """LEGACY single-shot photo route (CLI parity). The UI uses the staged pair."""
    try:
        pixels = bytearray(width * height * 3)
        js_buf.assign_to(pixels)
        img = Image(width=width, height=height, pixels=pixels)
        corners = [tuple(c) for c in _json.loads(corners_json)]
        result = extract_photo_position(img, corners, size)
        payload = _payload(result.position, result.warnings, result.uncertain)
        payload["mode"] = "photo"
        return _json.dumps(payload)
    except ExtractionError as exc:
        return _err("extract", exc)
    except ValueError as exc:
        # corner-geometry complaints, not JSON problems (picker review m7)
        return _err("corners", exc)
    except Exception as exc:
        return _err("internal", exc)


def photo_artifact_rgb(js_buf, width, height, corners_json, size):
    """One extraction -> the full result payload PLUS its rectified by-products.

    The canonical image is the very one the classifier read, so the grid the
    checkpoint draws is provably the grid the stones were read against. Its
    pixels are parked in _CANONICAL for take_canonical(); everything else comes
    back as JSON.
    """
    _CANONICAL["rgb"] = None
    try:
        pixels = bytearray(width * height * 3)
        js_buf.assign_to(pixels)
        img = Image(width=width, height=height, pixels=pixels)
        corners = [tuple(c) for c in _json.loads(corners_json)]
        artifact = extract_photo_artifact(img, corners, size)
        result = artifact.result
        payload = _payload(result.position, result.warnings, result.uncertain)
        payload["mode"] = "photo"
        payload["refined"] = artifact.refined
        margin = PHOTO_MARGIN_CELLS * PHOTO_CELL
        grid = [margin + i * PHOTO_CELL for i in range(size)]
        _CANONICAL["rgb"] = artifact.canonical.pixels
        return _json.dumps({
            "ok": True,
            "payload": payload,
            "rectW": artifact.canonical.width,
            "rectH": artifact.canonical.height,
            "gridXs": grid,
            "gridYs": list(grid),
            "refined": artifact.refined,
            "cornersUsed": [[float(x), float(y)] for x, y in artifact.corners_used],
        })
    except ExtractionError as exc:
        return _err("extract", exc)
    except ValueError as exc:
        return _err("corners", exc)
    except Exception as exc:
        return _err("internal", exc)


def take_canonical():
    """Hand the canonical RGB bytes over exactly once, dropping our reference."""
    buf = _CANONICAL["rgb"]
    _CANONICAL["rgb"] = None
    return buf


def clear_canonical():
    _CANONICAL["rgb"] = None
`;

let bootPromise = null;

/* ------------------------------------------------------------ preview stage */

let stage = null; // at most ONE staged preview; plain clone-safe data only
let tokenSeq = 0;

function newToken() {
  tokenSeq += 1;
  return `pv${tokenSeq}-${Math.random().toString(36).slice(2, 10)}`;
}

async function boot() {
  const cfg = await import("./gen/config.js");
  postMessage({ type: "boot-progress", stage: "runtime" });
  const { loadPyodide } = await import("./pyodide/pyodide.mjs");
  const py = await loadPyodide({ indexURL: new URL("./pyodide/", import.meta.url).href });
  postMessage({ type: "boot-progress", stage: "package" });
  await py.loadPackage(new URL(`./wheels/${cfg.WHEEL}`, import.meta.url).href);
  py.runPython(DRIVER);
  const version = py.runPython("import goban_svg; goban_svg.__version__");
  if (version !== cfg.APP_VERSION) {
    throw new Error(`wheel version ${version} != deployed app version ${cfg.APP_VERSION}`);
  }
  return { py, appVersion: version, pyodideVersion: cfg.PYODIDE_VERSION };
}

function ensureBoot() {
  if (!bootPromise) bootPromise = boot();
  return bootPromise;
}

/* Pull the canonical image out of Python and bridge RGB -> RGBA in JS. Every
 * proxy/buffer view is released in `finally`, and Python's own reference is
 * dropped by take_canonical() so the pixels are freed with the proxy. */
function takeCanonicalRGBA(py, width, height) {
  let proxy = null;
  let view = null;
  try {
    proxy = py.runPython("take_canonical()");
    if (!proxy || typeof proxy.getBuffer !== "function") {
      throw new Error("canonical image missing from the extraction");
    }
    view = proxy.getBuffer("u8");
    const src = view.data;
    const out = new Uint8ClampedArray(width * height * 4);
    for (let i = 0, j = 0; j < out.length; i += 3, j += 4) {
      out[j] = src[i];
      out[j + 1] = src[i + 1];
      out[j + 2] = src[i + 2];
      out[j + 3] = 255;
    }
    return out;
  } finally {
    if (view) view.release();
    if (proxy && typeof proxy.destroy === "function") proxy.destroy();
    py.runPython("clear_canonical()");
  }
}

async function runPreview(msg) {
  stage = null; // a newer preview always drops the older stage
  const envelope = {
    type: "photo-preview-result",
    id: msg.id,
    epoch: msg.epoch,
    revision: msg.revision,
    generation: msg.generation,
  };
  try {
    const { py } = await ensureBoot();
    const view = new Uint8Array(msg.buf);
    py.globals.set("_RGB_JS", view);
    py.globals.set("_CORNERS_JSON", JSON.stringify(msg.corners));
    let raw;
    try {
      raw = py.runPython(
        `photo_artifact_rgb(_RGB_JS, ${msg.width | 0}, ${msg.height | 0}, _CORNERS_JSON, ${msg.size | 0})`,
      );
    } finally {
      py.runPython("del _RGB_JS; del _CORNERS_JSON");
    }
    const meta = JSON.parse(raw);
    if (!meta.ok) {
      py.runPython("clear_canonical()"); // nothing staged on the error path
      postMessage({ ...envelope, ok: false, kind: meta.kind, message: meta.message });
      return;
    }
    const rgba = takeCanonicalRGBA(py, meta.rectW, meta.rectH);
    // The pixels are TRANSFERRED to the client, so the stage keeps only the
    // small metadata beside the result payload -- staging a detached buffer
    // would be a lie, and the client owns the image from here on.
    stage = {
      token: newToken(),
      epoch: msg.epoch,
      revision: msg.revision,
      generation: msg.generation,
      payload: meta.payload,
      refined: meta.refined,
      rectW: meta.rectW,
      rectH: meta.rectH,
      gridXs: meta.gridXs,
      gridYs: meta.gridYs,
    };
    postMessage(
      {
        ...envelope,
        token: stage.token,
        ok: true,
        refined: meta.refined,
        rectifiedRGBA: rgba.buffer,
        rectW: meta.rectW,
        rectH: meta.rectH,
        gridXs: meta.gridXs,
        gridYs: meta.gridYs,
      },
      [rgba.buffer],
    );
  } catch (err) {
    stage = null;
    postMessage({ ...envelope, ok: false, kind: "internal", message: String(err) });
  }
}

function commitPreview(msg) {
  const held = stage;
  const matches =
    held && held.token === msg.token && held.epoch === msg.epoch && held.revision === msg.revision;
  const envelope = {
    type: "photo-commit-result",
    id: msg.id,
    epoch: msg.epoch,
    revision: msg.revision,
    generation: msg.generation,
  };
  if (!matches) {
    // Deliberately does NOT clear the stage: a late/duplicate commit from an
    // older revision must not destroy a valid newer preview.
    postMessage({ ...envelope, ok: false, kind: "stale-preview" });
    return;
  }
  stage = null; // atomic consume
  postMessage({ ...envelope, ok: true, payload: held.payload });
}

/* All message handling is SERIALIZED through one promise chain. self.onmessage
 * being async means a second message's handler would otherwise interleave at the
 * first await — clobbering the shared Python globals, and letting a synchronous
 * photo-discard be resurrected by a suspended preview's later `stage =`
 * assignment (verify lens, 2026-08-21). The client's workerOccupied invariant
 * makes overlap unreachable in practice; this makes it impossible. */
let jobChain = Promise.resolve();
self.onmessage = (event) => {
  const msg = event.data;
  jobChain = jobChain.then(() => handleMessage(msg), () => handleMessage(msg));
};

async function handleMessage(msg) {
  if (msg.type === "boot") {
    try {
      const { appVersion, pyodideVersion } = await ensureBoot();
      postMessage({ type: "ready", appVersion, pyodideVersion });
    } catch (err) {
      bootPromise = null; // allow retry
      postMessage({ type: "boot-error", message: String(err) });
    }
    return;
  }

  if (msg.type === "photo-discard") {
    stage = null;
    return;
  }

  if (msg.type === "photo-commit") {
    commitPreview(msg); // no Python needed: the payload was computed at preview time
    return;
  }

  if (msg.type === "photo-preview") {
    await runPreview(msg);
    return;
  }

  if (msg.type === "convert" || msg.type === "rerender" || msg.type === "photo") {
    // ANY of these supersedes a staged preview — including rerender: an editor
    // mutation while a checkpoint is live must kill the stage, or a later
    // commit would silently replace the user's edits (code review r5 M1; the
    // client invalidates its side in beginTransaction).
    stage = null;
    try {
      const { py } = await ensureBoot();
      let raw;
      if (msg.type === "convert") {
        const view = new Uint8Array(msg.buf);
        py.globals.set("_RGB_JS", view);
        try {
          raw = py.runPython(`convert_rgb(_RGB_JS, ${msg.width | 0}, ${msg.height | 0})`);
        } finally {
          py.runPython("del _RGB_JS"); // release the JsProxy so the buffer can be GC'd
        }
      } else if (msg.type === "photo") {
        const view = new Uint8Array(msg.buf);
        py.globals.set("_RGB_JS", view);
        py.globals.set("_CORNERS_JSON", JSON.stringify(msg.corners));
        try {
          raw = py.runPython(`photo_rgb(_RGB_JS, ${msg.width | 0}, ${msg.height | 0}, _CORNERS_JSON, ${msg.size | 0})`);
        } finally {
          py.runPython("del _RGB_JS; del _CORNERS_JSON");
        }
      } else {
        py.globals.set("_JSON_TEXT", msg.json);
        try {
          raw = py.runPython("rerender(_JSON_TEXT)");
        } finally {
          py.runPython("del _JSON_TEXT");
        }
      }
      postMessage({ type: "result", id: msg.id, generation: msg.generation, payload: JSON.parse(raw) });
    } catch (err) {
      postMessage({ type: "error", id: msg.id, generation: msg.generation, message: String(err) });
    }
  }
}
