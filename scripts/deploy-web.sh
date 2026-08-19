#!/usr/bin/env bash
# Build + stage + deploy the goban-svg web app to Cloudflare Pages.
#
#   scripts/deploy-web.sh            # stage into web-dist/ only (for local testing)
#   scripts/deploy-web.sh --deploy   # stage AND wrangler pages deploy
#
# Versioning is coherent by construction (design amendment 10): the wheel is built
# fresh, its exact filename lands in gen/config.js, and the worker asserts the
# package version at boot. The Pyodide archive is pinned by version AND SHA-256,
# downloaded to a temp file, extracted to a temp dir, validated, then atomically
# moved into the cache (web code review M8 — a partial download must never
# poison the cache).

set -euo pipefail
cd "$(dirname "$0")/.."

PYODIDE_VERSION=314.0.5
PYODIDE_SHA256=f528dccea95fa8ec54295fd65bf86dd61183d11f0e52563dc8eadda45e0f78d6
PROJECT_NAME=goban-svg
CACHE="outputs/pyodide-cache/${PYODIDE_VERSION}"

echo "== build wheel"
rm -rf dist
uv build --wheel >/dev/null
WHEEL_PATH=$(ls dist/goban_svg-*-py3-none-any.whl)
WHEEL=$(basename "$WHEEL_PATH")
APP_VERSION=$(echo "$WHEEL" | sed -E 's/^goban_svg-([^-]+)-.*/\1/')
echo "   $WHEEL (version $APP_VERSION)"

echo "== pyodide ${PYODIDE_VERSION} (self-hosted core, sha256-pinned)"
if [ ! -f "$CACHE/pyodide.mjs" ]; then
  TMP_TAR=$(mktemp)
  TMP_DIR=$(mktemp -d)
  curl -fsSL "https://github.com/pyodide/pyodide/releases/download/${PYODIDE_VERSION}/pyodide-core-${PYODIDE_VERSION}.tar.bz2" -o "$TMP_TAR"
  echo "${PYODIDE_SHA256}  ${TMP_TAR}" | shasum -a 256 -c - >/dev/null
  tar -xj -C "$TMP_DIR" --strip-components=1 -f "$TMP_TAR"
  for f in pyodide.mjs pyodide.asm.mjs pyodide.asm.wasm python_stdlib.zip pyodide-lock.json; do
    [ -f "$TMP_DIR/$f" ] || { echo "pyodide archive missing $f" >&2; exit 1; }
  done
  rm -f "$TMP_TAR"
  mkdir -p "$(dirname "$CACHE")"
  rm -rf "$CACHE"
  mv "$TMP_DIR" "$CACHE"
fi

echo "== stage web-dist/"
rm -rf web-dist
mkdir -p web-dist/wheels web-dist/gen web-dist/pyodide
cp -R web/. web-dist/
cp "$WHEEL_PATH" web-dist/wheels/
cp "$CACHE"/* web-dist/pyodide/
cat > web-dist/gen/config.js <<EOF
export const WHEEL = "${WHEEL}";
export const APP_VERSION = "${APP_VERSION}";
export const PYODIDE_VERSION = "${PYODIDE_VERSION}";
EOF
for f in index.html app.js worker.js style.css _headers "wheels/$WHEEL" pyodide/pyodide.mjs gen/config.js; do
  [ -f "web-dist/$f" ] || { echo "staging incomplete: web-dist/$f missing" >&2; exit 1; }
done

echo "== staged: $(du -sh web-dist | cut -f1)"

if [ "${1:-}" = "--deploy" ]; then
  echo "== deploy to Cloudflare Pages (${PROJECT_NAME})"
  wrangler pages deploy web-dist --project-name "$PROJECT_NAME" --commit-dirty=true
  echo "== post-deploy smoke check"
  scripts/smoke-web.sh "https://${PROJECT_NAME}.pages.dev" "$WHEEL"
else
  echo "== stage only (pass --deploy to publish). Local test:"
  echo "   python3 -m http.server -d web-dist --bind 127.0.0.1 8788"
fi
