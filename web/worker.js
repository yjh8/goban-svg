/* Module Web Worker: hosts Pyodide + the goban_svg package.
 *
 * Protocol (postMessage):
 *   in : {type:"boot"}
 *   in : {type:"convert",  id, width, height, buf}   buf = transferred ArrayBuffer, packed RGB
 *   in : {type:"photo",    id, width, height, buf, corners, size}
 *                                                    corners = [[x,y] x4] TL,TR,BR,BL in
 *                                                    decoded-bitmap px (auto-refined engine-side)
 *   in : {type:"rerender", id, json}                 json = edited Position JSON text
 *   out: {type:"boot-progress", stage}
 *   out: {type:"ready", appVersion, pyodideVersion}
 *   out: {type:"boot-error", message}
 *   out: {type:"result", id, payload}                payload = parsed driver JSON (ok or error)
 *   out: {type:"error", id, message}                 unexpected JS/bridge failure
 *
 * The Python driver ALWAYS returns a JSON string: {"ok":true, svg, json, sgf, size,
 * black, white, marks, labels, warnings[]} or {"ok":false, kind:"extract"|"invalid"|
 * "internal", message}. Classification happens in Python where the exception types live.
 */

const DRIVER = `
import json as _json

from goban_svg.board import Position
from goban_svg.extract import ExtractionError, extract_position
from goban_svg.photo import extract_photo_position
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
        # assign_to copies the JS buffer straight into a preallocated bytearray --
        # one copy instead of the to_bytes()+bytearray() double copy (review M6).
        pixels = bytearray(width * height * 3)
        js_buf.assign_to(pixels)
        img = Image(width=width, height=height, pixels=pixels)
        result = extract_position(img)
        return _ok(result.position, result.warnings)
    except ExtractionError as exc:
        return _err("extract", exc)
    except Exception as exc:  # never let a traceback cross the bridge unlabelled
        return _err("internal", exc)


def rerender(text):
    try:
        pos = Position.from_json(text)
        return _ok(pos, [])
    except ValueError as exc:
        return _err("invalid", exc)
    except Exception as exc:
        return _err("internal", exc)


def photo_rgb(js_buf, width, height, corners_json, size):
    try:
        pixels = bytearray(width * height * 3)
        js_buf.assign_to(pixels)
        img = Image(width=width, height=height, pixels=pixels)
        corners = [tuple(c) for c in _json.loads(corners_json)]
        result = extract_photo_position(img, corners, size)
        payload = _json.loads(_ok(result.position, result.warnings))
        payload["mode"] = "photo"
        return _json.dumps(payload)
    except ExtractionError as exc:
        return _err("extract", exc)
    except ValueError as exc:
        # corner-geometry complaints, not JSON problems (picker review m7)
        return _err("corners", exc)
    except Exception as exc:
        return _err("internal", exc)
`;

let bootPromise = null;

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

self.onmessage = async (event) => {
  const msg = event.data;
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

  if (msg.type === "convert" || msg.type === "rerender" || msg.type === "photo") {
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
      postMessage({ type: "result", id: msg.id, payload: JSON.parse(raw) });
    } catch (err) {
      postMessage({ type: "error", id: msg.id, message: String(err) });
    }
  }
};
