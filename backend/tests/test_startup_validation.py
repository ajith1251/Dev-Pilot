"""Tests for Phase 20B — startup configuration validation."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.startup_validation import validate_settings


def _make_settings(**overrides) -> SimpleNamespace:
    defaults = dict(
        LLM_PROVIDER="nvidia",
        PROVIDER_PRIORITY=[],
        LLM_PROVIDER_FALLBACKS={},
        PROVIDER_DISABLED=[],
        DATABASE_URL=None,
        GEMINI_TIER="free",
        GEMINI_API_KEY="gk-test",
        PROVIDER_HEALTH_DEGRADED_SUCCESS_RATE=0.5,
        PROVIDER_HEALTH_UNHEALTHY_SUCCESS_RATE=0.3,
        PROVIDER_ROUTING_ENABLED=True,
        NVIDIA_API_KEY="nv-test",
        STARTUP_VALIDATION_STRICT=False,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestStartupValidation:
    def test_healthy_config_has_no_findings(self) -> None:
        findings = validate_settings(_make_settings())
        assert findings == []

    def test_unknown_primary_provider_is_error(self) -> None:
        findings = validate_settings(_make_settings(LLM_PROVIDER="totally_fake"))
        errors = [f for f in findings if f["severity"] == "error"]
        assert any(f["code"] == "CONFIG_UNKNOWN_PROVIDER" for f in errors)

    def test_unknown_priority_provider_is_error(self) -> None:
        findings = validate_settings(_make_settings(
            PROVIDER_PRIORITY=["nvidia", "not_a_provider"]))
        errors = [f for f in findings if f["severity"] == "error"]
        assert any(f["code"] == "CONFIG_UNKNOWN_PRIORITY" for f in errors)

    def test_invalid_capability_is_error(self) -> None:
        findings = validate_settings(_make_settings(
            LLM_PROVIDER_FALLBACKS={"brainstorming": ["nvidia"]}))
        errors = [f for f in findings if f["severity"] == "error"]
        assert any(f["code"] == "CONFIG_INVALID_CAPABILITY" for f in errors)

    def test_unknown_fallback_provider_is_error(self) -> None:
        findings = validate_settings(_make_settings(
            LLM_PROVIDER_FALLBACKS={"coding": ["nvidia", "ghost"]}))
        errors = [f for f in findings if f["severity"] == "error"]
        assert any(f["code"] == "CONFIG_UNKNOWN_FALLBACK_PROVIDER" for f in errors)

    def test_disabled_in_priority_is_warning(self) -> None:
        findings = validate_settings(_make_settings(
            PROVIDER_PRIORITY=["nvidia", "gemini"],
            PROVIDER_DISABLED=["gemini"],
        ))
        warnings = [f for f in findings if f["severity"] == "warning"]
        assert any(f["code"] == "CONFIG_DISABLED_IN_PRIORITY" for f in warnings)

    def test_non_postgres_scheme_is_error(self) -> None:
        findings = validate_settings(_make_settings(
            DATABASE_URL="mysql://user:pass@localhost/db"))
        errors = [f for f in findings if f["severity"] == "error"]
        assert any(f["code"] == "CONFIG_DATABASE_SCHEME" for f in errors)

    def test_postgres_scheme_accepted(self) -> None:
        findings = validate_settings(_make_settings(
            DATABASE_URL="postgresql+asyncpg://user:pass@localhost/db"))
        errors = [f for f in findings if f["severity"] == "error"]
        assert not any(f["code"] == "CONFIG_DATABASE_SCHEME" for f in errors)

    def test_paid_tier_without_key_is_warning(self) -> None:
        findings = validate_settings(_make_settings(
            GEMINI_TIER="paid", GEMINI_API_KEY=None))
        warnings = [f for f in findings if f["severity"] == "warning"]
        assert any(f["code"] == "CONFIG_PAID_TIER_NO_KEY" for f in warnings)

    def test_inverted_health_thresholds_is_error(self) -> None:
        findings = validate_settings(_make_settings(
            PROVIDER_HEALTH_DEGRADED_SUCCESS_RATE=0.2,
            PROVIDER_HEALTH_UNHEALTHY_SUCCESS_RATE=0.5,
        ))
        errors = [f for f in findings if f["severity"] == "error"]
        assert any(f["code"] == "CONFIG_HEALTH_THRESHOLDS" for f in errors)

    def test_no_provider_keys_is_warning(self) -> None:
        # Null every provider availability attr so nothing is configured.
        no_keys = _make_settings(
            NVIDIA_API_KEY=None,
            GEMINI_API_KEY=None,
            CLOUDFLARE_API_KEY=None,
            OLLAMA_CLOUD_API_KEY=None,
            OPENCODE_ZEN_API_KEY=None,
            OPENAI_API_KEY=None,
            ANTHROPIC_API_KEY=None,
            OPENROUTER_API_KEY=None,
            OLLAMA_BASE_URL=None,
            OPENAI_COMPATIBLE_BASE_URL=None,
        )
        findings = validate_settings(no_keys)
        warnings = [f for f in findings if f["severity"] == "warning"]
        assert any(f["code"] == "CONFIG_NO_PROVIDER_KEYS" for f in warnings)

    def test_routing_disabled_skips_provider_warning(self) -> None:
        findings = validate_settings(_make_settings(
            PROVIDER_ROUTING_ENABLED=False, NVIDIA_API_KEY=None))
        warnings = [f for f in findings if f["severity"] == "warning"]
        assert not any(f["code"] == "CONFIG_NO_PROVIDER_KEYS" for f in warnings)

    def test_strict_mode_raises_on_errors(self) -> None:
        from app.core.startup_validation import run_startup_validation

        bad = _make_settings(
            LLM_PROVIDER="nope",
            STARTUP_VALIDATION_STRICT=True,
        )
        with pytest.raises(RuntimeError, match="Startup validation failed"):
            run_startup_validation(bad)

    def test_strict_mode_passes_clean_config(self) -> None:
        from app.core.startup_validation import run_startup_validation

        findings = run_startup_validation(_make_settings(STARTUP_VALIDATION_STRICT=True))
        assert findings == []
