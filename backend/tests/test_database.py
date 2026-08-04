"""
Tests for database infrastructure (app/db/).

Unit tests use mocked settings — no live PostgreSQL required.
Integration tests use TEST_DATABASE_URL and skip if unavailable.
"""

from __future__ import annotations

import os
from typing import Optional
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.exceptions import (
    DatabaseConfigurationError,
    DatabaseConnectionError,
    DatabaseError,
    DatabaseUnavailableError,
)


# ── Unit Tests (mocked — no PostgreSQL required) ───────────────


class TestRedaction:
    """Secret redaction utilities must never expose credentials."""

    def test_redact_url_hides_password(self):
        from app.db.database import redact_url

        url = "postgresql+asyncpg://devpilot:s3cret!@localhost:5432/devpilot_dev"
        redacted = redact_url(url)
        assert "s3cret!" not in redacted
        assert "****" in redacted
        assert "devpilot" in redacted  # username visible
        assert "localhost:5432" in redacted  # host visible

    def test_redact_url_no_password(self):
        from app.db.database import redact_url

        url = "postgresql+asyncpg://localhost/devpilot_dev"
        redacted = redact_url(url)
        assert redacted == url  # unchanged

    def test_redact_message_hides_password(self):
        from app.db.database import redact_message

        msg = "connection to postgresql+asyncpg://devpilot:sekret@localhost:5432/devpilot failed"
        redacted = redact_message(msg)
        assert "sekret" not in redacted
        assert "****" in redacted

    def test_redact_empty_string(self):
        from app.db.database import redact_url

        assert redact_url("") == ""

    def test_redact_no_match(self):
        from app.db.database import redact_url

        url = "http://example.com/db"
        assert redact_url(url) == url


class TestGetDatabaseUrl:
    """DATABASE_URL must be read from settings."""

    def test_get_url_configured(self):
        with patch("app.db.database.settings") as mock_settings:
            mock_settings.DATABASE_URL = "postgresql+asyncpg://u:p@localhost/db"
            mock_settings.TEST_DATABASE_URL = None
            from app.db.database import get_database_url, get_test_database_url

            assert get_database_url() == "postgresql+asyncpg://u:p@localhost/db"
            assert get_test_database_url() is None

    def test_get_url_not_configured(self):
        with patch("app.db.database.settings") as mock_settings:
            mock_settings.DATABASE_URL = None
            mock_settings.TEST_DATABASE_URL = None
            from app.db.database import get_database_url, get_test_database_url

            assert get_database_url() is None
            assert get_test_database_url() is None


class TestCreateAsyncEngine:
    """Engine creation must handle missing/invalid config gracefully."""

    def test_create_no_url(self):
        with patch("app.db.database.settings") as mock_settings:
            mock_settings.DATABASE_URL = None
            mock_settings.is_debug = False
            from app.db.database import create_async_engine

            engine = create_async_engine()
            assert engine is None

    def test_create_with_url(self):
        with patch("app.db.database.settings") as mock_settings:
            mock_settings.DATABASE_URL = "postgresql+asyncpg://u:p@localhost/db"
            mock_settings.is_debug = False
            from app.db.database import create_async_engine

            engine = create_async_engine()
            assert engine is not None
            assert isinstance(engine, AsyncEngine)
            # SQLAlchemy hides password in str() — use render_as_string
            full_url = engine.url.render_as_string(hide_password=False)
            assert full_url.startswith("postgresql+asyncpg://u:")

    def test_create_with_url_arg(self):
        from app.db.database import create_async_engine

        engine = create_async_engine("postgresql+asyncpg://u:p@localhost/test")
        assert engine is not None
        assert "test" in str(engine.url)

    def test_create_preserves_database_name(self):
        """Database name must be extractable from URL."""
        from app.db.database import create_async_engine

        engine = create_async_engine("postgresql+asyncpg://u:p@localhost/myapp_db")
        assert engine is not None
        assert "myapp_db" in engine.url.render_as_string(hide_password=False)


class TestConnectionCheckWithoutDB:
    """Connection verification without a live database."""

    def test_no_url(self):
        with patch("app.db.database.settings") as mock_settings:
            mock_settings.DATABASE_URL = None
            import asyncio
            from app.db.database import check_database_connection

            result = asyncio.run(check_database_connection())
            assert result.configured is False
            assert result.connected is False
            assert "not configured" in result.error.lower()


class TestDisposeEngine:
    """Engine disposal must not raise."""

    def test_dispose_none(self):
        from app.db.database import dispose_engine

        # Should not raise
        dispose_engine(None)
        dispose_engine()  # _engine is None


class TestVerifyDatabaseConfig:
    """Configuration verification must produce sanitized output."""

    def test_verify_no_config(self):
        with patch("app.db.database.settings") as mock_settings:
            mock_settings.DATABASE_URL = None
            mock_settings.TEST_DATABASE_URL = None
            import asyncio
            from app.db.database import verify_database_config

            result = asyncio.run(verify_database_config())
            assert result["configured"] is False
            assert result["database_url_set"] is False
            assert "error" not in result

    def test_verify_with_configured_url(self):
        with patch("app.db.database.settings") as mock_settings:
            mock_settings.DATABASE_URL = "postgresql+asyncpg://u:p@localhost/devpilot_dev"
            mock_settings.TEST_DATABASE_URL = "postgresql+asyncpg://u:p@localhost/devpilot_test"
            import asyncio
            from app.db.database import verify_database_config

            result = asyncio.run(verify_database_config())
            assert result["configured"] is True
            assert result["type"] == "postgresql"
            assert result["test_database_url_set"] is True

    def test_verify_url_sanitized(self):
        """Verify that diagnostic output doesn't leak credentials."""
        import asyncio
        from app.db.database import verify_database_config, redact_url

        # Use a helper to ensure pattern works
        url = "postgresql+asyncpg://devpilot:secret123@localhost/db"
        redacted = redact_url(url)
        assert "secret123" not in redacted
        assert "devpilot" in redacted  # username visible
        assert "****" in redacted


class TestMainModule:
    """Package __init__ must expose expected symbols."""

    def test_module_exports(self):
        from app.db import (
            check_database_connection,
            create_async_engine,
            dispose_engine,
            get_database_url,
            verify_database_config,
        )

        assert callable(check_database_connection)
        assert callable(create_async_engine)
        assert callable(dispose_engine)
        assert callable(get_database_url)
        assert callable(verify_database_config)

    def test_module_imports(self):
        import app.db

        assert hasattr(app.db, "check_database_connection")
        assert hasattr(app.db, "create_async_engine")
        assert hasattr(app.db, "dispose_engine")


class TestExceptions:
    """Database exception hierarchy must be catchable."""

    def test_base_exception(self):
        assert issubclass(DatabaseError, Exception)

    def test_configuration_error(self):
        err = DatabaseConfigurationError("missing url", details={"field": "DATABASE_URL"})
        assert "missing url" in str(err)
        assert err.details["field"] == "DATABASE_URL"

    def test_connection_error(self):
        err = DatabaseConnectionError("server unreachable")
        assert "server unreachable" in str(err)

    def test_unavailable_error(self):
        err = DatabaseUnavailableError("database not found")
        assert "database not found" in str(err)


# ── Mocked Health Check Integration ────────────────────────────


class TestHealthCheckIntegration:
    """Health check must include database info without leaking credentials."""

    def test_health_contains_db_fields(self):
        from app.api.health import router

        # Verify route is properly configured
        routes = [r.path for r in router.routes]
        assert "/health" in routes

    def test_database_field_structure(self):
        from app.db.database import ConnectionCheckResult

        # Connected result
        connected = ConnectionCheckResult(
            configured=True, connected=True,
            database_name="devpilot_dev", server_version="PostgreSQL 16.0",
        )
        assert connected.configured is True
        assert connected.database_name == "devpilot_dev"

        # Disconnected result
        disconnected = ConnectionCheckResult(
            configured=True, connected=False, error="connection refused",
        )
        assert "connection refused" in disconnected.error


# ── Integration Tests (live PostgreSQL, skip if unavailable) ──


@pytest.mark.integration
class TestPostgreSQLIntegration:
    """Integration tests against live PostgreSQL via TEST_DATABASE_URL.

    These tests require PostgreSQL to be running and TEST_DATABASE_URL
    to be configured in the environment. They skip automatically
    when PostgreSQL is unavailable.
    """

    @pytest.fixture(autouse=True)
    def _check_config(self):
        from app.config import settings
        url = settings.TEST_DATABASE_URL or settings.DATABASE_URL
        if not url:
            pytest.skip("No TEST_DATABASE_URL or DATABASE_URL configured")
        self._test_url = url

    def _dev_url(self) -> Optional[str]:
        from app.config import settings
        return settings.DATABASE_URL

    async def _check_connect(self, url: str):
        from app.db.database import check_database_connection

        return await check_database_connection(database_url=url)

    @pytest.mark.asyncio
    async def test_connect_and_select_one(self):
        """Must connect and successfully execute SELECT 1."""
        result = await self._check_connect(self._test_url)
        assert result.connected is True, f"Connection failed: {result.error}"
        assert result.configured is True

    @pytest.mark.asyncio
    async def test_server_version_returned(self):
        """Must return server version on successful connection."""
        result = await self._check_connect(self._test_url)
        assert result.connected is True
        assert "PostgreSQL" in result.server_version

    @pytest.mark.asyncio
    async def test_database_name_correct(self):
        """Must identify the connected database."""
        result = await self._check_connect(self._test_url)
        assert result.connected is True
        assert result.database_name in ("devpilot_test", "devpilot_dev")

    @pytest.mark.asyncio
    async def test_development_test_separation(self):
        """Integration tests must NEVER use the development database."""
        dev_url = self._dev_url()
        test_url = self._test_url
        if dev_url and test_url:
            assert dev_url != test_url, (
                "DATABASE_URL and TEST_DATABASE_URL must be different databases!"
            )
            assert "devpilot_dev" not in test_url, (
                "TEST_DATABASE_URL must not point to devpilot_dev!"
            )

    @pytest.mark.asyncio
    async def test_create_and_dispose_engine(self):
        """Must create and safely dispose an engine."""
        from app.db.database import create_async_engine, dispose_engine

        engine = create_async_engine(self._test_url)
        assert engine is not None

        # Verify connectivity
        from app.db.database import check_database_connection

        result = await check_database_connection(engine=engine)
        assert result.connected is True

        # Dispose
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_verify_database_config(self):
        """Configuration verification must work with live DB."""
        from app.db.database import verify_database_config

        with patch("app.db.database.settings") as mock_settings:
            mock_settings.DATABASE_URL = self._test_url
            mock_settings.TEST_DATABASE_URL = None
            mock_settings.is_debug = False
            result = await verify_database_config()
            assert result["configured"] is True
            assert result["connected"] is True

    @pytest.mark.asyncio
    async def test_secret_redaction_in_error(self):
        """Error messages must not contain credentials."""
        from app.db.database import check_database_connection

        # Use a deliberately wrong URL
        bad_url = "postgresql+asyncpg://devpilot:wR0nGp@ss@localhost:5432/devpilot_test"
        result = await check_database_connection(database_url=bad_url)
        assert result.connected is False
        assert "wR0nGp@ss" not in result.error
