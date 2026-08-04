"""
Phase 11 — Async database session management.

Provides:
- async_sessionmaker-based session factory
- get_session() context manager for FastAPI dependencies
- run_in_session() helper for running async functions with a session
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Callable, Optional, TypeVar

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from app.core.logging import logger
from app.db.database import _engine, create_async_engine, redact_url

T = TypeVar("T")


def create_session_factory(engine: Optional[AsyncEngine] = None) -> async_sessionmaker[AsyncSession]:
    """Create an async session factory.

    Args:
        engine: Optional engine. Falls back to module-level engine.

    Returns:
        async_sessionmaker bound to the given engine.
    """
    target = engine if engine is not None else _engine
    if target is None:
        # Create a temporary engine from settings
        from app.config import settings
        if not settings.DATABASE_URL:
            raise RuntimeError("DATABASE_URL not configured — cannot create session factory")
        target = create_async_engine(settings.DATABASE_URL)
        if target is None:
            raise RuntimeError("Failed to create database engine")

    return async_sessionmaker(
        target,
        class_=AsyncSession,
        expire_on_commit=False,
    )


@asynccontextmanager
async def get_session(
    session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
) -> AsyncIterator[AsyncSession]:
    """Get an async database session as a context manager.

    Usage:
        async with get_session() as session:
            result = await session.execute(...)

    Args:
        session_factory: Optional pre-configured factory.
    """
    factory = session_factory or create_session_factory()
    async with factory() as session:
        try:
            yield session
        finally:
            await session.close()


async def run_in_session(
    callback: Callable[[AsyncSession], T],
    session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
) -> T:
    """Run a callback with a session provided.

    Args:
        callback: Async callable that receives an AsyncSession.
        session_factory: Optional session factory.

    Returns:
        Result of the callback.
    """
    factory = session_factory or create_session_factory()
    async with factory() as session:
        result = await callback(session)
        await session.commit()
        return result
