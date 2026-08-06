# DevPilot Architecture

> Autonomous Multi-Agent Software Engineering Platform

## System Overview

DevPilot accepts a repository and a development task, then coordinates specialized AI agents to perform the full software engineering lifecycle — analysis, planning, coding, testing, fixing, review, and quality gate.

**Current Phase:** Phase 18 — Engineering Knowledge Graph ✅

**Previous:** Phase 17 — Collaborative Reasoning & Evidence Consensus ✅

**Layered reasoning architecture (Phases 15–18):**

```text
EngineeringKnowledgeGraphService (Phase 18) — unified temporal knowledge layer
        ▲  nodes / edges / versions / provenance / history / query planner
CollaborativeReasoningEngine (Phase 17) — decides whether evidence AGREES
        ▲  consensus / contradictions / notebook / confidence
CollaborationService (Phase 15) — records WHAT agents produced / shared
        ▲  handoffs / decisions / conflicts / memory promotion
OrchestrationService (Phases 10/11) — coordinates agent stages
```

The EKG re-uses Phase 12 semantic graph, Phase 13/14 repository memory,
Phase 15 collaboration, Phase 16 autonomy, and Phase 17 reasoning as typed
nodes and temporal edges — see
[`docs/ENGINEERING_KNOWLEDGE_GRAPH.md`](ENGINEERING_KNOWLEDGE_GRAPH.md).

---

## Full Pipeline Architecture (Phases 1–10 + Database)

```
                    PHASES 1–5: INTELLIGENCE & PLANNING

Task / GitHub Issue
        ↓
Repository Intelligence (Phase 2) → RepositoryProfile
        ↓
Issue Analysis (Phase 4) → StructuredRequirements
        ↓
Planning (Phase 4) → ImplementationPlan
        ↓
Code Indexing & RAG (Phase 5) → RetrievedContext

────────────────────────────────────────────────
                PHASE 6: CODING
────────────────────────────────────────────────

CodingAgent → PatchSet → PatchValidator → SafePatchEngine → Modified Workspace

────────────────────────────────────────────────
                PHASE 7: TESTING
────────────────────────────────────────────────

TestAgent → ExecutionPlan → ExecutionPolicy → ControlledExecution → TestRunResult

────────────────────────────────────────────────
              PHASE 8: REPAIR
────────────────────────────────────────────────

FailureDiagnosis → FixAgent → RepairPolicy → PatchValidator → SafePatchEngine → TestAgent
                        ↕
                  Bounded Loop (max 3 attempts)

────────────────────────────────────────────────
              PHASE 9: REVIEW
────────────────────────────────────────────────

ReviewContextBuilder → DeterministicReview + ReviewerAgent → EvidenceValidation
        ↓
QualityGate → APPROVED | REJECTED | NEEDS_HUMAN_REVIEW

────────────────────────────────────────────────
           PHASE 10: ORCHESTRATION
────────────────────────────────────────────────

                      ONE RUN
                         │
                         ▼
              OrchestrationService
                         │
              ┌──────────┼──────────┐
              │          │          │
          ANALYSIS   PLANNING    RETRIEVAL
              │          │          │
              └──────────┼──────────┘
                         │
                      CODING
                         │
                  PATCH → TESTING → REPAIR (loop)
                         │
                      REVIEW
                         │
                   QUALITY GATE
                         │
            ┌────────────┼────────────┐
            ▼            ▼            ▼
       APPROVED      REJECTED       NEEDS
                                    HUMAN
                                    REVIEW
```

### Key Design Principles

- **Provider-independent LLM abstraction** — swap OpenAI, Anthropic, or local models
- **Deterministic security first** — LLMs propose, deterministic gates dispose
- **Bounded loops** — every iterative process has strict attempt limits
- **Human-in-the-loop safety** — approval gates before consequential write operations
- **Modular agents** — each agent has a single responsibility
- **Orchestration coordinates authority, never acquires it**
- **Database configuration-driven** — all connection info in `DATABASE_URL`, no hard-coded host/port

---

## Phase Architecture

### Phase 1 — Foundation (Complete ✓)

- Backend: FastAPI + Pydantic
- Agent abstraction (BaseAgent, AgentRegistry)
- LLM provider abstraction (provider-independent)
- GitHub service (API interaction)
- Repository Analyzer Agent (GitHub remote analysis)
- Issue Analyzer Agent (LLM-based issue parsing)
- 82 tests

### Phase 2 — Repository Intelligence Engine (Complete ✓)

- Local repository analysis (no LLM required)
- 9 modular detector services
- RepositoryProfile output model
- CLI + REST API
- LangGraph-ready workflow
- 60+ tests

### Phase 3 — GitHub Read Integration (Complete ✓)

```text
GitHub API (REST v3) → GitHubService → RepoAcquirer → RemoteAnalysisWorkflow → RepositoryProfile
```

- Typed GitHub models (repos, branches, issues, PRs)
- Safe repository acquisition via Git CLI
- Remote analysis pipeline
- Pagination, rate limits, retry, token redaction
- 5 API endpoints + 3 CLI commands
- 32 tests

### Phase 4 — Issue Analysis & Planning (Complete ✓)

```text
User Task / GitHub Issue
    ↓
IssueAnalyzerAgent (LLM) → StructuredRequirements
    ↓
Planner Agent (LLM) → ImplementationPlan
    ↓
PlanValidator (deterministic) → Validated Plan
```

- Planner Agent (LLM-based, provider-independent)
- PlanValidator (100% deterministic, no LLM calls)
- PlanningService orchestrator
- Prompt injection boundaries
- 74 tests

### Phase 5 — Code-Aware Repository Indexing & Hybrid RAG (Complete ✓)

```text
Repository Code → IndexBuilder → [Lexical Index | Symbol Index | Vector Index]
    ↓
HybridRetriever → PlanContextRetriever → RetrievedContext
```

- Code parsing (Python AST, JS/TS/Go/Java/Rust regex fallback)
- Symbol extraction (classes, functions, methods, imports)
- Lexical index (BM25-like), Symbol index, Vector index (cosine similarity)
- Hybrid retrieval with 4-signal weighted rank fusion
- Plan-aware retrieval integrating Phase 4 plans
- 57 tests

### Phase 6 — Coding Agent & Safe Patch Engine (Complete ✓)

```text
Plan + Context → CodingAgent (LLM) → PatchSet
    ↓
PatchValidator (deterministic) → SafePatchEngine → Modified Workspace
```

- Coding Agent (LLM-based PatchSet generation)
- PatchValidator (path safety, hash verification, size limits)
- SafePatchEngine (atomic writes, unified diffs, rollback)
- WorkspaceService (isolated writable copies)
- 43 tests

### Phase 7 — Test Agent & Controlled Execution Engine (Complete ✓)

```text
Workspace → TestAgent → ExecutionPlan
    ↓
ExecutionPolicy (deterministic) → ControlledExecutionEngine
    ↓
Result Parsers → TestRunResult + TestFailure[]
```

- Test Agent (deterministic by default, LLM optional)
- ExecutionPolicy (executable allowlist, argument validation, script inspection)
- ControlledExecutionEngine (asyncio subprocess, timeout, env sanitization)
- Pytest + Generic result parsers
- 79 tests

### Phase 8 — Fix Agent & Bounded Repair Loop (Complete ✓)

```text
TestRunResult → FailureDiagnosisService (deterministic)
    ↓
FixAgent (LLM) → RepairProposal → RepairPolicy (deterministic)
    ↓
PatchValidator → SafePatchEngine → TestAgent
    ↓
Bounded Loop (max 3, progress/worsening detection, rollback)
    ↓
RepairResult
```

- FailureDiagnosisService (deterministic triage, 11 × 2 classification matrix)
- RepairPolicy (test tampering, config weakening, path safety, dangerous content)
- FixAgent (LLM-powered, trust boundaries, structured JSON)
- RepairService (progress detection, rollback, fingerprinting)
- 77 tests

### Phase 9 — Reviewer Agent & Deterministic Quality Gate (Complete ✓)

```text
Final Workspace + Requirements + Plan + Patch History + Test Evidence
    ↓
ReviewContextBuilder → DeterministicReview + ReviewerAgent
    ↓
EvidenceValidator → ReviewReport
    ↓
QualityGate (deterministic) → APPROVED | REJECTED | NEEDS_HUMAN_REVIEW
```

- ReviewerAgent (two-mode: deterministic + LLM-assisted, provider-independent)
- ReviewContextBuilder (bounded context, secret redaction, configurable budget)
- DeterministicReview (21 DET-XXX check IDs across 9 categories)
- EvidenceValidator (hallucination protection, file/requirement/step validation)
- QualityGate (100% deterministic, hard rejection rules, reason codes)
- 66 tests

### Phase 10 — End-to-End Multi-Agent Orchestration (Complete ✓)

```text
User Task / GitHub Issue
    ↓
DevPilotRun → OrchestrationService
    ↓
[Analysis Stages] → [Planning Stage] → [Retrieval Stage]
    ↓
[Coding Stage] → [Patch Validation/Application]
    ↓
[Testing Stage] ─── [Repair Stage (if failed)]
    ↓
[Review Stage] → [Quality Gate] → Decision
```

| Component | Module | Purpose |
|-----------|--------|---------|
| `DevPilotRun` | `models/orchestration.py` | Run abstraction with all stage artifacts |
| `RunStateMachine` | `models/orchestration.py` | Deterministic transition validation |
| `OrchestrationService` | `services/orchestration_service.py` | Pipeline coordinator (DI all Phase 1–9 services) |
| `RunStore` | `services/run_store.py` | Protocol + InMemoryRunStore |
| `OrchestrationWorkflow` | `workflows/orchestration.py` | Standard workflow entry points |
| Events API | `models/orchestration.py` | EventType (17 types), RunEvent, structured logging |
| Orchestration API | `api/v1/orchestration.py` | 6 REST endpoints |
| `devpilot run` | `cli.py` | CLI command |
| Runs Dashboard | `frontend/src/app/dashboard/runs/` | List + detail pages |

- 50 tests (state machine, happy/repair/rejection/cancel/security paths)
- **653 total tests**, 0 failed, 4 skipped

---

### Database Infrastructure (Ready for Phase 11) ✅

```text
DevPilot Application
    ↓
app/db/database.py (async engine, connection pool, verification)
    ↓
SQLAlchemy 2.x AsyncEngine
    ↓
Connection Pool (pool_size=5, max_overflow=10, pre-ping)
    ↓
asyncpg
    ↓
PostgreSQL 18 (localhost:5432)
        ├── devpilot_dev   (development database)
        └── devpilot_test  (integration test database)
```

| Component | Purpose |
|-----------|---------|
| `app/db/database.py` | Async engine creation, connection pool, `SELECT 1` verification, secret redaction |
| `app/db/setup_databases.py` | One-time setup: creates `devpilot` role + databases + `.env` |
| `config.py` (Database fields) | `DATABASE_URL` (dev), `TEST_DATABASE_URL` (test isolation) |
| `main.py` (lifecycle) | Engine init on startup, safe dispose on shutdown |
| `api/health.py` (DB section) | Sanitized database health: type, connected, server version |
| `cli.py` (`db-check`) | Redacted connectivity diagnostic |
| `tests/test_database.py` | 22 unit tests (mocked) + 7 integration tests (live PostgreSQL) |

**Security:** Passwords always redacted in logs, errors, APIs, and CLI output. `.env` is gitignored.
**Portability:** Configuration-driven via `DATABASE_URL` — switch from localhost to managed PostgreSQL with config changes only.

## API Endpoints

| Method | Path | Description | Phase |
|--------|------|-------------|-------|
| GET | `/health` | Health check | 1 |
| POST | `/api/v1/repositories/analyze` | Analyze local repository | 2 |
| GET | `/api/v1/repositories/capabilities` | List detector capabilities | 2 |
| POST | `/api/v1/github/repositories/analyze` | Analyze remote repo | 3 |
| GET | `/api/v1/github/repositories/{owner}/{repo}` | Repo metadata | 3 |
| GET | `/api/v1/github/repositories/{owner}/{repo}/branches` | List branches | 3 |
| GET | `/api/v1/github/repositories/{owner}/{repo}/issues` | List issues | 3 |
| GET | `/api/v1/github/repositories/{owner}/{repo}/issues/{number}` | Get issue | 3 |
| POST | `/api/v1/planning/plan` | Plan from user task | 4 |
| POST | `/api/v1/planning/github/plan` | Plan from GitHub issue | 4 |
| GET | `/api/v1/planning/capabilities` | Planning capabilities | 4 |
| POST | `/api/v1/code-intelligence/index/build` | Build code index | 5 |
| POST | `/api/v1/code-intelligence/retrieval/search` | Search code | 5 |
| POST | `/api/v1/code-intelligence/retrieval/plan-context` | Plan-aware context | 5 |
| GET | `/api/v1/code-intelligence/retrieval/capabilities` | Retrieval capabilities | 5 |
| POST | `/api/v1/coding/generate` | Generate patch | 6 |
| POST | `/api/v1/coding/dry-run` | Dry-run patch | 6 |
| POST | `/api/v1/coding/apply` | Apply patch | 6 |
| GET | `/api/v1/coding/capabilities` | Coding capabilities | 6 |
| POST | `/api/v1/testing/plan` | Create execution plan | 7 |
| POST | `/api/v1/testing/plan-from-patch` | Plan from patch | 7 |
| POST | `/api/v1/testing/run` | Execute tests | 7 |
| GET | `/api/v1/testing/capabilities` | Testing capabilities | 7 |
| POST | `/api/v1/repair/diagnose` | Diagnose failures | 8 |
| POST | `/api/v1/repair/run` | Execute repair | 8 |
| GET | `/api/v1/repair/capabilities` | Repair capabilities | 8 |
| POST | `/api/v1/review/run` | Execute review | 9 |
| GET | `/api/v1/review/capabilities` | Review capabilities | 9 |
| POST | `/api/v1/runs` | Create/execute run | 10 |
| GET | `/api/v1/runs` | List runs | 10 |
| GET | `/api/v1/runs/{run_id}` | Get run | 10 |
| POST | `/api/v1/runs/{run_id}/cancel` | Cancel run | 10 |
| GET | `/api/v1/runs/{run_id}/events` | Run events | 10 |
| GET | `/api/v1/orchestration/capabilities` | Orchestration capabilities | 10 |
| GET | `/api/v1/providers` | Registered providers, priority, active | 19B |
| GET | `/api/v1/providers/health` | Per-provider health + circuit state | 19B |
| GET | `/api/v1/providers/metrics` | Runtime metrics + failover events | 19B |
| GET | `/api/v1/providers/metrics/history` | Persisted per-provider history | 19B |
| GET | `/api/v1/providers/config` | Redacted routing configuration | 19B |
| POST | `/api/v1/providers/test` | Route one benign test call | 19B |
| (DB) | `devpilot db-check` (CLI) | Database connectivity diagnostic | DB |

---

## Security Boundaries

| Boundary | Phase | Description |
|----------|-------|-------------|
| Read-only analysis | 1–3 | Never modifies target |
| Path validation | 2–8 | Traversal protection, allowed roots |
| Sensitive file protection | 2–5 | Detect by name — never read contents |
| Prompt injection | 4–9 | Untrusted content markers in LLM prompts |
| Patch validation | 6–10 | Hash verification, size limits, protected files |
| Execution policy | 7–10 | Executable allowlist, argument safety |
| Environment sanitization | 7–10 | Secret isolation, controlled env vars |
| Repair policy | 8–10 | Test tampering, config weakening, dangerous content |
| Human approval gate | 3 | Required before GitHub writes |
| Orchestrator authority | 10 | No direct file/process/exec/gate authority |
| Database security | DB | Credential redaction, sanitized errors, env isolation, `.env` gitignored |

---

## Agent Architecture

All agents implement `BaseAgent[TInput, TOutput]`:
- `execute(inp: TInput) → TOutput` — core logic
- `run(inp: TInput) → TOutput` — wraps execute with status tracking
- `reset()` — reset status for reuse

```
BaseAgent[TInput, TOutput]
    ├── RepositoryAnalyzerAgent  (Phase 1 — remote GitHub analysis)
    ├── IssueAnalyzerAgent       (Phase 1 — issue parsing)
    ├── PlannerAgent             (Phase 4 — plan generation)
    ├── CodingAgent              (Phase 6 — patch generation)
    ├── TestAgent                (Phase 7 — test planning)
    ├── FixAgent                 (Phase 8 — repair generation)
    └── ReviewerAgent            (Phase 9 — code review)
```

Each agent has **reasoning authority only**. No agent directly writes files or executes processes.

---

## Service Architecture (Phase 10 Orchestration)

```
OrchestrationService (Phase 10)
    │
    ├── RepositoryAnalysisWorkflow (Phase 2) — read-only analysis
    ├── GitHubService (Phase 3) — remote repo read
    ├── PlanningService (Phase 4) — plan generation + validation
    ├── RepositoryIndexBuilder (Phase 5) — code indexing (read-only)
    ├── CodingAgent (Phase 6) — patch generation (LLM)
    ├── PatchValidator (Phase 6) — deterministic patch safety
    ├── SafePatchEngine (Phase 6) — atomic file mutation
    ├── TestingService (Phase 7) — controlled test execution
    ├── RepairService (Phase 8) — bounded repair loop
    └── ReviewService (Phase 9) — review + quality gate
```

---

## Frontend Architecture

```
Dashboard (Next.js 14, TypeScript, Tailwind CSS)
    │
    ├── /dashboard                     — Overview stats
    ├── /dashboard/analysis            — Repository analysis
    ├── /dashboard/planning            — Planning view
    ├── /dashboard/coding              — Coding view
    ├── /dashboard/testing             — Testing view
    ├── /dashboard/repair              — Repair view
    ├── /dashboard/review              — Review & quality gate
    ├── /dashboard/runs                — Run list (Phase 10; multi-repo badges)
    ├── /dashboard/runs/[id]           — Run detail timeline (Phase 10) + repository
    │                                    status cards, per-repo execution timeline,
    │                                    organization summary, run history (Phase 20A6)
    ├── /dashboard/durability          — Durability report (Phase 19)
    ├── /dashboard/engineering-graph   — EKG graph explorer (Phase 18)
    │                                    + interactive React Flow view,
    │                                    filters, timeline diff, live
    │                                    WebSocket updates (Phase 19C)
    ├── /dashboard/organization-graph  — Organization graph (Phase 12)
    └── /dashboard/providers           — Provider router observability (Phase 19B)
```

The engineering-graph explorer runs on a production graph engine:
`@xyflow/react` (React Flow v12) owns pan/zoom/drag/fit/minimap/controls/
fullscreen and virtualized rendering, while `d3-force@3` is used only as a
**seeded, deterministic layout algorithm** (`computeForceLayout` — LCG seed 42,
per-signature position cache, `initialPositions` reseeding for incremental
expansion). Pure transforms (`applyViewFilters`, `snapshotFacets`,
`summarizeDiff`) live in `frontend/src/lib/graph/graphModel.ts` and are
unit-tested under Node; live graph updates stream from
`WS /api/v1/ws/graph` through a singleton `useGraphSocket` hook
(`useSyncExternalStore` + exponential-backoff reconnects). Full design:
`docs/GRAPH_VISUALIZATION.md`.

---

## Provider Router (Phase 19B)

Every LLM call now flows through a health-aware `ProviderRouter`
(`app/llm/router.py`) instead of a single hard-coded provider:

```text
Agents / services
      │
      ▼
llm_factory.get_provider()  ──►  RoutedProvider facade (agents unchanged)
      │
      ▼
      ProviderRouter
      │  priority chain: [DEVPILOT_LLM_PROVIDER] + nvidia, gemini, cloudflare,
      │                   ollama_cloud, opencode_zen, openai, anthropic,
      │                   openrouter, ollama, openai_compatible, fake
      │  per-provider CircuitBreaker  (closed → open → half-open)
      │  bounded RetryStrategy        (exponential backoff, recoverable only)
      │  failure classification       (quota → fail over immediately)
      │  streaming failover           (pre-first-token)
      ▼
   AllProvidersFailedError  — never silent; carries per-provider failures
```

- **Deterministic selection** — providers tried strictly in priority order,
  gated by circuit breaker; injectable `factory` / `settings` / `sleep` /
  `now_fn` keep all tests deterministic (no paid LLM).
- **Quota-aware failover** — permanent quota exhaustion fails over
  immediately; rate-limit/timeout/network/server errors retry with bounded
  exponential backoff before failing over.
- **Health windows** — rolling success rate (degraded < 50%, unhealthy < 30%)
  with per-provider latency EMA, uptime, retries and failover counters.
- **Redaction** — API keys/credentials never serialized; the config surface
  returns masked suffixes only (`app/llm/redaction.py`).
- **Persistence** — best-effort `provider_metric_snapshots` rows (migration
  `014`) when PostgreSQL is reachable; clean no-op otherwise.
- **Centralized registry (Phase 20F)** — `app/llm/provider_registry.py` is the
  single place a provider is added: one `ProviderSpec` (name, class, availability
  attribute, always-present flag) + a config block in `app/config.py` + a
  `BaseLLMProvider` implementation. `LLMFactory._providers`, the router's
  availability map and canonical (default priority) order all derive from it —
  adding a provider touches no agent, factory or routing code.
- **New providers** — `nvidia` (OpenAI-compatible NIM, default), `gemini`,
  `cloudflare` (Workers AI, OpenAI-compatible), `ollama_cloud` (hosted Ollama,
  OpenAI-compatible), `opencode_zen` (OpenAI-compatible gateway, free-tier
  `-free` models), `openrouter` (OpenAI-compatible, requires key),
  `openai_compatible` (any OpenAI chat-completions endpoint:
  vLLM/TGI/llama.cpp/LM Studio/remote Ollama) and `ollama` (keyless,
  `OLLAMA_BASE_URL`) — 11 registered providers. `DEVPILOT_PROVIDER_DISABLED`
  (JSON array from `.env`) excludes a provider from routing without deleting
  its key.
  Full design: `docs/MULTI_PROVIDER_ROUTING.md`.

---

## Multi-Repository Dashboard (Phase 20A6)

The user experience on top of the cross-repository execution layer (Phase 20A1–A5).
No backend redesign: a dedicated **view builder** re-shapes what the orchestrator, EKG,
and organization graph already produce into a repository-aware dashboard.

```text
Organization
    ↓  Repository Selection  (search / filter / pagination / multi-select)
    ↓
Cross-Repository Run   (primary + auxiliary repositories, ordering,
    ↓                    relationships, acceptance criteria, execution budget)
    ↓
Execution Timeline     (per-repo: planning → coding → testing → repair → review → gate)
    ↓
Repository Status      (current stage, progress, validation, EKG status, memory)
    ↓
Organization Summary   (participating/successful/failed/repaired, duration,
                         decisions, consensus, quality status)
```

### View builder (`app/services/run_dashboard.py`)

- `build_repository_view(run, org_service)` — per-repository status cards: primary +
  auxiliary repositories in materialized order (`ordering`), the six-stage per-repo
  timeline (`progress`), validation/application status, changed files, and per-repo EKG
  stats from `org_service.repository_stats()` (gracefully empty when unregistered).
- `build_organization_summary(run, org_service)` — participating / successful / failed /
  repaired repositories, duration (from `started_at`/`finished_at`), engineering
  decisions + consensus summary (from `decision_recorded` / `consensus_built` /
  `conflict_detected` events), quality status + quality-gate detail, org-graph stats.
- **Duck-typed** for both `DevPilotRun` (live run, WebSocket) and `DevPilotRunResult`
  (final result) — one code path, no divergence between live and final views.
- **Evidence-only & isolated**: only namespaces actually materialized for the run are
  surfaced; hidden reasoning, secrets, and credentials are never included.

### Consumers (all reuse the builders)

| Consumer | Surface |
|---|---|
| `GET /api/v1/runs/{id}` | `repositories` + `organization_summary` in the payload |
| `POST /api/v1/runs` | final result carries the same repository-aware surface |
| `GET /api/v1/runs` | `repository_count` per run (multi-repo badges) |
| WebSocket broadcast | `_broadcast_update` payload carries `repositories` + `organization_summary` for live card updates |
| `python -m app.cli run` | Participating Repositories + Organization Summary blocks; `--json` emits pure JSON with both merged |
| Run creation | `POST /api/v1/runs` accepts advisory `acceptance_criteria` + `execution_budget` (recorded on `RunSource`) |

### Repository selection (org-graph API)

- `GET /api/v1/graph/org/repositories?q=&organization=&limit=&offset=` — search +
  filter + pagination over registered namespaces (lazy-loaded by the frontend
  `RepositorySelector`).
- `GET /api/v1/graph/org/repositories/{id}` — per-repository EKG stats (404 for unknown).

### Frontend components (`frontend/src/components/runs/`)

- `RepositorySelector` — search/filter, lazy-load pagination, multi-select with
  dependency + status badges (used in `CreateRunModal`).
- `RepositoryStatusCards` — live per-repo cards (stage, progress, validation, EKG status)
  with navigation links into the org-graph / EKG views.
- `RepositoryTimeline` — per-repository six-stage execution timeline.
- `OrganizationSummary` — run-completion organization-level summary.
- `RunHistoryPanel` — context & run history (recent runs, repository relationships,
  prior executions, engineering decisions).

Pure mappers live in `frontend/src/lib/graph/repositoryStatusModel.ts` (unit-tested);
the WebSocket hook carries the repository-aware payload so cards update live.

### Restart recovery

`PostgresRunStore` round-trips `repository_path`, `auxiliary_repositories`, and
`repo_patches` through `context_json`, so a backend restart rebuilds the identical
repository-aware dashboard view from persisted state (demo M).

---

## Future Phases

| Phase | Focus | Status |
|-------|-------|--------|
| 1 | Foundation + Remote Analysis | ✅ Complete |
| 2 | Repository Intelligence Engine | ✅ Complete |
| 3 | GitHub Read Integration | ✅ Complete |
| 4 | Issue Analysis & Planning | ✅ Complete |
| 5 | Code-Aware Repository Indexing & RAG | ✅ Complete |
| 6 | Coding Agent + Safe Patch Engine | ✅ Complete |
| 7 | Test Agent + Controlled Execution Engine | ✅ Complete |
| 8 | Fix Agent + Bounded Repair Loop | ✅ Complete |
| 9 | Reviewer Agent + Deterministic Quality Gate | ✅ Complete |
| 10 | End-to-End Multi-Agent Orchestration | ✅ Complete |
| DB | PostgreSQL Infrastructure (async engine, pool, redaction, CLI diag) | ✅ Complete |
| 11 | Persistent State + Run/Task Management (PostgreSQL-backed) | ✅ Complete |
| 12 | Semantic Graph + Code Intelligence (pgvector) | ✅ Complete |
| 13 | Context Engineering, Repository Memory & Intelligent Agent Reasoning | ✅ Complete |
| 14 | Hardening, Integration Tests & Documentation | ✅ Complete |
| 15 | Multi-Agent Collaboration (handoffs, decisions, conflicts, memory promotion) | ✅ Complete |
| 16 | Autonomous Execution (goal tracking, dynamic replanning, safe termination) | ✅ Complete |
| 17 | Collaborative Reasoning & Evidence Consensus | ✅ Complete |
| 18 | Engineering Knowledge Graph (EKG) | ✅ Complete |
| 19 | Semantic EKG Retrieval + EKG-driven test selection | ✅ Complete |
| 19B | Multi-Provider Failover & Reliability Platform | ✅ Complete |
| 19C | Cross-repo namespaces + interactive EKG viz + multi-repo acquisition + org-scope queries | ✅ Complete |
| 20 | Cross-Repository Autonomous Engineering & Production Readiness (A1–A6: multi-repo runs, per-repo scope + EKG ingestion, repository dashboard; B1–B3: provider routing resilience; D: org-graph UI parity; E: unittest/Vitest/Jest parsers) | ✅ Complete (see `workflow-status/PHASE20_ROADMAP.md` + `PHASE20A6_COMPLETION_REPORT.md`) |


