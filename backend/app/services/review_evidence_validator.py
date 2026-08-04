"""
Review Evidence Validator — Phase 9 deterministic service.

Validates that ReviewerAgent findings reference only known context:
- Files that exist in the changed files list
- Requirements that exist in the input
- Plan steps that exist in the plan
- Test evidence that was provided
- No invented/hallucinated evidence

Findings without support should not become authoritative blockers.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set

from app.models.issues import ImplementationPlan
from app.models.review import (
    AgentReview,
    FindingCategory,
    FindingSeverity,
    ReviewFinding,
    ReviewInput,
)


class ReviewEvidenceValidator:
    """Validates that review findings reference only known context.

    Responsibilities:
    - Validate file paths exist in changed files
    - Validate requirement IDs exist
    - Validate plan step IDs exist
    - Validate test evidence references
    - Downgrade or reject hallucinated findings
    """

    def __init__(self) -> None:
        self._known_files: Set[str] = set()
        self._known_requirements: Set[str] = set()
        self._known_plan_steps: Set[str] = set()
        self._known_patches: Set[str] = set()

    def prepare(self, inp: ReviewInput) -> None:
        """Prepare the validator with known context from the review input."""
        # Known files
        if inp.original_patch:
            for c in inp.original_patch.changes:
                self._known_files.add(c.path)
        if inp.patch_application:
            self._known_files.update(inp.patch_application.files_created)
            self._known_files.update(inp.patch_application.files_modified)
            self._known_files.update(inp.patch_application.files_deleted)
        if inp.changed_files:
            self._known_files.update(inp.changed_files)

        # Known requirements
        if inp.requirements and inp.requirements.requirements:
            for i, req in enumerate(inp.requirements.requirements):
                self._known_requirements.add(f"REQ-{i+1:03d}")

        # Known plan steps
        if inp.implementation_plan:
            for step in inp.implementation_plan.steps:
                self._known_plan_steps.add(step.id)

        # Known patches/repairs
        if inp.repair_result and inp.repair_result.session:
            for attempt in inp.repair_result.session.attempts:
                if attempt.proposal:
                    self._known_patches.add(attempt.proposal.proposal_id)

    def validate(self, agent_review: AgentReview) -> AgentReview:
        """Validate all findings in an AgentReview.

        Invalid findings are downgraded or flagged with warnings.
        """
        validated_findings: List[ReviewFinding] = []
        warnings: List[str] = []

        for finding in agent_review.findings:
            validated = self._validate_finding(finding)
            if validated is None:
                warnings.append(
                    f"Removed invalid finding '{finding.title}': "
                    f"references unknown context"
                )
            else:
                validated_findings.append(validated)

        return AgentReview(
            findings=validated_findings,
            requirement_assessments=agent_review.requirement_assessments,
            summary=agent_review.summary,
            warnings=agent_review.warnings + warnings,
        )

    def _validate_finding(
        self, finding: ReviewFinding
    ) -> Optional[ReviewFinding]:
        """Validate a single finding. Returns None if entirely invalid."""
        valid = True
        validation_notes: List[str] = []

        # Validate file_path
        if finding.file_path:
            if self._known_files and finding.file_path not in self._known_files:
                # Check if it's a partial match
                if not any(finding.file_path.endswith(f) or f.endswith(finding.file_path) for f in self._known_files):
                    valid = False
                    validation_notes.append(
                        f"File '{finding.file_path}' not in known changed files"
                    )
                    # Downgrade severity since evidence is uncertain
                    finding.severity = FindingSeverity.LOW
                    finding.confidence = min(finding.confidence, 0.3)
                    finding.blocking = False

        # Validate requirement_ids
        if finding.requirement_ids:
            unknown_reqs = [
                r for r in finding.requirement_ids
                if self._known_requirements and r not in self._known_requirements
            ]
            if unknown_reqs:
                validation_notes.append(
                    f"Unknown requirement IDs: {unknown_reqs}"
                )
                finding.requirement_ids = [
                    r for r in finding.requirement_ids
                    if r in self._known_requirements
                ]

        # Validate plan_step_ids
        if finding.plan_step_ids:
            unknown_steps = [
                s for s in finding.plan_step_ids
                if self._known_plan_steps and s not in self._known_plan_steps
            ]
            if unknown_steps:
                validation_notes.append(
                    f"Unknown plan step IDs: {unknown_steps}"
                )
                finding.plan_step_ids = [
                    s for s in finding.plan_step_ids
                    if s in self._known_plan_steps
                ]

        if not valid:
            return None

        # Add validation notes to evidence
        if validation_notes:
            finding.evidence.extend(validation_notes)

        return finding

    def validate_assessments(
        self,
        assessments: list,
    ) -> list:
        """Validate requirement assessments."""
        validated = []
        for assessment in assessments:
            req_id = getattr(assessment, "requirement_id", "")
            if self._known_requirements and req_id not in self._known_requirements:
                continue
            validated.append(assessment)
        return validated
