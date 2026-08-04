# Context Engineering & Memory (Phases 13–15)

> **Status**: Complete ✅
> **Last updated**: August 1, 2026

---

## 1. Overview

DevPilot's agents consume **bounded, ranked, provenance-tracked context** and
collaborate through **structured evidence** — never unbounded prompt growth and
never private chain-of-thought.

```text
ContextEngine (what evidence an agent receives)
   ├── Repository evidence (retrieval, graph)
   ├── Run evidence (plan, patch, tests, repair, review)
   ├── Historical memory (RepositoryMemory)
   ├── Cross-agent notes (AGENT_NOTES)
   └── Structured handoffs (AGENT_HANDOFF)

CollaborationService (what agents produced/shared)
   ├── AgentHandoff / RunDecision / EvidenceConflict
   └── SharedRunContext / memory promotion
```

---

## 2. ContextEngine

`app/services/context_engine.py` builds an `AgentContext` per agent type
(planner, coding, test, repair, reviewer). Sources are ranked deterministically,
deduplicated with provenance merging, and fit within per-category token
budgets. `AgentContext` exposes 18+ evidence categories including:

- `retrieval`, `graph`, `repository`, `requirements`, `plan`
- `test_results`, `failures`, `repair_history`, `review_findings`
- `agent_notes` (cross-agent, Phase 15) and `agent_handoffs` (Phase 15)

**Budgeting** — a guaranteed allocation ensures notes and handoffs survive
token contention (e.g. `AGENT_NOTES` gets 15%/800-token cap).

**Graceful degradation** — every source is optional; an unavailable engine,
memory, graph, or collaboration service never breaks a basic workflow.

---

## 3. Provenance Dedup Merging (Phase 14 fix)

`_deduplicate()` keeps the **strongest-scored canonical item** and merges all
duplicates' provenance/evidence onto it via `ContextItem.merged_provenances`:

```text
AuthService.login
├── VECTOR   score=.91
├── GRAPH    CALLS distance=1
└── IMPACT   direct
```

The promotion branch is correct for both directions: when a new item wins, the
loser's provenance moves into `merged_provenances` (the winner's own
provenance stays in `provenance`). Deterministic ordering is tested in
`tests/test_context_engine_integration.py`.

---

## 4. Repository Memory

`app/services/repository_memory_service.py` — durable knowledge memory with a
lifecycle `VERIFIED / PROVISIONAL / STALE / INVALID`, symbol-based
invalidation, and PostgreSQL persistence (migration 004, `repository_memories`
table).

Phase 15 memory promotion (`CollaborationService.promote_memory`) promotes only
**verified** knowledge at terminal run completion:

- approved run + patch → `SUCCESSFUL_CHANGE` / `VERIFIED` (quality-gate evidence)
- rejected handoff claims with symbols → `FAILED_APPROACH` / `PROVISIONAL`
  (test-result evidence)

Current repository evidence always outranks stale historical memory.

---

## 5. Cross-Agent Context (Phase 15)

- `cross_agent_notes` accumulate stage-by-stage and flow to later agents via
  the `AGENT_NOTES` budget.
- `retrieve_relevant_handoffs(agent_type)` selects handoffs addressed to the
  agent first, then recent handoffs overall (bounded to `MAX_HANDOFFS_SELECTED`).
- `ContextEngine._build_handoff_context()` renders selected handoffs into the
  agent's context with a per-item token budget.

---

## 6. API & Observability

```text
GET /api/v1/runs/{run_id}/handoffs          — paginated, to_agent filter
GET /api/v1/runs/{run_id}/handoffs/{id}
GET /api/v1/runs/{run_id}/decisions         — paginated
GET /api/v1/runs/{run_id}/collaboration     — metrics + records
GET /api/v1/memory/repositories             — repository memory browsing
GET /api/v1/memory/{repository_id}/stats
POST /api/v1/memory/{repository_id}/invalidate-symbols
DELETE /api/v1/memory/{memory_id}
```

CLI: `devpilot handoffs|decisions|collaboration <run_id>`.

Frontend: `/devpilot-context` page with memory browser and collaboration view
(loading / empty / error / retry states; real APIs only).

---

## 7. Security

All context/handoff/memory text is untrusted: `redact_secrets()` strips
API keys, tokens, and private-key blocks before storage/exposure; handoff
claims are validated deterministically against the actual patch and test
result; nothing becomes a system instruction.

---

## 8. Limits

```text
MAX_HANDOFFS_PER_RUN      = 50        MAX_EVIDENCE_PER_HANDOFF = 20
MAX_DECISIONS_PER_RUN     = 100       MAX_HANDOFFS_SELECTED    = 8
MAX_CONFLICTS_PER_RUN     = 50        SUMMARY_MAX_LEN          = 500
CLAIM_MAX_LEN             = 300
```

Enforced at the model (`ValidationError`) and service (truncation) layers.

---

## 9. Engineering Knowledge Graph Integration (Phase 18)

Phase 18 adds the **Engineering Knowledge Graph (EKG)** as an additional,
unified retrieval source. `ContextEngine` queries the EKG alongside the
semantic graph, repository memory, consensus, notebook, and historical runs —
using the existing ranking, deduplication, and token budgets.

```text
ContextEngine.build_context(...)
   ├── Repository evidence (retrieval, Phase 12 graph)
   ├── Repository memory (Phase 13/14)
   ├── Run evidence (plan, patch, tests, repair, review)
   ├── Cross-agent notes / handoffs (Phase 15)
   ├── Consensus / notebook (Phase 17)
   └── Engineering Knowledge Graph (Phase 18) — unified temporal layer
        answering why/what-introduced/which-repair/which-decision
```

The EKG re-uses every earlier store as typed nodes and temporal edges, so the
same bounded-context contract holds — the graph never expands an agent's token
budget. See [`docs/ENGINEERING_KNOWLEDGE_GRAPH.md`](ENGINEERING_KNOWLEDGE_GRAPH.md).
