# Phase 15 — Multi-Agent Collaboration

> **Status**: Complete ✅
> **Tests**: 1210 total (+48 from baseline), 0 failed, 18 skipped
> **Database**: PostgreSQL — 3 new tables (agent_handoffs, run_decisions, evidence_conflicts), migration 006
> **Last updated**: August 1, 2026

---

## 1. Overview

Phase 15 transforms the pipeline from isolated stage execution into **Shared Run
Intelligence**: agents exchange *structured evidence, decisions and artifacts* —
never private chain-of-thought.

```
                    Shared Run Intelligence
                           │
       ┌───────────────────┼───────────────────┐
       ▼                   ▼                   ▼
 Repository Evidence   Run Evidence      Historical Memory
       │                   │                   │
       └───────────────────┼───────────────────┘
                           ▼
                   Context / Evidence Bus
                           ▼
Planner → Coding → Testing → Repair → Reviewer → Quality Gate
   │         │         │         │          │
   └─────────┴─────────┴─────────┴──────────┘
                           │
                           ▼
                 Durable Agent Handoffs
```

**Core rule**: agents share structured engineering evidence — never hidden
reasoning, internal scratchpads, or raw model traces.

The collaboration layer is split exactly as the spec requires:

| Service | Responsibility |
|---------|----------------|
| `CollaborationService` | **What agents produced/shared** — handoffs, decisions, conflicts, memory promotion |
| `ContextEngine` | **What evidence an agent receives** — ranking, dedup, budgets |

---

## 2. SharedRunContext

`app/models/collaboration.py` — the authoritative per-run collaboration summary.

```text
run_id / task
requirements_ref / plan_ref
repository_evidence / graph_evidence
agent_handoffs / decisions / conflicts
changed_files / changed_symbols
test_evidence / repair_evidence / review_evidence
warnings / version
```

- Stores **structured information and references** — never giant prompt strings.
- Bounded by per-run caps (see §11 Limits).
- `to_summary()` produces a compact API/CLI view (no hidden reasoning).

Built by `CollaborationService.build_shared_run_context(run)`.

---

## 3. AgentHandoff

A structured, bounded handoff between two agents:

```text
handoff_id / run_id / from_agent / to_agent / stage
summary / decisions / evidence_refs / artifact_refs
affected_symbols / warnings / open_questions
status / validation / created_at
```

Created by the **orchestrator only** (agents never invoke other agents):

```text
Planner → Coding
Coding   → Testing
Testing  → Repair        (on failure)
Repair   → Testing       (after each attempt)
Testing  → Reviewer      (pass or retest-pass)
Reviewer → Quality Gate
```

`status` evolves deterministically: `UNVERIFIED → VALIDATED / PARTIAL / REJECTED`.

---

## 4. Evidence Model

`EvidenceRef` carries `type`, `reference`, `confidence`, `provenance`, `created_at`.

```text
SOURCE_CODE        GRAPH_RELATIONSHIP   RETRIEVAL
PLAN               PATCH                TEST_RESULT
FAILURE            REPAIR               REVIEW_FINDING
QUALITY_GATE       HISTORICAL_MEMORY    AGENT_CLAIM
```

Evidence authority hierarchy (§12) — higher outranks lower:

```text
PATCH(100) > TEST_RESULT(95) > QUALITY_GATE(90) > FAILURE(88)
> SOURCE_CODE(85) > GRAPH_RELATIONSHIP(80) > REVIEW_FINDING(60)
> REPAIR(55) > PLAN(50) > RETRIEVAL(40) > HISTORICAL_MEMORY(30)
> AGENT_CLAIM(10)
```

Deterministic evidence **never** loses to an LLM claim.

---

## 5. Provenance Dedup Merging

The Phase 14 limitation is fixed in `ContextEngine._deduplicate()`:

- A symbol discovered by vector retrieval **and** graph retrieval **and** impact
  analysis now yields **one canonical context item**.
- The survivor keeps the strongest relevance score.
- All duplicates' provenance/evidence is merged onto the canonical item via
  `ContextItem.merged_provenances` — including when a higher-scored duplicate
  promotes over an existing item (the winner's own provenance stays in
  `provenance`; the loser's goes to `merged_provenances`).

```text
AuthService.login
├── VECTOR   score=.91
├── GRAPH    CALLS distance=1
└── IMPACT   direct
```

Covered by `tests/test_context_engine_integration.py` (deterministic ordering).

---

## 6. Decision Records

`RunDecision` — lightweight engineering decisions:

```text
decision_id / run_id / decision_type / statement / made_by / evidence_refs / created_at
```

Categories: `PLANNING`, `IMPLEMENTATION`, `TESTING`, `REPAIR`, `REVIEW`.

Recorded at stage boundaries by the orchestrator so later agents understand
*what was decided* without replaying previous prompts.

---

## 7. Conflict Detection

`EvidenceConflict` — contradictions between agent claims and deterministic
evidence. Example: coding claims success while actual test evidence reports
failure.

- `detect_conflicts()` builds an `AGENT_CLAIM` evidence ref vs a `TEST_RESULT`
  ref, resolution `DETERMINISTIC_WINS`, and **downgrades the handoff to
  REJECTED** (persisted durably).
- `validate_handoff()` validates each handoff claim deterministically against
  the actual patch (changed files/symbols) and actual test result; unsupported
  claims are marked `unverified` or `rejected`.
- Conflicts are never silently resolved in favor of the claim.

---

## 8. Cross-Agent Context

`ContextEngine.build_context(..., handoffs=...)` now accepts structured
handoffs. `retrieve_relevant_handoffs(agent_type)` selects:

1. Handoffs **addressed to** the agent (most relevant).
2. Most recent handoffs overall (repair/retest loops stay visible).

Bounded by `MAX_HANDOFFS_SELECTED` (8). Notes accumulated across stages
(`cross_agent_notes`) continue to flow to later agents via the `AGENT_NOTES`
budget. Per-agent context = current evidence + selected handoffs + required
history, all within Phase 14 budgets.

---

## 9. Orchestrator Integration

`OrchestrationService` remains the coordinator (Phase 10). It:

```text
build context → invoke agent → validate output → create handoff
→ record decision → persist evidence → transition stage
```

Wiring helpers (all never-fatal, graceful degradation):

- `_create_handoff(run, from_agent, to_agent, summary, decisions, ...)`
- `_record_decision(run, decision_type, statement, made_by)`
- `_detect_handoff_conflicts(run)` — validates claims + detects conflicts after
  each test run (initial and retest)
- `_promote_memory(run)` — promotes verified knowledge at terminal completion

Events emitted: `HANDOFF_CREATED`, `DECISION_RECORDED`, `CONFLICT_DETECTED`,
`MEMORY_PROMOTED` (also broadcast over WebSocket).

---

## 10. Persistence, Recovery & Concurrency

- **Tables** (migration 006): `agent_handoffs`, `run_decisions`,
  `evidence_conflicts` — added in `app/db/models.py` + `alembic/versions/006_add_collaboration.py`.
- `CollaborationService` mirrors to PostgreSQL when available and keeps an
  in-memory copy, so it **degrades gracefully** when the DB is down.
- `recover(run_id)` rehydrates handoffs/decisions/conflicts after a restart —
  a Planner→Coding handoff persisted before a restart is available to Coding
  without rerunning the Planner.
- `SharedRunContext.version` tracks optimistic-concurrency state; all write
  paths `commit()` explicitly (matching the codebase's established pattern).
- Existing Phase 8 repair bounds are preserved; attempt-specific handoffs are
  kept per repair cycle, not collapsed.

---

## 11. Memory Promotion

At terminal completion, `promote_memory(run)` promotes **verified** knowledge
only:

- **Successful change** memory (approved run with a patch) → `SUCCESSFUL_CHANGE`
  / `VERIFIED`, evidence sourced from the quality gate.
- **Known failed approach** (rejected handoff claims with symbols) →
  `FAILED_APPROACH` / `PROVISIONAL`, evidence sourced from test results.

Promotion requires a repository; running runs are skipped. Current repository
evidence always outranks historical memory (Phase 14 invalidation preserved).

---

## 12. API

Bounded, paginated diagnostic endpoints (`app/api/v1/collaboration.py`):

```text
GET /api/v1/runs/{run_id}/handoffs                 (limit/offset, to_agent filter)
GET /api/v1/runs/{run_id}/handoffs/{handoff_id}
GET /api/v1/runs/{run_id}/decisions                (limit/offset)
GET /api/v1/runs/{run_id}/collaboration            (metrics + handoffs + decisions + conflicts)
```

Only engineering evidence is exposed — never hidden reasoning or raw internal
prompts. All fields are truncated at the API boundary.

---

## 13. CLI

```text
devpilot handoffs <run_id> [--to-agent X] [--json]
devpilot decisions <run_id> [--json]
devpilot collaboration <run_id> [--json]
```

Example:

```text
Run: RUN-ABC123

Planner → Coding
  Target symbols: 4
  Evidence: 9
  Warnings: 1

Coding → Testing
  Changed symbols: 3
  Suggested tests: 5

Testing → Reviewer
  Passed: 24
  Failed: 0

Conflicts: 0
```

---

## 14. Frontend

The `/devpilot-context` page gained a **collaboration view** (real APIs):

- Run ID input + **View collaboration** button (loading / empty / error / retry).
- Metrics: handoffs, validated, decisions, conflicts, resolved.
- Handoff timeline cards (from → to, stage, status chip, decisions, symbols).
- Conflicts and Decisions lists.

No mock data for Phase 15 functionality; no direct PostgreSQL access from the
browser.

---

## 15. Security

Handoff/decision/conflict content is **untrusted**:

- `redact_secrets()` strips API keys, tokens, `sk-`/`ghp_`/`AKIA` patterns, and
  private-key blocks before durable storage or frontend exposure.
- Handoff claims are validated **deterministically** (patch + test evidence) —
  never trusted from the LLM.
- No content becomes a system instruction; prompts remain bounded.

Covered by secret-canary tests in `tests/test_collaboration_service.py`.

---

## 16. Graceful Degradation

The pipeline works unchanged when:

```text
CollaborationService unavailable
no previous handoffs
memory unavailable
graph unavailable
DB down
```

Every collaboration call is wrapped; failures are logged and skipped. The
orchestrator's `_get_collaboration()` returns `None` and all callers no-op.
Verified by `test_handoffs_survive_graceful_degradation`.

---

## 17. Limits

```text
MAX_HANDOFFS_PER_RUN      = 50
MAX_EVIDENCE_PER_HANDOFF  = 20
MAX_DECISIONS_PER_RUN     = 100
MAX_HANDOFFS_SELECTED     = 8
MAX_CONFLICTS_PER_RUN     = 50
SUMMARY_MAX_LEN           = 500
CLAIM_MAX_LEN             = 300
```

Enforced in models (Pydantic v2 `max_length` → `ValidationError`) **and** the
service (truncation before construction). APIs paginate history.

---

## 18. Tests

| Area | Location |
|------|----------|
| Models & bounds | `tests/test_collaboration_models.py` |
| Service: handoffs, validation, conflicts, decisions, recovery, memory promotion, redaction, metrics | `tests/test_collaboration_service.py` |
| API endpoints | `tests/test_collaboration_api.py` |
| ContextEngine handoff integration | `tests/test_context_engine_integration.py` |
| Orchestrator stage-boundary handoffs, repair separation, degradation | `tests/test_orchestration.py` |
| Migration 006 tables | `tests/test_migration.py` |

**Final baseline: 1210 passed, 18 skipped, 0 failed.**

---

## 19. Next Phase — Autonomous Execution

Phase 16 builds on this collaboration layer: `AutonomousExecutionController`
wraps the orchestrated pipeline in a goal-tracking loop with deterministic
decisions, versioned replanning, and safe termination. Collaboration outputs
(handoffs, decisions, conflicts, memory) are the natural evidence feed for
future autonomous goal iterations.

See [`docs/AUTONOMOUS_EXECUTION.md`](AUTONOMOUS_EXECUTION.md).
