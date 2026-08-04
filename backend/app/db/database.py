"""
Async SQLAlchemy database infrastructure for DevPilot.

Provides:
- Async engine creation with configurable connection pooling
- Safe engine disposal
- Connectivity verification (SELECT 1)
- Configuration validation
- Secret redaction for logs/errors
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine as _sa_create_async_engine

from app.config import settings
from app.core.logging import logger

# ── Redaction ───────────────────────────────────────────────────

# Pattern to redact passwords from connection strings
_PASSWORD_PATTERN = re.compile(r"(://[^:]+):([^@]+)@")


def redact_url(url: str) -> str:
    """Redact the password portion of a database URL."""
    return _PASSWORD_PATTERN.sub(r"\1:****@", url)


def redact_message(message: str) -> str:
    """Redact any credentials that might appear in an error message."""
    return _PASSWORD_PATTERN.sub(r"\1:****@", message)


# ── Engine Lifecycle ───────────────────────────────────────────

_engine: Optional[AsyncEngine] = None
"""Module-level async engine singleton."""


def get_database_url() -> Optional[str]:
    """Return the configured DATABASE_URL or None."""
    return settings.DATABASE_URL


def get_test_database_url() -> Optional[str]:
    """Return the configured TEST_DATABASE_URL or None."""
    return settings.TEST_DATABASE_URL


def create_async_engine(database_url: str | None = None) -> Optional[AsyncEngine]:
    """Create and return a SQLAlchemy AsyncEngine.

    Args:
        database_url: Connection string. Falls back to settings.DATABASE_URL.

    Returns:
        AsyncEngine instance, or None if no URL is configured.
    """
    url = database_url or get_database_url()
    if not url:
        logger.warning("DATABASE_URL not configured — skipping engine creation")
        return None

    safe_url = redact_url(url)
    logger.info("Creating async engine: %s", safe_url)

    engine = _sa_create_async_engine(
        url,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=3600,
        echo=settings.is_debug,
        connect_args={
            "timeout": 10,
            "command_timeout": 30,
        },
    )
    return engine


def dispose_engine(engine: Optional[AsyncEngine] = None) -> None:
    """Safely dispose of an async engine.

    During FastAPI shutdown, disposal is scheduled as a background task
    on the running event loop (fire-and-forget). Without a running loop,
    a temporary event loop is created.

    Args:
        engine: Engine to dispose. Falls back to module-level engine.
    """
    target = engine if engine is not None else _engine
    if target is None:
        return
    try:
        import asyncio

        loop = asyncio.get_running_loop()
        loop.create_task(target.dispose())
        logger.debug("Engine disposal scheduled via running event loop (fire-and-forget)")
        return
    except RuntimeError:
        # No running loop — dispose in a new event loop
        pass

    try:
        async def _dispose():
            await target.dispose()

        asyncio.run(_dispose())
        logger.info("Engine disposed")
    except Exception as exc:
        logger.warning("Engine disposal encountered an issue: %s", redact_message(str(exc)))


# ── Connectivity Verification ──────────────────────────────────


@dataclass
class ConnectionCheckResult:
    """Result of a database connection check."""

    configured: bool
    connected: bool
    database_name: str = ""
    error: str = ""
    server_version: str = ""


async def check_database_connection(
    engine: Optional[AsyncEngine] = None,
    database_url: str | None = None,
) -> ConnectionCheckResult:
    """Verify database connectivity by executing SELECT 1.

    Args:
        engine: Existing engine to use. If None, creates a temporary engine.
        database_url: Connection string (required if no engine provided).

    Returns:
        ConnectionCheckResult with status details.
    """
    url = database_url or get_database_url()
    if not url:
        return ConnectionCheckResult(
            configured=False,
            connected=False,
            error="DATABASE_URL not configured",
        )

    temp_engine = None
    try:
        if engine is None:
            temp_engine = create_async_engine(url)
            engine = temp_engine

        if engine is None:
            return ConnectionCheckResult(
                configured=True,
                connected=False,
                error="Failed to create engine",
            )

        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            row = result.scalar_one()

            # Get server version
            try:
                version_result = await conn.execute(text("SELECT version()"))
                server_version = version_result.scalar() or ""
                # Truncate to first segment for brevity
                server_version = server_version.split(",")[0].strip()
            except Exception:
                server_version = ""

            # Extract database name from URL
            db_name = url.rstrip("/").rsplit("/", 1)[-1] if "/" in url else ""
            # Remove query params
            db_name = db_name.split("?")[0]

            return ConnectionCheckResult(
                configured=True,
                connected=row == 1,
                database_name=db_name,
                server_version=server_version,
            )

    except Exception as exc:
        safe_msg = redact_message(str(exc))
        logger.error("Database connection check failed: %s", safe_msg)
        return ConnectionCheckResult(
            configured=True,
            connected=False,
            error=safe_msg[:200],
        )

    finally:
        if temp_engine is not None:
            await temp_engine.dispose()


async def verify_database_config() -> dict:
    """Verify database configuration and return a diagnostic dict.

    Returns:
        Dict with configuration status, safe for API exposure.
    """
    url = get_database_url()
    test_url = get_test_database_url()

    result = {
        "configured": url is not None,
        "type": "postgresql" if url and "+asyncpg" in url else "unknown",
        "database_url_set": url is not None,
        "test_database_url_set": test_url is not None,
    }

    if url:
        # Check dev database
        check = await check_database_connection(database_url=url)
        result["connected"] = check.connected
        result["database"] = check.database_name
        result["server_version"] = check.server_version
        if not check.connected:
            result["error"] = check.error[:100]

    return result
