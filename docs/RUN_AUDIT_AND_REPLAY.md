# Run Audit & Replay

Enterprise Run Replay & Deterministic Reproduction — backend subsystem plus
the dashboard experience on the run-detail page.

Replay answers, **from recorded evidence alone (never another LLM call)**:

1. What exactly happened during the run? → the replay manifest
2. Can the engineering result be reproduced? → EXACT / DETERMINISTIC replay
3. Which stages produced identical results? → per-stage comparisons
4. Where did replay diverge? → diverging stage hashes
5. What deterministic decision caused it? → diverging decision records
6. Can the process be audited without an LLM? → the audit report

**Architecture principle preserved: LLMs PROPOSE, deterministic systems
DECIDE.** Replay never proposes; it re-executes only deterministic systems
(patch validation, quality gate, handoff claim validation, consensus
confidence, contradictions, repository scope, application outcome, tests)
from the recorded inputs and compares each outcome against what was
recorded.

---

## 1. Components

| Layer | Files |
|---|---|
| Models | `backend/app/models/replay.py` — `ReplayManifest`, `ReplayStageRecord`, `ReplayDecisionRecord`, `ReplayCheck`, `ReplayStageComparison`, `ReplayResult`, verdicts/modes |
| Service | `backend/app/services/replay_service.py` — manifest build/capture, EXACT/DETERMINISTIC/COMPARE replay, deterministic re-execution checks, audit report |
| API | `backend/app/api/v1/replay.py` — manifest, execute, compare, audit, history |
| CLI | `backend/app/cli_replay.py` — `replay-manifest`, `replay`, `replay-compare`, `replay-audit`, `replays` (CI exit codes) |
| Persistence | Alembic migration `015` — `replay_manifests`, `replay_runs` |
| Orchestration hook | `OrchestrationService._finalize` captures the manifest at run completion (non-fatal) + `REPLAY_MANIFEST_CAPTURED` event |
| Frontend | `frontend/src/components/replay/*` — `ReplaySection`, `ReplayTimeline`, `DifferenceViewer`, `AuditReport`, `ReplayHistory`; `frontend/src/lib/replay/replayModel.ts` (pure logic); `frontend/src/lib/api/client.ts` `replayApi` |

## 2. Replay lifecycle

```
Original Run
     │
     ▼
Replay Manifest          ── repository state, run config, stage sequence,
     │                      stage inputs/outputs (hashes), deterministic
     │                      decisions, handoffs, reasoning/consensus,
     │                      graph/memory versions
     ▼
Replay Engine            ── EXACT (offline) · DETERMINISTIC (+ live
     │                      workspace) · COMPARE (two runs)
     ▼
Replay Result            ── MATCH · DRIFT · INVALID · INCOMPLETE
```

**Manifest capture.** `ReplayService.capture()` builds the manifest from the
run's recorded state and persists it (in-memory authoritative, optional
PostgreSQL via migration 015 with graceful fallback). `OrchestrationService`
captures automatically in `_finalize`; the manifest is also derivable on
demand from any run (`GET /runs/{id}/replay/manifest`).

**EXACT** — re-executes deterministic stages offline from recorded evidence
(no workspace, no tests, no LLM). Verdict MATCH means the recorded evidence
still reproduces the recorded decisions.

**DETERMINISTIC** — EXACT plus live-workspace verification: repository
fingerprint, patch application outcome, and test re-execution on the current
code. Verdict DRIFT means the live workspace no longer matches what the run
recorded (or the tests no longer pass).

**COMPARE** — compares two runs stage by stage: which stages matched, which
decision diverged.

## 3. Verdicts

| Verdict | Meaning |
|---|---|
| **MATCH** | Every replayed stage produced an identical result |
| **DRIFT** | At least one replayed stage diverged, with the deterministic check that caused it |
| **INVALID** | The run / manifest / mode is unusable (e.g. unknown run) |
| **INCOMPLETE** | Replay ran but some stages could not be re-executed from recorded evidence (e.g. no workspace, or a deterministic stage whose inputs were not fully recorded) |

## 4. Interpreting the audit

`GET /runs/{id}/replay/audit` runs a manifest build + an **EXACT** replay and
returns: run/manifest/repository identity, per-stage summary, deterministic
checks (expected vs actual), divergences and the overall verdict.

```
AUDIT RESULT — MATCH
  11 deterministic checks · 0 differences · 0 missing artifacts · 0 repository drift

DRIFT DETECTED
  11 checks · 2 differences
  Primary difference: TEST_RESULT_DRIFT
  Supporting evidence: test execution result changed (2 failed, 1 passed)
```

**MATCH vs DRIFT is evidence-scoped.** The audit's EXACT replay can report
MATCH (recorded evidence reproduces the recorded decisions) while a
DETERMINISTIC replay reports DRIFT (the live workspace drifted). Both are
correct: they answer different questions — *is the record internally
consistent?* versus *does the current code reproduce the run?* The dashboard
shows both: the audit banner carries the EXACT verdict; the replay history +
difference viewer surface the DETERMINISTIC finding.

## 5. Deterministic evidence

Every difference in the viewer is backed by a deterministic check:

| Check | Category | Severity |
|---|---|---|
| `repository_fingerprint` | repository drift | high |
| `repository_scope` | configuration drift | high |
| `patch_structure` / `application_outcome` | artifact drift | high |
| `manifest_fidelity` | stage-output drift / missing evidence | high / medium |
| `pipeline_sequence` | stage-input drift | high |
| `handoffs` / `consensus` / `contradictions` | decision drift | high |
| `testing` | test-result drift | high |
| `quality_gate` | quality-gate drift | high |
| deterministic stage without a recorded snapshot | missing evidence | medium |

No causality is claimed beyond what the backend recorded: a difference shows
expected vs actual and the supporting evidence, never a narrative.

## 6. Dashboard

The run-detail page (`/dashboard/runs/[id]`) hosts the Replay & Audit section:

- **Header** — manifest status, repository fingerprint, stages recorded,
  last replay; verdict banner (MATCH/DRIFT/INVALID/INCOMPLETE, color-coded);
  Refresh + Audit Report actions.
- **Start replay** — EXACT / DETERMINISTIC / COMPARE buttons with a
  compare-run picker (newest 50 runs). Phases: starting → running →
  completed/failed, with clean error + retry.
- **Replay Timeline** — stage-by-stage comparison status, deterministic /
  LLM-proposed / observational classification, recorded vs replay
  fingerprints. No chain-of-thought.
- **Difference Viewer** — bounded (50 max) categorized differences with
  severity + deterministic evidence.
- **Audit Report** — enterprise summary banner, identity, stage summary,
  expandable deterministic checks.
- **Replay History** — past replays (mode, verdict, checks, timestamp) with
  pagination.

**Live updates.** No new WebSocket system: replay execution is synchronous on
the backend (the POST returns the completed result), so the phase machine is
local. When the run's existing WebSocket pushes a terminal run status, the
section refreshes the manifest + history so a freshly captured manifest
appears automatically; the existing run page polling fallback covers
disconnected mode.

## 7. Security boundaries

- Replay **never calls an LLM** and never exposes chain-of-thought, hidden
  prompts, or internal reasoning.
- API responses are bounded: hashes truncated to 24 chars, decision/notes
  capped, divergences capped (`checks` ≤ 100, `comparisons` ≤ 64, manifest
  stages ≤ 64, decisions ≤ 200, handoffs ≤ 50).
- No API keys, provider credentials, or raw internal exceptions reach the
  dashboard (error messages are bounded `str(exc)[:300]`).
- Repository scope is respected: replay operates on the run's own recorded
  repository path; DETERMINISTIC mode only verifies the given/recorded
  workspace.

## 8. CLI

```
python -m app.cli replay-manifest <run_id> [--json]
python -m app.cli replay <run_id> --mode exact|deterministic [--workspace PATH] [--json]
python -m app.cli replay-compare <run_id> <other_run_id> [--json]
python -m app.cli replay-audit <run_id> [--json]
python -m app.cli replays <run_id> [--json]
```

CI exit codes (Windows-safe, UTF-8 output): **0** = MATCH, **1** = DRIFT /
INCOMPLETE, **2** = INVALID — a gate can fail a build when a replay
diverges.
