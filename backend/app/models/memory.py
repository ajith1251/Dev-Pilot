"""
Phase 13 — Repository Memory models.

Defines the data structures for durable engineering knowledge:
- MemoryType: categories of repository knowledge
- MemoryStatus: lifecycle states (VERIFIED → PROVISIONAL → STALE → INVALID)
- RepositoryMemory: the canonical memory unit
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class MemoryType(str, Enum):
    """Type of repository knowledge memory."""

    ARCHITECTURE = "architecture"           # Architectural knowledge
    CONVENTION = "convention"               # Coding conventions and patterns
    SUCCESSFUL_CHANGE = "successful_change" # Approved change knowledge
    FAILED_APPROACH = "failed_approach"     # Known failure patterns
    TEST_KNOWLEDGE = "test_knowledge"       # Testing patterns and fixtures
    REVIEW_FINDING = "review_finding"       # Reusable review insight
    MODULE_KNOWLEDGE = "module_knowledge"   # Module-level relationships
    DEPENDENCY = "dependency"               # Important dependency discovered
    WORKFLOW = "workflow"                   # Build/test/deploy workflow


class MemoryStatus(str, Enum):
    """Lifecycle status of a repository memory.

    VERIFIED:   Confirmed by successful run evidence
    PROVISIONAL: Tentative — awaiting confirmation
    STALE:      Potentially outdated (referenced symbol/file changed)
    INVALID:    Contradicted by evidence
    """

    VERIFIED = "verified"
    PROVISIONAL = "provisional"
    STALE = "stale"
    INVALID = "invalid"


class MemoryEvidence(BaseModel):
    """Evidence backing a repository memory."""

    source_type: str = Field(description="run | file | symbol | test_result | review_result | quality_gate")
    source_id: str = Field(description="Run ID, file path, symbol ID, or other identifier")
    description: str = Field(default="", description="Human-readable description of the evidence")


class RepositoryMemory(BaseModel):
    """A single unit of durable repository knowledge.

    Memory is created from verified engineering evidence (successful
    runs, confirmed reviews, detected patterns) — NOT from raw LLM output.

    Malicious repository text must NEVER become trusted memory.
    """

    memory_id: str = Field(description="Unique memory identifier")
    repository_id: str = Field(description="Repository this memory belongs to")

    memory_type: MemoryType = Field(description="Category of memory")
    status: MemoryStatus = Field(default=MemoryStatus.PROVISIONAL)
    content: str = Field(description="The memory content / knowledge statement")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    # Associated symbols and files
    symbol_names: List[str] = Field(default_factory=list, description="Referenced symbols")
    file_paths: List[str] = Field(default_factory=list, description="Referenced files")

    # Evidence
    evidence: List[MemoryEvidence] = Field(default_factory=list)

    # Source run
    source_run_id: Optional[str] = Field(default=None)

    # Tags for categorization
    tags: List[str] = Field(default_factory=list)

    # Timestamps
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    last_used_at: Optional[str] = Field(default=None)

    # Validity
    version: int = Field(default=1, ge=1)
    related_commit: Optional[str] = Field(default=None, description="Commit hash when this was created")


class MemoryQuery(BaseModel):
    """Query for retrieving repository memories."""

    repository_id: str = Field(description="Repository to search")
    memory_types: Optional[List[MemoryType]] = Field(default=None)
    status_filter: Optional[List[MemoryStatus]] = Field(default=None)
    symbol_names: Optional[List[str]] = Field(default=None)
    file_paths: Optional[List[str]] = Field(default=None)
    query_text: Optional[str] = Field(default=None)
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    include_stale: bool = Field(default=False)
