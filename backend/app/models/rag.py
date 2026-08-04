"""
Phase 5 — Code-Aware Repository Indexing & RAG models.

Defines the core data types for code intelligence:
- CodeSymbol: extracted symbols (classes, functions, etc.)
- CodeChunk: semantic code units
- RepositorySnapshot: identity of indexed repository state
- RepositoryCodeIndex: the full index
- RetrievalQuery / RetrievedContext: retrieval in/out
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.models.issues import ImplementationPlan, StructuredRequirements


# ── Symbol Kinds ────────────────────────────────────────────────


class SymbolKind(str, Enum):
    """Kinds of code symbols that can be extracted."""

    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    ASYNC_FUNCTION = "async_function"
    ASYNC_METHOD = "async_method"
    COMPONENT = "component"
    INTERFACE = "interface"
    TYPE = "type"
    CONSTANT = "constant"
    VARIABLE = "variable"
    IMPORT = "import"
    DECORATOR = "decorator"
    OTHER = "other"


class ChunkType(str, Enum):
    """Type of a code chunk."""

    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    COMPONENT = "component"
    INTERFACE = "interface"
    TYPE = "type"
    DOCUMENTATION = "documentation"
    CONFIGURATION = "configuration"
    SECTION = "section"
    FALLBACK = "fallback"


class EligibilityReason(str, Enum):
    """Reason a file was indexed or skipped."""

    INDEX_SOURCE = "source_code"
    INDEX_TEST = "test_file"
    INDEX_CONFIG = "relevant_configuration"
    INDEX_DOC = "architecture_documentation"
    INDEX_SCRIPT = "relevant_script"
    INDEX_MANIFEST = "dependency_manifest"
    SKIP_BINARY = "binary_file"
    SKIP_IMAGE = "image_file"
    SKIP_GENERATED = "generated_file"
    SKIP_SENSITIVE = "sensitive_file"
    SKIP_OVERSIZED = "oversized_file"
    SKIP_DEPENDENCY_DIR = "dependency_directory"
    SKIP_CACHE = "cache_directory"
    SKIP_LOCKFILE = "lockfile_content"
    SKIP_UNKNOWN = "unknown_category"
    SKIP_MINIFIED = "minified_bundle"


# ── Code Symbol ─────────────────────────────────────────────────


class CodeSymbol(BaseModel):
    """A symbol extracted from source code."""

    id: str = Field(description="Stable deterministic symbol ID")
    name: str = Field(description="Short symbol name (e.g. 'PlanningService')")
    qualified_name: str = Field(description="Fully qualified name (e.g. 'app.services.planning_service.PlanningService')")
    kind: SymbolKind = Field(default=SymbolKind.OTHER)
    file_path: str = Field(description="Relative path from repo root")
    language: str = Field(default="", description="Programming language")
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    parent_symbol: Optional[str] = Field(default=None, description="ID of parent symbol")
    signature: Optional[str] = Field(default=None, description="Signature line if available")
    docstring: Optional[str] = Field(default=None, description="First line of docstring if available")
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SymbolRelationship(BaseModel):
    """A lightweight relationship between symbols."""

    source_id: str = Field(description="Source symbol ID")
    target_id: str = Field(description="Target symbol ID")
    relationship: str = Field(description="contains | imports | extends | implements | calls")
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ── Code Chunk ──────────────────────────────────────────────────


class CodeChunk(BaseModel):
    """A semantic chunk of code."""

    chunk_id: str = Field(description="Unique chunk identifier")
    snapshot_id: str = Field(description="Which snapshot this belongs to")
    file_path: str = Field(description="Relative path from repo root")
    language: str = Field(default="")

    symbol_id: Optional[str] = Field(default=None, description="Associated symbol ID")
    symbol_name: Optional[str] = Field(default=None)
    symbol_kind: Optional[SymbolKind] = Field(default=None)

    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    chunk_type: ChunkType = Field(default=ChunkType.SECTION)
    content: str = Field(description="The actual code content")
    content_hash: str = Field(description="SHA-256 hex digest of content")

    module: Optional[str] = Field(default=None, description="Python/JS module path")
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ── Repository Snapshot ─────────────────────────────────────────


class RepositorySnapshot(BaseModel):
    """Identity of a repository state at indexing time."""

    snapshot_id: str = Field(description="Unique snapshot identifier")
    repository_id: str = Field(description="Repository path or GitHub owner/repo")
    repository_path: str = Field(description="Absolute path to repository")
    ref: Optional[str] = Field(default=None, description="Branch/tag ref if available")
    commit_sha: Optional[str] = Field(default=None, description="HEAD commit SHA if available")
    content_fingerprint: str = Field(description="Deterministic fingerprint of indexed state")
    file_count: int = Field(default=0)
    created_at: str = Field(description="ISO timestamp")

    # Optional metadata
    metadata: Dict[str, Any] = Field(default_factory=dict)


class IndexEligibilityResult(BaseModel):
    """Whether a file is eligible for indexing and why."""

    file_path: str = Field(description="Relative path")
    eligible: bool = Field(description="True if should be indexed")
    reason: EligibilityReason = Field(default=EligibilityReason.SKIP_UNKNOWN)
    detail: str = Field(default="", description="Human-readable explanation")
    category: str = Field(default="unknown")


# ── Index Statistics ────────────────────────────────────────────


class IndexStatistics(BaseModel):
    """Statistics about an index operation."""

    files_considered: int = Field(default=0)
    files_indexed: int = Field(default=0)
    files_skipped: int = Field(default=0)
    symbols_extracted: int = Field(default=0)
    chunks_created: int = Field(default=0)
    embedding_count: int = Field(default=0)
    embedding_cache_hits: int = Field(default=0)
    duration_seconds: float = Field(default=0.0)
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


# ── Repository Code Index ───────────────────────────────────────


class RepositoryCodeIndex(BaseModel):
    """The full code index for a repository snapshot."""

    snapshot: RepositorySnapshot = Field(description="Indexed repository identity")
    files: List[str] = Field(default_factory=list, description="Indexed file paths")
    symbols: List[CodeSymbol] = Field(default_factory=list, description="Extracted symbols")
    chunks: List[CodeChunk] = Field(default_factory=list, description="Code chunks")
    statistics: IndexStatistics = Field(default_factory=IndexStatistics)
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ── Retrieval Models ────────────────────────────────────────────


class RetrievalFilter(BaseModel):
    """Filters to apply during retrieval."""

    languages: List[str] = Field(default_factory=list)
    file_categories: List[str] = Field(default_factory=list)
    path_prefix: Optional[str] = Field(default=None)
    module: Optional[str] = Field(default=None)
    symbol_kinds: List[SymbolKind] = Field(default_factory=list)
    include_tests: bool = Field(default=True)


class RetrievalQuery(BaseModel):
    """A query for the hybrid retriever."""

    text: str = Field(description="Natural language query or search terms")
    snapshot_id: Optional[str] = Field(default=None)
    plan_step: Optional[str] = Field(default=None, description="Implementation plan step context")
    requirement_ids: List[str] = Field(default_factory=list)
    likely_affected_areas: List[str] = Field(default_factory=list)
    top_k: int = Field(default=10, ge=1, le=100)
    filters: Optional[RetrievalFilter] = Field(default=None)

    # Weights for hybrid scoring (optional, defaults used if None)
    weight_lexical: Optional[float] = Field(default=None)
    weight_semantic: Optional[float] = Field(default=None)
    weight_symbol: Optional[float] = Field(default=None)
    weight_structural: Optional[float] = Field(default=None)

    max_total_chars: int = Field(default=50_000, description="Max total characters in result")
    max_chunks_per_file: int = Field(default=5, description="Max chunks from a single file")


class RetrievedContextItem(BaseModel):
    """A single retrieved context item."""

    chunk: CodeChunk = Field(description="The retrieved code chunk")
    score: float = Field(default=0.0, ge=0.0)

    # Score breakdown
    lexical_score: float = Field(default=0.0)
    semantic_score: float = Field(default=0.0)
    symbol_score: float = Field(default=0.0)
    structural_score: float = Field(default=0.0)

    reasons: List[str] = Field(default_factory=list, description="Human-readable reasons for retrieval")


class RetrievedContext(BaseModel):
    """Structured output of the hybrid retriever."""

    query: RetrievalQuery = Field(description="The original query")
    snapshot_id: str = Field(default="")
    items: List[RetrievedContextItem] = Field(default_factory=list)
    total_candidates: int = Field(default=0)
    duration_seconds: float = Field(default=0.0)
    warnings: List[str] = Field(default_factory=list)

    # Trust boundary: all repository content is untrusted
    trust_level: str = Field(default="UNTRUSTED_REPOSITORY_CONTENT")


# ── Plan-Aware Retrieval ────────────────────────────────────────


class PlanAwareRetrievalInput(BaseModel):
    """Input for plan-aware retrieval."""

    plan: ImplementationPlan
    requirements: Optional[StructuredRequirements] = None
    repository_path: str = Field(description="Path to the repository")
    top_k_per_step: int = Field(default=5, ge=1, le=20)
    filters: Optional[RetrievalFilter] = Field(default=None)


class StepContext(BaseModel):
    """Retrieval results for a single plan step."""

    step_id: str = Field()
    step_title: str = Field()
    query: str = Field(description="Constructed query text")
    context: RetrievedContext = Field()


class PlanAwareRetrievalResult(BaseModel):
    """Result of plan-aware retrieval."""

    steps: List[StepContext] = Field(default_factory=list)
    total_chunks: int = Field(default=0)
    warnings: List[str] = Field(default_factory=list)
