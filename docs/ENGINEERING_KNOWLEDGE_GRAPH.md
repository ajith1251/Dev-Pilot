# Engineering Knowledge Graph (Phase 18)

> **Status**: Complete ✅
> **Last updated**: August 2, 2026
> **Test baseline**: 1392 passed / 18 skipped / 0 failed (live PostgreSQL 18.4)

---

## 1. Overview

Phase 18 unifies DevPilot's scattered engineering artifacts — code, requirements,
goals, plans, decisions, consensus, notebook, repository memory, evidence, and
historical runs — into **one reusable retrieval layer**: the Engineering
Knowledge Graph (EKG).

The EKG is a **higher abstraction** over the existing stores. It does not replace:

- Phase 12 Semantic Repository Graph (code symbols & relationships)
- Phase 13/14 Repository Memory (durable engineering knowledge)
- Phase 15 Collaboration (handoffs, decisions, conflicts)
- Phase 16 Autonomy (goals, plan versions, replans)
- Phase 17 Reasoning (consensus, contradictions, engineering notebook)

Instead, the EKG re-uses those entities as typed **NODES** and links them with
provenance-bearing, temporal **EDGES**, so the system can answer:

- *Why was this implemented?*
- *What introduced this symbol?*
- *Which repair fixed this issue?*
- *Which decision caused this architecture?*

```text
                Engineering Knowledge Graph
                               │
        ┌───────────┬───────────┼───────────┬──────────────┐
        ▼           ▼           ▼           ▼              ▼
      Code     Requirements  Goals     Decisions    Repository Memory
        │           │           │           │              │
        ▼           ▼           ▼           ▼              ▼
      Tests      Plans      Evidence     Consensus      Notebook
        │           │                       │              │
        ▼           ▼                       ▼              ▼
    Review      Quality Gate           Contradictions   Historical Runs
```

---

## 2. Architecture

```text
EngineeringKnowledgeGraphService  (Phase 18 — unified graph)
        │  nodes / edges / versions / traversal / provenance / history
        ▼
   ┌─────────┐  ┌──────────┐  ┌───────────┐  ┌──────────┐
   │Semantic │  │Repo      │  │Collaboration│ │Reasoning │
   │Graph    │  │Memory    │  │(handoffs)   │ │(consensus)│
   │(Ph 12)  │  │(Ph 13/14)│  │(Ph 15)      │ │(Ph 17)    │
   └─────────┘  └──────────┘  └───────────┘  └──────────┘
```

Consumers:

- **ContextEngine** — queries the EKG for graph-aware agent context
- **AutonomousExecutionController** — uses graph evidence for replanning
- **CollaborativeReasoningEngine** — writes consensus/contradictions/notebook
  into the graph; retrieves historical consensus
- **Planner / Coding / Testing / Repair / Reviewer** — consume via ContextEngine

---

## 3. Node Types (§3)

`EKNodeType` enum in `app/models/engineering_graph.py`:

| Category | Node types |
|---|---|
| Code | `repository`, `folder`, `file`, `module`, `package`, `class`, `interface`, `function`, `method` |
| Requirements | `requirement`, `acceptance_criterion` |
| Planning | `implementation_plan`, `plan_version`, `goal` |
| Implementation | `patch`, `commit_candidate` |
| Verification | `test`, `test_suite` |
| Review | `review_finding`, `quality_gate` |
| Reasoning | `evidence`, `consensus`, `contradiction`, `notebook_entry`, `decision` |
| Execution | `run`, `agent` |
| Knowledge | `repository_memory` |

Every node carries:

- stable **node_id** (bounded to 40 chars — fits `ekg_nodes.node_id`)
- **payload** — bounded, evidence-only metadata (never chain-of-thought)
- **provenance** — the evidence chain: run id, source store, originating agent

---

## 4. Relationships (§4)

`EKRelationshipType` enum — re-uses Phase 12 vocabulary and adds
engineering-lifecycle edges:

| Group | Relationships |
|---|---|
| Code structure | `calls`, `imports`, `contains`, `depends_on`, `implements`, `tests`, `references`, `affects`, `modifies` |
| Lifecycle | `satisfies`, `created_during`, `produced_by`, `derived_from`, `supports`, `contradicts`, `supersedes`, `uses_memory`, `validated_by`, `reviewed_by`, `approved_by` |

Edges are typed, directed, weighted (0–1) and carry their own provenance +
graph version. Adding an identical edge is a no-op (dedup).

---

## 5. Temporal Graph (§5)

Each node has a **temporal history** (`NodeHistory`): every mutation snapshots
the previous state tagged with its graph version. The graph answers temporal
questions like *"how was auth implemented historically?"* and *"which repair
fixed this issue?"* via:

- `history(node_id)` — temporal snapshots across versions
- `explain(node_id)` — provenance + related evidence (bounded, evidence-only)
- `neighborhood(node_id)` — bounded bidirectional traversal

---

## 6. Graph Versioning (§6)

Incremental, never a full rebuild:

- `increment_version(...)` bumps the version and records **which** nodes/edges
  changed (`GraphVersion.updated_nodes` / `updated_edges`)
- `superseded_node_ids` marks replaced nodes as `SUPERSEDED` (kept for history)
- Version records persist to `ekg_versions` (migration 011)
- `version_history(limit)` and `current_version()` expose the timeline

---

## 7. EngineeringKnowledgeGraphService (§7)

`app/services/engineering_graph_service.py` responsibilities:

- node / edge creation (upsert by stable id, dedup)
- graph updates & versioning (incremental)
- graph traversal (`neighborhood`, `dependencies`)
- provenance (`explain`)
- historical traversal (`history`)
- graph statistics (`stats`)
- run ingestion (`record_run`) — links goals → plans → patches → tests →
  review → gate → notebook → consensus → memory for every completed run
- PostgreSQL persistence with graceful in-memory fallback
- restart recovery (`recover`)

Stable node ids are **deterministic** (`PREFIX::source_type::source_ref::name`)
so re-ingesting the same entity upserts instead of duplicating. Long ids are
truncated to a bounded 40-char form (readable head + deterministic digest).

---

## 8. KnowledgeQueryPlanner (§8)

`app/services/knowledge_query_planner.py` classifies a user query and selects
the **minimum required retrieval strategy** — 100% deterministic, no LLM:

| Intent | Strategy | Example |
|---|---|---|
| `engineering_history` | HISTORY | "engineering history timeline for auth" |
| `historical_fixes` | HISTORY | "which repair fixed this issue?" |
| `explain_implementation` | KNOWLEDGE_GRAPH | "explain the auth implementation" |
| `find_related_requirements` | KNOWLEDGE_GRAPH | "requirements related to auth" |
| `affected_tests` | SEMANTIC_GRAPH | "which tests are affected by auth?" |
| `architecture_decisions` | KNOWLEDGE_GRAPH | "architecture decisions for billing" |
| `previous_solutions` | REPOSITORY_MEMORY | "how was this solved before?" |
| `notebook_entries` | NOTEBOOK | "notebook entries for run X" |
| `quality_evidence` | CONSENSUS | "quality evidence for auth" |

Results are merged, ranked, and bounded (`MAX_QUERY_RESULTS` = 50 nodes).

### Semantic merge (Phase 19)

`retrieve()` also runs a **semantic pass** as a recall booster: node payloads
are embedded into a fixed-dimension vector space (deterministic hashed
word/stem/trigram provider — no API) and the query's vector is compared with
cosine similarity. Semantic hits are merged into the lexical set within the
same `MAX_QUERY_RESULTS` bound; the flag `semantic_used` / `semantic_matches` /
`semantic_top_score` report whether the semantic pass contributed.

- The semantic pass is NOT restricted to the plan's inferred kinds (lexical
  intent kinds are a precision filter — a "memory" query plans
  REPOSITORY_MEMORY but the relevant node may be a REQUIREMENT).
- The index is derived deterministically from node text, so restart recovery
  is exact even without a pgvector mirror; when pgvector is available the
  vectors are mirrored to `ekg_embeddings` (migration 012).

### Impact-edge test selection (Phase 12d closure)

`EngineeringKnowledgeGraphService.select_tests_for_changes(changed_files)`
drives **smart test selection from graph evidence** — the last Phase 12d
roadmap promise. For each ingested run, `record_run` records which test files
ran on the TEST_SUITE node payload (`test_files`) and links the chain
`PATCH --MODIFIES--> FILE` + `PATCH --VALIDATED_BY--> TEST_SUITE`. Selection
walks those impact edges: changed file → FILE → reverse MODIFIES → PATCH →
VALIDATED_BY → TEST_SUITE → `test_files`. Deduplicated, bounded, and
gracefully empty (never raises) when there is no evidence.

Consumers:

- **Autonomy** — `AutonomousExecutionController._select_impact_tests()`
  queries the EKG first (preferring the orchestrator's graph instance, the
  one that actually ingests runs), falling back to an injected
  semantic-graph `TestSelectionService`, then `[]`. The lazy per-repo
  re-index cache was removed.
- **Orchestrator test stage** — `_stage_testing()` appends EKG-selected test
  files to the pytest candidate args (reason suffix `| EKG impact-selected
  tests: …`) when evidence exists; without evidence the full discovered
  suite runs unchanged.

---

## 9. Provenance (§9)

Every node retains its evidence origins — never lost:

```text
AuthService
  Evidence:
    • Requirement R4
    • Plan Version 2
    • Coding Agent
    • Review Finding
    • Quality Gate
    • Memory Entry
    • Run #82
```

`explain(node_id)` returns exactly this: the node's provenance map + bounded
related evidence (incoming/outgoing edges with their relationship).

---

## 10. Historical Engineering Graph (§10)

`record_run(run, reasoning_outcome)` ingests every completed run:

- run → repository (`REFERENCES`)
- requirements (bounded, first 10)
- implementation plan
- patch → files (`MODIFIES`)
- test suite (`VALIDATED_BY` from patch)
- repair patch
- review findings
- quality gate (`APPROVED_BY` from patch)
- consensus (`PRODUCED_BY` from run)
- contradictions
- engineering notebook (`PRODUCED_BY` from run)

Idempotent: re-ingesting the same run upserts nodes and dedups edges.

---

## 11. Retrieval (§11)

Reusable graph queries (all bounded, evidence-only):

- "Explain this implementation" → `explain` + `query`
- "Find related requirements" → `query` (REQUIREMENT kinds)
- "Find historical fixes" → `query` (HISTORY strategy)
- "Find affected tests" → `query` (SEMANTIC_GRAPH strategy)
- "Find architecture decisions" → `query` (DECISION/GOAL kinds)
- "Find engineering history" → `history` / `query` (HISTORY)
- "Find previous successful solutions" → `query` (REPOSITORY_MEMORY)
- "Find notebook entries" → `query` (NOTEBOOK)
- "Find quality evidence" → `query` (CONSENSUS/QUALITY_GATE)

---

## 12. ContextEngine Integration (§12)

`ContextEngine` queries the EKG alongside the semantic graph, repository memory,
consensus, notebook, and historical runs — using the existing ranking,
deduplication, and token budgets. Graph evidence flows into `AgentContext`
without changing the bounded-context contract.

---

## 13. Collaboration Integration (§13)

`CollaborativeReasoningEngine` writes consensus, contradictions, notebook, and
decisions into the graph (via `_sync_to_graph`), and the graph serves historical
consensus back to reasoning. Handoffs/decisions remain in the collaboration
store; the EKG links them to runs and evidence.

---

## 14. Autonomous Integration (§14)

`AutonomousExecutionController` uses the graph for:

- replanning rationale (graph evidence in `_decide` / REPLAN)
- requirement coverage
- historical engineering decisions & repairs
- graph-aware context
- impact-aware execution

The graph **informs** decisions but never overrides deterministic validation
(quality gate, budget, state machine).

---

## 15. PostgreSQL (§15)

PostgreSQL only — no Neo4j, no Redis Graph. Migration `011` adds three
normalized tables plus bounded JSONB:

| Table | Purpose |
|---|---|
| `ekg_nodes` | graph entities (node_id unique, node_type, payload, provenance, status, graph_version) |
| `ekg_edges` | typed directed relationships (edge_id unique, source/target, relationship, weight, metadata_json, provenance, graph_version) |
| `ekg_versions` | incremental version records (version unique, run_id, updated_nodes/edges, superseded_node_ids) |
| `ekg_embeddings` (012) | node_id → vector(256) + model; **only created when the pgvector extension is available** (guarded migration, mirrors 005). The in-memory semantic index is authoritative; this is a durable copy. |

Indexes cover node_type, source, name, version, edge source/target, and the
(source, target, relationship) triplet.

---

## 16. API (§16)

Bounded, evidence-only endpoints under `/api/v1/graph`:

| Endpoint | Purpose |
|---|---|
| `GET /graph/query?q=&limit=` | planner-driven graph query |
| `GET /graph/node/{id}` | node info + incident edges |
| `GET /graph/history/{id}` | temporal history |
| `GET /graph/neighborhood/{id}?depth=&max_nodes=` | bounded traversal |
| `GET /graph/explain/{id}` | provenance + related evidence |
| `GET /graph/version` | current version + stats + history |

Responses expose only verified engineering evidence, decisions, and provenance
— never chain-of-thought or hidden prompts.

---

## 17. CLI (§17)

```bash
devpilot graph query "which tests are affected by auth?"
devpilot graph explain <node_id>
devpilot graph history <node_id>
devpilot graph neighborhood <node_id> --depth 2
devpilot graph version
```

All commands support `--json` output.

---

## 18. Frontend (§18)

`/dashboard/engineering-graph` provides:

- query box (planner-driven) + result list
- node inspector: type/status/version badges, outgoing/incoming edges,
  provenance, related evidence, temporal history, payload
- graph version stats cards + version history table
- node distribution chips
- **force-directed neighborhood view**: select a node (in the result list,
  inspector, or the graph itself) to expand its 1–3 hop neighborhood on a
  shared SVG simulation canvas — pan/zoom/drag, color-by-type, size-by-degree,
  hover tooltip, depth selector, reload/reset-view/clear controls. Backed by
  `GET /api/v1/graph/neighborhood/{id}?depth=&max_nodes=`.

The force-directed canvas lives in
`frontend/src/components/graph/ForceDirectedGraph.tsx` (exported
`ForceGraph`, `hexFor`, `nodeTypeLabel`, `truncate`, `VizNode`, `VizEdge`);
it is shared with the organization-graph page.

Uses only the real `/api/v1/graph/*` endpoints — no mock data.

---

## 19. Security (§19)

- Repository content remains untrusted
- Nodes/edges store **bounded evidence only** — never chain-of-thought, hidden
  prompts, or internal reasoning
- API/CLI/frontend expose verified engineering evidence, decisions, provenance
- A regression test asserts explain responses never contain chain-of-thought
  markers

---

## 20. Testing (§20)

`tests/test_engineering_graph.py` (47 tests live-PG, 45 no-PG):

- node creation (stable ids, upsert, bounded ids)
- edge creation / relationship dedup
- traversal (bounded BFS, dependencies)
- versioning (monotonic, supersede, history, stats)
- history / explain provenance
- query planner (intent classification)
- PostgreSQL persistence (record_run round-trip + restart recovery)
- API endpoints (query/node/history/neighborhood/explain/version + 404 + security)
- CLI commands
- impact-edge test selection (walk, dedupe/bounds, empty graph, record_run
  payload persistence)
- integrations (ContextEngine, idempotent ingestion)
- regression (evidence-only, bounded results)

---

## 21. Demonstrations

`scripts/demo_phase18.py` (deterministic, no paid LLM):

- **A** Requirement → Implementation → Tests → Review → Quality Gate
- **B** Historical repair retrieval (explain)
- **C** Graph-powered ContextEngine retrieval
- **D** Graph-powered replanning
- **E** Graph version increment after repository change
- **F** Restart recovery preserving graph integrity (54/54 nodes live-PG)
- **G** Semantic retrieval (Phase 19) — a query with NO lexical name overlap
  still surfaces the node whose payload matches (lexical + cosine merge)
- **H** EKG-driven smart test selection (Phase 12d closure) — a changed file
  recovers its tests via patch → test impact edges; unknown files → empty
- **I** Cross-repository knowledge namespaces (Phase 19C) — three repos with
  explicit `link_repositories` edges; org-scope merge vs local-scope
  isolation; `cross_repository_traversal` hops across repo boundaries

```bash
python scripts/demo_phase18.py            # in-memory
python scripts/demo_phase18.py            # with TEST_DATABASE_URL set → live-PG
```

> Note: demo H's test-selection assertions can fail against an accumulated
> shared PG where historical TEST_SUITE rows from earlier runs bleed into the
> selection; it passes on a fresh graph. Demo I is idempotent.

---

## 22. Limitations

- Node payloads are bounded JSONB snapshots, not full artifact bodies (artifacts
  remain in their source stores)
- Semantic retrieval uses a deterministic hashed word/trigram provider by
  default (no API); production deployments can swap in OpenAI embeddings via
  `EMBEDDING_PROVIDER=openai`. The `ekg_embeddings` pgvector mirror (012) is
  dimension-locked to vector(256) — match `EMBEDDING_DIMENSION` if overridden.
- In-memory authoritative copy + PG mirror; heavy multi-process writes would
  need a lock/versioning strategy beyond the current optimistic versioning
- `notebook_entries` intent requires explicit "notebook" phrasing (kept distinct
  from generic history)
