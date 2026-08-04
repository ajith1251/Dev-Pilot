"""
PlanValidator — deterministic validation of ImplementationPlan structure.

All validation is done via software rules, not LLM calls.
Validates:
- Plan is non-empty
- Steps are non-empty
- Step IDs are unique
- Dependency references point to existing steps
- No self-dependencies
- Dependency graph is acyclic
- Required fields exist on steps
- Length limits
"""

from __future__ import annotations

from typing import List

from app.models.issues import (
    ImplementationPlan,
    PlanValidationResult,
)


# ── Max values ──────────────────────────────────────────────────
MAX_STEPS = 30
MAX_DESC_CHARS = 5000
MAX_STEP_TITLE_CHARS = 200


class PlanValidator:
    """Deterministic validator for ImplementationPlan objects.

    All validation is performed purely through software — no LLM calls.
    """

    def validate(self, plan: ImplementationPlan) -> PlanValidationResult:
        """Validate an ImplementationPlan.

        Args:
            plan: The plan to validate.

        Returns:
            PlanValidationResult with errors and warnings.
        """
        errors: list[str] = []
        warnings: list[str] = []

        # ── Structural checks ──────────────────────────────────
        if not plan.objective:
            errors.append("Plan objective is empty")

        if not plan.summary:
            warnings.append("Plan summary is empty")

        if not plan.steps:
            errors.append("Plan has no steps")
            return PlanValidationResult(
                is_valid=False,
                errors=errors,
                warnings=warnings,
            )

        if len(plan.steps) > MAX_STEPS:
            errors.append(
                f"Plan has {len(plan.steps)} steps (max {MAX_STEPS})"
            )

        # ── Step ID uniqueness ─────────────────────────────────
        ids: list[str] = [s.id for s in plan.steps]
        seen: set[str] = set()
        duplicate_ids: set[str] = set()

        for sid in ids:
            if sid in seen:
                duplicate_ids.add(sid)
            seen.add(sid)

        if duplicate_ids:
            errors.append(
                f"Duplicate step IDs: {sorted(duplicate_ids)}"
            )

        # ── Self-dependencies ──────────────────────────────────
        self_deps: list[str] = []
        for s in plan.steps:
            if s.id in s.depends_on:
                self_deps.append(s.id)

        if self_deps:
            errors.append(
                f"Self-dependencies detected: {sorted(self_deps)}"
            )

        # ── Dependency reference validity ──────────────────────
        valid_ids: set[str] = set(ids)
        missing_deps: list[str] = []

        for s in plan.steps:
            for dep in s.depends_on:
                if dep not in valid_ids:
                    missing_deps.append(
                        f"Step '{s.id}' depends on unknown '{dep}'"
                    )

        if missing_deps:
            errors.append(
                f"Non-existent dependency references: {'; '.join(missing_deps[:10])}"
            )

        # ── Cycle detection (DFS) ──────────────────────────────
        adj: dict[str, list[str]] = {}
        for s in plan.steps:
            adj[s.id] = [
                d for d in s.depends_on if d in valid_ids
            ]

        has_cycle = self._has_cycle(adj)

        if has_cycle:
            errors.append("Dependency graph contains a cycle")

        # ── Field emptiness checks ─────────────────────────────
        empty_title_count = sum(1 for s in plan.steps if not s.title)
        if empty_title_count:
            warnings.append(
                f"{empty_title_count} step(s) have empty titles"
            )

        empty_desc_count = sum(1 for s in plan.steps if not s.description)
        if empty_desc_count:
            warnings.append(
                f"{empty_desc_count} step(s) have empty descriptions"
            )

        empty_validation_count = sum(1 for s in plan.steps if not s.validation)
        if empty_validation_count:
            warnings.append(
                f"{empty_validation_count} step(s) have no validation criteria"
            )

        # ── Field length checks ────────────────────────────────
        for s in plan.steps:
            if len(s.title) > MAX_STEP_TITLE_CHARS:
                warnings.append(
                    f"Step '{s.id}' title exceeds {MAX_STEP_TITLE_CHARS} chars"
                )
            if len(s.description) > MAX_DESC_CHARS:
                warnings.append(
                    f"Step '{s.id}' description exceeds {MAX_DESC_CHARS} chars"
                )

        # ── Requirements coverage checks ───────────────────────
        if plan.requirements_coverage:
            empty_coverage = [
                req_id for req_id, step_ids in plan.requirements_coverage.items()
                if not step_ids
            ]
            if empty_coverage:
                warnings.append(
                    f"Requirements with no linked steps: {empty_coverage}"
                )

            # Check coverage step IDs exist
            for req_id, step_ids in plan.requirements_coverage.items():
                for step_id in step_ids:
                    if step_id not in valid_ids:
                        warnings.append(
                            f"Requirement '{req_id}' references unknown step '{step_id}'"
                        )

        # ── Test strategy ──────────────────────────────────────
        if not plan.test_strategy:
            warnings.append("No test strategy specified")

        dep_count = sum(len(s.depends_on) for s in plan.steps)

        return PlanValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            checked_step_count=len(plan.steps),
            checked_dependency_count=dep_count,
            has_cycles=has_cycle,
            has_self_dependencies=len(self_deps) > 0,
            has_missing_dependencies=len(missing_deps) > 0,
            has_duplicate_ids=len(duplicate_ids) > 0,
        )

    @staticmethod
    def _has_cycle(adj: dict[str, list[str]]) -> bool:
        """Detect cycles in a dependency graph using DFS with colors."""
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {v: WHITE for v in adj}

        def dfs(node: str) -> bool:
            color[node] = GRAY
            for neighbor in adj.get(node, []):
                if color.get(neighbor) == GRAY:
                    return True
                if color.get(neighbor) == WHITE:
                    if dfs(neighbor):
                        return True
            color[node] = BLACK
            return False

        for node in adj:
            if color.get(node) == WHITE:
                if dfs(node):
                    return True
        return False

    @staticmethod
    def validate_requirements_coverage(
        plan: ImplementationPlan,
    ) -> PlanValidationResult:
        """Run additional validation focused on requirements coverage.

        Can be called separately to validate traceability.

        Args:
            plan: The plan to check.

        Returns:
            PlanValidationResult with coverage-specific findings.
        """
        errors: list[str] = []
        warnings: list[str] = []

        if not plan.requirements_coverage:
            warnings.append("No requirements coverage map present")

            return PlanValidationResult(
                is_valid=True,
                errors=errors,
                warnings=warnings,
                checked_step_count=len(plan.steps),
            )

        valid_step_ids = {s.id for s in plan.steps}

        for req_id, step_ids in plan.requirements_coverage.items():
            for step_id in step_ids:
                if step_id not in valid_step_ids:
                    errors.append(
                        f"Requirement '{req_id}' → step '{step_id}' does not exist"
                    )

        uncovered_steps = set()
        for s in plan.steps:
            found = False
            for step_ids in plan.requirements_coverage.values():
                if s.id in step_ids:
                    found = True
                    break
            if not found:
                uncovered_steps.add(s.id)

        if uncovered_steps:
            warnings.append(
                f"Uncovered steps (not linked to any requirement): "
                f"{sorted(uncovered_steps)}"
            )

        return PlanValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )
