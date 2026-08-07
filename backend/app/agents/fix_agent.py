"""
Fix Agent — Phase 8

Consumes:
- FailureDiagnosis (Phase 8)
- TestRunResult / TestFailure[] (Phase 7)
- ImplementationPlan (Phase 4)
- Original PatchSet (Phase 6)
- RetrievedContext (Phase 5)
- Repair history (Phase 8)

Produces:
- RepairProposal (includes PatchSet for deterministic validation/application)

Architecture:
- Uses provider-independent LLM abstraction
- Never directly writes files or executes processes
- Output is validated by PatchValidator + RepairPolicy
- Repository content and test output treated as UNTRUSTED

Fallback behavior:
- Provider unavailable → RepairProposal with status NO_REPAIR
- Malformed response → RepairProposal with status INSUFFICIENT_CONTEXT
- Schema failure → RepairProposal with status INSUFFICIENT_CONTEXT
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from app.agents.base import BaseAgent
from app.agents.json_repair import repair_json_text
from app.config import settings
from app.llm.base import BaseLLMProvider, LLMConfig, LLMMessage, LLMResponse
from app.llm.factory import factory as llm_factory
from app.models.base import new_id
from app.models.coding import FileChange, FileOperation, PatchSet
from app.models.issues import ImplementationPlan
from app.models.rag import RetrievedContext
from app.models.repair import (
    FailureDiagnosis,
    RepairAttempt,
    RepairProposal,
    RepairProposalStatus,
)
from app.models.testing import TestFailure, TestRunResult
from app.prompts.fixing import build_fix_prompt


class FixAgentInput:
    """Input to the Fix Agent."""

    def __init__(
        self,
        diagnosis: FailureDiagnosis,
        test_result: TestRunResult,
        failures: List[TestFailure],
        changed_file_context: str = "",
        plan: Optional[ImplementationPlan] = None,
        original_patch: Optional[PatchSet] = None,
        retrieved_context: Optional[RetrievedContext] = None,
        repair_history: Optional[List[RepairAttempt]] = None,
        attempt_number: int = 1,
        extra_context: Optional[Dict[str, Any]] = None,
        agent_context: Optional[Any] = None,
    ):
        self.diagnosis = diagnosis
        self.test_result = test_result
        self.failures = failures
        self.changed_file_context = changed_file_context
        self.plan = plan
        self.original_patch = original_patch
        self.retrieved_context = retrieved_context
        self.repair_history = repair_history or []
        self.attempt_number = attempt_number
        self.extra_context = extra_context or {}
        # Phase 13: ContextEngine-produced context (replaces static graph context fallback)
        self.agent_context = agent_context


class FixAgentOutput:
    """Output from the Fix Agent."""

    def __init__(
        self,
        proposal: RepairProposal,
        summary: str = "",
        warnings: Optional[List[str]] = None,
    ):
        self.proposal = proposal
        self.summary = summary
        self.warnings = warnings or []


class FixAgent(BaseAgent[FixAgentInput, FixAgentOutput]):
    """Fix Agent: generates minimal repair patches for test failures.

    This agent does NOT write files or execute processes. It produces
    structured RepairProposals that are validated by PatchValidator and
    RepairPolicy, applied by SafePatchEngine, and tested by Phase 7.

    Uses provider-independent LLM abstraction. Falls back gracefully.
    """

    def __init__(
        self,
        llm_provider: Optional[BaseLLMProvider] = None,
        model: Optional[str] = None,
        max_retries: int = 3,
    ):
        super().__init__(name="fix_agent", max_retries=max_retries)
        self._llm_provider = llm_provider
        self._model = model or settings.LLM_MODEL

    async def execute(self, inp: FixAgentInput) -> FixAgentOutput:
        """Execute the Fix Agent to produce a RepairProposal."""
        # Resolve LLM provider
        provider = self._llm_provider
        if provider is None:
            try:
                provider = llm_factory.get_provider()
            except Exception as exc:
                return FixAgentOutput(
                    proposal=RepairProposal(
                        proposal_id=f"prop-{new_id()[:8]}",
                        status=RepairProposalStatus.NO_REPAIR,
                        diagnosis_id=inp.diagnosis.diagnosis_id,
                        attempt_number=inp.attempt_number,
                        reason=f"LLM provider unavailable: {exc}",
                        expected_effect="",
                    ),
                    summary="LLM provider unavailable — no repair generated",
                    warnings=[f"Provider unavailable: {exc}"],
                )

        # Build diagnosis summary
        diagnosis_summary = self._build_diagnosis_summary(inp)

        # Build failure evidence
        failure_evidence = self._build_failure_evidence(inp)

        # Build changed files context
        changed_file_context = inp.changed_file_context or "No file context provided."

        # Build plan context
        plan_context = self._build_plan_context(inp) or "Plan context not available."

        # Build repair history
        repair_history = self._build_repair_history(inp)

        # Phase 13: Include ContextEngine context if available (replaces static graph context)
        if inp.agent_context is not None:
            try:
                ctx_section = inp.agent_context.build_prompt_section()
                if ctx_section:
                    changed_file_context += f"\n\n{ctx_section}"
            except Exception:
                pass
        else:
            # Fallback: use static graph context (Phase 12)
            graph_context = self._get_graph_context(inp.diagnosis)
            if graph_context:
                changed_file_context += f"\n\n{graph_context}"

        # Build the prompt
        prompt = build_fix_prompt(
            diagnosis_summary=diagnosis_summary,
            failure_evidence=failure_evidence,
            changed_files_context=changed_file_context,
            plan_context=plan_context,
            repair_history=repair_history,
            attempt_number=inp.attempt_number,
        )

        # Call LLM
        try:
            messages = [LLMMessage(role="user", content=prompt)]
            config = LLMConfig(
                model=self._model,
                temperature=0.2,
                max_tokens=4096,
                capability="coding",
            )
            response: LLMResponse = await provider.chat(
                messages=messages,
                config=config,
            )
            raw_response = response.content
        except Exception as exc:
            return FixAgentOutput(
                proposal=RepairProposal(
                    proposal_id=f"prop-{new_id()[:8]}",
                    status=RepairProposalStatus.INSUFFICIENT_CONTEXT,
                    diagnosis_id=inp.diagnosis.diagnosis_id,
                    attempt_number=inp.attempt_number,
                    reason=f"LLM call failed: {exc}",
                    expected_effect="",
                ),
                summary="LLM call failed — no repair generated",
                warnings=[f"LLM call failed: {exc}"],
            )

        # Parse LLM response
        proposal, warnings = self._parse_response(raw_response, inp)

        return FixAgentOutput(
            proposal=proposal,
            summary=self._build_summary(proposal),
            warnings=warnings,
        )

    # ── Context Builders ────────────────────────────────────────

    def _build_diagnosis_summary(self, inp: FixAgentInput) -> str:
        """Build a concise diagnosis summary from structured data."""
        d = inp.diagnosis
        parts = [
            f"Category: {d.category.value}",
            f"Summary: {d.summary}",
            f"Likely Cause: {d.likely_cause}",
            f"Repairability: {d.repairability.value} (confidence: {d.confidence:.2f})",
        ]

        if d.affected_files:
            parts.append(f"Affected Files: {', '.join(d.affected_files[:5])}")

        if d.affected_symbols:
            parts.append(f"Affected Symbols: {', '.join(d.affected_symbols[:5])}")

        if d.related_patch_changes:
            parts.append(f"Related Patch Changes: {', '.join(d.related_patch_changes[:5])}")

        if d.related_to_patch is not None:
            parts.append(f"Related to Patch: {d.related_to_patch}")

        return "\n".join(parts)

    def _build_failure_evidence(self, inp: FixAgentInput) -> str:
        """Build failure evidence text from test results."""
        parts = []
        for i, failure in enumerate(inp.failures):
            parts.append(f"--- Failure {i + 1} ---")
            parts.append(f"Test: {failure.test_name or 'unknown'}")
            parts.append(f"Type: {failure.failure_type.value if hasattr(failure.failure_type, 'value') else failure.failure_type}")
            if failure.file_path:
                line = f":{failure.line_number}" if failure.line_number else ""
                parts.append(f"Location: {failure.file_path}{line}")
            if failure.message:
                parts.append(f"Message: {failure.message[:1000]}")
            if failure.stack_trace:
                lines = failure.stack_trace.strip().split("\n")
                parts.append(f"Traceback (first 15 lines):")
                parts.extend(f"  {l}" for l in lines[:15])
            if failure.related_output:
                parts.append(f"Related Output: {failure.related_output[:500]}")
            parts.append("")

        if not inp.failures:
            # Fall back to process results
            for proc in (inp.test_result.process_results or []):
                if proc.status.value in ("failed", "error", "timeout"):
                    parts.append(f"Process: {proc.command}")
                    parts.append(f"Status: {proc.status.value}")
                    parts.append(f"Exit Code: {proc.exit_code}")
                    if proc.stderr:
                        parts.append(f"Stderr: {proc.stderr[:1000]}")
                    if proc.stdout:
                        parts.append(f"Stdout: {proc.stdout[:500]}")
                    parts.append("")

        return "\n".join(parts) if parts else "No detailed failure evidence available."

    def _build_plan_context(self, inp: FixAgentInput) -> str:
        """Build plan context from ImplementationPlan."""
        if not inp.plan:
            return ""

        parts = [
            f"Plan: {inp.plan.summary or 'No summary'}",
            f"Plan ID: {inp.plan.plan_id if hasattr(inp.plan, 'plan_id') else 'N/A'}",
        ]

        if inp.plan.steps:
            parts.append("\nPlan Steps:")
            for step in inp.plan.steps:
                parts.append(f"  - {step.id}: {step.title}")
                parts.append(f"    Description: {step.description}")
                if step.expected_changes:
                    parts.append(f"    Expected: {step.expected_changes}")
                if step.affected_areas:
                    parts.append(f"    Affected: {', '.join(step.affected_areas)}")

        return "\n".join(parts)

    def _build_repair_history(self, inp: FixAgentInput) -> str:
        """Build repair history summary from previous attempts."""
        if not inp.repair_history:
            return ""

        parts = [f"Previous attempts: {len(inp.repair_history)}"]
        for i, attempt in enumerate(inp.repair_history):
            status = attempt.status.value if hasattr(attempt.status, "value") else attempt.status
            parts.append(f"\nAttempt {i + 1}:")
            parts.append(f"  Status: {status}")
            if attempt.proposal:
                parts.append(f"  Reason: {attempt.proposal.reason[:200]}")
            if attempt.test_result:
                tr = attempt.test_result
                parts.append(f"  Test result: {tr.summary[:100] if tr.summary else tr.status.value}")
            parts.append("")

        return "\n".join(parts)

    # ── Response Parsing ────────────────────────────────────────

    def _parse_response(
        self, raw_response: str, inp: FixAgentInput
    ) -> Tuple[RepairProposal, List[str]]:
        """Parse the LLM response into a RepairProposal."""
        warnings: List[str] = []

        # Extract JSON
        json_str = self._extract_json(raw_response)
        if not json_str:
            return RepairProposal(
                proposal_id=f"prop-{new_id()[:8]}",
                status=RepairProposalStatus.INSUFFICIENT_CONTEXT,
                diagnosis_id=inp.diagnosis.diagnosis_id,
                attempt_number=inp.attempt_number,
                reason="Could not parse LLM response as JSON",
                expected_effect="",
            ), ["No valid JSON found in LLM response"]

        # Parse JSON
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as exc:
            return RepairProposal(
                proposal_id=f"prop-{new_id()[:8]}",
                status=RepairProposalStatus.INSUFFICIENT_CONTEXT,
                diagnosis_id=inp.diagnosis.diagnosis_id,
                attempt_number=inp.attempt_number,
                reason=f"Failed to parse LLM response: {exc}",
                expected_effect="",
            ), [f"JSON parse error: {exc}"]

        # Check status
        status_str = data.get("status", "proposed")
        if status_str in ("no_repair", "environmental", "insufficient_context"):
            status_map = {
                "no_repair": RepairProposalStatus.NO_REPAIR,
                "environmental": RepairProposalStatus.ENVIRONMENTAL,
                "insufficient_context": RepairProposalStatus.INSUFFICIENT_CONTEXT,
            }
            reason = data.get("reason", "No repair possible")
            return RepairProposal(
                proposal_id=f"prop-{new_id()[:8]}",
                status=status_map.get(status_str, RepairProposalStatus.NO_REPAIR),
                diagnosis_id=inp.diagnosis.diagnosis_id,
                attempt_number=inp.attempt_number,
                reason=reason,
                expected_effect="",
                warnings=data.get("warnings", []),
            ), []

        # Parse changes
        changes_data = data.get("changes", [])
        if not changes_data:
            return RepairProposal(
                proposal_id=f"prop-{new_id()[:8]}",
                status=RepairProposalStatus.NO_REPAIR,
                diagnosis_id=inp.diagnosis.diagnosis_id,
                attempt_number=inp.attempt_number,
                reason="No changes proposed by LLM",
                expected_effect="",
            ), ["LLM response had no changes"]

        # Parse each change
        changes: List[FileChange] = []
        parse_warnings: List[str] = []

        for i, change_data in enumerate(changes_data):
            change, cwarnings = self._parse_change(change_data, i + 1)
            if change:
                changes.append(change)
            parse_warnings.extend(cwarnings)

        if not changes:
            return RepairProposal(
                proposal_id=f"prop-{new_id()[:8]}",
                status=RepairProposalStatus.INSUFFICIENT_CONTEXT,
                diagnosis_id=inp.diagnosis.diagnosis_id,
                attempt_number=inp.attempt_number,
                reason="All proposed changes failed validation",
                expected_effect="",
                warnings=parse_warnings,
            ), parse_warnings

        warnings.extend(parse_warnings)

        # Determine file context used
        context_used = list(inp.diagnosis.affected_files)
        if inp.plan:
            context_used.append(f"plan:{inp.plan.plan_id if hasattr(inp.plan, 'plan_id') else 'unknown'}")

        return RepairProposal(
            proposal_id=f"prop-{new_id()[:8]}",
            status=RepairProposalStatus.PROPOSED,
            diagnosis_id=inp.diagnosis.diagnosis_id,
            attempt_number=inp.attempt_number,
            target_failure_ids=inp.diagnosis.failure_ids,
            patch=PatchSet(
                patch_id=f"repair-{new_id()[:8]}-{inp.attempt_number}",
                changes=changes,
                plan_id=getattr(inp.plan, "plan_id", "") if inp.plan else "",
                warnings=parse_warnings,
                metadata={"repair_attempt": inp.attempt_number},
            ),
            reason=data.get("reason", ""),
            expected_effect=data.get("expected_effect", ""),
            context_used=context_used,
            warnings=data.get("warnings", []),
        ), warnings

    def _parse_change(
        self, data: dict, index: int
    ) -> Tuple[Optional[FileChange], List[str]]:
        """Parse a single change from the LLM output."""
        warnings: List[str] = []
        operation_str = data.get("operation", "").upper().strip()

        # Map operation
        operation_map = {
            "CREATE": FileOperation.CREATE,
            "MODIFY": FileOperation.MODIFY,
        }
        operation = operation_map.get(operation_str)

        if operation is None:
            warnings.append(f"Change {index}: Unsupported operation '{operation_str}' — skipping")
            return None, warnings

        path = data.get("path", "")
        if not path:
            warnings.append(f"Change {index}: Missing path — skipping")
            return None, warnings

        new_content = data.get("new_content", "")
        if not new_content:
            warnings.append(f"Change {index}: Missing new_content — skipping")
            return None, warnings

        reason = data.get("reason", "")

        return FileChange(
            change_id=f"REPAIR-{index:03d}",
            operation=operation,
            path=path,
            new_content=new_content,
            reason=reason,
            source_context_ids=data.get("source_context_ids", []),
        ), warnings

    # ── Helpers ─────────────────────────────────────────────────

    @staticmethod
    def _get_graph_context(diagnosis: FailureDiagnosis) -> str:
        """Get semantic graph context for symbols mentioned in diagnosis."""
        try:
            from app.code_intelligence.agent_graph_helper import (
                get_graph_context_markdown,
            )
            symbols = list(diagnosis.affected_symbols or [])
            files = list(diagnosis.affected_files or [])
            if not symbols and not files:
                return ""
            return get_graph_context_markdown(
                symbol_names=symbols[:10],
                file_paths=files[:10],
                max_context=15,
            )
        except Exception:
            return ""

    @staticmethod
    def _build_summary(proposal: RepairProposal) -> str:
        """Build a human-readable summary of the proposal."""
        if proposal.status == RepairProposalStatus.PROPOSED:
            patch = proposal.patch
            if patch and patch.changes:
                changed = [f"{c.operation.value}:{c.path}" for c in patch.changes[:5]]
                return f"Repair proposed: {', '.join(changed)}"
            return "Repair proposed (no changes?)"
        return f"No repair: {proposal.status.value} — {proposal.reason[:100]}"

    @staticmethod
    def _extract_json(text: str) -> Optional[str]:
        """Extract JSON from text, handling markdown code fences and applying
        the Session-44 JSON-repair pipeline (parity with CodingAgent /
        PlannerAgent): doubled structural braces, trailing commas, unquoted
        keys, smart quotes and bare None/True/False from weaker models."""
        # Try ```json ... ``` blocks
        json_pattern = r"```(?:json)?\s*\n?(.*?)\n?```"
        matches = re.findall(json_pattern, text, re.DOTALL)
        if matches:
            candidate = matches[0].strip()
            return repair_json_text(candidate) or candidate

        # Try finding { ... }
        start_idx = text.find("{")
        if start_idx >= 0:
            depth = 0
            for i in range(start_idx, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = text[start_idx: i + 1]
                        return repair_json_text(candidate) or candidate

        # Last resort: run the full repair pipeline over the raw response.
        return repair_json_text(text)
