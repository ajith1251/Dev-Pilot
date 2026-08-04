"""
Structured logging for DevPilot.

Provides a pre-configured logger with consistent formatting
appropriate for both development and production.
"""

from __future__ import annotations

import logging
import sys

from app.config import settings


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
            "%(filename)s:%(lineno)d  —  %(message)s"
        ),
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(fmt)
    logger.addHandler(handler)

    # Propagate to parent loggers selectively
    logger.propagate = False

    return logger


logger = configure_logging()
