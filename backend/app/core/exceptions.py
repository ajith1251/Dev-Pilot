"""
Exception hierarchy for DevPilot.

All domain exceptions inherit from DevPilotError so callers
can catch a single base type when needed.
"""

from __future__ import annotations


class DevPilotError(Exception):
    """Base exception for all DevPilot domain errors."""


# ── LLM ──────────────────────────────────────────────────────────


class LLMError(DevPilotError):
    """Raised when an LLM call fails."""


class LLMConfigurationError(LLMError):
    """Raised when the LLM provider is misconfigured."""


class LLMProviderNotFound(LLMError):
    """Raised when an unknown provider is requested."""


# ── Provider Router (Phase 19B) ────────────────────────────────


class ProviderRouterError(LLMError):
    """Raised when the provider router encounters an error."""


class AllProvidersFailedError(ProviderRouterError):
    """Raised when every provider in the routing chain failed.

    Never silently swallowed — the router surfaces the aggregated failure
    so callers know the request did not complete.
    """

    def __init__(self, message: str, failures=None):
        super().__init__(message)
        self.failures = failures or []

    def __str__(self) -> str:
        base = super().__str__()
        if self.failures:
            summary = "; ".join(
                f"{f.get('provider', '?')}:{f.get('kind', '?')}"
                for f in self.failures
            )
            return f"{base} [{summary}]"
        return base


class ProviderNotAvailableError(ProviderRouterError):
    """Raised when no provider is configured/enabled at all."""


class ProviderCallFailedError(ProviderRouterError):
    """Raised when a single provider exhausts retries and cannot serve a call.

    The router catches this internally during failover; it only surfaces
    directly when the router is used in non-failover mode.
    """

    def __init__(self, provider: str, kind: str, message: str):
        super().__init__(f"Provider '{provider}' failed ({kind}): {message}")
        self.provider = provider
        self.kind = kind
        self.message = message


# ── Agent ────────────────────────────────────────────────────────


class AgentError(DevPilotError):
    """Raised when an agent operation fails."""


class AgentNotFoundError(AgentError):
    """Raised when a requested agent is not registered."""


class AgentExecutionError(AgentError):
    """Raised when agent execution encounters a non-recoverable error."""


# ── Tool ─────────────────────────────────────────────────────────


class ToolError(DevPilotError):
    """Raised when a tool operation fails."""


class ToolExecutionError(ToolError):
    """Raised when tool execution fails."""


# ── GitHub ───────────────────────────────────────────────────────


class GitHubError(DevPilotError):
    """Raised when a GitHub API operation fails."""


class GitHubAuthenticationError(GitHubError):
    """Raised when GitHub credentials are missing or invalid."""


class GitHubRateLimitError(GitHubError):
    """Raised when GitHub API rate limit is exhausted."""


# ── Phase 4: Planning ────────────────────────────────────────────


class PlanningError(DevPilotError):
    """Raised when planning operations fail."""


class TaskValidationError(PlanningError):
    """Raised when task input validation fails."""


class IssueAnalysisError(PlanningError):
    """Raised when issue analysis fails."""


class RequirementsValidationError(PlanningError):
    """Raised when structured requirements fail validation."""


class PlanValidationError(PlanningError):
    """Raised when plan validation fails."""


class LLMOutputValidationError(PlanningError):
    """Raised when LLM response fails schema validation."""


# ── Workflow ─────────────────────────────────────────────────────


class WorkflowError(DevPilotError):
    """Raised when a workflow operation fails."""


class WorkflowExecutionError(WorkflowError):
    """Raised when workflow execution fails."""


# ── Phase 5: Code Intelligence / RAG ─────────────────────────────


class CodeIntelligenceError(DevPilotError):
    """Raised when code intelligence operations fail."""


class RepositoryIndexError(CodeIntelligenceError):
    """Raised when repository indexing fails."""


class CodeParsingError(CodeIntelligenceError):
    """Raised when source code parsing fails."""


class ChunkingError(CodeIntelligenceError):
    """Raised when code chunking fails."""


class EmbeddingError(CodeIntelligenceError):
    """Raised when embedding generation fails."""


class IndexStaleError(CodeIntelligenceError):
    """Raised when the index is stale (repository changed after indexing)."""


class RetrievalError(CodeIntelligenceError):
    """Raised when retrieval fails."""


class InvalidRetrievalQuery(CodeIntelligenceError):
    """Raised when a retrieval query is invalid."""


class IndexEligibilityError(CodeIntelligenceError):
    """Raised when index eligibility checks fail."""


# ── Phase 6: Coding & Patching ──────────────────────────────────


class CodingError(DevPilotError):
    """Raised when coding operations fail."""

    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message)
        self.details = details or {}


class CodingOutputValidationError(CodingError):
    """Raised when the LLM coding output fails schema validation."""

    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message, details=details)


class InsufficientContextError(CodingError):
    """Raised when retrieved context is insufficient for coding."""

    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message, details=details)


class WorkspaceError(CodingError):
    """Raised when workspace preparation or operations fail."""

    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message, details=details)


class PatchValidationError(CodingError):
    """Raised when patch validation fails."""

    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message, details=details)


class PatchConflictError(CodingError):
    """Raised when patch conflicts are detected."""

    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message, details=details)


class PatchApplicationError(CodingError):
    """Raised when patch application fails."""

    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message, details=details)


class PatchRollbackError(CodingError):
    """Raised when patch rollback fails."""

    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message, details=details)


# ── Phase 7: Testing & Execution ───────────────────────────────


class TestingError(DevPilotError):
    """Raised when testing operations fail."""

    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message)
        self.details = details or {}


class ExecutionPolicyError(TestingError):
    """Raised when an execution step violates policy."""

    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message, details=details)


class ExecutionRejectedError(TestingError):
    """Raised when a command is rejected by execution policy."""

    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message, details=details)


class ExecutionTimeoutError(TestingError):
    """Raised when a command times out."""

    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message, details=details)


class TestResultParseError(TestingError):
    """Raised when test result parsing fails."""

    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message, details=details)


class EnvironmentNotReadyError(TestingError):
    """Raised when the execution environment is not ready."""

    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message, details=details)


# ── Phase 8: Repair ─────────────────────────────────────────────


class RepairError(DevPilotError):
    """Raised when repair operations fail."""

    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message)
        self.details = details or {}


class RepairDiagnosisError(RepairError):
    """Raised when failure diagnosis fails."""

    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message, details=details)


class RepairProposalError(RepairError):
    """Raised when a repair proposal fails validation."""

    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message, details=details)


class RepairPolicyViolationError(RepairError):
    """Raised when a repair violates policy (tampering, unsafe paths, etc)."""

    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message, details=details)


class RepairLoopError(RepairError):
    """Raised when the repair loop encounters a non-recoverable error."""

    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message, details=details)


# ── Database ──────────────────────────────────────────────────


class DatabaseError(DevPilotError):
    """Raised when database operations fail."""

    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message)
        self.details = details or {}


class DatabaseConfigurationError(DatabaseError):
    """Raised when database configuration is missing or invalid."""

    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message, details=details)


class DatabaseConnectionError(DatabaseError):
    """Raised when the database server is unreachable."""

    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message, details=details)


class DatabaseUnavailableError(DatabaseError):
    """Raised when the database is not available."""

    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message, details=details)


# ── Phase 9: Review & Quality Gate ─────────────────────────────


class ReviewError(DevPilotError):
    """Raised when review operations fail."""

    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message)
        self.details = details or {}


class ReviewContextBuildError(ReviewError):
    """Raised when review context building fails."""

    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message, details=details)


class ReviewEvidenceError(ReviewError):
    """Raised when evidence validation fails."""

    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message, details=details)


class QualityGateError(ReviewError):
    """Raised when the quality gate encounters an error."""

    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message, details=details)
