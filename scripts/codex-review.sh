#!/usr/bin/env bash
# Run a Codex ultra review against goban-svg with PROJECT-SEPARATED state.
#
#   scripts/codex-review.sh <prompt-file> [output-file]
#   scripts/codex-review.sh - [output-file]      # prompt on stdin
#   scripts/codex-review.sh --seed               # re-copy credentials after re-auth
#
# WHY THE STATE IS SEPARATED (docs/build-learnings.md 2026-08-21): every codex
# surface on this Mac — the Homebrew CLI, ChatGPT.app's embedded codex, Codex
# Computer Use — shares ~/.codex by default. When the app rewrites
# models_cache.json in a schema the CLI cannot parse, the CLI's periodic
# cache-TTL renewal fails and long runs die MID-FLIGHT at random-looking
# durations with no obvious cause (four reviews were lost this way).
#
# WHY IT LIVES OUTSIDE THE REPO (code review r7 BLOCKER): the home holds a copy
# of auth.json. Anything inside the repo is inside the review boundary — the
# agent reads this tree, so OAuth material could surface in a transcript, and a
# hostile instruction in a reviewed file could ask for it directly. Per-project
# separation is preserved; only the LOCATION moved to ~/.codex-homes/<project>/,
# outside every workspace. Reviews also run with --ignore-user-config so no
# personal MCP servers / plugins load into a review.
#
# The run is DETACHED (python3 start_new_session; macOS has no setsid) so a
# supervising agent's task cleanup cannot reap it, and the prompt is fed on
# STDIN via `codex exec -` — which both dodges the ARG_MAX ceiling on large
# prompts and gives stdin a clean EOF (an argument-less codex exec blocks
# forever on "Reading additional input from stdin...").

set -euo pipefail
cd "$(dirname "$0")/.."

PROJECT=$(basename "$PWD")
HOME_DIR="$HOME/.codex-homes/$PROJECT"
MODEL=${CODEX_REVIEW_MODEL:-gpt-5.6-sol}
EFFORT=${CODEX_REVIEW_EFFORT:-ultra}

seed() {
  mkdir -p "$HOME/.codex-homes"
  chmod 700 "$HOME/.codex-homes"
  mkdir -p "$HOME_DIR"
  chmod 700 "$HOME_DIR"
  [ -f "$HOME/.codex/auth.json" ] || { echo "missing ~/.codex/auth.json — run 'codex login' first" >&2; exit 1; }
  cp "$HOME/.codex/auth.json" "$HOME_DIR/auth.json"
  chmod 600 "$HOME_DIR/auth.json"
  # config.toml is deliberately NOT copied: --ignore-user-config means reviews
  # run on explicit flags only, so personal MCP/plugin config never loads.
  echo "seeded $HOME_DIR (auth.json only, 0600)"
}

if [ "${1:-}" = "--seed" ]; then seed; exit 0; fi
[ $# -ge 1 ] || { echo "usage: scripts/codex-review.sh <prompt-file|-> [output-file]" >&2; exit 2; }

# codex refuses to create CODEX_HOME itself, so it must exist before launch.
[ -f "$HOME_DIR/auth.json" ] || seed

PROMPT_SRC=$1
if [ -n "${2:-}" ]; then
  OUT=$2
  [ -e "$OUT" ] && { echo "refusing to overwrite existing $OUT — choose another name" >&2; exit 1; }
else
  mkdir -p outputs
  # Second-resolution names collide when two reviews start in the same second
  # (r7 m6): mktemp creates exclusively.
  OUT=$(mktemp "outputs/codex-review-$(date +%Y%m%d-%H%M%S)-XXXXXX.md")
fi
mkdir -p "$(dirname "$OUT")"

if [ "$PROMPT_SRC" = "-" ]; then
  PROMPT_FILE=$(mktemp "${TMPDIR:-/tmp}/codex-prompt-XXXXXX")
  trap 'rm -f "$PROMPT_FILE"' EXIT
  cat >"$PROMPT_FILE"
else
  [ -f "$PROMPT_SRC" ] || { echo "prompt file not found: $PROMPT_SRC" >&2; exit 2; }
  PROMPT_FILE=$PROMPT_SRC
fi

echo "== codex review: model=$MODEL effort=$EFFORT"
echo "   CODEX_HOME=$HOME_DIR (per-project, outside the repo)"
echo "   prompt=$PROMPT_FILE  output=$OUT"

PID=$(CODEX_HOME="$HOME_DIR" REVIEW_OUT="$OUT" REVIEW_MODEL="$MODEL" \
      REVIEW_EFFORT="$EFFORT" REVIEW_PROMPT="$PROMPT_FILE" \
  python3 - <<'PY'
import os, subprocess
out = open(os.environ["REVIEW_OUT"], "wb")
# The prompt goes in on STDIN (`codex exec -`): no argv size ceiling, and the
# file's EOF is what stops codex waiting for more input.
prompt = open(os.environ["REVIEW_PROMPT"], "rb")
p = subprocess.Popen(
    ["codex", "exec", "--sandbox", "read-only", "--ignore-user-config",
     "-c", f'model="{os.environ["REVIEW_MODEL"]}"',
     "-c", f'model_reasoning_effort="{os.environ["REVIEW_EFFORT"]}"',
     "-"],
    stdout=out, stderr=subprocess.STDOUT, stdin=prompt,
    start_new_session=True,
)
print(p.pid)
PY
)
echo "   detached pid=$PID — tail -f $OUT ; kill $PID to stop"
