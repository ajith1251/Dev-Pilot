"""
Review Service — Phase 9 review orchestrator.

Orchestrates the complete review pipeline:
1. Validate review input
2. Build review context (ReviewContextBuilder)
3. Run deterministic review checks (DeterministicReview)
4. Run optional ReviewerAgent
5. Validate agent evidence (ReviewEvidenceValidator)
6. Build ReviewReport
7. Invoke QualityGate
8. Return final review result

This service does NOT modify files, execute processes, or make gate decisions.
It delegates to specialized services for each concern.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.agents.reviewer import ReviewerAgent, ReviewerAgentInput
from app.config import settings
from app.models.base import new_id
from app.models.coding import PatchApplicationResult, PatchSet
from app.models.issues import ImplementationPlan, StructuredRequirements
from app.models.profile import RepositoryProfile
from app.models.rag import RetrievedContext
from app.models.repair import RepairResult
from app.models.review import (
    AgentReview,
    DeterministicReviewResult,
    PlanStepAssessment,
    PlanStepStatus,
    QualityGateResult,
    RequirementCoverage,
    RequirementStatus,
    ReviewCapabilities,
    ReviewContext,
    ReviewFinding,
    ReviewInput,
    ReviewReport,
    TestSummary,
)
from app.models.testing import TestRunResult
from app.services.deterministic_review import DeterministicReview
from app.services.quality_gate import QualityGate
from app.services.review_context_builder import ReviewContextBuilder
from app.services.review_evidence_validator import ReviewEvidenceValidator


class ReviewService:
    """Orchestrates the complete Phase 9 review pipeline.

    Flow:
    1. Validate review input
    2. Build bounded review context (ReviewContextBuilder)
    3. Run deterministic review checks (DeterministicReview)
    4. Run optional ReviewerAgent (LLM-assisted)
    5. Validate agent evidence (ReviewEvidenceValidator)
    6. Build ReviewReport from all evidence
    7. Invoke QualityGate
    8. Return QualityGateResult
    """

    def __init__(
        self,
        context_builder: Optional[ReviewContextBuilder] = None,
        deterministic_review: Optional[DeterministicReview] = None,
        reviewer_agent: Optional[ReviewerAgent] = None,
        evidence_validator: Optional[ReviewEvidenceValidator] = None,
        quality_gate: Optional[QualityGate] = None,
        use_llm: bool = False,
    ):
        self._context_builder = context_builder or ReviewContextBuilder()
        self._deterministic_review = deterministic_review or DeterministicReview()
        self._reviewer_agent = reviewer_agent or ReviewerAgent()
        self._evidence_validator = evidence_validator or ReviewEvidenceValidator()
        self._quality_gate = quality_gate or QualityGate()
        self._use_llm = use_llm

    # ── Main Entry Point ────────────────────────────────────────

    async def run_review(
        self,
        workspace_id: str = "",
        workspace_root: str = "",
        requirements: Optional[StructuredRequirements] = None,
        implementation_plan: Optional[ImplementationPlan] = None,
        original_patch: Optional[PatchSet] = None,
        patch_application: Optional[PatchApplicationResult] = None,
        repair_result: Optional[RepairResult] = None,
        test_result: Optional[TestRunResult] = None,
        repository_profile: Optional[RepositoryProfile] = None,
        retrieved_context: Optional[RetrievedContext] = None,
        changed_files: Optional[List[str]] = None,
        final_workspace_metadata: Optional[Dict[str, Any]] = None,
        extra_context: Optional[Dict[str, Any]] = None,
        use_llm: Optional[bool] = None,
        agent_context: Any = None,
    ) -> Tuple[ReviewReport, QualityGateResult]:
        """Run the complete review pipeline.

        Args:
            See ReviewInput for parameter details.
            use_llm: Override LLM setting for this review.

        Returns:
            Tuple of (ReviewReport, QualityGateResult).
        """
        start_time = time.time()
        warnings: List[str] = []
        review_id = f"review-{new_id()[:8]}"

        # 0. Build review input
        inp = ReviewInput(
            workspace_id=workspace_id,
            workspace_root=workspace_root,
            requirements=requirements,
            implementation_plan=implementation_plan,
            original_patch=original_patch,
            patch_application=patch_application,
            repair_result=repair_result,
            test_result=test_result,
            repository_profile=repository_profile,
            retrieved_context=retrieved_context,
            changed_files=changed_files or [],
            final_workspace_metadata=final_workspace_metadata or {},
            extra_context=extra_context or {},
        )

        # 1. Build review context
        try:
            context = self._context_builder.build(inp)
            warnings.extend(context.warnings)
        except Exception as exc:
            return self._error_result(
                review_id=review_id,
                workspace_id=workspace_id,
                error=f"Context building failed: {exc}",
                start_time=start_time,
            )

        # 2. Run deterministic review checks
        try:
            deterministic_result = self._deterministic_review.run(inp)
            warnings.extend(deterministic_result.warnings)
        except Exception as exc:
            return self._error_result(
                review_id=review_id,
                workspace_id=workspace_id,
                error=f"Deterministic review failed: {exc}",
                start_time=start_time,
            )

        # 3. Run optional ReviewerAgent
        agent_review = AgentReview(findings=[], summary="")
        effective_use_llm = use_llm if use_llm is not None else self._use_llm

        if effective_use_llm:
            try:
                agent_input = ReviewerAgentInput(
                    context=context,
                    review_input=inp,
                    use_llm=True,
                    agent_context=agent_context,
                )
                agent_review = await self._reviewer_agent.run(agent_input)
                warnings.extend(agent_review.warnings)
            except Exception as exc:
                warnings.append(f"ReviewerAgent failed: {exc}")
                agent_review = AgentReview(
                    summary="ReviewerAgent failed — continuing with deterministic review only",
                    warnings=[f"Agent failed: {exc}"],
                )
        else:
            # Use deterministic-only mode
            agent_review = self._reviewer_agent._execute_deterministic(
                ReviewerAgentInput(context=context, review_input=inp, agent_context=agent_context)
            )

        # 4. Validate agent evidence
        try:
            self._evidence_validator.prepare(inp)
            validated_agent = self._evidence_validator.validate(agent_review)
        except Exception as exc:
            warnings.append(f"Evidence validation failed: {exc}")
            validated_agent = agent_review

        # 5. Build requirement coverage
        requirement_coverage = self._build_requirement_coverage(
            inp, deterministic_result, validated_agent
        )

        # 6. Build plan assessment
        plan_assessment = self._build_plan_assessment(
            inp, deterministic_result
        )

        # 7. Build test summary
        test_summary = deterministic_result.test_summary

        # 8. Build repair summary
        repair_summary = self._build_repair_summary(inp)

        # 9. Build ReviewReport
        all_findings = deterministic_result.findings + validated_agent.findings
        report = ReviewReport(
            review_id=review_id,
            workspace_id=workspace_id,
            requirement_coverage=requirement_coverage + validated_agent.requirement_assessments,
            plan_assessment=plan_assessment,
            findings=all_findings,
            test_summary=test_summary,
            repair_summary=repair_summary,
            security_summary=deterministic_result.security_summary,
            scope_summary=deterministic_result.scope_summary,
            agent_summary=validated_agent.summary,
            duration_seconds=time.time() - start_time,
            warnings=warnings,
        )

        # 10. Invoke QualityGate
        try:
            gate_result = self._quality_gate.decide(
                report=report,
                deterministic_result=deterministic_result,
                test_result=test_result,
            )
        except Exception as exc:
            return self._error_result(
                review_id=review_id,
                workspace_id=workspace_id,
                error=f"QualityGate failed: {exc}",
                start_time=start_time,
            )

        return report, gate_result

    # ── Requirement Coverage Builder ────────────────────────────

    def _build_requirement_coverage(
        self,
        inp: ReviewInput,
        deterministic_result: DeterministicReviewResult,
        agent_review: AgentReview,
    ) -> List[RequirementCoverage]:
        """Build deterministic requirement coverage assessment."""
        coverage: List[RequirementCoverage] = []

        if not inp.requirements:
            return coverage

        requirements = inp.requirements.requirements
        plan = inp.implementation_plan

        for i, req in enumerate(requirements):
            req_id = f"REQ-{i+1:03d}"
            desc = req.description if hasattr(req, "description") else str(req)

            # Find plan steps that cover this requirement
            plan_steps: List[str] = []
            if plan and plan.requirements_coverage:
                plan_steps = plan.requirements_coverage.get(req_id, [])

            # Find changed files
            changed_files: List[str] = []
            if inp.original_patch:
                if plan_steps:
                    for step in plan.steps:
                        if step.id in plan_steps:
                            changed_files.extend(step.affected_areas)
                else:
                    # No explicit mapping — use existing changed files
                    changed_files = inp.changed_files or []

            # Determine status
            # If tests passed and plan has mapping → SATISFIED
            # If tests passed but no mapping → PARTIALLY_SATISFIED
            # If tests failed → UNSATISFIED
            # If no tests → UNVERIFIED

            ts = deterministic_result.test_summary
            evidence: List[str] = []

            if ts.status == "passed":
                if plan_steps:
                    status = RequirementStatus.SATISFIED
                else:
                    status = RequirementStatus.PARTIALLY_SATISFIED
                evidence.append("Tests pass")
            elif ts.status in ("failed", "timeout", "error"):
                status = RequirementStatus.UNSATISFIED
                evidence.append(f"Tests: {ts.status}")
            else:
                status = RequirementStatus.UNVERIFIED
                evidence.append("No verification evidence")

            if plan_steps:
                evidence.append(f"Mapped to steps: {', '.join(plan_steps)}")

            coverage.append(RequirementCoverage(
                requirement_id=req_id,
                requirement_description=desc,
                status=status,
                plan_steps=plan_steps,
                changed_files=list(set(changed_files)),
                evidence=evidence,
                notes="",
            ))

        return coverage

    # ── Plan Assessment Builder ────────────────────────────────

    def _build_plan_assessment(
        self,
        inp: ReviewInput,
        deterministic_result: DeterministicReviewResult,
    ) -> List[PlanStepAssessment]:
        """Build plan step assessment."""
        assessments: List[PlanStepAssessment] = []

        if not inp.implementation_plan:
            return assessments

        # Determine if implementation was done (tests ran, files changed)
        has_implementation = bool(
            deterministic_result.test_summary.executed
            and inp.original_patch
        )

        for step in inp.implementation_plan.steps:
            if has_implementation:
                status = PlanStepStatus.IMPLEMENTED
            else:
                status = PlanStepStatus.MISSING

            assessments.append(PlanStepAssessment(
                step_id=step.id,
                step_title=step.title,
                status=status,
                changed_files=step.affected_areas,
                notes="",
            ))

        return assessments

    # ── Repair Summary Builder ─────────────────────────────────

    @staticmethod
    def _build_repair_summary(inp: ReviewInput) -> "RepairSummary":
        """Build repair summary from repair result."""
        from app.models.review import RepairSummary

        if not inp.repair_result:
            return RepairSummary()

        result = inp.repair_result
        return RepairSummary(
            attempted=True,
            status=result.status.value if hasattr(result.status, "value") else str(result.status),
            attempts=result.attempts,
            stop_reason=result.stop_reason,
            remaining_failures=len(result.remaining_failures),
        )

    # ── Error Result ───────────────────────────────────────────

    @staticmethod
    def _error_result(
        review_id: str,
        workspace_id: str,
        error: str,
        start_time: float,
    ) -> Tuple[ReviewReport, QualityGateResult]:
        """Build an error result when the review pipeline fails."""
        duration = time.time() - start_time
        report = ReviewReport(
            review_id=review_id,
            workspace_id=workspace_id,
            warnings=[error],
            duration_seconds=duration,
        )
        gate = QualityGateResult(
            review_id=review_id,
            decision="incomplete",
            verification_status="error",
            summary=f"Review pipeline error: {error}",
            reason_codes=["incomplete_review"],
        )
        return report, gate

    # ── Capabilities ───────────────────────────────────────────

    def get_capabilities(self) -> ReviewCapabilities:
        """Return current review capabilities."""
        return ReviewCapabilities(
            llm_review_available=self._use_llm,
        )
