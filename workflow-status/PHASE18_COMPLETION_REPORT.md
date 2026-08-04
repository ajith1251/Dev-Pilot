# Phase 18 Completion Report — Engineering Knowledge Graph (EKG)

> **Status**: COMPLETE ✅
> **Date**: August 2, 2026

---

## 1. Status & Test Baseline

| Metric | Before (Phase 17) | After (Phase 18) |
|--------|-------------------|------------------|
| Passed | 1352 | **1392** |
| Failed | 0 | **0** |
| Skipped | 21 | 18 |
| Frontend build | ✅ | ✅ |
| Migration tests | ✅ (chain 001→010) | ✅ (chain 001→011 on clean DB) |
| Regressions | — | **0** |

Final baseline: **1392 passed, 18 skipped, 0 failed** — full backend suite
against live PostgreSQL 18.4 with proper dev/test DB separation.

| Path | Result |
|---|---|
| Live-PG full suite | **1392 passed · 18 skipped · 0 failed** |
| In-memory fallback (no DB) | **1362 passed · 48 skipped · 0 failed** |
| Live-PG targeted (EKG + migration + run-store + API contract) | **123 passed · 14 skipped · 0 failed** |
| EKG test file | 35 passed (no-PG) / 37 passed (live-PG) |
| Frontend `next build` | ✅ (engineering-graph route included) |

---

## 2. Migration Summary

Migration **011** (`alembic/versions/011_add_engineering_knowledge_graph.py`)
adds three normalized tables plus bounded JSONB:

| Table | Columns (key) | Indexes |
|---|---|---|
| `ekg_nodes` | node_id (unique, String 40), node_type, name, qualified_name, kind, source_ref, source_type, payload (JSONB), provenance (JSONB), status, graph_version | node_type, source, name, version |
| `ekg_edges` | edge_id (unique), source_id, target_id, relationship, weight, metadata_json (JSONB), provenance (JSONB), graph_version | source, target, (source,target,relationship), rel, version |
| `ekg_versions` | version (unique), run_id, summary, updated_nodes (JSONB), updated_edges (JSONB), superseded_node_ids (JSONB) | version, run |

Verified: `alembic upgrade head` on a clean `devpilot_test` applies 001→011;
downgrade reverses cleanly; `clean_db` migration-test teardown drops the new
tables before re-upgrade (no `DuplicateTableError`).

---

## 3. Files Created

- `app/models/engineering_graph.py` — EKNodeType (30 kinds), EKRelationshipType
  (21 relations), EKNode/EKEdge, GraphVersion, RetrievalPlan/RetrievalStrategy,
  GraphQueryResult, GraphStats, NodeHistory/NodeHistoryEntry. Bounded lists.
- `app/services/engineering_graph_service.py` — EngineeringKnowledgeGraphService
  (node/edge upsert + dedup, bounded BFS neighborhood + dependencies, history,
  explain provenance, incremental versioning + supersede, stats, record_run
  ingestion, PG persistence with in-memory fallback, recover, database_url
  support, dispose).
- `app/services/knowledge_query_planner.py` — KnowledgeQueryPlanner
  (deterministic intent classification → minimal retrieval strategy).
- `alembic/versions/011_add_engineering_knowledge_graph.py` — migration 011.
- `app/api/v1/engineering_graph.py` — 6 bounded endpoints (query, node,
  history, neighborhood, explain, version).
- `app/cli_engineering_graph.py` — 5 CLI commands (query, explain, history,
  neighborhood, version) with `--json`.
- `frontend/src/lib/api/engineeringGraph.ts` — typed graph API client.
- `frontend/src/app/dashboard/engineering-graph/page.tsx` — graph explorer view.
- `scripts/demo_phase18.py` — demonstrations A–F.
- `tests/test_engineering_graph.py` — 37-test EKG suite.
- `docs/ENGINEERING_KNOWLEDGE_GRAPH.md` — design document.
- `workflow-status/PHASE18_COMPLETION_REPORT.md` — this file.

## 4. Files Modified

- `app/db/models.py` — EKNodeModel / EKEdgeModel / GraphVersionModel
  (edge payload column named `metadata_json` — SQLAlchemy reserves
  `metadata` on declarative models).
- `app/services/orchestration_service.py` — `_ingest_into_graph()` ingests a
  completed run (goals→plans→patches→tests→review→gate→notebook→consensus→
  memory) on completion.
- `app/services/reasoning_service.py` — `_sync_to_graph()` writes consensus /
  contradictions / notebook into the EKG after `analyze_run()`.
- `app/services/context_engine.py` — EKG context builder queried during
  `build_context()` (uses existing ranking/dedup/token budgets).
- `app/services/autonomy_service.py` — EKG getter + graph-aware REPLAN
  rationale (graph informs, never overrides deterministic validation).
- `app/main.py` — wired the engineering-graph router.
- `app/cli.py` — wired `graph` subcommands.
- `frontend/src/lib/api/client.ts` — exported `request()` helper.
- `frontend/src/app/dashboard/layout.tsx` — Knowledge Graph nav item.
- `tests/test_migration.py` — clean_db teardown drops `ekg_*` tables.
- `README.md`, `docs/ARCHITECTURE.md`, `docs/ORCHESTRATION.md`,
  `docs/CONTEXT_AND_MEMORY.md`, `workflow-status/PROJECT_STATE.md` — updated.

---

## 5. Engineering Knowledge Graph Architecture

```text
EngineeringKnowledgeGraphService  (Phase 18)
        │  nodes / edges / versions / traversal / provenance / history
        ▼
   ┌─────────┐  ┌──────────┐  ┌───────────┐  ┌──────────┐
   │Semantic │  │Repo      │  │Collaboration│ │Reasoning │
   │Graph    │  │Memory    │  │(handoffs)   │ │(consensus)│
   │(Ph 12)  │  │(Ph 13/14)│  │(Ph 15)      │ │(Ph 17)    │
   └─────────┘  └──────────┘  └───────────┘  └──────────┘
```

### Graph nodes

repository, folder, file, module, package, class, interface, function, method,
requirement, acceptance_criterion, implementation_plan, plan_version, goal,
patch, commit_candidate, test, test_suite, review_finding, quality_gate,
evidence, consensus, contradiction, notebook_entry, decision, run, agent,
repository_memory.

### Relationships

calls, imports, contains, depends_on, implements, tests, references, affects,
modifies, satisfies, created_during, produced_by, derived_from, supports,
contradicts, supersedes, uses_memory, validated_by, reviewed_by, approved_by.

### Temporal graph

Every node keeps a `NodeHistory` of versioned snapshots; `history(node_id)`
and `explain(node_id)` answer why-implemented / what-introduced /
which-repair / which-decision questions.

### Graph versioning

Incremental: each run/change bumps the version and records WHICH nodes/edges
changed (`updated_nodes` / `updated_edges`); `superseded_node_ids` keeps old
nodes for history. Never a full rebuild.

### Query planner

Deterministic intent classification (engineering_history, historical_fixes,
explain_implementation, affected_tests, find_related_requirements,
architecture_decisions, previous_solutions, notebook_entries, quality_evidence)
→ minimal retrieval strategy → bounded merged results (50 nodes cap).

### Context integration

ContextEngine queries the EKG alongside semantic graph / memory / consensus /
notebook / historical runs using existing ranking, dedup, and token budgets.

### Collaboration integration

CollaborativeReasoningEngine writes consensus / contradictions / notebook /
decisions into the graph and retrieves historical consensus.

### Autonomy integration

AutonomousExecutionController uses graph evidence for replanning rationale,
requirement coverage, historical repairs; the graph informs but never
overrides deterministic validation.

### PostgreSQL

Migration 011 — ekg_nodes / ekg_edges / ekg_versions, normalized + bounded
JSONB, with graceful in-memory fallback and restart recovery (`recover()`).

---

## 6. API

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/graph/query?q=&limit=` | planner-driven graph query |
| `GET /api/v1/graph/node/{id}` | node info + incident edges |
| `GET /api/v1/graph/history/{id}` | temporal history |
| `GET /api/v1/graph/neighborhood/{id}?depth=&max_nodes=` | bounded traversal |
| `GET /api/v1/graph/explain/{id}` | provenance + related evidence |
| `GET /api/v1/graph/version` | current version + stats + history |

Responses are bounded and evidence-only — never chain-of-thought or hidden
prompts.

## 7. CLI

```bash
devpilot graph query "which tests are affected by auth?"
devpilot graph explain <node_id>
devpilot graph history <node_id>
devpilot graph neighborhood <node_id> --depth 2
devpilot graph version
```

All commands support `--json`.

## 8. Frontend

`/dashboard/engineering-graph` — graph explorer with query box (planner-driven),
node inspector (type/status/version badges, outgoing/incoming edges,
provenance, related evidence, temporal history, payload), version stats cards
+ version history table, node distribution chips. Real APIs only, no mocks.

---

## 9. Demonstrations

`scripts/demo_phase18.py` — deterministic, no paid LLM required. All PASS in
both in-memory and live-PostgreSQL modes:

- **A** Requirement → Implementation → Tests → Review → Quality Gate lineage
- **B** Historical repair retrieval (explain)
- **C** Graph-powered ContextEngine retrieval (planner query)
- **D** Graph-powered replanning (requirement coverage evidence)
- **E** Graph version increment after repository change
- **F** Restart recovery preserving graph integrity (**54/54** persisted
  nodes recovered live-PG)

---

## 10. Security Review

- Repository content remains untrusted.
- Nodes/edges store bounded evidence + provenance only — never
  chain-of-thought, hidden prompts, or internal reasoning.
- API/CLI/frontend expose verified engineering evidence, decisions, and
  provenance.
- Regression test asserts explain/query responses never contain
  chain-of-thought markers.
- Query planner is deterministic — no LLM in the retrieval path.

---

## 11. Real Bugs Found & Fixed During the Build

1. **SQLAlchemy reserved `metadata`** — `EKEdgeModel.metadata` raised
   `InvalidRequestError` on import; renamed `metadata_json` across ORM +
   migration 011.
2. **Over-long stable node ids** — `_stable_id()` could exceed String(40)
   columns; `_with_session()` swallowed the error so node persistence
   silently no-oped while edges wrote (corrupting restart recovery).
   Fixed: bounded 40-char deterministic ids (head + sha1[:8]).
3. **Router prefix** — `/graph` → `/api/v1/graph` (v1 routers embed the
   full prefix).
4. **`GraphStats` field names** — CLI referenced nonexistent
   `by_type`/`active_node_count`; corrected to `node_types`/`relationship_types`.
5. **Planner intent shadowing** — bare `"history"` in `historical_fixes`
   shadowed `engineering_history`; reordered rules, moved keyword.
6. **Migration-test drop list** — `clean_db` teardown missing `ekg_*` tables
   → `DuplicateTableError` on re-upgrade (34 collateral failures); fixed.

---

## 12. Known Limitations

- Node payloads are bounded JSONB snapshots, not full artifact bodies
  (artifacts stay in their source stores).
- Query planning is lexical/deterministic — no semantic embeddings on the
  EKG (Phase 12 pgvector applies to code symbols).
- In-memory authoritative copy + PG mirror; heavy multi-process writes
  would need a lock strategy beyond the current optimistic versioning.
- `notebook_entries` intent requires explicit "notebook" phrasing.

---

## 13. Phase 19 Contract

```text
PHASE 18 COMPLETE: YES

FINAL TEST BASELINE:
    1392 passed / 18 skipped / 0 failed (live PostgreSQL 18.4)
    1362 passed / 48 skipped / 0 failed (in-memory fallback)

ENGINEERING KNOWLEDGE GRAPH: PASS
GRAPH VERSIONING:             PASS
QUERY PLANNER:                PASS
TEMPORAL GRAPH:               PASS
CONTEXT INTEGRATION:          PASS
AUTONOMY INTEGRATION:         PASS
POSTGRESQL:                   PASS

PHASE 19 READY: YES
```

**Suggested Phase 19 directions** (not started):

1. **Semantic EKG embeddings** — extend the planner with pgvector similarity
   over node payloads so retrieval merges lexical + semantic results.
2. **Cross-repository knowledge** — let the EKG span multiple repositories
   (per-repo namespaces, cross-repo `depends_on` / `satisfies` edges).
3. **Graph-backed test selection** — use EKG impact edges (patch→test) to
   drive the smart test selection promised in Phase 12d.
4. **Frontend graph visualization** — force-directed layout over the real
   `/graph/neighborhood` responses (currently a structured list view).
