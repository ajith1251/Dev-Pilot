"""
PlanningService — orchestrator for the Phase 4 planning pipeline.

Coordinates:
1. TaskInput normalization (GitHub issue → task, or user task)
2. IssueAnalyzer → StructuredRequirements
3. PlannerAgent → ImplementationPlan
4. PlanValidator → PlanValidationResult
"""

from __future__ import annotations

from typing import Any, Optional

from app.agents.issue_analyzer import IssueAnalyzerAgent
from app.agents.planner import PlannerAgent, PlannerInput
from app.core.logging import logger
from app.models.issues import (
    ImplementationPlan,
    PlanValidationResult,
    StructuredRequirements,
    TaskInput,
)
from app.prompts.issue_analysis import build_repo_context_section
from app.services.github import GitHubService
from app.services.plan_validator import PlanValidator
from app.services.repository_analyzer import RepositoryAnalyzer


class PlanningService:
    """Orchestrates the Phase 4 planning pipeline.

    Usage:
        service = PlanningService()
        result = await service.plan_from_task(
            "Add pagination to API",
            repo_path="/path/to/repo",
        )
        # result is a PlanningResult with requirements, plan, and validation
    """

    def __init__(
        self,
        issue_analyzer: Optional[IssueAnalyzerAgent] = None,
        planner: Optional[PlannerAgent] = None,
        validator: Optional[PlanValidator] = None,
        repository_analyzer: Optional[RepositoryAnalyzer] = None,
        github: Optional[GitHubService] = None,
    ) -> None:
        self._issue_analyzer = issue_analyzer or IssueAnalyzerAgent()
        self._planner = planner or PlannerAgent()
        self._validator = validator or PlanValidator()
        self._repo_analyzer = repository_analyzer or RepositoryAnalyzer()
        self._github = github or GitHubService()

    async def plan_from_github_issue(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        acquire_locally: bool = False,
    ) -> "PlanningResult":
        """Plan from a GitHub issue, optionally with local repository analysis.

        Args:
            owner: GitHub owner.
            repo: Repository name.
            issue_number: Issue number.
            acquire_locally: If True, acquire and analyze the repo locally.

        Returns:
            PlanningResult with requirements, plan, and validation.
        """
        # Fetch GitHub issue
        try:
            issue = await self._github.get_issue(owner, repo, issue_number)
        except Exception as exc:
            return PlanningResult(
                task=TaskInput(
                    source="github_issue",
                    title=f"#{issue_number} from {owner}/{repo}",
                    description=f"Failed to fetch issue: {exc}",
                    issue_number=issue_number,
                    repository=f"{owner}/{repo}",
                ),
                error=f"GitHub fetch failed: {exc}",
            )

        task = TaskInput(
            source="github_issue",
            title=issue.title,
            description=issue.body or "",
            issue_number=issue.number,
            issue_url=issue.html_url,
            labels=[l.name for l in issue.labels],
            repository=f"{owner}/{repo}",
        )

        # Optionally analyze repository
        repo_languages = []
        repo_technologies = []
        repo_modules = []
        repo_commands = []
        repo_important_files = []
        repo_tree = ""

        if acquire_locally:
            try:
                from app.services.remote_analyzer import RemoteRepositoryAnalyzer

                remote = RemoteRepositoryAnalyzer()
                result = await remote.analyze(
                    f"https://github.com/{owner}/{repo}"
                )
                if result.profile:
                    p = result.profile
                    repo_languages = [
                        lang.get("name", "")
                        for lang in p.get("languages", [])
                    ]
                    repo_technologies = [
                        tech.get("name", "")
                        for tech in p.get("technologies", [])
                    ]
                    repo_modules = [
                        mod.get("name", "")
                        for mod in p.get("modules", [])
                    ]
                    repo_important_files = [
                        f.get("path", "")
                        for f in p.get("important_files", [])
                    ]
                    tree_node = p.get("tree", {})
                    if tree_node and isinstance(tree_node, dict):
                        repo_tree = tree_node.get("text", "")
            except Exception as exc:
                logger.warning(
                    "Failed to acquire repo for planning context: %s",
                    exc,
                )

        task.repo_languages = repo_languages
        task.repo_technologies = repo_technologies
        task.repo_modules = repo_modules
        task.repo_commands = repo_commands
        task.repo_important_files = repo_important_files
        task.repo_tree_preview = repo_tree

        return await self._run_pipeline(task)

    async def plan_from_task(
        self,
        title: str,
        description: str = "",
        repo_path: Optional[str] = None,
        agent_context: Any = None,
        requirements: Optional["StructuredRequirements"] = None,
    ) -> "PlanningResult":
        """Plan from a user-provided task, optionally with local repo analysis.

        Args:
            title: Task title.
            description: Task description.
            repo_path: Optional local repository path for context.
            agent_context: Phase 13 ContextEngine context for the planner.
            requirements: Pre-computed StructuredRequirements. When provided
                (e.g. the orchestrator already ran task analysis and stored
                run.requirements), the LLM issue-analysis step is SKIPPED and
                these requirements are used directly — avoiding a redundant
                LLM call that can fail independently.

        Returns:
            PlanningResult with requirements, plan, and validation.
        """
        task = TaskInput(
            source="user_task",
            title=title,
            description=description or "",
        )

        # Optionally analyze local repository
        if repo_path:
            try:
                profile = self._repo_analyzer.analyze(repo_path)
                task.repo_languages = [
                    lang.name for lang in profile.languages
                ]
                task.repo_technologies = [
                    tech.name for tech in profile.technologies
                ]
                task.repo_modules = [
                    mod.name for mod in profile.modules
                ]
                task.repo_important_files = [
                    f.path for f in profile.important_files
                ]
                if profile.tree:
                    task.repo_tree_preview = profile.tree.text
            except Exception as exc:
                logger.warning(
                    "Failed to analyze repo for planning context: %s",
                    exc,
                )

        return await self._run_pipeline(task, agent_context=agent_context, requirements=requirements)

    async def _run_pipeline(
        self,
        task: TaskInput,
        agent_context: Any = None,
        requirements: Optional["StructuredRequirements"] = None,
    ) -> "PlanningResult":
        """Run the full planning pipeline: analyze → plan → validate.

        Args:
            task: Normalized task input.
            agent_context: Phase 13 ContextEngine context for the planner.
            requirements: Pre-computed requirements (skips the LLM issue
                analysis when provided — see plan_from_task).

        Returns:
            PlanningResult with all pipeline outputs.
        """
        repo_context = self._build_repo_context_str(task)

        # ── Step 1/2: Analyze issue + convert (SKIPPED when requirements
        # are pre-computed — the caller already ran task analysis).
        if requirements is None:
            try:
                # Use existing issue analyzer for LLM analysis
                from app.models.issues import IssueAnalysisInput

                analysis_input = IssueAnalysisInput(
                    title=task.title,
                    body=task.description,
                    repo_context=repo_context,
                )
                analysis = await self._issue_analyzer.execute(analysis_input)
            except Exception as exc:
                return PlanningResult(
                    task=task,
                    error=f"Issue analysis failed: {exc}",
                )

            if analysis.error:
                return PlanningResult(
                    task=task,
                    error=analysis.error,
                )

            requirements = self._convert_to_structured(task, analysis, repo_context)
            if requirements.error:
                return PlanningResult(
                    task=task,
                    requirements=requirements,
                    error=requirements.error,
                )

        # ── Step 3: Generate plan ───────────────────────────────
        planner_input = PlannerInput(
            requirements=requirements,
            repo_languages=task.repo_languages,
            repo_technologies=task.repo_technologies,
            repo_modules=task.repo_modules,
            repo_commands=task.repo_commands,
            repo_important_files=task.repo_important_files,
            repo_tree_preview=task.repo_tree_preview,
            agent_context=agent_context,
        )

        try:
            plan = await self._planner.execute(planner_input)
        # Also pass agent_context to plan_from_task for Phase 13 context integration
        # (the explicit post-task-analysis context is passed separately below)
        except Exception as exc:
            return PlanningResult(
                task=task,
                requirements=requirements,
                error=f"Planning failed: {exc}",
            )

        # ── Step 4: Validate plan ───────────────────────────────
        validation = self._validator.validate(plan)

        return PlanningResult(
            task=task,
            requirements=requirements,
            plan=plan,
            validation=validation,
            error=plan.error,
        )

    @staticmethod
    def _build_repo_context_str(task: TaskInput) -> str:
        """Build a compact repository context string for the LLM."""
        return build_repo_context_section(
            languages=task.repo_languages,
            technologies=task.repo_technologies,
            modules=task.repo_modules,
            commands=task.repo_commands,
            important_files=task.repo_important_files,
            tree_preview=task.repo_tree_preview,
        )

    @staticmethod
    def _convert_to_structured(
        task: TaskInput,
        analysis: object,
        repo_context: str = "",
    ) -> StructuredRequirements:
        """Convert IssueAnalysisOutput to StructuredRequirements.

        This bridges the Phase 1 Issue Analyzer output format to
        the Phase 4 domain model.
        """
        from app.models.issues import (
            Ambiguity,
            AmbiguityCategory,
            Constraint,
            ConstraintCategory,
            IssueAnalysisOutput,
            Requirement,
            Risk,
            RiskCategory,
        )

        if not isinstance(analysis, IssueAnalysisOutput):
            return StructuredRequirements(
                objective=task.title,
                error="Unexpected analysis output type",
            )

        output = analysis

        # Convert requirements
        requirements = [
            Requirement(
                description=r.description,
                requirement_type=r.requirement_type,
                is_implied=r.is_implied,
                acceptance_note=r.acceptance_note,
            )
            for r in output.requirements
        ]

        # Create constraints from implied information
        constraints: list[Constraint] = []

        # Create likely affected areas from affected_components
        from app.models.issues import AffectedArea

        affected_areas = [
            AffectedArea(
                path=comp,
                description=f"Likely affected component",
                confidence="medium",
            )
            for comp in output.affected_components
        ]

        # Create ambiguities from missing_info_questions
        ambiguities: list[Ambiguity] = []
        for q in output.missing_info_questions:
            ambiguities.append(Ambiguity(
                description=q,
                category=AmbiguityCategory.MISSING_CONTEXT,
                question=q,
            ))

        # Create risks from severity
        risks: list[Risk] = []
        if output.severity.value in ("critical", "high"):
            risks.append(Risk(
                description=f"High severity task: {output.summary[:200]}",
                category=RiskCategory.COMPLEXITY,
                likelihood="high",
                impact="high",
            ))

        # Confidence
        if output.needs_more_info:
            confidence = "low"
        elif output.priority_score >= 7:
            confidence = "high"
        else:
            confidence = "medium"

        return StructuredRequirements(
            objective=output.summary or task.title,
            requirements=requirements,
            constraints=constraints,
            likely_affected_areas=affected_areas,
            ambiguities=ambiguities,
            risks=risks,
            assumptions=[f"Based on issue type: {output.issue_type.value}"],
            confidence=confidence,
        )


class PlanningResult:
    """Result of the planning pipeline."""

    def __init__(
        self,
        task: TaskInput,
        requirements: Optional[StructuredRequirements] = None,
        plan: Optional[ImplementationPlan] = None,
        validation: Optional[PlanValidationResult] = None,
        error: Optional[str] = None,
    ) -> None:
        self.task = task
        self.requirements = requirements
        self.plan = plan
        self.validation = validation
        self.error = error

    @property
    def success(self) -> bool:
        """Whether the full pipeline succeeded."""
        if self.error:
            return False
        if self.plan and self.plan.error:
            return False
        if self.validation and not self.validation.is_valid:
            return False
        return self.plan is not None

    def to_dict(self) -> dict:
        """Convert to a serializable dict."""
        return {
            "success": self.success,
            "error": self.error,
            "task": {
                "source": self.task.source.value,
                "title": self.task.title[:200],
                "repository": self.task.repository,
                "issue_number": self.task.issue_number,
                "labels": self.task.labels,
            },
            "requirements": (
                self.requirements.model_dump() if self.requirements else None
            ),
            "plan": (
                self.plan.model_dump() if self.plan else None
            ),
            "validation": (
                self.validation.model_dump() if self.validation else None
            ),
        }
