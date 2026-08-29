<div align="center">

# 🚀 DevPilot

### Autonomous Multi-Agent Software Engineering Platform

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Next.js](https://img.shields.io/badge/Next.js-14-000000?style=flat-square&logo=next.js&logoColor=white)](https://nextjs.org)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-18-4169E1?style=flat-square&logo=postgresql&logoColor=white)](https://postgresql.org)
[![Tests](https://img.shields.io/badge/Tests-1900+-green?style=flat-square)](#testing)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)
[![Phases](https://img.shields.io/badge/Phases-1%20to%2021%20Complete-brightgreen?style=flat-square)](#phase-status)

---

**DevPilot is an autonomous multi-agent software engineering platform that coordinates specialized AI agents to analyze, plan, implement, test, repair, review, and quality-gate code changes — like having an entire AI software engineering team at your command.**

[Overview](#overview) • [Quick Start](#quick-start) • [Architecture](#architecture) • [Pipeline](#pipeline) • [Providers](#llm-providers) • [Testing](#testing) • [API Reference](#api-reference) • [CLI Reference](#cli-reference) • [Contributing](#contributing)

</div>

---

## 📖 Table of Contents

- [Overview](#overview)
- [Key Features](#key-features)
- [Technology Stack](#technology-stack)
- [Quick Start](#quick-start)
  - [Prerequisites](#prerequisites)
  - [Backend Setup](#backend-setup)
  - [Frontend Setup](#frontend-setup)
  - [Docker Setup](#docker-setup)
- [Architecture](#architecture)
  - [System Overview](#system-overview)
  - [Pipeline Flow](#pipeline-flow)
  - [Agent Architecture](#agent-architecture)
  - [Service Architecture](#service-architecture)
  - [LLM Providers](#llm-providers)
  - [Frontend Architecture](#frontend-architecture)
- [Pipeline Stages](#pipeline-stages)
  - [Stage 1: Repository Analysis](#stage-1-repository-analysis)
  - [Stage 2: Task Analysis](#stage-2-task-analysis)
  - [Stage 3: Planning](#stage-3-planning)
  - [Stage 4: Code Retrieval](#stage-4-code-retrieval)
  - [Stage 5: Coding](#stage-5-coding)
  - [Stage 6: Patch Validation](#stage-6-patch-validation)
  - [Stage 7: Testing](#stage-7-testing)
  - [Stage 8: Repair](#stage-8-repair)
  - [Stage 9: Review](#stage-9-review)
  - [Stage 10: Quality Gate](#stage-10-quality-gate)
- [Standard Operating Procedure (SOP)](#standard-operating-procedure-sop)
  - [SOP 1: Running a Full Pipeline](#sop-1-running-a-full-pipeline)
  - [SOP 2: Running Tests](#sop-2-running-tests)
  - [SOP 3: Development Workflow](#sop-3-development-workflow)
  - [SOP 4: Adding a New LLM Provider](#sop-4-adding-a-new-llm-provider)
  - [SOP 5: Database Setup](#sop-5-database-setup)
- [Project Structure](#project-structure)
  - [Backend Structure](#backend-structure)
  - [Frontend Structure](#frontend-structure)
  - [Documentation](#documentation)
- [Configuration](#configuration)
  - [Environment Variables](#environment-variables)
  - [Provider Configuration](#provider-configuration)
  - [Database Configuration](#database-configuration)
- [API Reference](#api-reference)
  - [Health Endpoints](#health-endpoints)
  - [Core Endpoints](#core-endpoints)
  - [Provider Endpoints](#provider-endpoints)
- [CLI Reference](#cli-reference)
- [Testing](#testing)
  - [Test Suite](#test-suite)
  - [Running Tests](#running-tests)
  - [Test Coverage](#test-coverage)
- [Phase Status](#phase-status)
- [Contributing](#contributing)
- [License](#license)

---

## Overview

DevPilot is a comprehensive autonomous software engineering platform that orchestrates multiple specialized AI agents to handle the entire software development lifecycle. From understanding a repository to implementing, testing, and reviewing code changes, DevPilot automates the complete workflow while maintaining deterministic security gates and human-in-the-loop controls.

### How It Works

```
User Task / GitHub Issue
        ↓
┌─────────────────────────────────────────────────────────────┐
│                    DEVPILOT PIPELINE                         │
├─────────────────────────────────────────────────────────────┤
│  1. Repository Analysis  →  Understand codebase structure   │
│  2. Task Analysis        →  Extract structured requirements │
│  3. Planning             →  Create implementation plan      │
│  4. Code Retrieval       →  Hybrid RAG context gathering    │
│  5. Coding               →  Generate code changes           │
│  6. Patch Validation     →  Deterministic security gate     │
│  7. Testing              →  Execute tests safely            │
│  8. Repair               →  Fix failures (bounded loop)     │
│  9. Review               →  Quality assessment              │
│ 10. Quality Gate         →  APPROVED | REJECTED | NEEDS_HUMAN│
└─────────────────────────────────────────────────────────────┘
        ↓
Final Changes + Quality Report
```

### Core Principle

> **LLMs PROPOSE, deterministic systems DECIDE.**

Every agent has **reasoning authority only**. No agent directly writes files or executes processes. All mutations pass through deterministic security gates.

---

## Key Features

### 🤖 Multi-Agent Orchestration
- **7 Specialized Agents**: Repository Analyzer, Issue Analyzer, Planner, Coding Agent, Test Agent, Fix Agent, Reviewer
- **Deterministic State Machine**: 12 stages, 15+ validated transitions
- **Event System**: 17 event types for real-time monitoring

### 🔌 Multi-Provider LLM Support
- **11 Providers**: NVIDIA NIM, Gemini, Cloudflare Workers AI, Ollama Cloud, OpenCode Zen, OpenAI, Anthropic, OpenRouter, Ollama, OpenAI-compatible, Fake (testing)
- **Health-Aware Routing**: Circuit breakers, retry with exponential backoff, quota-aware failover
- **Per-Capability Chains**: Route different agent types through different providers
- **Mid-Stream Recovery**: Resume dropped streams on the next provider

### 🛡️ Deterministic Security
- **Path Validation**: Traversal protection, allowed roots
- **Patch Validation**: Hash verification, size limits, protected files
- **Execution Policy**: Executable allowlist, argument safety
- **Environment Sanitization**: Secret isolation, controlled env vars
- **Repair Policy**: Test tampering detection, config weakening prevention

### 📊 Production Ready
- **PostgreSQL Persistence**: Async SQLAlchemy with connection pooling
- **Health Endpoints**: `/health/live` + `/health/ready` with subsystem matrix
- **Operations Dashboard**: Live system health, provider status, failover history
- **Startup Validation**: Fail-fast configuration checks
- **Correlation IDs**: Structured logging with request tracing

### 🎯 Engineering Knowledge Graph (EKG)
- **Temporal Graph**: Nodes, edges, versions, provenance, history
- **Cross-Repository**: Multi-repo support with org-graph
- **Interactive Visualization**: React Flow v12 with d3-force layout
- **Semantic Retrieval**: Cosine similarity + lexical search

---

## Technology Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Backend** | Python 3.10+, FastAPI, Uvicorn | API server, async processing |
| **LLM Interface** | Provider-independent abstraction | Multi-provider failover |
| **Frontend** | Next.js 14, TypeScript, Tailwind CSS | Dashboard, graph explorer |
| **Database** | PostgreSQL 18, SQLAlchemy 2.x async, asyncpg | Persistence, state management |
| **Code Intelligence** | Tree-sitter (11 languages), custom parsers | Language-aware analysis |
| **Graph Visualization** | @xyflow/react (React Flow v12), d3-force | Interactive EKG explorer |
| **Testing** | pytest, pytest-asyncio, vitest | Backend + frontend tests |
| **CI/CD** | GitHub Actions | Automated testing, deployment |

---

## Quick Start

### Prerequisites

- **Python 3.10+**
- **Node.js 18+** (for frontend)
- **PostgreSQL 18+** (optional for development)
- **Docker** (optional for database setup)

### Backend Setup

```bash
# Clone the repository
git clone https://github.com/ajith1251/Dev-Pilot.git
cd Dev-Pilot/backend

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the test suite (no API keys needed)
python -m pytest -q --tb=short
# Expected: 1900+ passed, 17 skipped

# Start the API server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
# API docs: http://localhost:8000/docs
```

### Frontend Setup

```bash
cd ../frontend

# Install dependencies
npm install

# Start development server
npm run dev
# Dashboard: http://localhost:3000/dashboard
```

### Docker Setup (Optional)

```bash
# Start PostgreSQL
docker compose up -d

# Set environment variables
export DATABASE_URL="postgresql+asyncpg://devpilot:devpilot@localhost:5432/devpilot_test"
export TEST_DATABASE_URL="$DATABASE_URL"

# Run migrations
cd backend
alembic upgrade head
```

### LLM Provider Setup

```bash
# Option 1: NVIDIA NIM (recommended, free tier available)
# Get key at https://build.nvidia.com
echo "DEVPILOT_LLM_PROVIDER=nvidia" >> backend/.env
echo "NVIDIA_API_KEY=nvapi-..." >> backend/.env

# Option 2: Google Gemini (free tier)
# Get key at https://aistudio.google.com/apikey
echo "DEVPILOT_LLM_PROVIDER=gemini" >> backend/.env
echo "GEMINI_API_KEY=..." >> backend/.env

# Option 3: OpenAI
echo "DEVPILOT_LLM_PROVIDER=openai" >> backend/.env
echo "OPENAI_API_KEY=..." >> backend/.env
```

---

## Architecture

### System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        DEVPILOT PLATFORM                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │   Frontend   │  │   Backend    │  │  PostgreSQL  │          │
│  │  Next.js 14  │  │  FastAPI     │  │   Database   │          │
│  │  Dashboard   │◄─┤  API Server  ├─►│   State      │          │
│  │  + Graph     │  │  + Agents    │  │   Persistence│          │
│  └──────────────┘  └──────┬───────┘  └──────────────┘          │
│                           │                                     │
│                           ▼                                     │
│                   ┌──────────────┐                              │
│                   │   Provider   │                              │
│                   │    Router    │                              │
│                   └──────┬───────┘                              │
│                          │                                      │
│          ┌───────────────┼───────────────┐                      │
│          ▼               ▼               ▼                      │
│   ┌────────────┐  ┌────────────┐  ┌────────────┐               │
│   │   NVIDIA   │  │   Gemini   │  │   OpenAI   │  ... 11 total │
│   │    NIM     │  │            │  │            │               │
│   └────────────┘  └────────────┘  └────────────┘               │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Pipeline Flow

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
```

### Agent Architecture

All agents implement `BaseAgent[TInput, TOutput]`:

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

### Service Architecture

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

### LLM Providers

```
ProviderRouter (Phase 19B+)
    │
    ├── health-based selection   healthy > warming > unknown > degraded > unhealthy
    ├── recovery detection       success after a failure spell ⇒ recovery + warm-up
    ├── automatic probing        passive ProviderHealthProbe loop (out of window)
    ├── post-failure cooldown    skipped for PROVIDER_COOLDOWN_AFTER_FAILURE_SECONDS
    ├── adaptive timeouts        max(base, avg_latency × multiplier) capped
    ├── circuit probe priority   OPEN-past-cooldown ranks first (gets its probe)
    ├── bounded retry            exponential backoff, recoverable only
    ├── quota-aware failover     permanent quota exhaustion fails over immediately
    └── streaming failover       pre-first-token, mid-stream token-loss recovery
```

**Supported Providers:**

| Provider | Config Key | Default Model | Notes |
|----------|-----------|---------------|-------|
| NVIDIA NIM | `NVIDIA_API_KEY` | `meta/llama-3.1-8b-instruct` | Default provider, free tier |
| Gemini | `GEMINI_API_KEY` | `gemini-3.6-flash` | Free/Paid tier |
| Cloudflare | `CLOUDFLARE_API_KEY` | `@cf/meta/llama-4-scout-17b-16e-instruct` | Workers AI |
| Ollama Cloud | `OLLAMA_CLOUD_API_KEY` | `gemma4:31b` | Hosted Ollama |
| OpenCode Zen | `OPENCODE_ZEN_API_KEY` | `deepseek-v4-flash-free` | Free tier models |
| OpenAI | `OPENAI_API_KEY` | `gpt-4o-mini` | Requires API key |
| Anthropic | `ANTHROPIC_API_KEY` | `claude-3-haiku-20240307` | Requires API key |
| OpenRouter | `OPENROUTER_API_KEY` | `poolside/laguna-s-2.1:free` | Multi-model router |
| Ollama | `OLLAMA_BASE_URL` | — | Local Ollama server |
| OpenAI Compatible | `OPENAI_COMPATIBLE_BASE_URL` | — | vLLM, TGI, llama.cpp, etc. |
| Fake | — | — | Deterministic testing |

### Frontend Architecture

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
    ├── /dashboard/runs                — Run list (multi-repo badges)
    ├── /dashboard/runs/[id]           — Run detail timeline + repository status
    ├── /dashboard/durability          — Durability report
    ├── /dashboard/engineering-graph   — EKG graph explorer (React Flow v12)
    ├── /dashboard/organization-graph  — Organization graph
    ├── /dashboard/providers           — Provider router observability
    ├── /dashboard/operations          — Operations Dashboard (Phase 20B)
    └── /dashboard/code-intelligence   — Code Intelligence
```

---

## Pipeline Stages

### Stage 1: Repository Analysis

**Purpose:** Understand the codebase structure, languages, and conventions.

**Components:**
- `RepositoryAnalyzerAgent` — LLM-based analysis
- `RepositoryAnalysisWorkflow` — Orchestrates analysis pipeline
- 9 modular detector services (language, technology, dependencies, etc.)

**Output:** `RepositoryProfile` — comprehensive codebase understanding

**Key Files:**
- `backend/app/agents/repo_analyzer.py`
- `backend/app/services/repository_analyzer.py`
- `backend/app/workflows/repository_analysis.py`

---

### Stage 2: Task Analysis

**Purpose:** Extract structured requirements from user task or GitHub issue.

**Components:**
- `IssueAnalyzerAgent` — LLM-based issue parsing
- Prompt injection boundaries for security

**Output:** `StructuredRequirements` — objective, constraints, ambiguities, risks

**Key Files:**
- `backend/app/agents/issue_analyzer.py`
- `backend/app/models/issues.py`

---

### Stage 3: Planning

**Purpose:** Create step-by-step implementation plan.

**Components:**
- `PlannerAgent` — LLM-based plan generation
- `PlanValidator` — 100% deterministic validation (no LLM calls)
- `PlanningService` — Orchestrator

**Output:** `ImplementationPlan` — ordered steps with dependencies

**Key Files:**
- `backend/app/agents/planner.py`
- `backend/app/services/plan_validator.py`
- `backend/app/services/planning_service.py`

---

### Stage 4: Code Retrieval

**Purpose:** Retrieve relevant code context via hybrid RAG.

**Components:**
- `RepositoryIndexBuilder` — Code indexing (read-only)
- `HybridRetriever` — 4-signal weighted rank fusion
- `PlanContextRetriever` — Plan-aware retrieval

**Output:** `RetrievedContext` — relevant code snippets and context

**Key Files:**
- `backend/app/services/index_builder.py`
- `backend/app/rag/retrieval/hybrid_retriever.py`

---

### Stage 5: Coding

**Purpose:** Generate code changes following conventions.

**Components:**
- `CodingAgent` — LLM-based patch generation
- `CodingService` — Orchestrates coding pipeline

**Output:** `PatchSet` — structured code changes

**Key Files:**
- `backend/app/agents/coding_agent.py`
- `backend/app/services/coding_service.py`

---

### Stage 6: Patch Validation

**Purpose:** Deterministic security gate for file mutations.

**Components:**
- `PatchValidator` — Path safety, hash verification, size limits
- `SafePatchEngine` — Atomic writes, unified diffs, rollback
- `WorkspaceService` — Isolated writable copies

**Output:** Validated and applied changes

**Key Files:**
- `backend/app/services/patch_validator.py`
- `backend/app/services/safe_patch_engine.py`
- `backend/app/services/workspace_service.py`

---

### Stage 7: Testing

**Purpose:** Discover and execute tests safely.

**Components:**
- `TestAgent` — Deterministic by default, LLM optional
- `ExecutionPolicy` — Executable allowlist, argument validation
- `ControlledExecutionEngine` — Asyncio subprocess, timeout, env sanitization
- `TestingService` — Orchestrator

**Output:** `TestRunResult` — test results with failure classification

**Key Files:**
- `backend/app/agents/test_agent.py`
- `backend/app/services/testing_service.py`
- `backend/app/services/controlled_execution_engine.py`

---

### Stage 8: Repair

**Purpose:** Diagnose failures and perform bounded automated repair.

**Components:**
- `FailureDiagnosisService` — Deterministic triage (11 × 2 classification matrix)
- `FixAgent` — LLM-powered repair generation
- `RepairPolicy` — Security gate (test tampering, config weakening, etc.)
- `RepairService` — Bounded loop orchestrator

**Output:** `RepairResult` — repair attempts with progress detection

**Key Files:**
- `backend/app/agents/fix_agent.py`
- `backend/app/services/repair_service.py`
- `backend/app/services/failure_diagnosis_service.py`

---

### Stage 9: Review

**Purpose:** Review implementation against requirements.

**Components:**
- `ReviewerAgent` — Two-mode (deterministic + LLM-assisted)
- `ReviewContextBuilder` — Bounded context, secret redaction
- `DeterministicReview` — 21 DET-XXX checks across 9 categories
- `EvidenceValidator` — Hallucination protection

**Output:** `ReviewReport` — findings, requirement coverage, evidence

**Key Files:**
- `backend/app/agents/reviewer.py`
- `backend/app/services/review_service.py`
- `backend/app/services/deterministic_review.py`

---

### Stage 10: Quality Gate

**Purpose:** Deterministic acceptance decision.

**Components:**
- `QualityGate` — 100% deterministic, hard rejection rules

**Output:** `APPROVED | REJECTED | NEEDS_HUMAN_REVIEW`

**Key Files:**
- `backend/app/services/quality_gate.py`

---

## Standard Operating Procedure (SOP)

### SOP 1: Running a Full Pipeline

#### Prerequisites
- Python 3.10+ installed
- API key configured (NVIDIA NIM, Gemini, or OpenAI)
- Repository path or GitHub URL

#### Steps

```bash
# 1. Navigate to backend directory
cd DevPilot/backend

# 2. Activate virtual environment
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 3. Configure LLM provider (if not already done)
echo "DEVPILOT_LLM_PROVIDER=nvidia" >> .env
echo "NVIDIA_API_KEY=nvapi-..." >> .env

# 4. Run the full pipeline via CLI
python -m app.cli run /path/to/repository \
  --task "Add user authentication with JWT tokens" \
  --description "Implement secure authentication using JSON Web Tokens"

# 5. Or via API
curl -X POST http://localhost:8000/api/v1/runs \
  -H "Content-Type: application/json" \
  -d '{
    "repository_path": "/path/to/repository",
    "task": "Add user authentication",
    "description": "Implement secure JWT authentication"
  }'

# 6. Monitor progress via WebSocket
wscat -c ws://localhost:8000/api/v1/ws/runs/{run_id}

# 7. Check run status
python -m app.cli run /path/to/repository --task "..." --json
```

#### Expected Output
- Repository analysis completed
- Implementation plan generated
- Code changes implemented
- Tests executed and passed
- Review completed with quality gate decision
- Final status: `APPROVED` or `REJECTED` with reasons

---

### SOP 2: Running Tests

#### Full Test Suite (No API Keys Required)

```bash
cd DevPilot/backend

# Run all tests
python -m pytest -v

# Run specific test file
python -m pytest tests/test_coding.py -v

# Run tests with coverage
python -m pytest --cov=app --cov-report=html

# Expected: 1900+ passed, 17 skipped
```

#### Live Provider Tests

```bash
# Requires API key configured in .env
python -m pytest -m live -v

# Run durability tests
python scripts/durability_report.py --out durability_report.json
```

#### Database Tests

```bash
# Requires PostgreSQL running
export TEST_DATABASE_URL="postgresql+asyncpg://devpilot:devpilot@localhost:5432/devpilot_test"
python -m pytest -m integration -v
```

#### Frontend Tests

```bash
cd DevPilot/frontend

# Run vitest
npm test

# Run in watch mode
npm run test:watch

# Build check
npm run build
```

---

### SOP 3: Development Workflow

#### Adding a New Feature

```bash
# 1. Create feature branch
git checkout -b feature/my-new-feature

# 2. Implement changes
# ... modify files ...

# 3. Run tests
cd backend && python -m pytest -q

# 4. Run type checking
python -m mypy app/

# 5. Run linting
python -m ruff check app/

# 6. Commit changes
git add .
git commit -m "feat: add new feature description"

# 7. Push to remote
git push origin feature/my-new-feature
```

#### Adding a New API Endpoint

1. Create route in `backend/app/api/v1/`
2. Add request/response models in `backend/app/models/`
3. Implement service logic in `backend/app/services/`
4. Add tests in `backend/tests/`
5. Update this README with endpoint documentation

#### Adding a New Agent

1. Create agent in `backend/app/agents/`
2. Implement `BaseAgent[TInput, TOutput]` interface
3. Register in agent registry
4. Add service layer in `backend/app/services/`
5. Wire into orchestration pipeline
6. Add comprehensive tests

---

### SOP 4: Adding a New LLM Provider

#### Steps

1. **Create provider class:**
   ```bash
   # backend/app/llm/providers/my_provider.py
   from app.llm.base import BaseLLMProvider

   class MyProvider(BaseLLMProvider):
       async def chat(self, messages, **kwargs):
           # Implementation
           pass
   ```

2. **Add config fields:**
   ```python
   # backend/app/config.py
   MY_PROVIDER_API_KEY: Optional[str] = Field(default=None, alias="MY_PROVIDER_API_KEY")
   MY_PROVIDER_BASE_URL: Optional[str] = Field(default=None, alias="MY_PROVIDER_BASE_URL")
   MY_PROVIDER_MODEL: Optional[str] = Field(default=None, alias="DEVPILOT_MY_PROVIDER_MODEL")
   ```

3. **Register in provider registry:**
   ```python
   # backend/app/llm/provider_registry.py
   register_provider(
       "my_provider",
       MyProvider,
       "MY_PROVIDER_API_KEY",
       description="My custom provider"
   )
   ```

4. **Add tests:**
   ```python
   # backend/tests/test_llm_providers.py
   def test_my_provider_availability():
       # Test provider availability
       pass
   ```

5. **Update documentation:**
   - Add to `docs/MULTI_PROVIDER_ROUTING.md`
   - Update this README's provider table

---

### SOP 5: Database Setup

#### Local PostgreSQL Setup

```bash
# 1. Install PostgreSQL 18
# https://www.postgresql.org/download/

# 2. Create databases
python -m app.db.setup_databases

# 3. Verify connectivity
python -m app.cli db-check

# 4. Run migrations
cd backend
alembic upgrade head

# 5. Verify schema
python -m app.cli verify-persistence
```

#### Docker Setup

```bash
# 1. Start PostgreSQL container
docker compose up -d

# 2. Set environment variables
export DATABASE_URL="postgresql+asyncpg://devpilot:devpilot@localhost:5432/devpilot_test"
export TEST_DATABASE_URL="$DATABASE_URL"

# 3. Run migrations
cd backend
alembic upgrade head

# 4. Verify
python -m app.cli verify
```

#### Migration Workflow

```bash
# Create new migration
alembic revision --autogenerate -m "description of changes"

# Apply migrations
alembic upgrade head

# Rollback
alembic downgrade -1

# Check current version
alembic current
```

---

## Project Structure

### Backend Structure

```
DevPilot/backend/
├── app/
│   ├── __init__.py
│   ├── main.py                    # FastAPI application entry point
│   ├── config.py                  # Configuration management (pydantic-settings)
│   ├── cli.py                     # CLI tool (analyze, plan, github, run, db-check)
│   ├── cli_autonomy.py            # Phase 16: Autonomous execution CLI
│   ├── cli_code_intelligence.py   # Phase 12: Code intelligence CLI
│   ├── cli_collaboration.py       # Phase 15: Collaboration CLI
│   ├── cli_context.py             # Phase 13: Context engineering CLI
│   ├── cli_engineering_graph.py   # Phase 18: EKG CLI
│   ├── cli_operations.py          # Phase 20B: Operations CLI
│   ├── cli_providers.py           # Phase 19B: Provider CLI
│   ├── cli_reasoning.py           # Phase 17: Reasoning CLI
│   ├── cli_replay.py              # Phase 21: Replay CLI
│   │
│   ├── agents/                    # AI Agents
│   │   ├── base.py                # BaseAgent[TInput, TOutput] abstract class
│   │   ├── registry.py            # Agent registry
│   │   ├── repo_analyzer.py       # Phase 1: Repository analysis agent
│   │   ├── issue_analyzer.py      # Phase 1: Issue analysis agent
│   │   ├── planner.py             # Phase 4: Planning agent
│   │   ├── coding_agent.py        # Phase 6: Coding agent
│   │   ├── test_agent.py          # Phase 7: Testing agent
│   │   ├── fix_agent.py           # Phase 8: Fix/repair agent
│   │   ├── reviewer.py            # Phase 9: Review agent
│   │   └── json_repair.py         # JSON repair utilities
│   │
│   ├── api/                       # API Routes
│   │   ├── health.py              # Health check endpoints
│   │   └── v1/
│   │       ├── repositories.py    # Phase 2: Repository analysis API
│   │       ├── github.py          # Phase 3: GitHub API
│   │       ├── planning.py        # Phase 4: Planning API
│   │       ├── code_intelligence.py  # Phase 5: Code intelligence API
│   │       ├── code_intelligence_v2.py  # Phase 12: Advanced code intelligence
│   │       ├── coding.py          # Phase 6: Coding API
│   │       ├── testing.py         # Phase 7: Testing API
│   │       ├── repair.py          # Phase 8: Repair API
│   │       ├── review.py          # Phase 9: Review API
│   │       ├── orchestration.py   # Phase 10: Orchestration API
│   │       ├── ws.py              # Phase 11: WebSocket API
│   │       ├── context.py         # Phase 13: Context API
│   │       ├── memory.py          # Phase 15: Memory API
│   │       ├── collaboration.py   # Phase 15: Collaboration API
│   │       ├── autonomy.py        # Phase 16: Autonomy API
│   │       ├── reasoning.py       # Phase 17: Reasoning API
│   │       ├── engineering_graph.py  # Phase 18: EKG API
│   │       ├── durability.py      # Phase 19: Durability API
│   │       ├── providers.py       # Phase 19B: Provider API
│   │       ├── operations.py      # Phase 20B: Operations API
│   │       ├── replay.py          # Phase 21: Replay API
│   │       └── repositories.py    # Repository management API
│   │
│   ├── models/                    # Data Models
│   │   ├── orchestration.py       # Phase 10: DevPilotRun, RunStateMachine
│   │   ├── issues.py              # Phase 4: Planning models
│   │   ├── rag.py                 # Phase 5: RAG models
│   │   ├── coding.py              # Phase 6: Coding models
│   │   ├── testing.py             # Phase 7: Testing models
│   │   ├── repair.py              # Phase 8: Repair models
│   │   ├── review.py              # Phase 9: Review models
│   │   ├── replay.py              # Phase 21: Replay models
│   │   └── ...
│   │
│   ├── services/                  # Business Logic
│   │   ├── orchestration_service.py  # Phase 10: Pipeline orchestrator
│   │   ├── repository_analyzer.py    # Phase 2: Repository analysis
│   │   ├── github.py                 # Phase 3: GitHub integration
│   │   ├── acquisition.py            # Phase 3: Repository acquisition
│   │   ├── planning_service.py       # Phase 4: Planning
│   │   ├── plan_validator.py         # Phase 4: Plan validation
│   │   ├── index_builder.py          # Phase 5: Code indexing
│   │   ├── coding_service.py         # Phase 6: Coding
│   │   ├── patch_validator.py        # Phase 6: Patch validation
│   │   ├── safe_patch_engine.py      # Phase 6: Safe patch application
│   │   ├── workspace_service.py      # Phase 6: Workspace management
│   │   ├── testing_service.py        # Phase 7: Testing
│   │   ├── execution_policy.py       # Phase 7: Execution policy
│   │   ├── controlled_execution_engine.py  # Phase 7: Controlled execution
│   │   ├── repair_service.py         # Phase 8: Repair
│   │   ├── failure_diagnosis_service.py  # Phase 8: Failure diagnosis
│   │   ├── repair_policy.py          # Phase 8: Repair policy
│   │   ├── review_service.py         # Phase 9: Review
│   │   ├── review_context_builder.py  # Phase 9: Context building
│   │   ├── review_evidence_validator.py  # Phase 9: Evidence validation
│   │   ├── deterministic_review.py   # Phase 9: Deterministic review
│   │   ├── quality_gate.py           # Phase 9: Quality gate
│   │   ├── run_store.py             # Phase 10: Run storage
│   │   ├── run_dashboard.py          # Phase 20A6: Dashboard view builder
│   │   ├── engineering_graph_service.py  # Phase 18: EKG
│   │   ├── organization_graph_service.py  # Phase 12: Organization graph
│   │   ├── context_engine.py         # Phase 13: Context engineering
│   │   ├── repository_memory_service.py  # Phase 13: Repository memory
│   │   ├── collaboration_service.py  # Phase 15: Collaboration
│   │   ├── autonomy_service.py       # Phase 16: Autonomy
│   │   ├── reasoning_service.py      # Phase 17: Reasoning
│   │   ├── provider_probe.py         # Phase 20B: Provider probing
│   │   ├── provider_metrics_persistence.py  # Phase 20B: Metrics persistence
│   │   ├── replay_service.py         # Phase 21: Replay
│   │   └── ...
│   │
│   ├── llm/                       # LLM Abstraction
│   │   ├── base.py                # BaseLLMProvider abstract class
│   │   ├── factory.py             # LLMFactory
│   │   ├── router.py              # ProviderRouter (Phase 19B)
│   │   ├── redaction.py           # API key redaction
│   │   ├── provider_registry.py   # Centralized provider registry
│   │   └── providers/
│   │       ├── nvidia.py          # NVIDIA NIM provider
│   │       ├── gemini.py          # Google Gemini provider
│   │       ├── cloudflare.py      # Cloudflare Workers AI
│   │       ├── ollama_cloud.py    # Ollama Cloud
│   │       ├── opencode_zen.py    # OpenCode Zen
│   │       ├── openai.py          # OpenAI provider
│   │       ├── anthropic.py       # Anthropic provider
│   │       ├── openrouter.py      # OpenRouter provider
│   │       ├── ollama.py          # Local Ollama
│   │       ├── openai_compatible.py  # Generic OpenAI-compatible
│   │       └── fake.py            # Fake provider (testing)
│   │
│   ├── db/                        # Database
│   │   ├── database.py            # Async engine, connection pool
│   │   ├── models.py              # SQLAlchemy models
│   │   └── setup_databases.py     # Database setup script
│   │
│   ├── rag/                       # RAG (Retrieval-Augmented Generation)
│   │   ├── parsers/               # Code parsers
│   │   ├── indexes/               # Lexical, symbol, vector indexes
│   │   ├── embeddings/            # Embedding providers
│   │   └── retrieval/             # Hybrid retriever
│   │
│   ├── code_intelligence/         # Code Intelligence (Phase 12)
│   │
│   ├── testing/                   # Test Framework Parsers (Phase 20E)
│   │   └── parsers/
│   │       ├── pytest_parser.py
│   │       ├── unittest_xml_parser.py
│   │       ├── vitest_json_parser.py
│   │       └── jest_json_parser.py
│   │
│   ├── prompts/                   # LLM Prompts
│   ├── tools/                     # Tool abstractions
│   ├── core/                      # Core utilities
│   │   ├── exceptions.py
│   │   ├── logging.py
│   │   ├── context.py
│   │   ├── middleware.py
│   │   └── startup_validation.py
│   └── workflows/                 # Workflow orchestration
│       ├── repository_analysis.py
│       ├── remote_analysis.py
│       ├── planning.py
│       ├── code_intelligence.py
│       ├── coding.py
│       ├── testing.py
│       ├── repair.py
│       ├── review.py
│       └── orchestration.py
│
├── tests/                         # Test Suite (1900+ tests)
│   ├── fixtures/                  # Test fixtures
│   ├── test_agents.py
│   ├── test_analyzer_tools.py
│   ├── test_github_integration.py
│   ├── test_planner.py
│   ├── test_coding.py
│   ├── test_testing.py
│   ├── test_repair.py
│   ├── test_review.py
│   ├── test_orchestration.py
│   ├── test_database.py
│   ├── test_provider_router.py
│   ├── test_engineering_graph.py
│   ├── test_phase20b_provider_reliability.py
│   ├── test_phase20b_operations.py
│   ├── test_startup_validation.py
│   ├── test_replay.py
│   └── ...
│
├── scripts/                       # Demo & Utility Scripts
│   ├── demo_phase15.py
│   ├── demo_phase16.py
│   ├── demo_phase17.py
│   ├── demo_phase18.py
│   ├── demo_phase19a.py
│   ├── demo_phase19c.py
│   ├── demo_phase20.py
│   ├── demo_phase20b.py
│   ├── benchmark_models.py
│   ├── durability_report.py
│   └── verify_api_durability.py
│
├── alembic/                       # Database Migrations
│   └── versions/                  # Migration files (001-015)
│
├── requirements.txt               # Python dependencies
├── pyproject.toml                 # Project configuration
└── alembic.ini                    # Alembic configuration
```

### Frontend Structure

```
DevPilot/frontend/
├── src/
│   ├── app/
│   │   ├── layout.tsx             # Root layout
│   │   ├── page.tsx               # Home page
│   │   └── dashboard/
│   │       ├── layout.tsx         # Dashboard layout
│   │       ├── page.tsx           # Overview stats
│   │       ├── analysis/          # Repository analysis
│   │       ├── planning/          # Planning view
│   │       ├── coding/            # Coding view
│   │       ├── testing/           # Testing view
│   │       ├── repair/            # Repair view
│   │       ├── review/            # Review & quality gate
│   │       ├── runs/
│   │       │   ├── page.tsx       # Run list
│   │       │   └── [id]/page.tsx  # Run detail
│   │       ├── durability/        # Durability report
│   │       ├── engineering-graph/ # EKG graph explorer
│   │       ├── organization-graph/ # Organization graph
│   │       ├── providers/         # Provider observability
│   │       ├── operations/        # Operations dashboard
│   │       └── code-intelligence/ # Code intelligence
│   │
│   ├── components/
│   │   ├── graph/
│   │   │   ├── InteractiveGraph.tsx  # React Flow v12 graph
│   │   │   └── ...
│   │   ├── runs/
│   │   │   ├── RepositoryStatusCards.tsx
│   │   │   ├── RepositoryTimeline.tsx
│   │   │   ├── OrganizationSummary.tsx
│   │   │   ├── RunHistoryPanel.tsx
│   │   │   └── ...
│   │   └── replay/
│   │       ├── ReplaySection.tsx
│   │       ├── ReplayTimeline.tsx
│   │       ├── DifferenceViewer.tsx
│   │       ├── AuditReport.tsx
│   │       └── ReplayHistory.tsx
│   │
│   └── lib/
│       ├── api/
│       │   ├── client.ts          # API client
│       │   └── client.test.ts     # API client tests
│       ├── graph/
│       │   ├── graphModel.ts      # Graph data models
│       │   └── orgGraphModel.ts   # Organization graph models
│       └── replay/
│           └── replayModel.ts     # Replay models
│
├── public/                        # Static assets
├── package.json                   # Node.js dependencies
├── tsconfig.json                  # TypeScript configuration
├── tailwind.config.ts             # Tailwind CSS configuration
├── next.config.js                 # Next.js configuration
└── vitest.config.ts               # Vitest configuration
```

### Documentation

```
DevPilot/docs/
├── ARCHITECTURE.md                # Full pipeline architecture
├── ORCHESTRATION.md               # Phase 10: Orchestration
├── REVIEW_AND_QUALITY_GATE.md     # Phase 9: Review & Quality Gate
├── REPAIR_AND_RECOVERY.md         # Phase 8: Fix Agent
├── TESTING_AND_EXECUTION.md       # Phase 7: Test Agent
├── CODING_AGENT.md                # Phase 6: Coding Agent
├── PLANNING.md                    # Phase 4: Planning
├── DATABASE.md                    # PostgreSQL setup
├── ENGINEERING_KNOWLEDGE_GRAPH.md # Phase 18: EKG
├── GRAPH_VISUALIZATION.md         # Phase 19C: Interactive visualization
├── MULTI_PROVIDER_ROUTING.md      # Phase 19B: Provider router
├── PRODUCTION_RELIABILITY.md      # Phase 20B: Production hardening
├── GEMINI_API_KEY_REPORT.md       # Gemini provider workflow
├── RUN_AUDIT_AND_REPLAY.md        # Phase 21: Replay
├── CODE_INTELLIGENCE.md           # Phase 12: Code intelligence
├── CONTEXT_AND_MEMORY.md          # Phase 13: Context engineering
├── MULTI_AGENT_COLLABORATION.md   # Phase 15: Collaboration
├── AUTONOMOUS_EXECUTION.md        # Phase 16: Autonomy
├── COLLABORATIVE_REASONING.md     # Phase 17: Reasoning
└── ...
```

---

## Configuration

### Environment Variables

#### Application

| Variable | Default | Description |
|----------|---------|-------------|
| `DEVPILOT_DEBUG` | `false` | Enable debug mode |
| `DEVPILOT_LOG_LEVEL` | `INFO` | Logging level |
| `DEVPILOT_HOST` | `0.0.0.0` | Server host |
| `DEVPILOT_PORT` | `8000` | Server port |
| `DEVPILOT_CORS_ORIGINS` | `["*"]` | CORS origins |

#### LLM Provider

| Variable | Default | Description |
|----------|---------|-------------|
| `DEVPILOT_LLM_PROVIDER` | `openai` | Default LLM provider |
| `DEVPILOT_LLM_MODEL` | `gpt-4o-mini` | Default model |
| `DEVPILOT_LLM_TEMPERATURE` | `0.3` | Temperature |
| `DEVPILOT_LLM_MAX_TOKENS` | `4096` | Max tokens |

#### Provider API Keys

| Variable | Description |
|----------|-------------|
| `NVIDIA_API_KEY` | NVIDIA NIM API key |
| `GEMINI_API_KEY` | Google Gemini API key |
| `OPENAI_API_KEY` | OpenAI API key |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `OPENROUTER_API_KEY` | OpenRouter API key |
| `OLLAMA_BASE_URL` | Local Ollama endpoint |
| `CLOUDFLARE_API_KEY` | Cloudflare API token |
| `OLLAMA_CLOUD_API_KEY` | Ollama Cloud API key |
| `OPENCODE_ZEN_API_KEY` | OpenCode Zen API key |
| `OPENAI_COMPATIBLE_BASE_URL` | OpenAI-compatible endpoint |

#### Provider Routing

| Variable | Default | Description |
|----------|---------|-------------|
| `DEVPILOT_PROVIDER_PRIORITY` | `[]` | Provider priority chain |
| `DEVPILOT_PROVIDER_DISABLED` | `[]` | Disabled providers |
| `DEVPILOT_LLM_PROVIDER_FALLBACKS` | `{}` | Per-capability fallback chains |
| `DEVPILOT_PROVIDER_TIMEOUT_SECONDS` | `60` | Per-call timeout |
| `DEVPILOT_PROVIDER_RETRY_MAX` | `2` | Retry count |
| `DEVPILOT_PROVIDER_STREAM_RESUME_MAX` | `3` | Mid-stream recovery max |
| `DEVPILOT_PROVIDER_HEALTH_PROBE_ENABLED` | `true` | Enable health probing |
| `DEVPILOT_PROVIDER_HEALTH_BASED_SELECTION` | `true` | Health-based selection |

#### Database

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string |
| `TEST_DATABASE_URL` | Test database connection string |

### Provider Configuration

#### NVIDIA NIM (Recommended)

```bash
# .env
DEVPILOT_LLM_PROVIDER=nvidia
NVIDIA_API_KEY=nvapi-...
DEVPILOT_NVIDIA_MODEL=meta/llama-3.1-8b-instruct
DEVPILOT_NVIDIA_TIMEOUT_SECONDS=300
```

#### Gemini (Free Tier)

```bash
# .env
DEVPILOT_LLM_PROVIDER=gemini
GEMINI_API_KEY=...
DEVPILOT_GEMINI_TIER=free
```

#### Per-Capability Chains

```bash
# .env
DEVPILOT_LLM_PROVIDER_FALLBACKS={"analysis":"gemini,nvidia","planning":"nvidia,gemini","coding":"gemini,nvidia","testing":"nvidia,gemini","review":"gemini,nvidia","reasoning":"gemini,nvidia"}
```

### Database Configuration

```bash
# .env
DATABASE_URL=postgresql+asyncpg://devpilot:devpilot@localhost:5432/devpilot_test
TEST_DATABASE_URL=postgresql+asyncpg://devpilot:devpilot@localhost:5432/devpilot_test
```

---

## API Reference

### Health Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/health/live` | Liveness probe (always 200) |
| GET | `/health/ready` | Readiness probe (200/503) |

### Core Endpoints

| Method | Path | Description | Phase |
|--------|------|-------------|-------|
| POST | `/api/v1/repositories/analyze` | Analyze local repository | 2 |
| GET | `/api/v1/repositories/capabilities` | List detector capabilities | 2 |
| POST | `/api/v1/github/repositories/analyze` | Analyze remote repo | 3 |
| GET | `/api/v1/github/repositories/{owner}/{repo}` | Repo metadata | 3 |
| GET | `/api/v1/github/repositories/{owner}/{repo}/branches` | List branches | 3 |
| GET | `/api/v1/github/repositories/{owner}/{repo}/issues` | List issues | 3 |
| POST | `/api/v1/planning/plan` | Plan from user task | 4 |
| POST | `/api/v1/planning/github/plan` | Plan from GitHub issue | 4 |
| POST | `/api/v1/code-intelligence/index/build` | Build code index | 5 |
| POST | `/api/v1/code-intelligence/retrieval/search` | Search code | 5 |
| POST | `/api/v1/coding/generate` | Generate patch | 6 |
| POST | `/api/v1/coding/dry-run` | Dry-run patch | 6 |
| POST | `/api/v1/coding/apply` | Apply patch | 6 |
| POST | `/api/v1/testing/plan` | Create execution plan | 7 |
| POST | `/api/v1/testing/run` | Execute tests | 7 |
| POST | `/api/v1/repair/diagnose` | Diagnose failures | 8 |
| POST | `/api/v1/repair/run` | Execute repair | 8 |
| POST | `/api/v1/review/run` | Execute review | 9 |
| POST | `/api/v1/runs` | Create/execute run | 10 |
| GET | `/api/v1/runs` | List runs | 10 |
| GET | `/api/v1/runs/{run_id}` | Get run | 10 |
| POST | `/api/v1/runs/{run_id}/cancel` | Cancel run | 10 |
| GET | `/api/v1/runs/{run_id}/events` | Run events | 10 |
| WS | `/api/v1/ws/runs/{run_id}` | WebSocket run updates | 11 |

### Provider Endpoints

| Method | Path | Description | Phase |
|--------|------|-------------|-------|
| GET | `/api/v1/providers` | Registered providers | 19B |
| GET | `/api/v1/providers/health` | Provider health | 19B |
| GET | `/api/v1/providers/metrics` | Runtime metrics | 19B |
| GET | `/api/v1/providers/config` | Routing configuration | 19B |
| POST | `/api/v1/providers/test` | Test provider | 19B |

### Operations Endpoints

| Method | Path | Description | Phase |
|--------|------|-------------|-------|
| GET | `/api/v1/operations/status` | Subsystem health | 20B |
| GET | `/api/v1/operations/metrics` | Operational metrics | 20B |
| GET | `/api/v1/operations/startup-validation` | Startup findings | 20B |

---

## CLI Reference

### Analysis Commands

```bash
# Analyze local repository
python -m app.cli analyze /path/to/repo --depth 10

# Analyze remote GitHub repository
python -m app.cli github analyze https://github.com/owner/repo

# Fetch GitHub issue
python -m app.cli github issue https://github.com/owner/repo/issues/42
```

### Planning Commands

```bash
# Plan from task
python -m app.cli plan --task "Add authentication" --repo-path /path/to/repo

# Plan from GitHub issue
python -m app.cli github plan https://github.com/owner/repo/issues/42
```

### Coding Commands

```bash
# Generate code changes
python -m app.cli code \
  --plan-file plan.json \
  --context-file context.json \
  --repo-path /path/to/repo \
  --output patch.json \
  --dry-run
```

### Testing Commands

```bash
# Create test plan
python -m app.cli test-plan --workspace /path/to/workspace

# Run tests
python -m app.cli test --workspace /path/to/workspace --timeout 120
```

### Repair Commands

```bash
# Diagnose failures
python -m app.cli repair-diagnose --run-file test_result.json

# Execute repair
python -m app.cli repair --workspace /path/to/workspace --run-file test_result.json
```

### Review Commands

```bash
# Review implementation
python -m app.cli review --workspace-id my-workspace --verbose
```

### Orchestration Commands

```bash
# Run full pipeline
python -m app.cli run /path/to/repo \
  --task "Add authentication" \
  --description "Implement JWT auth" \
  --json

# Run with auxiliary repositories
python -m app.cli run /path/to/repo \
  --task "Refactor" \
  --aux-repo shared-lib=/path/to/shared-lib
```

### Provider Commands

```bash
# List providers
python -m app.cli providers --json

# Check provider health
python -m app.cli provider-health --json

# Test provider
python -m app.cli provider-test --message "Hello" --json
```

### Operations Commands

```bash
# Validate configuration
python -m app.cli validate-config --json

# Check operations status
python -m app.cli ops-status --json

# View operational metrics
python -m app.cli ops-metrics --json
```

### Database Commands

```bash
# Check database connectivity
python -m app.cli db-check

# Verify persistence
python -m app.cli verify-persistence

# Run all verification checks
python -m app.cli verify
```

---

## Testing

### Test Suite

DevPilot has **1900+ tests** across 80+ test files:

| Test File | Tests | Phase |
|-----------|-------|-------|
| test_agents.py | 8 | 1 |
| test_analyzer_tools.py | 20 | 1 |
| test_github_integration.py | 32 | 3 |
| test_planner.py | 17 | 4 |
| test_code_intelligence.py | 57 | 5 |
| test_coding.py | 43 | 6 |
| test_testing.py | 79 | 7 |
| test_repair.py | 77 | 8 |
| test_review.py | 66 | 9 |
| test_orchestration.py | 50 | 10 |
| test_database.py | 29 | DB |
| test_provider_router.py | 75 | 19B+20B |
| test_engineering_graph.py | 45+ | 18 |
| test_phase20b_provider_reliability.py | 19 | 20B |
| test_phase20b_operations.py | 17 | 20B |
| test_startup_validation.py | 14 | 20B |
| test_replay.py | 34 | 21 |
| **Total** | **1900+** | **All** |

### Running Tests

```bash
# Full test suite (no API keys needed)
cd DevPilot/backend
python -m pytest -q --tb=short
# Expected: 1900+ passed, 17 skipped

# Specific test file
python -m pytest tests/test_coding.py -v

# With coverage
python -m pytest --cov=app --cov-report=html

# Live provider tests (requires API key)
python -m pytest -m live -v

# Database tests (requires PostgreSQL)
python -m pytest -m integration -v

# Frontend tests
cd DevPilot/frontend
npm test
```

### Test Coverage

- **Backend**: 90%+ coverage on core modules
- **Frontend**: All components tested with vitest
- **Integration**: Full E2E with live providers and PostgreSQL
- **Deterministic**: All tests runnable without API keys (using fake provider)

---

## Phase Status

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
| DB | PostgreSQL Infrastructure | ✅ Complete |
| 11 | Persistent State + Run/Task Management | ✅ Complete |
| 12 | Semantic Graph + Code Intelligence | ✅ Complete |
| 13 | Context Engineering + Repository Memory | ✅ Complete |
| 14 | Hardening, Integration Tests & Documentation | ✅ Complete |
| 15 | Multi-Agent Collaboration | ✅ Complete |
| 16 | Autonomous Execution | ✅ Complete |
| 17 | Collaborative Reasoning & Evidence Consensus | ✅ Complete |
| 18 | Engineering Knowledge Graph (EKG) | ✅ Complete |
| 19 | Semantic EKG Retrieval + Test Selection | ✅ Complete |
| 19B | Multi-Provider Failover & Reliability | ✅ Complete |
| 19C | Interactive EKG Visualization | ✅ Complete |
| 20 | Cross-Repository Autonomous Runs | ✅ Complete |
| 20B | Production Reliability & Operational Hardening | ✅ Complete |
| 21 | Run Replay & Deterministic Reproduction | ✅ Complete |

---

## Contributing

Contributions are welcome! Please follow these steps:

1. **Fork the repository**
2. **Create a feature branch** (`git checkout -b feature/amazing-feature`)
3. **Make your changes**
4. **Run tests** (`python -m pytest -q`)
5. **Commit your changes** (`git commit -m 'feat: add amazing feature'`)
6. **Push to the branch** (`git push origin feature/amazing-feature`)
7. **Open a Pull Request**

### Development Guidelines

- Follow PEP 8 for Python code
- Use TypeScript for frontend code
- Write tests for all new features
- Update documentation for API changes
- Use conventional commit messages (`feat:`, `fix:`, `docs:`, etc.)

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

<div align="center">

**Built with ❤️ as part of the [500+ AI Agent Projects](https://github.com/ashishpatel26/500-AI-Agents-Projects) collection**

[⬆ Back to Top](#-devpilot)

</div>
