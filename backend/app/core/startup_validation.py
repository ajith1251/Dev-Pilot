"""
Startup Configuration Validation — Phase 20B.

Runs before/at startup and reports configuration problems as structured
findings so misconfiguration is caught fast with clear diagnostics instead
of surfacing as a confusing runtime error hours into a run.

Checks (all deterministic, no network):

- ``LLM_PROVIDER`` is a registered provider
- ``DEVPILOT_PROVIDER_PRIORITY`` / ``DEVPILOT_LLM_PROVIDER_FALLBACKS`` /
  ``DEVPILOT_PROVIDER_DISABLED`` reference only registered provider names
- fallback capability keys are valid ``Capability`` values
- ``DATABASE_URL`` uses a PostgreSQL scheme when set
- ``GEMINI_TIER=paid`` implies ``GEMINI_API_KEY`` is set
- health thresholds are coherent (degraded rate > unhealthy rate)
- disabled providers are not also primary in the priority list
- routing is enabled and at least one provider is configured (warning)

When ``DEVPILOT_STARTUP_VALIDATION_STRICT`` is True, errors raise at startup;
otherwise findings are logged and exposed via
``GET /api/v1/operations/startup-validation`` and the CLI ``validate-config``.
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.config import settings as _default_settings
from app.core.logging import logger


def validate_settings(settings: Any = None) -> List[Dict[str, Any]]:
    """Validate the given settings (defaults to the global singleton).

    Returns a list of findings: {severity: error|warning, code, message}.
    """
    s = settings if settings is not None else _default_settings
    findings: List[Dict[str, Any]] = []

    from app.llm.provider_registry import get_spec, provider_names

    registered = set(provider_names())

    # 1. Primary LLM provider must be registered.
    primary = (getattr(s, "LLM_PROVIDER", "") or "").strip().lower()
    if primary and primary not in registered:
        findings.append({
            "severity": "error",
            "code": "CONFIG_UNKNOWN_PROVIDER",
            "message": (
                f"DEVPILOT_LLM_PROVIDER={primary!r} is not a registered provider. "
                f"Registered: {', '.join(sorted(registered))}"
            ),
        })

    # 2. Priority list entries must be registered.
    for name in (getattr(s, "PROVIDER_PRIORITY", None) or []):
        if str(name).strip().lower() not in registered:
            findings.append({
                "severity": "error",
                "code": "CONFIG_UNKNOWN_PRIORITY",
                "message": (
                    f"DEVPILOT_PROVIDER_PRIORITY references unknown provider "
                    f"{name!r}. Registered: {', '.join(sorted(registered))}"
                ),
            })

    # 3. Fallback capability keys + provider names must be valid.
    from app.llm.router import Capability

    valid_caps = set(Capability.names())
    for cap, names in (getattr(s, "LLM_PROVIDER_FALLBACKS", None) or {}).items():
        if str(cap).strip().lower() not in valid_caps:
            findings.append({
                "severity": "error",
                "code": "CONFIG_INVALID_CAPABILITY",
                "message": (
                    f"DEVPILOT_LLM_PROVIDER_FALLBACKS uses unknown capability "
                    f"{cap!r}. Valid: {', '.join(sorted(valid_caps))}"
                ),
            })
        for name in names:
            if str(name).strip().lower() not in registered:
                findings.append({
                    "severity": "error",
                    "code": "CONFIG_UNKNOWN_FALLBACK_PROVIDER",
                    "message": (
                        f"DEVPILOT_LLM_PROVIDER_FALLBACKS[{cap}] references "
                        f"unknown provider {name!r}."
                    ),
                })

    # 4. Disabled providers must be registered.
    for name in (getattr(s, "PROVIDER_DISABLED", None) or []):
        if str(name).strip().lower() not in registered:
            findings.append({
                "severity": "warning",
                "code": "CONFIG_UNKNOWN_DISABLED",
                "message": (
                    f"DEVPILOT_PROVIDER_DISABLED names unknown provider "
                    f"{name!r} (ignored)."
                ),
            })

    # 5. Disabled ∩ priority overlap is a footgun.
    disabled = {str(n).strip().lower() for n in (getattr(s, "PROVIDER_DISABLED", None) or [])}
    priority = [str(n).strip().lower() for n in (getattr(s, "PROVIDER_PRIORITY", None) or [])]
    overlap = disabled & set(priority)
    if overlap:
        findings.append({
            "severity": "warning",
            "code": "CONFIG_DISABLED_IN_PRIORITY",
            "message": (
                f"Providers disabled but also in the priority chain "
                f"(they will never route): {', '.join(sorted(overlap))}."
            ),
        })

    # 6. DATABASE_URL scheme must be PostgreSQL.
    db_url = getattr(s, "DATABASE_URL", None)
    if db_url:
        scheme = str(db_url).split(":", 1)[0].lower()
        if "postgres" not in scheme:
            findings.append({
                "severity": "error",
                "code": "CONFIG_DATABASE_SCHEME",
                "message": (
                    f"DATABASE_URL scheme {scheme!r} is not PostgreSQL; "
                    f"expected 'postgresql+asyncpg://...'."
                ),
            })

    # 7. Paid Gemini tier requires a key.
    tier = str(getattr(s, "GEMINI_TIER", "free") or "free").strip().lower()
    if tier == "paid" and not getattr(s, "GEMINI_API_KEY", None):
        findings.append({
            "severity": "warning",
            "code": "CONFIG_PAID_TIER_NO_KEY",
            "message": "GEMINI_TIER=paid but GEMINI_API_KEY is not set; the "
                       "gemini provider will be reported as not-configured.",
        })

    # 8. Health thresholds must be coherent.
    degraded = float(getattr(s, "PROVIDER_HEALTH_DEGRADED_SUCCESS_RATE", 0.5))
    unhealthy = float(getattr(s, "PROVIDER_HEALTH_UNHEALTHY_SUCCESS_RATE", 0.3))
    if degraded <= unhealthy:
        findings.append({
            "severity": "error",
            "code": "CONFIG_HEALTH_THRESHOLDS",
            "message": (
                f"PROVIDER_HEALTH_DEGRADED_SUCCESS_RATE ({degraded}) must be "
                f"greater than PROVIDER_HEALTH_UNHEALTHY_SUCCESS_RATE ({unhealthy})."
            ),
        })

    # 9. Routing enabled but zero configured providers (fake excluded from the
    #    check only when it is also excluded from the chain).
    routing = bool(getattr(s, "PROVIDER_ROUTING_ENABLED", True))
    if routing:
        from app.llm.provider_registry import provider_availability

        configured_names = []
        for name in registered:
            if name == "fake":
                continue
            attr, _always = provider_availability().get(name, (None, False))
            if attr and getattr(s, attr, None):
                configured_names.append(name)
        if not configured_names:
            findings.append({
                "severity": "warning",
                "code": "CONFIG_NO_PROVIDER_KEYS",
                "message": (
                    "Provider routing is enabled but no provider API keys / "
                    "endpoints are configured; all LLM calls will fail over "
                    "and eventually raise AllProvidersFailedError."
                ),
            })

    return findings


def run_startup_validation(settings: Any = None) -> List[Dict[str, Any]]:
    """Validate and log findings; raise when strict mode requires it.

    Returns the findings (also stored by the caller on app.state).
    """
    from app.config import settings as app_settings

    s = settings if settings is not None else app_settings
    findings = validate_settings(s)
    errors = [f for f in findings if f["severity"] == "error"]
    warnings = [f for f in findings if f["severity"] == "warning"]

    for f in findings:
        (logger.error if f["severity"] == "error" else logger.warning)(
            "Startup validation [%s]: %s", f["code"], f["message"]
        )
    logger.info(
        "Startup validation complete: %d error(s), %d warning(s)",
        len(errors), len(warnings),
    )

    strict = bool(getattr(s, "STARTUP_VALIDATION_STRICT", False))
    if strict and errors:
        raise RuntimeError(
            "Startup validation failed (%d error(s)); set "
            "DEVPILOT_STARTUP_VALIDATION_STRICT=false to start anyway. "
            "First error: %s"
            % (len(errors), errors[0]["message"])
        )
    return findings
