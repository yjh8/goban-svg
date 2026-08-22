#!/usr/bin/env bash
# Run a Codex ultra review with PER-PROJECT, OUT-OF-TREE Codex state.
#
#   scripts/codex-review.sh <prompt-file> [output-file]
#   scripts/codex-review.sh - [output-file]      # prompt on stdin
#   scripts/codex-review.sh --seed               # (re)create this project's home
#
# WHY THE STATE IS SEPARATED (docs/build-learnings.md 2026-08-21): every codex
# surface on this Mac — the Homebrew CLI, ChatGPT.app's embedded codex, Codex
# Computer Use — shares ~/.codex by default. When the app rewrites
# models_cache.json in a schema the CLI cannot parse, the CLI's periodic
# cache-TTL renewal fails and long runs die MID-FLIGHT at random-looking
# durations with no obvious cause (four reviews were lost this way). What has to
# be isolated is that MUTABLE cache/session state — not the credential.
#
# CREDENTIAL HANDLING (code review r7/r8). auth.json here is a SYMLINK to
# ~/.codex/auth.json: the wrapper creates no second copy of an OAuth token, and
# re-authenticating propagates automatically. Note the residual risk it CANNOT
# remove: `codex exec --sandbox read-only` runs as your UID and can read any
# file you can — ~/.codex/auth.json, ~/.ssh, ~/.aws — whether or not this
# wrapper exists. Isolation and --ignore-user-config shrink what a review sees
# (no personal MCP servers, plugins, or other projects' sessions); they do not
# sandbox the filesystem. Treat every reviewed repo and prompt as trusted input.
#
# The run is DETACHED (python3 start_new_session; macOS has no setsid) so a
# supervising agent's task cleanup cannot reap it, and the prompt arrives on
# STDIN via `codex exec -` — dodging ARG_MAX on large prompts and giving stdin
# the EOF that keeps codex from blocking on "Reading additional input...".

set -euo pipefail
cd "$(dirname "$0")/.."

# Identity = directory name + a hash of the canonical path, so two unrelated
# repos with the same basename never share a home (r8 B-1).
REPO_ROOT=$(pwd -P)
PROJECT="$(basename "$REPO_ROOT")-$(printf '%s' "$REPO_ROOT" | shasum -a 256 | cut -c1-8)"
HOME_DIR="$HOME/.codex-homes/$PROJECT"
MODEL=${CODEX_REVIEW_MODEL:-gpt-5.6-sol}
EFFORT=${CODEX_REVIEW_EFFORT:-ultra}

seed() {
  [ -f "$HOME/.codex/auth.json" ] || { echo "missing ~/.codex/auth.json — run 'codex login' first" >&2; exit 1; }
  mkdir -p "$HOME/.codex-homes" "$HOME_DIR"
  chmod 700 "$HOME/.codex-homes" "$HOME_DIR"
  # A symlink, never a copy: one credential on disk, and `codex login` updates
  # every project home at once.
  ln -sfn "$HOME/.codex/auth.json" "$HOME_DIR/auth.json"
  # config.toml is deliberately absent — reviews run under --ignore-user-config
  # so personal MCP servers and plugins never load into a review session.
  echo "seeded $HOME_DIR (auth.json -> ~/.codex/auth.json)"
}

if [ "${1:-}" = "--seed" ]; then seed; exit 0; fi
[ $# -ge 1 ] || { echo "usage: scripts/codex-review.sh <prompt-file|-> [output-file]" >&2; exit 2; }

# codex refuses to create CODEX_HOME itself, so it must exist before launch.
[ -e "$HOME_DIR/auth.json" ] || seed

PROMPT_SRC=$1
OUT_ARG=${2:-}

if [ "$PROMPT_SRC" = "-" ]; then
  PROMPT_FILE=$(mktemp "${TMPDIR:-/tmp}/codex-prompt-XXXXXX")
  trap 'rm -f "$PROMPT_FILE"' EXIT
  cat >"$PROMPT_FILE"
else
  [ -f "$PROMPT_SRC" ] || { echo "prompt file not found: $PROMPT_SRC" >&2; exit 2; }
  PROMPT_FILE=$PROMPT_SRC
fi
mkdir -p outputs

echo "== codex review: model=$MODEL effort=$EFFORT"
echo "   CODEX_HOME=$HOME_DIR (per-project, outside every repo)"

# The launcher OPENS the output exclusively (O_CREAT|O_EXCL) and hands codex the
# descriptor: no check-then-open race, no truncation of a concurrent run's file,
# and no dangling-symlink evasion (r8 B-2/B-3). Darwin's mktemp only randomizes
# TRAILING Xs, which is why this is Python's mkstemp and not a shell template.
read -r PID OUT < <(
  CODEX_HOME="$HOME_DIR" REVIEW_OUT="$OUT_ARG" REVIEW_MODEL="$MODEL" \
  REVIEW_EFFORT="$EFFORT" REVIEW_PROMPT="$PROMPT_FILE" \
  python3 - <<'PY'
import os, subprocess, sys, tempfile, time

want = os.environ["REVIEW_OUT"]
if want:
    try:
        fd = os.open(want, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        sys.exit(f"refusing to overwrite existing {want} — choose another name")
    path = want
else:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    fd, path = tempfile.mkstemp(prefix=f"codex-review-{stamp}-", suffix=".md", dir="outputs")

out = os.fdopen(fd, "wb")
prompt = open(os.environ["REVIEW_PROMPT"], "rb")  # stdin: no argv size ceiling, clean EOF
p = subprocess.Popen(
    ["codex", "exec", "--sandbox", "read-only", "--ignore-user-config",
     "-c", f'model="{os.environ["REVIEW_MODEL"]}"',
     "-c", f'model_reasoning_effort="{os.environ["REVIEW_EFFORT"]}"',
     "-"],
    stdout=out, stderr=subprocess.STDOUT, stdin=prompt,
    start_new_session=True,
)
print(p.pid, path)
PY
)
echo "   output=$OUT"
echo "   detached pid=$PID — tail -f $OUT ; kill $PID to stop"
