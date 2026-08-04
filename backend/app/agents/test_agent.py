"""
Test Agent — Phase 7

Reasons about what verification is relevant and produces an ExecutionPlan
for the Controlled Execution Engine.

Design:
- May use LLM for ambiguous/high-level selection
- Default deterministic behavior when context is unambiguous
- Does NOT execute processes directly (deterministic boundary)
- Output is validated by ExecutionPolicy before execution

Input may include:
    ImplementationPlan (Phase 4)
    PatchApplicationResult (Phase 6)
    Changed files
    RepositoryProfile (Phase 2)
    Detected commands (Phase 2)
    Workspace metadata

Output:
    ExecutionPlan
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from app.agents.base import BaseAgent
from app.config import settings
from app.llm.base import BaseLLMProvider, LLMConfig, LLMMessage, LLMResponse
from app.llm.factory import factory as llm_factory
from app.models.base import new_id
from app.models.coding import PatchApplicationResult
from app.models.testing import (
    CommandCandidate,
    CommandCategory,
    CommandSource,
    ExecutionPlan,
    ExecutionStep,
)
from app.prompts.testing import build_test_plan_prompt
from app.services.testing_service import TestingService


class TestAgentInput:
    """Input to the Test Agent for planning test execution.

    Flexible container — fields are optional depending on available context.
    """

    def __init__(
        self,
        workspace_id: str,
        workspace_root: str,
        candidates: Optional[List[CommandCandidate]] = None,
        changed_files: Optional[List[str]] = None,
        patch_result: Optional[PatchApplicationResult] = None,
        repository_language: Optional[str] = None,
        repository_frameworks: Optional[List[str]] = None,
        extra_context: Optional[Dict[str, Any]] = None,
        agent_context: Optional[Any] = None,
    ):
        self.workspace_id = workspace_id
        self.workspace_root = workspace_root
        self.candidates = candidates or []
        self.changed_files = changed_files or []
        self.patch_result = patch_result
        self.repository_language = repository_language
        self.repository_frameworks = repository_frameworks or []
        self.extra_context = extra_context or {}
        # Phase 13: ContextEngine-produced context (replaces static graph context fallback)
        self.agent_context = agent_context


class TestAgentOutput:
    """Output from the Test Agent — the ExecutionPlan and reasoning."""

    def __init__(
        self,
        plan: ExecutionPlan,
        reasoning: Optional[str] = None,
        warnings: Optional[List[str]] = None,
    ):
        self.plan = plan
        self.reasoning = reasoning or ""
        self.warnings = warnings or []


class TestAgent(BaseAgent[TestAgentInput, TestAgentOutput]):
    """Test Agent: determines what to test and in what order.

    This agent does NOT execute processes. It produces execution plans
    that are validated by ExecutionPolicy and run by ControlledExecutionEngine.

    Two modes:
    1. Deterministic (default): Uses TestingService.discover_commands() + build_plan()
       No LLM required. Suitable for standard projects with well-known frameworks.

    2. LLM-powered (use_llm=True): Uses LLM to analyze workspace context and
       make smarter command selection, ordering, and test targeting decisions.
       Useful when:
       - Multiple testing frameworks are present
       - Complex monorepo with many test targets
       - Test selection needs to be optimized for change impact
       - Ambiguity exists about which commands are meaningful
    """

    def __init__(
        self,
        testing_service: Optional[TestingService] = None,
        llm_provider: Optional[BaseLLMProvider] = None,
        model: Optional[str] = None,
        use_llm: bool = False,
    ):
        super().__init__(name="test_agent", max_retries=2)
        self._service = testing_service or TestingService()
        self._llm_provider = llm_provider
        self._model = model or settings.LLM_MODEL
        self._use_llm = use_llm

    async def execute(self, inp: TestAgentInput) -> TestAgentOutput:
        """Execute the Test Agent to produce an ExecutionPlan.

        Uses LLM if use_llm=True and a provider is available.
        Falls back to deterministic planning otherwise.
        """
        if self._use_llm and self._llm_provider:
            return await self._execute_with_llm(inp)

        return await self._execute_deterministic(inp)

    def _get_agent_context_str(self, inp: TestAgentInput) -> str:
        """Get Phase 13 AgentContext string or fall back to empty."""
        if inp.agent_context is not None:
            try:
                return inp.agent_context.build_prompt_section()
            except Exception:
                pass
        return ""

    async def _execute_deterministic(
        self, inp: TestAgentInput
    ) -> TestAgentOutput:
        """Execute with fully deterministic planning (no LLM)."""
        reasoning_parts: List[str] = []
        warnings: List[str] = []

        # 1. Use candidates from input or discover from workspace
        candidates = inp.candidates
        if not candidates:
            candidates = self._service.discover_commands(inp.workspace_root)

        if not candidates:
            warnings.append("No candidate commands discovered in workspace")

        # 2. If we have changed files, try to add targeted tests
        if inp.changed_files and candidates:
            test_files = self._service.find_related_tests(
                inp.workspace_root, inp.changed_files
            )
            if test_files:
                reasoning_parts.append(
                    f"Changed files: {inp.changed_files[:5]} → "
                    f"Related tests: {test_files}"
                )

        # 3. Build execution plan
        plan = self._service.build_plan(
            workspace_id=inp.workspace_id,
            workspace_root=inp.workspace_root,
            candidates=candidates,
            changed_files=inp.changed_files,
        )

        # 4. Add reasoning
        reasoning_parts.append(f"Plan has {len(plan.steps)} steps")
        for step in plan.steps:
            cmd = f"{step.executable} {' '.join(step.arguments)}"
            reasoning_parts.append(f"  {step.step_id}: {step.category.value} — {cmd}")

        reasoning = "\n".join(reasoning_parts)

        return TestAgentOutput(
            plan=plan,
            reasoning=reasoning,
            warnings=warnings,
        )

    async def _execute_with_llm(
        self, inp: TestAgentInput
    ) -> TestAgentOutput:
        """Execute with LLM-powered planning for smarter test selection."""
        reasoning_parts: List[str] = []
        warnings: List[str] = []

        # Resolve LLM provider if not provided
        provider = self._llm_provider
        if provider is None:
            try:
                provider = llm_factory.get_provider()
            except Exception as exc:
                warnings.append(f"LLM provider unavailable, falling back to deterministic: {exc}")
                return await self._execute_deterministic(inp)

        # 1. Build workspace summary
        workspace_summary = self._build_workspace_summary(inp)

        # 2. Get or discover candidates
        candidates = inp.candidates
        if not candidates:
            candidates = self._service.discover_commands(inp.workspace_root)

        # 3. Format candidates for the prompt
        candidates_text = self._format_candidates_for_prompt(candidates)

        # 4. Format changed files
        changed_files_text = "\n".join(inp.changed_files[:20]) if inp.changed_files else "No files changed (new test run)"

        # 5. Format previous results if available
        previous_results = ""
        if inp.patch_result:
            patch = inp.patch_result
            prev_parts = []
            if patch.files_created:
                prev_parts.append(f"Created: {patch.files_created[:5]}")
            if patch.files_modified:
                prev_parts.append(f"Modified: {patch.files_modified[:5]}")
            if patch.files_deleted:
                prev_parts.append(f"Deleted: {patch.files_deleted[:5]}")
            if patch.errors:
                prev_parts.append(f"Errors: {patch.errors[:3]}")
            previous_results = "Patch Application:\n" + "\n".join(prev_parts)

        # 6. Build the prompt
        prompt = build_test_plan_prompt(
            workspace_summary=workspace_summary,
            candidates=candidates_text,
            changed_files=changed_files_text,
            previous_results=previous_results,
        )

        # 7. Call LLM
        try:
            messages = [LLMMessage(role="user", content=prompt)]
            config = LLMConfig(
                model=self._model,
                temperature=0.2,
                max_tokens=2048,
            )
            response: LLMResponse = await provider.chat(
                messages=messages,
                config=config,
            )
            raw_response = response.content
        except Exception as exc:
            warnings.append(f"LLM call failed, falling back to deterministic: {exc}")
            return await self._execute_deterministic(inp)

        # 8. Parse LLM response
        llm_plan, llm_warnings = self._parse_llm_response(
            raw_response, inp, candidates
        )
        warnings.extend(llm_warnings)

        if llm_plan is not None:
            reasoning_parts.append("LLM-powered test selection:")
            for step in llm_plan.steps:
                cmd = f"{step.executable} {' '.join(step.arguments)}"
                reasoning_parts.append(f"  {step.step_id}: {step.category.value} — {cmd}")
                if step.reason:
                    reasoning_parts.append(f"    Reason: {step.reason}")

            reasoning = "\n".join(reasoning_parts)
            return TestAgentOutput(
                plan=llm_plan,
                reasoning=reasoning,
                warnings=warnings,
            )

        # 9. Fallback: LLM output couldn't be parsed, use deterministic
        warnings.append("Could not parse LLM response, falling back to deterministic")
        return await self._execute_deterministic(inp)

    def _build_workspace_summary(self, inp: TestAgentInput) -> str:
        """Build a concise summary of the workspace for the LLM prompt."""
        parts = []
        parts.append(f"Workspace ID: {inp.workspace_id}")
        parts.append(f"Workspace Root: {inp.workspace_root}")

        if inp.repository_language:
            parts.append(f"Primary Language: {inp.repository_language}")

        if inp.repository_frameworks:
            parts.append(f"Frameworks: {', '.join(inp.repository_frameworks)}")

        if inp.changed_files:
            parts.append(f"\nChanged Files ({len(inp.changed_files)}):")
            for f in inp.changed_files[:15]:
                parts.append(f"  • {f}")
            if len(inp.changed_files) > 15:
                parts.append(f"  ... and {len(inp.changed_files) - 15} more")

        # Phase 13: Include ContextEngine context if available (replaces static graph context)
        agent_ctx = self._get_agent_context_str(inp)
        if agent_ctx:
            parts.append(f"\n{agent_ctx}")
        else:
            # Fallback: use static graph context (Phase 12)
            try:
                from app.code_intelligence.agent_graph_helper import (
                    extract_symbols_from_changed_files,
                    get_graph_context_markdown,
                )
                symbol_names = extract_symbols_from_changed_files(inp.changed_files)
                if symbol_names:
                    ctx = get_graph_context_markdown(
                        symbol_names=symbol_names[:10],
                        file_paths=inp.changed_files[:10],
                        max_context=15,
                        repo_path=inp.workspace_root,
                    )
                    if ctx:
                        parts.append(ctx)
            except Exception:
                pass

        if inp.extra_context:
            for key, value in inp.extra_context.items():
                parts.append(f"{key}: {value}")

        return "\n".join(parts)

    @staticmethod
    def _format_candidates_for_prompt(candidates: List[CommandCandidate]) -> str:
        """Format candidates for the LLM prompt."""
        if not candidates:
            return "No commands discovered."

        parts = []
        for c in candidates:
            cmd = f"{c.executable} {' '.join(c.arguments)}"
            parts.append(
                f"  • [{c.source.value}] (confidence: {c.confidence}) "
                f"{c.category.value}: {cmd}"
            )
            if c.reason:
                parts.append(f"    Reason: {c.reason}")

        return "\n".join(parts)

    def _parse_llm_response(
        self,
        raw_response: str,
        inp: TestAgentInput,
        candidates: List[CommandCandidate],
    ) -> tuple:
        """Parse the LLM response into an ExecutionPlan.

        Returns:
            Tuple of (ExecutionPlan or None, warnings list)
        """
        warnings: List[str] = []

        # Try to extract JSON from the response
        json_str = self._extract_json(raw_response)
        if not json_str:
            warnings.append("No JSON found in LLM response")
            return None, warnings

        # Parse JSON
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as exc:
            warnings.append(f"Failed to parse LLM response as JSON: {exc}")
            return None, warnings

        # Check for "nothing to test" status
        if data.get("status") == "nothing_to_test":
            reasoning = data.get("reasoning", "No verification needed")
            plan = ExecutionPlan(
                plan_id=f"plan-{new_id()[:8]}",
                workspace_id=inp.workspace_id,
                workspace_root=inp.workspace_root,
                steps=[],
            )
            return plan, [reasoning]

        # Parse selected commands
        selected = data.get("selected_commands", [])
        if not selected:
            warnings.append("No commands selected by LLM")
            return None, warnings

        steps: List[ExecutionStep] = []
        for i, cmd_data in enumerate(selected):
            executable = cmd_data.get("executable", "")
            arguments = cmd_data.get("arguments", [])
            category_str = cmd_data.get("category", "other")
            reason = cmd_data.get("reason", "")
            priority = cmd_data.get("priority", 2)
            timeout = cmd_data.get("timeout_seconds", 60)

            # Validate executable
            if not executable:
                continue

            # Validate this command exists in the candidates list
            # Prevents LLM from inventing arbitrary commands
            if not self._validate_against_candidates(executable, arguments, candidates):
                warnings.append(
                    f"LLM suggested command '{executable} {' '.join(arguments)}' "
                    f"does not match any discovered candidate — skipping"
                )
                continue

            # Map category string to enum
            category = CommandCategory.OTHER
            try:
                category = CommandCategory(category_str)
            except ValueError:
                pass

            step = ExecutionStep(
                step_id=f"STEP-{i + 1:03d}",
                category=category,
                executable=executable,
                arguments=arguments,
                timeout_seconds=min(timeout, 300),
                required=(priority == 1),
                source=CommandSource.USER_APPROVED,
                reason=reason,
            )
            steps.append(step)

        plan = ExecutionPlan(
            plan_id=f"plan-{new_id()[:8]}",
            workspace_id=inp.workspace_id,
            workspace_root=inp.workspace_root,
            steps=steps,
        )

        return plan, warnings

    @staticmethod
    def _extract_json(text: str) -> Optional[str]:
        """Extract JSON from text, handling markdown code fences."""
        # Try extracting from ```json ... ``` blocks
        json_pattern = r"```(?:json)?\s*\n?(.*?)\n?```"
        matches = re.findall(json_pattern, text, re.DOTALL)
        if matches:
            return matches[0].strip()

        # Try finding {...} directly
        start_idx = text.find("{")
        if start_idx >= 0:
            depth = 0
            for i in range(start_idx, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        return text[start_idx: i + 1]

        return None

    def _validate_against_candidates(
        self,
        executable: str,
        arguments: List[str],
        candidates: List[CommandCandidate],
    ) -> bool:
        """Check if a command suggested by the LLM matches any known candidate.

        This prevents the LLM from inventing arbitrary commands that weren't
        discovered from the repository configuration.
        """
        llm_cmd = f"{executable} {' '.join(arguments)}"

        for candidate in candidates:
            candidate_cmd = f"{candidate.executable} {' '.join(candidate.arguments)}"
            # Check if the LLM's command is a superset of a candidate
            # (e.g., candidate is "python -m pytest -q" and LLM adds specific test files)
            if llm_cmd.startswith(candidate_cmd):
                return True
            # Check if they're the same executable with same -m module
            if (
                candidate.executable == executable
                and len(candidate.arguments) >= 2
                and len(arguments) >= 2
                and candidate.arguments[0] == arguments[0]  # same flag (-m, run, etc.)
                and candidate.arguments[1] == arguments[1]  # same module/script
            ):
                return True

        return False

    async def plan_from_patch(
        self,
        workspace_id: str,
        workspace_root: str,
        patch_result: PatchApplicationResult,
    ) -> TestAgentOutput:
        """Create an execution plan from a Phase 6 patch result."""
        candidates = self._service.discover_from_patch(workspace_root, patch_result)

        changed_files = (
            patch_result.files_created
            + patch_result.files_modified
            + patch_result.files_deleted
        )

        inp = TestAgentInput(
            workspace_id=workspace_id,
            workspace_root=workspace_root,
            candidates=candidates,
            changed_files=changed_files,
            patch_result=patch_result,
        )

        return await self.execute(inp)
