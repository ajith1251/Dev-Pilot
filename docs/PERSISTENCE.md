# Phase 11 — Persistent State & Run Management

## Overview

Phase 11 transforms DevPilot's orchestration from process-local in-memory
storage to durable PostgreSQL-backed persistence. Runs survive backend
restarts, crashes are recoverable, and the frontend displays real data
from the database.

## Architecture

```text
                 DevPilot

                    │
                    ▼
             FastAPI Backend
                    │
                    ▼
          OrchestrationService
                    │
                    ▼
               RunStore
              /        \
             /          \
            ▼            ▼
    InMemoryRunStore    PostgresRunStore
    (tests/dev)         (production path)
                         │
                         ▼
                     PostgreSQL
                         │
       ┌─────────────────┼──────────────────┐
       ▼                 ▼                  ▼
      Runs             Events          Stage Results
```

## Storage Models

### Tables

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `runs` | Core run state | run_id, status, current_stage, source fields, failure, version (concurrency) |
| `tasks` | Task identity | source_type, title, description, GitHub issue refs |
| `repositories` | Repository metadata | URL, owner, name, local reference |
| `stage_results` | Per-stage lifecycle | stage, status, timestamps, error, warnings |
| `run_events` | Orchestration events | sequence, event_type, stage, message |
| `artifacts` | Artifact metadata | artifact_type, storage_type, content (JSONB), size |

### Indexes

- `runs`: run_id (unique), status
- `run_events`: (run_id, sequence) unique, (run_id, event_type)
- `stage_results`: (run_id, stage)
- `artifacts`: artifact_id, artifact_type, (run_id, artifact_type)

## PostgresRunStore

`PostgresRunStore` implements the `RunStore` Protocol with the following
capabilities:

### Protocol Methods

| Method | Description |
|--------|-------------|
| `create(run)` | Persist a new DevPilotRun (atomic: run + events + stages) |
| `get(run_id)` | Load full DevPilotRun from DB (deserializes nested data) |
| `update(run)` | Atomic update with optimistic concurrency (version field) |
| `list(status, limit, offset)` | Paginated listing with status filter |
| `delete(run_id)` | CASCADE delete of run + events + stages |
| `request_cancel(run_id)` | Set cancellation_requested flag (checks terminal status) |

### Phase 11 Extensions

| Method | Description |
|--------|-------------|
| `append_event(run_id, event)` | Append single event with monotonic sequence |
| `get_events(run_id, limit, offset)` | Paginated event retrieval (by sequence) |
| `save_stage_result(run_id, sr)` | Persist individual stage result |
| `get_stage_results(run_id)` | Retrieve all stage results for a run |
| `save_artifact(run_id, ...)` | Persist artifact metadata + content (JSONB) |
| `get_artifacts(run_id, type)` | Retrieve artifacts by run/type |
| `find_recoverable_runs()` | Find non-terminal runs for recovery |
| `mark_stale_runs(max_age)` | Mark old pending/running runs as FAILED |
| `count_runs(status)` | Count runs (optionally filtered by status) |

## Concurrency

### Optimistic Locking

Every `runs` row has a `version` integer field. Updates use:

```sql
UPDATE runs SET version = version + 1, ...
WHERE run_id = ? AND version = ?
```

If another process modified the row concurrently, the update affects 0 rows
and `ConcurrentRunUpdateError` is raised.

### Transactions

State transitions are atomic:
1. Run status/current_stage updated
2. RunEvent row inserted  
3. StageResult row inserted

All in a single database transaction. If any step fails, all changes roll back.

## Recovery & Resume

### Startup Recovery

On backend startup, `check_recovery()` scans for:
- Runs with status `PENDING` or `RUNNING`
- Marks runs older than 60 minutes as `FAILED` (stale)
- Returns list of recoverable run IDs

### Safe Resume

`resume_run(run_id)` checks:
1. Run exists and is non-terminal
2. No cancellation requested
3. Last completed stage is determined from stage_results
4. Execution continues from `current_stage`

## RunStore Contract Tests

A shared test suite (`test_run_store_contract.py`) validates the same
behavior against **both** `InMemoryRunStore` and `PostgresRunStore`:

```bash
# Run InMemory contract tests
pytest tests/test_run_store_contract.py -q

# Run PostgreSQL integration tests
pytest tests/test_run_store_contract.py -m integration -q
```

## API Endpoints Added

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/runs/{run_id}/resume` | POST | Resume an interrupted run |
| `/api/v1/orchestration/recovery` | POST | Check for recoverable runs |

## Frontend Integration

A centralized API client (`frontend/src/lib/api/client.ts`) provides:
- `runsApi.create()`, `runsApi.list()`, `runsApi.get()`
- `runsApi.cancel()`, `runsApi.resume()`, `runsApi.events()`
- `orchestrationApi.capabilities()`, `orchestrationApi.recovery()`

All frontend components should use this client rather than raw `fetch()`.

## Configuration

Requires `DATABASE_URL` in `.env`:

```env
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/devpilot_dev
TEST_DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/devpilot_test
```

When `DATABASE_URL` is not set, `InMemoryRunStore` is used (development/CI mode).

## Migrations

```bash
# Upgrade to latest
PYTHONPATH=backend alembic upgrade head

# Show current revision
PYTHONPATH=backend alembic current

# History
PYTHONPATH=backend alembic history

# Create new migration
PYTHONPATH=backend alembic revision --autogenerate -m "description"
```

## Limitations

1. Single-process backend — no distributed worker support yet
2. Polling-based frontend updates (no WebSockets/SSE)
3. Local PostgreSQL deployment only
4. Artifact storage limited to JSONB (no object storage)
5. No multi-user tenancy
6. No automatic cleanup/retention policies
