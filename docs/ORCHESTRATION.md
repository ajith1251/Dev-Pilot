# Phase 10 — End-to-End Multi-Agent Orchestration

> **Status**: Complete ✅
> **Tests**: 50 Phase 10 tests + 653 total, 0 failed, 4 skipped
> **Database**: PostgreSQL 18.4 — connected and verified
> **Last updated**: July 30, 2026

---

## 1. Overview

Phases 1–9 built individually tested capabilities:

| Phase | Capability |
|-------|-----------|
| 1–3 | Repository analysis, GitHub integration |
| 4 | Issue analysis, planning |
| 5 | Code-aware indexing, hybrid RAG |
| 6 | Coding Agent, safe patch engine |
| 7 | Test Agent, controlled execution |
| 8 | Fix Agent, bounded repair loop |
| 9 | Reviewer Agent, deterministic quality gate |

Phase 10 connects them all into **one run**:

```
User Task / GitHub Issue
         ↓
   DevPilot Run
         ↓
Repository Analysis → Task Analysis
         ↓
     Planning
         ↓
  Code Retrieval
         ↓
     Coding
         ↓
 Patch Validation → Patch Application
         ↓
     Testing
    /       \
  PASS     FAIL
   │         │
   │     Repair Loop
   │         │
   └────┬────┘
        ↓
    Review
        ↓
 Quality Gate
        ↓
APPROVED | REJECTED | NEEDS HUMAN REVIEW
```

### Fundamental Invariant

> **The orchestrator coordinates authority. It does not acquire authority.**

The orchestrator calls existing trusted services. It never:
- Writes source files directly
- Executes arbitrary commands
- Bypasses PatchValidator or ExecutionPolicy
- Ignores repair limits
- Overrides QualityGate decisions
- Pushes Git changes

---

## 2. Run Model

### DevPilotRun

| Field | Type | Description |
|-------|------|-------------|
| `run_id` | `str` | e.g. `RUN-ABC12345` |
| `source` | `RunSource` | Task/issue origin |
| `status` | `RunStatus` | Current high-level status |
| `current_stage` | `StageType` | Current pipeline stage |
| `created_at` | `str` | ISO 8601 |
| `stage_results` | `list[StageResult]` | Per-stage outcomes |
| `events` | `list[RunEvent]` | Ordered event stream |
| `failure` | `RunFailure` | Structured error info |

### RunStatus

| Status | Terminal | Meaning |
|--------|----------|---------|
| `PENDING` | ❌ | Created, not started |
| `RUNNING` | ❌ | Pipeline executing |
| `APPROVED` | ✅ | Quality gate passed |
| `REJECTED` | ✅ | Quality gate rejected |
| `NEEDS_HUMAN_REVIEW` | ✅ | Insufficient evidence for gate |
| `FAILED` | ✅ | Infrastructure/process failure |
| `CANCELLED` | ✅ | User requested cancellation |

### RunSourceType

- `USER_TASK` — local repository + task description
- `GITHUB_ISSUE` — remote GitHub repository + issue number

---

## 3. State Machine

### Stages

| Stage | Description |
|-------|-------------|
| `INITIALIZING` | Run bootstrap |
| `ACQUIRING_REPOSITORY` | Clone GitHub repo (if applicable) |
| `ANALYZING_REPOSITORY` | Deterministic repo analysis (Phase 2) |
| `ANALYZING_TASK` | Task/issue parsing (Phase 4) |
| `PLANNING` | Plan generation + validation (Phase 4) |
| `RETRIEVING_CONTEXT` | Code-aware RAG retrieval (Phase 5) |
| `CODING` | Patch generation (Phase 6) |
| `VALIDATING_PATCH` | Deterministic patch safety (Phase 6) |
| `APPLYING_PATCH` | Atomic file mutation (Phase 6) |
| `TESTING` | Controlled test execution (Phase 7) |
| `REPAIRING` | Bounded repair loop (Phase 8) |
| `REVIEWING` | Review + quality assessment (Phase 9) |
| `QUALITY_GATE` | Deterministic final decision (Phase 9) |
| `COMPLETED` | Terminal — pipeline finished |
| `FAILED` | Terminal — unrecoverable error |
| `CANCELLED` | Terminal — user cancelled |

### Transitions

```
INITIALIZING → ACQUIRING_REPOSITORY
ACQUIRING_REPOSITORY → ANALYZING_REPOSITORY | FAILED | CANCELLED
ANALYZING_REPOSITORY → ANALYZING_TASK | FAILED | CANCELLED
ANALYZING_TASK → PLANNING | FAILED | CANCELLED
PLANNING → RETRIEVING_CONTEXT | FAILED | CANCELLED
RETRIEVING_CONTEXT → CODING | FAILED | CANCELLED
CODING → VALIDATING_PATCH | FAILED | CANCELLED
VALIDATING_PATCH → APPLYING_PATCH | FAILED | CANCELLED
APPLYING_PATCH → TESTING | FAILED | CANCELLED
TESTING → REPAIRING | REVIEWING | FAILED | CANCELLED
REPAIRING → TESTING | REVIEWING | FAILED | CANCELLED
REVIEWING → QUALITY_GATE | FAILED | CANCELLED
QUALITY_GATE → COMPLETED | FAILED | CANCELLED
```

### Key Branching Logic

- **TESTING → REPAIRING**: Tests failed, enter bounded repair loop
- **TESTING → REVIEWING**: Tests passed, skip repair
- **REPAIRING → TESTING**: Re-test after repair attempt
- **REPAIRING → REVIEWING**: Max attempts reached or best-known state — proceed to review (which may reject)
- **QUALITY_GATE → COMPLETED**: Status set to APPROVED, REJECTED, or NEEDS_HUMAN_REVIEW based on gate decision

### Transition Validator

`RunStateMachine` class provides:

```python
RunStateMachine.can_transition(current, target) → bool
RunStateMachine.transition(current, target) → StageType  # raises TransitionError
RunStateMachine.next_stage(current) → StageType | None
RunStateMachine.is_terminal(stage) → bool
```

Invalid transitions raise `TransitionError` (subclass of `ValueError`). The orchestrator catches these and raises `DevPilotError`.

### Terminal States

Once a run reaches `APPROVED`, `REJECTED`, `NEEDS_HUMAN_REVIEW`, `FAILED`, or `CANCELLED`, normal orchestration stops. No further stages execute.

---

## 4. Run Result

`DevPilotRunResult` is the final output of every run:

| Field | Description |
|-------|-------------|
| `run_id` | Unique identifier |
| `status` | Final decision |
| `source` | Original task/issue |
| `repository` | Repository path/URL |
| `stages` | Summarized per-stage results |
| `events` | Sanitized event stream (max 50) |
| `requirements` | Structured requirements |
| `plan` | Implementation plan |
| `test_result` | Final test run result |
| `review_report` | Review report (if review ran) |
| `quality_gate` | Quality gate result (if gate ran) |
| `failure` | Failure info (if any) |
| `warnings` | Non-blocking warnings |
| `duration_seconds` | Total wall-clock time |

---

## 5. Orchestrator Architecture

### OrchestrationService

**Module**: `app/services/orchestration_service.py`

| Aspect | Detail |
|--------|--------|
| **Class** | `OrchestrationService` |
| **Entry point** | `execute_run(run_id, workspace_root)` |
| **Dependency injection** | All Phase 1–9 services received as optional constructor params |

```python
OrchestrationService(
    run_store: RunStore = InMemoryRunStore(),
    analysis_workflow: RepositoryAnalysisWorkflow = RepositoryAnalysisWorkflow(),
    github_service: GitHubService = GitHubService(),
    planning_service: PlanningService = PlanningService(),
    plan_validator: PlanValidator = PlanValidator(),
    index_builder: RepositoryIndexBuilder = RepositoryIndexBuilder(),
    coding_agent: CodingAgent = CodingAgent(),
    patch_validator: PatchValidator = PatchValidator(""),
    patch_engine: SafePatchEngine = SafePatchEngine(""),
    testing_service: TestingService = TestingService(),
    repair_service: RepairService = RepairService(),
    review_service: ReviewService = ReviewService(),
)
```

### Service Responsibilities

| Phase | Service | Used For |
|-------|---------|----------|
| 2 | `RepositoryAnalysisWorkflow` | Deterministic repo analysis |
| 3 | `GitHubService`, `RemoteRepositoryAnalyzer` | GitHub acquisition |
| 4 | `PlanningService`, `PlanValidator` | Task analysis, planning, validation |
| 5 | `RepositoryIndexBuilder`, `HybridRetriever`, `PlanContextRetriever` | Code-aware retrieval |
| 6 | `CodingAgent`, `PatchValidator`, `SafePatchEngine` | Code generation, validation, application |
| 7 | `TestingService` | Test discovery, execution, result parsing |
| 8 | `RepairService` | Bounded repair loop |
| 9 | `ReviewService`, `DeterministicReview`, `QualityGate` | Review, evidence validation, gate |

### What the Orchestrator Does NOT Do

| Action | Prohibited? | Detail |
|--------|------------|--------|
| Write source files directly | ✅ Prohibited | Only through SafePatchEngine |
| Execute arbitrary commands | ✅ Prohibited | Only through ControlledExecutionEngine |
| Bypass PatchValidator | ✅ Prohibited | Every patch validated |
| Bypass ExecutionPolicy | ✅ Prohibited | Every command checked |
| Override repair limits | ✅ Prohibited | Phase 8 owns attempt limits |
| Override QualityGate | ✅ Prohibited | Decision is deterministic |
| Modify original repository | ✅ Prohibited | Writable workspace only |

### Stage Methods

Each stage is implemented as an `async` method:

```python
async def _stage_planning(self, run, workspace) -> bool
async def _stage_coding(self, run, workspace) -> bool
async def _stage_testing(self, run, workspace, is_retest=False) -> Optional[bool]
```

Return semantics:
- `True` → stage succeeded, continue pipeline
- `False` → stage failed, run FAILED
- `None` → error/non-recoverable, run FAILED (used in testing/repair for env failures)

---

## 6. Run Registry

| Aspect | Detail |
|--------|--------|
| **Interface** | `RunStore` (Protocol) — `create`, `get`, `update`, `list`, `delete`, `request_cancel` |
| **Implementation** | `InMemoryRunStore` — thread-safe dict-based storage |
| **Persistence** | None (Phase 11 will add PostgreSQL/SQLite) |
| **Concurrency** | `threading.Lock` for thread safety in FastAPI workers |

### Limitations

- Data lost on process restart
- Not suitable for multi-process deployment
- No advanced query capabilities beyond status filter + pagination

---

## 7. Events

### Event Model

```python
RunEvent:
    event_id: str       # e.g. "evt-a1b2c3d4"
    run_id: str
    timestamp: str      # ISO 8601
    event_type: EventType
    stage: StageType
    message: str        # Max 200 chars in API
    metadata: dict
```

### Event Types

| Type | When |
|------|------|
| `RUN_CREATED` | Run initialized |
| `RUN_COMPLETED` | Run finished (approved/rejected) |
| `RUN_FAILED` | Run failed unexpectedly |
| `RUN_CANCELLED` | User cancelled |
| `STAGE_STARTED` | Stage begins |
| `STAGE_COMPLETED` | Stage succeeds |
| `STAGE_FAILED` | Stage fails |
| `STAGE_SKIPPED` | Stage skipped (guard condition) |
| `PATCH_GENERATED` | Coding agent produces patch |
| `PATCH_VALIDATED` | Patch passes validation |
| `PATCH_APPLIED` | Patch applied to workspace |
| `PATCH_REJECTED` | Patch rejected by validator |
| `TESTS_COMPLETED` | Test execution finished |
| `REPAIR_STARTED` | Repair loop entered |
| `REPAIR_COMPLETED` | Repair loop finished |
| `REVIEW_COMPLETED` | Review finished |
| `QUALITY_GATE_COMPLETED` | Gate decision made |
| `CANCELLATION_REQUESTED` | User requested cancel |

### Event Safety

Events are sanitized before API exposure:
- `message` truncated to 200 characters
- `metadata` excluded from API responses
- No secrets, API keys, or full source content

### Structured Logging

Every event is logged with the pattern:
```
Event run_id=<id> stage=<stage> type=<type> msg=<msg>
```

---

## 8. Cancellation

| Aspect | Detail |
|--------|--------|
| **Mode** | Cooperative — checked between stages |
| **API** | `POST /api/v1/runs/{run_id}/cancel` |
| **Behavior** | Sets `cancellation_requested = True`; `_check_cancelled()` checks at stage boundaries |
| **Limitations** | Cannot abort in-progress stage; only prevents subsequent stages |
| **Terminal check** | Cannot cancel runs already in terminal state |

---

## 9. Failure Handling

### Failure Model

```python
RunFailure:
    stage: StageType       # Where failure occurred
    code: FailureCode      # Machine-readable code
    message: str           # Human-readable (max 500 chars)
    recoverable: bool
    details: dict
```

### Failure Codes

| Code | Meaning |
|------|---------|
| `REPOSITORY_ACQUISITION_FAILED` | Git clone failed |
| `REPOSITORY_ANALYSIS_FAILED` | Repository analysis crashed |
| `TASK_ANALYSIS_FAILED` | LLM/task parsing failed |
| `PLANNING_FAILED` | Plan generation or validation failed |
| `RETRIEVAL_FAILED` | Code retrieval failed (non-fatal) |
| `CODING_FAILED` | Coding agent produced no patch |
| `PATCH_VALIDATION_FAILED` | Patch rejected by deterministic validator |
| `PATCH_APPLICATION_FAILED` | SafePatchEngine apply failed |
| `TEST_EXECUTION_FAILED` | Environment not ready for tests |
| `REPAIR_FAILED` | Unsafe repair or loop error |
| `REVIEW_FAILED` | Review pipeline failed |
| `QUALITY_GATE_FAILED` | Gate processing failed |
| `CANCELLED` | User requested cancellation |
| `UNKNOWN` | Unexpected error |

### Error Boundaries

Every stage has its own try/except block. If a stage raises an unhandled exception:
1. `RunFailure` is recorded with the exception message
2. Run transitions to `FAILED`
3. `RUN_FAILED` event is emitted
4. No further stages execute

No silent fallback — failed stages do not produce fabricated output.

---

## 10. Phase Integrations

### Phase 2/3: Repository Analysis & GitHub

| Service | Entry Point | When Called |
|---------|-------------|-------------|
| `RepositoryAnalysisWorkflow` | `.run(path)` | After acquisition, if `repository_path` is set |
| `RemoteRepositoryAnalyzer` | `.analyze(url)` | For GitHub issue sources |

Guard: `if run.source.repository_path and not run.repository_profile`

### Phase 4: Planning

| Service | Entry Point | When Called |
|---------|-------------|-------------|
| `PlanningService` | `.plan_from_task(title, description, repo_path)` | After analysis, if no `run.plan` |
| `PlanValidator` | `.validate(plan)` | After plan generation |

Guard: `if not run.plan`

### Phase 5: Retrieval

| Service | Entry Point | When Called |
|---------|-------------|-------------|
| `RepositoryIndexBuilder` | `.build(path)` | Before retrieval |
| `HybridRetriever` | `.retrieve(RetrievalQuery)` | If no plan steps |
| `PlanContextRetriever` | `.retrieve_for_plan(plan)` | If plan has steps |

Guard: `if not run.retrieved_context`

### Phase 6: Coding & Patches

| Service | Entry Point | When Called |
|---------|-------------|-------------|
| `CodingAgent` | `.run(CodingAgentInput)` | After retrieval, if no `run.patch_set` |
| `PatchValidator` | `.validate(patch, workspace_root)` | After coding |
| `SafePatchEngine` | `.apply(PatchSet)` | After validation |

Guard: `if not run.patch_set` for coding; always for validation

### Phase 7: Testing

| Service | Entry Point | When Called |
|---------|-------------|-------------|
| `TestingService` | `.discover_commands(path)`, `.build_plan(...)`, `.run_tests(plan)` | After patch application; may be called twice (initial + re-test) |

Branching:
- Tests pass → transition to REVIEWING (skip repair)
- Tests fail → transition to REPAIRING
- Environment not ready → run FAILED

### Phase 8: Repair

| Service | Entry Point | When Called |
|---------|-------------|-------------|
| `RepairService` | `.run_repair(workspace_root, workspace_id, test_result, patch_set, ...)` | If tests fail and repair is enabled |

After repair, re-test runs. If still failing, proceeds to review anyway (review may reject).

### Phase 9: Review & Quality Gate

| Service | Entry Point | When Called |
|---------|-------------|-------------|
| `ReviewService` | `.run_review(workspace_id, requirements, plan, ...)` | After testing/repair |
| `DeterministicReview` | `.run(ReviewInput)` | Within quality gate |
| `QualityGate` | `.decide(report, deterministic_result, test_result)` | Final stage |

Decision mapping:
| QualityGate Decision | RunStatus |
|---------------------|-----------|
| `APPROVED` | `APPROVED` |
| `REJECTED` | `REJECTED` |
| `NEEDS_HUMAN_REVIEW` | `NEEDS_HUMAN_REVIEW` |
| Other | `FAILED` |

---

## 11. API

| Method | Endpoint | Purpose | Input | Output |
|--------|----------|---------|-------|--------|
| POST | `/api/v1/runs` | Create and execute a run | `{source, title, description, repository}` | Final `DevPilotRunResult` |
| GET | `/api/v1/runs` | List runs | `?status=&limit=50&offset=0` | List of run summaries |
| GET | `/api/v1/runs/{run_id}` | Get run details | `run_id` | Full `DevPilotRun` |
| POST | `/api/v1/runs/{run_id}/cancel` | Cancel a run | `run_id` | Success message |
| GET | `/api/v1/runs/{run_id}/events` | Get run events | `run_id` | Sanitized event list |
| GET | `/api/v1/orchestration/capabilities` | List capabilities | — | `OrchestrationCapabilities` |

### API Security

- Runs accept only validated input (title/description/repository)
- No arbitrary stage injection from API clients
- Response sanitization: messages truncated (200 chars), secrets excluded
- Events limited to 50 items per response
- Warnings limited to 10 items

---

## 12. CLI

| Command | Purpose |
|---------|---------|
| `devpilot run <repository> --title <title> [--description <desc>]` | Execute end-to-end run |

Options:
- `--title` (required): Task title
- `--description`: Task description
- `--json`: Machine-readable JSON output

### Example Output

```
DevPilot Run: RUN-ABC12345

✓ Repository Analysis
✓ Task Analysis
✓ Planning
✓ Retrieval
✓ Coding
✓ Patch Validation
✓ Patch Application
✓ Testing
○ Repair — skipped
✓ Review
✓ Quality Gate

Decision: APPROVED
```

---

## 13. Frontend

### Runs Dashboard

**Page**: `/dashboard/runs` (list) and `/dashboard/runs/[id]` (detail)

| Component | List Page | Detail Page |
|-----------|-----------|-------------|
| Status badges | ✅ | ✅ |
| Stage progress bar | ✅ | ✅ (timeline) |
| Stats cards | ✅ (total/approved/rejected/running) | ✅ (stages/failed/skipped/events) |
| Decision banner | ❌ | ✅ (3 tiers) |
| Events log | ❌ | ✅ (expandable) |
| Cancel button | ❌ | ✅ (running only) |
| New Run modal | ✅ | ❌ |
| Auto-refresh | ✅ (5s, toggleable) | ✅ (3s, running only) |
| Capabilities strip | ✅ | ❌ |

### States

Both pages handle:
- **Loading**: Centered spinner
- **Error**: Error card with retry button
- **Empty**: Descriptive message with create action
- **Running**: Auto-refreshing with stage indicators

---

## 14. Security

| Check | Status | Detail |
|-------|--------|--------|
| Orchestrator direct source writes | ❌ NONE | All file writes through SafePatchEngine |
| Orchestrator arbitrary process execution | ❌ NONE | All subprocesses through ControlledExecutionEngine |
| PatchValidator bypass | ❌ NONE | Every patch validated deterministically |
| ExecutionPolicy bypass | ❌ NONE | Every command validated |
| Repair-limit bypass | ❌ NONE | Phase 8 owns attempt counting |
| QualityGate override | ❌ NONE | Decision mapping is deterministic |
| Original repository mutation | ❌ NONE | Only writable workspace is modified |
| Secret exposure | ❌ NONE | Events/API responses sanitized |
| Prompt injection weakening | ❌ NONE | No new orchestrator LLM prompts introduced |

### Original Repository Immutability

The orchestrator must never pass the original repository path to a mutation API. Only the writable workspace is passed to `SafePatchEngine`, `TestingService`, and `RepairService`.

---

## 15. Tests

**Module**: `tests/test_orchestration.py`
**Tests**: 50

| Test Class | Tests | Description |
|------------|-------|-------------|
| `TestRunStateMachine` | 10 | Valid/invalid transitions, terminal states, next_stage, is_terminal, map completeness |
| `TestHappyPath` | 6 | Full pipeline → APPROVED, create_run with/without repo, not found, capabilities |
| `TestRepairPath` | 2 | Fail → repair → re-test → APPROVED; max attempts → proceed to review |
| `TestRejectionPath` | 1 | Quality gate rejects → REJECTED (not FAILED) |
| `TestCancellation` | 4 | Cancel running/terminal/nonexistent, _check_cancelled |
| `TestFailureBoundaries` | 4 | Planning fails→no coding, coding fails→no patch, validation fails→no apply, env→no repair |
| `TestSecurity` | 6 | No subprocess, no direct file writes, delegation, event redaction |
| `TestRunStore` | 7 | CRUD, list, filter, pagination, ID generation |
| `TestEvents` | 3 | Creation, sanitization, empty run |
| `TestDecisionMapping` | 4 | APPROVED, REJECTED (≠ FAILED), NEEDS_HUMAN_REVIEW |
| `TestTransitionMatrix` | 4 | Expected transitions, no duplicates, linearity, no skipping |

### Test Architecture

- **Deterministic and mocked** — no live API keys, no network calls
- **`_prepare_run` helper** — pre-populates guarded fields for focused stage testing
- **`@patch.object` decorators** — mock stage methods on the class for flow testing
- **Instance-level patches** — used in failure boundary tests for fine-grained control
- **`_mock_stage` / `_mock_approve` helpers** — advance `run.current_stage` so state machine transitions work

---

## 16. Known Limitations

1. **In-memory run storage** — data lost on process restart (Phase 11 will add persistence)
2. **Cooperative cancellation** — cannot abort in-progress stage
3. **Synchronous pipeline** — stages execute sequentially, no parallel execution
4. **No persistent database storage yet** — runs, events, and results are in-memory only (Phase 11 will add PostgreSQL-backed `PostgresRunStore`)
5. **LLM-dependent stages** — planning, coding, and review depend on LLM provider availability
6. **No distributed execution** — single-process only
7. **No resumption** — cancelled/failed runs cannot be resumed (design prepared for Phase 11)

---

## 17. Phase 11 Contract

### Prerequisites — Already Complete ✅

| Component | Status | Details |
|-----------|--------|---------|
| PostgreSQL 18.4 | ✅ Installed & running | `localhost:5432`, `devpilot_dev` + `devpilot_test` databases |
| SQLAlchemy 2.x + asyncpg | ✅ Installed | Async engine with connection pooling (size=5, overflow=10, pre-ping) |
| `app/db/database.py` | ✅ Ready | Engine creation, pool, verification, redaction |
| FastAPI lifecycle | ✅ Integrated | Engine init on startup, safe dispose on shutdown |
| Secret redaction | ✅ Tested | Passwords redacted from all output channels (logs, API, CLI, errors) |
| CLI diagnostic | ✅ Ready | `python -m app.cli db-check` with redacted output |
| Health check | ✅ Ready | `GET /health` — sanitized database status |
| `.env` configuration | ✅ Ready | `DATABASE_URL` and `TEST_DATABASE_URL` patterns |
| `pytest.mark.integration` marker | ✅ Registered | For PostgreSQL-dependent tests |
| `docs/DATABASE.md` | ✅ Complete | Full setup guide, architecture, testing, security |
| Unit tests (mocked) | ✅ Passed | 22 tests — no PostgreSQL required |
| Integration tests (live) | ✅ Passed | 7 tests — SELECT 1, version, name, separation, lifecycle |

### Storage Candidates

| Entity | Current Interface | Recommended Storage |
|--------|------------------|-------------------|
| `DevPilotRun` | `RunStore` (Protocol) | PostgreSQL table (`runs`) |
| `RunEvent` | In-memory list on `DevPilotRun` | PostgreSQL table (`run_events`) |
| `StageResult` | In-memory list on `DevPilotRun` | PostgreSQL table (`stage_results`) or JSONB |
| `DevPilotRunResult` | Constructed on-demand | Computed from persisted run + events |

### Interface to Replace

```python
class RunStore(Protocol):
    def create(self, run: DevPilotRun) -> DevPilotRun
    def get(self, run_id: str) -> Optional[DevPilotRun]
    def update(self, run: DevPilotRun) -> DevPilotRun
    def list(self, status: Optional[RunStatus] = None, limit: int = 50, offset: int = 0) -> list[DevPilotRun]
    def delete(self, run_id: str) -> bool
    def request_cancel(self, run_id: str) -> bool
```

### Recommended Implementation Order

1. Create Alembic migration for initial schema (runs, run_events, stage_results tables)
2. Implement `PostgresRunStore` — runs CRUD + cancellation
3. Implement `PostgresEventStore` — event append + list
4. Wire into existing FastAPI lifespan (reuse existing engine)
5. Run full regression: all 653 tests should pass
6. Update `.env.example` if new config vars are needed

### Testability

The `RunStore` protocol interface makes it straightforward to:
- Swap `InMemoryRunStore` with `PostgresRunStore`
- Inject store into `OrchestrationService` via constructor
- Run the same tests against in-memory and PostgreSQL implementations
- All existing unit tests (mocked) remain independent
- Add `@pytest.mark.integration` tests for PostgreSQL-specific behavior

---

## 18. Phase 10 Architecture Diagram

```
                        DevPilot

                           │
                ┌──────────┴──────────┐
                ▼                     ▼
             User Task           GitHub Issue
                │                     │
                └──────────┬──────────┘
                           ▼
                    DevPilotRun
                           │
                           ▼
                  OrchestrationService
                           │
                           ▼
                 Repository Analysis
                           │
                           ▼
                    Task Analysis
                           │
                           ▼
                       Planning
                           │
                           ▼
                   Code Retrieval
                           │
                           ▼
                     CodingAgent
                           │
                           ▼
                       PatchSet
                           │
                           ▼
                    PatchValidator
                           │
                           ▼
                      PatchEngine
                           │
                           ▼
                       TestAgent
                           │
                    ┌──────┴──────┐
                    │             │
                  PASS           FAIL
                    │             │
                    │             ▼
                    │          FixAgent
                    │             │
                    │        bounded repair
                    │             │
                    └──────┬──────┘
                           ▼
                     ReviewService
                           │
                           ▼
                     ReviewerAgent
                           │
                           ▼
                  EvidenceValidator
                           │
                           ▼
                      QualityGate
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
          APPROVED      REJECTED       NEEDS
                                      HUMAN
                                      REVIEW

                           │
                           ▼
                     DevPilotRunResult


           Supporting Infrastructure:

           RunStateMachine     RunStore       RunEvent[]
           (deterministic)    (in-memory)    (observability)

           Cancellation       StageResult[]  Failure boundaries
           (cooperative)      (per-stage)    (per-stage catches)
```

---

## Phase 15 — Multi-Agent Collaboration

> **Status**: Complete ✅

Phase 15 keeps `OrchestrationService` as the sole coordinator while adding a
**structured collaboration layer** between stages. Agents never invoke each
other directly — the orchestrator creates handoffs, records decisions, detects
conflicts, and promotes memory at stage boundaries:

```text
Orchestrator
     │
     ├── build context (ContextEngine + selected handoffs)
     ├── invoke agent
     ├── validate output
     ├── create handoff (_create_handoff)
     ├── record decision (_record_decision)
     ├── detect conflicts (_detect_handoff_conflicts)
     └── transition stage
```

Handoffs created:

```text
Planner → Coding      Coding → Testing      Testing → Repair (on failure)
Repair  → Testing     Testing → Reviewer    Reviewer → Quality Gate
```

Each boundary also emits WebSocket events (`HANDOFF_CREATED`,
`DECISION_RECORDED`, `CONFLICT_DETECTED`, `MEMORY_PROMOTED`). All
collaboration calls are **never-fatal** — the pipeline degrades gracefully to
pre-Phase 15 behavior when the CollaborationService or DB is unavailable.

See [`docs/MULTI_AGENT_COLLABORATION.md`](MULTI_AGENT_COLLABORATION.md).

---

## Phase 16 — Autonomous Execution, Goal Tracking & Safe Termination

> **Status**: Complete ✅

Phase 16 wraps the orchestrated pipeline in an **autonomous execution loop** —
a goal is tracked through deterministic decisions, plans are versioned on
replan, and the run terminates safely when budgets or criteria are exhausted:

```text
AutonomousExecutionController.start(goal_id)
     │
     ├── _decide(state)  ── deterministic, order-fixed:
     │       stuck? → PROGRESSING/CONTINUE
     │       all criteria satisfied + gate approved → COMPLETE (STOP)
     │       tests failing + repair budget → REPAIR
     │       + replan budget → REPLAN (versioned plan)
     │       else → ESCALATE (WAITING_FOR_HUMAN) / STOP
     ├── run one iteration (agent pipeline via _iteration_runner)
     ├── record evidence + plan version + checkpoint
     ├── BudgetManager: iterations/calls/time/files tracked
     └── loop until terminal decision, global-limit exhaustion,
         or cooperative cancel
```

Key guarantees:

- **Termination** — `max_iterations >= 1` is the hard bound; every non-terminal
  iteration increments `iterations_used`. Repair/replan limits are *routed* by
  `_decide` (REPAIR → REPLAN → ESCALATE), never fatal.
- **Determinism** — `_decide` order is fixed; the same evidence history always
  produces the same decision (fully testable).
- **Recovery** — `recover()` reconstructs a goal from persisted checkpoints;
  version conflicts on concurrent writes raise and are surfaced.
- **Safe termination** — COMPLETED / FAILED / CANCELLED / WAITING_FOR_HUMAN
  with a persisted reason code (`budget_exhausted`, `max_iterations`, etc.).
- **Human escalation** — WAITING_FOR_HUMAN goals resume via `provide_input()`.

Persistence is migration **007** (execution_goals, plan_versions,
autonomous_decisions, execution_checkpoints, human_escalations).

See [`docs/AUTONOMOUS_EXECUTION.md`](AUTONOMOUS_EXECUTION.md).

---

## Phase 17 — Collaborative Reasoning & Evidence Consensus

> **Status**: Complete ✅

Phase 17 builds the **reasoning layer above the collaboration store**: at run
completion the orchestrator asks the `CollaborativeReasoningEngine` to decide
whether the shared evidence AGREES. Where Phase 15 recorded *what* agents
produced/shared, Phase 17 interprets it:

```text
CollaborationService        = records WHAT agents produced / shared
CollaborativeReasoningEngine = decides whether the evidence AGREES
```

Pipeline (one call, never raises):

```text
_run_completed
     │
     ├── _get_reasoning() — lazy engine sharing the collaboration service
     ├── collect_evidence(run)   — planner/coding/testing/repair/reviewer/
     │                             graph/memory/gate buckets
     ├── compute_confidence()    — weighted authority; claim-only evidence
     │                             is capped below HIGH (0.49 < 0.75)
     ├── detect_contradictions() — claim-vs-test (coding/testing/repair
     │                             handoffs), claim-vs-gate, scope-vs-impact
     ├── build_consensus()       — per-topic AGREED / CONFLICTED / UNKNOWN
     ├── build_notebook()        — accepted/rejected decisions, conflicts,
     │                             consensus, timeline (bounded)
     └── events: CONSENSUS_BUILT / CONFLICT_DETECTED
```

Key guarantees:

- **Deterministic-outranks-claims** — an unsupported LLM claim can never flip
  a consensus; claim-only mixes are capped below `HIGH`.
- **Evidence-only exposure** — API/CLI/frontend surface consensus, confidence,
  and decisions; chain-of-thought is never exposed.
- **Bounded everywhere** — 50 consensus / 50 contradictions / 200 timeline
  entries / 20 evidence refs per record.
- **Recovery** — `recover(run_id)` rehydrates consensus, contradictions, and
  the notebook from PostgreSQL after a restart.
- **Orchestrator integration** — the reviewer agent context now carries
  shared-consensus notes (evidence-only) before producing its verdict;
  autonomy REPLAN rationale is enriched with consensus topics.

Persistence is migration **010** (evidence_consensus, contradiction_records,
engineering_notebooks — JSONB payloads).

See [`docs/COLLABORATIVE_REASONING.md`](COLLABORATIVE_REASONING.md).

---

## Phase 18 — Engineering Knowledge Graph

> **Status**: Complete ✅

Phase 18 adds a **unified, temporal knowledge layer** above every store the
orchestrator produces. On run completion the orchestrator ingests the run into
`EngineeringKnowledgeGraphService.record_run(run, reasoning_outcome)`, which
links goals → plans → patches → tests → review → quality gate → notebook →
consensus → repository memory as typed NODES and provenance-bearing EDGES.

```text
run_completed
     │
     ├── _build_reasoning() — CollaborativeReasoningEngine outcome
     ├── _ingest_into_graph(run, outcome) — record_run():
     │     RUN ──references──▶ REPOSITORY
     │     RUN ──contains──▶ REQUIREMENT(s)
     │     RUN ──created_during──▶ PATCH ──modifies──▶ FILE(s)
     │     PATCH ──validated_by──▶ TEST_SUITE
     │     PATCH ──approved_by──▶ QUALITY_GATE
     │     RUN ──produced_by──▶ CONSENSUS / CONTRADICTION / NOTEBOOK
     └── graph version bump (incremental — never a full rebuild)
```

Key guarantees:

- **Incremental versioning** — each run/change bumps the graph version and
  records WHICH nodes/edges changed; superseded nodes are kept for history.
- **Planner-driven retrieval** — `KnowledgeQueryPlanner` classifies queries
  (explain, affected tests, historical fixes, engineering history, notebook,
  quality evidence, …) and selects the minimal strategy — no blind search.
- **Provenance preserved** — every node retains its evidence origins; nothing
  is exposed beyond verified engineering evidence (never chain-of-thought).
- **Idempotent ingestion** — re-ingesting the same run upserts and dedups.
- **PostgreSQL persistence** — migration **011** (ekg_nodes, ekg_edges,
  ekg_versions) with graceful in-memory fallback and restart recovery.
- **Integrations** — ContextEngine queries the EKG for graph-aware agent
  context; the autonomy controller uses graph evidence for REPLAN rationale;
  the reasoning engine syncs consensus/contradictions/notebook into the graph.

See [`docs/ENGINEERING_KNOWLEDGE_GRAPH.md`](ENGINEERING_KNOWLEDGE_GRAPH.md).
