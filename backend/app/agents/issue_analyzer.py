"""
Issue Analyzer Agent.

Parses GitHub issues (or inline task descriptions) to extract structured
requirements, identify affected components, assess severity and priority,
and determine acceptance criteria.

This agent is the bridge between raw issue text and a structured
implementation plan (consumed by the Planner Agent).
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from app.agents.base import BaseAgent
from app.core.logging import logger
from app.llm.base import LLMConfig, LLMMessage
from app.llm.factory import factory as llm_factory
from app.models.base import Severity
from app.models.issues import (
    EstimatedEffort,
    IssueAnalysisInput,
    IssueAnalysisOutput,
    IssueType,
    Requirement,
    RequirementType,
)
from app.services.github import GitHubService


ANALYSIS_PROMPT = """You are a senior software engineer analysing a development task or GitHub issue.

Analyse the following issue and produce a structured JSON output.
Think carefully about what this issue actually requires.

## Issue
Title: {title}
Body: {body}
{repo_context}

## Output Format
Return ONLY a valid JSON object (no markdown fences, no extra text) with these fields:

```json
{{
  "summary": "One-paragraph summary of what needs to be done",
  "issue_type": "bug|feature|enhancement|documentation|performance|security|refactor|testing|question|deprecation|other",
  "severity": "critical|high|medium|low|info",
  "priority_score": <1-10 integer>,
  "affected_components": ["list", "of", "affected", "components"],
  "requirements": [
    {{
      "description": "What needs to be done",
      "requirement_type": "functional|non_functional|technical|ui_ux|test|documentation|security|performance",
      "is_implied": false,
      "acceptance_note": "How to verify this"
    }}
  ],
  "acceptance_criteria": ["list", "of", "concrete", "acceptance", "criteria"],
  "suggested_labels": ["bug", "frontend", etc],
  "estimated_effort": "trivial|small|medium|large|xlarge|uncertain",
  "related_files": ["paths", "or", "patterns", "likely", "needing", "changes"],
  "needs_more_info": false,
  "missing_info_questions": ["questions", "if", "needs_more_info", "is", "true"]
}}
```

Guidelines:
- Extract at least 3-5 concrete requirements when possible
- Be specific in acceptance criteria — they should be testable
- For bugs, include reproduction context in the summary
- If the issue is vague, set needs_more_info=true and ask specific questions
- priority_score should reflect both severity and business impact (1=lowest, 10=highest)
- related_files should suggest file patterns based on the issue description
"""


class IssueAnalyzerAgent(BaseAgent[IssueAnalysisInput, IssueAnalysisOutput]):
    """Agent that analyses GitHub issues and extracts structured requirements.

    Capabilities:
    - Fetches issues from GitHub by URL
    - Analyses inline title/body text
    - Extracts requirements, acceptance criteria, affected components
    - Assesses severity and priority
    - Estimates effort
    - Suggests labels and related files
    """

    def __init__(
        self,
        name: str = "IssueAnalyzer",
        description: str = (
            "Analyses GitHub issues and development tasks to extract "
            "structured requirements, affected components, severity, "
            "priority, and acceptance criteria"
        ),
        max_retries: int = 2,
    ) -> None:
        super().__init__(name=name, description=description, max_retries=max_retries)
        self._github = GitHubService()

    async def execute(
        self, inp: IssueAnalysisInput
    ) -> IssueAnalysisOutput:
        """Execute the issue analysis.

        Args:
            inp: Input specifying issue URL or inline title + body.

        Returns:
            Structured analysis output with requirements, components, etc.
        """
        # ── Resolve issue content ──────────────────────────────────
        title, body, repo_context = await self._resolve_issue(inp)
        if title is None:
            return IssueAnalysisOutput(
                title=inp.title or inp.issue_url or "",
                summary=body or "No issue content provided",
                error=body or "No issue content provided",  # body holds error msg
            )

        logger.info(
            "Analysing issue: \"%s\" (url=%s)",
            title[:80],
            inp.issue_url or "inline",
        )

        # ── LLM Analysis ──────────────────────────────────────────
        try:
            result = await self._analyse_with_llm(
                title=title,
                body=body,
                repo_context=repo_context,
            )
            return result

        except Exception as exc:
            logger.error("Issue analysis failed: %s", exc)
            return IssueAnalysisOutput(
                title=title,
                summary=f"Analysis failed: {exc}",
                error=f"Issue analysis failed: {exc}",
            )

    # ── Private helpers ───────────────────────────────────────────

    async def _resolve_issue(
        self, inp: IssueAnalysisInput
    ) -> tuple[Optional[str], str, str]:
        """Resolve the issue content from URL or inline fields.

        Returns:
            Tuple of (title, body, repo_context) or (None, error_msg, "").
        """
        # Case 1: issue_url provided → fetch from GitHub
        if inp.issue_url:
            try:
                owner, repo, number = GitHubService.parse_issue_url(inp.issue_url)
            except ValueError as exc:
                return None, str(exc), ""

            try:
                issue = await self._github.get_issue(owner, repo, number)
                return issue["title"], issue["body"], inp.repo_context or ""
            except Exception as exc:
                return None, f"Failed to fetch issue: {exc}", ""

        # Case 2: inline title + body
        if inp.title:
            return inp.title, inp.body or "", inp.repo_context or ""

        # Case 3: nothing provided
        return None, "Either issue_url or title must be provided", ""

    async def _analyse_with_llm(
        self,
        title: str,
        body: str,
        repo_context: str = "",
    ) -> IssueAnalysisOutput:
        """Use the LLM to analyse the issue and extract structured data."""
        provider = llm_factory.get_provider()

        ctx_section = ""
        if repo_context:
            ctx_section = (
                f"\n## Repository Context\n{repo_context[:1500]}"
            )

        prompt = ANALYSIS_PROMPT.format(
            title=title,
            body=body or "(no description provided)",
            repo_context=ctx_section,
        )

        messages = [
            LLMMessage(
                role="system",
                content=(
                    "You are a senior software engineer analysing development "
                    "tasks. You think step by step and produce precise, "
                    "structured analysis."
                ),
            ),
            LLMMessage(role="user", content=prompt),
        ]

        response = await provider.chat(
            messages,
            config=LLMConfig(temperature=0.1, max_tokens=2048),
        )

        raw = response.content.strip()
        parsed = self._parse_json_response(raw)

        return self._build_output(title, parsed)

    def _parse_json_response(self, text: str) -> Dict[str, Any]:
        """Parse JSON from LLM response, handling markdown fences and nested braces."""
        cleaned = text.strip()

        # Remove markdown code fences
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
            cleaned = re.sub(r"\n?\s*```\s*$", "", cleaned)
        cleaned = cleaned.strip()

        # Strategy 1: Try direct JSON parsing first (fast path)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # Strategy 2: Extract JSON object with balanced braces
        # Find the first '{' and count braces to find the matching '}'
        start = cleaned.find("{")
        if start == -1:
            logger.warning("No JSON object found in LLM response")
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
            logger.warning("Unbalanced braces in LLM response")
            return {}

        json_str = cleaned[start:end]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            logger.warning("Failed to parse extracted JSON from LLM response")
            return {}

    def _build_output(
        self, title: str, data: Dict[str, Any]
    ) -> IssueAnalysisOutput:
        """Build a validated IssueAnalysisOutput from parsed data."""
        # ── Parse issue_type ────────────────────────────────────────
        issue_type_str = data.get("issue_type", "other")
        try:
            issue_type = IssueType(issue_type_str)
        except ValueError:
            issue_type = IssueType.OTHER

        # ── Parse severity ──────────────────────────────────────────
        severity_str = data.get("severity", "medium")
        try:
            severity = Severity(severity_str)
        except ValueError:
            severity = Severity.MEDIUM

        # ── Parse priority_score ────────────────────────────────────
        priority_score = data.get("priority_score", 5)
        if not isinstance(priority_score, int) or priority_score < 1 or priority_score > 10:
            priority_score = 5

        # ── Parse estimated_effort ──────────────────────────────────
        effort_str = data.get("estimated_effort", "uncertain")
        try:
            effort = EstimatedEffort(effort_str)
        except ValueError:
            effort = EstimatedEffort.UNCERTAIN

        # ── Parse requirements ──────────────────────────────────────
        requirements_raw = data.get("requirements", [])
        requirements: list[Requirement] = []
        for req in requirements_raw:
            if isinstance(req, dict) and "description" in req:
                req_type_str = req.get("requirement_type", "functional")
                try:
                    req_type = RequirementType(req_type_str)
                except ValueError:
                    req_type = RequirementType.FUNCTIONAL

                requirements.append(
                    Requirement(
                        description=req["description"],
                        requirement_type=req_type,
                        is_implied=req.get("is_implied", False),
                        acceptance_note=req.get("acceptance_note"),
                    )
                )

        # ── Parse acceptance_criteria ───────────────────────────────
        acceptance_criteria: list[str] = [
            str(c) for c in data.get("acceptance_criteria", [])
            if isinstance(c, str) and c.strip()
        ]

        # ── Parse affected_components ───────────────────────────────
        affected_components: list[str] = [
            str(c) for c in data.get("affected_components", [])
            if isinstance(c, str) and c.strip()
        ]

        # ── Parse suggested_labels ──────────────────────────────────
        suggested_labels: list[str] = [
            str(l) for l in data.get("suggested_labels", [])
            if isinstance(l, str) and l.strip()
        ]

        # ── Parse related_files ─────────────────────────────────────
        related_files: list[str] = [
            str(f) for f in data.get("related_files", [])
            if isinstance(f, str) and f.strip()
        ]

        # ── Parse needs_more_info ───────────────────────────────────
        needs_more_info = bool(data.get("needs_more_info", False))

        # ── Parse missing_info_questions ────────────────────────────
        missing_info_questions: list[str] = [
            str(q) for q in data.get("missing_info_questions", [])
            if isinstance(q, str) and q.strip()
        ]

        return IssueAnalysisOutput(
            title=title,
            summary=data.get("summary", ""),
            issue_type=issue_type,
            severity=severity,
            priority_score=priority_score,
            affected_components=affected_components,
            requirements=requirements,
            acceptance_criteria=acceptance_criteria,
            suggested_labels=suggested_labels,
            estimated_effort=effort,
            related_files=related_files,
            needs_more_info=needs_more_info,
            missing_info_questions=missing_info_questions,
        )
