# CLAUDE.md changelog archive — entries for 2026-08-20 (rolled 2026-08-21)

### 2026-08-20 — photo mode phase 1 (engine + CLI, EXPERIMENTAL) + repo public + integration kit

**Repo went PUBLIC** (Joseph's call — staff AI needed a GitHub link); creds scan clean.
**Integration kit** for the 序盤盲區庫 team's Sonnet: docs/integration-prompt-for-blindspot.md
(v2, prescriptive) + v1 EN/zh-TW kept as teaching examples; CORS enabled on /wheels/* +
/pyodide/*. **Photo mode phase 1** (staff feedback "handle real photos"): `photo.py`
(assisted 4-corner homography + adaptive local-wood classifier, all thresholds tagged
UNCALIBRATED) + `goban-svg photo` CLI + 32 tests (459 total). Design review APPROVE-13
+ code review 2 rounds (2 BLOCKERs found+fixed: edge-clamp fabrication → validity mask;
axis-norm resolution gate → min singular value) — receipts stamped, all in
docs/photo-mode-design.md. **Phase 2 (corner-picker UI) BLOCKED on real staff photos**
(the calibration corpus; D-003). Deployed site unchanged today except CORS headers.
**Evening: calibration finding #1** — Joseph's monitor photo extracted PERFECTLY once
corners were right; thresholds blameless; shipped `refine_corners` iterative
auto-refinement (default on, FAIL-CLOSED per ultra review) + `examples/photo-1`
fixture with rough corners + `--no-refine` escape hatch. 471 tests. Gate now ultra/gpt-5.6-sol (fleet rule 2026-08-20); backgrounded codex needs < /dev/null (learned the hard way). Still needed: true physical-board photos (chroma constants unproven).

