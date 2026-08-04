"""
Code Intelligence Workflow — Phase 5.

Orchestrates the indexing and retrieval pipeline:
    START → validate_repository → analyze_repository → build_or_reuse_index →
    build_retrieval_queries → retrieve_context → validate_context → END

Follows the same pattern as RepositoryAnalysisWorkflow (Phase 2),
RemoteAnalysisWorkflow (Phase 3), and PlanningWorkflow (Phase 4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.logging import logger
from app.models.issues import ImplementationPlan, StructuredRequirements
from app.models.profile import RepositoryProfile
from app.models.rag import (
    PlanAwareRetrievalResult,
    RepositoryCodeIndex,
    RetrievalQuery,
    RetrievedContext,
)
from app.rag.retrieval.hybrid_retriever import HybridRetriever
from app.services.index_builder import RepositoryIndexBuilder
from app.services.repository_analyzer import RepositoryAnalyzer


@dataclass
class CodeIntelligenceState:
    """State for the Phase 5 code intelligence workflow."""

    repository_path: str
    plan: Optional[ImplementationPlan] = None
    requirements: Optional[StructuredRequirements] = None

    status: str = "pending"  # pending|running|completed|failed
    profile: Optional[RepositoryProfile] = None
    code_index: Optional[RepositoryCodeIndex] = None
    retrieval_result: Optional[RetrievedContext] = None
    plan_context_result: Optional[PlanAwareRetrievalResult] = None

    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class CodeIntelligenceWorkflow:
    """Workflow for Phase 5 code intelligence (indexing + retrieval).

    Current graph (linear):
        START → validate_repository → analyze_repository →
        build_index → retrieve_context → validate_context → END

    For plan-aware retrieval:
        START → ... → retrieve_plan_context → validate_context → END
    """

    def __init__(
        self,
        repo_analyzer: Optional[RepositoryAnalyzer] = None,
        index_builder: Optional[RepositoryIndexBuilder] = None,
        retriever: Optional[HybridRetriever] = None,
    ) -> None:
        self._repo_analyzer = repo_analyzer or RepositoryAnalyzer()
        self._index_builder = index_builder or RepositoryIndexBuilder()
        self._retriever = retriever or HybridRetriever()

    async def run_index(
        self,
        repo_path: str,
    ) -> CodeIntelligenceState:
        """Run the indexing workflow.

        Args:
            repo_path: Path to the local repository.

        Returns:
            CodeIntelligenceState with the built code index.
        """
        state = CodeIntelligenceState(
            repository_path=repo_path,
            started_at=datetime.now(timezone.utc).isoformat(),
            status="running",
        )

        logger.info("Code Intelligence workflow started (index): %s", repo_path)

        # Node 1: validate_repository
        state = self._validate_repository(state)
        if state.status == "failed":
            return self._finalize(state)

        # Node 2: analyze_repository (optional, for metadata)
        state = await self._analyze_repository(state)

        # Node 3: build_index
        state = await self._build_index(state)

        state.status = "completed"
        return self._finalize(state)

    async def run_retrieval(
        self,
        repo_path: str,
        query_text: str,
        top_k: int = 10,
    ) -> CodeIntelligenceState:
        """Run the retrieval workflow (index + query).

        Args:
            repo_path: Path to the repository.
            query_text: Natural language query.
            top_k: Number of results.

        Returns:
            CodeIntelligenceState with retrieved context.
        """
        state = CodeIntelligenceState(
            repository_path=repo_path,
            started_at=datetime.now(timezone.utc).isoformat(),
            status="running",
        )

        logger.info("Code Intelligence workflow started (retrieval): %s", repo_path)

        # Build index with indexes
        state = self._validate_repository(state)
        if state.status == "failed":
            return self._finalize(state)

        state = await self._build_index_with_indexes(state)
        if state.status == "failed":
            return self._finalize(state)

        # Retrieve
        query = RetrievalQuery(text=query_text, top_k=top_k)
        state.retrieval_result = self._retriever.retrieve(query)
        state.status = "completed"

        return self._finalize(state)

    async def run_plan_retrieval(
        self,
        repo_path: str,
        plan: ImplementationPlan,
        requirements: Optional[StructuredRequirements] = None,
        top_k_per_step: int = 5,
    ) -> CodeIntelligenceState:
        """Run plan-aware retrieval workflow.

        Args:
            repo_path: Path to the repository.
            plan: Implementation plan.
            requirements: Optional structured requirements.
            top_k_per_step: Results per plan step.

        Returns:
            CodeIntelligenceState with plan-aware context.
        """
        from app.rag.retrieval.plan_context_retriever import PlanContextRetriever

        state = CodeIntelligenceState(
            repository_path=repo_path,
            plan=plan,
            requirements=requirements,
            started_at=datetime.now(timezone.utc).isoformat(),
            status="running",
        )

        logger.info(
            "Code Intelligence workflow started (plan retrieval): %s",
            repo_path,
        )

        # Build index with indexes
        state = self._validate_repository(state)
        if state.status == "failed":
            return self._finalize(state)

        state = await self._build_index_with_indexes(state)
        if state.status == "failed":
            return self._finalize(state)

        # Plan-aware retrieval
        plan_retriever = PlanContextRetriever(
            index_builder=self._index_builder,
            retriever=self._retriever,
        )

        state.plan_context_result = await plan_retriever.retrieve_for_plan(
            plan=plan,
            requirements=requirements,
            repository_path=repo_path,
            top_k_per_step=top_k_per_step,
        )
        state.status = "completed"

        return self._finalize(state)

    # ── Nodes ──────────────────────────────────────────────

    def _validate_repository(self, state: CodeIntelligenceState) -> CodeIntelligenceState:
        """Validate that the repository exists and is accessible."""
        import os
        from pathlib import Path

        path = Path(state.repository_path).resolve()

        if not path.exists():
            state.status = "failed"
            state.errors.append(f"Path does not exist: {state.repository_path}")
            return state

        if not path.is_dir():
            state.status = "failed"
            state.errors.append(f"Path is not a directory: {state.repository_path}")
            return state

        if not os.access(str(path), os.R_OK):
            state.status = "failed"
            state.errors.append(f"Permission denied: {state.repository_path}")
            return state

        return state

    async def _analyze_repository(self, state: CodeIntelligenceState) -> CodeIntelligenceState:
        """Run Phase 2 repository analysis for metadata."""
        try:
            profile = self._repo_analyzer.analyze(state.repository_path)
            state.profile = profile
        except Exception as exc:
            state.warnings.append(f"Repository analysis failed (non-fatal): {exc}")

        return state

    async def _build_index(self, state: CodeIntelligenceState) -> CodeIntelligenceState:
        """Build the repository code index."""
        try:
            code_index = self._index_builder.build(state.repository_path)
            state.code_index = code_index

            if code_index.statistics.errors:
                state.errors.extend(code_index.statistics.errors)
            if code_index.statistics.warnings:
                state.warnings.extend(code_index.statistics.warnings)

        except Exception as exc:
            state.status = "failed"
            state.errors.append(f"Index build failed: {exc}")
            logger.error("Index build failed for %s: %s", state.repository_path, exc)

        return state

    async def _build_index_with_indexes(self, state: CodeIntelligenceState) -> CodeIntelligenceState:
        """Build the repository code index WITH individual indexes for retrieval."""
        try:
            code_index, lex_idx, sym_idx, vec_idx = self._index_builder.build_with_indexes(
                state.repository_path
            )
            state.code_index = code_index

            # Set indexes on retriever
            self._retriever.set_indexes(lex_idx, sym_idx, vec_idx, code_index.chunks)

            if code_index.statistics.errors:
                state.errors.extend(code_index.statistics.errors)
            if code_index.statistics.warnings:
                state.warnings.extend(code_index.statistics.warnings)

        except Exception as exc:
            state.status = "failed"
            state.errors.append(f"Index build failed: {exc}")
            logger.error("Index build failed for %s: %s", state.repository_path, exc)

        return state

    def _finalize(self, state: CodeIntelligenceState) -> CodeIntelligenceState:
        state.completed_at = datetime.now(timezone.utc).isoformat()

        logger.info(
            "Code Intelligence workflow %s: %s (errors=%d, warnings=%d)",
            state.status,
            state.repository_path,
            len(state.errors),
            len(state.warnings),
        )

        return state
