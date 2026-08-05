# DevPilot Project State

> **Last updated**: August 5, 2026 (session 38 — Phase 20 COMPLETE: workstream E extra test-framework parsers)
> **Current Phase**: Phase 20 COMPLETE ✅ — Workstream E DONE Session 38: dedicated test-result parsers for **unittest** (JUnit-style XML), **Vitest** (JSON) and **Jest** (JSON) in `backend/app/testing/parsers/` (`UnittestXMLParser` / `VitestJsonParser` / `JestJsonParser`), wired into the `TestingService` chain before the generic fallback; 12 new tests; full suite **1696 passed / 18 skipped / 1 failed**. (Earlier: B1 DONE Session 37: `DEVPILOT_GEMINI_TIER` free|paid + optional `DEVPILOT_GEMINI_PAID_MODELS` — paid tier keeps `GEMINI_API_KEY` (same key format, billing attached in AI Studio), disables free-tier daily-quota failover + 24h exhaustion markers, fails fast on genuine billing/quota errors, still retries transient 429s; `GET /api/v1/providers/config` exposes `data.gemini.{tier,paid_models}`, `POST /api/v1/providers/test` returns `gemini_tier`/`gemini_models`; 12 new tests. B2 DONE Session 34: `Capability` enum + `LLMConfig.capability` + `DEVPILOT_LLM_PROVIDER_FALLBACKS` typed chains — each agent stage routes through its own provider list. B3 DONE Session 35: mid-stream token-loss failover — `chat_stream` resumes a stream that drops after delivering tokens on the next provider with the partial output as continuation context, bounded by `DEVPILOT_PROVIDER_STREAM_RESUME_MAX`). Phase 20 Workstream D COMPLETE (Session 36: `/dashboard/organization-graph` migrated onto `InteractiveGraph.tsx` + timeline diff + live WS; legacy `ForceDirectedGraph.tsx` deleted; pure mappers in `frontend/src/lib/graph/orgGraphModel.ts`; frontend vitest 49 passed (7 files), `next build` EXIT=0). Phase 20 slices A1–A6 DONE: `RunSource.repositories` + orchestrator materialization through `OrganizationKnowledgeGraphService.acquire_and_link_repositories` (A1+A2, commit `0954604`), planner org-scope context for multi-repo runs (A3, Session 29, commit `895dad5`), per-repo scope enforcement (A4, Session 31, commit `e1fc08e`), per-repo EKG ingestion (A5, Session 32 — `record_run_across_namespaces` ingests each per-repo patch into its own namespace + cross-namespace run links, `RepositoryPatchResult.changed_files`, missing-`await` bug fixed in `_validate_single_repo_patch`), dashboard aux-repo + run-detail multi-repo surface (A6, Session 33 — `_sanitize_run` exposes `auxiliary_repositories` + `repo_validation`, `CreateRunModal` aux-repo editor, Repository Validation card). Prior: Phase 19C COMPLETE ✅ — interactive EKG visualization (Session 26), multi-repo remote acquisition + org-graph UI wiring + org-scope queries (Session 27, commit `1644fb3`), demo-H stale-PG fix (`select_tests_for_changes` scoping, commit `2cc929b`). Earlier: Phase 19B COMPLETE ✅ (multi-provider failover), Phase 18 COMPLETE + Phase 19 items — EKG ✅, semantic EKG retrieval ✅, EKG-driven test selection (Phase 12d closure) ✅
> **Total tests**: **1696 passed / 18 skipped / 1 failed** on the full deterministic live-PG suite (`-m "not live"`; the 1 failure is the pre-existing `test_wrapper_skips_cleanly_without_provider` env quirk — the `.env` Gemini key means the wrapper subprocess runs live). Organization-graph suite: **60 passed** (incl. multi-repo acquisition; roundtrip test idempotent against accumulated PG data). Phase 20: **53 new tests** — A1+A2: 10 (`test_phase20_multi_repo_run.py`), A3: 7 (3 engine-level in `test_organization_graph.py` + 4 orchestrator-level in `test_phase20_multi_repo_run.py`), A4: 21 (`test_phase20_repo_scope.py`), A5: 15 (`test_phase20_repo_ingestion.py` — 13 ingestion + 2 run-detail API surface), A6 frontend: 2 (`frontend/src/lib/api/client.test.ts`). Phase 20B: **29 new tests** — B1: 12 (5 paid-tier provider in `test_llm_providers.py` + 6 Gemini tier config + 1 router `config_snapshot`), B2: 12 (7 router capability fallbacks + 4 config parsing + 1 planner wiring), B3: 5 (4 stream-resume behaviour + 1 config parse; `test_provider_router.py` now 60). Phase 20E: **12 new tests** — `TestUnittestXMLParser` (4) + `TestVitestJsonParser` (4) + `TestJestJsonParser` (4, incl. service-chain order) in `test_testing.py`. `scripts/demo_phase20.py` demos A–G ALL PASS.
> **Live run-API validation**: `scripts/verify_api_durability.py --live` runs ONE real `execute_run` through the HTTP API (`POST /api/v1/runs`) against Gemini + live PG — all 11 stages flow, runs/handoffs/consensus persist via PostgresRunStore, restart recovery rehydrates; surfaced + fixed two raw-path bugs (INITIALIZING→ACQUIRING_REPOSITORY advance, `_stage_analysis` await)
> **Semantic EKG retrieval (Phase 19)**: KnowledgeQueryPlanner merges lexical + cosine retrieval over node payloads (deterministic hashed word/trigram provider, no API) within existing bounds; optional pgvector mirror via migration 012; demo G PASS in-memory + live-PG
> **EKG-driven test selection (Phase 12d closure)**: smart test selection driven by graph evidence — `select_tests_for_changes()` walks patch → test impact edges (FILE ← MODIFIES ← PATCH → VALIDATED_BY → TEST_SUITE); orchestrator test stage targets pytest candidates with EKG-selected tests; autonomy replans query the EKG first (fallback to injected selector); lazy per-repo cache removed; demo H PASS in-memory + live-PG
> **Live-LLM (Gemini)**: `scripts/demo_phase17.py --live` runs end-to-end on the free tier — 5 patches generated & applied, 3 real consensus records + 5 contradictions in Demonstration A, autonomy goal surfaces 3 consensus topics; multi-model daily-quota failover + 24h TTL recovery keep long-lived processes alive across midnight resets. Full report: `docs/GEMINI_API_KEY_REPORT.md`
> **Total files**: 300+
> **Frontend goal view**: Live status via **WebSocket push** (polling fallback), decision timeline, plan-version diffing, budget usage bars, escalation queue (resume/cancel/input) wired to `/v1/autonomy`
> **Frontend EKG view**: Graph explorer at `/dashboard/engineering-graph` — query box (planner-driven), node inspector (type/status/version, edges, provenance, related evidence, temporal history, payload), version stats + history table, node distribution chips, **interactive React Flow view** (shared `InteractiveGraph` engine — select a node to inspect/expand 1-3 hops, filters/search, timeline diff, live WS); `/dashboard/organization-graph` runs the same React Flow engine (Session 36); real `/api/v1/graph/*` + `/api/v1/graph/org/*` endpoints only
> **Database**: PostgreSQL 18.4 — 27 tables (+1: provider_metric_snapshots via migration 014; earlier +3: ekg_nodes, ekg_edges, ekg_versions via migration 011)
> **Recovery**: Automatic startup recovery — stale runs marked FAILED, recoverable runs logged
> **Code Intelligence**: Semantic graph with 18 symbol kinds, 12 relationship types, bounded traversal, impact analysis; tree-sitter parsers for Java, Go, Rust, C/C++, C#, Kotlin, Swift, Ruby, PHP; PostgreSQL graph persistence + pgvector embeddings
> **Context Engineering**: ContextEngine with deterministic ranking, deduplication + provenance merging, token budgeting, provenance tracking; AgentContext with 18+ evidence categories including agent handoffs
> **Repository Memory**: Durable knowledge memory with VERIFIED/PROVISIONAL/STALE/INVALID lifecycle; symbol-based invalidation; PostgreSQL persistence via migration 004; verified-knowledge promotion from approved runs
> **Orchestration Wiring**: ContextEngine wired into OrchestrationService at 4 stage boundaries (plan→code→repair→review); agent_context flows through PlanningService, RepairService, ReviewService
> **Multi-Agent Collaboration**: Structured handoffs (Planner→Coding→Testing→Repair→Reviewer→Quality Gate), decision records, evidence conflict detection, cross-agent notes + handoff context, verified memory promotion, restart recovery; API/CLI/frontend observability
> **Collaborative Reasoning (Phase 17)**: CollaborativeReasoningEngine above the collaboration store — evidence-driven confidence (deterministic-outranks-claims), contradiction detection (claim-vs-test / claim-vs-gate / scope-vs-impact), per-topic consensus, engineering notebook (accepted/rejected decisions, conflicts, timeline); reviewer context carries consensus; autonomy REPLAN rationale enriched with consensus topics; API `/runs/{id}/consensus|contradictions|notebook|reasoning`, CLI `consensus|conflicts|notebook <run>`, frontend Phase 17 view; migration 010 persistence + restart recovery
> **Engineering Knowledge Graph (Phase 18)**: EngineeringKnowledgeGraphService — unified temporal layer above code/requirements/goals/plans/evidence/consensus/notebook/memory/runs; typed nodes + provenance-bearing temporal edges (calls/imports/contains/depends_on/validated_by/approved_by/produced_by/supersedes…); incremental graph versioning (never full rebuild, superseded kept for history); deterministic KnowledgeQueryPlanner (intent classification → minimal strategy); run ingestion links goal→plan→patch→tests→review→gate→notebook→consensus→memory idempotently; PostgreSQL persistence (migration 011: ekg_nodes/ekg_edges/ekg_versions) with in-memory fallback + restart recovery; API `/api/v1/graph/{query,node,history,neighborhood,explain,version}`, CLI `graph {query,explain,history,neighborhood,version}` with --json, frontend graph explorer; ContextEngine queries the EKG, autonomy REPLAN uses graph evidence, reasoning syncs consensus/contradictions/notebook into the graph; demos A–F in scripts/demo_phase18.py
> **Multi-Provider Failover (Phase 19B)**: `ProviderRouter` in `app/llm/router.py` wraps every LLM call behind `llm_factory.get_provider()` via a `RoutedProvider` facade — deterministic priority chain (`[DEVPILOT_LLM_PROVIDER]` + `gemini, openai, anthropic, openrouter, ollama, fake`), per-provider `CircuitBreaker` (closed→open→half-open), bounded `RetryStrategy` (exponential backoff, recoverable-only), `FailureKind` classification (permanent quota → fail over immediately), streaming failover pre-first-token, rolling health windows (degraded <50%, unhealthy <30%) with latency EMA/uptime/retries/failovers, recursive secret redaction at the router boundary (`app/llm/redaction.py`), best-effort PG metric snapshots (migration `014`: provider_metric_snapshots), API `/api/v1/providers/{health,metrics,config,metrics/history,test}`, CLI `providers/provider-health/provider-metrics/provider-test`, dashboard `/dashboard/providers`; never silent — `AllProvidersFailedError` carries per-provider failures; 43 deterministic tests (no paid LLM). Full design: `docs/MULTI_PROVIDER_ROUTING.md`, report: `workflow-status/PHASE19B_COMPLETION_REPORT.md`

## 1. Project Overview

**DevPilot** is an Autonomous Multi-Agent Software Engineering Platform. It accepts a repository and a development task, then coordinates specialized AI agents to perform the full software engineering lifecycle.

**Phases 1-11 complete:**
- Phase 1 — Foundation (FastAPI, agents, LLM abstraction)
- Phase 2 — Repository Intelligence Engine (deterministic repo analysis)
- Phase 3 — GitHub Read Integration (remote repo acquisition + analysis)
- Phase 4 — Issue Analysis & Planning (StructuredRequirements → ImplementationPlan)
- Phase 5 — Code-Aware Repository Indexing & Hybrid RAG (index → retrieve → plan context)
- Phase 6 — Coding Agent & Safe Patch Engine (generate → validate → apply)
- Phase 7 — Test Agent & Controlled Execution Engine (plan → validate → execute → normalize)
- Phase 8 — Fix Agent & Bounded Repair Loop (diagnose → propose → validate → apply → verify)
- Phase 9 — Reviewer Agent & Deterministic Quality Gate (review → validate → gate)
- Phase 10 — End-to-End Multi-Agent Orchestration (run → coordinate → decide)
- **Phase 11 — Persistent State + PostgreSQL Run Management** ✅

## 2. Phase 11 Summary

Phase 11 transforms DevPilot's orchestration from process-local in-memory storage to durable PostgreSQL-backed persistence with:

- **PostgresRunStore** — Full async implementation of the RunStore Protocol with optimistic concurrency, event ordering, and artifact storage
- **6 database tables** — runs, tasks, repositories, stage_results, run_events, artifacts
- **Alembic migrations** — Versioned, repeatable, deterministic schema management
- **Async RunStore Protocol** — All storage operations async for database compatibility
- **44 new tests** — 41 InMemoryRunStore contract tests + 21 PostgreSQL integration tests
- **Recovery & Resume** — Startup recovery scan, stale run marking, safe resume
- **697 total tests** — +44 from Phase 10, all passing
- **Frontend API client** — Centralized typed client for all orchestration endpoints
- **Credential rotation** — PostgreSQL credentials rotated during Phase 11 setup
- **Auto-recovery on startup** — `main.py` lifespan now automatically runs recovery check
- **In-memory state cleanup** — RepairService cleans up session fingerprints; TestingService unregisters workspaces

## 3. Architecture Overview

```text
GitHub Issue / User Task
    ↓
Create DevPilotRun → Persisted in PostgreSQL
    ↓
OrchestrationService (Phase 10/11)
    ↓
    ├── Repository Analysis (Phase 2/3)
    ├── Task Analysis (Phase 4)
    ├── Planning (Phase 4) + Plan Validation
    ├── Code Retrieval (Phase 5)
    ├── Coding Agent → Patch Validator → Safe Patch Engine (Phase 6)
    ├── Test Agent → Execution Engine (Phase 7)
    ├── Repair Loop (Phase 8) — bounded retries
    ├── Reviewer Agent → Quality Gate (Phase 9)
    └── Final Result → Persisted in PostgreSQL
            ↓
        Recovery / Resume on restart (Phase 11)
```

## 4. Database Schema

| Table | Purpose | Key Features |
|-------|---------|-------------|
| `runs` | Core run state | Versioned concurrency, JSONB for flexible data, indexes on run_id/status |
| `tasks` | Task identity | GitHub issue references, FK from runs |
| `repositories` | Repository metadata | URL, owner, name, local ref |
| `stage_results` | Per-stage lifecycle | FK CASCADE, composite index on (run_id, stage) |
| `run_events` | Orchestration events | Monotonic sequence, unique (run_id, sequence), FK CASCADE |
| `artifacts` | Artifact metadata | JSONB content, composite index on (run_id, type) |

## 5. Test Results

```text
~708 passed, ~14 skipped in ~40s
```

| Test File | Tests | Phase | Description |
|-----------|-------|-------|-------------|
| `test_agents.py` | 8 | 1 | Base agent + registry |
| `test_analyzer_tools.py` | 20 | 1 | Phase 1 analysis tools |
| `test_github_integration.py` | 32 | 3 | Phase 3 GitHub (mocked) |
| `test_github_service.py` | 6 | 3 | GitHub URL parsing |
| `test_health.py` | 3 | 1 | Health endpoint |
| `test_issue_analyzer.py` | 14 | 1 | Issue Analyzer |
| `test_llm_base.py` | 3 | 1 | LLM abstraction |
| `test_repo_analyzer.py` | 11 | 1 | Repo analyzer |
| `test_repository_intelligence.py` | 60+ | 2 | Intelligence engine |
| `test_planner.py` | 17 | 4 | Planner Agent |
| `test_plan_validator.py` | 17 | 4 | PlanValidator |
| `test_planning_service.py` | 11 | 4 | PlanningService |
| `test_planning_workflow.py` | 12 | 4 | Workflow |
| `test_planning_api.py` | 11 | 4 | API endpoints |
| `test_planning_cli.py` | 6 | 4 | CLI commands |
| `test_code_intelligence.py` | 57 | 5 | Indexing + retrieval |
| `test_coding.py` | 43 | 6 | Coding + patches |
| `test_testing.py` | 79 | 7 | Execution + testing |
| `test_repair.py` | 77 | 8 | Fix + repair loop |
| `test_review.py` | 66 | 9 | Review + quality gate |
| `test_database.py` | 29 | DB | Database infrastructure |
| `test_orchestration.py` | 50 | 10 | End-to-end orchestration |
| `test_run_store_contract.py` | 64 **(+2)** | **11** | **New: PostgresRunStore `list_with_total_and_stats` tests** |
| `test_run_store_property.py` | **9 (new)** | **11H** | **New: Hypothesis property-based `count_runs` tests** |
| `test_api_contract.py` | 64 **(+8)** | **11** | **New: Seeded exact-number total_count tests** |
| `test_code_intelligence_phase12.py` | **101** | **12** | **Semantic graph, parsers, impact analysis, incremental indexing, graph retrieval** |
| `test_postgres_graph_persistence.py` | **19** | **12** | **Graph save/load/delete/list persistence** |
| `test_agent_graph_integration.py` | **23** | **12** | **GraphAwareRetriever agent context integration** |
| `test_tree_sitter_parsers.py` | **23** | **12** | **Tree-sitter parsers: Java, Go, Rust, C/C++, C#, Kotlin, Swift, Ruby, PHP** |
| `test_context_engine.py` | **30 (new)** | **13** | **ContextEngine ranking, dedup, budgeting, pipeline** |
| `test_repository_memory_service.py` | **40 (new)** | **13** | **RepositoryMemoryService CRUD, query, invalidation, stats (AsyncMock-based)** |
| `test_context_engine_integration.py` | **26 (new)** | **14** | **ContextEngine integration with mocked services (graph, memory, run history)** |
| **Total** | **~1130** | **All** | **+70 Phase 13, +26 Phase 14, full regression verified** |

## 6. Quick Start

```bash
cd DevPilot/backend
pip install -r requirements.txt

# Verify PostgreSQL connectivity
python -m app.cli db-check

# Run full test suite
python -m pytest -q --tb=no
# Expected: 1100 passed, 18 skipped, 0 failed

# Apply migrations
PYTHONPATH=backend alembic upgrade head

# Start API server (auto-runs recovery check on startup)
uvicorn app.main:app --reload

# Frontend dashboard
cd DevPilot/frontend
npm install && npm run dev
```

On startup, the application automatically checks for:
- Runs left in `PENDING` or `RUNNING` state from a previous session
- Marks runs older than 60 minutes as `FAILED` (stale)
- Logs recoverable run IDs for the user to resume manually

## 7. Phase 12 Summary

Phase 12 transforms DevPilot from file/chunk-oriented repository understanding into a **structural semantic code intelligence system** with:

- **SemanticRepositoryGraph** — In-memory directed graph with 18 symbol node kinds and 12 relationship types, bounded BFS traversal with cycle protection
- **PythonSymbolParser** — Full AST-based parser extracting classes, methods, functions, imports, decorators, calls, inheritance
- **TypeScriptJSParser** — Regex + brace-matching parser for TS, JS, TSX, JSX (classes, interfaces, types, enums, async functions, imports)
- **CodeIntelligenceService** — Repository-level orchestrator for indexing, symbol lookup, and graph access
- **ImpactAnalysisService** — Evidence-backed impact analysis with risk levels (CRITICAL through NONE), test discovery, and affected file reporting
- **IncrementalIndexer** — SHA-256 content hash-based change detection and partial re-indexing
- **GraphAwareRetriever** — Phase 5 extension with graph neighborhood expansion and LLM agent context formatting
- **PostgreSQL persistence** — 3 new tables (code_symbols, code_relationships, repository_indexes) via Alembic migration 003; `PostgresRunStore.save_graph()/load_graph()/delete_graph()` with 500-row chunked batch inserts
- **8 API endpoints** — Index management, symbol queries, impact analysis, graph retrieval
- **6 CLI commands** — code-index, code-symbols, code-symbol, code-impact, code-retrieve, code-status
- **Frontend dashboard** — `/dashboard/code-intelligence` with index builder, graph stats, symbol browser, graph query
- **Tree-sitter parsers** — Java, Go, and Rust parsers using tree-sitter 0.26 (lazy-init singleton pattern, graceful degradation on parse errors)
  - Java: classes, interfaces, enums, records, methods, constructors, fields, imports, annotations, extends/implements
  - Go: functions, methods (with receiver), structs (with fields), interfaces (with method specs), grouped imports, const/var declarations
  - Rust: functions, structs (with fields), enums (with variants), traits, impl blocks (with methods), use declarations, const/static items, type aliases, module items
- **19 graph persistence tests** — All passing, covering save/load/delete/list round-trips with proper async mocking
- **23 tree-sitter parser tests** — All passing, covering all 3 new languages
- **162 Phase 12 tests** (97 + 19 persistence + 23 tree-sitter + 23 agent integration) — All passing

### Architecture Invariants

```
Static Analysis (Parsers)
    ↓
Semantic Graph (GraphNodes + GraphEdges)
    ↓
Retrieval / Impact Analysis
    ↓
Bounded Evidence
    ↓
Agents (via GraphAwareRetriever.get_agent_context)
```

## 8. Phase 13 Summary

Phase 13 upgrades DevPilot from ad-hoc per-agent context assembly into a **deterministic context engineering system** with:

### ContextEngine Pipeline

```
Task
 ↓
Task Understanding
 ↓
Repository Knowledge (Phase 2)
 ↓
Semantic Graph (Phase 12)
 ↓
Historical Run Memory (Phase 11)
 ↓
Repository Memory (Phase 13-C)
 ↓
ContextEngine (Phase 13-A/B)
 ↓
Rank → Deduplicate → Budget → Compress
 ↓
Agent-Specific Context (AgentContext)
 ↓
Planner / Coding / Test / Repair / Reviewer
```

### Key Components

- **AgentContext Model** — Canonical context structure with 17 evidence categories, provenance tracking, and token metrics
- **ContextBudget** — Configurable per-agent token allocation (5 agent types with distinct category priorities)
- **ContextEngine** — Central orchestrator with 9 context sources, deterministic ranking, content-hash deduplication, category-budgeted token allocation, and diagnostic explain mode
- **RepositoryMemoryService** — Full CRUD + query + invalidation service using `_with_session` pattern (same as PostgresRunStore)
- **RepositoryMemoryModel** — 10-column table, 7 indexes, memory lifecycle: PROVISIONAL → VERIFIED → STALE → INVALID
- **Alembic Migration 004** — `repository_memories` table, revises 003, cleanly extends Phase 11/12 schema

### Agent Integration

All 5 agents now consume `AgentContext` with graceful fallback:

| Agent | Input Field | Injection Point | Fallback |
|-------|-------------|----------------|----------|
| **Planner** | `PlannerInput.agent_context` | `repo_context_text` | `_get_graph_context(inp)` |
| **Coding** | `CodingAgentInput.agent_context` | `extra_context` in prompt | `_get_graph_context(plan, ctx)` |
| **Test** | `TestAgentInput.agent_context` | `_build_workspace_summary()` | Inline graph context |
| **Fix** | `FixAgentInput.agent_context` | `changed_file_context` | `_get_graph_context(diagnosis)` |
| **Reviewer** | `ReviewerAgentInput.agent_context` | `arch_context` | `_get_graph_context(ctx)` |

### API + CLI

| Type | Endpoint / Command | Description |
|------|-------------------|-------------|
| API | `POST /api/v1/context/build` | Build agent-specific context with metrics + explanation |
| API | `GET /api/v1/context/explain` | Diagnostic explanation of context selection |
| CLI | `devpilot context <repo> "<task>"` | Build context and display results |
| CLI | `devpilot context-explain <repo> "<task>"` | Show diagnostic explanation with provenance |

### Frontend Diagnostic View

- New **/devpilot-context** route in the AI Agents Atlas frontend
- Form: task, agent type (5 options), repo path, symbol names
- Results: 5 metrics cards, context sources breakdown, explanation, prompt preview
- Calls `POST /api/v1/context/build` on DevPilot API

### Orchestration Wiring (Phase 13 Extension)

ContextEngine is wired into `OrchestrationService.execute_run()` at 4 stage boundaries:

```
execute_run()
├── before PLANNING  → _build_agent_context(run, "planner")  → PlanningService.plan_from_task() → PlannerInput
├── before CODING    → _build_agent_context(run, "coding")   → CodingAgentInput
├── before REPAIR    → _build_agent_context(run, "repair")   → RepairService.run_repair() → FixAgentInput
└── before REVIEW    → _build_agent_context(run, "reviewer") → ReviewService.run_review() → ReviewerAgentInput
```

**Evidence gathered per boundary:**

| Source | Planner | Coding | Repair | Reviewer |
|--------|:-------:|:------:|:------:|:--------:|
| Task title | ✅ | ✅ | ✅ | ✅ |
| Plan text | — | ✅ | ✅ | ✅ |
| Requirements | ✅ | ✅ | ✅ | ✅ |
| Test failures | — | — | ✅ | ✅ |
| Repair history | — | — | ✅ | ✅ |
| Review findings | — | — | — | ✅ |
| Run ID | ✅ | ✅ | ✅ | ✅ |

**Design:** Lazy ContextEngine init, graceful None fallback, backwards-compatible (all `agent_context` params default to `None`).

**Files modified:**
- `orchestration_service.py` — `_get_context_engine()`, `_build_agent_context()`, injection at 4 boundaries
- `planning_service.py` — `agent_context` param on `plan_from_task()` + `_run_pipeline()` → `PlannerInput`
- `repair_service.py` — `agent_context` param on `run_repair()` → `FixAgentInput`
- `review_service.py` — `agent_context` param on `run_review()` → `ReviewerAgentInput`

### Test Results

```
Phase 13:      70/70 passed (30 ContextEngine + 40 memory service tests)
Phase 12:     166/166 passed (preserved)
Phase 1-11:   All preserved
Full suite:   1074 passed, 18 skipped, 0 failed
```

### Service Bug Fixes

| Bug | File | Fix |
|-----|------|-----|
| `model.version = RepositoryMemoryModel.version + 1` produces BinaryExpression | `repository_memory_service.py` | `model.version = (model.version or 0) + 1` |
| Dead import of `AgentContext` | `orchestration_service.py` | Removed unused import |

## 9. Phase 14 Summary

Phase 14 hardens remaining Phase 13 limitations and adds formal integration tests:

### Completed Work Items

| Item | Area | Status |
|------|------|--------|
| **JSONB `.overlap()` fix** | `repository_memory_service.py` | ✅ `.overlap()` → `or_() + .contains()` for SQLAlchemy JSONB compatibility |
| **requirements.txt update** | `requirements.txt` | ✅ Added `psycopg2-binary>=2.9.9` for Alembic sync driver |
| **CLI service injection** | `cli_context.py` | ✅ `_try_init_code_intelligence()` + `_try_init_memory_service()` with graceful None fallback |
| **Integration tests** | `test_context_engine_integration.py` | ✅ 26 tests: graph, memory, run history, full pipeline, provenance, graceful degradation |

### Test Results

```
Phase 14:      26/26 passed (ContextEngine integration tests)
Phase 13:      70/70 passed (preserved)
Phase 12:     166/166 passed (preserved)
Phase 1-11:   All preserved
Full suite:   1100 passed, 18 skipped, 0 failed in 32.33s
```

### Key Details

**JSONB Fix:** `symbol_names` stores a JSON array (`["AuthService", "TokenService"]`), not a JSON object. PostgreSQL's `@>` containment operator (`JSONB.contains()`) checks array membership, while `?|` (`JSONB.has_any()`) checks object keys only. Using `or_(*[col.contains([sym]) for sym in symbols])` provides correct overlap semantics for JSONB arrays.

**CLI Injection:** Both `run_context()` and `run_context_explain()` now wire real services into the ContextEngine. Graceful degradation: if `CodeIntelligenceService` or `RepositoryMemoryService` cannot initialize, CLI falls back to task-only context silently.

**Files created:**
- `tests/test_context_engine_integration.py` — 26 tests across 7 TestClasses

**Files modified:**
- `app/services/repository_memory_service.py` — JSONB fix (added `or_` import, replaced `.overlap()`)
- `requirements.txt` — Added `psycopg2-binary`
- `app/cli_context.py` — Added service injection helpers

### Remaining Limitations

1. **Provenance dedup merging** — `_deduplicate()` keeps higher-scored item but doesn't merge provenance lists
2. **Frontend context/memory view** — `/devpilot-context` route exists, could be enhanced with memory browsing
3. **No cross-agent context sharing** — Each agent gets independent context at stage boundaries
4. **Graph evidence in integration tests** — `_build_graph_context()` calls module-level function, not mock CIS

## 10. Future Phases

| Phase | Focus | Status |
|-------|-------|--------|
| **Phase 13** | Context Engineering, Repository Memory & Intelligent Agent Reasoning | ✅ Complete |
| **Phase 14** | Hardening, Integration Tests & Documentation | ✅ Complete |
| **Phase 15** | Multi-Agent Collaboration (handoffs, decisions, conflicts, memory promotion) | ✅ Complete |
| **Phase 16** | Autonomous Execution (goal tracking, dynamic replanning, safe termination) + dashboard goal view | ✅ Complete |
| **Phase 17** | Collaborative Reasoning & Evidence Consensus (confidence, contradictions, engineering notebook, reviewer consensus context, autonomy replan enrichment) | ✅ Complete |
| **Phase 18** | Engineering Knowledge Graph (unified temporal layer, graph versioning, query planner, provenance, PG persistence, API/CLI/frontend, demos A–F) | ✅ Complete |

## 10. Security Posture

## 10. Security Posture

- PostgreSQL role is non-superuser (LOGIN + CREATEDB)
- All credentials from `.env` (gitignored)
- Secret redaction in all logs, errors, API responses
- Application role credentials rotated during Phase 11 setup
- SQL injection protection via SQLAlchemy expression API
- Test database isolation (never modifies `devpilot_dev`)
- No database credentials exposed to frontend
- No secrets leaked through recovery logging (run IDs only)

## 11. Memory & Session State Management

| Component | Storage | Persistence | Cleanup |
|-----------|---------|-------------|---------|
| DevPilotRun (runs) | InMemoryRunStore / PostgresRunStore | ✅ PostgreSQL (when DATABASE_URL set) | N/A (DB-managed) |
| Run events | InMemoryRunStore / PostgresRunStore | ✅ PostgreSQL | N/A (DB-managed) |
| Repair fingerprints | Per-session Dict | ❌ In-memory only | ✅ Auto-cleaned after session ends |
| Testing workspace registry | Dict[workspace_id → root] | ❌ In-memory only | ✅ `unregister_workspace()` available |
| WorkspaceService workspaces | File system (temp dirs) | ❌ Ephemeral | ✅ `cleanup_workspace()` available |
| WebSocket connections | Dict[run_id → Set[WS]] | ❌ Live connections | ✅ Auto-closed on shutdown |
| Startup recovery | Lifespan event | ✅ Automatic | Marks stale runs as FAILED |

### Recovery Flow

```text
Backend startup (lifespan)
    ↓
DATABASE_URL configured? ── No ──→ InMemoryRunStore (no recovery needed)
    ↓ Yes
Create PostgresRunStore
    ↓
check_recovery()
    ├── find_recoverable_runs() → PENDING/RUNNING runs
    ├── mark_stale_runs(60 min) → FAILED
    └── Log results
    ↓
Ready to serve requests
```

---

## 10. Phase 15 Summary

Phase 15 added a structured **collaboration layer** between pipeline stages — the
orchestrator stays the sole coordinator, but at every stage boundary it creates
handoffs, records decisions, detects conflicts, and promotes shared memory.

- `app/models/collaboration.py` — AgentHandoff, RunDecision, EvidenceConflict, SharedRunContext
- `app/services/collaboration_service.py` — handoff CRUD, validation, conflict detection, decisions, recovery, memory promotion, redaction, metrics
- `app/api/v1/collaboration.py` + `app/cli_collaboration.py` — paginated collaboration API + `handoffs`/`decisions`/`collaboration` CLI commands
- Migration **006** (agent_handoffs, run_decisions, evidence_conflicts)
- Provenance dedup merging, frontend collaboration view, cross-agent context via `SharedRunContext.notes`
- Validated against **real PostgreSQL 18.4** (migration chain 001→006, 40 integration tests) plus deterministic E2E (happy path, repair path, restart recovery, conflict detection, memory promotion)

```
Phase 15 final baseline: 1210 passed, 18 skipped, 0 failed
```

---

## 11. Phase 16 Summary

Phase 16 adds **autonomous task execution** on top of the collaboration layer:
agents now run toward an explicit goal with deterministic decision-making,
versioned replanning, bounded repair budgets, and safe termination.

### Completed Work Items

| Item | Area | Status |
|------|------|--------|
| **ExecutionGoal state machine** | `app/models/autonomy.py` | ✅ CREATED → RUNNING → COMPLETED / FAILED / CANCELLED / WAITING_FOR_HUMAN, versioned checkpoints, budget caps (`ExecutionBudget`), bounded fields |
| **AutonomousExecutionController** | `app/services/autonomy_service.py` | ✅ `create_goal` / `start` / `pause` / `resume` / `cancel` / `recover` / `dry_run`, deterministic `_decide` (stuck → complete → repair → replan → escalate), versioned plan recording, replan loop protection, `BudgetManager` |
| **Stuck detector** | `app/services/autonomy_service.py` | ✅ Failing-test fingerprint, identical-plan-version, checkpoint-stall, evidence-loop detection; bounded evidence window (200) |
| **API** | `app/api/v1/autonomy.py` | ✅ `/v1/autonomy/run`, `/dry-run`, `/{goal_id}` status + progress + decisions, pause/resume/cancel/input |
| **CLI** | `app/cli_autonomy.py` | ✅ `autonomous run` / `status` / `dry-run` / control commands, JSON output mode |
| **Persistence** | `alembic/versions/007_add_autonomy.py` | ✅ Migration 007: execution_goals, plan_versions, autonomous_decisions, execution_checkpoints, human_escalations |
| **Budget gate fix** | `app/models/autonomy.py` + `app/services/autonomy_service.py` | ✅ Zero limits mean "disabled" (routed by `_decide`), not instantly-exhausted; loop gate only stops on global limits (`max_iterations` is the hard bound) |
| **Migration harness** | `tests/test_migration.py` | ✅ `clean_db` now drops the 5 Phase 16 tables; round-trip tests assert Phase 16 tables exist post-upgrade |
| **Goal list + escalation queue API** | `app/api/v1/autonomy.py` + `app/services/autonomy_service.py` | ✅ `GET /v1/autonomy` lists goals (in-memory + persisted, best-effort) with open escalations; powers the dashboard queue |
| **Dashboard goal view** | `web/src/App.jsx` + `web/src/styles.css` | ✅ Live goal status (silent 5s polling, stops on terminal states), decision history timeline, plan-version token diffing, repair/replan budget bars, escalation queue with Respond/Resume/Cancel/View |

### Architecture Invariants

- `max_iterations >= 1` is the ultimate termination bound — every non-terminal
  iteration increments `iterations_used`, so the loop always stops.
- `_decide` is deterministic and order-fixed: stuck → all-satisfied+gate →
  repair (while budget) → replan (while budget) → escalate.
- ESCALATE / STOP decisions are terminal; the loop exits immediately after.
- Repair/replan budgets are never fatally exhausted — `_decide` routes around
  them (REPAIR → REPLAN → ESCALATE).
- Human escalation is cooperative: WAITING_FOR_HUMAN → `provide_input()` resumes.

### Test Results

```
Phase 16:      95/95 passed (autonomy models, controller, API, CLI, WS — +9 autonomy-WS tests)
Migration:      9/9 passed (incl. Phase 16 table assertions)
Full suite:  1267 passed, 18 skipped, 0 failed
Frontend build:  ✅
Live-LLM demo:  scripts/demo_phase16.py — real execute_run proven end-to-end
```

### Key Files

- `app/models/autonomy.py` — ExecutionGoal, ExecutionBudget, IterationEvidence, FailureClass, AutonomousDecision, PlanVersion, ExecutionCheckpoint, HumanEscalation
- `app/services/autonomy_service.py` — AutonomousExecutionController, StuckDetector, BudgetManager, ActionReason, plan/evidence validation
- `app/api/v1/autonomy.py`, `app/cli_autonomy.py` — API + CLI surfaces
- `alembic/versions/007_add_autonomy.py` — migration 007
- `docs/AUTONOMOUS_EXECUTION.md` — full phase documentation
- `tests/test_autonomy_models.py`, `tests/test_autonomy_controller.py`, `tests/test_autonomy_api.py`, `tests/test_autonomy_cli.py` — 86 tests

### Remaining Limitations

1. **Autonomy ↔ collaboration wiring** — autonomous goals drive the pipeline
   independently; feeding collaboration handoffs/decisions back into goal
   evidence is a natural Phase 17 integration.
2. **Goal list page** — the queue is embedded in `/devpilot-context`; a dedicated
   goal-browser route with state filters is not yet built.
3. **Run-from-UI** — goals are started from the dashboard, but criteria/budget
   inputs are not exposed in the run form yet.
4. **WS beyond autonomy** — run-list / collaboration views still poll; the
   autonomy WebSocket feed pattern could extend to push run + handoff events.
5. **Live-LLM in CI** — `scripts/demo_phase16.py --live` is proven locally but
   is not part of CI (requires provider keys); the deterministic demo covers
   the same loop in CI.

---

## 12. Session Log & Next-Session Handoff

### Session 7 (August 1, 2026) — Phase 17: Collaborative Reasoning & Evidence Consensus

Building the reasoning layer ABOVE the Phase 15 collaboration store:

    CollaborationService        = records WHAT agents produced/shared
    CollaborativeReasoningEngine = decides whether the evidence AGREES

Implemented so far (Phase 17 in progress):

- **Reasoning models** (`app/models/reasoning.py`) — `ConfidenceTier`
  (high/medium/low/unknown), `ConfidenceScore` (evidence-driven, bounded
  0..1), `EvidenceConsensus` (topic, status, supporting/conflicting
  evidence, final decision, contributing agents), `ContradictionRecord`
  (claim vs deterministic evidence, deterministic-wins resolution),
  `NotebookEntry` + `EngineeringNotebook` (accepted/rejected decisions,
  conflicts, resolved conflicts, consensus, timeline). Bounded: 50 consensus
  / 50 contradictions / 200 timeline entries / 20 evidence refs per record.
- **CollaborativeReasoningEngine** (`app/services/reasoning_service.py`) —
  `compute_confidence()` (weighted authority; claim-only evidence is capped
  below HIGH, so an unsupported LLM claim can never flip a consensus),
  `collect_evidence()` (planner/coding/testing/repair/reviewer/graph/memory/
  gate buckets), `detect_contradictions()` (claim-vs-test scoped to
  coding/testing/repair handoffs, claim-vs-gate, scope-vs-impact via the
  Phase 12 impact analyzer), `build_consensus()` (per-topic, deduped),
  `build_notebook()`, one-call `analyze_run()`. PostgreSQL persistence
  (migration 010) + in-memory fallback + `recover()` restart rehydration.
- **Migration 010 + DB models** — `evidence_consensus`,
  `contradiction_records`, `engineering_notebooks` (JSONB payloads,
  `created_at`/`updated_at` restored on recovery).
- **Orchestrator integration** — `_get_reasoning()` +
  `_build_reasoning()` run the reasoning pipeline at run completion
  (`CONSENSUS_BUILT` / `CONFLICT_DETECTED` events); the reviewer agent's
  context now carries shared-consensus notes (evidence-only).
- **Autonomy integration** — `AutonomousRunState.consensus_topics`;
  `_refresh_consensus_topics()` analyzes each executed run; REPLAN
  rationale is enriched with consensus topics (never overrides deterministic
  evidence).
- **API** — `GET /api/v1/runs/{run_id}/consensus`, `/contradictions`,
  `/notebook`, `/reasoning` (evidence-only responses, bounded).
- **CLI** — `devpilot consensus <run>`, `devpilot conflicts <run>`,
  `devpilot notebook <run>`.
- **Frontend** — Phase 17 Collaboration view on the dashboard (consensus
  cards with confidence, contradictions with deterministic evidence,
  engineering notebook + timeline) reusing the existing run-ID input.
- **Tests** — `tests/test_reasoning_engine.py` (23 tests): confidence model,
  contradiction detection (+ dedup), consensus agreement/conflict, notebook
  build, autonomy consensus integration, API, CLI. **23 passed**.
- **Demo** — `scripts/demo_phase17.py`: demonstrations A (planner+coding
  agreement), B (coding vs testing conflict), C (reviewer sees consensus),
  D (autonomy replan uses consensus), E (restart recovery with notebook
  persistence); deterministic default + `--live` real-LLM mode.

Reviewer fixes applied this session: `run.repair_result.summary`
AttributeError (RepairResult has no `summary` — derived from `stop_reason`),
claim-vs-test false positives (scoped to coding/testing/repair-origin
handoffs), `MAX_EVIDENCE_PER_CONSENSUS` import, dead `RESOLVED`/`or True`
logic removed, notebook_id construction order, `_build_agent_context` demo
call (passes the run object, not run_id), demo pre-populates
`repository_profile` (mirroring demo_phase15), `install_drivers` gated to
non-live mode, recovery restores `created_at`/`updated_at`.

**Validation (so far)**: 23/23 Phase 17 reasoning tests pass; all 14 Phase 17
files compile; frontend production build ✅; `devpilot_test` schema reset
clean (the pre-existing DB was left in a contradictory state — alembic
reported a duplicate index while the schema probe showed zero tables).

### Session 8 (August 1, 2026) — Phase 17 Final Verification & Completion ✅

Closed all remaining Phase 17 verification, validation, and documentation
items. No new features implemented.

**Verification** (all against live PostgreSQL 18.4, `devpilot_test`):

- **Full backend suite**: **1352 passed / 21 skipped / 0 failed** (final
  run, repeated twice — once pre-docs, once post-fixes).
- **Fresh-DB migration chain**: `alembic upgrade head` on a clean
  `devpilot_test` — revisions 001→010 all applied; tables, FKs, indexes,
  JSONB columns, and constraints verified post-upgrade.
- **Demo** (`scripts/demo_phase17.py`): all five demonstrations (A —
  planner/coding agreement → AGREED + HIGH; B — coding-vs-testing
  CLAIM_VS_TEST contradiction; C — reviewer context carries consensus;
  D — autonomy REPLAN rationale uses consensus topics; E — restart
  recovery rehydrates notebook + consensus from PostgreSQL). No
  duplicate-key errors. `--json` emits a structured summary; `--live`
  guard correctly refuses without a real LLM provider.
- **PostgreSQL recovery**: fresh-engine reload of
  `CollaborativeReasoningEngine` rehydrated notebook, consensus,
  contradictions, and timeline for a run with live data.
- **API**: `/api/v1/runs/{id}/consensus|contradictions|notebook|reasoning`
  return bounded, evidence-only payloads (no chain-of-thought); invalid
  run IDs degrade to `success: False` envelopes (no 500s).
- **CLI**: `devpilot consensus|conflicts|notebook <run>` normal + `--json`
  output; invalid runs print "No ... found".

**Real bugs found & fixed during final verification**:

1. **`PostgresRunStore` ignored its `database_url` parameter** —
   `_get_session_factory()` always called `create_session_factory()` with
   no args, connecting to `settings.DATABASE_URL` (devpilot_dev) even when
   tests passed `database_url=devpilot_test`; on a properly separated
   dev/test setup this hit the 7-table dev schema missing `context_json`
   (migration 008) → `UndefinedColumnError`. Fixed: the store now honors
   the explicit URL (owned engine + `async dispose()`), with a
   `RuntimeError` guard if engine creation returns `None`. Regression
   covered by the run-store contract + autonomy run-store tests (67
   passed) against the separated test DB.
2. **Windows CLI encoding crash** — `devpilot consensus/conflicts/notebook`
   printed Unicode `→` / `—` which cp1252 consoles cannot encode
   (`UnicodeEncodeError`). Fixed: all CLI output is ASCII-safe (`->` / `-`).
3. **Migration-test harness**: `clean_db` teardown now re-runs
   `alembic upgrade head` with a 120 s timeout (up from 30 s) after the
   10-revision upgrade; run-store contract fixture disposes its owned
   engine (no leaked connection pools).

**Docs written/updated**: `docs/COLLABORATIVE_REASONING.md` (new),
`docs/ORCHESTRATION.md` (Phase 17 section), `docs/ARCHITECTURE.md` (current
phase + layered reasoning diagram), `README.md` (Phase 17 banner),
`workflow-status/PROJECT_STATE.md` (this file),
`workflow-status/PHASE17_COMPLETION_REPORT.md` (new).

**Final acceptance checklist** — see `PHASE17_COMPLETION_REPORT.md` §4.

### Session 9 (August 2, 2026) — Phase 18: Engineering Knowledge Graph ✅

Built the unified, temporal, provenance-bearing knowledge layer above every
store — closing the "one reusable retrieval layer" objective.

**Deliverables**:

- **Models** — `app/models/engineering_graph.py`: `EKNodeType` (30 kinds),
  `EKRelationshipType` (21 relations), `EKNode`/`EKEdge` (bounded payload +
  provenance), `GraphVersion` (incremental), `RetrievalPlan`/`RetrievalStrategy`,
  `GraphQueryResult`, `GraphStats`, `NodeHistory`. All bounded.
- **Service** — `app/services/engineering_graph_service.py`:
  `EngineeringKnowledgeGraphService` (node/edge upsert + dedup, bounded BFS
  neighborhood + dependencies, `history()`, `explain()` provenance,
  `increment_version()` + supersede, `stats()`, `record_run()` ingestion,
  PG persistence with in-memory fallback, `recover()` restart rehydration,
  `database_url` support mirroring `PostgresRunStore`).
- **Planner** — `app/services/knowledge_query_planner.py`:
  `KnowledgeQueryPlanner` — deterministic intent classification
  (engineering_history / historical_fixes / explain_implementation /
  affected_tests / requirements / notebook / quality_evidence / …) →
  minimal retrieval strategy, bounded merged results.
- **Migration 011** — `ekg_nodes` / `ekg_edges` / `ekg_versions` (normalized
  + bounded JSONB, named unique constraints, source/target/version indexes).
- **Integrations** — orchestration `_ingest_into_graph()` on run completion;
  reasoning `_sync_to_graph()` (consensus/contradictions/notebook → graph);
  ContextEngine EKG context query; autonomy REPLAN rationale uses graph
  evidence.
- **API** — `/api/v1/graph/{query,node,history,neighborhood,explain,version}`
  (bounded, evidence-only). **CLI** — `devpilot graph {query,explain,history,
  neighborhood,version}` with `--json`.
- **Frontend** — `/dashboard/engineering-graph` graph explorer (query box,
  node inspector with provenance/history/edges, version stats + history,
  node distribution) on real APIs.
- **Demo** — `scripts/demo_phase18.py` demonstrations A–F (lineage,
  historical repair retrieval, graph-powered context retrieval, graph-powered
  replanning, graph version increment, restart recovery preserving graph
  integrity) — all PASS in-memory and against live PostgreSQL.
- **Tests** — `tests/test_engineering_graph.py`: 35 passed (no-PG),
  37 passed (live-PG); live-PG targeted (EKG + migration + run-store + API
  contract) 123 passed.

**Real bugs found & fixed during the build**:

1. **SQLAlchemy reserved `metadata` attribute** — `EKEdgeModel.metadata`
   raised `InvalidRequestError` on import; renamed to `metadata_json`
   (consistent with `StageResultModel`) across ORM + migration 011.
2. **Over-long stable node ids silently dropped node persistence** —
   `_stable_id()` produced ids > 40 chars, exceeding the String(40) columns;
   `_with_session()` swallowed the error so edges wrote but nodes didn't,
   corrupting restart recovery (demo F: 0 nodes / 24 edges). Fixed:
   bounded 40-char deterministic ids (head + sha1[:8]) — demo F now
   recovers 54/54 persisted nodes.
3. **Router prefix** — `/graph` → `/api/v1/graph` (v1 routers embed the
   full prefix); frontend client + tests aligned.
4. **`GraphStats` field names** — CLI referenced `by_type`/`by_relationship`
   /`active_node_count`; corrected to `node_types`/`relationship_types`.
5. **Planner intent shadowing** — bare `"history"` in `historical_fixes`
   shadowed `engineering_history`; reordered rules and moved the keyword.
6. **Migration-test drop list** — `clean_db` teardown missing the new
   `ekg_*` tables → `DuplicateTableError` on re-upgrade (34 collateral
   failures); added the three DROPs.

**Final baseline**: full suite against live PostgreSQL 18.4 =
**1392 passed / 18 skipped / 0 failed**; in-memory fallback path =
**1362 passed / 48 skipped / 0 failed**; migration chain 001→011 verified;
frontend production build passing; demos A–F PASS (in-memory + live-PG).

See `workflow-status/PHASE18_COMPLETION_REPORT.md`.

### Session 10 (August 2, 2026) — Phase 17 Live-LLM Hardening: Gemini Quota, Real Retrieval & Consensus ✅

Closed the last open Phase 17 item: **Demonstration A against production
content** (`scripts/demo_phase17.py --live` with a real Gemini free-tier key
in `.env`). Previously the demo's coding stage always failed (stub retrieved
context, broken retrieval wiring, brittle JSON extraction), so zero consensus
was ever produced. Today the live demo produces **real consensus**:

```
Run: RUN-934077FA (rejected by the deterministic quality gate)
Consensus records: 3   — test_status CONFLICTED (low 0.49) tests_failing /
                        patch_complete CONFLICTED (high 1.0) patch_conflicts_with_tests /
                        quality_gate CONFLICTED (high 1.0) rejected
Contradictions: 5      — claim_vs_test + claim_vs_gate (+3), all resolved deterministic_wins
Autonomy goal: 3 consensus topics (test_status:agreed / patch_complete:agreed /
               quality_gate:conflicted:rejected)
Patches: 5 generated · 5 applied · 0 quota errors (failover: 1)
```

**The 8 live-path bugs found & fixed (all regression-tested):**

1. **Gemini daily-quota failover** — `_CANDIDATE_MODELS`
   (3.6-flash → flash-lite → 3.5-flash); permanent daily caps fail fast in
   ~0.7s (previously burned ~16 min of backoff), transient 429s still retry
   with exponential backoff honoring the API retry delay.
2. **Real retrieval wiring** — `_stage_retrieval` passed a `RepositoryCodeIndex`
   as `lexical_index` → every `retrieve()` raised `AttributeError: '...' object
   has no attribute 'built'` (silently caught → retrieval always "skipped");
   now uses `build_with_indexes()` + `set_indexes()`; also fixed a latent
   `query_text=`→`text` typo and added a same-stage transition guard.
3. **Idempotent `_transition_to`** — same-stage calls (`coding → coding`) are
   now no-ops instead of `TransitionError` crashes (surfaced once real
   retrieval began completing stages).
4. **Demo/autonomy stub context** — demos 15/17 and the autonomy service
   pre-populated a zero-item `retrieved_context` stub → the coding LLM
   returned `INSUFFICIENT_CONTEXT` every time; live mode now lets real
   retrieval run after real planning (demo_phase15 gained a `live` param).
5. **JSON braces-in-strings** — `_extract_json`'s depth counter miscounted
   `{`/`}` inside `new_content` (real code in patch strings) → "Failed to
   parse LLM output as JSON"; now first-`{`/last-`}` extraction.
6. **Concatenated JSON objects** — Gemini sometimes emitted two objects
   (note + payload); `_load_json_with_fallback` tries every `{`-span until
   one parses.
7. **Patch hash enrichment** — LLM patches can't know SHA-256 `original_hash`
   for MODIFY/DELETE → validation rejected them; `_enrich_patch_hashes`
   computes hashes from the workspace (hallucinated files still rejected);
   plus `PatchValidator.validate()` call-site fix (stray `workspace_root=`)
   and a `PatchApplicationResult.summary` AttributeError fix.
8. **Workspace structure in the coding prompt** — `CodingAgentInput` gained
   `workspace_structure` (forwarded to the prompt, populated in `_stage_coding`)
   so the LLM knows which files exist instead of conservatively refusing.

**New doc**: `docs/GEMINI_API_KEY_REPORT.md` (214 lines) — the full Gemini
key workflow (AI Studio → .env → pydantic-settings → provider → factory →
`--live` guard), why Gemini was chosen, advantages, strengths/weaknesses
(11 real weaknesses each with its fix), free-vs-paid-vs-Vertex comparison,
security notes, Demo A results, and the 8 fixes. Linked from the README
Live-LLM E2E section + docs tree. The README test counts were refreshed to
1426 → 1429.

**Quota TTL (this session's final change)**: `_exhausted_models` in
`app/llm/providers/gemini.py` was a plain `set` that was never cleared — a
long-lived process (API server) skipped an exhausted model forever until
restart. Now a single dict `model → marked_at` (`time.monotonic()`) with a
24h TTL (`_EXHAUSTION_TTL_SECONDS`, instance-overridable), lazily pruned in
`_first_available()` so models are retried automatically after the daily
reset without a restart. Backward-compatible `_exhausted_models` property
retained. Tests: `tests/test_llm_providers.py` +3 (marker expires after TTL /
persists within TTL / `_resolve_model` recovers the preferred model after TTL).

**Test growth (Session 10 work)**: full suite at session start 1426 → final
**1429 passed / 18 skipped / 0 failed** (+3 provider TTL tests; the earlier
+23 additions across this session — orchestration unwrap, retrieval, coding
JSON, hash enrichment, workspace structure, concatenated-JSON — landed in
the preceding turns and were green at 1426). Full suite green on every
intermediate commit.

### Session 11 (August 2, 2026) — Phase 18 Live-DB Repopulation, Audit & Handoff ✅

Post-completion session. No new features — ran the Phase 18 demo against live
PostgreSQL to repopulate the (empty) `devpilot_test` DB, audited every table,
and recorded the findings in the handoff.

**Live-DB audit (devpilot_test, 26 tables, alembic at 011)**:

- The **schema is fully migrated and healthy** (001→011 at head), but **every
  table was EMPTY (0 rows)** at session start. The historical stored data from
  earlier sessions (8 goals, 72 handoffs, 56 run decisions, 24 memories, 24
  checkpoints) was **wiped by Phase 18's clean-schema migration verification**, which
  resets `devpilot_test` to prove 001→011 on a pristine DB.
- There is **no `devpilot` production database** on the local PostgreSQL —
  only `devpilot_test` exists (`devpilot` → InvalidCatalogNameError).
- Implication: persistence was proven by the suite/demos, but the live DB held
  **zero evidence rows**; a single demo run restores the persisted-state story.

**Repopulation — `scripts/demo_phase18.py` against live PostgreSQL**:

```
Persistence: PostgreSQL  (graph version 8)
Nodes: 57 | Edges: 25 | Runs: 6 | Repos: 1
[A] PASS  Requirement → Implementation → Tests → Review → Gate (RUN-P18A, APPROVED)
[B] PASS  Historical repair retrieval (explain_found, related_evidence: 3)
[C] PASS  Graph-powered ContextEngine retrieval (strategy: semantic_graph)
[D] PASS  Graph-powered replanning (strategy: knowledge_graph)
[E] PASS  Graph version increment (version 5 → 6)
[F] PASS  Restart recovery — 54/54 nodes recovered, integrity preserved
OVERALL: ALL PASS (VERSIONING/TEMPORAL/PLANNER/CONTEXT/AUTONOMY/POSTGRESQL)
```

Post-run row audit (non-empty tables): **ekg_nodes 54 · ekg_edges 24 ·
ekg_versions 6** (84 rows total). The EKG tables are now populated on the live
DB; the run/autonomy/collaboration/reasoning tables remain at 0 (the demo
exercises the graph service directly, not the orchestrator/autonomy stores —
demo_phase17 `--live` or an autonomous `execute_run` would populate those).
Note: the demo's in-memory closing stats (57 nodes / 25 edges) count
superseded/history-only nodes kept for the temporal graph; PostgreSQL
persists the active set (54 / 24) — demo F's fresh service recovered exactly
the persisted 54/54, confirming integrity.

**Handoff notes for the next session**:

- Priority 1 (open): re-run `scripts/demo_phase17.py --live` after a Gemini
  quota reset for a clean preferred-model run (PROJECT_STATE item 9).
- Priority 1: live-LLM validation of the API path
  (`scripts/verify_api_durability.py` + real provider).
- Phase 19 directions (spec: PHASE 19 READY: YES): semantic EKG embeddings
  (pgvector over node payloads), graph-backed test selection (closes Phase 12d),
  frontend force-directed graph viz, cross-repository EKG namespaces.
- `DevPilot/` is **not a git repository** (no `.git`) — no commit/diff baseline
  exists; consider `git init` for a durable change history.

### Session 14 (August 2, 2026) — Live HTTP Run-API Validation + Raw-Path Fixes ✅

Extended `scripts/verify_api_durability.py` with a **`--live` provider mode**
that runs ONE real `execute_run` through the HTTP API and verifies
runs/handoffs/consensus persist via PostgresRunStore end-to-end — the
run-API counterpart to demo_phase17's live Demonstration A. No new phase
begun.

**What was built:**

- **`verify_api_durability.py --live`** — reuses `check_live_mode()`
  (provider + API key); `build_wired_stack(live=True)` skips deterministic
  drivers and injects a test-DB-bound `CollaborativeReasoningEngine`;
  `seed_live_api()` points `app.api.v1.orchestration.workflow` and
  `app.api.v1.reasoning._service` at the wired stack; `run_live_http_execute()`
  copies `tests/fixtures/fixture_auth_app` into a temp workspace, then
  `POST /api/v1/runs` through httpx ASGITransport (real Gemini execute_run,
  900 s timeout) and verifies: run row in `PostgresRunStore.list`,
  `GET /api/v1/runs/{id}`, `GET /api/v1/runs/{id}/consensus`,
  `collab.get_collaboration_metrics`, and restart recovery via a fresh
  `CollaborativeReasoningEngine.recover` + `list_consensus`. Warns (does not
  silently pass) if the live run ends failed/rejected.

- **Two raw-path bugs surfaced by the live run, fixed in
  `orchestration_service.py`:**
  1. **INITIALIZING → ACQUIRING_REPOSITORY advance** — `POST /api/v1/runs`
     starts a fresh run at INITIALIZING; for local/no-repo (non-GitHub)
     sources `execute_run` now advances through ACQUIRING_REPOSITORY
     (recording a skip for local repos; no-repo already had create_run skips)
     before analysis — fixing the `Invalid transition: initializing ->
     analyzing_repository` TransitionError.
  2. **`_stage_analysis` await** — `RepositoryAnalysisWorkflow.run` is async;
     it now awaits and extracts `.profile` (previously assigned a coroutine,
     breaking task analysis with `'coroutine' object has no attribute
     'languages'`).

- **Regression tests** (`tests/test_orchestration.py::TestRawHttpPathFixes`, 5
  tests) — fresh local-repo run completes APPROVED with real profile + both
  pre-analysis stages; `_stage_analysis` await yields a real profile; no-repo
  run advances cleanly; GITHUB_ISSUE branch still routes through acquisition;
  resume-past-INITIALIZING is untouched by the guard.

**Validation:**

- Live run (Gemini + live PG): **11 stages** all recorded, run persisted in
  `runs` table, **4 handoffs / 3 decisions**, **3 consensus records** via API
  + fresh-engine recovery, notebook recovered.
- Full backend: **1454 passed / 18 skipped / 0 failed** (live-PG);
  **1424 passed / 48 skipped / 0 failed** (in-memory fallback).
- Deterministic `verify_api_durability.py` still green against live PG
  (2 runs, 9 handoffs, restart recovery).
- Reviewer pass — nits applied (unused `ctrl`, failed-run warning, GitHub /
  resume boundary tests).

### Session 15 (August 2, 2026) — CI Workflow + Live-LLM E2E Job & Terminal-Verdict Gate ✅

No new phase — closed the "live-LLM validation in CI" gap and made the
live API-path script CI-callable. The README already documented a
`.github/workflows/ci.yml` matrix that never existed; this session created it
and hardened the live path so the CI job fails on incomplete runs.

**What was built:**

- **`.github/workflows/ci.yml`** (new) — three jobs:
  1. `in-memory` — no PostgreSQL: `DATABASE_URL=`/`TEST_DATABASE_URL=` empty;
     PG-dependent tests must **skip**, not fail.
  2. `postgres` — dockerized `postgres:18.4` service: `alembic upgrade head`
     (migration chain 001→012) + the full suite against live PG.
  3. `live-llm-e2e` — **Demonstration A in CI**: dockerized PG + one real
     `execute_run` through the HTTP API via `scripts/verify_api_durability.py
     --live`. Gated with a job-level `if` on `workflow_dispatch` or a
     `DEVPILOT_LLM_PROVIDER` repo secret, so CI stays green with no API keys.
     The 24h-TTL free-tier provider key is refreshed as a repo secret before
     each manual dispatch (or passed via the `live_llm_provider` dispatch
     input).
- **Terminal-verdict gate in `scripts/verify_api_durability.py`** — new
  `TERMINAL_STATUSES = {approved, rejected, needs_human_review}`; in `--live`
  mode the script now **exits 1** (was: warn-only) when the run does not reach
  a terminal stage, so a provider outage/quota/mid-pipeline failure fails the
  CI job instead of passing on durability alone (mirrors demo_phase17
  Demonstration A).

**Validation:**

- `py_compile` clean; `--help` lists `--live` / `--repository`; YAML parses
  (PyYAML 6.0.3).
- Deterministic `verify_api_durability.py` still green against live PG after
  the gate edit.
- Full backend suites unchanged: **1454 passed / 18 skipped / 0 failed**
  (live-PG); **1424 passed / 48 skipped / 0 failed** (in-memory).
- Reviewer pass.

### Session 16 (August 3, 2026) — Live Goal-API Validation in `verify_api_durability.py --live` ✅

Extended `scripts/verify_api_durability.py --live` to validate **both** HTTP
API paths against the real LLM provider — the run API (already covered) AND
the autonomy goal API (`POST /api/v1/autonomy/run`), whose real stage bodies
were previously only exercised deterministically. No new phase begun.

**What was built:**

- **`run_live_http_goal()`** (new) — copies `tests/fixtures/fixture_auth_app`
  into a temp workspace, then `POST /api/v1/autonomy/run` with a bounded
  budget (`max_iterations: 2`, 0 repairs/replans = disabled) so the goal loop
  runs at most one real `execute_run` before the deterministic decision loop
  terminates. Verifies end-to-end: goal record through `GET
  /api/v1/autonomy/{goal_id}`, the goal's new runs persisted in the `runs`
  table via PostgresRunStore (before/after snapshot), per-run collaboration
  metrics (handoffs/decisions), consensus via `GET /api/v1/runs/{id}/consensus`,
  and restart recovery via a fresh `AutonomousExecutionController.recover`.
- **`seed_live_api(orch, reasoning, ctrl=None)`** — now also seeds
  `app.api.v1.autonomy._service` so the goal endpoint runs on the same
  test-DB-bound controller/collaboration/reasoning instances the validation
  inspects.
- **Dual-path CI gate** — `main()`'s live branch runs BOTH paths and exits 1
  (aggregating every failed gate) unless: the run reached a terminal verdict
  (approved / rejected / needs_human_review) AND the goal reached a terminal
  state (`TERMINAL_GOAL_STATES = {completed, stopped, waiting_for_human}`)
  with at least one persisted run that reached a verdict. FAILED/CANCELLED
  goals and broken pipelines fail the job. JSON output now nests `run_api`
  and `goal_api` results.
- **Docs** — module docstring + `--help` describe both paths; CI workflow
  header/step names + README CI Matrix row updated to "both live API paths".

**Real bug the live goal validation surfaced & fixed** — the goal API
runs failed at coding (`No patch produced`) while the run API's coding
succeeded in the same session: the coding LLM's ~20-25% transient
variance (valid-but-empty patch, and conservative INSUFFICIENT_CONTEXT
refusals — docs/GEMINI_API_KEY_REPORT.md, PROJECT_STATE item 12).
`_stage_coding` now retries once for both transient modes (new
`CODING_RETRY` event), failing immediately only on a hard
parse/validation `error`. Regression tests: retry-then-succeed (empty
patch + insufficient_context), bounded-retry exhaustion, error does NOT
retry. This is the documented item-12 fix, proven by the live run.

**Validation:**

- `py_compile` clean; `--help` lists the dual-path `--live`.
- Deterministic `verify_api_durability.py` still green against live PG
  (runs/handoffs/restart recovery).
- **Live run (Gemini + live PG), final**: run API — `POST /api/v1/runs`
  → RUN-3418371C **approved** (all 11 stages, 3 consensus, notebook
  recovered); goal API — `POST /api/v1/autonomy/run` → GOAL-367470B4
  `waiting_for_human` (terminal goal state) with its run **rejected**
  (terminal verdict), restart recovery rehydrates; **EXIT 0**, no gate
  failures — both paths validate end-to-end against real provider content.
- Full backend suites: **1460 passed / 18 skipped / 0 failed** (live-PG);
  **1430 passed / 48 skipped / 0 failed** (in-memory).
- Reviewer pass.

### Session 17 (August 3, 2026) — Raw-HTTP Durability as Repeatable Pytest ✅

Converted the `verify_api_durability.py --live` checks into a pytest test
class so the raw HTTP path is covered deterministically in CI — no script
invocation or manual run needed, and it skips cleanly when its
prerequisites are absent.

**What was built:**

- **`tests/test_api_durability.py`** (new) — two classes that reuse the
  script's exact helpers (`pick_database_url` / `ensure_schema` /
  `build_wired_stack` / `run_live_http_execute` / `run_live_http_goal` /
  `TERMINAL_STATUSES` / `TERMINAL_GOAL_STATES`):
  1. **`TestApiDurabilityDeterministic`** (`@pytest.mark.integration`) —
     drives `POST /api/v1/autonomy/run` through the real FastAPI app (ASGI)
     with deterministic stage drivers (no LLM). Needs only a test-named
     PostgreSQL → runs in the `postgres` CI job **on every push**.
     Asserts: goal reaches a terminal state + appears under the completed
     filter, ≥1 run persisted in the `runs` table (PostgresRunStore),
     handoff metrics ≥1, and restart recovery rehydrates the goal.
  2. **`TestLiveApiDurability`** (`@pytest.mark.integration` +
     `@pytest.mark.live`) — the full `--live` checks: ONE real
     `execute_run` (`POST /api/v1/runs`) **and** ONE real autonomous goal
     loop (`POST /api/v1/autonomy/run`) in a module-scoped fixture, then
     per-check asserts: runs table row, handoffs/decisions > 0, consensus
     via `GET /api/v1/runs/{id}/consensus`, fresh-engine restart recovery
     (consensus count preserved), terminal run verdict, persisted goal runs
     with verdicts, terminal goal state, and fresh-controller goal recovery.
     Skips cleanly without a test-named PG **or** a live provider.
- **`live` pytest marker** — registered in `pyproject.toml`
  (`-m live` / `-m "not live"` selectors; CI runs `pytest -m live`).
- **CI workflow** — the `live-llm-e2e` job now runs
  `pytest tests/test_api_durability.py -m live` (same dual-path gates the
  script enforced; `scripts/verify_api_durability.py --live` remains for
  manual `--json` runs); the `postgres` job adds the deterministic
  raw-HTTP class so the HTTP path is exercised on every push with no
  provider key. Both classes skip cleanly in jobs without their
  prerequisites (no-PG / no-provider), so CI stays green.
- **Docs** — README CI Matrix row + Live-LLM E2E section updated;
  workflow header comment refreshed; this Session 17 entry.

**Validation:**

- `py_compile` clean on the new test file.
- Skip paths verified: no-PG run → both classes skip (12/12); live-PG + no
  provider → deterministic class runs (4 passed), live class skips (8).
- Deterministic class green against live PG (goal terminal + completed
  filter, runs row, handoffs, restart recovery) with `-W error::RuntimeWarning`.
- **Live class, first run (Gemini + live PG): 8 passed / 0 failed in 2m01s**
  — run API reached a verdict, goal reached a terminal state, consensus +
  restart recovery all persisted.
- ⚠️ **Live goal-path flake surfaced (3 of 4 live runs)**: the goal loop's
  run failed at coding with the documented Gemini variance — attempt 1
  `insufficient_context`, `CODING_RETRY` fired, retry also empty → stage
  failed (`No patch produced`) → `test_goal_persists_runs_and_verdicts`
  correctly failed. The run-API path passed in the same sessions. This is
  PROJECT_STATE item 12's variance; the `_stage_coding` retry reduces but
  does not eliminate it, and the goal loop currently has no higher-level
  retry. The pytest class (like the script's gate) is intentionally strict —
  a broken pipeline must fail. Next session: add a bounded goal-path coding
  retry (or fixture re-run) to close the residual flake.
- Full suite (live-PG, provider configured): **1472 passed / 18 skipped /
  1 failed** (the 1 = documented goal-API flake). In-memory: **1431 passed /
  60 skipped / 0 failed**. Deterministic `verify_api_durability.py` still
  green.
- Reviewer pass — fixes applied (CI double-run removed, provider-gate-first
  ordering, memory-service engine dispose, flake-rate documented in
  `.github/workflows/ci.yml`).

### Session 18 (August 3, 2026) — Bounded Goal-Path Retry (item 13) + Live-Gate Re-validation ✅

Closed next-step item 13: the live goal-path coding flake.

**What was built:**

- `_run_iteration` (autonomy_service.py) now retries the whole run ONCE
  with a **fresh run per attempt** (`_GOAL_RUN_MAX_ATTEMPTS = 2` = 1
  retry), so a superseded failed attempt stays in the audit trail. Only the
  transient coding signature retries; environmental/non-coding failures
  fail the iteration immediately.
- New `EventType.RUN_RETRY` observability event + `_EVENT_MAP` entry.
- **Detector broadened to ANY `CODING_FAILED` after the live gate proved
  the stage-level 'status=error is deterministic' assumption false for
  Gemini**: the live run surfaced `No changes found in LLM output` and
  `Failed to parse LLM output as JSON` as `status="error"` variants the
  original message-based detector missed. Retrying any coding failure is
  safe: it is bounded (one fresh run) and a genuinely broken pipeline
  fails the second attempt too, so the gate still fails — no masking.
- Live gate (`verify_api_durability.py` + `test_api_durability.py`)
  changed from "ALL goal runs terminal" to "the goal's NEWEST run terminal"
  — a superseded `failed` first attempt is now a legitimate audit record.
- 5 new unit tests (`test_autonomy_run_iteration.py`): retry-then-succeed,
  bounded exhaustion, env no-retry, plus the two live-surfaced error
  signatures.
- `scripts/durability_report.py` (new): the `verify_api_durability.py
  --json`-equivalent for the pytest live class — runs the same
  `vd.run_live_http_execute`/`run_live_http_goal` helpers and emits the
  structured `run_api`/`goal_api` summary with the terminal gates applied.
  Helper chatter is redirected to stderr so stdout carries **only** the
  JSON document (machine-readable contract); live crashes emit
  `{"mode": "error"}` JSON instead of empty stdout; `--out` writes the
  report in every mode; skip path exits 0 with `{"mode": "skipped"}`.
  Deterministic skip-path coverage lives in
  `tests/test_api_durability.py::TestDurabilityReportJson` (subprocess,
  CI-safe without keys).

**Validation (exact CI `live-llm-e2e` command — `pytest
  tests/test_api_durability.py -q -m live` — against real Gemini + live PG
  18.4):**

- Run 1 (pre-fix): 5 failed / 3 passed — surfaced the detector gap.
- Run 2 (post-fix): retry fired (`coding_retry` + 4 goal-path runs = 2
  iterations × 2 attempts) but Gemini failed all 4 coding calls that
  session; run-API path failed at `analyzing_task` (`No requirements to
  plan against`).
- Run 3 (post-fix): **goal path PASSED** (coding succeeded, 3-file patches,
  testing handoffs); 4 failures remain — ALL run-API path, failing at
  `analyzing_task` when the task-analysis LLM returns empty requirements.
  This is a SEPARATE pre-existing flake mode item 13 did not cover (the
  goal path pre-populates requirements from the goal and never hits it).
- Full no-PG suite: **1436 passed / 60 skipped / 0 failed**.
- Unit tests: 7/7 pass.

**Residual (documented, not item-13 scope):** the run-API path has no
retry; task-analysis LLM variance (`No requirements to plan against`)
still fails `TestLiveApiDurability` run-side checks intermittently. A
follow-up could add a bounded retry at the run-API/`analyzing_task` level
(mirroring item 13) or at the orchestrator's `_stage_analysis`.

### Session 13 (August 2, 2026) — Phase 12d Closure: EKG-Driven Test Selection ✅

Closed the last Phase 12d roadmap promise: **smart test selection is now
driven by EKG impact edges (patch → test) instead of a lazy per-repo
semantic-graph re-index cache.** No new phase begun.

**What was built:**

- **`EngineeringKnowledgeGraphService.select_tests_for_changes(changed_files)`**
  (new) — walks the graph: changed file → FILE node (source_ref /
  qualified_name match) → reverse MODIFIES edge → PATCH node → VALIDATED_BY
  edge → TEST_SUITE node → `test_files` payload. Deduplicated, bounded
  (default 10), and gracefully empty (never raises) when there is no
  evidence.
- **`record_run` TEST_SUITE payload now persists `test_files`** — extracted
  via `_extract_test_files(run)` from the plan's `test_strategy`
  ("impact-driven tests: a, b"), failing-test `file_path`s, and pytest
  command args ending in `.py` (deduped, ≤20).
- **Autonomy** (`autonomy_service.py`) — `_select_impact_tests()` queries the
  EKG first (`select_tests_for_changes`), falls back to the injected
  semantic-graph `TestSelectionService`, then `[]`. `_get_engineering_graph()`
  now prefers the **orchestrator's graph instance** (the one that actually
  ingests runs) so replan selection sees real impact edges in live runs.
  The lazy per-repo `_load_impact_graph` cache and its `_graph_cache*`
  fields were removed.
- **Orchestrator test stage** (`orchestration_service.py`) — new
  `_select_tests_from_graph()` helper (never raises); `_stage_testing()`
  appends EKG-selected test files to python pytest candidate args with the
  reason suffix `| EKG impact-selected tests: …` when evidence exists;
  without evidence the full discovered suite runs unchanged.
- **Demo H** (`demo_phase18.py`) — a changed file (`auth/service.py`)
  recovers its tests (`tests/test_auth.py`, `tests/test_session.py`) via
  patch → test impact edges; unknown files → empty; `IMPACT TEST
  SELECTION: PASS` added to the OVERALL block.

**Validation:**

- EKG + autonomy + orchestration targeted suites: **132 passed / 2 skipped**
  (no-PG); **66–76 passed** live-PG.
- Full backend: **1451 passed / 18 skipped / 0 failed** (live-PG);
  **1421 passed / 48 skipped / 0 failed** (in-memory fallback).
- `scripts/demo_phase18.py` — **ALL PASS** in-memory AND live-PG
  (IMPACT TEST SELECTION: PASS, SEMANTIC RETRIEVAL: PASS, POSTGRESQL: PASS).
- Reviewer pass; the flagged autonomy-EKG-instance gap (separate empty graph)
  was fixed by delegating to the orchestrator's graph before the final sweep.

### Session 12 (August 2, 2026) — Phase 19: Semantic EKG Retrieval ✅

Implemented the first Phase 19 direction: **pgvector-style semantic similarity
over EKG node payloads merged into the KnowledgeQueryPlanner**, with migration,
tests, and a demo update. No new phase begun.

**What was built:**

- **`HashedNGramEmbeddingProvider`** (`app/rag/embeddings/hashed_provider.py`,
  new) — a deterministic, similarity-preserving embedder (lowercased words +
  4-char stems + within-word trigrams, stopword-filtered, feature-hashed into
  signed buckets, L2-normalized). Unlike the Phase 5 `FakeEmbeddingProvider`
  (hash-random vectors), texts that share vocabulary get real cosine
  similarity — so semantic retrieval is meaningful with NO paid API. Registered
  in `create_embedding_service` (`provider='hashed'`) and the
  `EMBEDDING_PROVIDER` validator.
- **Semantic index in `EngineeringKnowledgeGraphService`** — lazy, bounded
  (≤2000 nodes) node→vector index derived deterministically from node text
  (name/qualified_name/kind/source_type + bounded payload fields, evidence-only,
  never CoT). `semantic_search(query, limit, target_kinds)` returns cosine-
  ranked hits within `MAX_QUERY_RESULTS`; `semantic_stats()` reports size /
  provider / dimension. `record_run` embeds new nodes after ingest;
  `recover()` clears and deterministically rebuilds the index (exact restart
  recovery). When pgvector is available, vectors are ALSO mirrored to
  `ekg_embeddings` (best-effort; failure falls back to in-memory).
- **Planner merge** (`knowledge_query_planner.py`) — `retrieve()` now runs a
  semantic pass as a RECALL booster: hits are merged into the lexical set
  within the same `MAX_QUERY_RESULTS` bound, deduped, and the result carries
  `semantic_used` / `semantic_matches` / `semantic_top_score`. The semantic
  pass is NOT restricted to the plan's inferred kinds (a "memory" query plans
  REPOSITORY_MEMORY but the relevant node may be a REQUIREMENT) — only an
  explicit caller kind filter is honored.
- **Migration 012** (`012_add_ekg_embeddings.py`, new) — guarded pgvector
  table (node_id UNIQUE + vector(256) + model): skipped when the extension is
  absent (chain stays linear, mirrors 005); `clean_db` teardown drops it.
- **API/CLI** — `/api/v1/graph/query` response gains a `semantic` block;
  `devpilot graph version` prints the semantic index size/provider/dimension.
- **Demo G** (`demo_phase18.py`) — a query with NO lexical name overlap
  (`"memory caching of hot reads"`) still surfaces the node whose payload
  matches; `SEMANTIC RETRIEVAL: PASS` added to the OVERALL block.

**Bugs found & fixed during the build:**

1. **Attribute-vs-method shadow** — `self._semantic_pg_available = None` in
   `__init__` shadowed the `_semantic_pg_available()` method → every demo
   crashed with `'NoneType' object is not callable`. Renamed the field to
   `self._pg_ok`.
2. **Embedder not discriminative** — the first hashed-n-gram design (grams
   across spaces + stopwords) drowned real overlap in hash noise (same-topic
   0.027 < unrelated 0.055). Reworked to per-word features with stopword
   removal; discrimination now holds (same-topic > unrelated + 0.05).
3. **Planner over-restricted semantic search** — passing `plan.target_kinds`
   filtered out the semantically relevant node (REQUIREMENT) for a "memory"
   query. Semantic pass now only honors explicit caller kinds.
4. **Weak test** — the merge test passed via low-score noise nodes; reworked
   to a zero-lexical-overlap query so only the semantic pass can find the node.

**Validation:**

- EKG suite: **43 passed / 2 skipped** (no-PG); **55 passed** live-PG
  (EKG + migration).
- Full backend: **1437 passed / 18 skipped / 0 failed** (live-PG);
  **1407 passed / 48 skipped / 0 failed** (in-memory fallback).
- `scripts/demo_phase18.py` — **ALL PASS** in-memory AND live-PG
  (SEMANTIC RETRIEVAL: PASS, POSTGRESQL: PASS).
- Migration chain 001→012 (guarded: `ekg_embeddings` absent without pgvector).
- Multiple reviewer passes signed off (fake→hashed mapping, unconditional
  downgrade drop, semantic_matches counts merged nodes only, CLI line).

**Remaining Phase 19 directions** (not started): cross-repository knowledge
namespaces, frontend force-directed graph viz. **Phase 19B (multi-provider
failover) IS COMPLETE** — see Session 24 below.

### Session 3 (August 1, 2026) — WebSocket Live Updates

Replaced the goal view's 5s polling with **push-based WebSocket live updates**
(backend + frontend), closing roadmap item 1.

Backend:

- `WebSocketManager.broadcast_autonomy()` — fans one event out to the global
  `__autonomy__` feed and the per-goal `__autonomy__:{goal_id}` feed (set-union
  dedup, tolerated per-client failures).
- `AutonomousExecutionController` live hooks (fire-and-forget, never fatal):
  `create_goal` → `goal_created`; `_record_decision` → `decision` (live
  timeline); `_escalate` → `escalation` (queue refresh); `_checkpoint` →
  `status` heartbeat (per-iteration + terminal transitions, skipped on CAS
  conflict). Each event carries a full `status_summary()` snapshot.
- `app/api/v1/ws.py` — `WS /api/v1/ws/autonomy` (global feed; initial goal
  list snapshot) and `WS /api/v1/ws/autonomy/{goal_id}` (per-goal feed;
  initial status, error frame if unknown).
- Tests: `tests/test_autonomy_ws.py` (9 tests) — manager fan-out / no
  connections / tolerated failures; controller emission hooks; route
  registration.

Frontend (`web/src/App.jsx`, `web/src/styles.css`):

- WebSocket client connects to the global autonomy feed when Live is on;
  applies pushed status snapshots + appends pushed decisions (deduped by id,
  capped at 100) for the goal being viewed; refreshes the escalation queue on
  escalation/goal-created events; `ws-pill` indicator shows `● live ws` vs
  `○ polling`.
- **5s polling is now only a fallback** — it runs solely while the WebSocket
  is disconnected, so the dashboard degrades gracefully.
- Escalation events ignore their embedded pre-transition snapshot (the
  controller broadcasts before flipping to WAITING_FOR_HUMAN); the following
  checkpoint/status event owns the state chip.

**Validation**: backend **1267 passed / 18 skipped / 0 failed** (full suite,
+9 new autonomy-WS tests); frontend production build ✅; reviewer passes on
backend + frontend — fixed a mid-edit syntax slip in `classify_failure` and an
`_evidence()` test-helper signature; final review: no concrete bugs.

### Session 6 (August 1, 2026) — Impact-Driven Replanning, Goal Browser & Live API Validation

Closed three Phase 16 follow-ups and proved the API path durable:

- **Impact-analysis-driven replanning (Phase 12d closed)** —
  - Migration 009 (`alembic/versions/009_add_plan_test_set.py`) adds `test_set`
    (JSONB) to `plan_versions`; `PlanVersionModel` + `PlanVersion` carry it.
  - `AutonomousExecutionController._select_impact_tests(changed_files,
    repository)` uses the Phase 12 semantic-graph `TestSelectionService`; the
    graph is lazily built from the repository (via `CodeIntelligenceService`)
    and cached per repository — degrades to an empty set when unavailable.
  - The replan loop records the impact-selected test set on the new
    `PlanVersion`; `_persist_plan_versions`/`_load_goal` round-trip it;
    `_plan_from_version` restores it into `test_strategy` on continuation.
- **Goal browser + run-from-UI** — new `/devpilot-goals` route with state
  filter chips; run form exposes acceptance criteria + budget controls
  (criteria → `criteria_texts`, budget → `ExecutionBudget`); goal browser with
  View/Resume/Pause/Cancel + escalation display; detail shows plan versions
  incl. impact test_set + decision history. Backed by a new `state=` query
  filter on `GET /api/v1/autonomy` (`list_goals(limit, state)`).
- **Live API-path durability validation** — `scripts/verify_api_durability.py`
  drives the real HTTP `POST /api/v1/autonomy/run` (deterministic stage
  drivers) against the test PostgreSQL DB: goal completes, `state=completed`
  filter returns it, a fresh controller rehydrates it (restart recovery), the
  `runs` table gains rows, and collaboration handoffs persist.
- **Real bug found + fixed** — `OrchestrationService.create_run` recorded
  skipped stages via `_store.update()` BEFORE `_store.create()`, raising
  `RunNotFoundError` on PostgresRunStore for runs without a repository
  (InMemory silently tolerated it). The run is now persisted first, then
  stages are recorded (events added before their update so they persist).
  Regression-tested via a strict update-order store.
- **Tests** (+15) — `tests/test_autonomy_replan_test_set.py` (record,
  selection, graph/no-graph/error, `_plan_from_version` restoration, replan
  loop), `tests/test_autonomy_api.py` (state filter, empty filter,
  criteria/budget passthrough), `tests/test_autonomy_run_store.py`
  (create-run order regression).

**Validation**: `verify_api_durability.py` fully green (goal `completed` via
HTTP, 2 runs persisted, 9 handoffs, recovery rehydrates `completed`); backend
**1288 passed / 21 skipped / 0 failed** (full suite, +15); frontend production
build ✅; reviewer passes — graph wiring (caching, node_count, graceful
degradation), create_run reorder + event ordering, regression store, frontend
hooks/payload contract, and state filter all verified; final review: no
concrete bugs.

### Session 5 (August 1, 2026) — Durable Autonomy Runs (PostgresRunStore)

Closed the "runs=0" gap: autonomy goals ran on in-memory run stores, so the
`runs` table stayed empty and run-level handoffs/recovery were not durable
end-to-end. Now the autonomous loop writes runs through PostgresRunStore:

- **Migration 008** (`alembic/versions/008_add_run_context.py`) — adds a
  `context_json` JSONB column to `runs`.
- **PostgresRunStore context round-trip** (`app/services/postgres_run_store.py`)
  — `_serialize_context()`/`_deserialize_context()` persist the run's context
  (repository_profile, requirements, plan, retrieved_context, patch/test/
  repair/review/gate outputs) so `execute_run`'s store re-hydration keeps the
  autonomy controller's pre-populated context — without it the strict state
  machine rejects the first real transition (`analyzing_task → planning`).
  Attribute-less stub objects are skipped (cannot round-trip).
- **Controller wiring** (`app/services/autonomy_service.py`) —
  `AutonomousExecutionController` gains a `run_store` param;
  `_get_orchestration()`/`_get_run_store()` are async and probe
  `SELECT context_json FROM runs LIMIT 1` before binding PostgresRunStore,
  degrading to InMemoryRunStore on unmigrated DBs (graceful, same as other
  Phase 13-16 services). `_run_iteration` re-fetches the run from the store
  after `execute_run` so evidence reflects the persisted stage outputs.
- **Real-path bug fixed while wiring the durable store** —
  `RepositoryAnalysisWorkflow().run()` returns an `AnalysisState` dataclass,
  not a `RepositoryProfile`; the controller now extracts `.profile` (with an
  explicit stub fallback) so the profile actually round-trips.
- **Demo** (`scripts/demo_phase16.py`) — the orchestrator is bound to a
  PostgresRunStore when a test-DB session factory exists; the demo now prints
  the persisted runs (previously it explicitly kept run records ephemeral).
- **Tests** (`tests/test_autonomy_run_store.py`, +10) — context round-trip,
  stub skipping, unmigrated-schema probe fallback, controller wiring, and a
  deep-copy-store evidence re-fetch regression (reproduces the Postgres
  re-hydration failure mode).

**Validation**: demo completes `completed` with **7 persisted runs** in the
test DB, criteria 2/2, repairs=1, handoffs=9, restart recovery rehydrates
`completed`; backend **1273 passed / 21 skipped / 0 failed** (full suite,
+6 tests); migration 008 round-trip included; reviewer passes — the three
findings (API-path unmigrated-DB binding, `or analysis_state` fallback,
dead `repository_path` context field) all fixed; final review: no concrete
bugs.

### Session 4 (August 1, 2026) — Live Autonomous execute_run (Demonstration A)

Closed roadmap item 2: built `scripts/demo_phase16.py`, which drives ONE real
autonomous run end-to-end (goal → bounded loop on the real orchestrator →
collaboration summary → restart recovery), mirroring the Phase 15
live-PostgreSQL validation. Deterministic default (`fail_then_pass=True` with
the repair driver reporting `attempts=0`) so the autonomy **REPAIR** path
actually fires (CONTINUE → REPAIR → COMPLETE); `--live` runs a REAL
`execute_run` with the configured LLM provider; `--json` for machine output.

Building it exposed and fixed **four real-path latent bugs** in
`app/services/autonomy_service.py` (this path was never exercised in CI —
tests inject an `iteration_runner`):

- **`_run_iteration` state-machine bug** — the run was pre-populated with
  `requirements` but left at `current_stage=INITIALIZING` with no
  `repository_profile`, so the first real `execute_run` transition was
  rejected by the strict state machine. Now pre-populates
  `repository_profile` (real `RepositoryAnalysisWorkflow().run()` or a stub)
  and advances `current_stage=ANALYZING_TASK` (valid → PLANNING transition).
- **Persistence PK bug** — `_persist_goal`/`_load_goal` used
  `session.get(ExecutionGoalModel, goal_id)` with a string goal_id against an
  integer PK (only fails on real PostgreSQL). Now query by the unique
  `goal_id` column via `select().where(...).scalar_one_or_none()`.
- **Stale-state rehydration** — terminal transitions updated in-memory state
  but never the persisted `state` column, so a restarted controller rehydrated
  a COMPLETED goal as `running`. `_persist_checkpoint`'s CAS UPDATE now also
  syncs the `state` column (version guard preserved).
- **Attribute-less stubs** — `repository_profile`/`retrieved_context` were
  `type("RP", (), {})()` objects that would `AttributeError` downstream in
  live mode; now minimal valid `RepositoryProfile(name=...)` /
  `RetrievedContext(query=...)` instances. (Also awaited the previously
  never-awaited `RepositoryAnalysisWorkflow().run()` coroutine and hardened
  the demo's stdout/stderr to UTF-8 so Windows cp1252 can't emit
  `UnicodeEncodeError` per log line.)

Regression coverage: `tests/test_autonomy_run_iteration.py` (2 tests) —
exercises the real `_run_iteration` path with no injected runner, asserting
`repository_profile` is populated and `current_stage` is advanced.

**Validation**: demo completes `CONTINUE → REPAIR → COMPLETE` (repairs=1,
handoffs=9, restart recovery rehydrates `state completed`); backend
**1267 passed / 18 skipped / 0 failed** (full suite); 57 autonomy tests green;
4 reviewer passes — each finding (missing await, stub attributes, demo repair
path, unguarded recovery) fixed; final review: no concrete bugs.

### Session 2 (August 1, 2026) — Dashboard Goal View

Built the autonomous-execution **goal view** on the web dashboard
(`web/src/App.jsx`, `web/src/styles.css`), closing the Phase 16 frontend gap:

- **Live goal status** — pulsing state chip (`LIVE` dot), 5s silent background
  polling that stops for terminal states, manual refresh toggle.
- **Decision history timeline** — fetches `GET /v1/autonomy/{id}/decisions`,
  color-coded action badges, reason codes, rationale, evidence refs, timestamps.
- **Plan-version diffing** — token-level diff of summary + objective vs the
  actual previous version (also fixed a pre-existing stray-quote bug in the
  version chip).
- **Repair/replan budget bars** — usage vs limits (iterations, repairs, replans,
  agent/LLM calls, test runs) with exhaustion coloring.
- **Escalation queue** — `GET /v1/autonomy` lists goals with open escalations;
  per-goal **Respond** (`/input`), **Resume** (`/resume`), **Cancel** (`/cancel`),
  and **View** actions, all wired to the `/v1/autonomy` endpoints.

Backend additions: `AutonomousExecutionController.list_goals()` (in-memory +
persisted merge, best-effort, deduped escalations) and `GET /api/v1/autonomy`.
Tests: `test_list_goals`, `test_list_goals_escalation_queue` (API).

**Validation**: backend **1256 passed / 18 skipped / 0 failed**; frontend
production build ✅; 3 reviewer passes (fixed silent-poll view-wipe, diff-label
version mismatch, escalation dedup) — final review clean.

### Recommended Next Steps (priority order)

1. ✅ **WebSocket live updates** — DONE (Session 3). Push-based goal events;
   polling retained only as a disconnect fallback.
2. ✅ **Live-LLM E2E (Demonstration A)** — DONE (Session 4).
   `scripts/demo_phase16.py` drives one real autonomous `execute_run`
   (deterministic default + `--live` real-LLM mode, `--json`); building it
   fixed four real-path latent bugs in the autonomy service.
3. ✅ **Persist autonomy runs to PostgresRunStore** — DONE (Session 5).
   Migration 008 (`context_json` on `runs`), PostgresRunStore context
   round-trip, controller schema probe + in-memory fallback, run re-fetch
   after execute_run; demo now lists persisted runs in the test DB.
4. ✅ **Phase 17 — Collaborative Reasoning & Evidence Consensus** — DONE
   (Sessions 7–10). CollaborativeReasoningEngine, consensus records,
   contradiction detection, confidence model, engineering notebook, API/CLI/
   frontend, migration 010. **Live Demonstration A closed in Session 10** —
   `scripts/demo_phase17.py --live` now produces real consensus from
   production content (5 patches, 3 consensus records, 5 contradictions)
   after 8 live-path bugs were fixed and regression-tested.
5. ✅ **Goal list page** — DONE (Session 6). `/devpilot-goals` route with
   state-filter chips, goal browser (View/Resume/Pause/Cancel), escalation
   display, and selected-goal detail; backed by `GET /api/v1/autonomy?state=`.
6. ✅ **Run-from-UI** — DONE (Session 6). Criteria + budget (max_iterations /
   max_replans / max_repairs) exposed in the goals-page run form and sent via
   `POST /api/v1/autonomy/run`.
7. ✅ **Impact-analysis replanning** — DONE (Session 6). Migration 009
   (`test_set` on `plan_versions`), graph-backed `_select_impact_tests`
   (lazy per-repo cache), persisted on replan, restored into `test_strategy`.
8. ✅ **Gemini quota resilience** — DONE (Session 10). Multi-model daily-cap
   failover (`_CANDIDATE_MODELS`) + 24h TTL on `_exhausted_models` so
   long-lived processes recover after the midnight-Pacific reset without a
   restart. Report: `docs/GEMINI_API_KEY_REPORT.md`.
9. 🚧 **Re-run the final clean live demo** — the Session-10 run was executed
   mid-day when 3.6-flash/3.5-flash were already quota-exhausted (failover
   used flash-lite). After a daily reset (or with a paid key), re-run
   `python scripts/demo_phase17.py --live` once to confirm a fully clean
   run where the preferred model serves every stage. All other demo work is
   machine-verifiable on every push (full suite 1429/18/0).
10. **Push-to-all-run-list** — optionally extend WebSocket events to the
    collaboration/run views so handoffs and decisions stream without polling
    anywhere.
11. **Live-LLM validation of the API path** — `verify_api_durability.py`
    currently uses deterministic drivers; run it with `--live`-style wiring
    (or a live goal) against a real provider to close the final
    production-grade demonstration gap end-to-end through HTTP.
12. **Optional LLM quality improvements** — add one retry when the coding
    agent returns an empty patch (reduces the ~20-25% valid-but-empty
    response variance at the cost of quota); make the gemini exhaustion TTL
    configurable via settings; align the rolling 24h TTL to exact
    midnight-Pacific wall-clock reset for precise recovery.
13. 🚧 **Close the live goal-path coding flake** — surfaced by
    `TestLiveApiDurability` (Session 17): the goal loop's run can fail at
    coding when Gemini returns `insufficient_context` twice in a row (the
    `_stage_coding` retry fires but both attempts are empty) → the live
    goal-API test fails intermittently (~3 of 4 live runs). Options: (a)
    bounded retry at the autonomy iteration level when a run fails with
    `No patch produced` (mirrors the `_stage_coding` fix); (b) fixture-level
    re-run in `tests/test_api_durability.py` with a max-attempts constant;
    (c) accept + document the flake (already documented in
    `.github/workflows/ci.yml`). Run `python -m pytest
    tests/test_api_durability.py -m live` to reproduce. The new Phase 19B
    provider router reduces the blast radius of this flake (a quota-exhausted
    Gemini now fails over to other configured providers) but does not
    manufacture content where the model returns empty — the two fixes compose.
14. ✅ **Multi-provider failover (Phase 19B)** — DONE (Session 24). Health-aware
    `ProviderRouter`, circuit breakers, bounded retries, quota-aware failover,
    streaming failover, `openrouter`/`ollama` providers, PG metric snapshots
    (migration 014), `/api/v1/providers` API + CLI + dashboard page,
    redacted config surface, 43 deterministic tests. See
    `workflow-status/PHASE19B_COMPLETION_REPORT.md`.
15. ✅ **Phase 19C (part 1) — cross-repo namespaces + force-directed viz** — DONE
    (Session 25). `OrganizationGraphService` already existed (Phase 19A, tested);
    closed the two remaining 19C directions: (a) cross-repository knowledge
    namespaces — demo I in `scripts/demo_phase18.py` (3 repos, explicit
    `link_repositories` edges, org-scope merge + local-scope isolation, verified
    `cross_repository_traversal`); (b) frontend force-directed viz — shared
    `ForceDirectedGraph.tsx` (SVG simulation, pan/zoom/drag, node colors/labels),
    engineering-graph page now has a neighborhood panel backed by
    `GET /api/v1/graph/neighborhood`. See Session 25 below.
16. 🚧 **Phase 19C (part 2, NOT started)** — expose cross-repository namespaces
    through the API/CLI/frontend (org-scope queries) and wire multi-repo remote
    acquisition so the organization graph can be populated from real repositories
    instead of the synthetic demo. (✅ `git init` DONE Session 25 — baseline
    commit `8598153` exists; commit early and often from here on.)

### Session 19 (August 3, 2026) — Task-Analysis Stage Retry (run-API path) ✅

Closed the last residual live-gate flake: the raw-HTTP run-API path failed
intermittently at `analyzing_task` with `No requirements to plan against` —
the task-analysis LLM (issue analyzer) returned empty requirements on
Gemini (~20-25% variance, the same signature as the coding stage) and
`_stage_task_analysis` failed the run with no retry.

**What was built:**

- `_stage_task_analysis` (orchestration_service.py) now retries the
  `plan_from_task` call ONCE (`_TASK_ANALYSIS_MAX_ATTEMPTS = 2`) before
  failing the stage — a fresh LLM issue-analysis call per attempt, mirroring
  the `_stage_coding` retry (item 12). Bounded; a genuinely broken pipeline
  fails the second attempt too, so the gate still fails — no masking.
- New `EventType.TASK_ANALYSIS_RETRY` observability event.
- 4 new unit tests (`tests/test_orchestration.py::TestStageTaskAnalysisRetry`):
  retry-then-succeed, bounded exhaustion, clean-first-call no-retry, and
  raised-exception-fails-immediately (no retry on exceptions).

**Validation:**

- Unit tests: 12 passed (3 new task-analysis retry + 9 coding retry).
- Full no-PG suite: **1441 passed / 60 skipped / 0 failed**.
- Live gate (`pytest tests/test_api_durability.py -m live`, real Gemini +
  live PG 18.4): **8 passed, 5 deselected** — the run-API path that failed
  4-5 tests in every prior session is now green end-to-end (run verdict,
  runs-table persistence, handoffs, consensus via API + recovery, restart
  recovery, goal loop).

### Session 20 (August 3, 2026) — Durability Panel (backend endpoint + dashboard) ✅

Made the `scripts/durability_report.py` JSON visible in the web dashboard:

**Backend:**

- New `GET /api/v1/durability/report` (app/api/v1/durability.py) serves the
  latest report JSON in the codebase-standard `{success, data}` envelope.
  It only READS the configured file (never runs the live LLM paths);
  `settings.DURABILITY_REPORT_PATH` (default `backend/durability_report.json`
  via `parents[3]` of the router file — a reviewer-caught off-by-one) and
  returns 404 with generation guidance when missing, 500 on unreadable /
  non-object content. Registered in app/main.py.
- 6 deterministic tests (tests/test_durability_api.py): 404 missing, payload
  present, skipped mode, corrupt JSON 500, non-object 500, default path
  resolution regression.

**Frontend:**

- `/dashboard/durability` page (new): mode banner (live/skipped/error),
  RunApiCard + GoalApiCard with verdict badges and persisted-metric stat
  cards (handoffs, decisions, consensus via API + recovered, runs in table,
  goal recovery state), per-run audit-trail links into `/dashboard/runs/[id]`
  (retry attempts included), gate-failure list, collapsible raw JSON viewer,
  empty state with the exact generation command, refresh button.
- `durabilityApi.report()` + `DurabilityReport`/`DurabilityRunApi`/
  `DurabilityGoalApi` types in src/lib/api/client.ts; nav item added to the
  dashboard sidebar.

**Validation:**

- Full no-PG suite: **1447 passed / 60 skipped / 0 failed**.
- Frontend production build passes (route `/dashboard/durability` compiled).

### Session 21 (August 3, 2026) — Durability Report CI Artifact (dispatch-only) ✅

Wired the durability report into GitHub Actions as the ops artifact the
`/api/v1/durability/report` endpoint serves.

**What was built (DevPilot/.github/workflows/ci.yml, `live-llm-e2e` job):**

- New step `Generate durability report JSON`: runs
  `python scripts/durability_report.py --out durability_report.json` (same
  two live HTTP paths as the pytest `-m live` step — real `execute_run` +
  real autonomous goal loop), then asserts `mode == "live"` AND
  `passed is true` via a Python heredoc, exiting 1 otherwise. This is
  essential: the script exits 0 on skip, so without the assertion an expired
  24h-TTL key would silently upload a green artifact.
- New step `Upload durability report artifact`: `actions/upload-artifact@v4`
  (name `durability-report`, path `backend/durability_report.json`,
  `if-no-files-found: error`) — the exact document served by
  `GET /api/v1/durability/report` in the dashboard.
- Both new steps are gated `if: github.event_name == 'workflow_dispatch'`
  (reviewer-flagged): the pytest gate already covers push/PR live runs, so
  the artifact is produced only on manual ops runs — the double live-LLM
  cost is not paid on every push.

**Validation (exact step command run locally):**

- Skip mode (no provider): step exits 1 with `mode=skipped` — never a
  silent green artifact.
- Live mode (real Gemini + live PG 18.4): `passed: true`, `gates: []`,
  `STEP_EXIT=0`, `backend/durability_report.json` written.
- Workflow YAML parses; step order + artifact path verified (job
  `working-directory: backend` + workspace-relative artifact path align).

**Phases 1–18 complete; remaining work is polish, live re-verification,
and production hardening — no new phases planned.**

### Session 22 (August 3, 2026) — Shared Bounded-Retry Helper (Phase 19 refactor) ✅

Extracted the bounded-retry pattern from the three inline retry sites into
one shared helper so future stages get the same safety net in one line.

**What was built (DevPilot/backend):**

- `app/services/bounded_retry.py` (NEW) — `run_bounded_retry(attempt_fn,
  is_success, should_retry, max_attempts, on_retry)` returning
  `RetryOutcome(result, attempts, retried)`. Semantics preserve every
  original loop: stop on first success; retry only transient failures
  (`should_retry`), bounded by `max_attempts`; deterministic failures stop
  immediately; exceptions from `attempt_fn` propagate un-retried
  (environmental contract — a broken pipeline fails the final attempt too,
  no masking). `max_attempts < 1` rejected.
- `app/services/orchestration_service.py` — `_stage_coding` and
  `_stage_task_analysis` now delegate to the helper. The coding
  missing-context accumulation moved into the `attempt_fn` closure
  (`nonlocal last_missing_context`); the `CODING_RETRY` /
  `TASK_ANALYSIS_RETRY` events moved into `on_retry` (byte-identical
  messages, same 1-based attempt numbers). Downstream fail paths preserved
  in order: `status == "error"` first, then the no-patch + missing-context
  message.
- `app/services/autonomy_service.py` — `_run_iteration` builds the
  fresh-run-per-attempt body as a nested `_attempt` closure (tracking
  `last_run_id` so an environmental exception still reports the run),
  `_on_retry` emits `RUN_RETRY`, and `run_bounded_retry` is wrapped in
  try/except mapping exceptions to `FailureClass.ENVIRONMENT` evidence —
  preserving the "environmental failure never retried" contract.
- `tests/test_bounded_retry.py` (NEW, 7 tests) — first-attempt success,
  retry-then-succeed with `on_retry` numbering, bounded exhaustion
  (including a reviewer-flagged assertion that `on_retry` fires exactly
  `attempts - 1` times and never after the final attempt),
  deterministic failure never retried (and never emits a retry event),
  exception propagation with no retry, zero max-attempts rejection,
  single-attempt bound.

**Validation:**

- Full no-PG suite: **1454 passed / 60 skipped / 0 failed** (was 1447;
  +7 helper tests).
- Targeted retry suites (helper + coding unwrap + task-analysis retry +
  autonomy run iteration): 27 passed.
- Reviewer sign-off: semantic equivalence confirmed at all three sites;
  the one flagged gap (on_retry count pinned in the exhaustion/deterministic
  tests) was added and re-validated.

### Session 23 (August 3, 2026) — Durability Discovery UX (quick-action + Runs header) ✅

Made the durability validation view reachable without the sidebar.

**What was built (DevPilot/frontend):**

- `src/app/dashboard/page.tsx` — added a `Durability` quick-action card
  (cyan accent, `Live validation & gates`) to the Quick Actions grid,
  between Review & Approve and View Docs, reusing the exact shared card
  markup (label/desc/color/href shape).
- `src/app/dashboard/runs/page.tsx` — added a `Durability` header link in
  the Runs page button group (next/link already imported; styled like the
  Auto-refresh button with the shield-check icon + cyan light/dark hover
  accents) so operators jump from run history straight to the live gates.

**Validation:**

- Frontend production build passes: `/dashboard/durability` and
  `/dashboard/runs` compile, EXIT=0.
- Reviewer sign-off: both additions follow existing patterns; the only nit
  (plain `<a>` on quick-action cards) is pre-existing convention, correctly
  followed by the new card.

### Session 24 (August 3, 2026) — Phase 19B: Multi-Provider Failover & Reliability Platform ✅

Built the health-aware, failover-capable provider router. **Phase 19C is NOT
started.**

**Backend (DevPilot/backend):**

- `app/llm/router.py` — core router: `FailureKind` + `classify_failure`
  (quota markers matched BEFORE rate-limit markers), `CircuitBreaker`
  (closed→open→half-open, half-open probe budget, automatic recovery),
  `RetryStrategy` (bounded exponential backoff, recoverable-only),
  `ProviderHealth` (single bounded rolling window, latency EMA, uptime),
  `MetricsRegistry` (shared health objects; retry totals reflect real
  retries), `ProviderRouter` (deterministic priority chain, `asyncio.wait_for`
  timeout, failover, streaming failover pre-first-token, snapshots),
  `RoutedProvider` facade + `get_router`/`reset_router`/`get_routed_provider`
  singletons. `AllProvidersFailedError` is never silent — it carries per-
  provider `failures`.
- `app/llm/redaction.py` — recursive `redact_secret`/`redact_dict` (incl.
  nested lists of dicts); applied at the router boundary so API/CLI surfaces
  never serialize raw keys.
- `app/llm/providers/openrouter.py`, `app/llm/providers/ollama.py` — two new
  providers (ollama is keyless via `OLLAMA_BASE_URL`; both resolve the
  `gpt-4o-mini` sentinel to their real default model).
- `app/llm/factory.py` — registered both; `get_provider(None)` returns
  `RoutedProvider` when `PROVIDER_ROUTING_ENABLED` (default True), named
  lookups stay direct. Agents are untouched.
- `app/config.py` — `OPENROUTER_API_KEY`/`OLLAMA_BASE_URL` + 14 Phase 19B
  settings (routing, priority, timeout 60s, retry 2/0.5s/10s, circuit
  3/30s/2, health window 100/0.5/0.3, metrics persist True).
- `app/core/exceptions.py` — `ProviderRouterError`, `AllProvidersFailedError`,
  `ProviderNotAvailableError`, `ProviderCallFailedError` (stores
  `.provider`/`.kind`/`.message`).
- `app/services/provider_metrics_store.py` — PG snapshot store
  (`enabled` = `PROVIDER_METRICS_PERSIST` + `DATABASE_URL`); clean no-op
  without a DB.
- `alembic/versions/014_add_provider_metrics.py` — migration `014`
  (`provider_metric_snapshots` + provider/recorded_at indexes);
  `alembic upgrade head` green on `devpilot_test`; `tests/test_migration.py`
  updated (`_drop_all_tables`, expected-table sets) + new schema test.
- `app/api/v1/providers.py` — `/api/v1/providers` router (overview, health,
  metrics, metrics/history, redacted config, test); registered in
  `app/main.py` (98 routes).
- `app/cli.py` + `app/cli_providers.py` — `providers`, `provider-health`,
  `provider-metrics`, `provider-test` with `--json`.
- `tests/test_provider_router.py` — **43 deterministic tests** (no paid LLM).

**Frontend (DevPilot/frontend):**

- `src/lib/api/client.ts` — provider types + `providersApi`
  (overview/health/metrics/history/config/test).
- `src/app/dashboard/providers/page.tsx` — provider observability page:
  active provider + routing + totals cards, live test-call button, per-provider
  cards (status/circuit/success-rate/latency/retries/failovers/uptime/
  configured/priority), failover-event table, persisted snapshot, redacted
  config panel. Real APIs only.
- `src/app/dashboard/layout.tsx` — "Providers" nav item.

**Validation:**

- Targeted deterministic: **110 passed** (43 router + 11 migration + 56
  run-store contract).
- CLI smoke (`providers` / `provider-health` / `provider-metrics`): correct
  payloads with current `.env`.
- Frontend: `npx tsc --noEmit` clean, `npm run build` EXIT=0
  (`/dashboard/providers` route included).
- Full live-PG suite: **1573 passed / 18 skipped**; the only failures are the
  4 pre-existing live-Gemini durability tests (need a fresh free-tier quota
  key) — verified unrelated to 19B.
- Docs: `docs/MULTI_PROVIDER_ROUTING.md` (new), `README.md`,
  `docs/ARCHITECTURE.md`, `workflow-status/PHASE19B_COMPLETION_REPORT.md`
  (new), this file. Phase 19C remains NOT started.

### Session 25 (August 4, 2026) — Phase 19C part 1: Cross-Repo Namespaces Demo + Force-Directed Graph Viz ✅

Closed two of the three remaining Phase 19C gaps. **Phase 19C part 2 (API/UI
surface + multi-repo acquisition wiring) is NOT started.**

**Backend (DevPilot/backend):**

- `scripts/demo_phase18.py` — added **demo I (cross_repository_namespaces)**:
  registers 3 repos (`acme-web`, `acme-api`, `acme-lib`), synthesizes one
  `_synthetic_run` per repo, links repos explicitly with
  `link_repositories` (`DEPENDS_ON_REPOSITORY` web→api→lib, `SHARES_LIBRARY`
  web↔api), then verifies org-scope merge (`scope=org`), local-scope isolation
  (`scope=local` returns only within-repo edges), and a
  `cross_repository_traversal` from `repo-acme-web` (hop 1 = api, hop 2 = lib).
  Uses `_repo_node_id`/`_db_url` helpers, so it persists to PG and is
  idempotent on re-run. Registered as `"I_cross_repository_namespaces"` in
  `main()` (9 demos A–I) + module docstring + summary print.
- Demo I passes standalone and in the full 9-demo run.

**Frontend (DevPilot/frontend):**

- `src/components/graph/ForceDirectedGraph.tsx` — NEW shared, reusable
  force-directed canvas: d3-force-free SVG simulation (manual velocity
  integration), pan/zoom/drag, color-by-type, size-by-degree, hover tooltip,
  labels. Exports `ForceGraph`, `hexFor`, `nodeTypeLabel`, `truncate`,
  `VizNode`, `VizEdge`. Removed ~270 lines of duplicated simulation code from
  the org-graph page.
- `src/app/dashboard/organization-graph/page.tsx` — refactored to import
  `ForceGraph` (casts `OrgRepository` via `as unknown as { [k: string]:
  unknown }`); behavior unchanged.
- `src/app/dashboard/engineering-graph/page.tsx` — NEW force-directed
  neighborhood view: state for `vizNodes/vizEdges/selectedVizId/hoveredId/
  neighborhoodDepth/neighborhoodRoot/vizLoading/vizError/viewReset`;
  `loadNeighborhood` calls `graphApi.neighborhood(nodeId, depth, 60)`; wired
  into `selectNode`/`jumpToNode` and `focusVizNode` for selecting viz nodes;
  depth selector (1–3), Reload/Reset-view/Clear buttons, empty + loading
  states. The structured list view remains the default; the graph appears
  after a node is selected.

**Validation:**

- Backend full deterministic suite (`-m "not live"`): **1568 passed / 18
  skipped / 1 failed** — the 1 failure is the pre-existing
  `test_wrapper_skips_cleanly_without_provider` environmental quirk (`.env`
  Gemini key ⇒ the wrapper subprocess runs live). No regressions vs baseline.
- `tests/test_organization_graph.py`: **37 passed**. The persistence
  roundtrip test was made idempotent against accumulated PG data — it now uses
  a per-run unique namespace (`rt-<uuid>-a/b`) and asserts *its* cross-edge
  survived a restart instead of a global `len(cross_edges()) == 1` (which
  breaks once any other run/demo persists cross-edges into the shared DB).
- Frontend: `npm run build` EXIT=0 — both graph pages + shared component
  compile, in-build lint + typecheck clean (18 static routes). `npm run lint`
  still has no config (interactive ESLint prompt); `next build` is the gate.
- Demo H (`ekg_driven_test_selection`) still FAILS in a full 9-demo run
  against accumulated PG: `impact-edge selection missing test_auth:
  ['auth/tests/test_auth.py', 'tests/test_session.py']` — historical
  TEST_SUITE rows from prior sessions' runs bleed into `select_tests_for_changes`.
  Pre-existing, passes on a fresh graph. Suggested fix (deferred): scope the
  selection to the current run's suite or clear stale EKG tables.

**Caveats / known issues (carried forward):**

- ✅ `git init` DONE — baseline commit `8598153` (454 files) created this
  session; commit early and often from here on.
- Demo I (like A–H) persists namespaces + cross-edges into `devpilot_dev`
  when `DATABASE_URL` is set; harmless and idempotent, but it does accumulate
  in the shared dev DB. The org persistence test now tolerates this.

### Session 26 (August 4, 2026) — Phase 19C part 2: Interactive EKG Visualization ✅

Implemented the final Phase 19C visualization direction (Phase 19D not
started). Replaced the legacy custom SVG canvas on `/dashboard/engineering-graph`
with a **production graph engine** (`@xyflow/react` React Flow v12) using
d3-force strictly as a seeded, deterministic layout algorithm.

**Backend (DevPilot/backend):**

- `app/services/engineering_graph_service.py` — `diff_versions(from_version,
  to_version=None)` returns `{from_version, to_version, added_nodes,
  removed_nodes, changed_edges, counts, per_version}` (incremental change-set,
  `ValueError` on invalid versions); `_fire_graph_broadcast`/
  `_run_graph_broadcast` + an `increment_version` broadcast hook (fire-and-forget).
- `app/services/ws_manager.py` — `broadcast_graph_update` on the `__graph__`
  channel. `app/api/v1/ws.py` — `WS /api/v1/ws/graph` (snapshot on connect +
  live `version_incremented`). `app/api/v1/engineering_graph.py` — `GET
  /api/v1/graph/diff` (HTTP 400 on invalid versions).
- Tests: `TestVersionDiff`, diff-endpoint tests, `TestGraphWebSocket`,
  `TestBroadcastGraphUpdate` → graph+ws suites **83 passed**.
- `scripts/demo_phase19c.py` — demos A–F, deterministic, no LLM: A bounded
  neighborhood expansion depth 1/2/3 + facets; B org merge / local isolation /
  bridge traversal (fixed: seed nodes tagged with their repo id; demo C depth
  3 to reach the full lineage); C palette contract (frontend covers 100% of the
  28 node types + 27 relationships) + relationship histogram + filtered edges;
  D `diff_versions` change-set; E live WS snapshot + `version_incremented` via
  `client.portal.call(lambda: graph.increment_version(...))`; F 3000-node
  ingest/query/neighborhood latency. **ALL PASS** (with `--json` for CI).

**Frontend (DevPilot/frontend):**

- `src/lib/graph/graphModel.ts` — pure model: `NODE_CATEGORY`/`NODE_HEX` (28
  types) + `RELATIONSHIP_HEX` (27 rels), `computeForceLayout` (seeded LCG,
  `initialPositions`), `applyViewFilters` (edge survives iff both endpoints),
  `snapshotFacets`, `summarizeDiff`.
- `src/lib/graph/useGraphSocket.ts` — module-level singleton WebSocket +
  `useSyncExternalStore`, exponential-backoff reconnects (1s→15s), pure
  `deriveGraphWsUrl`.
- `src/components/graph/InteractiveGraph.tsx` — React Flow engine: custom
  `GraphNodeView`, cached layout per graph signature, highlight/dim neighbors,
  MiniMap + relayout/fullscreen controls, virtualization above 200 nodes.
- `src/app/dashboard/engineering-graph/page.tsx` — toolbar (search, node-type /
  relationship / repo filters with counts, depth 1–3, Fit/Relayout/Refresh/
  Collapse/Clear), live WS badge + notice, stats cards, InteractiveGraph +
  provenance panel (evidence-only, prohibited-key filter), timeline with
  version-diff panel, relationship legend, breadcrumbs, keyboard shortcuts.
- `vitest.config.ts` + `test` scripts; `globals.css` React Flow theming; root
  layout imports `@xyflow/react/dist/style.css`.

**Validation:**

- Backend full deterministic suite (`-m "not live"`): **1602 passed / 18
  skipped / 1 failed** (the 1 failure remains the pre-existing
  `test_wrapper_skips_cleanly_without_provider` env quirk). No regressions.
- Frontend: **29 vitest tests passed** (graphModel 12, registryContract 4,
  useGraphSocket 4, engineeringGraph API 3) and `npm run build` EXIT=0
  (engineering-graph route 74.7 kB / 162 kB first load).
- `scripts/demo_phase19c.py` ALL PASS.

**Docs:** `docs/GRAPH_VISUALIZATION.md` (new), `docs/ENGINEERING_KNOWLEDGE_GRAPH.md`
(API/WS/frontend/testing/demos + new §22), `docs/ARCHITECTURE.md` (frontend),
`README.md`, and this log. Completion report:
`workflow-status/PHASE19C_COMPLETION_REPORT.md`.

**Caveats carried forward:** the pre-existing `test_wrapper_skips_cleanly_without_provider`
env failure. All Phase 19C items — interactive viz, multi-repo acquisition,
org-graph UI wiring, org-scope queries, and the demo-H stale-PG fix — are
complete and committed (`5cc371a`, `1644fb3`, `2cc929b`; see Session 27).

### Session 27 (August 4, 2026) — Phase 19C close-out: Multi-Repo Acquisition + Org-Graph UI Wiring + Demo-H Fix ✅

Closed the final Phase 19C directions left open by Sessions 25–26. Phase 19C is
now fully complete; no Phase 19D started.

**Multi-repo remote acquisition (backend):**

- `app/services/organization_graph_service.py` — `acquire_multi(manifest)`
  materializes multiple repository namespaces in one deterministic,
  evidence-only pass: `source="local"` ingests an existing checkout (offline),
  `source="github"` clones via the acquisition service; each spec may declare
  explicit cross-repository `relationships` (registered via `link_repositories`
  only — never LLM-inferred). Bounded by `MAX_REPOSITORIES_PER_ORG`.
- `app/api/v1/engineering_graph.py` — `POST /api/v1/graph/org/acquire-multi`
  (flat manifest array). Orchestrator + CLI wiring:
  `python -m app.cli graph org-acquire-multi --manifest <json> [--ingest]`.
- Frontend: `orgGraphApi.acquireMulti()` (`src/lib/api/organizationGraph.ts`) +
  "Acquire + Link (Phase 19C)" manifest form on `/dashboard/organization-graph`.

**Org-scope queries (cross-repo namespaces via API/CLI/frontend):**

- API: `GET /api/v1/graph/org/query?q=&scope=auto|local|organization&repository_id=`
  (scope-routed, `QueryScope.AUTO` merges linked repos when the planner decides),
  `GET /api/v1/graph/org/traversal/{id}`, `GET /api/v1/graph/org/stats`,
  `/org/repositories`, `/org/cross-edges`, `POST /org/repositories`, `/org/link`.
- CLI: `org-stats`, `org-repositories`, `org-cross-edges`, `org-query`,
  `org-traversal`.
- Frontend: org page scope selector (`auto` / `local` isolated / `organization`
  wide), query → graph merge with `in_repository` clustering, node Expand →
  cross-repo traversal, register/link forms.
- 28 new backend tests (org API endpoints, org-scope merging, multi-repo
  acquisition + wiring); frontend `organizationGraph.test.ts` contract tests.
  Demo G (`demo_phase18.py`) + demo I PASS in-memory and against live PG.

**Demo-H stale-PG fix (accumulated-shared-PG regression):**

- `select_tests_for_changes` scoped to the **newest TEST_SUITE per changed
  path** — recency keyed on `graph_version` (global monotonic counter bumped
  per ingested run and on re-ingest), then `created_at`, then `node_id` as a
  stable tiebreaker; iterates the raw reverse index to bypass
  `MAX_EDGES_PER_NODE` so the newest run's patch is never hidden behind
  historical edges. Two regression tests in `test_engineering_graph.py`
  (stale-bleed + re-ingested-run-wins-despite-older-created_at). Verified
  against an accumulated in-memory graph and the full suite.

**Validation:**

- Backend full deterministic suite (`-m "not live"`): **1602 passed / 18
  skipped / 1 failed** (the 1 failure remains the pre-existing
  `test_wrapper_skips_cleanly_without_provider` env quirk). No regressions.
- Organization-graph + multi-repo acquisition suites: **57 passed**.
- `scripts/demo_phase18.py` (demos A–I) **ALL PASS** — incl. G (org-scope
  query) and H (impact test selection, stale-PG fix exercised).
- Frontend: **29 vitest tests passed** + `npm run build` EXIT=0.

**Docs:** `AGENTS.md` (session memory), this log, and
`workflow-status/PHASE19C_COMPLETION_REPORT.md` updated to the final baseline.

**Caveats carried forward:** the pre-existing `test_wrapper_skips_cleanly_without_provider`
env failure. All Phase 19C items are committed: `5cc371a` (interactive viz),
`1644fb3` (multi-repo acquisition + org-graph UI wiring), `2cc929b` (demo-H
fix), `6f88fd1` (state/docs).

### Session 28 (August 4, 2026) — Phase 20 slice A1+A2: Multi-Repository Runs via Org Graph 🚀

First slice of the Phase 20 roadmap (`workflow-status/PHASE20_ROADMAP.md`,
committed in `131d848`): give an autonomous run a set of **auxiliary
repositories** that are materialized + linked into the organization graph
alongside the primary checkout — without touching the single-repo path.

**A1 — model surface (`app/models/orchestration.py`):**

- New `RepositorySpec` (repository_id/name/source local|github/owner/repo/
  path/ref/depth/relationships) mirroring the org graph's
  `MultiRepoAcquisitionSpec` + `summary()`.
- `RunSource.repositories: Optional[List[RepositorySpec]]` — strictly optional,
  so every existing run (single repo or none) is unchanged.
- `DevPilotRun.auxiliary_repositories` (recorded namespaces) + the same on
  `DevPilotRunResult`; new `EventType.AUXILIARY_REPOSITORIES_ACQUIRED`.

**A2 — orchestrator wiring (`app/services/orchestration_service.py`):**

- `_get_org_service()` — lazy, graceful-None factory (same pattern as the
  ContextEngine org hook).
- `_materialize_auxiliary_repositories(run)` — converts `RepositorySpec`s into
  `MultiRepoAcquisitionSpec`s and delegates to
  `OrganizationKnowledgeGraphService.acquire_and_link_repositories`
  (`ingest=True`). Deterministic + evidence-only: `source=local` is offline,
  only explicitly-declared relationships become cross-repo edges, primary
  `repository_path` is untouched, per-repo isolation preserved. Records the
  namespaces + emits the event; on failure moves the run to FAILED.
- Wired into `execute_run` right after the acquisition branch (covers both the
  GitHub and local paths), before analysis.
- API `POST /api/v1/runs` (`app/api/v1/orchestration.py`) + workflow
  (`app/workflows/orchestration.py`) + CLI `run --aux-repo ID=PATH`
  (`app/cli.py`) all accept/forward `repositories`.

**Tests — 10 new deterministic offline tests (`tests/test_phase20_multi_repo_run.py`):**

- Model: RepositorySpec roundtrip; `RunSource.repositories` optional
  (backwards compat) + parsing.
- Materialization unit: local aux repos registered as namespaces + cross edge
  linked + event emitted + primary untouched; no-repos/empty-list no-op; invalid
  local path → FAILED; org service unavailable → FAILED.
- execute_run integration: local primary + 2 aux repos → APPROVED with
  `auxiliary_repositories` on the result + AUX event; aux failure → FAILED.

**Validation:** full deterministic suite `-m "not live"` → **1612 passed / 18
skipped / 1 failed** (+10 new, zero regressions; the 1 failure remains the
pre-existing `test_wrapper_skips_cleanly_without_provider` env quirk).
Related suites (orchestration, multi-repo acquisition, org graph, run-store,
API contract, autonomy run-store, recovery hardening) all green.

**Next:** slice A3 — cross-repo planning context (surface org-graph evidence
for the primary repo's planner; browse the aux namespaces via the scope-aware
query path). The roadmap also has the API/CLI/frontend wiring note that
`repositories` is now accepted end-to-end.

### Session 29 (August 4, 2026) — Phase 20 slice A3: Cross-Repo Planning Context 🚀

Second slice of Phase 20: surface the org-graph evidence (auxiliary namespaces +
bridges materialized in A2) to the **planner** of an explicitly multi-repo run —
while single-repo runs keep strict isolation.

**ContextEngine (`app/services/context_engine.py`):**

- `build_context` gained a trailing `include_organization_context: bool = False`.
  When set, step 10b calls `_build_organization_graph_context(task, scope=QueryScope.ORGANIZATION)`
  instead of the default `QueryScope.AUTO`.
- `_build_organization_graph_context` accepts the scope and only applies the
  AUTO vocabulary gate (`plan.cross_repository`) when scope is AUTO — an explicit
  ORGANIZATION scope bypasses it. Empty/unavailable org graph still degrades to
  no items.

**OrchestrationService (`app/services/orchestration_service.py`):**

- The module-level `_get_org_service()` was refactored into an instance method
  `_get_org_graph()` with a cached `self._organization_graph` (same graceful-None
  pattern), so the org graph acquired in A2 is shared with context building.
- `_get_context_engine` now injects the shared graph:
  `ContextEngine(organization_graph=self._get_org_graph())` — the 1-run
  synchronous ordering (materialize aux repos → planner context) now sees the
  materialized namespaces deterministically.
- Planner call site in `_build_agent_context` passes
  `include_organization_context=True` **only** when `agent_type == "planner"` AND
  `run.source.repositories` AND `run.auxiliary_repositories` are both set — i.e.
  an explicitly multi-repo run that actually materialized. Single-repo and
  not-yet-materialized runs are byte-for-byte unchanged.

**Tests — 7 new deterministic offline tests:**

- Engine-level (`tests/test_organization_graph.py`,
  `TestOrgContextEngineIntegration`): forced ORGANIZATION scope surfaces org
  evidence for local-looking vocabulary that AUTO filters; forced scope with an
  empty org graph degrades; forced scope with no injected org graph (lazy empty
  fallback) degrades.
- Orchestrator-level (`tests/test_phase20_multi_repo_run.py`,
  `TestCrossRepoPlanningContext`): multi-repo + materialized run → planner
  context includes "Organization knowledge graph" evidence; single-repo run →
  isolated; multi-repo but not-yet-materialized → isolated; multi-repo with empty
  org graph → clean.
- A2 tests updated from `patch("..._get_org_service")` to
  `patch.object(orch, "_get_org_graph")` after the refactor (still 10/10 green).

**Validation:** full deterministic suite `-m "not live"` → **1619 passed / 18
skipped / 1 failed** (+7 new, zero regressions; the 1 failure remains the
pre-existing `test_wrapper_skips_cleanly_without_provider` env quirk). Org-graph
suite 60 passed, phase20 suite 14 passed, context-engine 55 passed,
orchestration 82 passed.

**Next:** slice A4 — per-repo scope enforcement (extend `ScopeController` +
`deterministic_review._check_file_scope` so a patch is validated against its own
repo's checkout, never cross-checkout). A5 (per-repo EKG ingestion) pairs with it.

### Session 31 (August 5, 2026) — Phase 20 slice A4: Per-Repo Scope Enforcement 🚀

Fourth slice of Phase 20: make repository isolation **deterministic and
enforced at every gate**. Before A4 a per-repo patch was declared alongside a
run but nothing stopped it being validated/applied against the wrong checkout.
A4 closes that: every repository has a scope, every patch is bound to one
repository, and the orchestrator validates + applies each patch against ITS OWN
checkout only.

**New model + engine layer:**

- `app/services/repository_scope.py` (new): `RepositoryScope` (repository_id,
  namespace, checkout_root, owned_paths, workspace_path) +
  `RepositoryScopeRegistry` — `register`/`resolve`/`check_path` (path
  containment, no `..` escape) /`validate_patch` (cross-repository claim +
  out-of-checkout rejection) /`to_dicts`/`from_dicts` (serialization).
- `app/models/coding.py` `PatchSet` gained `repository_id`/
  `repository_namespace`/`workspace_path`/`originating_run_id` (provenance).
- `app/models/orchestration.py`: `RepositoryPatchInput` (input spec: repository_id,
  namespace, workspace_path, patch) + `RepositoryPatchResult` (per-repo
  validation/application outcome with `summary()`, incl. `originating_plan_id`);
  `RunSource.repo_patches` + `DevPilotRun.repo_patches` + `DevPilotRunResult.repo_validation`;
  events `REPOSITORY_SCOPE_VIOLATION`/`REPOSITORY_PATCH_VALIDATED`/
  `REPOSITORY_PATCH_REJECTED`.
- `app/models/review.py`: `ReviewInput.extra_context` now carries
  `primary_repository_id`/`repository_patch_results`/`repository_scopes`.

**SafePatchEngine ownership gate:**

- `check_repository_ownership(patch)` — a patch bound to repository A is never
  validated/applied against repository B's checkout (cross-repository) nor may
  any path escape the owning checkout. Backwards compatible: unattributed
  patches behave as before. Wired into `dry_run`/`apply`.

**DeterministicReview DET-020 (CRITICAL, blocking):**

- `_check_repository_scope` produces a blocking `DET-020` finding when a
  pre-computed per-repo result is rejected, the primary patch escapes, or any
  changed path lands outside its owning repository scope.

**ScopeController (autonomy):**

- `set_repository_scopes`/`clear_repository_scopes`/per-repo evidence —
  rejected per-repo results surface as repository scope violations on
  autonomous runs; clean results stay clean.

**OrchestrationService wiring:**

- `_primary_repository_id` (stable `repo-<dir>` id),
  `_build_repository_scopes` (per-run cached registry from primary + aux
  namespaces + pending inputs), `_repository_scope_for`.
- `_stage_patch_validation` validates the primary patch vs the primary checkout
  AND each `repo_patches` input against its own checkout
  (`_validate_single_repo_patch`: hash enrichment vs own checkout, ownership
  gate, content validation); any rejection fails the stage (blocking isolation).
- `_stage_patch_application` applies each validated per-repo patch with an
  engine bound to that repo's checkout (`_apply_single_repo_patch`), mirrors the
  primary outcome onto its repo result.
- `execute_run` application gate fixed: it now looks for PENDING work (unapplied
  primary + validated-but-unapplied per-repo results) instead of
  `not run.repo_patches` — the old gate short-circuited application for every
  multi-repo run once validation populated results.
- Review/quality-gate `extra_context` gains `_repository_review_context(run)`;
  `_build_result` aggregates `repo_validation`; autonomy evidence populates
  `repository_validation`.

**API/CLI surface:**

- `POST /api/v1/runs` accepts `repo_patches` (malformed → HTTP 400) and returns
  `repo_validation` summaries; workflow `run_user_task`/`run_github_issue` pass
  them through; CLI `run --repo-patch ID=WORKSPACE=PATCH_JSON` +
  per-repo result display.

**Validation:** full deterministic suite `-m "not live"` → **1640 passed / 18
skipped / 1 failed** (+21 new A4 tests in `tests/test_phase20_repo_scope.py`,
zero regressions; the 1 failure remains the pre-existing
`test_wrapper_skips_cleanly_without_provider` env quirk). A4 suite 21/21,
phase20 suite 31 passed, orchestration 82 passed. `scripts/demo_phase20.py`
demos A–F ALL PASS (multi-repo surface, aux materialization, org planning
context, per-repo validation, cross-checkout rejection, end-to-end execute_run
with `repo_validation` aggregated).

**Next:** slice A5 — per-repo EKG ingestion (`record_run` already stamps
`repository_id`; ensure cross-repo runs ingest their patches into each repo's
namespace and link the run across namespaces via the org graph).

### Session 32 (August 5, 2026) — Phase 20 slice A5: Per-Repo EKG Ingestion 🚀

Fifth slice of Phase 20: after A4 guarantees a per-repo patch is validated and
applied only against its own checkout, A5 makes that evidence land in the
**right** namespace of the organization graph. Before A5 a cross-repo run's
shared evidence went to the org-level graph but the per-repo patches were not
ingested into each repository's own knowledge namespace, so repo-scoped EKG
queries could not see them.

**New org-graph method:**

- `OrganizationKnowledgeGraphService.record_run_across_namespaces(run,
  reasoning_outcome=None)` — shared evidence first via
  `self._org_graph.record_run(...)` (org-level namespace), then each
  `run.repo_patches[i]` is ingested into ITS OWN repository namespace:
  `_ingest_run_into_repository_namespace(graph, result, run_id)` adds a RUN
  node, REPOSITORY node (`_repo_node_id(repo_id)`, e.g. `REPO::repo-b`), PATCH
  node (payload: `files_changed`, `files`, `validation_status`,
  `application_status`, `changes_applied`, `changes_attempted`) and FILE nodes
  for the first 10 changed files, with REFERENCES RUN→REPO / RUN→PATCH and
  MODIFIES PATCH→FILE edges; unregistered namespaces are skipped gracefully.
  Auxiliary repositories that were materialized but produced no patch results
  are still counted as involved.
- `_link_run_to_repositories(run_id, repo_ids)` — adds org-level REFERENCES
  edges from the RUN node to each involved repository's cross-namespace edge
  target (`_repo_node_id(repo_id)`), linking the run across namespaces.
- Both helpers run the same `increment_version` + node/edge/version/semantic
  persistence used by `record_run`.

**Orchestrator wiring:**

- `_ingest_into_graph` now delegates to `record_run_across_namespaces` for any
  cross-repo run (`run.repo_patches` or `run.source.repo_patches` or
  `run.auxiliary_repositories` set); single-repo runs keep the existing
  `graph.record_run` path. Org-graph failures fall back to the EKG record as
  before.

**Model + bug fixes:**

- `RepositoryPatchResult.changed_files: List[str]` added (default `[]`) and
  populated as `[c.path for c in patch.changes]` on both per-repo and primary
  patch results; `summary()` now includes `changed_files[:20]`.
- Fixed a **pre-existing latent bug**: `_validate_single_repo_patch` called the
  async `_enrich_patch_hashes` without `await`, so per-repo MODIFY/DELETE patches
  were always rejected (original-hash enrichment never ran). The method is now
  `async def` and awaits the enrichment; both call sites in
  `_stage_patch_validation` updated.

**Demo + tests:**

- `scripts/demo_phase20.py` gained demo G (`G_per_repo_ekg_ingestion`): runs an
  end-to-end multi-repo `execute_run`, then asserts per-repo ingest evidence —
  `changed_files` on results and REPO::/PATCH:: nodes + edges in each
  repository's namespace via `acquire_run_evidence`. Demo G re-clears its
  checkouts so re-runs are deterministic (a prior demo leaving `feature.py`
  behind would otherwise fail the CREATE patch validation). Demos A–G ALL PASS.
- New `tests/test_phase20_repo_ingestion.py` (13 tests): `TestChangedFilesEvidence`
  (default, populated, summary), `TestRecordRunAcrossNamespaces` (per-repo node
  types/edges/payload, cross-namespace RUN→REPO links, unregistered-namespace
  skip, orphan/aux-only involvement), `TestIngestIntoGraphRouting` (single-repo
  → `record_run`, cross-repo → `record_run_across_namespaces`, fallback on
  org-graph failure), plus an end-to-end `execute_run` test with real aux
  materialization asserting per-repo evidence in the aux namespace.

**Validation:** A5 suite 13/13; A4+A5 34 passed; related suites (A4, A5,
org-graph, multi-repo run, multi-repo acquisition) 108 passed with zero
regressions. `scripts/demo_phase20.py` demos A–G ALL PASS against live PG
(plus in-memory). `changed_files` evidence also surfaces per repo in
`repo_validation`/`repository_validation` outputs.

**Next:** slice A6 — dashboard run form exposes optional auxiliary
repositories (API/CLI already accept `repositories`/`repo_patches` from
A1/A2/A4; only the frontend surface remains).

### Session 33 (August 5, 2026) — Phase 20 slice A6: Dashboard Aux-Repo + Run-Detail Multi-Repo Surface 🚀

Sixth (final) slice of Phase 20 workstream A: the multi-repo vertical is now
fully surfaced end-to-end in the dashboard. A1/A2 wired the create-side API/CLI
(`repositories`/`--aux-repo`), A4 added `repo_patches`, and A5 ingested
per-repo evidence; A6 closes the two remaining UI + read-API gaps so an operator
can create a multi-repo run from the form and see the per-repo validation
outcome on the run-detail page.

**Backend — run-detail API surface:**

- `_sanitize_run` (`backend/app/api/v1/orchestration.py`) now exposes
  `auxiliary_repositories` (the raw materialized spec list, with
  `repository_id`/`namespace_id`/`path`/`source_type`) and `repo_validation`
  (`[r.summary() for r in run.repo_patches]`, i.e. per-repo
  status/changes/changed_files/errors) on `GET /api/v1/runs/{id}`. The
  create-side `repositories` field was already present from A1/A2.

**Frontend:**

- `frontend/src/lib/api/client.ts`: new `AuxiliaryRepositorySpec` type
  (mirrors backend `RepositorySpec`/`MultiRepoAcquisitionSpec` — local|github,
  owner/repo/ref/depth, relationships) and `RepositoryPatchValidation` type;
  `RunDetail`/`RunResult` gained optional `auxiliary_repositories` +
  `repo_validation`; `runsApi.create` accepts optional `repositories`.
- `CreateRunModal` (`dashboard/runs/page.tsx`): aux-repo editor with dynamic
  add/remove rows — each row is a local path OR a github owner/repo/ref;
  invalid rows (no id, or local without path, or github without owner+repo) are
  dropped client-side before submit; rows submitted as `repositories` only when
  non-empty.
- Run-detail page (`dashboard/runs/[id]/page.tsx`): Source card now lists the
  auxiliary repositories, and a new "Repository Validation" card renders each
  repo's validation/application status, changes applied/attempted, changed
  files (first 10, truncated), and first validation error.

**Tests:**

- `TestRunDetailApiSurface` (2 tests) added to
  `tests/test_phase20_repo_ingestion.py` (now 15 total): asserts `_sanitize_run`
  emits the new fields and that `GET /api/v1/runs/{id}` returns them through the
  HTTP client.
- New `frontend/src/lib/api/client.test.ts` (2 tests, fetch-mocked): verifies
  `runsApi.create` forwards auxiliary `repositories` verbatim and omits them
  when none supplied.
- Hardened a second TTL-boundary flake: `test_exhausted_marker_expires_after_ttl`
  simulated exactly `marked_at + ttl`, but the provider prunes with
  `now - ts >= ttl`, so float rounding at the boundary could flip the `>=`;
  now uses `marked_at + 3601.0` (matching the earlier fix to
  `test_chat_recovers_preferred_model_after_ttl`).

**Validation:** targeted suite (llm_providers, phase20_repo_ingestion,
api_contract, api_durability, phase20_repo_scope) **83 passed / 1 known env
failure**; full deterministic suite **1655 passed / 18 skipped / 1
pre-existing env failure** (`test_wrapper_skips_cleanly_without_provider`);
frontend vitest **39/39** (6 files); `next build` EXIT=0;
`scripts/demo_phase20.py` demos A–G ALL PASS against live PG.

### Session 34 (August 5, 2026) — Phase 20B slice B2: Typed Per-Capability Provider Fallback Chains 🚀

First slice of Phase 20B (production reliability): typed fallback lists per
capability (`DEVPILOT_LLM_PROVIDER_FALLBACKS`). Today the router has ONE global
priority chain (`PROVIDER_PRIORITY`), so a coding-generation failure can fall
back to a model unfit for long output. B2 lets each agent stage route through
its OWN provider chain.

**Capability model:**

- `Capability` enum (`app/llm/router.py`): `analysis`, `planning`, `coding`,
  `testing`, `review`, `reasoning`, `general` (canonical order for the entry
  union).
- `LLMConfig.capability` field (`app/llm/base.py`) — a plain dataclass field
  ignored by providers; the router reads it from `config.capability` or the
  explicit `chat(..., capability=...)` kwarg. No provider signature changes, so
  routing-disabled mode stays fully compatible.
- `Settings.LLM_PROVIDER_FALLBACKS` (`app/config.py`) + validator parsing
  `cap:prov1,prov2;cap2:prov3` (also `=` separators, JSON dict, mixed case —
  all lower-cased; empty segments dropped).

**Router enforcement (`app/llm/router.py`):**

- `_fallbacks()` / `_priority_for()` / `_candidate_names()` / `_ordered_entries(capability)`
  — a configured capability chain is **authoritative** for that kind of call:
  failover only walks its providers, never the global list. Unlabelled calls and
  capabilities without an override keep the global `PROVIDER_PRIORITY`. A typed
  chain with no viable provider raises `ProviderNotAvailableError` (no silent
  leakage into the global list).
- `_build_entries()` registers the **union** of global + all capability chains,
  so a provider referenced only in a capability chain still gets
  health/circuit/metrics observability.
- `chat_stream` honours the typed chain before the first token (mid-stream
  failures unchanged).
- `config_snapshot()` now exposes `provider_fallbacks` and the real
  `provider_priority` names (moved out of the generic redactor, which was
  masking every string inside lists — provider names are not secrets).

**Agent wiring (all 7 stages label their calls):** repo_analyzer +
issue_analyzer → `analysis`, planner → `planning`, coding_agent + fix_agent →
`coding`, test_agent → `testing`, reviewer → `review`.

**Tests (12 new):** `TestCapabilityFallbacks` (7) — typed chain skips the
global priority, `config.capability` selects the chain, unlabelled calls keep
the global chain, capability does not leak into global, streaming uses the
typed chain, capability-only providers are registered + health-monitored,
`config_snapshot` exposes `provider_fallbacks`; `TestProviderFallbacksConfig`
(4) — env-string parsing, `=` separators, empty values, case normalisation;
`TestPlannerAgent.test_plan_call_uses_planning_capability` (1) — the planner
passes `capability="planning"` in its `LLMConfig`.

**Validation:** `test_provider_router.py` now 54 tests, all targeted agent
suites (coding/issue_analyzer/repair/agent-graph-integration/llm_providers)
187 passed; full deterministic suite (`-m "not live"`) **1667 passed / 18
skipped / 1 failed** (only the pre-existing
`test_wrapper_skips_cleanly_without_provider` env quirk). Docs updated:
`docs/MULTI_PROVIDER_ROUTING.md` §2.7 (typed fallbacks) + §9 future directions,
`backend/.env.example`, `README.md` (Phase 20B blurb + test counts),
`workflow-status/PHASE20_ROADMAP.md` (B2 ✅).

**Next:** Phase 20B — B3 mid-stream token-loss failover (resend prompt with
full prefix) is the remaining unblocked B slice; B1 (billing on the Gemini key
or Vertex AI) still needs a user infra decision. Then workstream D (org-graph UI
parity on the React Flow engine) and E (extra test-framework parsers).

---

### Session 35 (August 5, 2026) — Phase 20B slice B3: Mid-Stream Token-Loss Failover 🚀

**Goal:** when a long streaming generation drops AFTER tokens have already been
delivered, recover the generation instead of surfacing an error — resend the
prompt with the full prefix so the response continues from where it was cut off.

**Approach.** `ProviderRouter.chat_stream` already failed over **before** the
first token; a mid-stream failure surfaced as `LLMError` (retrying would
duplicate tokens). B3 adds token-loss recovery:

- `_continuation_messages(messages, prefix_parts)` rebuilds the prompt for the
  hand-off: the already-delivered prefix is embedded as
  `<partial>…</partial>` context with an explicit do-not-repeat instruction, so
  the next provider produces **only the remaining text** — no duplicated tokens,
  no lost generation, and the caller keeps the tokens already received.
- On a mid-stream failure with resume capacity and a remaining candidate, the
  router records the hand-off and continues on the next provider with the
  continuation prompt; the accumulated `prefix_parts` carry forward across every
  hop in the chain.
- **Bounded** by the new `DEVPILOT_PROVIDER_STREAM_RESUME_MAX` (default `3`,
  range 0–20) per streaming call: once the budget is spent — or the last provider
  drops with no candidate remaining — the failure surfaces as `LLMError`
  (with the caller keeping the partial output). Setting the value to `0`
  disables mid-stream recovery entirely (previous behaviour).
- **Observability:** `ProviderHealth.resumes` + `record_resume()` surfaced in
  `provider_snapshots()` and `metrics_snapshot().totals["resumes"]`; the failover
  event for a hand-off carries `reason="mid_stream_token_loss"` and
  `mid_stream=true`; `health_snapshot()` exposes `stream_resume_max`.

**Files:** `backend/app/llm/router.py` (`ProviderHealth`, `MetricsRegistry`,
`_continuation_messages`, `chat_stream`, `health_snapshot`),
`backend/app/config.py` (`PROVIDER_STREAM_RESUME_MAX`), `backend/.env.example`,
`backend/tests/test_provider_router.py` (+5 tests: 4 stream-resume behaviour +
1 config parse → 59 total).

**Validation:** targeted `test_provider_router.py` 59 passed; full deterministic
suite (`-m "not live"`) **1672 passed / 18 skipped / 1 failed** (only the
pre-existing `test_wrapper_skips_cleanly_without_provider` env quirk). Docs
updated: `docs/MULTI_PROVIDER_ROUTING.md` §2.8 (rewritten: streaming failover +
token-loss recovery) + §3 config table + §9 future directions,
`backend/.env.example`, `workflow-status/PHASE20_ROADMAP.md` (B3 ✅). Committed.

**Next:** Phase 20B — B1 (billing on the Gemini key or Vertex AI) still needs a
user infra decision; all unblocked B slices (B2, B3) are done. Then workstream D
(org-graph UI parity on the React Flow engine) and E (extra test-framework
parsers). Workstream C (live E2E) re-runs after a Gemini quota reset.

---

### Session 36 (August 5, 2026) — Phase 20 Workstream D: Org-Graph UI Parity on the React Flow Engine

**Goal:** upgrade `/dashboard/organization-graph` from the legacy
`ForceDirectedGraph` to the same React Flow engine + timeline diff + live WS
used on `/dashboard/engineering-graph` (`InteractiveGraph.tsx`,
`useGraphSocket.ts`), and delete the duplicated legacy implementation.

**Pre-audit (mandatory, done before any code):** confirmed `.ai-memory/` did not
exist anywhere (memory pivot: `AGENTS.md` + `workflow-status/*`); inventoried the
graph infrastructure. Findings — already implemented & reused: `InteractiveGraph.tsx`
(React Flow v12 engine), `useGraphSocket.ts` (live WS singleton), `graphModel.ts`
(VizNode/VizEdge, registries, filters, seeded layout, `summarizeDiff`), the EKG +
org API clients, timeline diff API. Missing: the org page still rendered on
`ForceGraph`. Tech debt: `ForceDirectedGraph.tsx` duplicated `NODE_HEX`/`hexFor`/
`nodeTypeLabel`/`truncate`/`VizNode`/`VizEdge` from `graphModel.ts`.

**Changes.**

- **New** `frontend/src/lib/graph/orgGraphModel.ts` — pure org→Viz mappers:
  `repoVizId`, `repoNodeId` (40-char cap), `reposToVizNodes`,
  `crossEdgesToVizEdges`, `orgNodesToVizNodes`, `orgEdgesToVizEdges` (synthesized
  id fallback), `clusterVirtualEdges` (`in_repository` virtual cluster edges),
  `mergeOrgGraph` (add-only dedup).
- **Modified** `frontend/src/lib/api/engineeringGraph.ts` — optional
  `GraphNode.repository_id?: string` (backend `_node_to_api` always emits it at
  the top level — verified `backend/app/api/v1/engineering_graph.py:62`).
- **Migrated** `frontend/src/app/dashboard/organization-graph/page.tsx` →
  `InteractiveGraph` with search filter (`applyViewFilters`), neighbor
  highlight + focus, **Timeline Diff** section (`graphApi.version`/`diff` +
  `summarizeDiff`, per-version + added/removed lists), and **live WS** badge +
  auto-refresh on `version_incremented`. Kept org stats cards, scope query,
  register/link/acquire forms, and the cross-edge inspector.
- **Deleted** `frontend/src/components/graph/ForceDirectedGraph.tsx` — sole
  consumer migrated; duplicated registries removed.
- **New** `frontend/src/lib/graph/orgGraphModel.test.ts` — 10 tests.

**Validation:** frontend vitest **49 passed (7 files)** — 39 existing + 10 new;
`next build` EXIT=0 (18 routes, types + lint clean). Backend untouched (D is
pure frontend). Docs updated: `workflow-status/PHASE20_ROADMAP.md` §D ✅,
`AGENTS.md` Session 36. `.ai-memory/` created at workspace root.

**Next:** Phase 20B — B1 (billing on the existing Gemini key) ✅ DONE in
**Session 37**; B2 (Session 34) and B3 (Session 35) are done too — Phase 20B is
COMPLETE. Then workstream E (extra test-framework parsers). Workstream C (live
E2E) re-runs after a Gemini quota reset.

---

### Session 37 (August 5, 2026) — Phase 20B slice B1: Paid Gemini Tier (billing on the existing key) ✅

**Goal:** close recommendation 3 — the production path for the Gemini LLM. User
decision: **attach billing to the existing AI Studio `GEMINI_API_KEY`** (same
key format, no Vertex migration, no provider/auth code change).

**Changes.**

- **`backend/app/config.py`** — `DEVPILOT_GEMINI_TIER` (`free|paid`, default
  `free`) with a validator that rejects unknown values, and
  `DEVPILOT_GEMINI_PAID_MODELS` (comma-separated or JSON list; lower-cased,
  deduped preserving first-seen order; empty when unset).
- **`backend/app/llm/providers/gemini.py`** — `GeminiProvider` is now
  tier-aware. Paid tier: **no** cross-model daily-quota failover, **no** 24h
  exhaustion markers (`_first_available` always returns the preferred model);
  a genuine quota/billing error fails fast with a clear
  "paid-tier call failed (check your plan and billing)" `LLMError` instead of
  the free-tier "wait for midnight" all-exhausted error; transient per-minute
  429s keep the existing exponential-backoff retry. `DEVPILOT_GEMINI_PAID_MODELS`
  pins the paid candidate pool (first entry = `default_model`; free tier
  ignores it). New introspection: `provider.tier`, `provider.model_candidates`.
  Free tier is byte-for-byte unchanged (default).
- **`backend/app/llm/router.py`** — `config_snapshot()` now exposes
  `data.gemini = {tier, paid_models}` (secret-safe — model names only).
- **`backend/app/api/v1/providers.py`** — `POST /api/v1/providers/test` adds
  `gemini_tier` + `gemini_models` to the response when the active provider is
  Gemini, so a paid key can be self-checked in the intended mode.

**Files:** `backend/app/config.py`, `backend/app/llm/providers/gemini.py`,
`backend/app/llm/router.py`, `backend/app/api/v1/providers.py`,
`backend/.env.example`, `backend/tests/test_llm_providers.py` (+5 paid-tier
provider tests `TestGeminiPaidTier` + 6 config tests `TestGeminiTierConfig`),
`backend/tests/test_provider_router.py` (+1 `config_snapshot` tier test → 60
total).

**Validation:** targeted `test_llm_providers.py` + `test_provider_router.py`
94 passed; full deterministic suite (`-m "not live"`) **1684 passed / 18
skipped / 1 failed** (only the pre-existing
`test_wrapper_skips_cleanly_without_provider` env quirk). Docs updated:
`docs/GEMINI_API_KEY_REPORT.md` (§7.1 paid-tier knob + recommendation 3),
`backend/.env.example`, `workflow-status/PHASE20_ROADMAP.md` (B1 ✅,
Phase 20B COMPLETE). Frontend untouched (B1 is backend + docs).

**Next:** Phase 20B is **COMPLETE** (B1, B2, B3). Remaining Phase 20: workstream
E (extra test-framework parsers); workstream C (live E2E:
`scripts/demo_phase17.py --live`, `scripts/verify_api_durability.py --live`)
re-runs after a Gemini quota reset — a paid tier now makes unlimited runs
possible without waiting for the daily reset.


### Session 38 (August 5, 2026) — Phase 20 workstream E: unittest XML / Vitest JSON / Jest JSON parsers ✅

**Goal:** close the last Phase 20 workstream — replace the pytest-only parser
with dedicated parsers for the frameworks the docs had listed as falling back
to `GenericResultParser` (`docs/TESTING_AND_EXECUTION.md:250`).

**Changes.**

- **`backend/app/testing/parsers/unittest_xml_parser.py`** (new) —
  `UnittestXMLParser` parses JUnit-style XML from `xmlrunner` /
  `unittest-xml-reporting`: `testsuites`/`testsuite` root via
  `xml.etree.ElementTree`; aggregates `tests`/`failures`/`errors`/`skipped`
  (top-level attrs preferred, suite sum fallback, passed = total − failed −
  skipped); per-`testcase` `failure`/`error`/`skipped` children →
  `TestFailure` with `module.Class.test_name`, module/class→path heuristic
  (`tests.test_example.Tests` → `tests/test_example.py`), message + `type` +
  traceback text, `line N` extraction, and `classify_message` on
  type+message+traceback.
- **`backend/app/testing/parsers/vitest_json_parser.py`** (new) —
  `VitestJsonParser` parses `--reporter=json` output: top-level
  `numTotalTests`/`numPassedTests`/`numFailedTests`/`numPendingTests`, per-suite
  `testResults` (suite `name` → file path), per-assertion `fullName` +
  `ancestorTitles` + `failureMessages` (message, stack, first `path:line:col` →
  line number). Discriminates from Jest by the ABSENCE of `perfStats` on suites
  (Vitest never emits it). JSON located even when embedded in runner text;
  counts never fabricated.
- **`backend/app/testing/parsers/jest_json_parser.py`** (new) — `JestJsonParser`
  parses `--json` output with the same top-level shape, discriminated by
  `perfStats` PRESENCE; `failureMessages` stack frames give file/line.
- **`backend/app/services/testing_service.py`** — default parser chain is now
  `PytestResultParser → UnittestXMLParser → VitestJsonParser → JestJsonParser →
  GenericResultParser` (most specific first; generic still the unconditional
  fallback).

**Files:** `backend/app/testing/parsers/unittest_xml_parser.py`,
`backend/app/testing/parsers/vitest_json_parser.py`,
`backend/app/testing/parsers/jest_json_parser.py` (all new),
`backend/app/services/testing_service.py`, `backend/tests/test_testing.py`
(+12 tests: `TestUnittestXMLParser` 4, `TestVitestJsonParser` 4,
`TestJestJsonParser` 4 incl. `test_default_service_registers_all_parsers` chain
order).

**Validation:** targeted parser tests 22 passed; `tests/test_testing.py` 106
passed; full deterministic suite (`-m "not live"`) **1696 passed / 18 skipped /
1 failed** (only the pre-existing `test_wrapper_skips_cleanly_without_provider`
env quirk). Docs updated: `docs/TESTING_AND_EXECUTION.md` framework table
(unittest/Vitest/Jest rows now dedicated parsers), `README.md` (workstream E
blurb + counts 1696), `workflow-status/PHASE20_ROADMAP.md` (E ✅, Phase 20
COMPLETE). Frontend untouched (E is backend only).

**Next:** **Phase 20 is COMPLETE** (A1–A6, B1–B3, D, E). Remaining: workstream C
(live E2E: `scripts/demo_phase17.py --live`,
`scripts/verify_api_durability.py --live`) re-runs after a Gemini quota reset —
a paid tier now makes unlimited runs possible without waiting for the daily
reset.


