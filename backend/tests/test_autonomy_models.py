"""
Tests for Phase 16 autonomy models — goal model, criteria extraction,
budget limits, progress tracking, plan versions, and scope control.

Deterministic only; no LLM or live PostgreSQL required.
"""

from __future__ import annotations

from app.models.autonomy import (
    AcceptanceCriterion,
    AutonomousRunState,
    AutonomyPolicy,
    CriterionStatus,
    CriterionType,
    ExecutionBudget,
    ExecutionGoal,
    ExecutionState,
    GoalProgress,
    IterationEvidence,
    PlanVersion,
    ProgressTrend,
    TaskScope,
)
from app.models.issues import (
    ImplementationPlan,
    ImplementationStep,
    Requirement,
    RequirementType,
    StructuredRequirements,
)
from app.services.autonomy_service import (
    BudgetManager,
    GoalEvaluator,
    ProgressEvaluator,
    StuckDetector,
    classify_failure,
)


class TestExecutionGoalModel:
    def test_goal_defaults(self) -> None:
        goal = ExecutionGoal(
            goal_id="GOAL-TEST1",
            task="Fix expired-token validation",
        )
        assert goal.status == ExecutionState.RUNNING
        assert goal.attempt == 0
        assert goal.replan_count == 0
        assert goal.progress.criteria_total == 0

    def test_goal_progress_initialized(self) -> None:
        goal = ExecutionGoal(
            goal_id="GOAL-TEST2",
            task="Task",
            acceptance_criteria=[AcceptanceCriterion(description="C1", verification="suite:pass")],
        )
        assert len(goal.acceptance_criteria) == 1
        assert goal.progress.criteria_total == 0  # updated by GoalEvaluator.evaluate

    def test_criteria_summary_bounded(self) -> None:
        goal = ExecutionGoal(
            goal_id="GOAL-TEST3",
            task="t" * 500,  # exactly at the model max
            acceptance_criteria=[AcceptanceCriterion(description="C1", verification="suite:pass")],
        )
        summary = goal.criteria_summary()
        assert summary["goal_id"] == "GOAL-TEST3"
        assert len(summary["task"]) <= 200
        assert summary["criteria"][0]["criterion_id"]


class TestAcceptanceCriterion:
    def test_defaults(self) -> None:
        c = AcceptanceCriterion(description="C")
        assert c.status == CriterionStatus.PENDING
        assert c.criterion_type == CriterionType.FUNCTIONAL
        assert c.confidence == 0.0
        assert c.criterion_id.startswith("CR-")

    def test_statuses_valid(self) -> None:
        for s in ("pending", "satisfied", "unsatisfied", "blocked", "unknown"):
            c = AcceptanceCriterion(description="C", status=s)
            assert c.status.value == s


class TestGoalEvaluator:
    def setup_method(self):
        self.evaluator = GoalEvaluator()

    def test_extract_criteria_from_texts(self) -> None:
        criteria = self.evaluator.extract_criteria(
            task="Fix auth",
            criteria_texts=["Expired tokens rejected", "Valid tokens work"],
        )
        assert len(criteria) == 2
        assert all(c.verification == "suite:pass" for c in criteria)

    def test_extract_criteria_dedupes(self) -> None:
        criteria = self.evaluator.extract_criteria(
            task="Fix auth",
            criteria_texts=["Same requirement", "same requirement"],
        )
        assert len(criteria) == 1

    def test_extract_criteria_from_requirements(self) -> None:
        reqs = StructuredRequirements(
            objective="Fix auth",
            requirements=[
                Requirement(
                    description="Expired tokens rejected",
                    requirement_type=RequirementType.SECURITY,
                    acceptance_note="test:test_validate_expired_token",
                ),
                Requirement(
                    description="Performance target",
                    requirement_type=RequirementType.PERFORMANCE,
                ),
            ],
        )
        criteria = self.evaluator.extract_criteria(task="Fix auth", requirements=reqs)
        assert len(criteria) == 2
        types = {c.criterion_type for c in criteria}
        assert CriterionType.SECURITY in types
        assert CriterionType.PERFORMANCE in types

    def test_extract_criteria_from_plan_steps(self) -> None:
        plan = ImplementationPlan(
            summary="Plan",
            objective="Fix auth",
            steps=[
                ImplementationStep(id="S1", title="Add validation", description="Add token validation"),
            ],
        )
        criteria = self.evaluator.extract_criteria(task="Fix auth", plan=plan)
        assert len(criteria) == 1
        assert "validation" in criteria[0].description

    def test_evaluate_suite_pass(self) -> None:
        goal = ExecutionGoal(
            goal_id="GOAL-X",
            task="T",
            acceptance_criteria=[AcceptanceCriterion(description="C", verification="suite:pass")],
        )
        progress = self.evaluator.evaluate(
            goal,
            IterationEvidence(iteration=1, test_status="passed", tests_passed=5, tests_failed=0),
        )
        assert progress.criteria_satisfied == 1
        assert goal.acceptance_criteria[0].status == CriterionStatus.SATISFIED

    def test_evaluate_suite_fail(self) -> None:
        goal = ExecutionGoal(
            goal_id="GOAL-X",
            task="T",
            acceptance_criteria=[AcceptanceCriterion(description="C", verification="suite:pass")],
        )
        progress = self.evaluator.evaluate(
            goal,
            IterationEvidence(iteration=1, test_status="failed", tests_failed=3),
        )
        assert progress.criteria_unsatisfied == 1

    def test_evaluate_specific_test(self) -> None:
        goal = ExecutionGoal(
            goal_id="GOAL-X",
            task="T",
            acceptance_criteria=[
                AcceptanceCriterion(description="C", verification="test:test_expired")
            ],
        )
        progress = self.evaluator.evaluate(
            goal,
            IterationEvidence(iteration=1, test_status="failed",
                              failing_test_names=["test_expired"]),
        )
        assert progress.criteria_unsatisfied == 1
        assert goal.acceptance_criteria[0].evidence[0].reference == "test_expired"

    def test_evaluate_gate_criterion(self) -> None:
        goal = ExecutionGoal(
            goal_id="GOAL-X",
            task="T",
            acceptance_criteria=[AcceptanceCriterion(description="C", verification="gate:approved")],
        )
        progress = self.evaluator.evaluate(
            goal,
            IterationEvidence(iteration=1, test_status="passed",
                              quality_gate_decision="approved"),
        )
        assert progress.criteria_satisfied == 1

    def test_unknown_without_evidence(self) -> None:
        """An LLM claim alone must not satisfy a criterion (§5)."""
        goal = ExecutionGoal(
            goal_id="GOAL-X",
            task="T",
            acceptance_criteria=[AcceptanceCriterion(description="C", verification="suite:pass")],
        )
        progress = self.evaluator.evaluate(
            goal,
            IterationEvidence(iteration=1, test_status=None, quality_gate_decision=None),
        )
        assert progress.criteria_unknown == 1
        assert goal.acceptance_criteria[0].status == CriterionStatus.UNKNOWN


class TestExecutionBudget:
    def test_defaults(self) -> None:
        b = ExecutionBudget()
        assert b.max_iterations == 5
        assert b.max_replans == 2
        assert b.max_repairs == 3

    def test_exhausted_none_when_fresh(self) -> None:
        b = ExecutionBudget()
        assert b.exhausted() is None

    def test_exhausted_iterations(self) -> None:
        b = ExecutionBudget(max_iterations=2)
        b.iterations_used = 2
        assert b.exhausted() == "max_iterations"

    def test_exhausted_replans(self) -> None:
        b = ExecutionBudget(max_replans=1)
        b.replans_used = 1
        assert b.exhausted() == "max_replans"

    def test_usage_and_limits_roundtrip(self) -> None:
        b = ExecutionBudget()
        assert b.usage()["iterations"] == b.iterations_used
        assert b.limits()["max_iterations"] == b.max_iterations


class TestBudgetManager:
    def setup_method(self):
        self.manager = BudgetManager()

    def test_record_increments_counters(self) -> None:
        state = AutonomousRunState(task="T")
        evidence = IterationEvidence(iteration=1, test_status="passed", duration_seconds=2.0)
        self.manager.record(state, evidence)
        assert state.budget.iterations_used == 1
        assert state.budget.test_runs_used == 1
        assert state.budget.execution_time_used_seconds == 2.0

    def test_check_warning_at_threshold(self) -> None:
        state = AutonomousRunState(task="T")
        state.budget.max_iterations = 2
        state.budget.iterations_used = 2  # 100% of limit
        warning = self.manager.check_warning(state)
        assert warning is not None
        assert "iterations" in warning

    def test_check_warning_none_when_low(self) -> None:
        state = AutonomousRunState(task="T")
        assert self.manager.check_warning(state) is None


class TestProgressEvaluator:
    def setup_method(self):
        self.evaluator = ProgressEvaluator()

    def test_improving(self) -> None:
        prev = GoalProgress(criteria_satisfied=0, criteria_total=2)
        cur = GoalProgress(criteria_satisfied=1, criteria_total=2)
        trend = self.evaluator.evaluate(prev, cur, IterationEvidence(iteration=2))
        assert trend == ProgressTrend.PROGRESSING

    def test_stalled(self) -> None:
        prev = GoalProgress(criteria_satisfied=1, criteria_total=2)
        cur = GoalProgress(criteria_satisfied=1, criteria_total=2)
        trend = self.evaluator.evaluate(prev, cur, IterationEvidence(iteration=2))
        assert trend == ProgressTrend.STALLED

    def test_environment_blocks(self) -> None:
        from app.models.autonomy import FailureClass
        prev = GoalProgress(criteria_satisfied=0, criteria_total=1)
        cur = GoalProgress(criteria_satisfied=1, criteria_total=1)
        trend = self.evaluator.evaluate(
            prev, cur,
            IterationEvidence(iteration=2, failure_class=FailureClass.ENVIRONMENT),
        )
        assert trend == ProgressTrend.BLOCKED


class TestStuckDetector:
    def setup_method(self):
        self.detector = StuckDetector()

    def _state_with_history(self, evidences):
        state = AutonomousRunState(task="T")
        state.evidence_history = evidences
        return state

    def test_no_history_progressing(self) -> None:
        state = AutonomousRunState(task="T")
        assert self.detector.evaluate(state) == ProgressTrend.PROGRESSING

    def test_same_failing_tests_loop(self) -> None:
        evidences = [
            IterationEvidence(iteration=i, test_status="failed",
                              failing_test_names=["t1"], quality_gate_decision="rejected")
            for i in (1, 2, 3)
        ]
        state = self._state_with_history(evidences)
        assert self.detector.evaluate(state) == ProgressTrend.LOOPING

    def test_repeated_gate_rejection_stalled(self) -> None:
        evidences = [
            IterationEvidence(iteration=i, test_status="passed",
                              quality_gate_decision="rejected")
            for i in (1, 2, 3)
        ]
        state = self._state_with_history(evidences)
        assert self.detector.evaluate(state) == ProgressTrend.STALLED

    def test_environment_blocks(self) -> None:
        from app.models.autonomy import FailureClass
        evidences = [
            IterationEvidence(iteration=1, failure_class=FailureClass.ENVIRONMENT)
        ]
        state = self._state_with_history(evidences)
        assert self.detector.evaluate(state) == ProgressTrend.BLOCKED

    def test_identical_plan_versions_loop(self) -> None:
        state = AutonomousRunState(task="T")
        # Evidence history is required for the plan-version check to run.
        state.evidence_history = [IterationEvidence(iteration=1, test_status="failed")]
        state.plan_versions = [
            PlanVersion(version=1, plan_summary="SAME"),
            PlanVersion(version=2, plan_summary="SAME"),
        ]
        assert self.detector.evaluate(state) == ProgressTrend.LOOPING


class TestTaskScope:
    def test_summary(self) -> None:
        scope = TaskScope(
            allowed_modules=["auth"],
            forbidden_areas=["migrations"],
            max_scope_expansions=2,
        )
        summary = scope.summary()
        assert summary["allowed_modules"] == ["auth"]
        assert summary["max_scope_expansions"] == 2

    def test_no_constraints_means_nothing_to_enforce(self) -> None:
        scope = TaskScope()
        assert not scope.allowed_modules and not scope.expected_change_area


class TestAutonomousRunState:
    def test_auto_goal_builder_keeps_ids_in_sync(self) -> None:
        """The before-validator injects a goal; outer goal_id must match."""
        state = AutonomousRunState(task="Fix tokens")
        assert state.goal_id == state.goal.goal_id
        assert state.goal.task == "Fix tokens"

    def test_events_bounded(self) -> None:
        state = AutonomousRunState(task="T")
        for i in range(300):
            state.add_event("TEST", f"event {i}")
        assert len(state.events) <= 200

    def test_status_summary_has_no_cot(self) -> None:
        state = AutonomousRunState(task="T")
        summary = state.status_summary()
        assert "goal_id" in summary
        assert "chain_of_thought" not in summary
        assert "scratchpad" not in summary


class TestClassifyFailure:
    def test_environment_not_ready(self) -> None:
        from app.models.autonomy import FailureClass
        assert classify_failure(None, "environment_not_ready") == FailureClass.ENVIRONMENT

    def test_patch_failure_is_code(self) -> None:
        from app.models.autonomy import FailureClass
        assert classify_failure("patch_application_failed", "failed") == FailureClass.CODE

    def test_test_execution_failure_is_test(self) -> None:
        from app.models.autonomy import FailureClass
        assert classify_failure("test_execution_failed", "failed") == FailureClass.TEST

    def test_unknown(self) -> None:
        from app.models.autonomy import FailureClass
        assert classify_failure(None, None) == FailureClass.UNKNOWN


class TestAutonomyPolicy:
    def test_defaults_safe(self) -> None:
        policy = AutonomyPolicy()
        assert policy.allow_repair is True
        assert policy.allow_replan is True
        assert policy.allow_scope_expansion is False
        assert policy.max_scope_expansions == 2
