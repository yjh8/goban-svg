#!/usr/bin/env bash
# Build + stage + deploy the goban-svg web app to Cloudflare Pages.
#
#   scripts/deploy-web.sh            # stage into web-dist/ only (for local testing)
#   scripts/deploy-web.sh --deploy   # stage AND wrangler pages deploy
#
# Versioning is coherent by construction (design amendment 10): the wheel is built
# fresh, its exact filename lands in gen/config.js, and the worker asserts the
# package version at boot.
#
# Published wheel URLs are IMMUTABLE (webapp-design.md v3 amendment 1). `web/wheels/`
# is a tracked archive holding every wheel this site has ever published, byte for
# byte, plus a `SHA256SUMS` manifest. This script:
#   1. verifies the whole archive against the manifest BEFORE it stages anything,
#   2. refuses to replace an already-published wheel's bytes (same filename, different
#      bytes = a source change without a version bump),
#   3. stages the ENTIRE archive, so every URL an integrator ever pinned keeps
#      serving the exact bytes they hashed.
#
# The Pyodide archive is pinned by version AND SHA-256, downloaded to a temp file,
# extracted to a temp dir, validated, then atomically moved into the cache (web code
# review M8 — a partial download must never poison the cache).

set -euo pipefail
cd "$(dirname "$0")/.."

PYODIDE_VERSION=314.0.5
PYODIDE_SHA256=f528dccea95fa8ec54295fd65bf86dd61183d11f0e52563dc8eadda45e0f78d6
PROJECT_NAME=goban-svg
CACHE="outputs/pyodide-cache/${PYODIDE_VERSION}"
ARCHIVE=web/wheels
MANIFEST="$ARCHIVE/SHA256SUMS"

sha256_of() { shasum -a 256 "$1" | cut -d' ' -f1; }

# Wheel filenames listed in the manifest, one per line (shasum format:
# "<64 hex><spaces>[*]<name>").
manifest_names() { sed -nE 's/^[0-9a-f]{64}[[:space:]]+[*]?(.+)$/\1/p' "$MANIFEST"; }
manifest_count() { manifest_names | wc -l | tr -d ' '; }

DEPLOY_MODE=0
[ "${1:-}" = "--deploy" ] && DEPLOY_MODE=1
LIVE_BASE="https://${PROJECT_NAME}.pages.dev"

echo "== verify published wheel archive ($MANIFEST)"
[ -f "$MANIFEST" ] || { echo "missing $MANIFEST — the wheel manifest is tracked; restore it from git" >&2; exit 1; }
# A mutable local manifest is not an immutability record (code review r5 M2):
# deploys require the archive + manifest to be committed and clean, so history
# is durable BEFORE bytes go public. (Stage-only runs skip this for dev loops.)
if [ "$DEPLOY_MODE" = 1 ]; then
  DIRTY=$(git status --porcelain -- "$ARCHIVE" 2>/dev/null || true)
  if [ -n "$DIRTY" ]; then
    echo "refusing to deploy: $ARCHIVE has uncommitted changes — commit the archive + manifest first:" >&2
    echo "$DIRTY" >&2
    exit 1
  fi
fi
# Snapshot the manifest BEFORE any append this run may do: these are the
# already-published entries the live site must still serve byte-identically.
PREV_MANIFEST_CONTENT=$(cat "$MANIFEST")
if ! ARCHIVE_CHECK=$(cd "$ARCHIVE" && shasum -a 256 -c SHA256SUMS 2>&1); then
  echo "$ARCHIVE_CHECK" >&2
  echo "wheel archive verification FAILED — published wheel bytes are immutable; restore web/wheels/ from git" >&2
  exit 1
fi
MANIFEST_LINES=$(grep -c '[^[:space:]]' "$MANIFEST" || true)
[ "$MANIFEST_LINES" -gt 0 ] || { echo "$MANIFEST lists no wheels" >&2; exit 1; }
[ "$MANIFEST_LINES" = "$(manifest_count)" ] || { echo "malformed line(s) in $MANIFEST (expected 'shasum -a 256' format)" >&2; exit 1; }
# Nothing unverified may ride along in the archive directory.
for f in "$ARCHIVE"/*.whl; do
  [ -e "$f" ] || continue
  # process substitution, not a pipe: `grep -q` exits early and would SIGPIPE the
  # left-hand side of a pipeline, which `pipefail` would report as a false failure.
  grep -qxF "$(basename "$f")" < <(manifest_names) || {
    echo "$f is not listed in $MANIFEST — every archived wheel must have a manifest entry" >&2
    exit 1
  }
done
echo "   $(manifest_count) published wheel(s) verified"

echo "== build wheel"
rm -rf dist
uv build --wheel >/dev/null
WHEEL_PATH=$(ls dist/goban_svg-*-py3-none-any.whl)
WHEEL=$(basename "$WHEEL_PATH")
APP_VERSION=$(echo "$WHEEL" | sed -E 's/^goban_svg-([^-]+)-.*/\1/')
echo "   $WHEEL (version $APP_VERSION)"

echo "== wheel archive (published URLs are immutable)"
FRESH_SHA=$(sha256_of "$WHEEL_PATH")
if [ -f "$ARCHIVE/$WHEEL" ]; then
  ARCHIVED_SHA=$(sha256_of "$ARCHIVE/$WHEEL")
  if [ "$FRESH_SHA" != "$ARCHIVED_SHA" ]; then
    {
      echo "published wheel bytes are immutable: $ARCHIVE/$WHEEL exists with DIFFERENT bytes"
      echo "  archived: $ARCHIVED_SHA"
      echo "  fresh:    $FRESH_SHA"
      echo "The source changed without a version bump. Bump the version (pyproject.toml +"
      echo "src/goban_svg/__init__.py + uv.lock) so the new build gets its own URL."
      echo "If this archive entry was never published (its manifest line is not committed),"
      echo "delete $ARCHIVE/$WHEEL, drop its $MANIFEST line, and re-run."
    } >&2
    exit 1
  fi
  echo "   $WHEEL already archived (bytes identical)"
else
  cp "$WHEEL_PATH" "$ARCHIVE/$WHEEL"
  [ -z "$(tail -c 1 "$MANIFEST")" ] || printf '\n' >>"$MANIFEST"
  (cd "$ARCHIVE" && shasum -a 256 "$WHEEL" >>SHA256SUMS)
  echo "   !! NEW WHEEL ARCHIVED — commit BOTH of these, they are the immutability record:"
  echo "   !!   $ARCHIVE/$WHEEL"
  echo "   !!   $MANIFEST  (+ $FRESH_SHA  $WHEEL)"
fi

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
mkdir -p web-dist/gen web-dist/pyodide
cp -R web/. web-dist/
# Restage the wheel directory explicitly from the manifest: the whole published
# archive ships (old URLs keep their bytes) and only manifest-listed files ship.
rm -rf web-dist/wheels
mkdir -p web-dist/wheels
cp "$MANIFEST" web-dist/wheels/SHA256SUMS
while IFS= read -r name; do
  cp "$ARCHIVE/$name" "web-dist/wheels/$name"
done < <(manifest_names)
cp "$CACHE"/* web-dist/pyodide/
cat > web-dist/gen/config.js <<EOF
export const WHEEL = "${WHEEL}";
export const APP_VERSION = "${APP_VERSION}";
export const PYODIDE_VERSION = "${PYODIDE_VERSION}";
EOF
for f in index.html app.js worker.js style.css _headers "wheels/$WHEEL" pyodide/pyodide.mjs gen/config.js; do
  [ -f "web-dist/$f" ] || { echo "staging incomplete: web-dist/$f missing" >&2; exit 1; }
done
while IFS= read -r name; do
  [ -f "web-dist/wheels/$name" ] || { echo "staging incomplete: web-dist/wheels/$name missing" >&2; exit 1; }
done < <(manifest_names)
if ! STAGED_CHECK=$(cd web-dist/wheels && shasum -a 256 -c SHA256SUMS 2>&1); then
  echo "$STAGED_CHECK" >&2
  echo "staged wheel archive does not match $MANIFEST" >&2
  exit 1
fi
echo "   wheel archive staged: $(manifest_count) wheel(s), all sha256-verified"

echo "== staged: $(du -sh web-dist | cut -f1)"

if [ "$DEPLOY_MODE" = 1 ]; then
  # Pre-deploy cross-check against the LIVE site (r5 M2): every wheel that was
  # in the manifest before this run must still be served byte-identically. A
  # 404 is legal ONLY for the freshly built current wheel (a new release).
  echo "== pre-deploy: verify live site against the published manifest"
  PRE_TMP=$(mktemp)
  while IFS= read -r line || [ -n "$line" ]; do
    [ -n "$line" ] || continue
    want=$(printf '%s' "$line" | cut -d' ' -f1)
    name=$(printf '%s' "$line" | sed -nE 's/^[0-9a-f]{64}[[:space:]]+[*]?(.+)$/\1/p')
    [ -n "$name" ] || { echo "malformed manifest line: $line" >&2; exit 1; }
    if curl -fsSL "$LIVE_BASE/wheels/$name" -o "$PRE_TMP" 2>/dev/null; then
      got=$(sha256_of "$PRE_TMP")
      [ "$got" = "$want" ] || {
        echo "LIVE $name serves $got but the manifest pins $want — refusing to deploy over an inconsistent archive" >&2
        exit 1
      }
    else
      [ "$name" = "$WHEEL" ] || {
        echo "LIVE $LIVE_BASE/wheels/$name is unreachable but is a previously published wheel — refusing to deploy" >&2
        exit 1
      }
      echo "   $name not live yet (new release) — OK"
    fi
  done <<< "$PREV_MANIFEST_CONTENT"
  rm -f "$PRE_TMP"
  echo "== deploy to Cloudflare Pages (${PROJECT_NAME})"
  wrangler pages deploy web-dist --project-name "$PROJECT_NAME" --commit-dirty=true
  echo "== post-deploy smoke check"
  scripts/smoke-web.sh "$LIVE_BASE" "$WHEEL"
else
  echo "== stage only (pass --deploy to publish). Local test:"
  echo "   python3 -m http.server -d web-dist --bind 127.0.0.1 8788"
fi
