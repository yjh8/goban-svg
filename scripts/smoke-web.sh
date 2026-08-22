#!/usr/bin/env bash
# Deployed smoke check for the goban-svg web app (web code review M10).
#   scripts/smoke-web.sh [base-url] [wheel-filename] [manifest-path]
# Asserts: index served with the strict CSP + noindex, the wheel and pyodide
# runtime are fetchable, gen/config.js names the expected wheel, and EVERY wheel
# in the local (repo, trusted) web/wheels/SHA256SUMS manifest still serves its
# exact published bytes — published wheel URLs are immutable, so an integrator's
# pinned URL+hash must keep matching forever (webapp-design.md v3 amendment 1).
# The manifest is read from the repo, never from the deployed site: the repo copy
# is the trust anchor a bad deploy cannot rewrite.

set -euo pipefail
BASE="${1:-https://goban-svg.pages.dev}"
WHEEL="${2:-}"
REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
MANIFEST="${3:-$REPO_ROOT/web/wheels/SHA256SUMS}"

fail() { echo "SMOKE FAIL: $1" >&2; exit 1; }
TMPD=$(mktemp -d)
trap 'rm -rf "$TMPD"' EXIT

HDRS=$(curl -fsSI "$BASE/") || fail "index not reachable"
echo "$HDRS" | grep -qi "content-security-policy: .*connect-src 'self'" || fail "CSP missing/weak"
echo "$HDRS" | grep -qi "x-robots-tag: noindex" || fail "noindex header missing"

CFG=$(curl -fsS "$BASE/gen/config.js") || fail "gen/config.js not reachable"
[ -n "$WHEEL" ] && { echo "$CFG" | grep -q "$WHEEL" || fail "config.js does not name $WHEEL"; }
W=$(echo "$CFG" | sed -nE 's/export const WHEEL = "([^"]+)".*/\1/p')

# Pages returns 200 + index.html for missing paths, so identify by CONTENT (r11).
curl -fsSL "$BASE/wheels/$W" -o "$TMPD/w.bin" 2>/dev/null || fail "wheel $W not fetchable"
[ "$(head -c 2 "$TMPD/w.bin")" = "PK" ] || fail "wheel $W is not served (got a non-zip body — Pages fallback)"
curl -fsSI "$BASE/pyodide/pyodide.mjs" | grep -q "200" || fail "pyodide.mjs not fetchable"
curl -fsSI "$BASE/pyodide/pyodide.asm.wasm" | grep -q "200" || fail "wasm not fetchable"
curl -fsS "$BASE/" | grep -q "棋譜圖" || fail "index content unexpected"

# --- published wheel archive: every URL still serves its original bytes ---
[ -f "$MANIFEST" ] || fail "wheel manifest not found: $MANIFEST"
COUNT=0
CURRENT_LISTED=0
while read -r want name || [ -n "${want:-}" ]; do
  [ -n "${want:-}" ] || continue
  name="${name#\*}"
  [ -n "${name:-}" ] || fail "malformed line in $MANIFEST: $want"
  [ ${#want} -eq 64 ] || fail "malformed sha256 in $MANIFEST: $want"
  case "$want" in *[!0-9a-f]*) fail "malformed sha256 in $MANIFEST: $want" ;; esac
  curl -fsSL "$BASE/wheels/$name" -o "$TMPD/wheel.bin" || fail "published wheel $name not fetchable"
  [ "$(head -c 2 "$TMPD/wheel.bin")" = "PK" ] || fail "published wheel $name is not served (Pages fallback body)"
  got=$(shasum -a 256 "$TMPD/wheel.bin" | cut -d' ' -f1)
  [ "$got" = "$want" ] || fail "published wheel $name changed: served $got, manifest says $want (published wheel URLs are immutable)"
  if [ "$name" = "$W" ]; then CURRENT_LISTED=1; fi
  COUNT=$((COUNT + 1))
done < "$MANIFEST"
[ "$COUNT" -gt 0 ] || fail "no wheels listed in $MANIFEST"
[ "$CURRENT_LISTED" = 1 ] || fail "config.js names $W, which is not in $MANIFEST"

echo "SMOKE OK: $BASE ($W; $COUNT published wheel(s) sha256-verified)"
