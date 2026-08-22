#!/usr/bin/env bash
# Run a Codex ultra review against goban-svg with PROJECT-LOCAL state.
#
#   scripts/codex-review.sh <prompt-file> [output-file]
#   scripts/codex-review.sh - [output-file]      # prompt on stdin
#
# Why this wrapper exists (docs/build-learnings.md 2026-08-21): every codex
# surface on this Mac — the Homebrew CLI, ChatGPT.app's embedded codex, Codex
# Computer Use — shares ~/.codex by default. When the app rewrites
# models_cache.json in a schema the CLI cannot parse, the CLI's periodic cache-TTL
# renewal fails and long runs die MID-FLIGHT, at random-looking durations, with no
# obvious cause (four reviews were lost this way). Isolating CODEX_HOME per project
# ends that class of failure and keeps concurrent reviews in different repos from
# sharing session/cache state at all.
#
# The run is also DETACHED (setsid) so a supervising agent's task cleanup cannot
# reap it, and stdin is closed (the other way backgrounded codex dies silently —
# it blocks on "Reading additional input from stdin..." before creating a session).
#
# .codex-home/ holds a COPY of ~/.codex/auth.json and is gitignored. Re-seed it
# after re-authenticating: scripts/codex-review.sh --seed

set -euo pipefail
cd "$(dirname "$0")/.."

HOME_DIR="$PWD/.codex-home"
MODEL=${CODEX_REVIEW_MODEL:-gpt-5.6-sol}
EFFORT=${CODEX_REVIEW_EFFORT:-ultra}

seed() {
  mkdir -p "$HOME_DIR"
  for f in auth.json config.toml; do
    [ -f "$HOME/.codex/$f" ] || { echo "missing ~/.codex/$f — run 'codex login' first" >&2; exit 1; }
    cp "$HOME/.codex/$f" "$HOME_DIR/$f"
  done
  chmod 700 "$HOME_DIR"
  echo "seeded $HOME_DIR from ~/.codex (auth.json + config.toml)"
}

if [ "${1:-}" = "--seed" ]; then seed; exit 0; fi
[ $# -ge 1 ] || { echo "usage: scripts/codex-review.sh <prompt-file|-> [output-file]" >&2; exit 2; }

# The home must EXIST before codex starts: it refuses to create CODEX_HOME itself.
[ -f "$HOME_DIR/auth.json" ] || seed

PROMPT_SRC=$1
OUT=${2:-outputs/codex-review-$(date +%Y%m%d-%H%M%S).md}
mkdir -p "$(dirname "$OUT")"

if [ "$PROMPT_SRC" = "-" ]; then
  PROMPT=$(cat)
else
  [ -f "$PROMPT_SRC" ] || { echo "prompt file not found: $PROMPT_SRC" >&2; exit 2; }
  PROMPT=$(cat "$PROMPT_SRC")
fi

echo "== codex review: model=$MODEL effort=$EFFORT"
echo "   CODEX_HOME=$HOME_DIR (project-local)"
echo "   output=$OUT"

CODEX_HOME="$HOME_DIR" setsid codex exec \
  --sandbox read-only \
  -c "model=\"$MODEL\"" \
  -c "model_reasoning_effort=\"$EFFORT\"" \
  "$PROMPT" </dev/null >"$OUT" 2>&1 &

PID=$!
echo "   detached pid=$PID — tail -f $OUT ; kill $PID to stop"
