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
# CREDENTIAL HANDLING (code reviews r7/r8/r9). Two layers:
#
#   1. No second copy of the token exists: $CODEX_HOME/auth.json is a SYMLINK to
#      ~/.codex/auth.json, so `codex login` propagates everywhere at once.
#   2. A PERMISSION PROFILE denies the filesystem root and re-grants the repo,
#      the toolchain, and ~/.gitconfig. `~/.ssh`, `~/.aws`, and the original
#      `~/.codex/auth.json` are "Operation not permitted" to anything the model
#      runs — verified by probe, not assumed. This REPLACES `--sandbox
#      read-only`, which bypasses default_permissions and left the whole home
#      readable. It is NOT a complete jail: see the temp-directory residual
#      below, and treat reviewed repos and prompts as trusted input.
#
# The profile is default-DENY: a new toolchain path may need adding here (the
# symptom is a review reporting a blocked command), which is the correct
# direction to fail. Authentication still works because the parent codex process
# reads auth.json before the profile applies to model-launched commands.
# https://developers.openai.com/codex/permissions
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
EFFORT=${CODEX_REVIEW_EFFORT:-xhigh}  # Joseph 2026-08-22: ultra burns far more budget

# Default-deny filesystem, then re-grant exactly what a review needs:
#   :minimal         runtime essentials codex itself requires
#   :workspace_roots the repo under review
#   toolchain        node / python / git / system libraries
#   ~/.gitconfig     or `git log` fails with a bare "permission denied"
# Everything else — notably ~/.ssh, ~/.aws, ~/.codex — stays denied. Refinements
# from r10 B-2/B-3, each probe-verified:
#   - /opt/homebrew/var is denied INSIDE the homebrew grant: it holds service
#     data (postgres clusters, logs, caches), not toolchain. /opt/homebrew/etc
#     stays readable because node loads openssl.cnf from it — config, not data.
#   - KNOWN RESIDUAL: global temp (/tmp, /private/tmp, /var/tmp) stays READABLE.
#     `:minimal` grants it and per-path denies do NOT override that in codex
#     0.144.1 — probed both orderings, neither worked (r11 B-3). So: do not
#     leave secrets in /tmp on a machine that runs reviews. This wrapper no
#     longer contributes to the exposure — its prompt spool moved to a 0700
#     directory under ~/.codex-homes/ (which the profile denies).
#   - ~/.gitconfig uses the literal ~ so a HOME containing a quote or backslash
#     cannot break the TOML.
PERM_FS="permissions.review-readonly.filesystem={\
\":root\"=\"deny\",\":minimal\"=\"read\",\":workspace_roots\"={\".\"=\"read\"},\
\"/opt/homebrew\"=\"read\",\"/opt/homebrew/var\"=\"deny\",\
\"/Library/Developer\"=\"read\",\"/usr\"=\"read\",\
\"/bin\"=\"read\",\"/System\"=\"read\",\
\"~/.gitconfig\"=\"read\"}"

# Codex 0.144.1 defaults to inherit=all with default excludes OFF, so every
# exported *_TOKEN / *_KEY in the launching shell is visible to `env` inside a
# model command (r10 B-1). Inherit only core vars, re-enable the name-based
# excludes, and forbid a login shell from re-sourcing a profile that would put
# them back.
PERM_ENV='shell_environment_policy={inherit="core",ignore_default_excludes=false,exclude=["*TOKEN*","*KEY*","*SECRET*","*PASSWORD*","*CREDENTIAL*","AWS_*","GH_*","GITHUB_*","OPENAI_*","ANTHROPIC_*"]}'

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
  SPOOL=$(mktemp -d "$HOME/.codex-homes/.spool-XXXXXX")
  chmod 700 "$SPOOL"
  PROMPT_FILE="$SPOOL/prompt.txt"
  trap 'rm -rf "$SPOOL"' EXIT
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
  REVIEW_EFFORT="$EFFORT" REVIEW_PROMPT="$PROMPT_FILE" REVIEW_PERM_FS="$PERM_FS" \
  REVIEW_PERM_ENV="$PERM_ENV" \
  python3 - <<'PY'
import os, subprocess, sys, tempfile, time

want = os.environ["REVIEW_OUT"]
if want:
    parent = os.path.dirname(want)
    if parent:
        os.makedirs(parent, exist_ok=True)  # explicit nested paths used to work (r9 B-1)
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
    # NOTE: no --sandbox flag — it bypasses default_permissions (r9).
    ["codex", "exec", "--ignore-user-config",
     "-c", f'model="{os.environ["REVIEW_MODEL"]}"',
     "-c", f'model_reasoning_effort="{os.environ["REVIEW_EFFORT"]}"',
     "-c", 'default_permissions="review-readonly"',
     "-c", os.environ["REVIEW_PERM_FS"],
     "-c", os.environ["REVIEW_PERM_ENV"],
     "-c", "permissions.review-readonly.network.enabled=false",
     "--strict-config",  # a malformed -c must fail loudly, not silently degrade
     "-"],
    stdout=out, stderr=subprocess.STDOUT, stdin=prompt,
    start_new_session=True,
)
print(p.pid, path)
PY
)
echo "   output=$OUT"
echo "   detached pid=$PID — tail -f $OUT ; kill $PID to stop"
