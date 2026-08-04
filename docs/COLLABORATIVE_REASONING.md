# Collaborative Reasoning & Evidence Consensus

**Status**: ✅ Complete
**Tests**: 1352 passed / 21 skipped / 0 failed (full backend suite, live PostgreSQL)
**Database**: PostgreSQL 18.4 — migration chain 001→010
**Last updated**: August 1, 2026 (Session 8)

---

## 1. Overview

Phase 17 builds the **reasoning layer above the Phase 15 collaboration store**:

```text
CollaborationService        = records WHAT agents produced / shared
CollaborativeReasoningEngine = decides whether the evidence AGREES
```

Where Phase 15 persisted handoffs, decisions, and conflicts, Phase 17 interprets
that shared evidence: it aggregates bounded confidence, detects contradictions
between agent claims and deterministic evidence, produces per-topic consensus,
and assembles a shared **Engineering Notebook** per run.

The engine sits at run completion inside the orchestrator and is also available
to the autonomy controller (consensus topics enrich REPLAN rationale) and to the
dashboard (consensus / contradictions / notebook views).

### Security invariant

**Only evidence, confidence, decisions, and consensus are ever exposed —
never chain-of-thought.** Deterministic evidence always outranks agent claims;
an unsupported LLM claim can never flip a consensus.

---

## 2. Layering

```text
┌──────────────────────────────────────────────────────────────┐
│  CollaborativeReasoningEngine (Phase 17)                     │
│  decides whether shared evidence AGREES                      │
├──────────────────────────────────────────────────────────────┤
│  CollaborationService (Phase 15)                             │
│  records handoffs, decisions, conflicts, memory promotion    │
├──────────────────────────────────────────────────────────────┤
│  OrchestrationService (Phase 10/11)                          │
│  coordinates agent stages                                   │
└──────────────────────────────────────────────────────────────┘
```

---

## 3. Confidence Model

`compute_confidence(evidence_refs)` is purely arithmetic over evidence — no LLM
and no hidden reasoning.

- Every `EvidenceType` has a fixed **authority weight**:

  | Evidence | Authority |
  |----------|-----------|
  | PATCH | 1.0 |
  | TEST_RESULT | 0.95 |
  | QUALITY_GATE | 0.9 |
  | FAILURE | 0.88 |
  | SOURCE_CODE | 0.85 |
  | GRAPH_RELATIONSHIP | 0.8 |
  | REVIEW_FINDING | 0.6 |
  | REPAIR | 0.55 |
  | PLAN | 0.5 |
  | RETRIEVAL | 0.4 |
  | HISTORICAL_MEMORY | 0.3 |
  | AGENT_CLAIM | 0.1 |

- `value = Σ(authority × weight) / Σ(authority)`.
- **Bounded**: a claim-only mix (zero deterministic evidence) is capped at
  **0.49**, so it can never reach `HIGH` (≥ 0.75). An unsupported LLM claim
  cannot promote a consensus.
- Output: `ConfidenceScore { value, tier (high/medium/low/unknown),
  evidence_count, deterministic_count, claim_count, basis }`.

---

## 4. Contradiction Detection

`detect_contradictions(run)` scans Phase 15 handoffs and run state for three kinds:

| Kind | Claim | Deterministic evidence | Resolution |
|------|-------|------------------------|------------|
| `CLAIM_VS_TEST` | coding/testing/repair handoff claims tests passed | test result reports failure | `deterministic_wins` |
| `CLAIM_VS_GATE` | handoff claims ready/approved | quality gate rejected | `deterministic_wins` |
| `SCOPE_VS_IMPACT` | plan scoped changes to X | impact graph shows Y also affected | `unresolved` (informational) |

Notes:

- Claim-vs-test is **scoped to handoffs from coding/testing/repair** — a
  planner's "Plan complete" is not a claim that tests passed.
- Scope-vs-impact uses the Phase 12 `ImpactAnalysisService` over the run's
  changed files; it degrades to an empty result when the graph is unavailable.
- Contradictions are deduplicated by description and bounded (50/run).

---

## 5. Evidence Consensus

`build_consensus(run)` produces per-topic records from collected evidence:

| Topic | Produced when | Status |
|-------|---------------|--------|
| `test_status` | test evidence exists | AGREED / CONFLICTED / UNKNOWN |
| `patch_complete` | patch exists | AGREED / CONFLICTED (test failures) |
| `quality_gate` | gate decision exists | AGREED (approved) / CONFLICTED |
| `scope_compliance` | scope-vs-impact contradictions | CONFLICTED |

Each `EvidenceConsensus` carries: topic, status, confidence, supporting
evidence refs, conflicting evidence refs, final decision, and contributing
agents. Records are deduplicated by topic and bounded (50/run, 20 evidence refs).

---

## 6. Engineering Notebook

`build_notebook(run, consensus, contradictions)` assembles a shared notebook:

- **accepted_decisions** — decisions adopted by the agents (bounded 50)
- **rejected_decisions** — resolved contradictions (bounded 50)
- **conflicts / resolved_conflicts** — contradiction breakdown (bounded)
- **consensus** — consensus records (bounded)
- **timeline** — ordered DECISION / CONSENSUS / CONTRADICTION entries
  (bounded 200)

The notebook ID is derived from the unique run ID (`NB-{run_id}`), guaranteeing
a unique `uq_engineering_notebooks_notebook_id` per run.

---

## 7. One-Call Pipeline

```python
outcome = await reasoning.analyze_run(run)
# => { run_id, consensus, contradictions, notebook, confidence }
```

`analyze_run()` runs detect → consensus → notebook and never raises (degrading
to empty output with a debug log on unexpected errors).

---

## 8. Orchestrator Integration

- `OrchestrationService._get_reasoning()` lazily builds the engine (sharing the
  collaboration service).
- `_build_reasoning()` runs the pipeline at run completion, emitting
  `CONSENSUS_BUILT` / `CONFLICT_DETECTED` events.
- The **reviewer agent context now carries shared-consensus notes**
  (evidence-only) so the reviewer sees what the collaboration layer concluded
  before producing its verdict.

---

## 9. Autonomy Integration

- `AutonomousRunState.consensus_topics` — per-run topic summaries
  (`test_status:agreed:tests_passed`, ...).
- `_refresh_consensus_topics()` analyzes each executed run through the engine.
- Consensus topics **enrich the REPLAN rationale** — they inform the decision,
  but never override deterministic evidence.

---

## 10. PostgreSQL Persistence (Migration 010)

Three tables, JSONB payloads, restart recovery:

| Table | Unique | Indexes |
|-------|--------|---------|
| `evidence_consensus` | `uq_evidence_consensus_consensus_id` | `idx_ecs_run_id`, `idx_ecs_run_topic` |
| `contradiction_records` | `uq_contradiction_records_contradiction_id` | `idx_cdr_run_id`, `idx_cdr_run_kind` |
| `engineering_notebooks` | `uq_engineering_notebooks_notebook_id` | `idx_en_run_id` |

> **Index-naming note**: evidence_consensus uses the `idx_ecs_*` prefix because
> migration 006 already owns `idx_ec_run_id` on `evidence_conflicts`.
> PostgreSQL index names are schema-unique, so the prefixes are distinct.

- The engine keeps an in-memory mirror (authoritative during the run) and
  persists best-effort; DB failures degrade to in-memory.
- `recover(run_id)` rehydrates consensus, contradictions, and the notebook from
  PostgreSQL after a restart (timestamps restored from the DB).

---

## 11. API

| Endpoint | Returns |
|----------|---------|
| `GET /api/v1/runs/{run_id}/consensus` | consensus records (bounded) |
| `GET /api/v1/runs/{run_id}/contradictions` | contradiction records (bounded) |
| `GET /api/v1/runs/{run_id}/notebook` | engineering notebook |
| `GET /api/v1/runs/{run_id}/reasoning` | combined snapshot (recover + list) |

All responses are evidence-only (no chain-of-thought), bounded, and wrapped in
the standard `Response` envelope with `success` / `error` / `message`.

---

## 12. CLI

```text
devpilot consensus <run_id> [--json]
devpilot conflicts <run_id> [--json]
devpilot notebook <run_id> [--json]
```

Output is ASCII-safe (Windows cp1252 consoles cannot encode `→` / `—`, so the
CLI uses `->` / `-`). Invalid run IDs degrade gracefully ("No ... found").

---

## 13. Frontend

The Phase 17 Collaboration view on the dashboard shows:

- **Consensus cards** — topic, status, confidence tier + value, decision
- **Contradictions** — kind, resolution, deterministic evidence reference
- **Engineering notebook** — accepted/rejected decisions, conflicts, timeline

It reuses the existing run-ID input pattern used by the Phase 15/16 views and
calls the reasoning endpoints.

---

## 14. Demo

`python scripts/demo_phase17.py` (deterministic, no LLM API) demonstrates:

- **A** — planner + coding agreement → AGREED consensus, HIGH confidence
- **B** — coding claims success but tests fail → CLAIM_VS_TEST contradiction
- **C** — reviewer context carries consensus notes
- **D** — autonomy REPLAN rationale uses consensus topics
- **E** — restart recovery rehydrates the notebook from PostgreSQL

`--json` emits a structured summary; `--live` runs a real LLM execute_run when a
provider is configured (and refuses otherwise). The demo targets a test-named
database and refuses to mutate a non-test DB.

---

## 15. Security Model

- Evidence-only APIs — no chain-of-thought exposure.
- Deterministic authority — deterministic evidence outranks claims everywhere.
- Bounded responses — caps on records, evidence refs, timeline entries.
- Bounded confidence — claim-only evidence cannot reach HIGH.
- Graceful degradation — DB unavailable ⇒ in-memory fallback, never a crash.

---

## 16. Known Limitations

1. **Consensus-driven replan test selection** — replans currently use impact
   analysis (Phase 12d); feeding consensus topics into the impact-selected
   `test_set` is a natural next step.
2. **Push consensus events over WebSocket** — the goal feed is push-based; the
   collaboration/reasoning views still poll.
3. **Notebook diffing view** — accepted vs rejected decisions and resolved
   conflicts are rendered as lists; a browsable engineering timeline with
   version diffing is not built.
4. **Live-LLM in CI** — `--live` requires provider keys and is proven locally
   only; the deterministic demo covers the same loop in CI.
