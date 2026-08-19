#!/usr/bin/env bash
# Deployed smoke check for the goban-svg web app (web code review M10).
#   scripts/smoke-web.sh [base-url] [wheel-filename]
# Asserts: index served with the strict CSP + noindex, the wheel and pyodide
# runtime are fetchable, and gen/config.js names the expected wheel.

set -euo pipefail
BASE="${1:-https://goban-svg.pages.dev}"
WHEEL="${2:-}"

fail() { echo "SMOKE FAIL: $1" >&2; exit 1; }

HDRS=$(curl -fsSI "$BASE/") || fail "index not reachable"
echo "$HDRS" | grep -qi "content-security-policy: .*connect-src 'self'" || fail "CSP missing/weak"
echo "$HDRS" | grep -qi "x-robots-tag: noindex" || fail "noindex header missing"

CFG=$(curl -fsS "$BASE/gen/config.js") || fail "gen/config.js not reachable"
[ -n "$WHEEL" ] && { echo "$CFG" | grep -q "$WHEEL" || fail "config.js does not name $WHEEL"; }
W=$(echo "$CFG" | sed -nE 's/export const WHEEL = "([^"]+)".*/\1/p')

curl -fsSI "$BASE/wheels/$W" | grep -q "200" || fail "wheel $W not fetchable"
curl -fsSI "$BASE/pyodide/pyodide.mjs" | grep -q "200" || fail "pyodide.mjs not fetchable"
curl -fsSI "$BASE/pyodide/pyodide.asm.wasm" | grep -q "200" || fail "wasm not fetchable"
curl -fsS "$BASE/" | grep -q "棋譜圖" || fail "index content unexpected"

echo "SMOKE OK: $BASE ($W)"
