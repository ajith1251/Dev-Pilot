# Test Agent Architecture

> **Phase 7** · Stand-alone architecture documentation for the **Test Agent** and its supporting services.

## Overview

The Test Agent is responsible for determining *what should be verified* when a code change (Phase 6 patch) has been applied to a workspace. It produces a structured **ExecutionPlan** that is validated by the **Execution Policy** and executed by the **Controlled Execution Engine**.

### Key Invariant

> An agent may decide what should be verified, but deterministic security policy decides what may execute.

## High-Level Architecture

```
                    PHASE 6 OUTPUT
                         │
              Modified Workspace
              + PatchApplicationResult
                         │
                         ▼
        ┌─────────────────────────────────┐
        │         TEST AGENT              │
        │  (Reasoning authority only)     │
        │                                 │
        │  ┌──────────────────────────┐   │
        │  │  Deterministic (default) │   │
        │  │  · Rule-based selection  │   │
        │  │  · No LLM required      │   │
        │  │  · Fast & predictable   │   │
        │  └──────────┬───────────────┘   │
        │             │                   │
        │  ┌──────────▼───────────────┐   │
        │  │  LLM-powered (optional)  │   │
        │  │  · Smart test selection  │   │
        │  │  · Change-aware ordering │   │
        │  │  · Three-tier fallback   │   │
        │  └──────────┬───────────────┘   │
        └─────────────┼───────────────────┘
                      │
                      ▼
               ExecutionPlan
                      │
                      ▼
        ┌─────────────────────────────────┐
        │      EXECUTION POLICY           │
        │  (100% deterministic gate)      │
        │                                 │
        │  · Executable allowlist         │
        │  · Argument validation          │
        │  · Package script inspection    │
        │  · Working directory safety     │
        │  · ALLOW / REJECT               │
        └──────────────────┬──────────────┘
                           │ (REJECTED → STOP)
                           │ (ALLOWED → continue)
                           ▼
        ┌─────────────────────────────────┐
        │   CONTROLLED EXECUTION ENGINE   │
        │  (Safe subprocess execution)    │
        │                                 │
        │  · asyncio.create_subprocess_exec│
        │  · No shell=True                │
        │  · Environment sanitized        │
        │  · Timeout enforced             │
        │  · Output limits applied        │
        └──────────────────┬──────────────┘
                           │
                           ▼
        ┌─────────────────────────────────┐
        │      RESULT PARSERS            │
        │                                 │
        │  · PytestResultParser (full)    │
        │  · GenericResultParser (fallback)│
        └──────────────────┬──────────────┘
                           │
                           ▼
        ┌─────────────────────────────────┐
        │         TEST RUN RESULT         │
        │  · TestRunResult                │
        │  · TestFailure[]                │
        │  · ProcessExecutionResult[]     │
        │                                 │
        │  → Consumed by Phase 8          │
        │    (Fix Agent)                  │
        └─────────────────────────────────┘
```

## Agent Input / Output

### TestAgentInput

| Field | Type | Description | Source |
|-------|------|-------------|--------|
| `workspace_id` | `str` | Workspace identifier | Phase 6 |
| `workspace_root` | `str` | Absolute path to workspace | Phase 6 |
| `candidates` | `List[CommandCandidate]` | Discovered commands | TestingService |
| `changed_files` | `List[str]` | Files modified by patch | PatchApplicationResult |
| `patch_result` | `PatchApplicationResult` | Full patch result (optional) | Phase 6 |
| `repository_language` | `Optional[str]` | Primary language | Phase 2 |
| `repository_frameworks` | `Optional[List[str]]` | Detected frameworks | Phase 2 |
| `extra_context` | `Dict[str, Any]` | Additional context | Caller |

### TestAgentOutput

| Field | Type | Description |
|-------|------|-------------|
| `plan` | `ExecutionPlan` | Validated execution plan |
| `reasoning` | `str` | Explanation of command selections |
| `warnings` | `List[str]` | Issues encountered during planning |

## Components

### 1. Test Agent (`app/agents/test_agent.py`)

The core agent extends `BaseAgent[TestAgentInput, TestAgentOutput]`. It has **two operating modes**:

```
TestAgent.execute(inp)
    │
    ├── use_llm=True AND provider available?
    │   YES → _execute_with_llm(inp)
    │            │
    │            ├── Build prompt with workspace context
    │            ├── Call LLM → parse JSON response
    │            ├── Validate candidates against discovered list
    │            ├── Any invented commands? → reject + warn
    │            ├── Merge LLM selection with deterministic ordering
    │            └── Return plan + reasoning + warnings
    │
    NO  → _execute_deterministic(inp)
    │            │
    │            ├── Use candidates from input or discover from workspace
    │            ├── Filter: confidence >= 0.4
    │            ├── Deduplicate: same executable+arguments
    │            ├── Order by category priority (test → lint → typecheck → build)
    │            └── Return plan + reasoning + warnings
    │
    └── Fallback chain (for LLM mode):
        1. Provider unavailable → deterministic
        2. LLM call fails → deterministic  
        3. Output unparseable → deterministic
```

#### Deterministic Mode (`use_llm=False`)

Pure rule-based command selection:

| Step | Rule | Description |
|------|------|-------------|
| 1 | Confidence filter | Discard candidates with `confidence < 0.4` |
| 2 | Deduplication | Remove duplicate `executable + arguments` combinations |
| 3 | Priority ordering | `test > lint > typecheck > build` |
| 4 | Required-first | `required=True` steps come before optional ones |
| 5 | In-request ordering | Within same priority, preserve original order |

No LLM required. Deterministic and fast. Suitable for standard projects with well-known frameworks.

#### LLM-Powered Mode (`use_llm=True`)

Uses the provider-independent `BaseLLMProvider` to analyze workspace context and make smarter decisions.

**Prompt structure** (from `app/prompts/testing.py`):

```
[TRUSTED SYSTEM INSTRUCTIONS]
  - Safety rules
  - Output schema
  - Security constraints

=== WORKSPACE SUMMARY (DETERMINISTIC DATA) ===
  - Detected languages and frameworks
  - Project structure overview

=== CANDIDATE COMMANDS ([UNTRUSTED REPOSITORY CONTENT]) ===
  - Discovered commands from repository config
  - Each with executable, arguments, source, confidence

=== CHANGED FILES (TRUSTED DATA) ===
  - Files modified by the Phase 6 patch

=== YOUR TASK ===
  - Select commands from candidates list only
  - Determine optimal order
  - Provide reasoning
```

**Three-tier fallback:**

```
LLM Provider    → Provider fails   → Deterministic
LLM Call        → Call fails       → Deterministic  
LLM Output      → Not parseable    → Deterministic
```

**Candidate validation:** All LLM-suggested commands are validated against the original candidate list. Any command the LLM invents that doesn't match a known candidate is **rejected with a warning**, preventing arbitrary command generation.

### 2. Execution Policy (`app/services/execution_policy.py`)

100% deterministic security gate. *No LLM involvement.*

```
ExecutionPolicy.validate_step(step, workspace_root)
    │
    ├── 1. Category check ─── BUILD/LINT/TYPECHECK allowed?
    ├── 2. Executable check ── In allowlist?
    ├── 3. CWD check ───────── Inside workspace root?
    ├── 4. Argument check ──── No shell metacharacters?
    └── 5. Count check ─────── Under max commands?
    │
    └── Result: ALLOWED or REJECTED (with reason)
```

| Validation | What It Checks |
|------------|---------------|
| Category | BUILD/LINT/TYPECHECK disabled by default (configurable) |
| Executable | Must be in explicit allowlist |
| Working directory | Must resolve to a path inside workspace root |
| Arguments | No `;`, `|`, `$()`, backticks, `>` redirects |
| Package scripts | For `npm test`/`npm run`, inspects the actual script content |
| Command count | Enforces `TEST_MAX_COMMANDS` limit |

**Allowed executables (conservative default):**

```
python, python3, pytest
node, npm, npx, pnpm, yarn
make
```

**Always blocked:**

```
powershell, pwsh, cmd, bash, sh, zsh, fish
curl, wget, ssh, scp, sftp
sudo, su, docker, podman, kubectl
```

### 3. Controlled Execution Engine (`app/services/controlled_execution_engine.py`)

Safe, bounded subprocess execution. *Never uses `shell=True`.*

```
ControlledExecutionEngine.execute(step, workspace_root)
    │
    ├── 1. Validate workspace path (must be inside workspace root)
    ├── 2. Build sanitized environment
    │      ├── PATH, SYSTEMROOT, TEMP, USERPROFILE
    │      └── ❌ NEVER: OPENAI_API_KEY, ANTHROPIC_API_KEY, GITHUB_TOKEN
    ├── 3. Start subprocess
    │      └── asyncio.create_subprocess_exec(executable, *arguments)
    │                    cwd=validated_workspace_path
    │                    env=sanitized_environment
    ├── 4. Capture stdout/stderr (bounded)
    │      ├── Max bytes configurable (default 1MB)
    │      └── Truncated flag set if exceeded
    ├── 5. Enforce timeout
    │      ├── Per-command timeout
    │      ├── Terminate process tree
    │      └── Collect partial output
    └── 6. Return ProcessExecutionResult
           ├── exit_code, stdout, stderr
           ├── timeout flag, truncation flags
           └── duration & timestamps
```

### 4. Result Parsers (`backend/app/testing/parsers/`)

| Parser | Framework | Test Counts | Failure Names | Stack Traces | Classification |
|--------|-----------|-------------|---------------|--------------|----------------|
| **PytestResultParser** | pytest | ✅ Full parsing | ✅ Extracted | ✅ Captured | ✅ 11 categories |
| **UnittestXMLParser** | unittest (JUnit XML) | ✅ tests/failures/errors/skipped | ✅ Extracted | ✅ Captured | ✅ `classify_message` |
| **VitestJsonParser** | Vitest (JSON) | ✅ numTotal/Passed/Failed/Pending | ✅ `fullName` + ancestors | ✅ failureMessages | ✅ `classify_message` |
| **JestJsonParser** | Jest (JSON) | ✅ numTotal/Passed/Failed/Pending | ✅ `fullName` + ancestors | ✅ failureMessages | ✅ `classify_message` |
| **GenericResultParser** | Fallback | ❌ Not parsed | ⚠️ Stderr text | ⚠️ Raw text | ⚠️ Exit code only |

> Dispatch order in `TestingService`: pytest → unittest → vitest → jest → generic.
> Vitest vs Jest JSON are discriminated by the `perfStats` key Jest suites always
> carry (Vitest never does).

### 5. TestingService (`app/services/testing_service.py`)

Orchestrator that coordinates the full pipeline:

```
TestingService.run_tests(workspace_root, changed_files)
    │
    ├── 1. discover_commands(workspace_root)
    │      ├── Check pyproject.toml for pytest config
    │      ├── Check pytest.ini
    │      ├── Check setup.cfg
    │      ├── Check package.json scripts
    │      └── Return List[CommandCandidate]
    │
    ├── 2. build_plan(candidates, changed_files)
    │      ├── Deduplicate by executable+arguments
    │      ├── Order by category + required flag
    │      └── Return ExecutionPlan
    │
    ├── 3. TestAgent.execute(input) ← LLM or deterministic
    │      └── Return ExecutionPlan (refined)
    │
    ├── 4. For each step in plan:
    │      ├── ExecutionPolicy.validate_step(step)
    │      └── If ALLOWED: ControlledExecutionEngine.execute(step)
    │
    ├── 5. Parse results
    │      ├── Try PytestResultParser first
    │      ├── Fall back to GenericResultParser
    │      └── Return TestRunResult
    │
    └── 6. Return TestRunResult
```

## Data Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│  DATA FLOW — Phase 6 Workspace → TestRunResult                      │
└─────────────────────────────────────────────────────────────────────┘

Phase 6 Workspace (modified)
    │
    ├── workspace_root ─────────────────────────────────┐
    ├── changed_files[] (from PatchApplicationResult) ──┤
    └── patch_result (optional) ────────────────────────┤
                                                       │
                                                       ▼
                                              ┌─────────────────┐
                                              │  TestingService  │
                                              │  .discover()     │
                                              └────────┬─────────┘
                                                       │
                                                       ▼
                                              ┌─────────────────┐
                                              │  TestAgent       │
                                              │  · deterministic │
                                              │  · or LLM       │
                                              └────────┬─────────┘
                                                       │
                                                       ▼
                                              ┌─────────────────┐
                                              │  ExecutionPlan   │
                                              │  · steps[]      │
                                              └────────┬─────────┘
                                                       │
                                            ┌──────────┴──────────┐
                                            ▼                     ▼
                                  ┌─────────────────┐  ┌─────────────────┐
                                  │ ExecutionPolicy │  │ (REJECTED →     │
                                  │ · validate each │  │  record + skip) │
                                  └────────┬────────┘  └─────────────────┘
                                           │
                                           ▼
                                  ┌─────────────────┐
                                  │ControlledEngine │
                                  │ · execute       │
                                  └────────┬────────┘
                                           │
                                           ▼
                                  ┌─────────────────┐
                                  │ ProcessResult[] │
                                  └────────┬────────┘
                                           │
                                           ▼
                                  ┌─────────────────┐
                                  │ Result Parsers  │
                                  │ · pytest/gen.   │
                                  └────────┬────────┘
                                           │
                                           ▼
                                  ┌─────────────────┐
                                  │  TestRunResult  │──→ Phase 8 (Fix Agent)
                                  │  + TestFailure[]│
                                  └─────────────────┘
```

## Prompt Security Architecture

```
LLM PROMPT (to BaseLLMProvider)
    │
    ├── [TRUSTED DEVPILOT INSTRUCTIONS]
    │   · System role: "You are DevPilot's Test Agent..."
    │   · Critical rules (safety, output schema, constraints)
    │   · Output JSON schema
    │
    ├── [DETERMINISTIC DATA]
    │   · Workspace summary (languages, frameworks)
    │   · Changed files list
    │
    └── [UNTRUSTED REPOSITORY CONTENT]
        · Candidate commands (discovered from repo files)
        · Command names, script content, arguments
    
    The LLM is instructed NOT to follow instructions
    embedded in [UNTRUSTED REPOSITORY CONTENT].
    
    OUTPUT → JSON parsed, validated against candidate list
             Any non-matching commands → REJECTED with warning
```

## Phase 6 Integration

The Test Agent consumes Phase 6 outputs for targeted test selection:

| Phase 6 Output | How Used |
|---------------|----------|
| `CodingWorkspace` (root path) | Working directory for test execution |
| `PatchApplicationResult.changed_files` | `changed_files` input for test selection |
| `FileChange.path` | Related test detection via filename patterns |
| `PatchSet` (metadata) | Context for LLM-powered planning |

The Test Agent **never modifies** Phase 6 workspace files. Planning is read-only. Execution may create `.pytest_cache`, coverage data, and build artifacts through approved commands — but this is natural tool behavior, not agent writes.

## Security Boundaries

| Protection | Implementation |
|------------|---------------|
| **Workspace isolation** | All commands execute with `cwd` validated to be inside workspace root |
| **Executable control** | Only known-safe executables are allowed |
| **No shell injection** | `asyncio.create_subprocess_exec(*args)` — argument arrays, never shell strings |
| **No secret leakage** | Child process environment sanitized — API keys never passed |
| **Bounded execution** | Per-command timeout + process tree termination + output size limits |
| **Prompt injection** | Repository content marked `[UNTRUSTED]` in LLM prompt |
| **Candidate validation** | LLM cannot invent arbitrary commands — only select from discovered list |

## API

| Endpoint | Method | Purpose | Request Body |
|----------|--------|---------|--------------|
| `/api/v1/testing/plan` | POST | Create execution plan (no execution) | `{ workspace_id, workspace_root, changed_files[] }` |
| `/api/v1/testing/plan-from-patch` | POST | Create plan from Phase 6 patch | `{ workspace_id, workspace_root, patch_result }` |
| `/api/v1/testing/run` | POST | Execute a plan (controlled execution) | `{ workspace_id, workspace_root, plan_id? }` |
| `/api/v1/testing/capabilities` | GET | List testing capabilities | — |

## CLI

| Command | Purpose |
|---------|---------|
| `python -m app.cli test-plan --workspace PATH` | Create a test plan (inspect only) |
| `python -m app.cli test --workspace PATH` | Execute tests with controlled execution |

## Workflow

The Phase 7 workflow in `app/workflows/testing.py` is a linear graph:

```
validate_workspace → discover_commands → build_plan → validate_policy → execute → normalize → END
```

Each node is an async function. The workflow wraps `TestingService.run_tests()` with proper error handling and workspace validation.

## Key Files

| File | Purpose |
|------|---------|
| `app/agents/test_agent.py` | Test Agent — reasoning, LLM integration, deterministic fallback |
| `app/services/testing_service.py` | TestingService — orchestrator (discover → plan → execute → parse) |
| `app/services/execution_policy.py` | ExecutionPolicy — deterministic security gate |
| `app/services/controlled_execution_engine.py` | ControlledExecutionEngine — safe subprocess execution |
| `app/testing/parsers/pytest_parser.py` | PytestResultParser — full pytest output parsing |
| `app/testing/parsers/unittest_xml_parser.py` | UnittestXMLParser — JUnit-style XML (unittest reporters) |
| `app/testing/parsers/vitest_json_parser.py` | VitestJsonParser — Vitest JSON reporter |
| `app/testing/parsers/jest_json_parser.py` | JestJsonParser — Jest JSON reporter |
| `app/testing/parsers/generic_parser.py` | GenericResultParser — fallback parser |
| `app/workflows/testing.py` | Testing workflow graph |
| `app/api/v1/testing.py` | REST API endpoints |
| `app/prompts/testing.py` | LLM prompt with trust boundaries |
| `app/models/testing.py` | Data models (ExecutionPlan, TestRunResult, TestFailure, etc.) |
| `tests/test_testing.py` | 79 Phase 7 tests |

## Related Documentation

| Document | Content |
|----------|---------|
| `docs/ARCHITECTURE.md` | Full pipeline architecture (all 8 phases) |
| `docs/TESTING_AND_EXECUTION.md` | Comprehensive Phase 7 documentation |
| `docs/FIX_AGENT_ARCHITECTURE.md` | Phase 8 Fix Agent architecture (downstream consumer) |
| `docs/CODING_AGENT.md` | Phase 6 Coding Agent architecture (upstream producer) |
