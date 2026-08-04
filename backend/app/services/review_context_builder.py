"""
Review Context Builder — Phase 9 deterministic service.

Builds a bounded ReviewContext from the outputs of Phases 4-8.
Prioritizes context to stay within budget limits.
Never includes secrets or sensitive data.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Set

from app.config import settings
from app.core.exceptions import ReviewContextBuildError
from app.models.coding import FileChange, PatchApplicationResult, PatchSet
from app.models.issues import ImplementationPlan, Requirement, StructuredRequirements
from app.models.profile import RepositoryProfile
from app.models.rag import RetrievedContext
from app.models.repair import RepairAttempt, RepairResult, RepairSessionStatus
from app.models.review import (
    ChangedFileSummary,
    ReviewContext,
    ReviewInput,
)
from app.models.testing import TestFailure, TestRunResult


class ReviewContextBuilder:
    """Builds a bounded ReviewContext from review input.

    Responsibilities:
    - Collect requirements and plan context
    - Collect final changed files with content
    - Collect original patch metadata
    - Collect repair history
    - Collect final test evidence
    - Build bounded context for ReviewerAgent
    - Redact secrets
    """

    def __init__(
        self,
        max_context_chars: int = 0,
        max_files: int = 0,
        max_content_per_file: int = 0,
    ):
        self._max_context_chars = max_context_chars or settings.REVIEW_MAX_CONTEXT_CHARS
        self._max_files = max_files or settings.REVIEW_MAX_FILES
        self._max_content_per_file = max_content_per_file or settings.REVIEW_MAX_CONTENT_PER_FILE

    # ── Main Entry Point ────────────────────────────────────────

    def build(self, inp: ReviewInput) -> ReviewContext:
        """Build a bounded ReviewContext from the review input.

        Args:
            inp: Structured input from Phases 4-8.

        Returns:
            Bounded ReviewContext for the ReviewerAgent.
        """
        warnings: List[str] = []

        # 1. Build requirements text
        requirements_text = self._build_requirements_text(
            inp.requirements, inp.implementation_plan
        )

        # 2. Build plan text
        plan_text = self._build_plan_text(inp.implementation_plan)

        # 3. Build changed file summaries
        changed_files, cf_warnings = self._build_changed_files(inp)
        warnings.extend(cf_warnings)

        # 4. Build changed files content
        changed_files_content = self._build_changed_files_content(
            inp.workspace_root, changed_files
        )

        # 5. Build test evidence
        test_evidence = self._build_test_evidence(inp.test_result)

        # 6. Build repair history
        repair_history = self._build_repair_history(inp.repair_result)

        # 7. Build original patch summary
        original_patch_summary = self._build_patch_summary(
            inp.original_patch, inp.patch_application
        )

        # 8. Build architecture context
        architecture_context = self._build_architecture_context(
            inp.repository_profile, inp.retrieved_context
        )

        # 9. Redact secrets from all text
        requirements_text = self._redact_secrets(requirements_text)
        changed_files_content = self._redact_secrets(changed_files_content)
        test_evidence = self._redact_secrets(test_evidence)
        repair_history = self._redact_secrets(repair_history)
        architecture_context = self._redact_secrets(architecture_context)

        # 10. Apply context budget
        changed_files_content = self._apply_budget(
            changed_files_content, "changed files"
        )

        return ReviewContext(
            requirements_text=requirements_text,
            plan_text=plan_text,
            changed_files_summaries=changed_files,
            changed_files_content=changed_files_content,
            test_evidence=test_evidence,
            repair_history=repair_history,
            original_patch_summary=original_patch_summary,
            architecture_context=architecture_context,
            warnings=warnings,
        )

    # ── Requirements ─────────────────────────────────────────

    def _build_requirements_text(
        self,
        requirements: Optional[StructuredRequirements],
        plan: Optional[ImplementationPlan],
    ) -> str:
        """Build requirements section of the context."""
        parts: List[str] = []

        if requirements:
            parts.append(f"Objective: {requirements.objective}")
            parts.append("")
            if requirements.requirements:
                parts.append(f"Requirements ({len(requirements.requirements)}):")
                for i, req in enumerate(requirements.requirements):
                    desc = req.description if hasattr(req, "description") else str(req)
                    parts.append(f"  REQ-{i+1:03d}: {desc}")
                parts.append("")
            if requirements.constraints:
                parts.append("Constraints:")
                for c in requirements.constraints:
                    desc = c.description if hasattr(c, "description") else str(c)
                    parts.append(f"  - {desc}")
                parts.append("")
            if requirements.assumptions:
                parts.append("Assumptions:")
                for a in requirements.assumptions:
                    parts.append(f"  - {a}")
                parts.append("")

        if plan and plan.requirements_coverage:
            parts.append("Requirement Coverage:")
            for req_id, step_ids in plan.requirements_coverage.items():
                parts.append(f"  {req_id} → Steps: {', '.join(step_ids)}")
            parts.append("")

        return "\n".join(parts)

    # ── Plan ──────────────────────────────────────────────────

    def _build_plan_text(
        self, plan: Optional[ImplementationPlan]
    ) -> str:
        """Build plan section of the context."""
        if not plan:
            return ""

        parts: List[str] = []
        parts.append(f"Plan Summary: {plan.summary}")
        parts.append(f"Objective: {plan.objective}")
        parts.append("")
        parts.append(f"Steps ({len(plan.steps)}):")
        for step in plan.steps:
            parts.append(f"  {step.id}: {step.title}")
            if step.description:
                parts.append(f"    Description: {step.description[:200]}")
            if step.expected_changes:
                parts.append(f"    Expected: {step.expected_changes[:200]}")
            if step.affected_areas:
                parts.append(f"    Affected: {', '.join(step.affected_areas)}")
            if step.depends_on:
                parts.append(f"    Depends on: {', '.join(step.depends_on)}")
            parts.append("")
        if plan.test_strategy:
            parts.append(f"Test Strategy: {plan.test_strategy[:200]}")
        if plan.risks:
            parts.append("Risks:")
            for r in plan.risks:
                parts.append(f"  - {r.description[:200]} ({r.likelihood}/{r.impact})")
        if plan.assumptions:
            parts.append("Assumptions:")
            for a in plan.assumptions:
                parts.append(f"  - {a}")

        return "\n".join(parts)

    # ── Changed Files ─────────────────────────────────────────

    def _build_changed_files(
        self, inp: ReviewInput
    ) -> tuple:
        """Build summaries of all changed files."""
        warnings: List[str] = []
        file_map: Dict[str, ChangedFileSummary] = {}

        # Collect from original patch
        if inp.original_patch:
            for change in inp.original_patch.changes:
                path = change.path
                op = change.operation.value if hasattr(change.operation, "value") else str(change.operation)
                if path not in file_map:
                    file_map[path] = ChangedFileSummary(
                        path=path,
                        change_type=op,
                        has_original_patch=True,
                    )

        # Collect from patch application
        if inp.patch_application:
            for f in inp.patch_application.files_created:
                if f not in file_map:
                    file_map[f] = ChangedFileSummary(path=f, change_type="create")
                file_map[f].has_original_patch = True
            for f in inp.patch_application.files_modified:
                if f not in file_map:
                    file_map[f] = ChangedFileSummary(path=f, change_type="modify")
                file_map[f].has_original_patch = True
            for f in inp.patch_application.files_deleted:
                if f not in file_map:
                    file_map[f] = ChangedFileSummary(path=f, change_type="delete")
                file_map[f].has_original_patch = True

        # Collect from repair history
        if inp.repair_result and inp.repair_result.session:
            for attempt in inp.repair_result.session.attempts:
                if attempt.proposal and attempt.proposal.patch:
                    for change in attempt.proposal.patch.changes:
                        path = change.path
                        if path not in file_map:
                            op = change.operation.value if hasattr(change.operation, "value") else str(change.operation)
                            file_map[path] = ChangedFileSummary(
                                path=path,
                                change_type=op,
                            )
                        file_map[path].repair_attempts += 1

        # Map requirements to files
        if inp.implementation_plan:
            for req_id, step_ids in inp.implementation_plan.requirements_coverage.items():
                for step in inp.implementation_plan.steps:
                    if step.id in step_ids:
                        for area in step.affected_areas:
                            for fpath, fsum in list(file_map.items()):
                                if area in fpath or area.split("/")[-1] in fpath:
                                    if req_id not in fsum.related_requirements:
                                        fsum.related_requirements.append(req_id)

        # Trim to max files
        files = list(file_map.values())
        if len(files) > self._max_files:
            warnings.append(
                f"Changed files ({len(files)}) exceeds max ({self._max_files}), "
                f"showing first {self._max_files}"
            )
            files = files[:self._max_files]

        for f in files:
            if inp.implementation_plan:
                for step in inp.implementation_plan.steps:
                    for area in step.affected_areas:
                        if area in f.path or f.path in area:
                            if f.path not in f.related_requirements:
                                pass

        return files, warnings

    def _build_changed_files_content(
        self, workspace_root: str, files: List[ChangedFileSummary]
    ) -> str:
        """Build file content for the review context."""
        parts: List[str] = []
        total_chars = 0

        for f in files:
            if total_chars >= self._max_context_chars:
                parts.append("\n... (context budget exceeded, remaining files omitted)")
                break

            # Try to read file content
            content = ""
            if workspace_root:
                full_path = Path(workspace_root) / f.path
                if full_path.exists() and full_path.is_file():
                    try:
                        content = full_path.read_text("utf-8", errors="replace")
                    except (OSError, PermissionError):
                        content = "(unreadable)"

            preview = content[:self._max_content_per_file] if content else "(no content available)"
            parts.append(f"=== {f.path} ({f.change_type}) ===")
            if preview:
                parts.append(preview)
                if len(content) > self._max_content_per_file:
                    parts.append(f"... (truncated, {len(content)} total chars)")
            parts.append("")
            total_chars += len(preview)

        return "\n".join(parts)

    # ── Test Evidence ─────────────────────────────────────────

    def _build_test_evidence(
        self, test_result: Optional[TestRunResult]
    ) -> str:
        """Build test evidence section."""
        if not test_result:
            return "No test results available."

        parts: List[str] = []
        parts.append(f"Status: {test_result.status.value if hasattr(test_result.status, 'value') else test_result.status}")
        parts.append(f"Duration: {test_result.duration_seconds:.2f}s")
        parts.append("")
        parts.append(f"Commands: {test_result.commands_total} total, "
                     f"{test_result.commands_passed} passed, "
                     f"{test_result.commands_failed} failed")
        parts.append("")
        if test_result.tests_total is not None:
            parts.append(f"Tests: {test_result.tests_total} total, "
                         f"{test_result.tests_passed or 0} passed, "
                         f"{test_result.tests_failed or 0} failed")
            parts.append("")

        # Rejected commands
        if test_result.process_results:
            rejected = [p for p in test_result.process_results if hasattr(p.status, 'value') and p.status.value == 'rejected']
            if rejected:
                parts.append(f"Rejected Commands ({len(rejected)}):")
                for r in rejected:
                    parts.append(f"  - {r.command}")
                parts.append("")

        # Failures
        if test_result.failures:
            parts.append(f"Failures ({len(test_result.failures)}):")
            for i, f in enumerate(test_result.failures[:10]):
                parts.append(f"  [{i+1}] {f.test_name or f.file_path or 'unknown'}")
                parts.append(f"       Type: {f.failure_type.value if hasattr(f.failure_type, 'value') else f.failure_type}")
                if f.file_path:
                    line = f":{f.line_number}" if f.line_number else ""
                    parts.append(f"       Location: {f.file_path}{line}")
                if f.message:
                    parts.append(f"       Message: {f.message[:200]}")
                parts.append("")
            if len(test_result.failures) > 10:
                parts.append(f"  ... and {len(test_result.failures) - 10} more failures")
                parts.append("")

        if test_result.warnings:
            parts.append("Warnings:")
            for w in test_result.warnings[:5]:
                parts.append(f"  - {w}")
            parts.append("")

        if test_result.summary:
            parts.append(f"Summary: {test_result.summary[:200]}")

        return "\n".join(parts)

    # ── Repair History ─────────────────────────────────────────

    def _build_repair_history(
        self, repair_result: Optional[RepairResult]
    ) -> str:
        """Build repair history section."""
        if not repair_result:
            return ""

        parts: List[str] = []
        parts.append(f"Repair Status: {repair_result.status.value if hasattr(repair_result.status, 'value') else repair_result.status}")
        parts.append(f"Attempts: {repair_result.attempts}")
        parts.append(f"Stop Reason: {repair_result.stop_reason}")
        parts.append(f"Summary: {repair_result.summary[:200]}")
        parts.append("")

        if repair_result.session and repair_result.session.attempts:
            for i, attempt in enumerate(repair_result.session.attempts):
                status = attempt.status.value if hasattr(attempt.status, 'value') else attempt.status
                parts.append(f"Attempt {i+1}: [{status}]")
                if attempt.proposal:
                    if attempt.proposal.reason:
                        parts.append(f"  Reason: {attempt.proposal.reason[:200]}")
                    if attempt.proposal.patch:
                        changes = [f"{c.operation.value if hasattr(c.operation, 'value') else c.operation}:{c.path}" for c in attempt.proposal.patch.changes[:3]]
                        parts.append(f"  Changes: {', '.join(changes)}")
                if attempt.test_result:
                    tr = attempt.test_result
                    parts.append(f"  Test: {tr.status.value if hasattr(tr.status, 'value') else tr.status} "
                                 f"({tr.tests_passed or 0}/{tr.tests_failed or 0})")
                if attempt.errors:
                    parts.append(f"  Errors: {'; '.join(attempt.errors[:2])}")
                parts.append("")

        if repair_result.remaining_failures:
            parts.append(f"Remaining Failures ({len(repair_result.remaining_failures)}):")
            for f in repair_result.remaining_failures[:5]:
                parts.append(f"  - {f.test_name or f.file_path or 'unknown'}: {f.failure_type.value if hasattr(f.failure_type, 'value') else f.failure_type}")
                if f.message:
                    parts.append(f"    Message: {f.message[:100]}")

        return "\n".join(parts)

    # ── Patch Summary ─────────────────────────────────────────

    def _build_patch_summary(
        self,
        patch: Optional[PatchSet],
        application: Optional[PatchApplicationResult],
    ) -> str:
        """Build original patch summary."""
        parts: List[str] = []
        if patch:
            parts.append(f"Patch ID: {patch.patch_id}")
            parts.append(f"Changes: {len(patch.changes)}")
            for c in patch.changes:
                parts.append(f"  {c.change_id}: {c.operation.value if hasattr(c.operation, 'value') else c.operation} {c.path}")
                if c.reason:
                    parts.append(f"    Reason: {c.reason[:100]}")
            parts.append("")
        if application:
            parts.append(f"Application: {application.status.value if hasattr(application.status, 'value') else application.status}")
            parts.append(f"  Created: {application.files_created}")
            parts.append(f"  Modified: {application.files_modified}")
            parts.append(f"  Deleted: {application.files_deleted}")
            parts.append("")

        return "\n".join(parts)

    # ── Architecture Context ──────────────────────────────────

    def _build_architecture_context(
        self,
        profile: Optional[RepositoryProfile],
        context: Optional[RetrievedContext],
    ) -> str:
        """Build relevant architecture context, including semantic graph."""
        parts: List[str] = []

        if profile:
            parts.append("Repository Profile:")
            if profile.languages:
                langs = [f"{l.name} ({l.percentage:.0f}%)" for l in profile.languages[:5] if l.percentage]
                if langs:
                    parts.append(f"  Languages: {', '.join(langs)}")
            if profile.technologies:
                techs = [t.name for t in profile.technologies[:8]]
                if techs:
                    parts.append(f"  Technologies: {', '.join(techs)}")
            if profile.modules:
                mods = [m.name for m in profile.modules[:5]]
                if mods:
                    parts.append(f"  Modules: {', '.join(mods)}")
            parts.append("")

        if context and context.items:
            parts.append("Retrieved Context:")
            for item in context.items[:5]:
                chunk = item.chunk
                parts.append(f"  [{item.score:.3f}] {chunk.file_path} ({chunk.symbol_name or 'N/A'})")
            parts.append("")

        # Add semantic graph context using changed file paths
        try:
            from app.code_intelligence.agent_graph_helper import (
                get_graph_context_markdown,
            )
            # Collect affected file paths from the architecture context
            changed_files = []
            if profile:
                important_files = getattr(profile, 'important_files', [])
                for f in important_files[:10]:
                    path = getattr(f, 'path', None)
                    if path:
                        changed_files.append(path)
            if changed_files:
                ctx = get_graph_context_markdown(
                    symbol_names=[],
                    file_paths=changed_files[:10],
                    max_context=15,
                )
                if ctx:
                    parts.append("\n" + ctx)
        except Exception:
            pass

        return "\n".join(parts)

    # ── Budget & Redaction ────────────────────────────────────

    def _apply_budget(self, text: str, label: str) -> str:
        """Apply character budget to text."""
        if len(text) <= self._max_context_chars:
            return text
        return text[:self._max_context_chars] + f"\n... ({label} truncated to {self._max_context_chars} chars)"

    @staticmethod
    def _redact_secrets(text: str) -> str:
        """Redact known secret patterns from text."""
        import re
        # API keys
        text = re.sub(
            r'(?i)(OPENAI_API_KEY|ANTHROPIC_API_KEY|GITHUB_TOKEN|DEVPILOT_SECRET_CANARY)\s*[=:]\s*\S+',
            r'\1=***REDACTED***',
            text,
        )
        # Random hex tokens
        text = re.sub(r'sk-[a-zA-Z0-9]{20,}', 'sk-***REDACTED***', text)
        text = re.sub(r'ghp_[a-zA-Z0-9]{36}', 'ghp_***REDACTED***', text)
        return text
