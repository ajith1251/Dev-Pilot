<div align="center">

# DevPilot 🚀

**Autonomous Multi-Agent Software Engineering Platform**

[![GitHub](https://img.shields.io/badge/Status-In_Development-yellow?style=for-the-badge)](https://github.com/ashishpatel26/500-AI-Agents-Projects)
[![Python](https://img.shields.io/badge/Python-3.10+-blue?style=for-the-badge&logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-green?style=for-the-badge&logo=fastapi)](https://fastapi.tiangolo.com)
[![Next.js](https://img.shields.io/badge/Next.js-14-black?style=for-the-badge&logo=next.js)](https://nextjs.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-red?style=for-the-badge)](LICENSE)

---

**DevPilot coordinates specialised AI agents to accept a development task, then autonomously analyze, plan, implement, test, repair, review, and quality-gate the required changes — like having an entire AI software engineering team at your command.**

---

**Phase 18 Complete — Engineering Knowledge Graph (EKG)** ✅

**Phase 19B Complete — Multi-Provider Failover & Reliability Platform** ✅

**Phase 19C part 1 — Cross-Repo Namespaces + Force-Directed Graph Viz** ✅

**Phase 19C part 2 — Interactive EKG Visualization** ✅

**Phase 20 — Cross-Repository Autonomous Runs (A1–A5)** ✅

_Phase 20 makes the execution pipeline multi-repository aware: `RunSource.repositories` + `repo_patches` (A1), auxiliary repositories materialized + linked through the org graph (`_materialize_auxiliary_repositories`, A2), org-scope planning context for explicitly multi-repo runs (A3), and deterministic per-repo scope enforcement (A4) — a new `RepositoryScopeRegistry` + `SafePatchEngine.check_repository_ownership` gate + blocking `DET-020` review finding guarantee a patch is validated and applied against ITS OWN checkout only, never cross-checkout. Per-repo EKG ingestion (A5) lands each patch's evidence in its own repository namespace (`OrganizationKnowledgeGraphService.record_run_across_namespaces`: RUN/REPOSITORY/PATCH/FILE nodes + REFERENCES/MODIFIES edges, `RepositoryPatchResult.changed_files`) and links the run across namespaces via org-level RUN→REPO edges. Wired end-to-end: `POST /api/v1/runs` `repo_patches`/`repositories`, CLI `--aux-repo ID=PATH` + `--repo-patch ID=WORKSPACE=PATCH_JSON`, result `repo_validation`, autonomy `repository_validation` evidence. Demos A–G in `scripts/demo_phase20.py` all pass. See `workflow-status/PHASE20_ROADMAP.md`._

_DevPilot now routes every LLM call through a health-aware `ProviderRouter`: deterministic priority chains (`[DEVPILOT_LLM_PROVIDER]` + `gemini, openai, anthropic, openrouter, ollama, fake`), per-provider circuit breakers, bounded retry with exponential backoff, quota-aware failure classification (permanent quota exhaustion fails over immediately), streaming failover before the first token, rolling health windows, a fully redacted config surface, PG-persisted metric snapshots (migration `014`), `/api/v1/providers` observability + CLI commands, a new dashboard "Providers" page, and two new first-class providers (`openrouter`, keyless `ollama`). Agents are untouched — the router wraps `llm_factory.get_provider()` behind a `RoutedProvider` facade._

_Phase 19C part 2 (Interactive EKG Visualization) replaced the custom SVG canvas on `/dashboard/engineering-graph` with a production graph engine (`@xyflow/react` React Flow v12 + d3-force used only as a seeded, deterministic layout algorithm): incremental neighborhood expansion with cached positions, node/relationship/repository filtering + text search, jump-to-node/repo, a live `WS /api/v1/ws/graph` badge (`snapshot` + `version_incremented` broadcasts with backoff reconnects), a graph timeline with `GET /api/v1/graph/diff` change-sets (added/removed nodes, changed edges, per-version), an evidence-only provenance panel, and 3000-node performance bounds. 29 new frontend vitest tests + backend diff/WS test suites; demo A–F in `scripts/demo_phase19c.py` all pass. See `docs/GRAPH_VISUALIZATION.md`._

_DevPilot now routes every LLM call through a health-aware `ProviderRouter`: deterministic priority chains (`[DEVPILOT_LLM_PROVIDER]` + `gemini, openai, anthropic, openrouter, ollama, fake`), per-provider circuit breakers, bounded retry with exponential backoff, quota-aware failure classification (permanent quota exhaustion fails over immediately), streaming failover before the first token, rolling health windows, a fully redacted config surface, PG-persisted metric snapshots (migration `014`), `/api/v1/providers` observability + CLI commands, a new dashboard "Providers" page, and two new first-class providers (`openrouter`, keyless `ollama`). Agents are untouched — the router wraps `llm_factory.get_provider()` behind a `RoutedProvider` facade._

_Phase 18 (Engineering Knowledge Graph) built one unified, temporal, provenance-bearing retrieval layer above code, requirements, goals, plans, evidence, consensus, notebook, memory, and runs: the EKG answers "why was this implemented?", "which repair fixed this?", and "which decision caused this architecture?". Graph versioning is incremental (never a full rebuild), retrieval is planner-driven (deterministic intent classification), every node retains its evidence origins, and the whole layer is exposed evidence-only via PostgreSQL persistence, API, CLI, and a dashboard graph explorer._

_Phase 17 (Collaborative Reasoning & Evidence Consensus) built the reasoning layer above collaboration: bounded confidence scores, per-topic consensus, an engineering notebook, deterministic-outranks-claims, and contradiction detection — now re-used as EKG evidence._

_Phase 19 (Semantic EKG Retrieval) added cosine-similarity retrieval over node payloads (deterministic hashed word/trigram provider, no API): the planner now merges lexical + semantic results within the same bounds, with an optional pgvector mirror (migration 012)._

_Phase 19C part 1 closed two remaining EKG gaps: cross-repository knowledge namespaces (demo I in `scripts/demo_phase18.py` — three repos with explicit `link_repositories` edges, org-scope merge vs local-scope isolation, `cross_repository_traversal` across repo boundaries) and a force-directed graph view — a shared `ForceDirectedGraph.tsx` SVG canvas (pan/zoom/drag, color-by-type, size-by-degree) now powers the organization-graph page and a new neighborhood panel on `/dashboard/engineering-graph` (select a node → expand 1–3 hops via `GET /api/v1/graph/neighborhood`)._

_Phase 12d closure (EKG-driven test selection): smart test selection now walks EKG impact edges (patch → test) persisted for every ingested run — the orchestrator's test stage targets pytest candidates with graph-selected tests, and autonomy replans query the EKG first (lazy per-repo cache removed)._

_Building on Phase 16 (autonomous goals, deterministic decisions, versioned replanning, budget enforcement, escalation, safe termination) and Phase 15 (handoffs, decisions, conflicts, memory promotion)._ _Phase 19B (Multi-Provider Failover & Reliability Platform) added the health-aware, failover-capable provider router (see `docs/MULTI_PROVIDER_ROUTING.md`)._ _Tests: **1640 passed / 18 skipped** on the live-PG full suite (the only failures are the pre-existing live-Gemini durability tests that need a fresh quota key); **zero regressions** in every deterministic suite — 43 new `test_provider_router.py` tests green, migration chain now 001→014. Raw-HTTP durability remains repeatable pytest (deterministic class in CI, live class gated on provider; item-13 bounded goal-path retry on ANY coding failure + task-analysis stage retry for the run-API path, see Sessions 17–19). The full deterministic live-PG suite (`-m "not live"`) is **1640 passed / 18 skipped / 1 failed**, the 1 failure being the pre-existing `test_wrapper_skips_cleanly_without_provider` env quirk._

</div>

---

## The Problem

Software development involves many repetitive but essential tasks:

- Analysing repositories
- Understanding issues and planning implementations
- Writing code, tests, and documentation
- Reviewing changes for bugs, security, and quality
- Coordinating multiple stages into a coherent pipeline

These tasks are **time-consuming**, **error-prone**, and **slow down iteration**.

---

## The Solution

DevPilot orchestrates a complete autonomous pipeline:

| Stage | Phase | Description |
|-------|-------|-------------|
| 🔍 **Repository Analysis** | 2–3 | Understands codebase structure, languages, and conventions |
| 📋 **Task Analysis** | 4 | Extracts structured requirements |
| 🗺️ **Planning** | 4 | Creates step-by-step implementation plan |
| 💻 **Code Retrieval** | 5 | Retrieves relevant code context via hybrid RAG |
| ✏️ **Coding** | 6 | Generates patches following conventions |
| 🛡️ **Patch Validation** | 6 | Deterministic security gate for file mutations |
| 🧪 **Testing** | 7 | Discovers and executes tests safely |
| 🔧 **Repair** | 8 | Diagnoses failures and bounded automated repair |
| 👁️ **Review** | 9 | Reviews implementation against requirements |
| ✅ **Quality Gate** | 9 | Deterministic acceptance decision |
| 🎯 **Orchestration** | 10 | Coordinates all stages into one run |

---

## Architecture

```text
                    DEVPILOT — FULL PIPELINE (Phases 1–10)

User Task / GitHub Issue
        ↓
Create DevPilotRun → OrchestrationService
        ↓
Repository Analysis (Phase 2/3) → RepositoryProfile
        ↓
Task Analysis (Phase 4) → StructuredRequirements
        ↓
Planning (Phase 4) → ImplementationPlan
        ↓
Code Retrieval (Phase 5) → RetrievedContext
        ↓
Coding Agent (Phase 6) → PatchSet
        ↓
PatchValidator (Phase 6) → SafePatchEngine → Modified Workspace
        ↓
Test Agent (Phase 7) → Controlled Test Execution → TestRunResult
        ↓
   ┌─── tests pass? ───┐
   │                    │
  YES                  NO
   │                    │
   │              Fix Agent (Phase 8)
   │              Bounded Repair Loop
   │                    │
   └──────────┬─────────┘
              │
        Review (Phase 9)
              │
        Quality Gate (Phase 9)
              │
   ┌──────────┼──────────┐
   ▼          ▼          ▼
APPROVED   REJECTED   NEEDS HUMAN
                       REVIEW
```

> **📖 Full detailed architecture diagrams** are available in **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.
> **📖 Phase 10 orchestration** is documented in **[docs/ORCHESTRATION.md](docs/ORCHESTRATION.md)**.

### Key Invariant

> **The orchestrator coordinates authority. It does not acquire authority.**

Every agent has **reasoning authority only**. No agent directly writes files or executes processes. All mutations and executions pass through multiple deterministic security gates.

| Authority | Phase 6 (Coding) | Phase 7 (Testing) | Phase 8 (Repair) | Phase 10 (Orchestrator) |
|-----------|-----------------|-------------------|------------------|------------------------|
| Propose changes | ✅ CodingAgent | ❌ | ✅ FixAgent | ❌ |
| Generate patches | ✅ CodingAgent | ❌ | ✅ FixAgent | ❌ |
| Determine test plan | ❌ | ✅ TestAgent | ❌ | ❌ |
| **Validate safety** | **✅ PatchValidator** | **✅ ExecutionPolicy** | **✅ RepairPolicy + PatchValidator** | **❌ (delegates)** |
| **Mutate workspace** | **✅ SafePatchEngine only** | **❌** | **✅ SafePatchEngine only** | **❌ (delegates)** |
| **Execute processes** | **❌** | **✅ ControlledExecutionEngine only** | **❌** | **❌ (delegates)** |
| **Final decision** | ❌ | ❌ | ❌ | **✅ QualityGate only** |

### Key Design Principles

- **Provider-independent LLM abstraction** — swap OpenAI, Anthropic, or local models
- **Deterministic security first** — LLMs propose, deterministic gates dispose
- **Bounded loops** — every iterative process has strict attempt limits
- **Deterministic quality gate** — LLM cannot override hard rejection rules
- **Modular agents** — each agent has a single responsibility, orchestrated by Phase 10

---

## Technology Stack

| Layer | Technology |
|-------|-----------|
| **Backend** | Python 3.10+, FastAPI, Uvicorn |
| **LLM Interface** | Provider-independent abstraction (OpenAI, Anthropic, extensible) |
| **Frontend** | Next.js 14, TypeScript, Tailwind CSS |
| **Database** | PostgreSQL 18, SQLAlchemy 2.x (async), asyncpg |
| **GitHub Integration** | GitHub REST API v3 |
| **Testing** | pytest, pytest-asyncio |

---

## Quick Start

### Prerequisites

- Python 3.10+
- PostgreSQL 16+ (optional for Phase 11 development)

### Backend Setup

```bash
# Navigate to the project
cd DevPilot/backend
pip install -r requirements.txt

# Run the test suite (no API keys needed)
pytest -v
# Expected: 653 passed, 4 skipped, 0 failed

# Analyze a local repository via CLI
python -m app.cli analyze /path/to/repository

# Plan a task
python -m app.cli plan --task "Add pagination" --repo-path /path/to/repo

# Verify database connectivity (if PostgreSQL configured)
python -m app.cli db-check

# Run the full end-to-end pipeline
python -m app.cli run /path/to/repo --title "Add quantity validation"

# Or via the API server
uvicorn app.main:app --reload
```

### PostgreSQL Setup (for Phase 11+ development)

**Option A — local PostgreSQL 18.4** (recommended for development):

```bash
# Create the application role and databases
python -m app.db.setup_databases

# Verify connectivity
python -m app.cli db-check
```

> 📖 Full PostgreSQL setup guide: **[docs/DATABASE.md](docs/DATABASE.md)**

**Option B — disposable PostgreSQL 18.4 via Docker** (recommended for CI and isolated runs):

```bash
docker compose up -d   # starts postgres:18.4 on localhost:5432 (env-overridable)

# Point the suite at it (defaults: devpilot / devpilot / devpilot_test)
export DATABASE_URL="postgresql+asyncpg://devpilot:devpilot@localhost:5432/devpilot_test"
export TEST_DATABASE_URL="$DATABASE_URL"
```

Credentials and port are overridable via `POSTGRES_USER` / `POSTGRES_PASSWORD` /
`POSTGRES_DB` / `POSTGRES_PORT` environment variables (see `docker-compose.yml`).

### CI Matrix

The repository ships a GitHub Actions workflow (`.github/workflows/ci.yml`) that
validates **both** persistence paths on every push/PR, plus a secrets-gated
live-LLM end-to-end job:

| Job | PostgreSQL | Validates |
|-----|-----------|-----------|
| `in-memory` | none | Graceful degradation — PG-dependent tests **skip**, not fail (0 failures/errors required) |
| `postgres` | dockerized `postgres:18.4` service | `alembic upgrade head` (migration chain 001→014) + full suite against live PG |
| `live-llm-e2e` | dockerized `postgres:18.4` service | **Both live API paths** against a production LLM provider (`pytest tests/test_api_durability.py -m live`): one real `execute_run` (`POST /api/v1/runs`) **and** one real autonomous goal loop (`POST /api/v1/autonomy/run`) — runs/handoffs/consensus must persist via PostgresRunStore, the run must reach a terminal verdict, and the goal a terminal state, or the job **fails**. Equivalent gates via `scripts/verify_api_durability.py --live` (for manual `--json` runs). On `workflow_dispatch` the job additionally runs `scripts/durability_report.py --out durability_report.json`, asserts `mode=live && passed=true` (an expired 24h-TTL key can never produce a silent green artifact), and uploads the JSON as the `durability-report` artifact — the document `GET /api/v1/durability/report` serves. The `postgres` job additionally runs the deterministic raw-HTTP class (`TestApiDurabilityDeterministic`, no LLM needed) on every push |

`in-memory` and `postgres` always run. `live-llm-e2e` runs on manual dispatch
or whenever a `DEVPILOT_LLM_PROVIDER` repo secret is set — CI stays green
with no API keys configured.

Run the same two paths locally:

```bash
# Path 1 — in-memory fallback (no DB configured): PG tests must skip
cd DevPilot/backend && DATABASE_URL= TEST_DATABASE_URL= python -m pytest -q --tb=short

# Path 2 — live PostgreSQL (dockerized or local)
cd DevPilot/backend && export TEST_DATABASE_URL="postgresql+asyncpg://devpilot:devpilot@localhost:5432/devpilot_test"
python -m pytest -q --tb=short
```

### Live-LLM E2E (Demonstration A)

The reasoning/consensus loop is fully validated **without** an LLM in the
deterministic demo (`python scripts/demo_phase17.py`). To run the real
`execute_run` against a production provider:

```bash
cd DevPilot/backend
# 1. Set the provider + key in .env (never commit the key)
#    DEVPILOT_LLM_PROVIDER=openai    (or anthropic, or gemini — free tier)
#    OPENAI_API_KEY=sk-...           (or ANTHROPIC_API_KEY=..., GEMINI_API_KEY=...)
#    Get a FREE Gemini key at https://aistudio.google.com/apikey
# 2. Ensure a test-named DB is reachable (docker compose up -d)
# 3. Run the live demo — it prints the same consensus/contradiction/notebook
#    summaries from real provider content
python scripts/demo_phase17.py --live
```

The `--live` guard refuses to start without a registered provider and key, so
CI stays green with no API keys configured.

> ⚙️ **Live-LLM in CI**: the `live-llm-e2e` job (`.github/workflows/ci.yml`)
> runs `tests/test_api_durability.py -m live` — a pytest class that drives
> **both** the run API (`POST /api/v1/runs`) and the goal API
> (`POST /api/v1/autonomy/run`) with real LLM stage bodies, and **fails if
> either path does not reach a terminal outcome** (run verdict + terminal
> goal state; mirrors the Demonstration A pattern above). The same checks
> are available as `scripts/verify_api_durability.py --live`. The same
> live class also emits a structured `run_api`/`goal_api` JSON report via
> `scripts/durability_report.py` (the `--json`-equivalent wrapper: reuses
> the identical `vd.*` helpers, prints **only** JSON to stdout, applies the
> terminal gates and exits 1 on failure, and skips cleanly with no
> provider/DB). The raw HTTP path is also covered deterministically (no
> provider key) by
> `tests/test_api_durability.py::TestApiDurabilityDeterministic`, which the
> `postgres` job runs on every push — both classes skip cleanly when their
> prerequisites (test-named PostgreSQL / live provider) are absent.
> Free-tier provider keys are intentionally short-lived (~24h TTL for Gemini)
> — before each manual run, refresh the repo secrets
> `DEVPILOT_LLM_PROVIDER` + the matching `OPENAI_API_KEY` /
> `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` (or pass `live_llm_provider` in
> the workflow-dispatch form).

> 📖 Gemini provider workflow, failover, and the production-content results of
> Demonstration A: **[docs/GEMINI_API_KEY_REPORT.md](docs/GEMINI_API_KEY_REPORT.md)**
>
> 📖 Phase 19B provider router (priority, circuit breakers, retries, health,
> metrics persistence, API, CLI, new `openrouter`/`ollama` providers):
> **[docs/MULTI_PROVIDER_ROUTING.md](docs/MULTI_PROVIDER_ROUTING.md)**

### Provider Router — quick observability

```bash
cd DevPilot/backend
python -m app.cli providers --json          # registered providers + priority + active
python -m app.cli provider-health --json    # per-provider health + circuit state
python -m app.cli provider-metrics --json   # runtime metrics + failover events
python -m app.cli provider-test --json      # route one benign call through the router
```

### Frontend Setup

```bash
cd DevPilot/frontend
npm install
npm run dev
# Open http://localhost:3000/dashboard
```

---

## Current Status

### ✅ Phase 1 — Foundation (Complete)
- [x] Project structure and configuration
- [x] FastAPI application with health check
- [x] LLM abstraction (OpenAI, Anthropic providers)
- [x] Agent base class and registry
- [x] Tool abstraction
- [x] GitHub integration service
- [x] Comprehensive test suite
- [x] Professional documentation
- [x] Next.js frontend foundation

### ✅ Phase 2 — Repository Intelligence Engine (Complete)
- [x] Safe file system scanner with exclusions
- [x] Language detection (50+ languages)
- [x] Technology/framework detection (30+ techs with evidence)
- [x] Dependency parsing (6 ecosystems)
- [x] Command discovery (npm, Make, poetry)
- [x] File classification (16 categories)
- [x] Monorepo module detection
- [x] Important file identification
- [x] Compact repository tree generation
- [x] LangGraph-ready workflow architecture
- [x] REST API (POST /analyze, GET /capabilities)
- [x] CLI demo tool (python -m app.cli analyze)
- [x] 54 new tests (136 total, all passing)

### ✅ Phase 3 — GitHub Read Integration (Complete)
- [x] Typed GitHub models (repos, branches, issues, PRs)
- [x] Safe repository acquisition via Git CLI (token auth, shallow clone)
- [x] Remote analysis pipeline: GitHub → acquisition → RepositoryAnalyzer
- [x] Pagination, rate limits, retry, token redaction
- [x] 5 API endpoints, 3 CLI commands, 32 tests

### ✅ Phase 4 — Issue Analysis & Planning (Complete)
- [x] StructuredRequirements — objective, constraints, ambiguities, risks
- [x] Planner Agent — ordered ImplementationPlan with dependencies
- [x] PlanValidator — 100% deterministic (no LLM calls)
- [x] Prompt injection boundaries
- [x] API + CLI integration (plan, github plan)
- [x] 74 new tests (241 total, all passing)

### ✅ Phase 5 — Code-Aware Repository Indexing & Hybrid RAG (Complete)
- [x] Safe indexable file selection (eligibility with 16 categories)
- [x] Language-aware parsing (Python AST, JS/TS/Go/Java/Rust fallback)
- [x] Symbol extraction (classes, functions, methods, imports)
- [x] Code-aware chunking (semantic boundaries + window fallback)
- [x] Repository snapshot identity with staleness detection
- [x] Lexical index (BM25-like with identifier normalization)
- [x] Symbol index (exact, qualified, normalized, partial)
- [x] Vector index (in-memory cosine similarity)
- [x] Embedding service abstraction (fake provider for tests)
- [x] Hybrid retrieval (4-signal weighted rank fusion)
- [x] Plan-aware retrieval (Phase 4 ImplementationPlan integration)
- [x] Retrieval filters, deduplication, context budget
- [x] REST API + CLI + Workflow
- [x] 57 new tests (298 total, all passing)

### ✅ Phase 6 — Coding Agent & Safe Patch Engine (Complete)
- [x] Coding Agent — generates structured PatchSet from plan + retrieved context via LLM
- [x] CodingService — orchestrates the coding pipeline (generate → validate → dry-run → apply)
- [x] SafePatchEngine — deterministic file mutation with atomic writes, rollback, diff generation
- [x] PatchValidator — 100% deterministic security gate (path safety, hash verification, content limits)
- [x] WorkspaceService — safe isolated writable copies of source repositories
- [x] Prompt boundaries — trusted/untrusted content separation (UNTRUSTED_REPOSITORY_CONTENT)
- [x] CRLF preservation, content hash verification, path traversal protection
- [x] 4 REST API endpoints (generate, dry-run, apply, capabilities)
- [x] 2 CLI commands (code, patch)
- [x] 43 new tests (341 total, all passing)

### ✅ Phase 7 — Test Agent & Controlled Execution Engine (Complete)
- [x] Test Agent — reasons about what to test, produces ExecutionPlan (deterministic by default)
- [x] ExecutionPolicy — security gate with executable allowlist, argument validation, script inspection
- [x] ControlledExecutionEngine — safe asyncio subprocess with timeout, env sanitization, output limits
- [x] TestingService — orchestrator (discover commands → build plan → validate policy → execute → parse)
- [x] PytestResultParser — full pytest output parsing (counts, failures, classification)
- [x] GenericResultParser — fallback framework support
- [x] Failure classification — 11 categories (ASSERTION_FAILURE, IMPORT_ERROR, SYNTAX_ERROR, etc.)
- [x] Secret isolation — environment sanitization prevents secret leakage to child processes
- [x] Pass/fail/syntax/import error test fixtures
- [x] 4 REST API endpoints (plan, plan-from-patch, run, capabilities)
- [x] 2 CLI commands (test-plan, test)
- [x] 79 new tests (427 total, all passing)

### ✅ Phase 8 — Fix Agent & Bounded Repair Loop (Complete)
- [x] FailureDiagnosisService — deterministic triage: maps failures to patches/plans, classifies repairability
- [x] RepairPolicy — security gate: test tampering detection, config weakening, path safety, dangerous content
- [x] FixAgent — LLM-powered repair generation with provider-independent BaseLLMProvider
- [x] RepairService — bounded loop orchestrator with progress/worsening/repeated-patch detection
- [x] Failure fingerprinting — normalized hash for detecting repeated failure states
- [x] Patch fingerprinting — content-based hash for detecting repeated proposals
- [x] Rollback support — snapshot-based restoration to best-known state
- [x] SafePatchEngine extended — public snapshot() and rollback() methods
- [x] Fix prompt with trust boundaries (UNTRUSTED_REPOSITORY_CONTENT, UNTRUSTED_TEST_OUTPUT)
- [x] 3 REST API endpoints (diagnose, run, capabilities)
- [x] 2 CLI commands (repair-diagnose, repair)
- [x] 77 new tests (504 total, all passing)

### ✅ Phase 9 — Reviewer Agent & Quality Gate (Complete)
- [x] Reviewer Agent — two-mode (deterministic + LLM-assisted), provider-independent
- [x] Review Context Builder — bounded context from Phases 4–8, secret redaction
- [x] Deterministic Review — 21 DET-XXX checks across 9 categories
- [x] Evidence Validator — hallucination protection, validates LLM findings against known context
- [x] Quality Gate — 100% deterministic, hard rejection rules, reason codes
- [x] Requirement Coverage — SATISFIED/PARTIALLY_SATISFIED/UNSATISFIED/UNVERIFIED
- [x] Review Findings — 11 categories × 5 severities, hallucination-protected
- [x] Security — read-only review, no write/execute authority, secret redaction
- [x] Full documentation (docs/REVIEW_AND_QUALITY_GATE.md)
- [x] 66 new tests (571 total, all passing)

### ✅ Phase 10 — End-to-End Multi-Agent Orchestration (Complete)
- [x] DevPilotRun model — run abstraction with all stage artifacts
- [x] RunStateMachine — deterministic transition validation (12 stages, 15+ transitions)
- [x] OrchestrationService — coordinates Phase 1–9 services via DI
- [x] Branching logic: TESTING → REPAIRING (fail) vs REVIEWING (pass)
- [x] Cancellation support — cooperative cancellation between stages
- [x] Event system — 17 event types, structured logging, sanitization
- [x] Failure model — 13 failure codes, per-stage error boundaries
- [x] In-memory RunStore — thread-safe, Protocol interface for Phase 11
- [x] 6 REST API endpoints (create, list, get, cancel, events, capabilities)
- [x] CLI command (devpilot run)
- [x] Frontend runs dashboard — list + detail with timeline, events, decision banner
- [x] 50 comprehensive tests (state machine, paths, security, store, events)
- [x] Full documentation (docs/ORCHESTRATION.md)

### ✅ Database Infrastructure — PostgreSQL Ready (Complete)
- [x] PostgreSQL 18.4 installed and running on localhost:5432
- [x] `devpilot` application role (dedicated, not superuser)
- [x] `devpilot_dev` and `devpilot_test` databases with dev/test separation
- [x] SQLAlchemy 2.x async engine with connection pooling (pool_size=5, max_overflow=10)
- [x] asyncpg driver for high-performance async PostgreSQL access
- [x] FastAPI lifecycle — engine init on startup, safe dispose on shutdown
- [x] Secret redaction — passwords redacted from logs, errors, APIs, CLI
- [x] Sanitized health check — database status with no credential exposure
- [x] CLI diagnostic (`devpilot db-check`) — redacted connectivity verification
- [x] 4 database exception types (ConfigurationError, ConnectionError, UnavailableError)
- [x] Unit tests: 22 passed (mocked, no PostgreSQL needed)
- [x] Integration tests: 7 passed (live PostgreSQL, separate test database)
- [x] Full documentation (docs/DATABASE.md)

---

## Project Structure

```
DevPilot/
├── backend/
│   ├── app/
│   │   ├── main.py                # FastAPI application
│   │   ├── config.py              # Configuration management
│   │   ├── cli.py                 # CLI tool (analyze, plan, github, run, db-check)
│   │   ├── db/                    # Database infrastructure (PostgreSQL, SQLAlchemy async)
│   │   │   ├── __init__.py
│   │   │   ├── database.py        # Async engine, connection pool, redaction, verification
│   │   │   └── setup_databases.py # One-time setup script (role + databases + .env)
│   │   ├── api/v1/
│   │   │   ├── repositories.py    # Phase 2: Analysis API
│   │   │   ├── github.py          # Phase 3: GitHub API
│   │   │   ├── planning.py        # Phase 4: Planning API
│   │   │   ├── code_intelligence.py  # Phase 5: Code Intelligence API
│   │   │   ├── coding.py          # Phase 6: Coding API
│   │   │   ├── testing.py         # Phase 7: Testing API
│   │   │   ├── repair.py          # Phase 8: Repair API
│   │   │   ├── review.py          # Phase 9: Review API
│   │   │   ├── orchestration.py   # Phase 10: Orchestration API
│   │   │   └── providers.py       # Phase 19B: Provider router API
│   │   ├── agents/
│   │   │   ├── base.py, registry.py
│   │   │   ├── repo_analyzer.py   # Phase 1
│   │   │   ├── issue_analyzer.py  # Phase 1
│   │   │   ├── planner.py         # Phase 4
│   │   │   ├── coding_agent.py    # Phase 6
│   │   │   ├── test_agent.py      # Phase 7
│   │   │   ├── fix_agent.py       # Phase 8
│   │   │   └── reviewer.py        # Phase 9
│   │   ├── models/
│   │   │   ├── issues.py          # Phase 4: Planning models
│   │   │   ├── rag.py             # Phase 5: RAG models
│   │   │   ├── coding.py          # Phase 6: Coding models
│   │   │   ├── testing.py         # Phase 7: Testing models
│   │   │   ├── repair.py          # Phase 8: Repair models
│   │   │   ├── review.py          # Phase 9: Review models
│   │   │   └── orchestration.py   # Phase 10: Orchestration models
│   │   ├── llm/                   # Phase 19B: base.py, factory.py, router.py,
│   │   │   │                      #   redaction.py, providers/{openai,anthropic,
│   │   │   │                      #   gemini,openrouter,ollama,fake}.py
│   │   ├── services/
│   │   │   ├── provider_metrics_store.py  # Phase 19B: PG metric snapshots
│   │   │   ├── repository_analyzer.py, github.py, acquisition.py, remote_analyzer.py
│   │   │   ├── plan_validator.py, planning_service.py
│   │   │   ├── index_builder.py, code_chunker.py, index_eligibility.py
│   │   │   ├── coding_service.py, patch_validator.py, safe_patch_engine.py, workspace_service.py
│   │   │   ├── testing_service.py, execution_policy.py, controlled_execution_engine.py
│   │   │   ├── repair_service.py, failure_diagnosis_service.py, repair_policy.py
│   │   │   ├── review_service.py, review_context_builder.py, review_evidence_validator.py
│   │   │   ├── deterministic_review.py, quality_gate.py
│   │   │   ├── run_store.py       # Phase 10: Run storage
│   │   │   └── orchestration_service.py  # Phase 10: Orchestrator
│   │   ├── prompts/               # LLM prompts with trust boundaries
│   │   ├── rag/                   # Phase 5: Parsers, indexes, embeddings, retrieval
│   │   ├── workflows/
│   │   │   ├── repository_analysis.py  # Phase 2
│   │   │   ├── remote_analysis.py      # Phase 3
│   │   │   ├── planning.py             # Phase 4
│   │   │   ├── code_intelligence.py    # Phase 5
│   │   │   ├── coding.py               # Phase 6
│   │   │   ├── testing.py              # Phase 7
│   │   │   ├── repair.py               # Phase 8
│   │   │   ├── review.py               # Phase 9
│   │   │   └── orchestration.py        # Phase 10
│   │   └── core/                  # Logging, exceptions
│   ├── tests/
│   │   ├── fixtures/              # Test fixtures
│   │   └── test_*.py              # Per-phase test files
│   └── requirements.txt
├── frontend/                      # Next.js dashboard
│   └── src/app/dashboard/
│       ├── page.tsx               # Overview stats
│       ├── analysis, planning, coding, testing, repair, review/
│       └── runs/                  # Phase 10: Runs list + detail
├── docs/
│   ├── ARCHITECTURE.md            # Full pipeline architecture
│   ├── ORCHESTRATION.md           # Phase 10: Orchestration
│   ├── REVIEW_AND_QUALITY_GATE.md # Phase 9: Review & Quality Gate
│   ├── REPAIR_AND_RECOVERY.md     # Phase 8: Fix Agent
│   ├── TESTING_AND_EXECUTION.md   # Phase 7: Test Agent
│   ├── CODING_AGENT.md            # Phase 6: Coding Agent
│   ├── ENGINEERING_KNOWLEDGE_GRAPH.md # Phase 18: EKG design
│   ├── GRAPH_VISUALIZATION.md   # Phase 19C: Interactive EKG visualization
│   ├── MULTI_PROVIDER_ROUTING.md   # Phase 19B: Provider router design
│   ├── GEMINI_API_KEY_REPORT.md   # Live-LLM workflow & Demo A results
│   └── ...
├── docs/
│   ├── ...
│   └── DATABASE.md                # PostgreSQL setup and architecture
└── workflow-status/
    └── PROJECT_STATE.md           # Current project state
```

---

## Testing

The suite validates **both** persistence paths (see CI Matrix above):

```bash
# Live-PG path (PostgreSQL reachable; migration chain 001→014 validated)
export TEST_DATABASE_URL="postgresql+asyncpg://devpilot:devpilot@localhost:5432/devpilot_test"
cd DevPilot/backend && python -m pytest -q --tb=short
# Expected: 1573 passed, 18 skipped, 0 deterministic failures
#   (the only full-suite failures are the pre-existing live-Gemini
#    durability tests, which need a fresh free-tier quota key)

# In-memory fallback path (no DB configured; PG tests must SKIP, not fail)
cd DevPilot/backend && DATABASE_URL= TEST_DATABASE_URL= python -m pytest -q --tb=short
# Expected: 1573 passed, 18 skipped, 0 failed
```

| Test File | Tests | Phase |
|-----------|-------|-------|
| test_agents.py | 8 | 1 |
| test_analyzer_tools.py | 20 | 1 |
| test_github_integration.py | 32 | 3 |
| test_github_service.py | 6 | 3 |
| test_health.py | 3 | 1 |
| test_issue_analyzer.py | 14 | 1 |
| test_llm_base.py | 3 | 1 |
| test_repo_analyzer.py | 11 | 1 |
| test_repository_intelligence.py | 60+ | 2 |
| test_planner.py | 17 | 4 |
| test_plan_validator.py | 17 | 4 |
| test_planning_service.py | 11 | 4 |
| test_planning_workflow.py | 12 | 4 |
| test_planning_api.py | 11 | 4 |
| test_planning_cli.py | 6 | 4 |
| test_code_intelligence.py | 57 | 5 |
| test_coding.py | 43 | 6 |
| test_testing.py | **79** | 7 |
| test_repair.py | **77** | 8 |
| test_review.py | **66** | 9 |
| **test_orchestration.py** | **50** | **10** | End-to-end orchestration |
| `test_database.py` | **29** | **DB** | Database infrastructure (22 unit + 7 integration) |
| `test_run_store_contract.py` | **50+** | **10–17** | RunStore contract (in-memory + Postgres) |
| `test_migration.py` | **11** | **11H** | Migration chain 001→014 (skips without PG) |
| `test_reasoning_engine.py` | **20+** | **17** | Collaborative reasoning & evidence consensus |
| `test_collaboration_service.py` | **20+** | **15** | Handoffs, decisions, conflicts, memory |
| `test_autonomy_*` | **15+** | **16** | Autonomous goals, replanning, budgets |
| `test_engineering_graph.py` | **45+** | **18** | EKG: nodes, edges, traversal, versioning, planner, provenance, PG, API, CLI, impact-edge test selection |
| `test_provider_router.py` | **43** | **19B** | Router: classification, retry, circuit breaker, health, failover, streaming, snapshots, redaction, API, metrics store |
| **Total** | **1573** | **All** | (18 skipped = live-GitHub/PG-dependent) |

---

## Contributing

Contributions are welcome! Please see the main repository's [CONTRIBUTION.md](../CONTRIBUTION.md) for guidelines.

---

## License

This project is part of the [500-AI-Agents-Projects](https://github.com/ashishpatel26/500-AI-Agents-Projects) repository and is licensed under the MIT License. See the [LICENSE](../LICENSE) file for details.

---

<div align="center">

**Built with ❤️ as part of the 500+ AI Agent Projects collection**

</div>
