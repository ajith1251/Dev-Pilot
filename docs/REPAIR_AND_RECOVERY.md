# Fix Agent & Bounded Repair Loop — Phase 8

> Phase 8 answers: *"When Phase 7 detects a test failure, can DevPilot diagnose the root cause and generate a minimal, safe repair?"*

## Architecture

```
                    PHASE 7 OUTPUT
                        │
               TestRunResult + TestFailure[]
                        │
                        ▼
               FailureDiagnosisService
                        │
                        ▼
               FailureDiagnosis
                        │
               repairable?
              /           \
            NO             YES
             |              |
         STOP REPAIR    FixAgent (LLM)
                            |
                       RepairProposal
                            |    \
                     RepairPolicy  \
                      (tampering,   \
                       config,       \
                       path safety)   \
                            |           \
                     PatchValidator      \
                            |              \
                     SafePatchEngine        \
                            |                 \
                       Modified Workspace       \
                            |                    \
                     TestAgent (Phase 7)          \
                            |                      \
                       TestRunResult                \
                      /              \                \
                   PASS              FAIL              \
                     |              /    \               \
                  SUCCESS     Progress?  Repeated?    Environmental?
                                /    \       |             |
                              YES    NO    STOP         STOP
                               |      |
                          Retry?   STOP
                          /    \
                        YES    NO
                         |      |
                     FixAgent  STOP
```

## Key Design Principles

### 1. The Fix Agent Has Reasoning Authority Only

The Fix Agent can **propose** repairs but has **no direct mutation or execution authority**:

| Authority | FixAgent | RepairPolicy / PatchValidator / SafePatchEngine |
|-----------|----------|------------------------------------------------|
| Propose code changes | ✅ Generate | ❌ |
| Validate safety | ❌ | ✅ Deterministic policy |
| Apply to workspace | ❌ | ✅ SafePatchEngine only |
| Execute tests | ❌ | ✅ Phase 7 only |

### 2. Deterministic Safety First

Before any repair is applied, it passes through:

1. **RepairPolicy** — Test tampering detection, config weakening, path safety, scope limits, dangerous content patterns
2. **PatchValidator** — Phase 6 validation: hash verification, protected files, path safety
3. **SafePatchEngine** — Phase 6 engine: atomic writes, rollback support

### 3. Bounded Loop

The repair loop is strictly bounded:

```
MAX_REPAIR_ATTEMPTS = 3 (configurable 1–5)
```

Additional stop conditions:
- **No progress** — failure fingerprint hasn't changed after 2 attempts
- **Repeated patch** — same patch fingerprint proposed twice
- **Worsening** — repair makes results worse (more failures, fewer passes)
- **Environmental** — failure is infrastructure-related, not code
- **Unsafe repair** — proposal violates repair policy

## Models

All models in `app/models/repair.py`:

| Model | Purpose |
|-------|---------|
| `FailureDiagnosis` | Structured diagnosis from test evidence |
| `RepairProposal` | Fix Agent output (PatchSet or decision not to repair) |
| `RepairAttempt` | Single repair attempt with full history |
| `RepairSession` | Bounded repair loop session |
| `RepairResult` | Final output consumed by Phase 9 |
| `RepairCapabilities` | Reported capabilities |
| `Repairability` | Enum: REPAIRABLE, POSSIBLY_REPAIRABLE, NOT_REPAIRABLE, ENVIRONMENTAL, INSUFFICIENT_CONTEXT |
| `RepairProposalStatus` | PROPOSED, NO_REPAIR, INSUFFICIENT_CONTEXT, ENVIRONMENTAL, REJECTED |
| `RepairAttemptStatus` | 11 statuses from PENDING through ROLLED_BACK and ERROR |
| `RepairSessionStatus` | SUCCESS, FAILED, MAX_ATTEMPTS, NO_PROGRESS, REPEATED_PATCH, UNSAFE_REPAIR, etc. |

## Services

### FailureDiagnosisService (`app/services/failure_diagnosis_service.py`)

Deterministic failure triage without LLM:

| Method | Purpose |
|--------|---------|
| `diagnose()` | Produces list of `FailureDiagnosis` from `TestRunResult` |
| `_classify_repairability()` | Maps failure category + patch evidence to repairability |

Repairability classification:

| Failure Category | Without Patch Evidence | With Patch Evidence |
|-----------------|----------------------|-------------------|
| SYNTAX_ERROR | REPAIRABLE (0.7) | REPAIRABLE (0.9) |
| IMPORT_ERROR | POSSIBLY_REPAIRABLE (0.4) | REPAIRABLE (0.7–0.8) |
| ASSERTION_FAILURE | POSSIBLY_REPAIRABLE (0.4) | REPAIRABLE (0.7) |
| TYPE_ERROR | POSSIBLY_REPAIRABLE (0.4) | REPAIRABLE (0.7) |
| BUILD_FAILURE | POSSIBLY_REPAIRABLE (0.5) | POSSIBLY_REPAIRABLE (0.5) |
| LINT_FAILURE | POSSIBLY_REPAIRABLE (0.5) | POSSIBLY_REPAIRABLE (0.5) |
| TIMEOUT | ENVIRONMENTAL (0.3) | ENVIRONMENTAL (0.3) |
| DEPENDENCY_ERROR | ENVIRONMENTAL (0.6) | ENVIRONMENTAL (0.6) |
| CONFIGURATION_ERROR | ENVIRONMENTAL (0.6) | ENVIRONMENTAL (0.6) |
| EXECUTION_ERROR | ENVIRONMENTAL (0.4) | ENVIRONMENTAL (0.4) |
| UNKNOWN | INSUFFICIENT_CONTEXT (0.2) | INSUFFICIENT_CONTEXT (0.2) |

### RepairPolicy (`app/services/repair_policy.py`)

Deterministic safety validation:

| Check | Guards Against |
|-------|---------------|
| Scope | Too many files, oversized patches |
| Path safety | Directory traversal, absolute paths outside workspace, symlink escape |
| Test tampering | Test file deletion, @pytest.mark.skip, @pytest.mark.xfail, assertion weakening |
| Config weakening | `norecursedirs`, `collect_ignore`, `testpaths` changes that exclude tests |
| Dangerous content | `os.system()`, `subprocess.*()`, `eval()`, `exec()` |
| Protected files | `.git/` directory, `.env` deletion |

### RepairService (`app/services/repair_service.py`)

Bounded loop orchestrator. Main entry point: `run_repair()`.

Flow:
1. Validate input (not environmental, has failures)
2. Diagnose failures
3. Check repairability
4. Bounded loop (max 3 attempts):
   a. Invoke FixAgent
   b. Validate with RepairPolicy + PatchValidator
   c. Snapshot workspace
   d. Apply via SafePatchEngine
   e. Rerun Phase 7 verification
   f. Evaluate: progress? worsened? repeated?
   g. Rollback if worsened
   h. Stop on: PASS / MAX_ATTEMPTS / NO_PROGRESS / REPEATED_PATCH / UNSAFE_REPAIR

## Fix Agent (`app/agents/fix_agent.py`)

LLM-powered repair generation using provider-independent `BaseLLMProvider`.

| Aspect | Design |
|--------|--------|
| LLM abstraction | `llm_factory.get_provider()` — no vendor SDK imports |
| Prompt | `app/prompts/fixing.py` with trust boundaries |
| Output parsing | JSON extraction from markdown fences or raw text |
| Fallback | Provider unavailable → NO_REPAIR. Malformed JSON → INSUFFICIENT_CONTEXT |
| Trust boundaries | Repository content = `[UNTRUSTED REPOSITORY CONTENT]`, Test output = `[UNTRUSTED TEST OUTPUT]` |

## Failure Fingerprinting

Used to detect repeated failure states and no-progress conditions:

```python
fingerprint = hash(
    failure_type + test_name + file_path + normalized_message
)
```

Normalization strips: hex addresses (`0x...`), temp paths (`/tmp/...`), timestamps.

## Patch Fingerprinting

Used to detect repeated (identical) repair proposals:

```python
fingerprint = hash(
    operation + path + new_content_hash
)
```

## API

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v1/repair/diagnose` | POST | Diagnose test failures without repair |
| `/api/v1/repair/run` | POST | Execute full bounded repair workflow |
| `/api/v1/repair/capabilities` | GET | List Phase 8 capabilities |

## CLI

| Command | Purpose |
|---------|---------|
| `devpilot repair-diagnose --run-file <path>` | Inspect test failures and return diagnoses |
| `devpilot repair --workspace <path> --run-file <path>` | Execute bounded repair loop |

## Security

| Concern | Status |
|---------|--------|
| Direct FixAgent file writes | ❌ Not allowed — all outputs go through PatchValidator + RepairPolicy + SafePatchEngine |
| Direct FixAgent execution | ❌ Not allowed — testing uses Phase 7 ControlledExecutionEngine |
| Test tampering | ✅ Blocked — RepairPolicy detects skip/xfail/assertion weakening |
| Config weakening | ✅ Blocked — RepairPolicy detects `norecursedirs`, `collect_ignore` |
| Dangerous content | ✅ Blocked — `os.system`, `subprocess`, `eval`, `exec` |
| Path escape | ✅ Blocked — workspace boundary enforced |
| Original repository | ❌ Never mutated — only writable workspaces |
| Secret leakage | ✅ Inherits Phase 7 environment sanitization |
| Prompt injection | ✅ Test output = `[UNTRUSTED TEST OUTPUT]` |

## Known Limitations

1. **LLM quality dependency** — Repairs are only as good as the LLM's reasoning. Deterministic validation provides a safety net.
2. **Prompt injection surface** — Test output and repository content are marked untrusted, but hardening is continuous.
3. **Limited test selection heuristics** — After repair, the full test plan is re-run. Targeted retesting is not yet optimized.
4. **Windows process tree termination** — Rollback may have edge cases on Windows due to file-locking behavior.
5. **In-memory state** — Repair sessions are in-memory only. Persistence is deferred to a later phase.

## Phase 9 Contract

Phase 9 (Reviewer Agent + Quality Gate) receives:

```
ImplementationPlan          → app.models.issues.ImplementationPlan
PatchSet                    → app.models.coding.PatchSet (original + repair)
PatchApplicationResult      → app.models.coding.PatchApplicationResult
TestRunResult               → app.models.testing.TestRunResult (initial + final)
TestFailure[]               → app.models.testing.TestFailure
FailureDiagnosis[]          → app.models.repair.FailureDiagnosis
RepairAttempt[]             → app.models.repair.RepairAttempt
RepairResult                → app.models.repair.RepairResult
Final workspace state       → writable workspace path
```
