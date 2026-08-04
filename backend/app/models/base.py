"""
Base Pydantic models shared across the application.

Provides common patterns: timestamps, serialisable enums,
and a flexible response wrapper.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Generic, Optional, TypeVar
from uuid import uuid4

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    """Return current UTC datetime (timezone-aware)."""
    return datetime.now(timezone.utc)


def new_id() -> str:
    """Return a short unique identifier."""
    return uuid4().hex[:12]


# ── Common Enums ─────────────────────────────────────────────────


class AgentStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    BLOCKED = "blocked"  # awaiting human approval or external input


class WorkflowStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    AWAITING_APPROVAL = "awaiting_approval"
    CANCELLED = "cancelled"


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


# ── Base Models ──────────────────────────────────────────────────


class TimestampMixin(BaseModel):
    """Mixin that adds created_at / updated_at timestamps."""

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class IdentifiedMixin(BaseModel):
    """Mixin that adds an id field."""

    id: str = Field(default_factory=new_id)


# ── Generic response wrapper ────────────────────────────────────

DataT = TypeVar("DataT")


class Response(BaseModel, Generic[DataT]):
    """Standard API response envelope."""

    success: bool = True
    data: Optional[DataT] = None
    error: Optional[str] = None
    message: Optional[str] = None
