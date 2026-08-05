"""
Coding Agent — Phase 6

Consumes:
- Validated ImplementationPlan (Phase 4)
- Plan-aware RetrievedContext (Phase 5)
- Workspace metadata

Produces:
- Structured PatchSet (proposed changes)

Architecture:
- Uses provider-independent LLM abstraction
- Never directly writes files
- Output is validated by PatchValidator
- Repository content treated as UNTRUSTED
"""

import json
import re
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from app.agents.base import BaseAgent
from app.core.exceptions import (
    CodingError,
    CodingOutputValidationError,
    InsufficientContextError,
)
from app.llm.base import BaseLLMProvider, LLMConfig, LLMMessage, LLMResponse
from app.llm.factory import factory as llm_factory

LLMProvider = BaseLLMProvider  # Backward compat alias
from app.models.coding import (
    CodingAgentInput,
    CodingAgentOutput,
    FileChange,
    FileOperation,
    PatchSet,
    PatchStatus,
)
from app.models.issues import ImplementationPlan, StructuredRequirements
from app.models.rag import (
    RetrievedContext,
)
from app.prompts.coding import build_coding_prompt


class CodingAgent(BaseAgent[CodingAgentInput, CodingAgentOutput]):
    """Coding Agent: generates structured PatchSets from plans and context.

    This agent does NOT write files. It produces structured proposals
    that are validated by PatchValidator and applied by SafePatchEngine.
    """

    def __init__(
        self,
        llm_provider: Optional[LLMProvider] = None,
        model: Optional[str] = None,
        max_retries: int = 3,
    ):
        super().__init__(
            name="coding_agent",
            max_retries=max_retries,
        )
        self._llm_provider = llm_provider
        self._model = model or "gpt-4o-mini"

    def _get_provider(self) -> BaseLLMProvider:
        """Resolve the LLM provider, defaulting to the configured factory provider."""
        if self._llm_provider is None:
            self._llm_provider = llm_factory.get_provider()
        return self._llm_provider

    async def execute(self, inp: CodingAgentInput) -> CodingAgentOutput:
        """Execute the Coding Agent with a structured input."""
        try:
            patch_set = await self.generate_patch(
                plan=inp.plan,
                retrieved_context=inp.retrieved_context or RetrievedContext(
                    query=inp.plan.summary, snapshot_id="", items=[], total_candidates=0
                ),
                requirements=inp.requirements,
                workspace_structure=inp.workspace_structure,
                agent_context=inp.agent_context,
            )
            return CodingAgentOutput(patch_set=patch_set, status="success")
        except InsufficientContextError as exc:
            return CodingAgentOutput(
                status="insufficient_context",
                missing_context=exc.details.get("missing_context", []),
                warnings=exc.details.get("warnings", []),
            )
        except CodingError as exc:
            return CodingAgentOutput(status="error", error=str(exc))

    async def generate_patch(
        self,
        plan: ImplementationPlan,
        retrieved_context: RetrievedContext,
        requirements: StructuredRequirements,
        workspace_structure: str = "",
        agent_context: Optional[Any] = None,
    ) -> PatchSet:
        """Generate a PatchSet from a validated plan and retrieved context.

        This is the primary entry point. It:
        1. Builds the coding prompt with trust boundaries
        2. Calls the LLM
        3. Parses and validates the structured output
        4. Returns a PatchSet (or raises on failure)

        Args:
            plan: Validated implementation plan.
            retrieved_context: Phase 5 RAG context.
            requirements: Structured requirements.
            workspace_structure: Optional workspace layout summary.
            agent_context: Phase 13 AgentContext from ContextEngine.
                If provided, replaces the static graph context fallback.
        """
        # Build plan context string
        plan_context = self._format_plan(plan, requirements)

        # Build retrieved context string
        context_str = self._format_retrieved_context(retrieved_context)

        # Phase 13: Include ContextEngine context if available (replaces static graph context)
        if agent_context is not None:
            ctx_section = agent_context.build_prompt_section()
            extra_context = ctx_section if ctx_section else ""
        else:
            # Fallback: use static graph context (Phase 12)
            extra_context = self._get_graph_context(plan, retrieved_context)

        prompt = build_coding_prompt(
            plan_context=plan_context,
            retrieved_context=context_str + extra_context,
            workspace_structure=workspace_structure,
        )

        # Call LLM using the provider-independent interface
        messages = [LLMMessage(role="user", content=prompt)]
        config = LLMConfig(model=self._model, temperature=0.3, max_tokens=8192, capability="coding")
        response: LLMResponse = await self._get_provider().chat(
            messages=messages,
            config=config,
        )
        raw_response = response.content

        # Parse and validate
        patch_set = self._parse_response(raw_response, plan, requirements)

        return patch_set

    async def generate_patch_for_step(
        self,
        step_title: str,
        chunks: List = None,
        plan: ImplementationPlan = None,
        requirements: StructuredRequirements = None,
    ) -> PatchSet:
        """Generate a PatchSet for a single implementation step.

        This enables step-by-step patch generation where each step
        builds on the previous workspace state.
        Enhanced in Phase 7+ with proper step context.
        """
        from app.models.rag import RetrievedContext, RetrievedContextItem, CodeChunk

        items = []
        if chunks:
            for c in chunks[:10]:
                chunk = CodeChunk(chunk_id=f"chunk-{id(c)}", file_path="", language="", content=str(c)[:1000], start_line=0, end_line=0, content_hash="")
                items.append(RetrievedContextItem(chunk=chunk, score=0.5))

        ctx = RetrievedContext(
            query=step_title,
            snapshot_id="",
            items=items,
            total_candidates=len(items),
        )

        return await self.generate_patch(
            plan=plan,
            retrieved_context=ctx,
            requirements=requirements,
        )

    def _format_plan(self, plan: ImplementationPlan, requirements: Optional[StructuredRequirements]) -> str:
        """Format plan and requirements for the prompt."""
        lines = []
        if requirements is not None:
            lines.append(f"Objective: {requirements.objective}")
            if requirements.requirements:
                lines.append("Requirements:")
                for i, req in enumerate(requirements.requirements, 1):
                    description = req.description if hasattr(req, 'description') else str(req)
                    lines.append(f"  REQ-{i:03d}: {description}")
                lines.append("")
        else:
            lines.append(f"Objective: {plan.summary}")
        lines.append(f"Plan summary: {plan.summary}")
        lines.append("")

        lines.append("Implementation Plan:")
        for step in plan.steps:
            lines.append(f"  {step.id}: {step.title}")
            lines.append(f"    Description: {step.description}")
            if step.expected_changes:
                lines.append(f"    Expected changes: {step.expected_changes}")
            if step.affected_areas:
                lines.append(f"    Affected areas: {', '.join(step.affected_areas)}")
            lines.append("")

        return "\n".join(lines)

    def _format_retrieved_context(self, context: RetrievedContext) -> str:
        """Format retrieved context for the prompt."""
        lines = []
        lines.append(f"Query: {context.query}")
        lines.append(f"Total candidates: {context.total_candidates}")
        lines.append("")

        for i, item in enumerate(context.items[:30]):  # Limit to top 30
            chunk = item.chunk
            lines.append(f"[Result {i+1}] (score: {item.score:.4f})")
            lines.append(f"  File: {chunk.file_path}")
            lines.append(f"  Symbol: {chunk.symbol_name or 'N/A'} ({chunk.symbol_kind or 'N/A'})")
            lines.append(f"  Lines: {chunk.start_line}-{chunk.end_line}")
            lines.append(f"  Language: {chunk.language}")
            if item.reasons:
                lines.append(f"  Context: {'; '.join(item.reasons[:3])}")
            lines.append("")
            lines.append("  ```" + chunk.language.lower() if chunk.language else "  ```")
            lines.append(chunk.content[:2000])  # Limit content per chunk
            lines.append("  ```")
            lines.append("")

        return "\n".join(lines)

    def _parse_response(
        self,
        raw_response: str,
        plan: ImplementationPlan,
        requirements: StructuredRequirements,
    ) -> PatchSet:
        """Parse and validate the LLM response into a PatchSet.

        Handles:
        - Markdown code fences
        - Malformed JSON
        - Missing fields
        - Insufficient context responses
        """
        # Try to extract JSON from markdown code fences
        json_str = self._extract_json(raw_response)

        if not json_str:
            raise CodingOutputValidationError(
                "No valid JSON found in LLM response",
                details={"raw_response_preview": raw_response[:500]},
            )

        # Parse JSON — tolerate concatenated objects: when the extracted
        # span fails, fall back to the LAST {..} span (the actual payload
        # usually follows any preamble/notes text).
        data = self._load_json_with_fallback(json_str, raw_response)

        # Check for insufficient context
        if data.get("status") == "INSUFFICIENT_CONTEXT":
            missing = data.get("missing_context", [])
            warnings = data.get("warnings", [])
            raise InsufficientContextError(
                "Coding Agent reported insufficient context",
                details={
                    "missing_context": missing,
                    "warnings": warnings,
                },
            )

        # Parse changes
        changes_data = data.get("changes", [])
        if not changes_data:
            raise CodingOutputValidationError(
                "No changes found in LLM output",
                details={"data_keys": list(data.keys())},
            )

        changes: List[FileChange] = []
        for i, change_data in enumerate(changes_data):
            try:
                change = self._parse_change(change_data, i)
                changes.append(change)
            except (ValidationError, ValueError) as exc:
                raise CodingOutputValidationError(
                    f"Invalid change at index {i}: {exc}",
                    details={"change_data": change_data},
                )

        warnings = data.get("warnings", [])

        return PatchSet(
            patch_id=f"patch-{plan.plan_id if hasattr(plan, 'plan_id') else 'generated'}-{len(changes)}",
            plan_id=getattr(plan, "plan_id", ""),
            changes=changes,
            warnings=warnings,
            metadata={
                "step_count": len(plan.steps),
                "change_count": len(changes),
            },
        )

    def _parse_change(self, data: dict, index: int) -> FileChange:
        """Parse a single change from the LLM output."""
        operation_str = data.get("operation", "").upper()

        operation_map = {
            "CREATE": FileOperation.CREATE,
            "MODIFY": FileOperation.MODIFY,
            "DELETE": FileOperation.DELETE,
        }

        operation = operation_map.get(operation_str)
        if operation is None:
            raise ValueError(f"Unsupported operation: {operation_str}")

        return FileChange(
            change_id=data.get("change_id", f"CHANGE-{index+1:03d}"),
            operation=operation,
            path=data.get("path", ""),
            original_hash=data.get("original_hash"),
            new_content=data.get("new_content"),
            reason=data.get("reason", ""),
            plan_step_id=data.get("plan_step_id"),
            requirement_ids=data.get("requirement_ids", []),
            source_context_ids=data.get("source_context_ids", []),
        )

    @staticmethod
    def _get_graph_context(
        plan: ImplementationPlan,
        retrieved_context: RetrievedContext,
    ) -> str:
        """Get semantic graph context for coding.

        Extracts symbol names from plan and retrieved context,
        then retrieves graph neighborhood for each symbol.
        """
        try:
            from app.code_intelligence.agent_graph_helper import (
                extract_symbols_from_changed_files,
                extract_symbols_from_plan,
                get_graph_context_markdown,
            )

            # Collect symbol names from plan and context
            plan_text = plan.summary + " " + " ".join(s.title for s in plan.steps)
            symbol_names = extract_symbols_from_plan(plan_text)

            file_paths = list(set(
                item.chunk.file_path for item in retrieved_context.items[:20]
                if item.chunk.file_path
            ))

            if not symbol_names and not file_paths:
                return ""

            return get_graph_context_markdown(
                symbol_names=symbol_names[:10],
                file_paths=file_paths[:10],
                max_context=15,
            )
        except Exception:
            return ""

    @staticmethod
    def _load_json_with_fallback(primary: str, raw: str) -> dict:
        """json.loads(primary), falling back to later {..} spans on failure.

        Gemini sometimes returns two objects (e.g. a note then the payload);
        the first-{/last-} extractor spans both, so the first object's '}'
        lands mid-JSON ("Expecting ',' delimiter"). Try every '{' start
        through the last '}' until one candidate parses — the payload object
        is whichever span is valid JSON.
        """
        candidates = [primary]
        idx = 0
        while True:
            idx = raw.find("{", idx)
            if idx < 0:
                break
            end = raw.rfind("}")
            if end > idx:
                cand = raw[idx : end + 1]
                if cand != primary:
                    candidates.append(cand)
            idx += 1
        last_err: Optional[Exception] = None
        for candidate in candidates:
            try:
                data = json.loads(candidate)
                if isinstance(data, dict):
                    return data
            except (json.JSONDecodeError, ValueError) as exc:
                last_err = exc
        raise CodingOutputValidationError(
            f"Failed to parse LLM output as JSON: {last_err}",
            details={"raw_json": primary[:500]},
        )

    def _extract_json(self, text: str) -> Optional[str]:
        """Extract JSON from text, handling markdown code fences.

        The bare-object fallback takes the first '{' through the LAST '}'.
        A naive brace-depth counter (the previous implementation) miscounts
        braces INSIDE string values — e.g. a PatchSet's new_content holding
        real code with { } — truncating the JSON and failing json.loads.
        """
        # Try extracting from ```json ... ``` blocks
        json_pattern = r"```(?:json)?\s*\n?(.*?)\n?```"
        matches = re.findall(json_pattern, text, re.DOTALL)
        if matches:
            return matches[0].strip()

        # Bare object: first '{' to last '}' (parser tolerates braces in strings)
        start_idx = text.find("{")
        end_idx = text.rfind("}")
        if start_idx >= 0 and end_idx > start_idx:
            return text[start_idx : end_idx + 1]

        # Bare array fallback
        start_idx = text.find("[")
        end_idx = text.rfind("]")
        if start_idx >= 0 and end_idx > start_idx:
            return text[start_idx : end_idx + 1]

        return None



