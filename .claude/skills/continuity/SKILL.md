---
name: continuity
description: Show the current continuity brief — environment (cloud vs local), latest session handoff, and whether this checkout is behind origin/main. Use at session start, after context compaction, or whenever orientation is lost.
---

Run the continuity hook manually (stdin must be redirected — in hook context the
harness pipes a JSON payload; manually there is none):

```bash
bash "${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/session-start.sh" < /dev/null
```

Relay the brief to the user, then act on it:

- If it reports HEAD behind `origin/main`: sync before writing docs or handoffs
  (local: pull/merge; cloud: merge `origin/main` into the working branch).
- In cloud (`CLAUDE_CODE_REMOTE=true`): follow the write-back rule it prints —
  commit to a `claude/*` branch → push → draft PR, never main — and treat
  machine-local, gitignored assets (the `screenshots/` inbox, `outputs/`,
  `.venv`, `web-dist/`) as absent; never fabricate their contents.
- If the brief shows an in-flight build-state: that is the resume point — read
  it before starting anything new.

Design + verified evidence (cross-repo): `yjh8/supertitle` →
`docs/20260820-cloud-local-continuity-design.md`. A cloud session is a third
machine — fresh clone, branch+PR write-back, push-before-switching.
