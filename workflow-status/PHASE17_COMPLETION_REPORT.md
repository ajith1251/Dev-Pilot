# Phase 17 Completion Report — Collaborative Reasoning & Evidence Consensus

> **Status**: COMPLETE ✅
> **Date**: August 1, 2026

---

## 1. Status & Test Baseline

| Metric | Before (Phase 16) | After (Phase 17) |
|--------|-------------------|------------------|
| Passed | 1288 | **1352** |
| Failed | 0 | **0** |
| Skipped | 21 | 21 |
| Frontend build | ✅ | ✅ |
| Migration tests | ✅ | ✅ (chain 001→010 on clean DB) |
| Regressions | — | **0** |

Final baseline: **1352 passed, 21 skipped, 0 failed** — full backend suite
against live PostgreSQL 18.4 with proper dev/test DB separation
(`DATABASE_URL=devpilot_dev`, `TEST_DATABASE_URL=devpilot_test`).

> The full suite was run with the DB separated for the first time. That
> exposed one genuine pre-existing bug (see §4) which, once fixed, brought
> the suite from 1326/26-failed to 1352/0-failed and is now green.

---

## 2. Deliverables

### New — backend

- `app/models/reasoning.py` — `ConfidenceTier` (high/medium/low/unknown),
  `ConfidenceScore` (weighted authority, bounded 0..1, evidence breakdown),
  `EvidenceConsensus` (topic, status, supporting/conflicting evidence refs,
  final decision, contributing agents), `ContradictionRecord` (kind,
  claim vs deterministic evidence, deterministic-wins resolution),
  `NotebookEntry` + `EngineeringNotebook` (accepted/rejected decisions,
  conflicts, resolved conflicts, consensus, timeline). All bounded:
  50 consensus / 50 contradictions / 200 timeline / 20 evidence refs.
- `app/services/reasoning_service.py` — `CollaborativeReasoningEngine`:
  `compute_confidence()` (claim-only evidence capped below HIGH so an
  unsupported LLM claim can never flip a consensus), `collect_evidence()`
  (planner/coding/testing/repair/reviewer/graph/memory/gate buckets),
  `detect_contradictions()` (claim-vs-test scoped to coding/testing/repair
  handoffs, claim-vs-gate, scope-vs-impact via Phase 12 impact analyzer),
  `build_consensus()`, `build_notebook()`, one-call `analyze_run()`
  (never raises — degrades to empty output).
- `app/api/v1/reasoning.py` — `GET /api/v1/runs/{run_id}/consensus`,
  `/contradictions`, `/notebook`, `/reasoning` — evidence-only, bounded,
  standard `Response` envelope; not-found degrades to `success: False`.
- `app/cli_reasoning.py` — `devpilot consensus|conflicts|notebook <run_id>`
  with `--json`; ASCII-safe output (Windows cp1252-safe).
- `alembic/versions/010_add_reasoning.py` — migration 010:
  `evidence_consensus`, `contradiction_records`, `engineering_notebooks`
  (JSONB payloads, named unique constraints, `idx_ecs_*`/`idx_cdr_*`/
  `idx_en_*` indexes).

### Modified — backend

- `app/services/orchestration_service.py` — `_get_reasoning()` +
  `_build_reasoning()` run the pipeline at run completion
  (`CONSENSUS_BUILT` / `CONFLICT_DETECTED` events); reviewer context
  carries shared-consensus notes (evidence-only).
- `app/services/autonomy_service.py` — `AutonomousRunState.consensus_topics`;
  `_refresh_consensus_topics()`; REPLAN rationale enriched with consensus
  topics (never overrides deterministic evidence).
- `app/services/postgres_run_store.py` — **bug fix**: honors the
  `database_url` parameter (owned engine + `async dispose()`, engine-None
  guard) instead of silently connecting to `settings.DATABASE_URL`.
- `app/main.py`, `app/cli.py` — reasoning router/CLI registration.
- `app/models/context.py` — CROSS_AGENT evidence category (Phase 15).

### New — frontend

- `web/src/App.jsx` + `web/src/styles.css` — Phase 17 Collaboration view:
  consensus cards (topic, status, confidence tier + value, decision),
  contradictions (kind, resolution, deterministic evidence), engineering
  notebook (accepted/rejected decisions, conflicts, timeline). Reuses the
  existing run-ID input pattern.

### New — tests (23 reasoning + regression)

- `tests/test_reasoning_engine.py` — 23 tests: confidence model (incl.
  claim-only cap), contradiction detection + dedup, consensus
  agreement/conflict, notebook build, autonomy consensus integration,
  API, CLI.

### Docs & scripts

- `docs/COLLABORATIVE_REASONING.md` (new — full phase documentation)
- `docs/ORCHESTRATION.md`, `docs/ARCHITECTURE.md`, `README.md`,
  `workflow-status/PROJECT_STATE.md` (updated)
- `scripts/demo_phase17.py` (new) — deterministic demo (A–E) +
  `--json` + `--live` guard
- `workflow-status/PHASE17_COMPLETION_REPORT.md` (this file)

**Dependencies**: none added. **Alembic**: migration 010 (3 tables).
Total schema: **21 tables**.

---

## 3. Architecture

```text
CollaborationService (Phase 15)      = records WHAT agents produced / shared
CollaborativeReasoningEngine (P17)   = decides whether the evidence AGREES
```

```text
run completes
     │
     ├── collect_evidence(run)   → planner / coding / testing / repair /
     │                             reviewer / graph / memory / gate buckets
     ├── compute_confidence()    → weighted authority (deterministic evidence
     │                             outranks claims; claim-only ≤ 0.49)
     ├── detect_contradictions() → CLAIM_VS_TEST (coding/testing/repair
     │                             handoffs), CLAIM_VS_GATE, SCOPE_VS_IMPACT
     ├── build_consensus()       → per-topic AGREED / CONFLICTED / UNKNOWN
     ├── build_notebook()        → accepted/rejected decisions, conflicts,
     │                             consensus, timeline
     └── persist (migration 010) + in-memory mirror + recover(run_id)
```

### Security invariants

- **Evidence-only exposure** — consensus, confidence, and decisions are
  surfaced; chain-of-thought is never.
- **Deterministic authority** — deterministic evidence always outranks an
  LLM claim; an unsupported claim cannot flip a consensus (claim-only mixes
  capped below `HIGH`).
- **Bounded responses** — caps on records, evidence refs, timeline entries.

### Confidence model

`value = Σ(authority × weight) / Σ(authority)` over fixed evidence-type
authority weights (PATCH 1.0 … AGENT_CLAIM 0.1); a claim-only mix is
**capped at 0.49** — never `HIGH` (≥ 0.75).

---

## 4. Verification Checklist

| # | Check | Result |
|---|-------|--------|
| 1 | Full backend suite (live PG, dev/test DB separation) | ✅ **1352 passed, 21 skipped, 0 failed** |
| 2 | Fresh-DB migration chain 001→010 (tables, FKs, indexes, JSONB, constraints) | ✅ |
| 3 | Demo `scripts/demo_phase17.py` — sections A–E + JSON + live guard | ✅ no duplicate-key errors |
| 4 | PostgreSQL recovery — fresh-engine reload rehydrates notebook/consensus/contradictions/timeline | ✅ |
| 5 | API — consensus/contradictions/notebook/reasoning bounded, evidence-only, graceful not-found | ✅ |
| 6 | CLI — consensus/conflicts/notebook normal + `--json` + invalid run | ✅ |
| 7 | Reasoning tests | ✅ 23/23 |
| 8 | Frontend production build | ✅ |
| 9 | Reviewer passes on the full change set + all fixes | ✅ no concrete bugs |
| 10 | No dependencies added; no regressions | ✅ |

### Bugs found & fixed during final verification

1. **`PostgresRunStore` ignored its `database_url` parameter** — always
   connected to `settings.DATABASE_URL` (devpilot_dev, 7 tables) even when
   an explicit test URL was passed, so on a properly separated dev/test
   setup the run-store contract tests hit a schema missing `context_json`
   (migration 008) → `UndefinedColumnError`. **Fixed**: the store honors the
   explicit URL (owned engine + `async dispose()` + engine-None guard).
   Contract + autonomy run-store tests: **67 passed** against the separated
   test DB. This was a real pre-existing bug the Phase 17 live-PG run with
   dev/test separation exposed.
2. **Windows CLI encoding crash** — `→` / `—` in `devpilot
   consensus|conflicts|notebook` output raised `UnicodeEncodeError` on
   cp1252 consoles. **Fixed**: ASCII-safe output (`->` / `-`).
3. **Migration-test harness hardening** — `clean_db` teardown re-runs
   `alembic upgrade head` with a 120 s timeout (was 30 s); run-store
   contract fixture disposes its owned engine (no leaked pools).

---

## 5. Remaining Limitations / Next Steps

1. **Consensus-driven replan test selection** — replans currently select
   tests via Phase 12 impact analysis; feeding consensus topics into the
   impact-selected `test_set` is the natural next step.
2. **Push collaboration/reasoning events over WebSocket** — the goal feed
   is push-based; the collaboration + reasoning views still poll (the
   autonomy WS pattern could extend to run + handoff + consensus events).
3. **Notebook diffing view** — accepted vs rejected decisions and resolved
   conflicts render as lists; a browsable engineering timeline with version
   diffing is not built.
4. **Live-LLM E2E runbook + CI matrix (closed)** — a dockerized CI matrix
   (`.github/workflows/ci.yml` + `docker-compose.yml`, postgres:18.4 service)
   now validates both paths on every push: the in-memory-fallback job (no PG,
   PG tests must skip — currently 1327 passed / 46 skipped / 0 failed) and the
   live-PG job (alembic 001→010 + full suite, 1352 passed / 0 failed). The
   `--live` runbook is documented in README; actually running `--live` still
   requires a provider key (`DEVPILOT_LLM_PROVIDER=openai` + `OPENAI_API_KEY`)
   which cannot be committed to CI.
5. **Phase 18** — candidate directions: feed consensus/conflicts into goal
   evidence for cross-run learning (autonomy ↔ collaboration closed loop),
   multi-run consensus mining (what does the team "always agree" on), or a
   reasoning trust layer that gates agent claims by consensus history.
   See `workflow-status/PROJECT_STATE.md` §12.

---

## 6. Files Created / Modified (recap)

```text
NEW    app/models/reasoning.py
NEW    app/services/reasoning_service.py
NEW    app/api/v1/reasoning.py
NEW    app/cli_reasoning.py
NEW    alembic/versions/010_add_reasoning.py
NEW    tests/test_reasoning_engine.py
NEW    scripts/demo_phase17.py
NEW    docs/COLLABORATIVE_REASONING.md
NEW    workflow-status/PHASE17_COMPLETION_REPORT.md
MOD    app/services/orchestration_service.py, autonomy_service.py,
      postgres_run_store.py, app/main.py, app/cli.py
MOD    web/src/App.jsx, web/src/styles.css
MOD    tests/test_migration.py, tests/test_run_store_contract.py
MOD    docs/ORCHESTRATION.md, docs/ARCHITECTURE.md, README.md,
      workflow-status/PROJECT_STATE.md
```
