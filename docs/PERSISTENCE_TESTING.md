# Persistence Testing — Phase 11H

This document describes the automated testing strategy for DevPilot's PostgreSQL persistence layer implemented in Phase 11 and hardened in Phase 11H.

## Test Strategy Overview

```
Phase 11H Tests
├── Migration Tests              (test_migration.py)
├── RunStore Contract Tests      (test_run_store_contract.py)
├── Recovery Hardening Tests     (test_recovery_hardening.py)
├── API Contract Tests           (test_api_contract.py)
└── Full Regression Suite        (697+ baseline)
```

## 1. Migration Tests (`test_migration.py`)

### Non-Destructive Checks
These tests inspect migration files on disk without touching the database:
- **Revision graph validity** — Verifies all revisions have valid parent references
- **Single head** — Confirms exactly one Alembic head revision
- **Filename consistency** — Migration filenames match revision IDs, each has `upgrade()` and `downgrade()`

### Destructive Round-Trip (requires test database)
These tests run against an isolated test database:
- **Empty → upgrade head** — Verifies all 6 expected tables are created
- **Upgrade → downgrade → upgrade** — Full round-trip to ensure reversibility
- **Schema invariants** — Expected columns, foreign keys, and indexes on `runs`, `run_events`, `stage_results`
- **Idempotent upgrade** — Running `alembic upgrade head` twice produces the same schema
- **Expected indexes** — Composite indexes on `(run_id_fk, sequence)` and `(run_id_fk, stage)` exist

### Database Safety
All destructive operations verify the database name contains "test" and refuse to run against production databases.

## 2. RunStore Contract Tests (`test_run_store_contract.py`)

A shared behavioral contract tested against both implementations:

| Test | InMemoryRunStore | PostgresRunStore |
|------|:-:|:-:|
| Create + Get | ✓ | ✓ |
| Get nonexistent | ✓ | ✓ |
| Update | ✓ | ✓ |
| Delete | ✓ | ✓ |
| List empty | ✓ | — |
| List multiple | ✓ | ✓ |
| Status filter | ✓ | ✓ |
| Pagination | ✓ | ✓ |
| Reverse chronological order | ✓ | — |
| Cancel running | ✓ | ✓ |
| Cancel terminal fails | ✓ | ✓ |
| Cancel nonexistent | ✓ | — |
| Events persisted | ✓ | ✓ |
| Event ordering | ✓ | — |
| Stage results | ✓ | ✓ |
| Warnings | ✓ | ✓ |
| Failure persisted | ✓ | ✓ |
| Failure cleared | ✓ | — |
| Timestamps | ✓ | — |
| Source fields | ✓ | ✓ |
| Generate run ID | ✓ | — |
| GitHub source fields | — | ✓ |
| Append event | — | ✓ |
| Get events | — | ✓ |
| Save/get stage results | — | ✓ |
| Save/get artifacts | — | ✓ |
| Find recoverable runs | — | ✓ |
| Count runs | — | ✓ |

## 3. Recovery Hardening Tests (`test_recovery_hardening.py`)

### Crash Injection Framework (`FaultInjector`)

A test-only singleton for deterministic fault simulation:
```python
FaultInjector.inject_after("coding")     # Crash after coding stage
FaultInjector.inject_before("testing")    # Crash before testing stage
FaultInjector.should_crash("coding")     # Check if crash should occur
FaultInjector.was_injected("coding")     # Verify injection was triggered
```

Valid injection points match the stage lifecycle:
- `after run creation` / `before analysis`
- `after analysis` / `before planning`
- `after planning` / `before retrieval`
- `after retrieval` / `before coding`
- `after coding` / `before patch validation`
- `after patch validation` / `before patch application`
- `after patch application` / `before testing`
- `during repair`
- `after review` / `before quality gate persistence`
- `after final state persistence`

### Recovery Verification
- Running runs with completed stages are recoverable
- Completed work is preserved after simulated interruption
- Terminal runs (approved, rejected, failed, cancelled) are not resumable
- Event history is preserved after interruption

### Idempotency Testing
- Create same run ID twice is safe
- Update same state multiple times is idempotent
- Append same event reference doesn't duplicate
- Cancel on already cancelled run is safe

### Transaction Fault Testing
- Multi-step operations roll back atomically on failure
- Events and stage results persist consistently
- Concurrent updates preserve all data

### Optimistic Concurrency Stress
- 20 concurrent read-write workers exercise thread-safe access
- All warnings preserved with concurrent appends

### Event Ordering Stress
- Events maintain append order
- No duplicate event IDs in stored state

### State Machine Fuzzing
All common transition sequences verified:
- 15 valid transitions accepted
- 7 common invalid transitions rejected
- Terminal states remain terminal (no transitions from COMPLETED, FAILED, CANCELLED)
- Linear `next_stage()` returns expected next stage

### Database Outage Safety
Graceful behavior when database is unavailable:
- Missing run returns `None` (not a crash)
- Empty database returns empty list
- Deleting nonexistent run returns `False`

## 4. API Contract Tests (`test_api_contract.py`)

### Status Codes
- Health endpoint returns 200 with `success: true`
- Run listing returns 200 or handles errors gracefully
- Nonexistent run returns 404
- Capabilities endpoint returns 200 with valid structure

### Pagination
- `limit` parameter caps results
- `offset` parameter works correctly
- `status` filter parameter returns filtered results
- Large limit values don't crash

### Security Boundaries
- 1000-character run IDs handled safely
- SQL-like strings treated as literal (safe error)
- Unicode run IDs accepted gracefully
- Negative pagination values don't crash
- Invalid enum status values handled
- HTML injection attempts sanitized
- Malformed UUIDs don't crash

### Schema Validation
- Run list response has `success`, `data`, `count`
- Run summaries have `run_id`, `status`, `title`, `source`, `current_stage`, `created_at`
- Status values match valid enum set
- Capabilities response has `stages`, `cancellation_mode`, `persistence_mode`

## Running Tests

```bash
# Full backend suite
cd backend && python -m pytest

# Specific test files
python -m pytest tests/test_migration.py
python -m pytest tests/test_recovery_hardening.py
python -m pytest tests/test_api_contract.py
python -m pytest tests/test_run_store_contract.py

# Integration tests only (require PostgresRunStore)
python -m pytest tests/test_run_store_contract.py -m integration

# Migration tests (require test database)
python -m pytest tests/test_migration.py

# CLI verification
python -m app.cli verify-persistence
python -m app.cli verify
```

## CI Integration

See `.github/workflows/test.yml` for the CI pipeline configuration.

The CI quality gate requires:
1. All existing tests pass
2. Phase 11H hardening tests pass
3. Frontend build passes
4. Migration verification passes

## Future Work

- **Hypothesis property-based tests** — Generate valid/invalid combinations of run states, stage states, and event sequences
- **Testcontainers** — Isolated PostgreSQL containers for integration tests
- **Performance sanity** — Seed database with 1,000+ runs and measure query times
- **Frontend E2E with Playwright** — Browser-level integration tests
- **Secret canary** — Verify credentials never leak through error paths
