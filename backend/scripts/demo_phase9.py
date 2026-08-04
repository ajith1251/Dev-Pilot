"""
Phase 9 Quality Gate Demonstration (ASCII-safe for Windows consoles)

Shows three scenarios:
1. APPROVED - All requirements satisfied, tests pass, no blockers
2. REJECTED - Tests fail with security violation + test file deletion
3. APPROVED with minor findings - LOW findings don't block approval

This script exercises the full Phase 9 pipeline:
    ReviewContextBuilder -> DeterministicReview -> QualityGate
    (No LLM required - all deterministic)
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.base import new_id
from app.models.coding import FileChange, FileOperation, PatchSet
from app.models.issues import (
    ImplementationPlan, ImplementationStep,
    Requirement, RequirementType, StructuredRequirements,
)
from app.models.review import (
    QualityGateDecision, ReasonCode, RequirementCoverage,
    RequirementStatus, ReviewReport, TestSummary, SecuritySummary,
    ReviewFinding, FindingCategory, FindingSeverity, ReviewInput,
)
from app.models.testing import (
    ExecutionStatus, FailureCategory,
    ProcessExecutionResult, TestFailure, TestRunResult,
)
from app.services.deterministic_review import DeterministicReview
from app.services.quality_gate import QualityGate


def sep(title):
    print()
    print("=" * 72)
    print("  " + title)
    print("=" * 72)
    print()


def gate_decision(dec, label):
    print("  " + ("=" * 56))
    print("  >>> DECISION: " + dec.upper())
    print("  " + label)
    print("  " + ("=" * 56))


# =====================================================================
# SCENARIO 1: APPROVED
# =====================================================================

def scenario_approved():
    sep("SCENARIO 1: CLEAN APPROVAL")

    print("  Context:")
    print("    * 3 requirements, all covered by plan")
    print("    * Patch modifies auth/tokens.py")
    print("    * All 5 tests pass")
    print("    * No security issues")
    print("    * No repair needed")
    print()

    # 1. Requirements
    requirements = StructuredRequirements(
        objective="Fix password reset token expiration",
        requirements=[
            Requirement(description="Expired reset tokens must be rejected"),
            Requirement(description="Valid reset tokens must be accepted"),
            Requirement(description="Token expiration must be configurable"),
        ],
    )
    print("  + Requirements loaded (" + str(len(requirements.requirements)) + " total)")
    for i, r in enumerate(requirements.requirements):
        print("    REQ-" + str(i+1).zfill(3) + ": " + r.description)

    # 2. Plan
    plan = ImplementationPlan(
        summary="Add token expiration validation",
        objective="Ensure expired password reset tokens are rejected",
        steps=[
            ImplementationStep(
                id="STEP-001", title="Add token expiration validation",
                description="Add expiration check to token validation",
                affected_areas=["auth/tokens.py"],
                expected_changes="Add expires_at comparison",
            ),
            ImplementationStep(
                id="STEP-002", title="Add config for token TTL",
                description="Add TOKEN_TTL_SECONDS setting",
                affected_areas=["config.py", "auth/tokens.py"],
                expected_changes="Add TOKEN_TTL env var",
            ),
        ],
        requirements_coverage={
            "REQ-001": ["STEP-001"], "REQ-002": ["STEP-001"],
            "REQ-003": ["STEP-002"],
        },
    )
    print("  + Plan loaded (" + str(len(plan.steps)) + " steps)")

    # 3. Patch
    patch = PatchSet(
        patch_id="patch-clean-001",
        changes=[
            FileChange(
                change_id="CHANGE-001",
                operation=FileOperation.MODIFY,
                path="auth/tokens.py",
                new_content="def validate_reset_token(token):\n"
                            "    if token.expires_at < datetime.utcnow():\n"
                            "        raise TokenExpiredError()\n"
                            "    return token\n",
                reason="Add expiration check",
                plan_step_id="STEP-001",
                requirement_ids=["REQ-001", "REQ-002"],
            ),
            FileChange(
                change_id="CHANGE-002",
                operation=FileOperation.MODIFY,
                path="config.py",
                new_content="TOKEN_TTL_SECONDS = 3600",
                reason="Add configurable token TTL",
                plan_step_id="STEP-002",
                requirement_ids=["REQ-003"],
            ),
        ],
    )
    print("  + Patch loaded (" + str(len(patch.changes)) + " changes)")

    # 4. Test result (ALL PASS)
    test_result = TestRunResult(
        run_id="run-" + new_id()[:8],
        workspace_id="demo-ws",
        status=ExecutionStatus.PASSED,
        commands_total=1, commands_passed=1,
        tests_total=5, tests_passed=5, tests_failed=0,
        summary="All 5 tests passed in 0.32s",
    )
    print("  + Tests passed (5/5)")

    # 5. Run Deterministic Review
    print()
    print("  -- Running Deterministic Review --")
    inp = ReviewInput(
        workspace_id="demo-ws",
        requirements=requirements,
        implementation_plan=plan,
        original_patch=patch,
        test_result=test_result,
        changed_files=["auth/tokens.py", "config.py"],
    )
    deterministic = DeterministicReview()
    det_result = deterministic.run(inp)

    print("  " + ("+" if det_result.passed else "-") + " Passed: " + str(det_result.passed))
    print("  Findings: " + str(len(det_result.findings)))
    for f in det_result.findings:
        print("    [" + f.severity.value.upper() + "] " + f.title +
              (" [BLOCKING]" if f.blocking else ""))

    # 6. Build Review Report
    coverages = []
    for i, req in enumerate(requirements.requirements):
        rid = "REQ-" + str(i+1).zfill(3)
        coverages.append(RequirementCoverage(
            requirement_id=rid, requirement_description=req.description,
            status=RequirementStatus.SATISFIED,
            plan_steps=plan.requirements_coverage.get(rid, []),
            changed_files=["auth/tokens.py", "config.py"],
            evidence=["Tests pass", "Plan steps implemented"],
            tests=["test_expired_token", "test_valid_token", "test_ttl_config"],
        ))

    report = ReviewReport(
        review_id="review-" + new_id()[:8],
        workspace_id="demo-ws",
        test_summary=TestSummary(
            executed=True, status="passed",
            tests_passed=5, tests_failed=0,
            commands_total=1, commands_passed=1,
            duration_seconds=0.32,
        ),
        requirement_coverage=coverages,
        findings=det_result.findings,
        security_summary=SecuritySummary(passed=True),
        agent_summary="All requirements satisfied. Code quality acceptable.",
    )

    # 7. Run Quality Gate
    print()
    print("  -- Running Quality Gate --")
    gate = QualityGate()
    result = gate.decide(report, det_result, test_result)

    print()
    gate_decision(result.decision.value, "  " + result.summary[:120])
    print()
    print("  Detail:")
    print("    Requirements: " + str(result.requirements_satisfied) + " satisfied, "
          + str(result.requirements_partial) + " partial, "
          + str(result.requirements_unsatisfied) + " unsatisfied")
    print("    Verification: " + result.verification_status)
    print("    Security:     " + result.security_status)
    print("    Score:        " + str(result.score) + "/100")
    if result.reason_codes:
        print("    Reason codes: " + ", ".join(result.reason_codes))
    if result.blocking_findings:
        print("    Blocking:     " + str(result.blocking_findings))
    print()
    print("  >> RESULT: Clean approval - tests pass, reqs met, no blockers")


# =====================================================================
# SCENARIO 2: REJECTED
# =====================================================================

def scenario_rejected():
    sep("SCENARIO 2: REJECTED (Test Failure + Security + Tampering)")

    print("  Context:")
    print("    * 2 requirements")
    print("    * Patch uses shell=True (security violation)")
    print("    * 1 of 3 tests failed")
    print("    * Test file was deleted (!)")
    print()

    # 1. Requirements
    requirements = StructuredRequirements(
        objective="Add user export feature",
        requirements=[
            Requirement(description="Export users to CSV"),
            Requirement(description="Export must be safe with special characters"),
        ],
    )
    print("  + Requirements loaded (" + str(len(requirements.requirements)) + " total)")

    # 2. Plan
    plan = ImplementationPlan(
        summary="Add CSV user export",
        objective="Export user list to CSV format",
        steps=[
            ImplementationStep(
                id="STEP-001", title="Implement CSV export",
                description="Add CSV generation for user data",
                affected_areas=["api/export.py"],
                expected_changes="Add export endpoint",
            ),
        ],
        requirements_coverage={"REQ-001": ["STEP-001"], "REQ-002": ["STEP-001"]},
    )
    print("  + Plan loaded (" + str(len(plan.steps)) + " step)")

    # 3. BAD patch - security issue + test deletion
    patch = PatchSet(
        patch_id="patch-unsafe-001",
        changes=[
            FileChange(
                change_id="CHANGE-001",
                operation=FileOperation.CREATE,
                path="api/export.py",
                new_content=(
                    "import subprocess\n"
                    "\n"
                    "def export_users(usernames):\n"
                    "    for u in usernames:\n"
                    "        subprocess.run(f'echo {u}', shell=True)  # DANGEROUS\n"
                ),
                reason="Add CSV export endpoint",
            ),
            FileChange(
                change_id="CHANGE-002",
                operation=FileOperation.DELETE,
                path="tests/test_users.py",
                reason="Removed old test file",
            ),
        ],
    )
    print("  + Patch loaded (" + str(len(patch.changes)) + " changes)")
    print("    Contains: subprocess.run with shell=True")
    print("    Contains: test file deletion")

    # 4. Test result (1 FAILED)
    test_result = TestRunResult(
        run_id="run-" + new_id()[:8],
        workspace_id="demo-ws-2",
        status=ExecutionStatus.FAILED,
        commands_total=1, commands_passed=0, commands_failed=1,
        tests_total=3, tests_passed=1, tests_failed=1, tests_skipped=1,
        summary="1 failed, 1 skipped, 1 passed",
        failures=[
            TestFailure(
                failure_id="fail-001",
                test_name="test_export.py::test_csv_special_chars",
                failure_type=FailureCategory.ASSERTION_FAILURE,
                message="AssertionError: Expected 'hello, world' got 'hello world'",
                file_path="tests/test_export.py", line_number=25,
            ),
        ],
    )
    print("  - Tests: 1 failed, 1 passed, 1 skipped")

    # 5. Run Deterministic Review
    print()
    print("  -- Running Deterministic Review --")
    inp = ReviewInput(
        workspace_id="demo-ws-2",
        requirements=requirements,
        implementation_plan=plan,
        original_patch=patch,
        test_result=test_result,
        changed_files=["api/export.py", "tests/test_users.py"],
    )
    deterministic = DeterministicReview()
    det_result = deterministic.run(inp)

    print("  " + ("+" if det_result.passed else "-") + " Passed: " + str(det_result.passed))
    print("  Findings: " + str(len(det_result.findings)))
    for f in det_result.findings:
        print("    [" + f.severity.value.upper() + "] " + f.title +
              (" [BLOCKING]" if f.blocking else ""))

    # 6. Build Review Report
    coverages = [
        RequirementCoverage(
            requirement_id="REQ-001",
            requirement_description="Export users to CSV",
            status=RequirementStatus.PARTIALLY_SATISFIED,
            plan_steps=["STEP-001"], changed_files=["api/export.py"],
            evidence=["Export endpoint created"],
            notes="Export exists but has security issues",
        ),
        RequirementCoverage(
            requirement_id="REQ-002",
            requirement_description="Export must be safe with special characters",
            status=RequirementStatus.UNSATISFIED,
            plan_steps=["STEP-001"], changed_files=["api/export.py"],
            evidence=["Shell injection vulnerability present"],
            notes="shell=True with unescaped input is not safe",
        ),
    ]

    security_blocked = any(f.category.name == "SECURITY" for f in det_result.findings)

    report = ReviewReport(
        review_id="review-" + new_id()[:8],
        workspace_id="demo-ws-2",
        test_summary=TestSummary(
            executed=True, status="failed",
            tests_passed=1, tests_failed=1, tests_skipped=1,
            commands_total=1, commands_passed=0, commands_failed=1,
            has_skipped=True,
        ),
        requirement_coverage=coverages,
        findings=det_result.findings,
        security_summary=SecuritySummary(
            passed=not security_blocked,
            blocked_patterns=["subprocess.run with shell=True"],
            warnings=["Shell injection vulnerability in api/export.py"],
        ),
        agent_summary="Critical security issue and test failure detected.",
    )

    # 7. Run Quality Gate
    print()
    print("  -- Running Quality Gate --")
    gate = QualityGate()
    result = gate.decide(report, det_result, test_result)

    print()
    gate_decision(result.decision.value, "  " + result.summary[:120])
    print()
    print("  Detail:")
    print("    Requirements: " + str(result.requirements_satisfied) + " satisfied, "
          + str(result.requirements_partial) + " partial, "
          + str(result.requirements_unsatisfied) + " unsatisfied")
    print("    Verification: " + result.verification_status)
    print("    Security:     " + result.security_status)
    print("    Score:        " + str(result.score) + "/100")
    print("    Reason codes: " + ", ".join(result.reason_codes))
    if result.blocking_findings:
        print()
        print("  Blocking issues (" + str(len(result.blocking_findings)) + "):")
        for bf in result.blocking_findings:
            print("    [BLOCKER] " + bf)
    print()
    print("  >> RESULT: REJECTED for multiple reasons (tests, security, tampering)")


# =====================================================================
# SCENARIO 3: APPROVED WITH MINOR FINDINGS
# =====================================================================

def scenario_minor_findings():
    sep("SCENARIO 3: APPROVED WITH MINOR FINDINGS")

    print("  Context:")
    print("    * All tests pass")
    print("    * Reviewer noted minor style issues (LOW severity)")
    print("    * LOW/INFO findings should NOT block approval")
    print()

    test_result = TestRunResult(
        run_id="run-" + new_id()[:8],
        workspace_id="demo-ws-3",
        status=ExecutionStatus.PASSED,
        commands_total=1, commands_passed=1,
        tests_total=3, tests_passed=3,
        summary="All tests passed",
    )
    deterministic = DeterministicReview()
    det_result = deterministic.run(ReviewInput(
        workspace_id="demo-ws-3", test_result=test_result,
    ))

    report = ReviewReport(
        review_id="review-" + new_id()[:8],
        workspace_id="demo-ws-3",
        test_summary=TestSummary(
            executed=True, status="passed",
            tests_passed=3, commands_total=1, commands_passed=1,
        ),
        security_summary=SecuritySummary(passed=True),
        findings=det_result.findings + [
            ReviewFinding(
                finding_id="F-001",
                category=FindingCategory.MAINTAINABILITY,
                severity=FindingSeverity.LOW,
                title="Consider using f-strings instead of concatenation",
                description="String concatenation found in api/export.py",
                file_path="api/export.py", line_start=42,
                blocking=False, confidence=0.7,
            ),
            ReviewFinding(
                finding_id="F-002",
                category=FindingCategory.DOCUMENTATION,
                severity=FindingSeverity.INFO,
                title="Add docstring to export_users function",
                description="Function has no docstring",
                file_path="api/export.py",
                blocking=False, confidence=0.9,
            ),
        ],
        agent_summary="Implementation correct. Minor improvements suggested.",
    )

    gate = QualityGate()
    result = gate.decide(report, det_result, test_result)

    gate_decision(result.decision.value,
                  "  LOW findings (" + str(len(report.findings)) + ") do not block")
    print()
    print("  Detail:")
    print("    Tests:              " + report.test_summary.status)
    print("    Blocking findings:  " + str(len(result.blocking_findings)))
    print("    Total findings:     " + str(len(report.findings)))
    print("    Score:              " + str(result.score) + "/100")
    print("    Reason codes:       " + ", ".join(result.reason_codes))
    print()
    print("  >> RESULT: APPROVED - minor style issues don't block")
    print("     The Quality Gate requires CRITICAL/HIGH to reject")


# =====================================================================
# MAIN
# =====================================================================

def main():
    print()
    print("+" + "-" * 70 + "+")
    print("|        DevPilot Phase 9 - Quality Gate Demonstration         |")
    print("+" + "-" * 70 + "+")
    print()
    print("  Demonstrating the deterministic Quality Gate with 3 scenarios.")
    print("  No LLM required - all decisions are 100% deterministic.")
    print()

    scenario_approved()
    print()
    print("  --- Moving to Scenario 2 ---")
    print()

    scenario_rejected()
    print()
    print("  --- Moving to Scenario 3 ---")
    print()

    scenario_minor_findings()

    print()
    print("=" * 72)
    print("  SUMMARY")
    print("=" * 72)
    print()
    print("  The Quality Gate demonstrates the fundamental Phase 9 invariant:")
    print()
    print("    > Passing tests alone are NOT automatic approval.")
    print("    > Deterministic evidence and Gate policy determine acceptance.")
    print()
    print("  Key rules demonstrated:")
    print("    * Tests pass + reqs met + no blockers    -> APPROVED")
    print("    * Tests fail or security issues          -> REJECTED")
    print("    * LOW findings alone                     -> APPROVED")
    print("    * Test deletion + security                -> REJECTED")
    print()


if __name__ == "__main__":
    main()
