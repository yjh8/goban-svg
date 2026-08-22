# CLAUDE.md changelog archive — 2026-08-21

> Rolled out of CLAUDE.md on 2026-08-22 (keep-window: today only).

### 2026-08-21 — staff feedback round 1 → correction editor + photo checkpoint shipped as 0.1.1 (D-007/D-008)

**Morning:** c4615de (fail-closed refine_corners + photo-1 fixture) · 1308c40
(corner-picker fallback, D-006) — deployed.
**Evening — feedback round 1 processed:** two staff first-try failures (both
MONITOR photos) triaged → calibration finding #2 (measured: whites ΔL +8..+16 vs
floor 20, 9 silent deaths; labeled whites flip BLACK at −134; H1–H3 hypotheses
parked for the physical corpus Joseph is collecting) + `examples/board-4` (the
same program's true screenshot extracts perfectly — 4th D-003 fixture).
**Shipped (D-007):** click-to-cycle correction editor (blindspot-pattern, verified
live on their deployment) + staged photo extraction with a rectified-grid
checkpoint + corner-placement guidance, as **0.1.1** with additive
`ExtractionResult.uncertain` + `PhotoArtifact` APIs. Design took TWO Codex ultra
REJECT rounds (25 findings → v3 contract); build = 5-agent workflow + 2 verify
lenses (14 findings fixed); code review r5 REJECT 0-BLOCKER (7 MAJOR + 5 MINOR,
all fixed); Chrome E2E passed twice (pre- and post-fix). 486 tests.
**D-008:** published wheel URLs immutable — tracked `web/wheels/` archive +
manifest; deploy verifies the LIVE site pre-publish (discovered the 0.1.0 pin in
the blindspot prompt was already stale — Joseph to notify the author).
**Infra lesson:** ChatGPT.app's codex rewrites `~/.codex/models_cache.json` →
CLI runs crash mid-flight; fix = isolated `CODEX_HOME` (memory + build-learnings).
**Open:** staff verification of the new editor; physical-board photo corpus →
H1–H3 calibration; phase-2 auth.
