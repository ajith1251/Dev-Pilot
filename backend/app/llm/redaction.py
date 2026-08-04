"""
Secret redaction for the provider router (Phase 19B).

API keys, tokens and internal credentials must NEVER appear in logs, CLI
output or API responses. Every surface that serializes provider state goes
through these helpers so a leaked key is structurally impossible.
"""

from __future__ import annotations

from typing import Optional

# Substring markers that identify an API key / token / credential even when
# the field name is unknown (defense in depth for structured payloads).
_SENSITIVE_MARKERS = (
    "api_key",
    "apikey",
    "token",
    "secret",
    "password",
    "credential",
    "authorization",
    "private_key",
)


def redact_secret(value: Optional[str], visible_prefix: int = 4) -> str:
    """Mask a secret, keeping only a short prefix/suffix for identification.

    Examples:
        "sk-abc123XYZ789"   -> "sk-ab…789"
        "" / None            -> "<not set>"
        "abc" (too short)    -> "***"
    """
    if value is None or value == "":
        return "<not set>"
    if len(value) <= visible_prefix * 2:
        return "***"
    return f"{value[:visible_prefix]}…{value[-visible_prefix:]}"


def redact_value(value: Optional[str], is_secret: bool = True) -> str:
    """Return '<not set>' for empty values, else a masked secret."""
    if not value:
        return "<not set>"
    return redact_secret(value) if is_secret else str(value)


def redact_dict(data: dict) -> dict:
    """Recursively redact any sensitive-looking key in a dict."""
    out: dict = {}
    for key, value in data.items():
        lowered = key.lower()
        if any(marker in lowered for marker in _SENSITIVE_MARKERS):
            if isinstance(value, dict):
                out[key] = redact_dict(value)
            elif isinstance(value, (list, tuple)):
                out[key] = _redact_sequence(value)
            else:
                out[key] = redact_value(str(value) if value is not None else None)
        elif isinstance(value, dict):
            out[key] = redact_dict(value)
        elif isinstance(value, (list, tuple)):
            out[key] = _redact_sequence(value)
        else:
            out[key] = value
    return out


def _redact_sequence(items) -> list:
    """Recurse into sequences so dicts nested in lists are still redacted."""
    out = []
    for item in items:
        if isinstance(item, dict):
            out.append(redact_dict(item))
        elif isinstance(item, (list, tuple)):
            out.append(_redact_sequence(item))
        elif isinstance(item, str):
            out.append(redact_value(item))
        else:
            out.append(item)
    return out
