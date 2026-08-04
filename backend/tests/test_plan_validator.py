"""Tests for the PlanValidator (deterministic validation)."""

from __future__ import annotations

import pytest

from app.models.issues import (
    ImplementationPlan,
    ImplementationStep,
    PlanValidationResult,
)
from app.services.plan_validator import PlanValidator


# ── Fixtures ───────────────────────────────────────────────────


def make_valid_plan() -> ImplementationPlan:
    return ImplementationPlan(
        summary="Add pagination to products API",
        objective="Add pagination to the products API",
        steps=[
            ImplementationStep(
                id="STEP-001",
                title="Define pagination schema",
                description="Create pagination request schema",
                affected_areas=["api/schemas"],
                depends_on=[],
            ),
            ImplementationStep(
                id="STEP-002",
                title="Update endpoint",
                description="Update GET /api/products with pagination",
                affected_areas=["api/routes/products"],
                depends_on=["STEP-001"],
            ),
            ImplementationStep(
                id="STEP-003",
                title="Update docs",
                description="Document pagination parameters",
                affected_areas=["docs/api"],
                depends_on=["STEP-001", "STEP-002"],
            ),
        ],
        test_strategy="Add unit and integration tests",
        documentation_impact="Update API docs",
    )


# ── Tests ─────────────────────────────────────────────────────


class TestPlanValidator:
    """PlanValidator tests."""

    @pytest.fixture
    def validator(self) -> PlanValidator:
        return PlanValidator()

    def test_valid_plan_passes(self, validator: PlanValidator) -> None:
        """A well-formed plan should pass validation."""
        plan = make_valid_plan()
        result = validator.validate(plan)

        assert result.is_valid is True
        assert len(result.errors) == 0
        assert result.checked_step_count == 3

    def test_empty_plan_fails(self, validator: PlanValidator) -> None:
        """Empty plan should fail with 'no steps' error."""
        plan = ImplementationPlan(
            summary="",
            objective="Test",
            steps=[],
        )
        result = validator.validate(plan)

        assert result.is_valid is False
        assert any("no steps" in e.lower() for e in result.errors)

    def test_duplicate_ids_fail(self, validator: PlanValidator) -> None:
        """Duplicate step IDs should fail."""
        plan = ImplementationPlan(
            summary="Test",
            objective="Test",
            steps=[
                ImplementationStep(id="STEP-001", title="Step 1", description="Desc"),
                ImplementationStep(id="STEP-001", title="Step 2", description="Desc"),
            ],
        )
        result = validator.validate(plan)

        assert result.is_valid is False
        assert result.has_duplicate_ids is True
        assert any("duplicate" in e.lower() for e in result.errors)

    def test_self_dependency_fails(self, validator: PlanValidator) -> None:
        """Self-dependency should fail."""
        plan = ImplementationPlan(
            summary="Test",
            objective="Test",
            steps=[
                ImplementationStep(
                    id="STEP-001", title="Step 1", description="Desc",
                    depends_on=["STEP-001"],
                ),
            ],
        )
        result = validator.validate(plan)

        assert result.is_valid is False
        assert result.has_self_dependencies is True

    def test_missing_dependency_fails(self, validator: PlanValidator) -> None:
        """Reference to non-existent step should fail."""
        plan = ImplementationPlan(
            summary="Test",
            objective="Test",
            steps=[
                ImplementationStep(
                    id="STEP-001", title="Step 1", description="Desc",
                    depends_on=["STEP-999"],
                ),
            ],
        )
        result = validator.validate(plan)

        assert result.is_valid is False
        assert result.has_missing_dependencies is True
        assert any("non-existent" in e.lower() or "unknown" in e.lower()
                    for e in result.errors)

    def test_cycle_detection(self, validator: PlanValidator) -> None:
        """Circular dependency should fail."""
        plan = ImplementationPlan(
            summary="Test",
            objective="Test",
            steps=[
                ImplementationStep(
                    id="A", title="A", description="Desc",
                    depends_on=["B"],
                ),
                ImplementationStep(
                    id="B", title="B", description="Desc",
                    depends_on=["C"],
                ),
                ImplementationStep(
                    id="C", title="C", description="Desc",
                    depends_on=["A"],  # Cycle!
                ),
            ],
        )
        result = validator.validate(plan)

        assert result.is_valid is False
        assert result.has_cycles is True

    def test_warnings_for_empty_title(self, validator: PlanValidator) -> None:
        """Steps with empty titles should generate warnings."""
        plan = make_valid_plan()
        plan.steps[0].title = ""
        result = validator.validate(plan)

        assert result.is_valid is True  # Warning, not error
        assert any("empty title" in w.lower() for w in result.warnings)

    def test_warnings_for_no_test_strategy(self, validator: PlanValidator) -> None:
        """Missing test strategy should generate warning."""
        plan = make_valid_plan()
        plan.test_strategy = ""
        result = validator.validate(plan)

        assert result.is_valid is True
        assert any("test strategy" in w.lower() for w in result.warnings)

    def test_warnings_for_no_validation(self, validator: PlanValidator) -> None:
        """Steps without validation criteria should generate warning."""
        plan = make_valid_plan()
        plan.steps[0].validation = ""
        result = validator.validate(plan)

        assert result.is_valid is True
        assert any("no validation" in w.lower() for w in result.warnings)

    def test_too_many_steps_fails(self, validator: PlanValidator) -> None:
        """More than 30 steps should fail."""
        steps = [
            ImplementationStep(
                id=f"STEP-{i:03d}", title=f"Step {i}", description="Desc",
            )
            for i in range(31)
        ]
        plan = ImplementationPlan(
            summary="Test", objective="Test", steps=steps,
        )
        result = validator.validate(plan)

        assert result.is_valid is False
        assert any("max" in e.lower() for e in result.errors)

    def test_empty_objective_warns(self, validator: PlanValidator) -> None:
        """Empty objective should produce an error."""
        plan = make_valid_plan()
        plan.objective = ""
        result = validator.validate(plan)

        assert result.is_valid is False
        assert any("objective" in e.lower() for e in result.errors)

    def test_empty_summary_warns(self, validator: PlanValidator) -> None:
        """Empty summary should produce a warning (not error)."""
        plan = make_valid_plan()
        plan.summary = ""
        result = validator.validate(plan)

        assert result.is_valid is True
        assert any("summary" in w.lower() for w in result.warnings)

    def test_dependency_count_tracked(self, validator: PlanValidator) -> None:
        """Dependency count should be tracked."""
        plan = make_valid_plan()
        result = validator.validate(plan)

        # STEP-002 depends on 1 step, STEP-003 depends on 2 = 3 total
        assert result.checked_dependency_count == 3

    def test_requirements_coverage_valid(self, validator: PlanValidator) -> None:
        """Requirements coverage with valid step IDs should not error."""
        plan = make_valid_plan()
        plan.requirements_coverage = {
            "REQ-001": ["STEP-001", "STEP-002"],
        }
        result = validator.validate_requirements_coverage(plan)

        assert result.is_valid is True

    def test_requirements_coverage_invalid_step(self, validator: PlanValidator) -> None:
        """Requirements coverage with invalid step ID should error."""
        plan = make_valid_plan()
        plan.requirements_coverage = {
            "REQ-001": ["STEP-999"],
        }
        result = validator.validate_requirements_coverage(plan)

        assert result.is_valid is False
        assert any("STEP-999" in e for e in result.errors)

    def test_cycle_detection_empty_graph(self, validator: PlanValidator) -> None:
        """Empty graph should not have cycles."""
        assert validator._has_cycle({}) is False

    def test_cycle_detection_no_edges(self, validator: PlanValidator) -> None:
        """Graph with no edges should not have cycles."""
        adj = {"A": [], "B": [], "C": []}
        assert validator._has_cycle(adj) is False

    def test_cycle_detection_self_loop(self, validator: PlanValidator) -> None:
        """Self-loop is a cycle."""
        adj = {"A": ["A"]}
        assert validator._has_cycle(adj) is True

    def test_cycle_detection_complex_dag(self, validator: PlanValidator) -> None:
        """Complex DAG should not have cycles."""
        adj = {
            "A": ["B", "C"],
            "B": ["D"],
            "C": ["D"],
            "D": [],
        }
        assert validator._has_cycle(adj) is False

    def test_cycle_detection_disconnected_cycle(self, validator: PlanValidator) -> None:
        """Disconnected subgraph with cycle."""
        adj = {
            "A": ["B"],
            "B": ["A"],  # Cycle in disconnected subgraph
            "C": [],
            "D": ["E"],
            "E": [],
        }
        assert validator._has_cycle(adj) is True
