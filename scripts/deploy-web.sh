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

# Cloudflare Pages answers ANY missing path with HTTP 200 and the index.html
# body (verified 2026-08-22), so status codes cannot tell "published" from
# "absent" here. Fetch and identify by CONTENT: a real wheel is a zip ("PK").
# Echoes the sha256 of a genuine wheel, or "ABSENT"; exits non-zero only on a
# transport failure, which must never be read as either (r11).
live_wheel_sha() {
  local url=$1 tmp code
  tmp=$(mktemp)
  if ! code=$(curl -sS -L -o "$tmp" -w '%{http_code}' "$url"); then
    rm -f "$tmp"; return 1
  fi
  # THREE states, never two: an outage must not read as "absent" and authorize
  # a rewrite (r12 B-1). Absence is proven ONLY by the known Pages fallback:
  # HTTP 200 whose body is the site's own HTML, not a zip.
  if [ "$code" != "200" ]; then
    rm -f "$tmp"; echo INDETERMINATE; return 0
  fi
  if [ "$(head -c 2 "$tmp")" != "PK" ]; then
    if head -c 200 "$tmp" | grep -qi "<!doctype html"; then
      rm -f "$tmp"; echo ABSENT; return 0
    fi
    rm -f "$tmp"; echo INDETERMINATE; return 0
  fi
  sha256_of "$tmp"
  rm -f "$tmp"
}

# Wheel filenames listed in the manifest, one per line (shasum format:
# "<64 hex><spaces>[*]<name>").
manifest_names() { sed -nE 's/^[0-9a-f]{64}[[:space:]]+[*]?(.+)$/\1/p' "$MANIFEST"; }
manifest_count() { manifest_names | wc -l | tr -d ' '; }

DEPLOY_MODE=0
REARCHIVE=0
case "${1:-}" in
  --deploy) DEPLOY_MODE=1 ;;
  --rearchive) REARCHIVE=1 ;;
  "") ;;
  *) echo "usage: scripts/deploy-web.sh [--deploy|--rearchive]" >&2; exit 2 ;;
esac
LIVE_BASE="https://${PROJECT_NAME}.pages.dev"

echo "== verify published wheel archive ($MANIFEST)"
[ -f "$MANIFEST" ] || { echo "missing $MANIFEST — the wheel manifest is tracked; restore it from git" >&2; exit 1; }
# A mutable local manifest is not an immutability record (r5 M2). Deploys require
# the archive + manifest to be COMMITTED and clean, so published history is
# durable before bytes go public. Checked here AND again after the build, because
# a new release archives a wheel between the two points (r6 A-M2).
# `git status` failure must abort, not read as "clean" (r6 B-4).
require_clean_archive() {
  local dirty
  if ! dirty=$(git status --porcelain -- "$ARCHIVE"); then
    echo "git status failed for $ARCHIVE — cannot verify the archive is committed" >&2
    exit 1
  fi
  if [ -n "$dirty" ]; then
    {
      echo "refusing to deploy: $ARCHIVE has uncommitted changes:"
      echo "$dirty"
      echo
      echo "Release flow: run scripts/deploy-web.sh (stage only) to build + archive the"
      echo "wheel, commit BOTH the wheel and $MANIFEST, then re-run with --deploy."
    } >&2
    exit 1
  fi
}
[ "$DEPLOY_MODE" = 1 ] && require_clean_archive
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
  if [ "$FRESH_SHA" != "$ARCHIVED_SHA" ] && [ "$REARCHIVE" = 1 ]; then
    # Re-archiving is legal ONLY for a wheel that was never served: prove it by
    # asking the live site, and treat anything other than a clean 404 as "may be
    # published" (r11 workflow trap).
    echo "== --rearchive: confirming $WHEEL was never published"
    if ! RA_SHA=$(live_wheel_sha "$LIVE_BASE/wheels/$WHEEL"); then
      echo "cannot reach $LIVE_BASE to prove $WHEEL is unpublished — refusing to re-archive" >&2
      exit 1
    fi
    [ "$RA_SHA" = "ABSENT" ] || {
      if [ "$RA_SHA" = "INDETERMINATE" ]; then
        echo "$LIVE_BASE/wheels/$WHEEL gave an indeterminate response (outage? proxy?) — refusing to re-archive" >&2
        echo "Absence must be PROVEN before archived bytes may be replaced." >&2
      else
        echo "$LIVE_BASE/wheels/$WHEEL serves a real wheel ($RA_SHA) — it IS published; refusing to re-archive" >&2
        echo "Bump the version instead: published wheel bytes are immutable." >&2
      fi
      exit 1
    }
    cp "$WHEEL_PATH" "$ARCHIVE/$WHEEL"
    ( cd "$ARCHIVE" && grep -v "  $WHEEL\$" SHA256SUMS > SHA256SUMS.tmp || true
      shasum -a 256 "$WHEEL" >> SHA256SUMS.tmp && mv SHA256SUMS.tmp SHA256SUMS )
    echo "   re-archived $WHEEL ($FRESH_SHA) — commit the wheel AND $MANIFEST"
  elif [ "$FRESH_SHA" != "$ARCHIVED_SHA" ]; then
    {
      echo "published wheel bytes are immutable: $ARCHIVE/$WHEEL exists with DIFFERENT bytes"
      echo "  archived: $ARCHIVED_SHA"
      echo "  fresh:    $FRESH_SHA"
      echo "The source changed without a version bump."
      echo
      echo "If $WHEEL is ALREADY PUBLISHED (live at $LIVE_BASE/wheels/$WHEEL):"
      echo "  bump the version (pyproject.toml + src/goban_svg/__init__.py + uv.lock)"
      echo "  so the new build gets its own URL. Published bytes never change."
      echo
      echo "If it was archived but NEVER published — the ordinary case when source"
      echo "changes between archiving and deploying — re-archive it:"
      echo "  scripts/deploy-web.sh --rearchive"
      echo "  (that flag REFUSES unless the wheel 404s on the live site, so it can"
      echo "   never rewrite bytes an integrator may have pinned.)"
    } >&2
    exit 1
  else
    echo "   $WHEEL already archived (bytes identical)"
  fi
else
  if [ "$DEPLOY_MODE" = 1 ]; then
    {
      echo "refusing to deploy: $WHEEL is not in the published archive yet."
      echo "A wheel must be committed BEFORE it is published, so the immutability"
      echo "record exists first. Run scripts/deploy-web.sh (stage only) to archive it,"
      echo "commit $ARCHIVE/$WHEEL + $MANIFEST, then re-run with --deploy."
    } >&2
    exit 1
  fi
  cp "$WHEEL_PATH" "$ARCHIVE/$WHEEL"
  [ -z "$(tail -c 1 "$MANIFEST")" ] || printf '\n' >>"$MANIFEST"
  (cd "$ARCHIVE" && shasum -a 256 "$WHEEL" >>SHA256SUMS)
  echo "   !! NEW WHEEL ARCHIVED — commit BOTH of these, they are the immutability record:"
  echo "   !!   $ARCHIVE/$WHEEL"
  echo "   !!   $MANIFEST  (+ $FRESH_SHA  $WHEEL)"
fi

echo "== pyodide ${PYODIDE_VERSION} (self-hosted core, sha256-pinned)"
if ! [ -f "$CACHE/pyodide.mjs" ] || ! [ -f "$CACHE/pyodide.asm.wasm" ] || ! [ -f "$CACHE/python_stdlib.zip" ] || ! [ -f "$CACHE/pyodide.asm.mjs" ] || ! [ -f "$CACHE/pyodide-lock.json" ]; then
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
for f in index.html app.js worker.js style.css _headers "wheels/$WHEEL" gen/config.js \
         pyodide/pyodide.mjs pyodide/pyodide.asm.mjs pyodide/pyodide.asm.wasm \
         pyodide/python_stdlib.zip pyodide/pyodide-lock.json \
         assets/corner-correct.png assets/corner-wrong.png; do
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
  # The archive gained a wheel + manifest line since the first check if this is
  # a new release; that must be committed too (r6 A-M2).
  require_clean_archive
  echo "== pre-deploy: verify live site against the published manifest"
  while IFS= read -r line || [ -n "$line" ]; do
    [ -n "$line" ] || continue
    want=$(printf '%s' "$line" | cut -d' ' -f1)
    name=$(printf '%s' "$line" | sed -nE 's/^[0-9a-f]{64}[[:space:]]+[*]?(.+)$/\1/p')
    [ -n "$name" ] || { echo "malformed manifest line: $line" >&2; exit 1; }
    # Transport failure and HTTP status are DIFFERENT things: a 500/403/DNS/TLS
    # error must never be waved through as "new release" (r6 B-2).
    if ! got=$(live_wheel_sha "$LIVE_BASE/wheels/$name"); then
      echo "LIVE $LIVE_BASE/wheels/$name — transport failure (DNS/TLS/timeout); refusing to deploy" >&2
      exit 1
    fi
    [ "$got" != "INDETERMINATE" ] || {
      echo "LIVE $LIVE_BASE/wheels/$name gave an indeterminate response — refusing to deploy" >&2
      exit 1
    }
    if [ "$got" = "ABSENT" ]; then
      # Absent is legal ONLY for the wheel this run introduces, and only once the
      # live site is proven to be serving a different (older) version.
      [ "$name" = "$WHEEL" ] || {
        echo "LIVE $name is not served but is a previously published wheel — refusing to deploy" >&2
        exit 1
      }
      LIVE_CFG=$(curl -fsS "$LIVE_BASE/gen/config.js") || {
        echo "cannot read the live gen/config.js to confirm $WHEEL is a new release — refusing to deploy" >&2
        exit 1
      }
      LIVE_WHEEL=$(printf '%s' "$LIVE_CFG" | sed -nE 's/export const WHEEL = "([^"]+)".*/\1/p')
      [ -n "$LIVE_WHEEL" ] && [ "$LIVE_WHEEL" != "$WHEEL" ] || {
        echo "live config names '$LIVE_WHEEL' — $WHEEL is not a new release, yet it is not served; refusing to deploy" >&2
        exit 1
      }
      echo "   $name not live yet (new release; live currently serves $LIVE_WHEEL) — OK"
    else
      [ "$got" = "$want" ] || {
        echo "LIVE $name serves $got but the manifest pins $want — refusing to deploy over an inconsistent archive" >&2
        exit 1
      }
    fi
  done <<< "$PREV_MANIFEST_CONTENT"
  echo "== deploy to Cloudflare Pages (${PROJECT_NAME})"
  wrangler pages deploy web-dist --project-name "$PROJECT_NAME" --commit-dirty=true
  echo "== post-deploy smoke check"
  scripts/smoke-web.sh "$LIVE_BASE" "$WHEEL"
else
  echo "== stage only (pass --deploy to publish). Local test:"
  echo "   python3 -m http.server -d web-dist --bind 127.0.0.1 8788"
fi
