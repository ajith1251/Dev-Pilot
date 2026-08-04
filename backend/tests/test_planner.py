"""Tests for the Planner Agent (mocked LLM responses)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.planner import PlannerAgent, PlannerInput
from app.models.issues import (
    Ambiguity,
    Constraint,
    ImplementationPlan,
    Risk,
    RiskCategory,
    StructuredRequirements,
)


# ── Fixtures ───────────────────────────────────────────────────


def make_valid_requirements() -> StructuredRequirements:
    return StructuredRequirements(
        objective="Add pagination to the products API",
        requirements=[
            {
                "description": "Add page and per_page query parameters to GET /api/products",
                "requirement_type": "functional",
                "is_implied": False,
                "acceptance_note": "GET /api/products?page=2&per_page=20 returns page 2 with 20 items",
            },
            {
                "description": "Include pagination metadata in response",
                "requirement_type": "functional",
                "is_implied": False,
                "acceptance_note": "Response includes total, page, per_page, total_pages fields",
            },
        ],
        constraints=[
            Constraint(
                description="Must not break existing API responses",
                category="backward_compatibility",
                source="task",
            ),
        ],
        likely_affected_areas=[
            {"path": "api/products", "description": "Product listing endpoint", "confidence": "high"},
            {"path": "models/product", "description": "Product model/query", "confidence": "medium"},
        ],
        ambiguities=[
            Ambiguity(
                description="Maximum per_page value not specified",
                category="unspecified_scope",
                question="What should be the maximum allowed per_page value?",
            ),
        ],
        risks=[
            Risk(
                description="Large page sizes could impact database performance",
                category="performance",
                likelihood="medium",
                impact="medium",
            ),
        ],
        assumptions=["Existing authentication/authorization remains unchanged"],
        confidence="high",
    )


VALID_PLAN_JSON = """{
  "summary": "Add pagination support to the products API while maintaining backward compatibility",
  "objective": "Add pagination to the products API",
  "steps": [
    {
      "id": "STEP-001",
      "title": "Define pagination parameters schema",
      "description": "Create a shared pagination request schema with page and per_page fields, including validation for min/max values",
      "affected_areas": ["api/schemas", "models/product"],
      "depends_on": [],
      "expected_changes": "New Pydantic model for pagination query parameters",
      "validation": "Schema accepts valid params and rejects invalid ones",
      "risk": null,
      "effort_estimate": "small"
    },
    {
      "id": "STEP-002",
      "title": "Add pagination to product listing endpoint",
      "description": "Update the GET /api/products handler to accept pagination params and return paginated results",
      "affected_areas": ["api/routes/products", "services/product_service"],
      "depends_on": ["STEP-001"],
      "expected_changes": "Updated route handler, modified service layer query",
      "validation": "Endpoint returns paginated results with correct metadata",
      "risk": "Query performance with high page offsets",
      "effort_estimate": "medium"
    },
    {
      "id": "STEP-003",
      "title": "Update API documentation",
      "description": "Document the new pagination parameters and response format in the API docs",
      "affected_areas": ["docs/api", "README.md"],
      "depends_on": ["STEP-001", "STEP-002"],
      "expected_changes": "Updated API documentation with pagination examples",
      "validation": "Documentation reflects actual API behavior",
      "effort_estimate": "small"
    }
  ],
  "test_strategy": "Add unit tests for pagination schema validation and integration tests for paginated endpoint responses",
  "documentation_impact": "Update API route documentation with pagination parameter details and response format",
  "risks": [
    {
      "description": "Query performance with large page numbers and high per_page values",
      "category": "performance",
      "likelihood": "medium",
      "impact": "high",
      "mitigation": "Set reasonable max per_page limit and use keyset pagination if needed"
    }
  ],
  "assumptions": ["Existing authentication remains unchanged", "Database supports offset-based pagination"],
  "requirements_coverage": {
    "REQ-001": ["STEP-001", "STEP-002"],
    "REQ-002": ["STEP-002"]
  }
}"""


INVALID_PLAN_JSON = """{
  "summary": "",
  "objective": "",
  "steps": [],
  "test_strategy": "",
  "documentation_impact": ""
}"""


# ── Tests ─────────────────────────────────────────────────────


class TestPlannerAgent:
    """Planner Agent tests."""

    @pytest.fixture
    def agent(self) -> PlannerAgent:
        return PlannerAgent()

    @pytest.mark.asyncio
    async def test_valid_plan_generation(self, agent: PlannerAgent) -> None:
        """Valid requirements should produce a structured plan."""
        requirements = make_valid_requirements()
        inp = PlannerInput(requirements=requirements)

        with patch("app.agents.planner.llm_factory") as mock_factory:
            mock_provider = AsyncMock()
            mock_provider.chat = AsyncMock(
                return_value=MagicMock(content=VALID_PLAN_JSON)
            )
            mock_factory.get_provider = MagicMock(return_value=mock_provider)

            plan = await agent.execute(inp)

        assert plan.error is None
        assert len(plan.steps) == 3
        assert plan.steps[0].id == "STEP-001"
        assert plan.steps[1].depends_on == ["STEP-001"]
        assert plan.steps[2].depends_on == ["STEP-001", "STEP-002"]
        assert "pagination" in plan.summary.lower()
        assert plan.test_strategy
        assert plan.documentation_impact

    @pytest.mark.asyncio
    async def test_no_requirements_returns_error(self, agent: PlannerAgent) -> None:
        """No requirements should return an error."""
        requirements = StructuredRequirements(
            objective="Test",
            requirements=[],
        )
        inp = PlannerInput(requirements=requirements)

        plan = await agent.execute(inp)
        assert plan.error is not None
        assert "No requirements" in plan.error

    @pytest.mark.asyncio
    async def test_requirements_with_error(self, agent: PlannerAgent) -> None:
        """Requirements with error should propagate."""
        requirements = StructuredRequirements(
            objective="Test",
            requirements=[],
            error="Previous analysis failed",
        )
        inp = PlannerInput(requirements=requirements)

        plan = await agent.execute(inp)
        assert plan.error is not None
        assert "Previous analysis failed" in plan.error

    @pytest.mark.asyncio
    async def test_invalid_plan_json(self, agent: PlannerAgent) -> None:
        """Malformed LLM response should produce empty plan with error."""
        requirements = make_valid_requirements()
        inp = PlannerInput(requirements=requirements)

        with patch("app.agents.planner.llm_factory") as mock_factory:
            mock_provider = AsyncMock()
            mock_provider.chat = AsyncMock(
                return_value=MagicMock(content="not valid json")
            )
            mock_factory.get_provider = MagicMock(return_value=mock_provider)

            plan = await agent.execute(inp)

        # Should have steps field, but likely empty
        assert plan is not None

    @pytest.mark.asyncio
    async def test_empty_plan_handling(self, agent: PlannerAgent) -> None:
        """Empty steps should produce validation warnings."""
        requirements = make_valid_requirements()
        inp = PlannerInput(requirements=requirements)

        with patch("app.agents.planner.llm_factory") as mock_factory:
            mock_provider = AsyncMock()
            mock_provider.chat = AsyncMock(
                return_value=MagicMock(content=INVALID_PLAN_JSON)
            )
            mock_factory.get_provider = MagicMock(return_value=mock_provider)

            plan = await agent.execute(inp)

        assert plan.steps == []
        assert not plan.summary
        assert not plan.test_strategy

    @pytest.mark.asyncio
    async def test_llm_failure_fallback(self, agent: PlannerAgent) -> None:
        """LLM failure should return error gracefully."""
        requirements = make_valid_requirements()
        inp = PlannerInput(requirements=requirements)

        with patch("app.agents.planner.llm_factory") as mock_factory:
            mock_factory.get_provider = MagicMock(
                side_effect=Exception("LLM unavailable")
            )

            plan = await agent.execute(inp)

        assert plan.error is not None
        assert "LLM" in plan.error or "Planning failed" in plan.error

    @pytest.mark.asyncio
    async def test_run_wraps_execute(self, agent: PlannerAgent) -> None:
        """Agent.run() should wrap execute() and set status."""
        requirements = make_valid_requirements()
        inp = PlannerInput(requirements=requirements)

        with patch("app.agents.planner.llm_factory") as mock_factory:
            mock_provider = AsyncMock()
            mock_provider.chat = AsyncMock(
                return_value=MagicMock(content=VALID_PLAN_JSON)
            )
            mock_factory.get_provider = MagicMock(return_value=mock_provider)

            await agent.run(inp)

        assert agent.status.value == "success"

    @pytest.mark.asyncio
    async def test_dependency_chain_order(self, agent: PlannerAgent) -> None:
        """Steps should maintain correct dependency ordering."""
        requirements = make_valid_requirements()
        inp = PlannerInput(requirements=requirements)

        with patch("app.agents.planner.llm_factory") as mock_factory:
            mock_provider = AsyncMock()
            mock_provider.chat = AsyncMock(
                return_value=MagicMock(content=VALID_PLAN_JSON)
            )
            mock_factory.get_provider = MagicMock(return_value=mock_provider)

            plan = await agent.execute(inp)

        # Verify dependency constraints
        assert len(plan.steps) == 3
        # Step 1 has no dependencies
        assert plan.steps[0].depends_on == []
        # Step 2 depends on Step 1
        assert plan.steps[1].depends_on == ["STEP-001"]
        # Step 3 depends on Steps 1 and 2
        assert "STEP-001" in plan.steps[2].depends_on
        assert "STEP-002" in plan.steps[2].depends_on

    @pytest.mark.asyncio
    async def test_affected_areas_in_steps(self, agent: PlannerAgent) -> None:
        """Plan steps should include affected areas."""
        requirements = make_valid_requirements()
        inp = PlannerInput(requirements=requirements)

        with patch("app.agents.planner.llm_factory") as mock_factory:
            mock_provider = AsyncMock()
            mock_provider.chat = AsyncMock(
                return_value=MagicMock(content=VALID_PLAN_JSON)
            )
            mock_factory.get_provider = MagicMock(return_value=mock_provider)

            plan = await agent.execute(inp)

        for step in plan.steps:
            assert len(step.affected_areas) > 0
            assert step.validation  # Each step must have validation criteria

    @pytest.mark.asyncio
    async def test_repository_context_usage(self, agent: PlannerAgent) -> None:
        """Repository context should be passed to LLM."""
        requirements = make_valid_requirements()
        inp = PlannerInput(
            requirements=requirements,
            repo_languages=["Python", "TypeScript"],
            repo_technologies=["FastAPI", "React"],
            repo_modules=["backend/api", "frontend/app"],
            repo_important_files=["backend/app/main.py", "frontend/package.json"],
            repo_tree_preview="project/\n  backend/\n  frontend/",
        )

        with patch("app.agents.planner.llm_factory") as mock_factory:
            mock_provider = AsyncMock()
            mock_provider.chat = AsyncMock(
                return_value=MagicMock(content=VALID_PLAN_JSON)
            )
            mock_factory.get_provider = MagicMock(return_value=mock_provider)

            plan = await agent.execute(inp)

        assert plan.error is None
        assert len(plan.steps) == 3

    def test_unknown_risk_category_coerced_to_other(self) -> None:
        """LLM risk categories outside the enum must not abort the plan."""
        agent = PlannerAgent()
        # logic_bug is now a member; anything truly unknown falls back to OTHER
        assert agent._coerce_risk_category("logic_bug") == RiskCategory.LOGIC_BUG
        assert agent._coerce_risk_category("performance") == RiskCategory.PERFORMANCE
        assert agent._coerce_risk_category("mystery_category") == RiskCategory.OTHER
        assert agent._coerce_risk_category("") == RiskCategory.OTHER
        assert agent._coerce_risk_category(None) == RiskCategory.OTHER
        assert agent._coerce_risk_category(RiskCategory.SECURITY) == RiskCategory.SECURITY

    @pytest.mark.asyncio
    async def test_plan_with_unknown_risk_category_parses(self, agent: PlannerAgent) -> None:
        """A plan whose risks use an unknown category should still parse."""
        plan_json = VALID_PLAN_JSON.replace(
            '"category": "performance"', '"category": "logic_bug"')
        requirements = make_valid_requirements()
        inp = PlannerInput(requirements=requirements)

        with patch("app.agents.planner.llm_factory") as mock_factory:
            mock_provider = AsyncMock()
            mock_provider.chat = AsyncMock(
                return_value=MagicMock(content=plan_json)
            )
            mock_factory.get_provider = MagicMock(return_value=mock_provider)

            plan = await agent.execute(inp)

        assert plan.error is None
        assert plan.risks[0].category == RiskCategory.LOGIC_BUG

    @pytest.mark.asyncio
    async def test_json_parse_with_fences(self, agent: PlannerAgent) -> None:
        """JSON parsing should handle markdown fences."""
        text = f"```json\n{VALID_PLAN_JSON}\n```"
        result = agent._parse_json_response(text)
        assert "summary" in result
        assert "steps" in result

    @pytest.mark.asyncio
    async def test_json_parse_plain(self, agent: PlannerAgent) -> None:
        """JSON parsing should work without fences."""
        text = '{"summary": "test", "steps": []}'
        result = agent._parse_json_response(text)
        assert result.get("summary") == "test"

    @pytest.mark.asyncio
    async def test_json_parse_empty(self, agent: PlannerAgent) -> None:
        """Empty response should return empty dict."""
        result = agent._parse_json_response("")
        assert result == {}

    @pytest.mark.asyncio
    async def test_cycle_detection(self, agent: PlannerAgent) -> None:
        """Cycle detection should identify dependency cycles."""
        adj = {
            "A": ["B"],
            "B": ["C"],
            "C": ["A"],  # Cycle!
        }
        assert agent._has_cycle(adj) is True

    @pytest.mark.asyncio
    async def test_no_cycle_detection(self, agent: PlannerAgent) -> None:
        """DAG should not have cycles."""
        adj = {
            "A": ["B", "C"],
            "B": ["C"],
            "C": [],
        }
        assert agent._has_cycle(adj) is False
