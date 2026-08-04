"""
Quality Gate — Phase 9 deterministic gate.

The LLM must NOT override mandatory gate rules.
The QualityGate makes the final decision based on:
- Deterministic review findings
- Test results
- Requirement coverage
- Repair result
- Security checks

This is the authoritative decision maker. LLM findings are advisory only.
"""

from __future__ import annotations

from typing import List, Optional

from app.models.review import (
    DeterministicReviewResult,
    FindingSeverity,
    QualityGateDecision,
    QualityGateResult,
    QualityMetrics,
    ReasonCode,
    RequirementCoverage,
    RequirementStatus,
    ReviewFinding,
    ReviewReport,
)
from app.models.testing import ExecutionStatus, TestRunResult


class QualityGate:
    """Deterministic quality gate that makes the final decision.

    Architecture:
        DeterministicReview findings ─────┐
        TestRunResult                     │
        RequirementCoverage               ├──→ QualityGate → Decision
        RepairResult                      │
        Security checks                   ┘

    NOT:
        LLM says "looks good" → APPROVED
    """

    def __init__(
        self,
        require_human_for_unverified: bool = True,
    ):
        self._require_human_for_unverified = require_human_for_unverified

    def decide(
        self,
        report: ReviewReport,
        deterministic_result: DeterministicReviewResult,
        test_result: Optional[TestRunResult] = None,
    ) -> QualityGateResult:
        """Make the final quality gate decision.

        Args:
            report: Complete review report with findings and coverage.
            deterministic_result: Result of deterministic checks.
            test_result: Final test results.

        Returns:
            QualityGateResult with the authoritative decision.
        """
        reason_codes: List[str] = []
        blocking_findings: List[str] = []
        warnings: List[str] = []

        # ── Collect evidence ─────────────────────────────────────

        all_findings = deterministic_result.findings + report.findings
        blocking = [f for f in all_findings if f.blocking]
        critical_security = [
            f for f in all_findings
            if f.category.name == "SECURITY" and f.severity in (
                FindingSeverity.CRITICAL, FindingSeverity.HIGH
            )
        ]
        high_correctness = [
            f for f in all_findings
            if f.category.name == "CORRECTNESS" and f.severity == FindingSeverity.CRITICAL
        ]

        requirements_satisfied = sum(
            1 for c in report.requirement_coverage
            if c.status == RequirementStatus.SATISFIED
        )
        requirements_partial = sum(
            1 for c in report.requirement_coverage
            if c.status == RequirementStatus.PARTIALLY_SATISFIED
        )
        requirements_unsatisfied = sum(
            1 for c in report.requirement_coverage
            if c.status == RequirementStatus.UNSATISFIED
        )
        requirements_unverified = sum(
            1 for c in report.requirement_coverage
            if c.status in (RequirementStatus.UNVERIFIED, RequirementStatus.NOT_APPLICABLE)
        )

        verification_status = report.test_summary.status if report.test_summary.executed else "missing"
        security_ok = report.security_summary.passed

        # ── Hard Rejection Rules ─────────────────────────────────

        # Rule 1: Final required tests FAILED
        if test_result and test_result.status in (
            ExecutionStatus.FAILED,
            ExecutionStatus.TIMEOUT,
            ExecutionStatus.ERROR,
        ):
            reason_codes.append(ReasonCode.TESTS_FAILED.value)
            blocking_findings.append("Final tests failed or errored")

        # Rule 2: Unresolved CRITICAL blocking finding
        for f in blocking:
            if f.severity in (FindingSeverity.CRITICAL, FindingSeverity.HIGH):
                blocking_findings.append(f.title)
                if f.category.name == "SECURITY":
                    reason_codes.append(ReasonCode.SECURITY_BLOCKER.value)
                elif f.category.name == "CORRECTNESS":
                    reason_codes.append(ReasonCode.CRITICAL_FINDING.value)
                else:
                    reason_codes.append(ReasonCode.CRITICAL_FINDING.value)

        # Rule 3: Required requirement UNSATISFIED
        if requirements_unsatisfied > 0:
            reason_codes.append(ReasonCode.REQUIREMENT_UNSATISFIED.value)
            blocking_findings.append(
                f"{requirements_unsatisfied} required requirement(s) unsatisfied"
            )

        # Rule 4: Test tampering detected
        tampering = [f for f in all_findings if f.category.name == "TAMPERING"]
        if tampering:
            reason_codes.append(ReasonCode.TEST_TAMPERING.value)
            for f in tampering:
                blocking_findings.append(f.title)

        # Rule 5: Security boundary bypass
        if critical_security:
            reason_codes.append(ReasonCode.SECURITY_BLOCKER.value)
            for f in critical_security:
                blocking_findings.append(f.title)

        # Rule 6: Unresolved repair
        if report.repair_summary.attempted and report.repair_summary.status in (
            "max_attempts", "failed", "no_progress", "unsafe_repair"
        ):
            reason_codes.append(ReasonCode.UNRESOLVED_REPAIR.value)
            blocking_findings.append(
                f"Repair unresolved: {report.repair_summary.stop_reason}"
            )

        # Rule 7: Missing verification
        if not report.test_summary.executed:
            reason_codes.append(ReasonCode.MISSING_VERIFICATION.value)
            blocking_findings.append("No verification was executed")

        # ── Human Review Rules ────────────────────────────────────

        human_review_triggers: List[str] = []

        # Insufficient evidence
        if requirements_unverified > 0 and self._require_human_for_unverified:
            human_review_triggers.append(
                f"{requirements_unverified} requirement(s) unverified"
            )

        # Conflicting evidence
        if blocking_findings and sum(1 for f in all_findings if not f.blocking) == 0:
            # All findings are blocking — no positive evidence
            pass  # This will be rejected, not human review

        # Deterministic checks failed but not critically
        if not deterministic_result.passed and not blocking_findings:
            human_review_triggers.append("Deterministic checks flagged warnings")

        # ── Decision ──────────────────────────────────────────────

        decision = QualityGateDecision.APPROVED
        summary_parts: List[str] = []

        if blocking_findings:
            # Has blocking issues → REJECTED
            decision = QualityGateDecision.REJECTED
            summary_parts.append(f"REJECTED: {', '.join(blocking_findings[:3])}")
            if len(blocking_findings) > 3:
                summary_parts.append(f"+ {len(blocking_findings) - 3} more blocking issue(s)")
        elif human_review_triggers:
            decision = QualityGateDecision.NEEDS_HUMAN_REVIEW
            summary_parts.append(f"NEEDS HUMAN REVIEW: {', '.join(human_review_triggers[:3])}")
        else:
            summary_parts.append("APPROVED")
            reason_codes.append(ReasonCode.REVIEW_PASSED.value)

        # Build summary
        summary_parts.append(f"Requirements: {requirements_satisfied} satisfied, "
                             f"{requirements_partial} partial, "
                             f"{requirements_unsatisfied} unsatisfied, "
                             f"{requirements_unverified} unverified")
        summary_parts.append(f"Verification: {verification_status}")
        summary_parts.append(f"Security: {'PASS' if security_ok else 'ISSUES DETECTED'}")

        summary = " | ".join(summary_parts)

        # Deduplicate reason codes
        reason_codes = list(dict.fromkeys(reason_codes))

        # Compute heurisitic quality score (informational only)
        metrics = self._compute_metrics(report, deterministic_result)

        return QualityGateResult(
            review_id=report.review_id,
            decision=decision,
            blocking_findings=blocking_findings,
            warnings=warnings,
            requirements_status=(
                RequirementStatus.SATISFIED
                if requirements_unsatisfied == 0 and requirements_unverified == 0
                else RequirementStatus.PARTIALLY_SATISFIED
                if requirements_unsatisfied == 0
                else RequirementStatus.UNSATISFIED
            ),
            requirements_satisfied=requirements_satisfied,
            requirements_partial=requirements_partial,
            requirements_unsatisfied=requirements_unsatisfied,
            requirements_unverified=requirements_unverified,
            verification_status=verification_status,
            security_status="PASS" if security_ok else "FAIL",
            score=metrics.overall,
            reason_codes=reason_codes,
            summary=summary,
        )

    def _compute_metrics(
        self,
        report: ReviewReport,
        deterministic_result: DeterministicReviewResult,
    ) -> QualityMetrics:
        """Compute heuristic quality metrics (informational only).

        These metrics are NOT the primary decision mechanism.
        Hard gates always override scores.
        """
        score = QualityMetrics()

        # Requirements score
        if report.requirement_coverage:
            satisfied = sum(
                1 for c in report.requirement_coverage
                if c.status == RequirementStatus.SATISFIED
            )
            total = len(report.requirement_coverage)
            score.requirements = round((satisfied / total) * 100, 1) if total > 0 else 0

        # Testing score
        ts = report.test_summary
        total_tests = (ts.tests_passed or 0) + (ts.tests_failed or 0) + (ts.tests_skipped or 0)
        if ts.executed and total_tests > 0:
            passed_ratio = (ts.tests_passed or 0) / total_tests
            score.testing = round(passed_ratio * 100, 1)
            if ts.has_timeout:
                score.testing *= 0.8
        elif ts.executed and ts.commands_total > 0:
            passed_ratio = ts.commands_passed / ts.commands_total
            score.testing = round(passed_ratio * 100, 1)
        else:
            score.testing = 0

        # Security score
        security_blockers = [
            f for f in deterministic_result.findings
            if f.category.name == "SECURITY" and f.blocking
        ]
        score.security = 0 if security_blockers else (
            70 if report.security_summary.warnings else 100
        )

        # Correctness score
        correctness_blockers = [
            f for f in deterministic_result.findings
            if f.category.name == "CORRECTNESS" and f.blocking
        ]
        score.correctness = 0 if correctness_blockers else (
            70 if report.test_summary.status == "failed" else 100
        )

        # Scope score
        scope_issues = [
            f for f in deterministic_result.findings
            if f.category.name == "SCOPE"
        ]
        score.scope = max(0, 100 - len(scope_issues) * 20)

        # Maintainability and architecture scores
        finding_count = len(report.findings) + len(deterministic_result.findings)
        score.maintainability = max(60, 100 - finding_count * 5)
        score.architecture = max(60, 100 - finding_count * 3)

        return score
