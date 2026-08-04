"""
Phase 7 — Test Agent & Controlled Execution Engine models.

Defines data types for:
- Command discovery and selection (CommandCandidate, ExecutionStep, ExecutionPlan)
- Controlled subprocess execution (ProcessExecutionResult)
- Test result normalization (TestRunResult, TestFailure)
- Failure classification

These models enable structured evidence for Phase 8 (Fix Agent).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── Execution Statuses ──────────────────────────────────────────


class ExecutionStatus(str, Enum):
    """Status of a single process execution or overall test run."""

    PASSED = "passed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    REJECTED = "rejected"
    ERROR = "error"
    SKIPPED = "skipped"
    ENVIRONMENT_NOT_READY = "environment_not_ready"
    RUNNING = "running"
    PENDING = "pending"


# ── Command Categories & Sources ────────────────────────────────


class CommandCategory(str, Enum):
    """Category of a discovered or selected command."""

    TEST = "test"
    LINT = "lint"
    TYPECHECK = "typecheck"
    BUILD = "build"
    OTHER = "other"


class CommandSource(str, Enum):
    """Provenance of a command candidate."""

    PYPROJECT = "pyproject"
    PACKAGE_JSON = "package_json"
    CONFIG = "config"
    PHASE2_DETECTION = "phase2_detection"
    DEFAULT_FRAMEWORK_RULE = "default_framework_rule"
    USER_APPROVED = "user_approved"


# ── Command Candidate ───────────────────────────────────────────


class CommandCandidate(BaseModel):
    """A single candidate command for possible execution.

    Captures provenance so the execution policy can make informed
    decisions about whether this command should be allowed.
    """

    command_id: str = Field(description="Unique command identifier")
    category: CommandCategory = Field(description="What kind of command")
    executable: str = Field(description="The executable to run (e.g. python)")
    arguments: List[str] = Field(
        default_factory=list, description="Arguments as a list (never shell string)"
    )
    working_directory: str = Field(
        default=".", description="Working directory relative to workspace root"
    )
    source: CommandSource = Field(
        default=CommandSource.PHASE2_DETECTION,
        description="Where this command was discovered",
    )
    confidence: float = Field(
        default=0.5, ge=0.0, le=1.0, description="Confidence this is the right command"
    )
    reason: str = Field(
        default="", description="Human-readable explanation of why this command"
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ── Execution Step ──────────────────────────────────────────────


class ExecutionStep(BaseModel):
    """A single step in an execution plan."""

    step_id: str = Field(description="Unique step identifier (e.g. STEP-001)")
    category: CommandCategory = Field(description="What kind of command")
    executable: str = Field(description="Executable to run")
    arguments: List[str] = Field(
        default_factory=list, description="Arguments as a list"
    )
    working_directory: str = Field(
        default=".", description="Working directory relative to workspace root"
    )
    timeout_seconds: int = Field(
        default=60, ge=1, le=600, description="Per-command timeout"
    )
    required: bool = Field(
        default=True,
        description="If True, failure of this step marks the entire run as failed",
    )
    source: CommandSource = Field(
        default=CommandSource.PHASE2_DETECTION,
        description="Where this command was sourced from",
    )
    reason: str = Field(
        default="", description="Why this step is included in the plan"
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ── Execution Plan ──────────────────────────────────────────────


class ExecutionPlan(BaseModel):
    """A validated execution plan — a bounded set of steps to run.

    Produced by the Test Agent (or deterministic planner) and consumed
    by the Controlled Execution Engine after policy validation.
    """

    plan_id: str = Field(description="Unique plan identifier")
    workspace_id: str = Field(description="Target workspace identifier")
    workspace_root: str = Field(description="Absolute path to workspace root")
    steps: List[ExecutionStep] = Field(
        default_factory=list, description="Ordered execution steps"
    )
    max_total_timeout_seconds: int = Field(
        default=300, ge=1, le=3600, description="Maximum wall-clock time for entire plan"
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ── Process Execution Result ────────────────────────────────────


class ProcessExecutionResult(BaseModel):
    """Raw result of a single process execution.

    Never includes DevPilot secrets.
    """

    step_id: str = Field(description="Corresponding ExecutionStep.step_id")
    command: str = Field(description="Command string for display (no secrets)")
    category: CommandCategory = Field(description="Category of the command")
    status: ExecutionStatus = Field(description="Execution status")
    exit_code: Optional[int] = Field(default=None, description="Process exit code")
    stdout: str = Field(default="", description="Captured stdout")
    stderr: str = Field(default="", description="Captured stderr")
    stdout_truncated: bool = Field(
        default=False, description="Whether stdout was truncated"
    )
    stderr_truncated: bool = Field(
        default=False, description="Whether stderr was truncated"
    )
    started_at: Optional[str] = Field(
        default=None, description="ISO 8601 start timestamp"
    )
    finished_at: Optional[str] = Field(
        default=None, description="ISO 8601 finish timestamp"
    )
    duration_seconds: Optional[float] = Field(
        default=None, description="Execution duration in seconds"
    )
    timed_out: bool = Field(default=False, description="Whether the process timed out")
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ── Failure Categories ──────────────────────────────────────────


class FailureCategory(str, Enum):
    """Deterministically classified failure categories.

    These are classifications, not diagnoses. Phase 8 will use these
    as evidence for generating fixes.
    """

    ASSERTION_FAILURE = "assertion_failure"
    IMPORT_ERROR = "import_error"
    SYNTAX_ERROR = "syntax_error"
    TYPE_ERROR = "type_error"
    BUILD_FAILURE = "build_failure"
    LINT_FAILURE = "lint_failure"
    TIMEOUT = "timeout"
    DEPENDENCY_ERROR = "dependency_error"
    CONFIGURATION_ERROR = "configuration_error"
    EXECUTION_ERROR = "execution_error"
    UNKNOWN = "unknown"


# ── Test Failure ────────────────────────────────────────────────


class TestFailure(BaseModel):
    """A single normalized test failure.

    Fields are optional where the framework may not provide them.
    """

    failure_id: str = Field(description="Unique failure identifier")
    framework: str = Field(default="", description="Test framework (e.g. pytest)")
    test_name: str = Field(default="", description="Fully qualified test name")
    file_path: Optional[str] = Field(
        default=None, description="File path relative to workspace root"
    )
    line_number: Optional[int] = Field(default=None, description="Line number")
    message: str = Field(default="", description="Failure message")
    failure_type: FailureCategory = Field(
        default=FailureCategory.UNKNOWN, description="Classified failure type"
    )
    stack_trace: Optional[str] = Field(default=None, description="Traceback text")
    related_output: Optional[str] = Field(
        default=None, description="Related stdout/stderr excerpt"
    )
    step_id: Optional[str] = Field(
        default=None, description="ExecutionStep this failure came from"
    )


# ── Test Run Result ─────────────────────────────────────────────


class TestRunResult(BaseModel):
    """Normalized result of a full test execution run.

    This is the primary output of Phase 7 and the input contract
    for Phase 8 (Fix Agent).
    """

    run_id: str = Field(description="Unique run identifier")
    workspace_id: str = Field(description="Workspace identifier")
    status: ExecutionStatus = Field(description="Overall run status")

    # Command-level counts
    commands_total: int = Field(default=0)
    commands_passed: int = Field(default=0)
    commands_failed: int = Field(default=0)
    commands_skipped: int = Field(default=0)

    # Test-level counts (populated by parser evidence only)
    tests_total: Optional[int] = Field(default=None)
    tests_passed: Optional[int] = Field(default=None)
    tests_failed: Optional[int] = Field(default=None)
    tests_skipped: Optional[int] = Field(default=None)

    # Failures & results
    failures: List[TestFailure] = Field(
        default_factory=list, description="Normalized test failures"
    )
    process_results: List[ProcessExecutionResult] = Field(
        default_factory=list, description="Raw process results"
    )

    # Metadata
    duration_seconds: float = Field(default=0.0)
    summary: str = Field(default="", description="Human-readable summary")
    warnings: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ── Capabilities ────────────────────────────────────────────────


class TestingCapabilities(BaseModel):
    """Reported capabilities of the Phase 7 testing system."""

    supported_categories: List[str] = Field(
        default_factory=lambda: ["test", "lint", "typecheck", "build"]
    )
    supported_frameworks: List[str] = Field(
        default_factory=lambda: ["pytest", "unittest", "vitest", "jest", "generic"]
    )
    max_commands_per_run: int = Field(default=10)
    default_timeout_seconds: int = Field(default=60)
    max_output_bytes: int = Field(default=1_048_576)
    environment_sanitization: bool = Field(default=True)
    workspace_isolation: bool = Field(default=True)
    llm_required: bool = Field(
        default=False, description="Whether LLM is required for planning"
    )
