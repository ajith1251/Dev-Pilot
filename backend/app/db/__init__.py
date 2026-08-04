"""Database infrastructure for DevPilot.

Provides async SQLAlchemy engine, connection pool, and lifecycle
management for PostgreSQL connectivity.

This module does NOT contain domain persistence models.
Phase 11 will add PostgresRunStore and schema management.
"""

from app.db.database import (
    check_database_connection,
    create_async_engine,
    dispose_engine,
    get_database_url,
    verify_database_config,
)

__all__ = [
    "check_database_connection",
    "create_async_engine",
    "dispose_engine",
    "get_database_url",
    "verify_database_config",
]
