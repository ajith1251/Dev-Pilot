"""
Reviewer Agent — Phase 9

Consumes:
- ReviewContext (bounded context from ReviewContextBuilder)
- ReviewInput (original structured input)

Produces:
- AgentReview (structured findings and assessments, NON-authoritative)

Architecture:
- Two-mode design: deterministic checks (no LLM) + optional LLM-assisted review
- Uses provider-independent LLM abstraction
- NEVER modifies files, executes processes, or makes gate decisions
- Repository content and test output treated as UNTRUSTED

Fallback behavior:
- Provider unavailable → empty findings (deterministic review continues)
- Malformed response → findings with warnings
- Schema failure → findings with warnings
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
from app.models.review import (
    AgentReview,
    FindingCategory,
    FindingSeverity,
    RequirementCoverage,
    RequirementStatus,
    ReviewContext,
    ReviewFinding,
    ReviewInput,
)
from app.prompts.review import build_review_prompt


class ReviewerAgentInput:
    """Input to the Reviewer Agent."""

    def __init__(
        self,
        context: ReviewContext,
        review_input: Optional[ReviewInput] = None,
        use_llm: bool = False,
        agent_context: Optional[Any] = None,
    ):
        self.context = context
        self.review_input = review_input
        self.use_llm = use_llm
        # Phase 13: ContextEngine-produced context (replaces static graph context fallback)
        self.agent_context = agent_context


class ReviewerAgent(BaseAgent[ReviewerAgentInput, AgentReview]):
    """Reviewer Agent: evaluates implementation quality and produces structured findings.

    This agent does NOT modify files, execute processes, or make gate decisions.
    It produces structured findings that feed into the deterministic Quality Gate.

    Two modes:
    1. Deterministic-only (default, use_llm=False): No LLM required.
       Produces findings based on structured context analysis.

    2. LLM-assisted (use_llm=True): Uses LLM for semantic analysis.
       Requires a configured LLM provider.
    """

    def __init__(
        self,
        llm_provider: Optional[BaseLLMProvider] = None,
        model: Optional[str] = None,
        max_retries: int = 2,
    ):
        super().__init__(name="reviewer_agent", max_retries=max_retries)
        self._llm_provider = llm_provider
        self._model = model or settings.LLM_MODEL

    async def execute(self, inp: ReviewerAgentInput) -> AgentReview:
        """Execute the Reviewer Agent.

        Args:
            inp: Review input with context and configuration.

        Returns:
            AgentReview with structured findings and assessments.
        """
        if inp.use_llm:
            return await self._execute_with_llm(inp)
        return self._execute_deterministic(inp)

    # ── Deterministic Mode ──────────────────────────────────────

    def _execute_deterministic(self, inp: ReviewerAgentInput) -> AgentReview:
        """Execute deterministic-only review (no LLM)."""
        findings: List[ReviewFinding] = []
        assessments: List[RequirementCoverage] = []
        warnings: List[str] = inp.context.warnings or []

        # Basic structural findings from context analysis
        ctx = inp.context

        # Check if requirements were mapped to files
        if ctx.changed_files_summaries:
            unmapped = [
                f for f in ctx.changed_files_summaries
                if not f.related_requirements
            ]
            if unmapped and len(unmapped) > len(ctx.changed_files_summaries) * 0.5:
                findings.append(ReviewFinding(
                    finding_id=f"REV-DET-{new_id()[:8]}",
                    category=FindingCategory.REQUIREMENT,
                    severity=FindingSeverity.MEDIUM,
                    title="Multiple changed files lack requirement mapping",
                    description=f"{len(unmapped)} of {len(ctx.changed_files_summaries)} "
                                f"changed files have no clear requirement mapping.",
                    blocking=False,
                    confidence=0.6,
                    evidence=[f"Unmapped files: {[f.path for f in unmapped[:5]]}"],
                ))

        # Check context completeness
        if not ctx.requirements_text:
            findings.append(ReviewFinding(
                finding_id=f"REV-DET-{new_id()[:8]}",
                category=FindingCategory.REQUIREMENT,
                severity=FindingSeverity.HIGH,
                title="No requirements provided for review",
                description="The review context contains no requirements. "
                            "Cannot assess requirement coverage.",
                blocking=True,
                confidence=1.0,
            ))

        if not ctx.changed_files_content:
            findings.append(ReviewFinding(
                finding_id=f"REV-DET-{new_id()[:8]}",
                category=FindingCategory.QUALITY,
                severity=FindingSeverity.MEDIUM,
                title="No changed file content available for review",
                description="The review context contains no changed file content. "
                            "Code quality review is limited.",
                blocking=False,
                confidence=1.0,
            ))

        return AgentReview(
            findings=findings,
            requirement_assessments=assessments,
            summary="Deterministic-only review completed. "
                    f"{len(findings)} finding(s) identified.",
            warnings=warnings,
        )

    # ── LLM-Assisted Mode ───────────────────────────────────────

    async def _execute_with_llm(self, inp: ReviewerAgentInput) -> AgentReview:
        """Execute LLM-assisted review."""
        warnings: List[str] = list(inp.context.warnings or [])
        ctx = inp.context

        # Resolve LLM provider
        provider = self._llm_provider
        if provider is None:
            try:
                provider = llm_factory.get_provider()
            except Exception as exc:
                warnings.append(f"LLM provider unavailable, falling back to deterministic: {exc}")
                return self._execute_deterministic(inp)

        # Build changed files summaries text
        summaries_text = self._build_file_summaries_text(ctx.changed_files_summaries)

        # Phase 13: Include ContextEngine context if available (replaces static graph context)
        arch_context = ctx.architecture_context or ""
        if inp.agent_context is not None:
            try:
                ctx_section = inp.agent_context.build_prompt_section()
                if ctx_section:
                    arch_context += f"\n\n{ctx_section}"
            except Exception:
                pass
        else:
            # Fallback: use static graph context (Phase 12)
            graph_context = self._get_graph_context(ctx)
            if graph_context:
                arch_context += f"\n\n{graph_context}"

        # Build warnings text
        warnings_text = "\n".join(ctx.warnings) if ctx.warnings else ""

        # Build the prompt
        prompt = build_review_prompt(
            requirements_text=ctx.requirements_text,
            plan_text=ctx.plan_text,
            changed_files_content=ctx.changed_files_content,
            changed_files_summaries=summaries_text,
            test_evidence=ctx.test_evidence,
            repair_history=ctx.repair_history,
            original_patch_summary=ctx.original_patch_summary,
            architecture_context=arch_context,
            warnings=warnings_text,
        )

        # Call LLM
        try:
            messages = [LLMMessage(role="user", content=prompt)]
            config = LLMConfig(
                model=self._model,
                temperature=0.2,
                max_tokens=4096,
                capability="review",
            )
            response: LLMResponse = await provider.chat(
                messages=messages,
                config=config,
            )
            raw_response = response.content
        except Exception as exc:
            warnings.append(f"LLM call failed, falling back to deterministic: {exc}")
            return self._execute_deterministic(inp)

        # Parse LLM response
        agent_review, parse_warnings = self._parse_response(raw_response, ctx)
        warnings.extend(parse_warnings)

        agent_review.warnings = warnings
        return agent_review

    # ── Response Parsing ────────────────────────────────────────

    def _parse_response(
        self, raw_response: str, ctx: ReviewContext
    ) -> Tuple[AgentReview, List[str]]:
        """Parse the LLM response into an AgentReview."""
        warnings: List[str] = []

        # Extract JSON
        json_str = self._extract_json(raw_response)
        if not json_str:
            return AgentReview(
                findings=[],
                summary="Could not parse LLM response as JSON",
                warnings=["No valid JSON found in LLM response"],
            ), ["No JSON found"]

        # Parse JSON
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as exc:
            return AgentReview(
                findings=[],
                summary="Failed to parse LLM response",
                warnings=[f"JSON parse error: {exc}"],
            ), [f"JSON parse error: {exc}"]

        # Parse findings
        findings_data = data.get("findings", [])
        findings: List[ReviewFinding] = []
        for i, fd in enumerate(findings_data):
            finding, fwarnings = self._parse_finding(fd, i + 1)
            if finding:
                findings.append(finding)
            warnings.extend(fwarnings)

        # Parse requirement assessments
        assessments_data = data.get("requirement_assessments", [])
        assessments: List[RequirementCoverage] = []
        for ad in assessments_data:
            assessment = self._parse_assessment(ad)
            if assessment:
                assessments.append(assessment)

        # Validate findings against context
        findings = self._validate_findings(findings, ctx, warnings)

        summary = data.get("summary", "LLM-assisted review completed.")

        return AgentReview(
            findings=findings,
            requirement_assessments=assessments,
            summary=summary,
            warnings=warnings,
        ), warnings

    def _parse_finding(
        self, data: dict, index: int
    ) -> Tuple[Optional[ReviewFinding], List[str]]:
        """Parse a single finding from LLM output."""
        warnings: List[str] = []

        category_str = data.get("category", "QUALITY").upper()
        severity_str = data.get("severity", "MEDIUM").upper()
        title = data.get("title", "")
        description = data.get("description", "")

        if not title or not description:
            warnings.append(f"Finding {index}: Missing title or description — skipping")
            return None, warnings

        # Map category
        try:
            category = FindingCategory(category_str.lower())
        except ValueError:
            warnings.append(f"Finding {index}: Unknown category '{category_str}' — defaulting to QUALITY")
            category = FindingCategory.QUALITY

        # Map severity
        try:
            severity = FindingSeverity(severity_str.lower())
        except ValueError:
            warnings.append(f"Finding {index}: Unknown severity '{severity_str}' — defaulting to MEDIUM")
            severity = FindingSeverity.MEDIUM

        return ReviewFinding(
            finding_id=f"REV-LLM-{new_id()[:8]}",
            category=category,
            severity=severity,
            title=title[:200],
            description=description[:2000],
            file_path=data.get("file_path"),
            line_start=data.get("line_start"),
            line_end=data.get("line_end"),
            symbol=data.get("symbol"),
            requirement_ids=data.get("requirement_ids", []),
            plan_step_ids=data.get("plan_step_ids", []),
            evidence=data.get("evidence", []),
            recommendation=data.get("recommendation", ""),
            blocking=bool(data.get("blocking", False)),
            confidence=min(float(data.get("confidence", 0.8)), 1.0),
        ), warnings

    def _parse_assessment(self, data: dict) -> Optional[RequirementCoverage]:
        """Parse a requirement assessment from LLM output."""
        req_id = data.get("requirement_id", "")
        if not req_id:
            return None

        status_str = data.get("status", "unverified").upper()
        try:
            status = RequirementStatus(status_str.lower())
        except ValueError:
            status = RequirementStatus.UNVERIFIED

        return RequirementCoverage(
            requirement_id=req_id,
            requirement_description=data.get("description", ""),
            status=status,
            plan_steps=data.get("plan_steps", []),
            changed_files=data.get("changed_files", []),
            evidence=data.get("evidence", []),
            tests=data.get("tests", []),
            notes=data.get("notes", ""),
        )

    # ── Finding Validation ─────────────────────────────────────

    def _validate_findings(
        self,
        findings: List[ReviewFinding],
        ctx: ReviewContext,
        warnings: List[str],
    ) -> List[ReviewFinding]:
        """Validate findings against known context."""
        validated: List[ReviewFinding] = []

        # Collect known paths
        known_paths = {f.path for f in ctx.changed_files_summaries}

        for finding in findings:
            valid = True

            # Check file path
            if finding.file_path and known_paths:
                if finding.file_path not in known_paths:
                    # Check partial match
                    if not any(
                        finding.file_path.endswith(p) or p.endswith(finding.file_path)
                        for p in known_paths
                    ):
                        warnings.append(
                            f"LLM finding references unknown file '{finding.file_path}' — "
                            f"downgrading confidence"
                        )
                        finding.confidence = min(finding.confidence, 0.3)
                        finding.blocking = False

            validated.append(finding)

        return validated

    # ── Helpers ─────────────────────────────────────────────────

    @staticmethod
    def _get_graph_context(ctx: ReviewContext) -> str:
        """Get semantic graph context for symbols mentioned in review context.

        Extracts symbol names from the implementation plan and requirements,
        then queries the semantic graph for their dependencies and relationships.
        """
        try:
            from app.code_intelligence.agent_graph_helper import (
                extract_symbols_from_plan,
                get_graph_context_markdown,
            )

            # Extract symbol names from plan and requirements text
            plan_symbols = extract_symbols_from_plan(ctx.plan_text or "")
            if not plan_symbols:
                plan_symbols = extract_symbols_from_plan(ctx.requirements_text or "")

            # Extract file paths from changed file summaries
            file_paths = [
                f.path for f in (ctx.changed_files_summaries or [])[:10]
            ]

            if not plan_symbols and not file_paths:
                return ""

            return get_graph_context_markdown(
                symbol_names=plan_symbols[:10],
                file_paths=file_paths[:10],
                max_context=15,
            )
        except Exception:
            return ""

    @staticmethod
    def _build_file_summaries_text(summaries) -> str:
        """Build text representation of file summaries."""
        if not summaries:
            return "No changed files."

        parts = []
        for f in summaries:
            parts.append(f"  {f.path} ({f.change_type})")
            if f.related_requirements:
                parts.append(f"    Requirements: {', '.join(f.related_requirements)}")
            if f.repair_attempts:
                parts.append(f"    Repair attempts: {f.repair_attempts}")
        return "\n".join(parts)

    @staticmethod
    def _extract_json(text: str) -> Optional[str]:
        """Extract JSON from text, handling markdown code fences."""
        # Try ```json ... ``` blocks
        json_pattern = r"```(?:json)?\s*\n?(.*?)\n?```"
        matches = re.findall(json_pattern, text, re.DOTALL)
        if matches:
            return matches[0].strip()

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
                        return text[start_idx: i + 1]

        return None
