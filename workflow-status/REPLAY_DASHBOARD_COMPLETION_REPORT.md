# REPLAY DASHBOARD COMPLETION REPORT

**Project**: DevPilot — Enterprise Run Audit & Replay Dashboard
**Date**: August 14, 2026 (Session 47)
**Scope**: Turn the existing Phase-21 Replay subsystem into a production-quality
dashboard experience on the run-detail page. No redesign of the Replay
subsystem; no Phase 21 extension beyond the dashboard + CLI exit codes.

---

## 1. Implementation summary

The run-detail page (`/dashboard/runs/[id]`) now hosts a **Replay & Audit**
section that consumes only real APIs. An engineer can open any completed run
and answer, from recorded evidence alone (never another LLM call):

1. What happened? → manifest summary (status, fingerprint, stages, last replay)
2. What evidence was recorded? → manifest + repository fingerprint + stage records
3. Can it be reproduced? → EXACT / DETERMINISTIC replay buttons
4. Was the replay identical? → verdict banner (MATCH / DRIFT / INVALID / INCOMPLETE)
5/6. Where and why did it differ? → Difference Viewer + audit evidence
7. Which checks support it? → expandable deterministic checks in the audit

## 2. Architecture

```
runs/[id]/page.tsx
   └── ReplaySection (orchestrator)
         ├── manifest + history fetch (replayApi, Promise.all)
         ├── verdict banner + manifest/status cards
         ├── start-replay phase machine (useReducer)
         │     EXACT · DETERMINISTIC · COMPARE (compare-run picker)
         ├── AuditReport        (audit() payload, enterprise banner)
         ├── ReplayTimeline     (stage comparisons + classification)
         ├── DifferenceViewer   (bounded categorized differences)
         └── ReplayHistory      (paginated past replays)
   └── liveRunStatus (run WS) ──→ terminal transition refreshes manifest/history
```

- **Live updates**: replay execution is synchronous on the backend (the POST
  returns the completed result), so the phase machine is local
  (idle → starting → running → completed/failed). No new WebSocket system.
  The run's existing WebSocket pushes a terminal status; the section then
  refreshes manifest + history so a freshly captured manifest appears
  automatically. The run page's existing polling fallback covers disconnected
  mode (WebSocket → live update, fallback → API refresh).
- **Pure logic** (`frontend/src/lib/replay/replayModel.ts`): verdict tones,
  check/stage-kind tones, difference categorization (check → category/severity),
  stage views, start-replay reducer — all DOM-free and vitest-covered.
- **API client** (`frontend/src/lib/api/client.ts`): typed `replayApi`
  (manifest / execute / compare / audit / history) + replay types mirroring
  the backend models.
- **CLI** (`backend/app/cli_replay.py` + `cli.py`): `replay` and
  `replay-compare` now return CI exit codes — 0 = MATCH, 1 = DRIFT/INCOMPLETE,
  2 = INVALID (Windows-safe UTF-8 output preserved).

## 3. Files created

| File | Purpose |
|---|---|
| `frontend/src/components/replay/ReplaySection.tsx` | Run-detail Replay & Audit section (orchestrator) |
| `frontend/src/components/replay/ReplayTimeline.tsx` | Stage-by-stage replay timeline |
| `frontend/src/components/replay/DifferenceViewer.tsx` | Bounded difference viewer |
| `frontend/src/components/replay/AuditReport.tsx` | Enterprise audit report |
| `frontend/src/components/replay/ReplayHistory.tsx` | Paginated replay history |
| `frontend/src/lib/replay/replayModel.ts` | Pure replay presentation logic |
| `frontend/src/lib/replay/replayModel.test.ts` | 20 unit tests (node env) |
| `docs/RUN_AUDIT_AND_REPLAY.md` | Design doc: lifecycle, verdicts, evidence, security |
| `workflow-status/REPLAY_DASHBOARD_COMPLETION_REPORT.md` | This report |

## 4. Files modified

| File | Change |
|---|---|
| `frontend/src/lib/api/client.ts` | Replay types + `replayApi` client |
| `frontend/src/lib/api/client.test.ts` | +6 `replayApi` contract tests |
| `frontend/src/app/dashboard/runs/[id]/page.tsx` | Mounts `<ReplaySection>` with live run status |
| `backend/app/cli_replay.py` | CI exit codes for `replay` / `replay-compare` |
| `backend/app/cli.py` | Propagates replay exit codes via `sys.exit` |
| `backend/tests/test_replay.py` | +3 CLI tests (exit-code helper, match=0, invalid=2) |
| `README.md` | Solution table row + test table entry |
| `docs/ARCHITECTURE.md` | §Run Replay & Audit + phase table |
| `workflow-status/PROJECT_STATE.md` | Session 47 log |

## 5. Frontend components (detail)

- **ReplaySection** — manifest status / repository fingerprint / stages
  recorded / last replay cards; verdict banner; EXACT / DETERMINISTIC /
  COMPARE buttons (compare picker loads the newest 50 runs via `runsApi`),
  phase machine with spinner + failure banner + dismiss; Refresh + Audit
  Report actions; error + retry; manifest fingerprint + original run status.
- **ReplayTimeline** — per-stage MATCH/DRIFT/REPLAYED/RECORDED status,
  deterministic/LLM-proposed/observational chips, recorded vs replay
  fingerprints. No chain-of-thought.
- **DifferenceViewer** — bounded to 50; category (repository/config/artifact/
  stage-input/stage-output/test-result/review/quality-gate/decision drift +
  missing evidence), severity (HIGH/MEDIUM/LOW), original vs replay,
  deterministic evidence (expected/actual/note), dedup across checks +
  comparisons.
- **AuditReport** — AUDIT RESULT / DRIFT DETECTED banner with verdict +
  counts, primary difference + supporting evidence, run/repository identity,
  stage summary chips, expandable check list.
- **ReplayHistory** — replay id, mode, verdict badge, checks, timestamp,
  Previous/Next pagination (page size 10), retry on error, empty state.

## 6. API changes

None required — all replay endpoints already existed and are bounded.
Consumed as-is:
`GET /runs/{id}/replay/manifest`, `POST /runs/{id}/replay` (mode, workspace,
other_run_id), `GET /runs/{id}/replay/compare/{other}`,
`GET /runs/{id}/replay/audit`, `GET /runs/{id}/replay?limit&offset`.

## 7. WebSocket behavior

- **No new WebSocket infrastructure** (explicitly avoided per requirements).
- Reuse: the run-detail page's existing `useRunWebSocket` pushes run updates.
  When the live run status transitions to a terminal state
  (approved/rejected/needs_human_review/failed/cancelled), ReplaySection
  refreshes manifest + history so a captured manifest appears automatically.
- Fallback: when the WebSocket is disconnected, the run page's existing
  5s polling continues; the replay section also refreshes on user action and
  after each replay.

## 8. Replay workflow

1. Open a completed run → manifest loads (status, fingerprint, stages).
2. Click **EXACT** → POST returns MATCH (recorded evidence reproduces the
   recorded decisions); verdict banner + timeline + history update.
3. Click **DETERMINISTIC** → EXACT + live workspace fingerprint / application
   outcome / test re-execution → MATCH on an unchanged workspace, DRIFT on a
   tampered one.
4. Click **COMPARE** after picking another run → per-stage comparisons.

## 9. Audit workflow

1. Click **Audit Report** → `GET /runs/{id}/replay/audit` (manifest + EXACT
   replay, zero LLM calls).
2. Enterprise banner: AUDIT RESULT (MATCH) or DRIFT DETECTED with counts and
   primary difference + supporting evidence.
3. Expand **Deterministic Checks** to see each check's expected vs actual.
4. Note the semantics: the audit is EXACT-based (record consistency); a live
   workspace DRIFT is a DETERMINISTIC-mode finding shown in the replay
   history + difference viewer. Both are surfaced.

## 10. Tests (actual counts)

**Frontend — vitest: 93 passed (9 files)**
- Baseline 67 + **26 new**:
  - `replayModel.test.ts`: **20** — verdict tones (4 distinct), check/stage
    tones, formatting (durations, timestamps, modes), difference categories,
    severity mapping, difference building (failed checks, missing evidence,
    stage comparisons, dedup, 50-bound), stage views, run-state machine
    (start/complete/fail/reset).
  - `client.test.ts` **+6** — replayApi contract tests (manifest GET, execute
    POST mode+workspace, compare other_run_id forwarding, COMPARE GET, audit
    GET, history pagination).

**Backend — 1889 passed / 17 skipped / 1 pre-existing env failure**
(unchanged baseline; the 1 failure is the documented
`test_wrapper_skips_cleanly_without_provider` env quirk). `test_replay.py`
now **34 tests** (+3 CLI exit-code tests). 54 integration tests green against
live PostgreSQL (earlier session run).

**Production build** — `next build` EXIT=0 (18 routes, run detail = dynamic).

**Typecheck** — Next.js build type gate passed (fixed 2 null-narrowing
errors during verification).

**Lint** — `next lint` prompts for ESLint setup; ESLint is not configured in
this project (pre-existing). The build's integrated lint/type check passes.

**Live PostgreSQL UI-facing API E2E — PASS** (test DB, real runs):
1. Manifest endpoint → exists, 11 stages, fingerprint ✅
2. EXACT replay → MATCH (8 checks) ✅
3. DETERMINISTIC replay → MATCH (11 checks) ✅
4. Tampered workspace → DRIFT (1 failed check) ✅
5. Audit endpoint → available, 8 checks, 0 divergences ✅
6. History → 4 persisted entries (exact match, deterministic drift,
   deterministic match, exact match) ✅
7. Invalid mode → clean `success: false` error ✅
8. COMPARE → DRIFT with 11 stage comparisons ✅
All probe rows cleaned up.

## 11. Security review

- Replay never calls an LLM; the UI never renders chain-of-thought.
- Bounded responses end-to-end (hashes truncated, checks ≤ 100, comparisons
  ≤ 64, divergences capped, error strings `[:300]`).
- No API keys / provider credentials / raw exceptions reach the dashboard.
- Repository scope respected (DETERMINISTIC verifies the recorded/given
  workspace only).
- COMPARE picker loads only run ids + titles via the existing list API
  (no repository contents).
- No new auth surface; the section inherits the run-detail page's existing
  authorization posture.

## 12. Demonstrations

| # | Scenario | Result |
|---|---|---|
| A | Open completed run | Replay & Audit section renders on run detail ✅ |
| B | View replay manifest | Manifest status, fingerprint, 11 stages, captured timestamp ✅ |
| C | Start EXACT replay | Phase machine → completed ✅ |
| D | Receive MATCH result | Verdict banner MATCH, 8/8 checks ✅ |
| E | Start DETERMINISTIC replay | Verdict MATCH, 11 checks (fingerprint/application/testing) ✅ |
| F | Display deterministic checks | Timeline + audit check list with expected/actual ✅ |
| G | Tamper → DRIFT | Tampered workspace → DRIFT, 1 failed check ✅ |
| H | Difference viewer identifies drift | `repository_fingerprint` → repository drift, HIGH, expected vs actual ✅ |
| I | Audit explains with evidence | Audit banner DRIFT-scoped + supporting evidence ✅ |
| J | Restart/reload preserves history | History persisted to `replay_runs` in PG; reloaded via list API ✅ |

## 13. Known limitations

- The replay history API does not return a total count; pagination infers
  "has more" from a full page (page size 10).
- COMPARE picker lists the newest 50 runs (bounded); very old runs require
  the CLI `replay-compare <run> <other>`.
- The audit verdict is EXACT-based by design; live-workspace drift is a
  DETERMINISTIC-mode finding. The UI shows both contexts explicitly.
- ESLint is not configured in the repo (pre-existing); the Next build's
  type/lint gate is the project's gate.

---

## Final status

```
REPLAY DASHBOARD:        PASS
EXACT REPLAY UI:         PASS
DETERMINISTIC REPLAY UI: PASS
DRIFT VIEWER:            PASS
AUDIT REPORT:            PASS
REPLAY HISTORY:          PASS
LIVE UPDATES:            PASS
SECURITY:                PASS
FRONTEND BUILD:          PASS
REGRESSION:              PASS
TASK COMPLETE:           YES
PHASE 21 READY:          YES
```

Phase 21 is COMPLETE. **STOP** — do not start Phase 21 beyond this scope;
do not implement unrelated features.
