"""
Plan Context Retriever — integrates Phase 4 ImplementationPlan with Phase 5
hybrid retrieval to retrieve context relevant to each plan step.

This is the key Phase 4 → Phase 5 integration point.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from app.core.logging import logger
from app.models.issues import ImplementationPlan, StructuredRequirements
from app.models.rag import (
    PlanAwareRetrievalInput,
    PlanAwareRetrievalResult,
    RetrievalFilter,
    RetrievalQuery,
    RetrievedContext,
    StepContext,
)
from app.rag.retrieval.hybrid_retriever import HybridRetriever
from app.services.index_builder import RepositoryIndexBuilder


class PlanContextRetriever:
    """Retrieves repository context relevant to an ImplementationPlan.

    For each plan step, constructs a targeted retrieval query and
    retrieves the most relevant code chunks, symbols, and files.
    """

    def __init__(
        self,
        index_builder: Optional[RepositoryIndexBuilder] = None,
        retriever: Optional[HybridRetriever] = None,
        enable_embeddings: bool = False,
    ) -> None:
        self.index_builder = index_builder or RepositoryIndexBuilder(
            enable_embeddings=enable_embeddings
        )
        self.retriever = retriever or HybridRetriever()
        self.enable_embeddings = enable_embeddings

    async def retrieve_for_plan(
        self,
        plan: ImplementationPlan,
        requirements: Optional[StructuredRequirements] = None,
        repository_path: str = "",
        top_k_per_step: int = 5,
    ) -> PlanAwareRetrievalResult:
        """Retrieve context relevant to each step in an implementation plan.

        Args:
            plan: The implementation plan.
            requirements: Optional structured requirements for additional context.
            repository_path: Path to the repository.
            top_k_per_step: Top-K results per step.

        Returns:
            PlanAwareRetrievalResult with context for each step.
        """
        warnings: List[str] = []

        if not repository_path:
            warnings.append("No repository path provided")

        result = PlanAwareRetrievalResult(warnings=warnings)

        # ── Build index if not already built ────────────────
        try:
            code_index, lex_idx, sym_idx, vec_idx = self.index_builder.build_with_indexes(
                repo_path=repository_path,
            )
        except Exception as exc:
            warnings.append(f"Index build failed: {exc}")
            return PlanAwareRetrievalResult(warnings=warnings)

        # Set indexes in retriever
        self.retriever.set_indexes(
            lexical=lex_idx,
            symbol=sym_idx,
            vector=vec_idx,
            chunks=code_index.chunks,
        )

        # ── Build query for each step ───────────────────────
        total_chunks = 0

        for step in plan.steps:
            query_text = self._build_step_query(
                step_title=step.title,
                step_description=step.description,
                expected_changes=step.expected_changes,
                affected_areas=step.affected_areas,
                requirements=requirements,
            )

            query = RetrievalQuery(
                text=query_text,
                snapshot_id=code_index.snapshot.snapshot_id,
                plan_step=step.id,
                requirement_ids=[],
                likely_affected_areas=step.affected_areas,
                top_k=top_k_per_step,
                max_chunks_per_file=3,
            )

            retrieved = self.retriever.retrieve(query)

            step_context = StepContext(
                step_id=step.id,
                step_title=step.title,
                query=query_text,
                context=retrieved,
            )
            result.steps.append(step_context)
            total_chunks += len(retrieved.items)

            logger.debug(
                "Step %s: retrieved %d chunks (query: %s)",
                step.id, len(retrieved.items), query_text[:80],
            )

        result.total_chunks = total_chunks

        return result

    def _build_step_query(
        self,
        step_title: str,
        step_description: str,
        expected_changes: str,
        affected_areas: List[str],
        requirements: Optional[StructuredRequirements] = None,
    ) -> str:
        """Build a retrieval query text for a single plan step.

        Combines step title, description, expected changes, and
        affected areas into a rich query for hybrid retrieval.
        """
        parts: List[str] = []

        # Step title is primary signal
        parts.append(step_title)

        # Description adds context
        if step_description:
            parts.append(step_description[:300])

        # Expected changes
        if expected_changes:
            parts.append(expected_changes[:200])

        # Affected areas
        if affected_areas:
            parts.append(" ".join(affected_areas))

        # Requirement objective
        if requirements and requirements.objective:
            parts.append(requirements.objective[:200])

        return " ".join(parts)
