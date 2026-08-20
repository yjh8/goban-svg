#!/bin/bash
# Continuity session-start hook — fires at session startup, resume, /clear, and
# after context compaction (the hook payload's "source" field says which).
# Its stdout is injected into Claude's context, so this is the mechanism that
# makes handoff state survive compaction and travel between local machines and
# Claude Code on the web (cloud containers): everything it prints comes from
# git-tracked files that exist identically in every clone.
#
# Manual invocation (the /continuity repo skill does this):
#   bash .claude/hooks/session-start.sh < /dev/null
#
# Portable: bash 3.2+ (stock macOS) + git + sed only. Never blocks a session:
# all failures are swallowed and it always exits 0.
set -uo pipefail

payload="$(cat 2>/dev/null || true)"
src="$(printf '%s' "$payload" | sed -n 's/.*"source"[^"]*"\([^"]*\)".*/\1/p')"
root="${CLAUDE_PROJECT_DIR:-$(pwd)}"
cd "$root" 2>/dev/null || exit 0

if [ "${CLAUDE_CODE_REMOTE:-}" = "true" ]; then
  env_kind="CLOUD container — fresh clone of origin's default branch; machine-local assets (anything gitignored: the screenshots/ inbox, outputs/, .venv, web-dist/) are ABSENT here: treat as unavailable, never fabricate their contents. Write-back = commit to a claude/* branch, push, draft PR — never main."
else
  env_kind="LOCAL ($(hostname -s 2>/dev/null || echo unknown)) — full toolchain available. If cloud sessions ran since your last one, check for open claude/* handoff PRs before doc writes."
fi

echo "=== continuity brief (${src:-manual}) ==="
echo "env: $env_kind"
echo "repo: $(git remote get-url origin 2>/dev/null | sed 's/\.git$//;s|.*[:/]\([^/]*/[^/]*\)$|\1|') | branch: $(git rev-parse --abbrev-ref HEAD 2>/dev/null) @ $(git log -1 --format='%h %ad %s' --date=short 2>/dev/null)"

# Latest handoff: files are YYYYMMDD-prefixed, so lexicographic order IS
# chronological. Never use mtime (`ls -t`) — a fresh clone gives every file
# the same checkout mtime.
hd="session_handoff"
if [ -d "$hd" ]; then
  latest="$(ls -1 "$hd" 2>/dev/null | grep '\.md$' | LC_ALL=C sort | tail -1)"
  if [ -n "$latest" ]; then
    echo "latest handoff: $hd/$latest — first lines:"
    sed -n '1,12p' "$hd/$latest" 2>/dev/null | sed 's/^/  | /'
  fi
fi

if [ -f build-state.yaml ]; then
  echo "build-state.yaml present (in-flight multi-PR work) — first lines:"
  sed -n '1,8p' build-state.yaml | sed 's/^/  | /'
fi

# Freshness probe: warn when this checkout is behind origin/main (a cloud clone
# can be minutes stale; a local checkout days stale). Guarded so an offline
# machine or missing `timeout` (stock macOS) never hangs session start.
TO=""
command -v timeout >/dev/null 2>&1 && TO="timeout 6"
if $TO git fetch origin main --quiet 2>/dev/null; then
  behind="$(git rev-list --count HEAD..origin/main 2>/dev/null || echo 0)"
  if [ "${behind:-0}" -gt 0 ] 2>/dev/null; then
    echo "NOTE: HEAD is $behind commit(s) behind origin/main — sync before writing docs/handoffs."
  fi
fi
echo "=== end continuity brief ==="
exit 0
