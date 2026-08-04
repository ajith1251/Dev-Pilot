"""
Phase 6 — Coding Agent & Safe Patch Engine models.

Defines the core data types for code generation and patching:
- PatchSet, FileChange: structured code change proposals
- CodingWorkspace: isolated writable copy of a repository
- PatchApplicationResult: outcome of applying a patch
- CodingAgentInput/Output: agent contract types
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.models.issues import ImplementationPlan, StructuredRequirements
from app.models.rag import RetrievedContext


# ── Forward reference for Phase 13 agent context ───────────────
# Avoid circular imports; AgentContext is resolved at runtime.
AgentContextRef = Any


# ── File Operations ─────────────────────────────────────────────


class FileOperation(str, Enum):
    """Types of file operations that can be performed."""

    CREATE = "create"
    MODIFY = "modify"
    DELETE = "delete"


class PatchStatus(str, Enum):
    """Lifecycle states of a patch proposal."""

    PROPOSED = "proposed"
    VALIDATED = "validated"
    REJECTED = "rejected"
    DRY_RUN = "dry_run"
    APPLIED = "applied"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


# ── File Change ─────────────────────────────────────────────────


class FileChange(BaseModel):
    """A single file change within a PatchSet."""

    change_id: str = Field(description="Unique change identifier (e.g. CHANGE-001)")
    operation: FileOperation = Field(description="CREATE, MODIFY, or DELETE")
    path: str = Field(description="Relative path from workspace root")
    original_hash: Optional[str] = Field(
        default=None,
        description="SHA-256 of original content (required for MODIFY/DELETE)",
    )
    new_content: Optional[str] = Field(
        default=None,
        description="New file content (required for CREATE/MODIFY, omitted for DELETE)",
    )
    reason: str = Field(default="", description="Why this change is needed")
    plan_step_id: Optional[str] = Field(
        default=None, description="ImplementationPlan step this change belongs to"
    )
    requirement_ids: List[str] = Field(
        default_factory=list,
        description="Requirement identifiers this change addresses",
    )
    source_context_ids: List[str] = Field(
        default_factory=list,
        description="Retrieved chunk IDs that informed this change",
    )


# ── Patch Set ────────────────────────────────────────────────────


class PatchSet(BaseModel):
    """A complete set of file changes proposed by the Coding Agent."""

    patch_id: str = Field(description="Unique patch identifier")
    plan_id: Optional[str] = Field(
        default=None,
        description="ImplementationPlan step ID or plan summary hash",
    )
    workspace_snapshot: Optional[str] = Field(
        default=None,
        description="Workspace fingerprint at generation time",
    )
    changes: List[FileChange] = Field(
        default_factory=list, description="Ordered file changes"
    )
    summary: str = Field(default="", description="Human-readable summary of the patch")
    warnings: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    status: PatchStatus = Field(default=PatchStatus.PROPOSED)


# ── Patch Validation ────────────────────────────────────────────


class PatchValidationResult(BaseModel):
    """Result of validating a PatchSet."""

    is_valid: bool = Field(description="Whether the patch passed validation")
    errors: List[str] = Field(
        default_factory=list, description="Validation errors (if invalid)"
    )
    warnings: List[str] = Field(
        default_factory=list, description="Validation warnings (non-blocking)"
    )
    checked_changes: int = Field(default=0)
    checked_operations: int = Field(default=0)
    status: str = Field(default="validated", description="validated | rejected")


# ── Workspace ───────────────────────────────────────────────────


class CodingWorkspace(BaseModel):
    """An isolated writable copy of a repository for coding operations."""

    workspace_id: str = Field(description="Unique workspace identifier")
    source_repository: str = Field(
        description="Absolute path to the original source repository"
    )
    root_path: str = Field(description="Absolute path to the workspace root")
    fingerprint: str = Field(
        description="SHA-256 fingerprint of source files at creation"
    )
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    writable: bool = Field(default=True)
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ── Patch Application Result ────────────────────────────────────


class PatchApplicationResult(BaseModel):
    """Result of applying a PatchSet to a workspace."""

    patch_id: str = Field(description="The patch that was applied")
    status: PatchStatus = Field(
        default=PatchStatus.PROPOSED,
        description="Final status (APPLIED, FAILED, ROLLED_BACK, DRY_RUN)",
    )
    dry_run: bool = Field(default=False)
    changes_attempted: int = Field(default=0)
    changes_applied: int = Field(default=0)
    files_created: List[str] = Field(default_factory=list)
    files_modified: List[str] = Field(default_factory=list)
    files_deleted: List[str] = Field(default_factory=list)
    diff: Optional[str] = Field(default=None, description="Unified diff text")
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    rolled_back: bool = Field(default=False)
    duration_seconds: float = Field(default=0.0)


# ── Coding Result ────────────────────────────────────────────────


class CodingResult(BaseModel):
    """Result of a coding operation (generate, dry-run, apply)."""

    status: str = Field(
        default="PROPOSED",
        description="PROPOSED | REJECTED | APPLIED | FAILED | INSUFFICIENT_CONTEXT",
    )
    plan_id: str = Field(default="")
    patch_set: Optional[PatchSet] = Field(default=None)
    validation: Optional[PatchValidationResult] = Field(default=None)
    workspace_id: Optional[str] = Field(default=None)
    workspace_root: Optional[str] = Field(default=None)
    dry_run_result: Optional[PatchApplicationResult] = Field(default=None)
    apply_result: Optional[PatchApplicationResult] = Field(default=None)
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    duration: float = Field(default=0.0)


class CodingCapabilities(BaseModel):
    """Reported capabilities of the Phase 6 coding system."""

    supported_operations: List[str] = Field(
        default_factory=lambda: ["CREATE", "MODIFY", "DELETE"]
    )
    max_files_per_patch: int = Field(default=20)
    max_file_size: int = Field(default=500_000)
    dry_run_supported: bool = Field(default=True)
    diff_format: str = Field(default="unified")
    rollback_supported: bool = Field(default=True)
    workspace_isolation: bool = Field(default=True)
    delete_enabled: bool = Field(default=False)


# ── Coding Agent Input / Output ─────────────────────────────────


class CodingAgentInput(BaseModel):
    """Input to the Coding Agent.

    Combines the validated ImplementationPlan with retrieved
    repository context for code generation.
    """

    plan: ImplementationPlan = Field(
        description="Validated implementation plan from Phase 4"
    )
    requirements: Optional[StructuredRequirements] = Field(
        default=None, description="Structured requirements from analysis"
    )
    retrieved_context: Optional[RetrievedContext] = Field(
        default=None,
        description="Retrieved repository context per plan step",
    )
    workspace: Optional[CodingWorkspace] = Field(
        default=None,
        description="Coding workspace for context (paths, structure)",
    )

    workspace_structure: str = Field(
        default="",
        description="Workspace file layout summary (relative paths) so the LLM "
                    "knows which files exist and can propose MODIFY changes",
    )

    # Phase 13: ContextEngine-produced context
    agent_context: Optional[AgentContextRef] = Field(
        default=None,
        description="Phase 13 AgentContext from ContextEngine (replaces _get_graph_context fallback)",
    )

    # Context budget limits
    max_context_chunks: int = Field(default=10, ge=1, le=50)
    max_context_chars: int = Field(default=50_000, ge=1000)


class CodingAgentOutput(BaseModel):
    """Output from the Coding Agent.

    A structured PatchSet with all proposed changes, or a status
    indicating insufficient context.
    """

    patch_set: Optional[PatchSet] = Field(
        default=None, description="Structured patch proposal"
    )
    status: str = Field(
        default="success",
        description="success | insufficient_context | error",
    )
    missing_context: List[str] = Field(
        default_factory=list,
        description="Files or context that were missing",
    )
    warnings: List[str] = Field(default_factory=list)
    error: Optional[str] = Field(default=None)
