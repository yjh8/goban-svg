# Starter prompt — goban-svg: paused; one message waiting to be sent

Paste this into a fresh session in `~/GitHub/goban-svg`.

## Where the project stands

**The project is intentionally PAUSED.** goban-svg was a Claude Code capability
demo; v0.1.1 is live at https://goban-svg.pages.dev and Joseph stopped here on
2026-08-22 for token budget. Nothing is broken, nothing is half-finished in the
code. Do **not** start new work unless Joseph asks.

The last session closed **carry #2** (notify the go-blindspot author) on the
engineering side and shipped `5dd21a1` — docs-only. What triaging it turned up
became **D-009**, and it matters more than the carry did:

**go-blindspot / 序盤盲區庫 is a SOURCE FORK, not a wheel consumer.** 夏大銘
(夏老師) at 海峰棋院 built the first, non-functional version; Joseph built the
working engine and let him **adopt the source** on Thursday 2026-08-20 — the last
day of their class, engine at commit `07a1ccb`, before `photo.py` landed at 20:45
that evening. 夏老師 has modified his copy since, and built his first version with
Claude.

Two consequences that will bite a session that reads only the integration spec:

- **"Upgrade" for him is a cherry-pick into a diverged tree**, never the
  `URL + hash + EXPECTED_VERSION` swap `docs/integration-prompt-for-blindspot.md`
  describes. That spec models a runtime-Pyodide integration he does not use.
- **The published sha256 is not enforced anywhere in code** (`worker.js` asserts
  `goban_svg.__version__`, not bytes), so the "stale hash" never had a runtime
  failure mode. It was a record correction, not an incident.

## The one thing actually waiting

**Joseph owns sending a message to 夏老師. It has not been sent.**

- Cover note (zh-TW): `outputs/blindspot-notification-2026-08-22.md` —
  **gitignored, exists only on joseph-macmini.** Also preserved in the session
  artifact on claude.ai. If you are on another machine, it is not in your clone.
- Merge spec for his AI: `docs/upgrade-prompt-for-blindspot.md` (tracked, 505
  lines) — hard MUST-NOT rules, exact FIND/REPLACE hunks, self-contained
  verification, troubleshooting table.

It offers him three silent-failure bug fixes (duplicate JSON key discards stones;
lone UTF-16 surrogate corrupts the SVG; phone JPEGs load sideways because EXIF
orientation is ignored) plus two opt-in features. Do not re-derive any of it — it
is verified. If Joseph wants tone or length changed, edit the note; the technical
content is settled.

## Key docs

- `docs/decisions.md` — **D-009** (fork semantics, and how the two `0.1.0` builds
  were identified); D-008 for wheel immutability
- `docs/build-learnings.md` — **2026-08-22: "A spec written for an AI must be
  trialled BY an AI."** Read this before writing any AI-executable spec. Blind
  execution exposed a blocker in a spec that looked careful.
- `session_logs/2026-08-22-session-b.md` — the full account
- `docs/tasks.md` — In Progress item 3 is the unsent outreach

## Still open (unchanged, all lower priority than "do nothing")

1. **Physical-board photo corpus → H1–H3 calibration.** Still blocked: nothing had
   arrived as of 2026-08-22. This was the *previous* handoff's headline mission and
   remains the natural next real work when Joseph resumes.
2. Staff verification of the correction editor — nobody outside this machine has
   used it.
3. Phase-2 auth (Cloudflare Access + Google IdP), gated on staff confirming phase 1.
4. Backlog: warning i18n via stable codes; photo-mode perf (~35 s under Pyodide);
   the deferred `APPROVE-0` re-verification on the v0.1.0 CLI diff.

## Gotchas

- **Codex reviews** run via `scripts/codex-review.sh`; floor is **xhigh**. Docs-only
  edits are exempt from the gate.
- **`git add -A` is unsafe while background agents write the tree** — stage explicit
  paths.
- **Cloudflare Pages returns 200 + index.html for missing paths** — never trust a
  status code for existence; check content.
- The two `0.1.0` builds: build 1 `5964bad5…` (65,760 B, `07a1ccb`), build 2
  `02b158f7…` (78,112 B, `c4615de`). Build 1's wheel bytes are gone, but both source
  generations are in git — so the delta **is** knowable. Don't repeat the earlier
  claim that it isn't.

## First action

Start by running `session-init`. Then, unless Joseph says otherwise, **confirm the
pause and stop** — the only live item is a message he sends himself.
