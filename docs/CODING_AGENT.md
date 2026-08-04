# DevPilot — Coding Agent & Safe Patch Engine (Phase 6)

> **Status**: Complete ✅
> **Phase**: 6 of 10+

## Overview

Phase 6 implements the **Coding Agent and Safe Patch Engine** — the bridge between validated implementation plans and actual code changes. It transforms a validated `ImplementationPlan` (Phase 4) augmented with `RetrievedContext` (Phase 5) into structured, validated, and safely applied file modifications.

The core capability: **Given a validated implementation plan and relevant repository context, generate, validate, dry-run, and optionally apply code changes — all without executing repository code or risking data loss.**

### Pipeline

```
ImplementationPlan (Phase 4) + RetrievedContext (Phase 5)
        ↓
    WorkspaceService → Isolated writable copy of repository
        ↓
    CodingAgent (LLM) → Structured PatchSet proposal
        ↓
    PatchValidator (Deterministic) → Security gate
        ↓
    SafePatchEngine
        ├── Dry-run → Unified diff preview (no file mutations)
        └── Apply → Atomic writes with snapshot/rollback
                ↓
        PatchApplicationResult
```

### Key Principles

- **LLM proposes, deterministic software disposes**: The Coding Agent generates patch proposals; PatchValidator and SafePatchEngine apply zero-trust security
- **Isolation first**: Workspace is a copy of the source repo — original is never modified
- **Explicit apply**: Generation never mutates files. Apply requires explicit intent.
- **Atomic operations**: Writes use temp-file → fsync → rename pattern; rollback restores on failure
- **Minimal changes**: The agent is instructed to produce the smallest coherent changes
- **Traceability**: Every change ties back to a specific plan step and requirement
- **No code execution**: The engine never runs tests, installs deps, or executes repository code

---

## Architecture

### Directory Structure

```
backend/app/
├── agents/
│   └── coding_agent.py           ← Coding Agent: LLM-powered patch generation
├── models/
│   └── coding.py                 ← Phase 6 domain models (8 types)
├── services/
│   ├── safe_patch_engine.py      ← Deterministic file mutation engine
│   ├── patch_validator.py        ← Security gate — validates PatchSet structure
│   ├── coding_service.py         ← Orchestrator — coordinates the pipeline
│   └── workspace_service.py      ← Safe isolated writable workspace management
├── workflows/
│   └── coding.py                 ← Coding workflow (generate + apply modes)
├── api/v1/
│   └── coding.py                 ← 4 API endpoints
├── prompts/
│   └── coding.py                 ← System prompt with trust boundaries
├── core/
│   └── exceptions.py             ← 8 Phase 6 exception types
├── config.py                     ← 5 Phase 6 configuration settings
└── cli.py                        ← `code` and `patch` CLI subcommands
```

### Component Responsibilities

| Component | File | Responsibility |
|-----------|------|----------------|
| **Domain Models** | `models/coding.py` | All Phase 6 data types: FileChange, PatchSet, CodingWorkspace, PatchValidationResult, PatchApplicationResult, CodingResult, CodingCapabilities, CodingAgentInput/Output |
| **Coding Agent** | `agents/coding_agent.py` | Consumes plan + context → produces structured PatchSet via LLM; never writes files directly |
| **System Prompt** | `prompts/coding.py` | TRUSTED vs UNTRUSTED content separation, JSON output schema, minimal change rules |
| **Patch Validator** | `services/patch_validator.py` | 100% deterministic security gate — path safety, protected files, content limits, hash verification, cycle/conflict detection |
| **Safe Patch Engine** | `services/safe_patch_engine.py` | Atomic writes, dry-run/diff generation, pre-apply snapshots, rollback, CRLF preservation, content hash verification |
| **Workspace Service** | `services/workspace_service.py` | Creates isolated writable copies excluding `.git`, `.env`, `node_modules`, etc.; cleanup support |
| **Coding Service** | `services/coding_service.py` | Orchestrates the pipeline: workspace → coding agent → validate → dry-run → apply |
| **Coding Workflow** | `workflows/coding.py` | Two modes: `run_generate` (default safe) and `run_apply` (explicit apply); follows same dataclass state pattern as Phases 2-5 |
| **API** | `api/v1/coding.py` | 4 REST endpoints (generate, dry-run, apply, capabilities) |
| **CLI** | `cli.py` | 2 subcommands (`code` and `patch`) |

---

## Domain Models (`models/coding.py`)

### File Change

```python
class FileOperation(str, Enum):
    CREATE = "create"    # Create a new file
    MODIFY = "modify"    # Modify an existing file
    DELETE = "delete"    # Delete an existing file

class PatchStatus(str, Enum):
    PROPOSED    = "proposed"     # Initial proposal from Coding Agent
    VALIDATED   = "validated"    # Passed deterministic validation
    REJECTED    = "rejected"     # Failed validation
    DRY_RUN     = "dry_run"      # Dry-run executed (no mutation)
    APPLIED     = "applied"      # Successfully applied to workspace
    FAILED      = "failed"       # Application failed
    ROLLED_BACK = "rolled_back"  # Applied but rolled back

class FileChange(BaseModel):
    change_id: str                  # Unique identifier (e.g. CHANGE-001)
    operation: FileOperation         # CREATE | MODIFY | DELETE
    path: str                        # Relative path from workspace root
    original_hash: Optional[str]     # SHA-256 of original (required for MODIFY/DELETE)
    new_content: Optional[str]       # New file content (CREATE/MODIFY)
    reason: str                      # Why this change is needed
    plan_step_id: Optional[str]      # Ties to ImplementationPlan step
    requirement_ids: List[str]       # Ties to requirements
    source_context_ids: List[str]    # Ties to retrieved chunks
```

### Patch Set

```python
class PatchSet(BaseModel):
    patch_id: str                    # Unique patch identifier
    plan_id: Optional[str]           # Plan this patch implements
    workspace_snapshot: Optional[str]# Workspace fingerprint at generation time
    changes: List[FileChange]        # Ordered list of file changes
    summary: str                     # Human-readable patch summary
    warnings: List[str]              # Non-blocking concerns
    metadata: Dict[str, Any]         # Extended metadata
    status: PatchStatus              # Lifecycle status
```

### Patch Validation Result

```python
class PatchValidationResult(BaseModel):
    is_valid: bool                   # Pass/fail
    errors: List[str]                # Blocking validation errors
    warnings: List[str]              # Non-blocking warnings
    checked_changes: int             # Number of changes examined
    checked_operations: int          # Number of operation checks
    status: str                      # "validated" | "rejected"
```

### Coding Workspace

```python
class CodingWorkspace(BaseModel):
    workspace_id: str                # UUID-based unique ID
    source_repository: str           # Absolute path to original source
    root_path: str                   # Absolute path to workspace root
    fingerprint: str                 # SHA-256 fingerprint at creation
    created_at: str                  # ISO timestamp
    writable: bool                   # True for actual patching
```

### Patch Application Result

```python
class PatchApplicationResult(BaseModel):
    patch_id: str                    # Applied patch identifier
    status: PatchStatus              # Final status
    dry_run: bool                    # Whether this was a simulation
    changes_attempted: int           # Total changes tried
    changes_applied: int             # Successfully applied
    files_created: List[str]         # New files
    files_modified: List[str]        # Modified files
    files_deleted: List[str]         # Deleted files
    diff: Optional[str]              # Unified diff text
    warnings: List[str]
    errors: List[str]
    rolled_back: bool
    duration_seconds: float
```

### Coding Result & Capabilities

```python
class CodingResult(BaseModel):
    status: str                      # PROPOSED | REJECTED | APPLIED | FAILED | INSUFFICIENT_CONTEXT
    plan_id: str
    patch_set: Optional[PatchSet]
    validation: Optional[PatchValidationResult]
    workspace_id: Optional[str]
    workspace_root: Optional[str]
    dry_run_result: Optional[PatchApplicationResult]
    apply_result: Optional[PatchApplicationResult]
    errors: List[str]
    warnings: List[str]
    duration: float

class CodingCapabilities(BaseModel):
    supported_operations: List[str]  # ["CREATE", "MODIFY", "DELETE"]
    max_files_per_patch: int         # Default 20
    max_file_size: int               # Default 500,000 bytes
    dry_run_supported: bool          # True
    diff_format: str                 # "unified"
    rollback_supported: bool         # True
    workspace_isolation: bool        # True
    delete_enabled: bool             # False (safety default)
```

### Coding Agent Contract

```python
class CodingAgentInput(BaseModel):
    plan: ImplementationPlan              # Validated plan from Phase 4
    requirements: Optional[StructuredRequirements]
    retrieved_context: Optional[RetrievedContext]  # Context from Phase 5
    workspace: Optional[CodingWorkspace]
    max_context_chunks: int               # 1-50, default 10
    max_context_chars: int                # 1000-50000, default 50000

class CodingAgentOutput(BaseModel):
    patch_set: Optional[PatchSet]         # Proposed changes
    status: str                           # "success" | "insufficient_context" | "error"
    missing_context: List[str]
    warnings: List[str]
    error: Optional[str]
```

---

## Coding Agent (`agents/coding_agent.py`)

The Coding Agent is an LLM-powered agent that produces structured `PatchSet` proposals. It **never writes files directly** — it outputs structured JSON that downstream deterministic components validate and apply.

### Flow

1. **Format plan context**: Serializes `ImplementationPlan` + `StructuredRequirements` into structured text
2. **Format retrieved context**: Converts `RetrievedContext` items (top 30 by default) into readable format with file paths, symbols, scores, and content previews
3. **Build prompt**: Calls `build_coding_prompt()` which combines:
   - System instructions (trusted)
   - Implementation plan (trusted)
   - Retrieved repository code (untrusted data)
   - Workspace structure (untrusted data)
4. **Call LLM**: Uses provider-independent `BaseLLMProvider.chat()` interface
5. **Parse response**: Extracts JSON from markdown code fences (```json ... ```) or raw braces
6. **Validate structure**: Parses each `FileChange` with Pydantic validation
7. **Return PatchSet**: Or raises `InsufficientContextError` / `CodingOutputValidationError`

### Prompt Design (`prompts/coding.py`)

The system prompt enforces:

- **Minimal changes**: "Make the smallest coherent changes that satisfy the plan"
- **UNTRUSTED content boundary**: Repository code is explicitly labeled as data, not instructions
- **No hallucinated files**: MODIFY targets must exist in context
- **Traceability**: Each change must include `plan_step_id`, `requirement_ids`, `source_context_ids`
- **Strict JSON schema**: Enforced via prompt instruction (not code)

For insufficient context, the agent can return:
```json
{
  "status": "INSUFFICIENT_CONTEXT",
  "missing_context": ["auth/tokens.py"],
  "warnings": ["cannot determine token validation logic"]
}
```

### Key Methods

| Method | Purpose |
|--------|---------|
| `generate_patch(plan, retrieved_context, requirements)` | Primary entry point — produces PatchSet |
| `generate_patch_for_step(step_title, chunks, plan, requirements)` | Step-by-step generation (extensible for Phase 7+) |
| `execute(inp: CodingAgentInput)` | Agent interface — wraps generate_patch with error handling |
| `_format_plan(plan, requirements)` | Serializes plan for prompt context |
| `_format_retrieved_context(context)` | Formats top 30 chunks with scores and content |
| `_parse_response(raw_response, plan, requirements)` | Extracts + validates JSON → PatchSet |
| `_extract_json(text)` | Handles markdown fences, malformed JSON, nested braces |

---

## Patch Validator (`services/patch_validator.py`)

**100% deterministic — no LLM calls.** This is the security gate between the Coding Agent and the filesystem.

### Validation Checks

| # | Check | Error/Warning | Description |
|---|-------|---------------|-------------|
| 1 | Non-empty patch | Error | PatchSet must contain at least one change |
| 2 | Change count limit | Error | Max `CODING_MAX_FILES_PER_PATCH` (default 20) |
| 3 | Unique change IDs | Error | No duplicate `change_id` values |
| 4 | Operation support | Error | Only CREATE, MODIFY, DELETE allowed |
| 5 | Path non-empty | Error | `path` must not be empty |
| 6 | Path safety | Error | Rejects absolute paths (`/etc/passwd`, `C:\...`) and traversal (`../../`) |
| 7 | Protected paths | Error | Blocks `.env`, `.git/`, `credentials.json`, `*.pem`, `*.key` |
| 8 | Content requirement | Error | CREATE/MODIFY require non-empty `new_content` |
| 9 | Content size limit | Error | Per-file `CODING_MAX_FILE_SIZE` (default 500KB) |
| 10 | Original hash | Error | MODIFY/DELETE require `original_hash` |
| 11 | Hash verification (workspace) | Error | SHA-256 must match actual file content |
| 12 | File existence (workspace) | Error | MODIFY/DELETE target must exist; CREATE target must not exist |
| 13 | Conflicting operations | Error | Same path with different operations |
| 14 | Delete policy | Error | DELETE disabled by default (`CODING_ALLOW_DELETE=False`) |
| 15 | Total patch size | Error | Sum of all content ≤ `CODING_MAX_PATCH_SIZE` (default 1MB) |

### Protected Paths

Files that can never be modified automatically:

| Category | Patterns |
|----------|----------|
| **Version control** | `.git/` |
| **Secrets** | `.env`, `.env.local`, `.env.production`, `.env.development` |
| **Credentials** | `credentials.json`, `id_rsa`, `id_rsa.pub` |
| **Certificate extensions** | `.pem`, `.key`, `.cert`, `.p12` |

### Path Safety Rules

- Absolute paths (`/etc/passwd`, `C:\windows`) → rejected
- Path traversal (`../../outside`) → rejected
- Path normalization: `\\` → `/` before checking
- Workspace validation: resolves symlinks, verifies path stays within workspace root

---

## Safe Patch Engine (`services/safe_patch_engine.py`)

A deterministic file mutation engine with comprehensive safety guarantees.

### Public API

| Method | Purpose | File Mutations? |
|--------|---------|-----------------|
| `dry_run(patch_set)` | Validate + generate diff without modifying files | ❌ No |
| `apply(patch_set)` | Validate + snapshot + apply + (rollback on failure) | ✅ Yes |

### Apply Flow

```
1. Validate against actual workspace state (path safety, hashes, existence)
2. Take snapshot of all affected files (in-memory: path → bytes)
3. Apply changes sequentially:
   a. CREATE: mkdir parents → atomic write → diff
   b. MODIFY: verify original_hash → atomic write → diff
   c. DELETE: verify original_hash → unlink → diff
4. On ANY exception → rollback all files from snapshot
5. Return PatchApplicationResult
```

### Safety Features

| Feature | Implementation |
|---------|----------------|
| **Atomic writes** | Write to `.{filename}.devpilot_tmp` → `fsync()` → `shutil.move()` |
| **Content hash verification** | SHA-256 match before MODIFY/DELETE |
| **Pre-apply snapshots** | Full byte-capture of all affected files |
| **Rollback** | Restore all files from snapshot on any failure; raises `PatchRollbackError` if rollback itself fails |
| **Path traversal prevention** | `Path.resolve()` + `relative_to()` check against workspace root |
| **Size limits** | Content bytes checked against `max_file_size` |
| **CRLF preservation** | Detects `\r\n` in existing file → normalizes `\n` to `\r\n` in new content |
| **Original source isolation** | Workspace is a copy — original repo never touched |

### Diff Generation

Uses Python stdlib `difflib.unified_diff` to produce standard unified diffs:

```diff
--- a/auth/tokens.py
+++ b/auth/tokens.py
@@ -1,3 +1,4 @@
 class TokenManager:
-    def create_token(self, user):
-        return 'token'
+    def create_token(self, user):
+        expires_at = datetime.utcnow() + timedelta(hours=24)
+        return {'token': 'abc', 'expires_at': expires_at.isoformat()}
+    def is_token_expired(self, token):
+        return True
```

---

## Workspace Service (`services/workspace_service.py`)

Manages safe isolated writable copies of source repositories for patch application.

### Workspace Lifecycle

```
1. create_workspace(source_path)
   ├── Validates source exists and is a directory
   ├── Generates UUID-based workspace_id (e.g. "ws-a1b2c3d4e5f6")
   ├── Creates temp directory (system temp or configurable base)
   ├── Copies source (excluding sensitive/system dirs)
   └── Returns CodingWorkspace

2. apply patches to workspace (via SafePatchEngine)

3. cleanup_workspace(workspace)
   └── shutil.rmtree() with ignore_errors=True
```

### Excluded Patterns

| Category | Patterns |
|----------|----------|
| **Version control** | `.git`, `.gitattributes`, `.gitignore` |
| **Environment** | `.env`, `.env.local`, `.env.production` |
| **Dependencies** | `node_modules` |
| **Python cache** | `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache` |
| **Build artifacts** | `.tox`, `.eggs`, `*.pyc` |
| **OS files** | `.DS_Store` |

### Fingerprinting

`fingerprint_source(source_path)` computes a deterministic SHA-256 hash of:
- Sorted relative file paths
- File sizes (st_size)
- File modification times (st_mtime)

This enables change detection for future incremental updates.

---

## Coding Service (`services/coding_service.py`)

The orchestration layer that wires together all Phase 6 components.

### Methods

| Method | Flow | Mutations? |
|--------|------|------------|
| `generate(plan, requirements, retrieved_context, repo_path)` | Create workspace → build structure summary → CodingAgent → PatchValidator | ❌ Workspace created but no file changes |
| `dry_run(patch_set, workspace_root)` | SafePatchEngine.dry_run() | ❌ No |
| `apply(patch_set, workspace_root)` | SafePatchEngine.apply() | ✅ Yes (to workspace) |
| `generate_and_dry_run(plan, requirements, retrieved_context, repo_path)` | generate() → dry_run() | ❌ No |
| `generate_and_apply(plan, requirements, retrieved_context, repo_path)` | generate() → dry_run() → apply() | ✅ Yes (explicit) |
| `get_capabilities()` | Returns CodingCapabilities | — |

### Error Handling

| Scenario | Result Status |
|----------|---------------|
| Workspace creation fails | `FAILED` |
| Coding Agent returns insufficient_context | `INSUFFICIENT_CONTEXT` |
| Coding Agent errors | `FAILED` |
| PatchValidator rejects | `REJECTED` |
| All checks pass | `PROPOSED` |
| Dry-run + apply succeed | `APPLIED` |

---

## Coding Workflow (`workflows/coding.py`)

Follows the same dataclass-based state pattern as Phases 2-5 workflows.

### State Model

```python
@dataclass
class CodingWorkflowState:
    plan: ImplementationPlan
    requirements: StructuredRequirements
    retrieved_context: RetrievedContext
    repository_path: str
    status: str                     # pending|running|completed|failed
    workspace_id: Optional[str]
    workspace_root: Optional[str]
    patch_set: Optional[PatchSet]
    validation: Optional[PatchValidationResult]
    dry_run_result: Optional[PatchApplicationResult]
    apply_result: Optional[PatchApplicationResult]
    errors: List[str]
    warnings: List[str]
    coding_result: Optional[CodingResult]
    started_at: Optional[str]
    completed_at: Optional[str]
```

### Entry Points

```python
# Safe mode — no file mutations beyond workspace creation
state = await workflow.run_generate(plan, requirements, context, repo_path)

# Apply mode — generates + validates + dry-runs + applies
state = await workflow.run_apply(plan, requirements, context, repo_path)
```

### Pipeline Nodes

```python
START → _run_generate_pipeline OR _run_apply_pipeline → END

_generate_pipeline:
    CodingService.generate_and_dry_run()
    → state.patch_set, state.validation, state.dry_run_result

_apply_pipeline:
    CodingService.generate_and_apply()
    → state.patch_set, state.validation, state.dry_run_result, state.apply_result
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/coding/generate` | Generate PatchSet from plan + context. Returns validated patch with dry-run diff. No file mutations. |
| `POST` | `/api/v1/coding/dry-run` | Dry-run an existing PatchSet against a workspace. Returns unified diff. No file mutations. |
| `POST` | `/api/v1/coding/apply` | Apply a PatchSet to a workspace. **Requires explicit intent.** Full safety checks enforced. |
| `GET` | `/api/v1/coding/capabilities` | List Phase 6 coding capabilities. |

### Request Schemas

#### `POST /api/v1/coding/generate`

```json
{
  "plan": { /* Serialized ImplementationPlan */ },
  "requirements": { /* Serialized StructuredRequirements */ },
  "retrieved_context": { /* Serialized RetrievedContext */ },
  "repository_path": "/path/to/source/repo"
}
```

#### `POST /api/v1/coding/dry-run`

```json
{
  "patch_set": { /* Serialized PatchSet */ },
  "workspace_root": "/path/to/workspace"
}
```

#### `POST /api/v1/coding/apply`

```json
{
  "patch_set": { /* Serialized PatchSet */ },
  "workspace_root": "/path/to/workspace"
}
```

---

## CLI Commands

```bash
# Generate code changes from a plan file and repo context
python -m app.cli code generate \
    --repo /path/to/repo \
    --plan plan.json \
    --context context.json \
    --requirements requirements.json

# Dry-run a pre-generated patch
python -m app.cli patch dry-run \
    --patch patch.json \
    --workspace /path/to/workspace

# Apply a pre-generated patch (explicit)
python -m app.cli patch apply \
    --patch patch.json \
    --workspace /path/to/workspace
```

---

## Configuration

All Phase 6 settings in `backend/app/config.py`:

| Setting | Env Variable | Default | Description |
|---------|-------------|---------|-------------|
| `CODING_MAX_FILES_PER_PATCH` | `DEVPILOT_CODING_MAX_FILES_PER_PATCH` | `20` | Maximum files per PatchSet (1-100) |
| `CODING_MAX_FILE_SIZE` | `DEVPILOT_CODING_MAX_FILE_SIZE` | `500_000` | Max file size in bytes for coding modifications |
| `CODING_MAX_PATCH_SIZE` | `DEVPILOT_CODING_MAX_PATCH_SIZE` | `1_000_000` | Max total patch content size in bytes |
| `CODING_ALLOW_DELETE` | `DEVPILOT_CODING_ALLOW_DELETE` | `False` | Whether DELETE operations are permitted |
| `CODING_WORKSPACE_ROOT` | `DEVPILOT_CODING_WORKSPACE_ROOT` | `None` (system temp) | Base directory for coding workspaces |

---

## Exceptions

All Phase 6 exceptions inherit from `CodingError(DevPilotError)`:

| Exception | Raised When |
|-----------|-------------|
| `CodingError` | Base — generic coding operation failure |
| `CodingOutputValidationError` | LLM response fails schema validation (bad JSON, missing fields) |
| `InsufficientContextError` | Coding Agent reports insufficient context for code generation |
| `WorkspaceError` | Workspace preparation or cleanup fails |
| `PatchValidationError` | PatchSet fails deterministic validation |
| `PatchConflictError` | Conflicting patch operations detected |
| `PatchApplicationError` | Patch application fails (hash mismatch, disk error, path traversal) |
| `PatchRollbackError` | Rollback restoration fails partially |

All exceptions carry an optional `details: dict` for structured error context.

---

## Security

### Protection Mechanisms

| Threat | Protection Layer | Implementation |
|--------|-----------------|----------------|
| **Code injection via prompt** | Coding Agent prompt | Explicit TRUSTED/UNTRUSTED content separation. Repository code is DATA, not instructions. |
| **Malicious PatchSet** | PatchValidator | 15 deterministic checks block path traversal, protected files, content violations, hash mismatches |
| **Path traversal** | PatchValidator + SafePatchEngine | `_is_safe_path()` and `_resolve_safe_path()` both use `Path.resolve()` + `relative_to()` |
| **Accidental mutation** | WorkspaceService | Operations happen on isolated workspace copy, not original source |
| **Data loss on failure** | SafePatchEngine | Pre-apply snapshot + transactional rollback |
| **Secrets in patches** | PatchValidator | `.env`, `credentials.json`, `*.pem`, `*.key`, `id_rsa` all blocked |
| **Oversized changes** | PatchValidator | Per-file (500KB) and per-patch (1MB) size limits |
| **Unsolicited mutations** | API + CodingService | Generation creates workspace but applies nothing. Apply requires explicit POST. |
| **No code execution** | All components | SafePatchEngine uses stdlib `difflib`, `hashlib`, `shutil`. Never `os.system`, `subprocess`, or `exec()`. |

### Trust Boundary

```
TRUSTED:                                 UNTRUSTED (DATA):
- DevPilot system instructions           - Repository file contents
- ImplementationPlan (validated)         - Retrieved code chunks
- StructuredRequirements (validated)     - Workspace structure
- Deterministic validation rules         - File paths (syntax only)
```

### Read-Only Guarantees

| Operation | Mutates Original? | Mutates Workspace? |
|-----------|-------------------|-------------------|
| Workspace creation | ❌ Never | ✅ Creates workspace dir |
| Code generation (LLM call) | ❌ Never | ❌ No |
| Patch validation | ❌ Never | ❌ No |
| Dry-run | ❌ Never | ❌ No |
| Explicit apply (via API/CLI) | ❌ Never | ✅ Applies to workspace only |

---

## Testing

### Test Coverage

| Area | Tests | Description |
|------|-------|-------------|
| **PatchValidator** | 14 | Valid/invalid patches, empty, duplicates, path traversal, absolute paths, protected files, missing hashes, content requirements, conflicts, delete policy, safe path utility, workspace validation, missing files, hash mismatch |
| **SafePatchEngine** | 10 | Dry-run no-modification guarantee, create/modify/delete operations, rollback on failure, diff generation, CRLF preservation, path traversal rejection, oversized content rejection, source isolation |
| **WorkspaceService** | 6 | Create workspace, excludes `.git`, excludes `.env`, source unchanged after workspace modification, cleanup, invalid source |
| **CodingAgent** | 6 | JSON extraction from markdown/plain, full generate_patch with mock LLM, insufficient context handling, parse_change, invalid operation |
| **API** | 1 | Capabilities endpoint response |
| **Integration** | 2 | Full pipeline (validate → dry-run → apply), PatchSet model validation |
| **Exceptions** | 4 | All 4 Phase 6 exception types construct correctly |

### Test Requirements

- **No network**: All tests work offline
- **No API keys**: Mocked LLM provider — no real API calls
- **No external services**: In-process only
- **Deterministic**: Same tests always produce same results
- **Filesystem isolation**: All file operations use `tmp_path` fixtures, cleaned up automatically

---

## Dependencies

Phase 6 adds **no new external dependencies**. All functionality uses:

- Python standard library (`difflib`, `hashlib`, `os`, `shutil`, `tempfile`, `json`, `re`, `pathlib`)
- Existing Phase 1-5 abstractions (`BaseAgent`, `BaseLLMProvider`, `LLMMessage`, `LLMConfig`)
- Existing Phase 4 models (`ImplementationPlan`, `StructuredRequirements`)
- Existing Phase 5 models (`RetrievedContext`, `RetrievedContextItem`, `CodeChunk`)
- Pydantic (`BaseModel`, `Field`, `ValidationError`)

This was a deliberate design constraint — no external diff libraries, no patch parsers, no file-watching libraries.

---

## Known Limitations

1. **LLM required**: The Coding Agent requires a configured LLM provider. No offline/reduced mode for code generation.

2. **No incremental workspace reuse**: Each generate call creates a fresh workspace. Future phases may add workspace caching or step-sequential patching.

3. **Delete disabled by default**: File deletion requires explicit `CODING_ALLOW_DELETE=True`. This is a safety measure that most use cases won't need.

4. **No streamed patch generation**: Full PatchSet is generated in one LLM call. Step-by-step generation is scaffolded via `generate_patch_for_step()` but not the default flow.

5. **No test generation**: Tests are not generated alongside code changes. This is planned for Phase 7.

6. **No post-apply validation**: After applying, the engine does not verify correctness (e.g., test execution). Human review is expected.

7. **In-memory workspace registry**: Workspaces are not persisted across process restarts. `WorkspaceService.get_workspace()` returns `None` until persistence is implemented.

8. **Single-attempt apply**: No retry logic for failed applications. Rollback restores state but does not re-attempt.

9. **CLI requires plan/context/requirements as JSON files**: No interactive generation mode. Pre-computed plan and context are expected inputs.

---

## Cross-Phase Integration

### Phase 4 → Phase 6

```
ImplementationPlan.plan_id
    → PatchSet.plan_id (traceability)

ImplementationStep.id (e.g. "STEP-001")
    → FileChange.plan_step_id (per-change traceability)

StructuredRequirements.objective
    → CodingAgent prompt context
```

### Phase 5 → Phase 6

```
RetrievedContext.items[].chunk.content
    → CodingAgent prompt (UNTRUSTED DATA section)

RetrievedContext.items[].score
    → Context ranking for prompt ordering

RetrievedContextItem.reasons
    → Context explanation in prompt

CodeChunk.chunk_id
    → FileChange.source_context_ids (traceability)
```

### API/CLI → Phase 6

```
/api/v1/planning/plan + /api/v1/code-intelligence/retrieval/plan-context
    → POST /api/v1/coding/generate (manual pipeline currently)

Future: Automated end-to-end pipeline (Phase 7+)
```

---

## Future Improvements (Post-Phase 6)

- Step-by-step incremental patching (each step builds on previous workspace state)
- Automated test generation alongside code changes (Phase 7)
- Post-apply test execution and validation
- Workspace persistence and caching
- Streaming patch generation with partial results
- Interactive patch review and approval UI (Frontend)
- Multi-repository patches (monorepo workspace support)
- Git-aware patch generation (branch creation, commit messages, PR bodies)
- Rollback enhancement: per-file granularity instead of all-or-nothing
