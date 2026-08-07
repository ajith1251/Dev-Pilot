"""
Structured logging for DevPilot.

Provides a pre-configured logger with consistent formatting
appropriate for both development and production.

Phase 20B: every record carries the current correlation ID (from
``app.core.context``) so operators can trace a request end-to-end across
provider calls, database queries and background probes.
"""

from __future__ import annotations

import logging
import sys

from app.config import settings


class CorrelationIdFilter(logging.Filter):
    """Inject the current correlation ID into every log record.

    The contextvar is per-task, so concurrent requests each see their own
    ID. Outside a request (startup, background loops) it renders as ``-``.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            from app.core.context import get_correlation_id

            record.correlation_id = get_correlation_id() or "-"
        except Exception:
            record.correlation_id = "-"
        return True


def configure_logging(
    level: str | None = None,
    *,
    name: str = "devpilot",
) -> logging.Logger:
    """Configure and return the application logger.

    Args:
        level: Override log level (default: from settings).
        name: Logger name.

    Returns:
        Configured logger instance.
    """
    log_level = (level or settings.LOG_LEVEL).upper()

    logger = logging.getLogger(name)
    logger.setLevel(log_level)

    # Prevent duplicate handlers if called multiple times
    if logger.handlers:
        return logger

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(log_level)

    fmt = logging.Formatter(
        fmt=(
            "%(asctime)s  %(levelname)-8s  %(name)s  "
            "[%(correlation_id)s]  "
            "%(filename)s:%(lineno)d  —  %(message)s"
        ),
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(fmt)
    handler.addFilter(CorrelationIdFilter())
    logger.addHandler(handler)

    # Propagate to parent loggers selectively
    logger.propagate = False

    return logger


logger = configure_logging()
