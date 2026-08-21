# CLAUDE.md changelog archive — entries for 2026-08-19 (rolled 2026-08-21)

### 2026-08-19 — v0.1.0 genesis → shipped (Claude Code/Fable 5, joseph-macmini)

**Commits:** b894722 (bootstrap) · 12eb6bf (screenshots + accidental early code) ·
07a1ccb (v0.1.0, review-hardened) · 22726b9 (docs format) — all pushed, CI green.
**Decisions:** D-002 wedge redesign · D-003 real-image fixtures · D-004 web app
planned, Google auth deferred (docs/decisions.md created).
**Docs synced:** design.md (+amendments) · interfaces.md · build-learnings.md ·
TESTING.md, tasks.md, decisions.md (new) · session_logs/2026-08-19-session.md.
**Learnings:** unmeasured thresholds are hypotheses · git add -A vs background
agents · ruff formats md code blocks.
**Files:** full package + 427 tests + examples/ gallery (~8k lines).
**Open:** staff verification round in progress (link shared to 海峰棋院 team);
web phase 2 = Cloudflare Access + Google IdP after they confirm; deferred: clean
APPROVE-0 re-verification round on the CLI diff.

### 2026-08-19b — web app phase 1 shipped to goban-svg.pages.dev (same session, evening)

**Decisions:** D-005 (static Pages + client-side Pyodide 314.0.5 self-hosted,
zh-TW 圍棋 UI, in-page JSON correction loop, strict CSP).
**Reviews:** Codex design review APPROVE-10 (all folded in pre-build, incl. the
correction-loop BLOCKER) + Codex web-code review APPROVE-10 (all 10 MAJOR fixed
same evening: job/selection races, worker crash recovery, preview-URL leak,
assign_to single-copy bridge, JSON caps, sha256-pinned pyodide cache, checked
staging + committed smoke script). E2E-verified live twice (board-1 pre-fix,
board-2 post-fix); negative path + rerender verified in Chrome.
**Files:** web/ (index/app/worker/style/_headers), scripts/deploy-web.sh,
scripts/smoke-web.sh, docs/webapp-design.md (+amendments), tasks/TESTING/README.
