"""
Planner Agent — creates structured ImplementationPlans from requirements.

Input: StructuredRequirements + RepositoryProfile context
Output: ImplementationPlan

The Planner uses the LLM abstraction (provider-independent) and produces
validated, structured plans with ordered steps, dependencies, and
requirement traceability.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from app.agents.base import BaseAgent
from app.core.logging import logger
from app.llm.base import LLMConfig, LLMMessage
from app.llm.factory import factory as llm_factory
from app.models.issues import (
    ImplementationPlan,
    ImplementationStep,
    PlanValidationResult,
    Risk,
    RiskCategory,
    StructuredRequirements,
)
from app.prompts.planning import (
    PLANNER_SYSTEM_PROMPT,
    PLANNER_USER_PROMPT,
    build_ambiguities_text,
    build_constraints_text,
    build_requirements_text,
    build_risks_text,
)


# ── Max input sizes ─────────────────────────────────────────────
MAX_REQUIREMENTS = 15
MAX_CONSTRAINTS = 10
MAX_RISKS = 10
MAX_AMBIGUITIES = 10
MAX_DESC_CHARS = 5000


class PlannerInput:
    """Input to the Planner Agent (not a Pydantic model to avoid over-engineering)."""

    def __init__(
        self,
        requirements: StructuredRequirements,
        repo_languages: Optional[list[str]] = None,
        repo_technologies: Optional[list[str]] = None,
        repo_modules: Optional[list[str]] = None,
        repo_commands: Optional[list[str]] = None,
        repo_important_files: Optional[list[str]] = None,
        repo_tree_preview: str = "",
        graph_context: str = "",
        agent_context: Optional[Any] = None,
    ) -> None:
        self.requirements = requirements
        self.repo_languages = repo_languages or []
        self.repo_technologies = repo_technologies or []
        self.repo_modules = repo_modules or []
        self.repo_commands = repo_commands or []
        self.repo_important_files = repo_important_files or []
        self.repo_tree_preview = repo_tree_preview
        self.graph_context = graph_context
        # Phase 13: ContextEngine-produced context (replaces graph_context)
        self.agent_context = agent_context


class PlannerInputModel:
    """Placeholder type for BaseAgent generic parameter."""


class PlannerAgent(BaseAgent[PlannerInput, ImplementationPlan]):
    """Agent that creates structured implementation plans.

    Input: StructuredRequirements + compact repository context
    Output: ImplementationPlan with ordered steps, dependencies,
            test strategy, documentation impact, and requirement traceability.

    The plan is structurally validated before being returned.
    """

    def __init__(
        self,
        name: str = "Planner",
        description: str = (
            "Creates structured implementation plans from requirements "
            "and repository context"
        ),
        max_retries: int = 2,
    ) -> None:
        super().__init__(name=name, description=description, max_retries=max_retries)

    async def execute(self, inp: PlannerInput) -> ImplementationPlan:
        """Execute planning.

        Args:
            inp: PlannerInput with requirements and repo context.

        Returns:
            ImplementationPlan with validated plan or error.
        """
        requirements = inp.requirements

        if requirements.error:
            return ImplementationPlan(
                summary="",
                objective=requirements.objective,
                steps=[],
                error=f"Cannot plan: {requirements.error}",
            )

        if not requirements.requirements:
            return ImplementationPlan(
                summary="",
                objective=requirements.objective,
                steps=[],
                error="No requirements to plan against",
            )

        logger.info(
            "Planning started: objective='%s' (%d requirements, %d constraints)",
            requirements.objective[:60],
            len(requirements.requirements),
            len(requirements.constraints),
        )

        # ── Format input for LLM ──────────────────────────────────
        reqs_text = build_requirements_text([
            {
                "description": r.description,
                "requirement_type": r.requirement_type.value,
                "acceptance_note": r.acceptance_note,
                "is_implied": r.is_implied,
            }
            for r in requirements.requirements[:MAX_REQUIREMENTS]
        ])

        constraints_text = build_constraints_text([
            {
                "description": c.description,
                "category": c.category.value if hasattr(c.category, "value") else str(c.category),
                "source": c.source,
            }
            for c in requirements.constraints[:MAX_CONSTRAINTS]
        ])

        risks_text = build_risks_text([
            {
                "description": r.description,
                "category": r.category.value if hasattr(r.category, "value") else str(r.category),
                "likelihood": r.likelihood,
                "impact": r.impact,
            }
            for r in requirements.risks[:MAX_RISKS]
        ])

        ambiguities_text = build_ambiguities_text([
            {
                "description": a.description,
                "category": a.category.value if hasattr(a.category, "value") else str(a.category),
                "question": a.question,
            }
            for a in requirements.ambiguities[:MAX_AMBIGUITIES]
        ])

        # Build compact repo context
        repo_lines: list[str] = []
        if inp.repo_languages:
            repo_lines.append(f"Languages: {', '.join(inp.repo_languages[:8])}")
        if inp.repo_technologies:
            repo_lines.append(f"Technologies: {', '.join(inp.repo_technologies[:10])}")
        if inp.repo_modules:
            repo_lines.append(f"Modules: {', '.join(inp.repo_modules[:8])}")
        if inp.repo_important_files:
            repo_lines.append(f"Key files: {', '.join(inp.repo_important_files[:10])}")
        if inp.repo_tree_preview:
            repo_lines.append(f"Structure:\n{inp.repo_tree_preview[:600]}")

        repo_context_text = "\n".join(repo_lines) or "(no repository context provided)"

        # Phase 13: Include ContextEngine context if available (replaces graph_context)
        if inp.agent_context is not None:
            ctx_section = inp.agent_context.build_prompt_section()
            if ctx_section:
                repo_context_text += f"\n\n{ctx_section}"
        else:
            # Fallback: use graph context (Phase 12)
            graph_ctx = inp.graph_context or self._get_graph_context(inp)
            if graph_ctx:
                repo_context_text += f"\n\n=== Semantic Structure ===\n{graph_ctx[:2000]}"

        # ── Call LLM ──────────────────────────────────────────
        try:
            provider = llm_factory.get_provider()

            # Use .replace() to avoid brace conflicts with JSON examples
            user_prompt = (PLANNER_USER_PROMPT
                .replace("{requirements_text}", reqs_text)
                .replace("{repo_context_text}", repo_context_text)
                .replace("{constraints_text}", constraints_text)
                .replace("{risks_text}", risks_text)
                .replace("{ambiguities_text}", ambiguities_text)
            )

            messages = [
                LLMMessage(role="system", content=PLANNER_SYSTEM_PROMPT),
                LLMMessage(role="user", content=user_prompt),
            ]

            response = await provider.chat(
                messages,
                config=LLMConfig(temperature=0.1, max_tokens=4096, capability="planning"),
            )

            raw = response.content.strip()
            parsed = self._parse_json_response(raw)

            plan = self._build_plan(requirements.objective, parsed)

            # Validate plan
            validation = self._validate_plan(plan)
            if not validation.is_valid:
                logger.warning(
                    "Plan validation had %d errors: %s",
                    len(validation.errors),
                    "; ".join(validation.errors[:3]),
                )

            return plan

        except Exception as exc:
            logger.error("Planning failed: %s", exc)
            return ImplementationPlan(
                summary="",
                objective=requirements.objective,
                steps=[],
                error=f"Planning failed: {exc}",
            )

    # ── JSON parsing ──────────────────────────────────────────

    def _parse_json_response(self, text: str) -> Dict[str, Any]:
        """Parse JSON from LLM response, handling markdown fences."""
        cleaned = text.strip()

        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
            cleaned = re.sub(r"\n?\s*```\s*$", "", cleaned)
        cleaned = cleaned.strip()

        # Try direct parse
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # Extract JSON object with balanced braces
        start = cleaned.find("{")
        if start == -1:
            logger.warning("No JSON object found in planner LLM response")
            return {}

        depth = 0
        in_string = False
        escape = False
        end = start

        for i in range(start, len(cleaned)):
            ch = cleaned[i]
            if escape:
                escape = False
                continue
            if ch == "\\" and in_string:
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if not in_string:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break

        if depth != 0:
            logger.warning("Unbalanced braces in planner LLM response")
            return {}

        try:
            return json.loads(cleaned[start:end])
        except json.JSONDecodeError:
            logger.warning("Failed to parse planner JSON")
            return {}

    # ── Plan building ─────────────────────────────────────────

    def _build_plan(
        self, objective: str, data: Dict[str, Any]
    ) -> ImplementationPlan:
        """Build a validated ImplementationPlan from parsed data."""
        summary = data.get("summary", "")
        plan_objective = data.get("objective", objective)

        # Parse steps
        steps_raw = data.get("steps", [])
        steps: list[ImplementationStep] = []
        seen_ids: set[str] = set()

        for s in steps_raw[:20]:  # Limit to 20 steps
            if not isinstance(s, dict):
                continue

            step_id = str(s.get("id", f"STEP-{len(steps) + 1:03d}"))
            if step_id in seen_ids:
                step_id = f"{step_id}-{len(steps)}"
            seen_ids.add(step_id)

            depends = s.get("depends_on", [])
            if isinstance(depends, str):
                depends = [depends]

            steps.append(ImplementationStep(
                id=step_id,
                title=str(s.get("title", f"Step {len(steps) + 1}")),
                description=str(s.get("description", "")),
                affected_areas=[
                    str(a) for a in s.get("affected_areas", [])
                    if isinstance(a, str) and a.strip()
                ],
                depends_on=[
                    str(d) for d in depends
                    if isinstance(d, str) and d.strip()
                ],
                expected_changes=str(s.get("expected_changes", "")),
                validation=str(s.get("validation", "")),
                risk=s.get("risk"),
                effort_estimate=s.get("effort_estimate"),
            ))

        # Parse risks
        risks = []
        for r in data.get("risks", []):
            if isinstance(r, dict) and "description" in r:
                risks.append(Risk(
                    description=r["description"],
                    category=self._coerce_risk_category(r.get("category", "other")),
                    likelihood=str(r.get("likelihood", "medium")),
                    impact=str(r.get("impact", "medium")),
                    mitigation=r.get("mitigation"),
                ))

        # Parse requirements_coverage
        coverage = data.get("requirements_coverage", {})
        if not isinstance(coverage, dict):
            coverage = {}

        return ImplementationPlan(
            summary=summary[:2000],
            objective=plan_objective[:500],
            steps=steps,
            test_strategy=str(data.get("test_strategy", ""))[:2000],
            documentation_impact=str(data.get("documentation_impact", ""))[:1000],
            risks=risks,
            assumptions=[str(a) for a in data.get("assumptions", []) if isinstance(a, str) and a.strip()],
            requirements_coverage=coverage,
        )

    @staticmethod
    def _safe_enum_str(value: Any) -> str:
        """Convert a value to a safe string, handling enum objects."""
        if hasattr(value, "value"):
            return str(value.value)
        return str(value)

    @staticmethod
    def _coerce_risk_category(value: Any) -> RiskCategory:
        """Map an LLM-provided risk category to the enum, defaulting to OTHER.

        LLMs occasionally emit categories outside the enum (e.g.
        "logic_bug"); a strict parse failure would abort the whole plan.
        """
        raw = str(value.value) if hasattr(value, "value") else str(value)
        try:
            return RiskCategory(raw.strip())
        except ValueError:
            return RiskCategory.OTHER

    # ── Plan validation ───────────────────────────────────────

    def _validate_plan(self, plan: ImplementationPlan) -> PlanValidationResult:
        """Run deterministic validation on the plan."""
        errors: list[str] = []
        warnings: list[str] = []

        if not plan.steps:
            errors.append("Plan has no steps")

        # Check step IDs are unique
        ids: list[str] = [s.id for s in plan.steps]
        duplicate_ids = [i for i in ids if ids.count(i) > 1]
        if duplicate_ids:
            errors.append(f"Duplicate step IDs: {set(duplicate_ids)}")

        # Check for self-dependencies
        for s in plan.steps:
            if s.id in s.depends_on:
                errors.append(f"Step {s.id} depends on itself")

        # Check dependency references are valid
        valid_ids = set(ids)
        for s in plan.steps:
            for dep in s.depends_on:
                if dep not in valid_ids:
                    errors.append(
                        f"Step {s.id} references non-existent dependency: {dep}"
                    )

        # Check for cycles (simple DFS-based check)
        if plan.steps:
            adj: dict[str, list[str]] = {s.id: s.depends_on for s in plan.steps}
            has_cycle = self._has_cycle(adj)
            if has_cycle:
                errors.append("Dependency graph contains a cycle")

        return PlanValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            checked_step_count=len(plan.steps),
            checked_dependency_count=sum(len(s.depends_on) for s in plan.steps),
            has_cycles=has_cycle if plan.steps else False,
            has_self_dependencies=len(duplicate_ids) > 0,
            has_missing_dependencies=len([
                e for e in errors if "non-existent dependency" in e
            ]) > 0,
            has_duplicate_ids=len(duplicate_ids) > 0,
        )

    @staticmethod
    def _get_graph_context(inp: PlannerInput) -> str:
        """Get semantic graph context for the planner.

        Queries the graph for symbols found in requirements
        and important repo files.
        """
        try:
            from app.code_intelligence.agent_graph_helper import (
                extract_symbols_from_plan,
                get_graph_context_markdown,
            )
            plan_text = inp.requirements.objective + " " + " ".join(
                r.description for r in inp.requirements.requirements[:10]
            )
            symbol_names = extract_symbols_from_plan(plan_text)
            if symbol_names:
                return get_graph_context_markdown(
                    symbol_names=symbol_names[:10],
                    file_paths=inp.repo_important_files[:10],
                    max_context=15,
                )
        except Exception:
            pass
        return ""

    @staticmethod
    def _has_cycle(adj: dict[str, list[str]]) -> bool:
        """Detect cycles in a dependency graph using DFS."""
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {v: WHITE for v in adj}

        def dfs(node: str) -> bool:
            color[node] = GRAY
            for neighbor in adj.get(node, []):
                if neighbor not in color:
                    continue
                if color[neighbor] == GRAY:
                    return True
                if color[neighbor] == WHITE:
                    if dfs(neighbor):
                        return True
            color[node] = BLACK
            return False

        for node in adj:
            if color[node] == WHITE:
                if dfs(node):
                    return True
        return False
