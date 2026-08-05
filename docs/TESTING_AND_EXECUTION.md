# Test Agent & Controlled Execution Engine — Phase 7

> Phase 7 answers: *"Did the code produced in Phase 6 pass the repository's relevant verification commands, and what structured evidence should the next agent receive if it did not?"*

## Architecture

```
                    PHASE 6

             Modified Workspace
                     +
          PatchApplicationResult
                     │
                     ▼
              ┌──────────────┐
              │  Test Agent  │
              └──────┬───────┘
                     │
           ┌─────────┴────────────┐
           ▼                      ▼
   Deterministic            LLM-powered
   (use_llm=False)          (use_llm=True)
   Rule-based plan           Prompt → LLM
                               ↓
                           JSON response
                               ↓
                        Candidate validation
                               ↓
                    Fallback on failure → deterministic
           │                      │
           └──────────┬───────────┘
                      ▼
               ExecutionPlan
                      │
                      ▼
            ┌───────────────────┐
            │ Execution Policy  │
            └─────────┬─────────┘
                      │
               ALLOW / REJECT
                      │
                      ▼
        ┌──────────────────────────┐
        │ Controlled Execution    │
        │ Engine                  │
        └────────────┬─────────────┘
                     │
                     ▼
           ProcessExecutionResult
                     │
                     ▼
             Test Result Parser
                     │
                     ▼
                TestRunResult
                     │
                     ▼
           Structured Failures
                     │
                     ▼
       Frontend Dashboard
       /dashboard/testing
```

### Key Invariant

> An agent may decide what should be verified, but deterministic security policy decides what may execute.

---

## Components

### 1. Models (`app/models/testing.py`)

| Model | Purpose |
|-------|---------|
| `ExecutionStatus` | Enum: PASSED, FAILED, TIMEOUT, REJECTED, ERROR, SKIPPED, ENVIRONMENT_NOT_READY |
| `CommandCategory` | Enum: TEST, LINT, TYPECHECK, BUILD, OTHER |
| `CommandSource` | Enum: PYPROJECT, PACKAGE_JSON, CONFIG, PHASE2_DETECTION, DEFAULT_FRAMEWORK_RULE, USER_APPROVED |
| `CommandCandidate` | A discovered command with provenance (executable, arguments, source, confidence) |
| `ExecutionStep` | A single step in an execution plan (executable, arguments, cwd, timeout) |
| `ExecutionPlan` | A validated plan — bounded set of steps with workspace context |
| `ProcessExecutionResult` | Raw process result (stdout, stderr, exit code, timeout info) |
| `FailureCategory` | Enum: ASSERTION_FAILURE, IMPORT_ERROR, SYNTAX_ERROR, TYPE_ERROR, BUILD_FAILURE, LINT_FAILURE, TIMEOUT, DEPENDENCY_ERROR, CONFIGURATION_ERROR, EXECUTION_ERROR, UNKNOWN |
| `TestFailure` | Normalized failure record with framework, test name, file, line, message, type |
| `TestRunResult` | Complete normalized result with counts, failures, process results |

### 2. Execution Policy (`app/services/execution_policy.py`)

Deterministic security gate. Answers: *"Is this execution step permitted?"*

**Allowed executables (conservative default):**
- `python`, `python3`, `pytest`
- `node`, `npm`, `npx`, `pnpm`, `yarn`
- `make`

**Always blocked:**
- `powershell`, `pwsh`, `cmd`, `bash`, `sh`, `zsh`, `fish`
- `curl`, `wget`, `ssh`, `scp`, `sftp`
- `sudo`, `su`, `docker`, `podman`, `kubectl`

**Validation checks:**
1. Category filtering (BUILD/LINT/TYPECHECK disabled by default)
2. Executable allowlist
3. Working directory safety (must be inside workspace root)
4. Argument safety (no shell metacharacters, no dangerous patterns)
5. Package script content inspection (rejects scripts with dangerous content)
6. Command count limits

### 3. Controlled Execution Engine (`app/services/controlled_execution_engine.py`)

Safe, bounded subprocess execution:
- **Process API**: `asyncio.create_subprocess_exec()` — argument arrays, no `shell=True`
- **Environment sanitization**: Only safe env vars passed (PATH, SYSTEMROOT, TEMP, etc.)
- **Secret isolation**: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GITHUB_TOKEN` NEVER passed
- **Timeout**: Per-command timeout with process tree termination
- **Output limits**: Configurable max stdout/stderr bytes (truncated when exceeded)
- **Best-effort process cleanup**: SIGTERM → SIGKILL → wait

### 4. Result Parsers (`app/testing/parsers/`)

| Parser | Framework | Features |
|--------|-----------|----------|
| `PytestResultParser` | pytest | Test counts, failure extraction, classification |
| `GenericResultParser` | Fallback | Exit code, stdout, stderr, basic classification |

### 5. Testing Service (`app/services/testing_service.py`)

Orchestrator coordinating the full pipeline:
1. Command discovery (from pyproject.toml, pytest.ini, package.json)
2. Execution plan building (deterministic, deduplicated, ordered)
3. Policy validation
4. Controlled execution
5. Result parsing and normalization

### 6. Test Agent (`app/agents/test_agent.py`)

Reasons about what verification is relevant and produces an `ExecutionPlan`:

**Two operation modes:**

| Mode | Flag | Behavior |
|------|------|----------|
| **Deterministic** (default) | `use_llm=False` | Pure rule-based: discards low-confidence candidates (<0.4), deduplicates by executable+args, orders by category priority. No LLM required. |
| **LLM-powered** | `use_llm=True` | Uses `BaseLLMProvider` (same abstraction as CodingAgent) to analyze workspace context, changed files, and discovered candidates. LLM produces structured JSON selecting/ordering commands with reasoning. |

**LLM integration details:**
- Prompt includes workspace summary, discovered candidates (marked `[UNTRUSTED REPOSITORY CONTENT]`), changed files, and optional patch results
- LLM selects commands via JSON output with `executable`, `arguments`, `category`, `reason`, `priority`, and `timeout_seconds` fields
- **Candidate validation**: All LLM-suggested commands are validated against the discovered candidate list — invented commands that don't match are rejected with a warning, preventing arbitrary command generation
- **Three-tier fallback**: (1) LLM provider unavailable → deterministic, (2) LLM call fails → deterministic, (3) LLM output unparseable → deterministic. Graceful degradation at every level.
- **Prompt security**: Repository file content is marked as `[UNTRUSTED REPOSITORY CONTENT]` in the prompt — the LLM is instructed not to follow instructions embedded in source files

**Phase 6 integration:**
- `plan_from_patch()` consumes `PatchApplicationResult` for changed-file-aware test selection
- Works in both deterministic and LLM modes

### 7. Testing Workflow (`app/workflows/testing.py`)

Linear graph: validate_workspace → discover_commands → build_plan → validate_policy → execute → normalize → END

### 8. API (`app/api/v1/testing.py`)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v1/testing/plan` | POST | Create execution plan (no execution) |
| `/api/v1/testing/plan-from-patch` | POST | Create plan from Phase 6 patch |
| `/api/v1/testing/run` | POST | Execute a plan (controlled execution) |
| `/api/v1/testing/capabilities` | GET | List testing capabilities |

### 9. CLI

| Command | Purpose |
|---------|---------|
| `python -m app.cli test-plan --workspace PATH` | Create a test plan (inspect only) |
| `python -m app.cli test --workspace PATH` | Execute tests with controlled execution |

---

## Frontend Dashboard

Phase 7 includes a testing dashboard at `/dashboard/testing` with the following views:

| View | Description |
|------|-------------|
| **Workspace Input** | Enter workspace ID, root path, and changed files (one per line from Phase 6 patch) |
| **Plan Builder** | Discover commands and build execution plan with step timeline, category badges, reasoning, and warnings |
| **Execution** | Run the plan with loading state showing policy validation, workspace isolation, and environment sanitization status |
| **Results Summary** | Tabbed view: **Summary** (run metadata), **Processes** (expandable stdout/stderr/exit codes per command), **Failures** (expandable cards with stack traces, classification badges, file paths) |
| **Capabilities Strip** | Always-visible bar showing max commands, timeout, environment sanitization, workspace isolation, and LLM requirement status |

**State handling:** Empty state, planning loading, plan created (with reasoning + warnings), executing, results with failures, re-run support.

**Backend alignment:** All TypeScript types match `app/models/testing.py` models — ready for live API connection.

---

## Command Discovery

Phase 7 reuses Phase 2 intelligence patterns. Detected configurations:

| Configuration | Commands | Confidence |
|---------------|----------|------------|
| `pyproject.toml` with `[tool.pytest]` | `python -m pytest -q` | HIGH (0.9) |
| `pytest.ini` | `python -m pytest -q` | HIGH (0.9) |
| `setup.cfg` with `[tool:pytest]` | `python -m pytest -q` | HIGH (0.8) |
| `package.json` with `test` script | `npm test` | HIGH (0.9) |
| `package.json` with `test:*` scripts | `npm run test:*` | MEDIUM (0.8) |
| Python project (default) | `python -m pytest -q` | MEDIUM (0.5) |

---

## Security

### What Phase 7 Protects

| Protection | Implementation |
|------------|---------------|
| Workspace path boundary | Resolved working directory must be inside workspace root |
| Argument-array execution | `asyncio.create_subprocess_exec(*cmd)`, no `shell=True` |
| Command policy | Executable allowlist, blocked executables, dangerous pattern detection |
| Secret environment filtering | Only safe env vars passed (PATH, TEMP, USERPROFILE); secrets blocked |
| Timeouts | Per-command timeout with process tree termination |
| Bounded output | Configurable max stdout/stderr bytes |
| Process cleanup | SIGTERM → SIGKILL → wait, best-effort on all platforms |
| Original repository isolation | Services never write to source repositories |

### What Phase 7 Does NOT Guarantee

| Area | Status | Notes |
|------|--------|-------|
| OS-level filesystem isolation | ❌ Deferred | Requires proper sandbox/container |
| Network namespace isolation | ❌ Deferred | Phase 7 defaults to no network tools but doesn't enforce network sandboxing |
| Kernel isolation | ❌ Deferred | Requires container runtime |
| Container isolation | ❌ Deferred | Not implemented — controlled subprocess execution only |
| Strict CPU/memory quotas | ❌ Deferred | Not implemented |

---

## Framework Support

| Framework | Parser | Test Counts | Failure Names | Failure Types | Classification |
|-----------|--------|-------------|---------------|---------------|----------------|
| **pytest** | Dedicated `PytestResultParser` | ✅ `X passed / Y failed / Z skipped` | ✅ Fully qualified test names | ✅ File, line number, message | ✅ ASSERTION, IMPORT, SYNTAX, TIMEOUT, etc. |
| **unittest** | Dedicated `UnittestXMLParser` (JUnit XML from `xmlrunner` / `unittest-xml-reporting`) | ✅ `tests`/`failures`/`errors`/`skipped` attrs | ✅ `module.Class.test_name` + file path heuristic | ✅ Failure/error message, `type`, traceback, line number | ✅ `classify_message` on message + type + traceback |
| **Vitest** | Dedicated `VitestJsonParser` (`--reporter=json`) | ✅ `numTotalTests` / `numPassedTests` / `numFailedTests` / `numPendingTests` | ✅ `fullName` + ancestor titles | ✅ File (suite `name`), line number, failure messages | ✅ `classify_message` on first failure message |
| **Jest** | Dedicated `JestJsonParser` (`--json`) | ✅ `numTotalTests` / `numPassedTests` / `numFailedTests` / `numPendingTests` | ✅ `fullName` + ancestor titles | ✅ File (suite `name`), line number, `failureMessages` + stack | ✅ `classify_message` on first failure message |
| **Generic** | `GenericResultParser` | ❌ Not parsed | ⚠️ Raw stdout/stderr | ⚠️ Raw stdout/stderr | ⚠️ Exit code + stderr heuristics |

> **Note:** Phase 7 has dedicated parsers for **pytest**, **unittest** (JUnit XML),
> **Vitest** (JSON) and **Jest** (JSON). Vitest and Jest JSON output share the same
> top-level shape; they are discriminated by the `perfStats` key that Jest suites
> always carry (Vitest never does). Unknown frameworks fall back to a generic
> parser that preserves raw stdout/stderr and classifies by exit code.

---

## Phase 6 Integration

Phase 7 consumes Phase 6 outputs:
- `CodingWorkspace` — workspace root for test execution
- `PatchApplicationResult` — changed files for targeted test selection
- `FileChange` paths — used for finding related tests

Phase 7 never modifies Phase 6 workspace files (planning is read-only; execution may create `.pytest_cache`, coverage, and build artifacts through approved commands).

## Phase 8 Integration

> **Phase 8 (Fix Agent + Bounded Repair Loop) is now COMPLETE ✅**

Phase 8 consumes Phase 7's `TestRunResult` and `TestFailure[]` outputs:

| Model | Path | Consumed By |
|-------|------|-------------|
| `TestRunResult` | `app.models.testing` | `FailureDiagnosisService.diagnose()` |
| `TestFailure[]` | `app.models.testing` | `FixAgent` diagnosis input |
| `ProcessExecutionResult[]` | `app.models.testing` | Infrastructure failure detection |

**Phase 8 key entry points consuming Phase 7 output:**
- `RepairService.run_repair()` — accepts `TestRunResult` with failures
- `FailureDiagnosisService.diagnose()` — maps failures to `FailureDiagnosis`
- `FixAgent.execute()` — generates repair proposals from failure evidence
- `TestingService.run_tests()` — re-run after repair for verification

**Key files:** `app/services/repair_service.py`, `app/services/failure_diagnosis_service.py`, `app/agents/fix_agent.py`, `app/prompts/fixing.py`, `app/models/repair.py`

**Documentation:** `docs/REPAIR_AND_RECOVERY.md`

---

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `TEST_DEFAULT_TIMEOUT` | 60 | Per-command timeout (seconds) |
| `TEST_MAX_OUTPUT_BYTES` | 1,048,576 | Max captured stdout/stderr per process |
| `TEST_MAX_COMMANDS` | 10 | Max commands per test run |
| `TEST_ALLOW_BUILD` | False | Allow build commands |
| `TEST_ALLOW_LINT` | False | Allow lint commands |
| `TEST_ALLOW_TYPECHECK` | False | Allow typecheck commands |

---

## Limitations

1. **No automatic dependency installation** — missing dependencies return `ENVIRONMENT_NOT_READY`
2. **No OS-level sandbox** — controlled subprocess execution, not container isolation
3. **No network isolation** — Phase 7 doesn't run network tools but doesn't enforce network sandboxing
4. **No full process tree cleanup on Windows** — best-effort only (`taskkill` not implemented)
5. **LLM quality dependency** — When `use_llm=True`, the quality of test selection depends on LLM capability. The fallback to deterministic mode is safe (all commands still validated by ExecutionPolicy) but may miss optimization opportunities.
6. **Prompt injection surface** — Repository file content is marked as untrusted in the LLM prompt, but comprehensive prompt injection hardening is a continuous process
7. **Limited test selection heuristics** — simple filename pattern matching for related tests; no coverage-based or dependency-graph-based test selection
8. **In-memory workspace registry** — no persistence (Phase 11 will add this)
