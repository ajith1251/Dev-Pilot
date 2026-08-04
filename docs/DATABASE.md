# DevPilot PostgreSQL Database

> **Status**: Infrastructure Ready ✅
> **Phase**: Phase 11 Preparation (persistence not yet implemented)

---

## 1. Overview

DevPilot uses **PostgreSQL** via **SQLAlchemy 2.x** (async) + **asyncpg** for database connectivity. This document covers the database infrastructure, setup, and usage.

### Architecture

```
DevPilot Application
    ↓
app/db/database.py (async engine, connection pool, verification)
    ↓
SQLAlchemy 2.x AsyncEngine
    ↓
Connection Pool (pool_size=5, max_overflow=10)
    ↓
asyncpg
    ↓
PostgreSQL (localhost:5432)
```

### Target Databases

| Database | Purpose |
|----------|---------|
| `devpilot_dev` | Development database |
| `devpilot_test` | Integration test database (isolated from dev) |

### Application Role

| Role | Purpose |
|------|---------|
| `devpilot` | Application database role (not superuser) |

---

## 2. Quick Start

### Prerequisites

- PostgreSQL 16+ installed and running on `localhost:5432` (project standard is 18.4 — docker-compose and CI use `postgres:18.4`)
- `psql` command-line tool available

### Setup

1. **Create the application role** (from `psql` as superuser):
   ```sql
   CREATE ROLE devpilot WITH LOGIN PASSWORD '<your-password>' CREATEDB;
   ```

2. **Create the databases**:
   ```sql
   CREATE DATABASE devpilot_dev OWNER devpilot;
   CREATE DATABASE devpilot_test OWNER devpilot;
   ```

3. **Configure environment** — copy `.env.example` to `.env` and set:
   ```env
   DATABASE_URL=postgresql+asyncpg://devpilot:<password>@localhost:5432/devpilot_dev
   TEST_DATABASE_URL=postgresql+asyncpg://devpilot:<password>@localhost:5432/devpilot_test
   ```

4. **Verify connectivity**:
   ```bash
   python -m app.cli db-check
   ```

---

## 3. Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | Connection string for development database |
| `TEST_DATABASE_URL` | No | Connection string for integration tests (must differ from DATABASE_URL) |

### Connection String Format

```
postgresql+asyncpg://<user>:<password>@<host>:<port>/<database>
```

**Examples:**
```
# Local development
DATABASE_URL=postgresql+asyncpg://devpilot:secret@localhost:5432/devpilot_dev

# Integration tests (must be a different database!)
TEST_DATABASE_URL=postgresql+asyncpg://devpilot:secret@localhost:5432/devpilot_test
```

### Security

- Never hard-code credentials in source code
- Store credentials in `.env` (gitignored)
- `.env.example` contains placeholder values only
- Passwords are redacted from logs, errors, and API responses
- The `devpilot` role has only the permissions necessary for application operation

---

## 4. Database Infrastructure

### Module: `app/db/database.py`

| Function | Description |
|----------|-------------|
| `create_async_engine(url=None)` | Create SQLAlchemy AsyncEngine with connection pool |
| `dispose_engine(engine=None)` | Safely dispose an engine (handles running loop) |
| `check_database_connection(engine=None, url=None)` | Execute `SELECT 1` and return connectivity status |
| `verify_database_config()` | Return sanitized diagnostic dict |
| `redact_url(url)` | Redact password from connection string for safe display |
| `redact_message(msg)` | Redact credentials from error messages |

### Connection Pool

| Parameter | Value | Description |
|-----------|-------|-------------|
| `pool_size` | 5 | Minimum connections in pool |
| `max_overflow` | 10 | Extra connections beyond pool_size (max total = 15) |
| `pool_pre_ping` | True | Verify connection before use |
| `pool_recycle` | 3600 | Recycle connections after 1 hour |
| `connect_args.timeout` | 10 | Connection timeout (seconds) |
| `connect_args.command_timeout` | 30 | Query timeout (seconds) |

### FastAPI Lifecycle

The database engine is initialized on application startup and disposed on shutdown:

- **Startup**: `create_async_engine()` → stored in `app.state.db_engine` and module-level `_engine`
- **Shutdown**: `dispose_engine(app.state.db_engine)` → safely closes all connections

---

## 5. Health Check

Database connectivity is exposed through the health endpoint at `GET /health`:

```json
{
  "success": true,
  "data": {
    "database": {
      "type": "postgresql",
      "configured": true,
      "connected": true,
      "database": "devpilot_dev",
      "server_version": "PostgreSQL 16.0 (Debian ...)"
    }
  }
}
```

**Never exposed:**
- Passwords or full connection URLs
- Credentials in error messages
- Internal connection pool details

---

## 6. CLI Diagnostic

```bash
python -m app.cli db-check
```

Example output:

```
============================================================
  DevPilot Database Check
============================================================

  DATABASE_URL:    postgresql+asyncpg://devpilot:****@localhost:5432/devpilot_dev
  TEST_DATABASE_URL: postgresql+asyncpg://devpilot:****@localhost:5432/devpilot_test

  Configuration: OK
  Server:        Reachable
  Database:      devpilot_dev
  Version:       PostgreSQL 16.0
  Connection:    OK
  SELECT 1:      OK

============================================================
```

---

## 7. Error Handling

### Exception Hierarchy

| Exception | When Raised |
|-----------|-------------|
| `DatabaseError` | Base database exception |
| `DatabaseConfigurationError` | Missing/invalid configuration |
| `DatabaseConnectionError` | Server unreachable |
| `DatabaseUnavailableError` | Database not available |

All database exceptions include sanitized messages (credentials redacted).

### Error Redaction

The `redact_message()` function ensures credentials never appear in:
- Log messages
- API responses
- CLI output
- Exception messages
- Serialized configuration

---

## 8. Testing

### Unit Tests (mocked)

Run without any PostgreSQL dependency:

```bash
python -m pytest tests/test_database.py -k "not integration"
# Expected: 22 passed
```

Covers:
- URL redaction (passwords hidden)
- Engine creation (with/without URL)
- Connection check (without database)
- Engine disposal
- Configuration verification
- Exception hierarchy
- Health check integration

### Integration Tests (live PostgreSQL)

Require `TEST_DATABASE_URL` to be configured:

```bash
python -m pytest tests/test_database.py -k "integration"
# Expected: 7 passed (if PostgreSQL is configured)
```

Covers:
- `SELECT 1` execution
- Server version detection
- Database name identification
- Dev/test database separation (REQUIRED check)
- Engine create → connect → dispose cycle
- Configuration verification against live DB
- Secret redaction in error messages

### Database Separation Safeguard

Integration tests include a **mandatory check** that `TEST_DATABASE_URL` points to a different database than `DATABASE_URL`. If both are set to the same database, the test explicitly fails:

```python
assert dev_url != test_url, "DATABASE_URL and TEST_DATABASE_URL must be different!"
assert "devpilot_dev" not in test_url, "TEST_DATABASE_URL must not point to devpilot_dev!"
```

---

## 9. Future Production Migration

The current architecture supports moving from local PostgreSQL to managed production PostgreSQL with **configuration changes only** (no application code changes):

| Today | Future Production |
|-------|-------------------|
| `localhost:5432` | Managed endpoint (e.g., RDS, Cloud SQL) |
| Connection string in `.env` | Connection string in secrets manager |
| In-memory RunStore | `PostgresRunStore` |
| Single process | Multi-process (pool isolated per worker) |

### What Will Change

1. `DATABASE_URL` — points to managed PostgreSQL endpoint
2. `PostgresRunStore` — replaces `InMemoryRunStore` (Phase 11)
3. Connection pool tuning — may need adjustment for production load
4. SSL/TLS — add `?sslmode=require` to connection string

### What Will NOT Change

- Application code (`app/db/`, models, services)
- SQLAlchemy async patterns
- Connection verification logic
- Health check endpoints

---

## 10. Phase 11 Boundary

This infrastructure does **NOT** implement:

- `PostgresRunStore` (database-backed RunStore)
- Domain persistence models (runs, events, stage results)
- Alembic migrations
- Schema management
- Run recovery / resumption

Phase 11 will add:

```text
PostgreSQL schema (tables for runs, events, stage_results, etc.)
    ↓
Alembic migrations (version-controlled schema evolution)
    ↓
PostgresRunStore (implements RunStore Protocol)
    ↓
Durable runs, events, and artifacts
    ↓
Run recovery and resumption
    ↓
FastAPI integration
```
