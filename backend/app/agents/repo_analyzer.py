"""
Repository Analyzer Agent.

Analyses a GitHub repository to understand its structure, detect
languages and frameworks, identify important files, and build
useful code context for downstream agents (Planner, Coding, etc.).
"""

from __future__ import annotations

from typing import List, Optional

from app.agents.base import BaseAgent
from app.core.logging import logger
from app.llm.base import LLMConfig, LLMMessage
from app.llm.factory import factory as llm_factory
from app.models.analysis import (
    FileCategory,
    FileInfo,
    FrameworkInfo,
    LanguageInfo,
    RepositoryAnalysisInput,
    RepositoryAnalysisOutput,
    RepositoryStructure,
    RepositorySummary,
)
from app.models.analysis import DirectoryNode
from app.services.github import GitHubService
from app.tools.analyzer import (
    build_repo_structure,
    collect_files,
    detect_dependencies,
    detect_frameworks,
    detect_languages,
    find_entry_points,
    find_files_by_category,
)


SUMMARIZE_PROMPT = """You are an expert software engineer reviewing a repository analysis.

Based on the following repository analysis data, provide a high-level summary.

Repository: {repo_name}
Default branch: {branch}

## Languages
{languages}

## Frameworks / Tools Detected
{frameworks}

## Entry Points
{entry_points}

## Configuration Files
{config_files}

## Structure Overview
{depth} directories, {total_files} files, max depth of {max_depth_levels}

Provide a concise summary covering:
1. What this project appears to do (purpose)
2. Its technology stack
3. Any notable architectural patterns
4. A brief overall description

Keep the response concise and technical. Focus on what you can infer from the data.
"""


def _build_empty_tree(repo_name: str) -> DirectoryNode:
    """Return a minimal empty tree node when analysis cannot proceed."""
    return DirectoryNode(path="", name=repo_name)


class RepositoryAnalyzerAgent(BaseAgent[RepositoryAnalysisInput, RepositoryAnalysisOutput]):
    """Agent that analyses a GitHub repository.

    Capabilities:
    - Fetches repository metadata via GitHub API
    - Builds a recursive directory tree
    - Detects programming languages by file extension
    - Detects frameworks and tools from config files
    - Identifies entry points, config files, test files
    - Generates a high-level summary using LLM (optional)
    """

    def __init__(
        self,
        name: str = "RepositoryAnalyzer",
        description: str = "Analyses GitHub repositories to understand structure, languages, frameworks, and key files",
        max_retries: int = 2,
    ) -> None:
        super().__init__(name=name, description=description, max_retries=max_retries)
        self._github = GitHubService()

    async def execute(
        self, inp: RepositoryAnalysisInput
    ) -> RepositoryAnalysisOutput:
        """Execute the repository analysis.

        Args:
            inp: Input specifying repo URL, branch, and analysis options.

        Returns:
            Structured analysis output with tree, languages, frameworks, etc.
        """
        # ── Parse the repository URL ─────────────────────────────────
        try:
            owner, repo_name = GitHubService.parse_repo_url(inp.repo_url)
        except ValueError as exc:
            # repo_name is NOT defined here because tuple unpacking failed
            fallback_name = inp.repo_url.split("/")[-1] or inp.repo_url
            return RepositoryAnalysisOutput(
                repo_name=inp.repo_url,
                error=str(exc),
                structure=RepositoryStructure(
                    tree=_build_empty_tree(fallback_name),
                ),
            )

        full_name = f"{owner}/{repo_name}"
        logger.info(
            "Analyzing repository %s (branch=%s, depth=%d)",
            full_name,
            inp.branch or "default",
            inp.max_depth,
        )

        # ── Fetch repo metadata ──────────────────────────────────────
        try:
            repo_info = await self._github.get_repo_info(owner, repo_name)
            default_branch = inp.branch or repo_info.get("default_branch", "main")
        except Exception as exc:
            logger.error("Failed to fetch repo info for %s: %s", full_name, exc)
            return RepositoryAnalysisOutput(
                repo_name=full_name,
                error=f"Failed to fetch repository: {exc}",
                structure=RepositoryStructure(
                    tree=_build_empty_tree(repo_name),
                ),
            )

        # ── Build directory structure ────────────────────────────────
        try:
            structure = await build_repo_structure(
                owner=owner,
                repo=repo_name,
                branch=default_branch,
                max_depth=inp.max_depth,
                github=self._github,
            )
        except Exception as exc:
            logger.error("Failed to build structure for %s: %s", full_name, exc)
            return RepositoryAnalysisOutput(
                repo_name=full_name,
                default_branch=default_branch,
                error=f"Failed to build directory structure: {exc}",
                structure=RepositoryStructure(
                    tree=_build_empty_tree(repo_name),
                ),
            )

        # ── Flatten file list for analysis ───────────────────────────
        all_files = collect_files(structure.tree)

        # ── Detect languages ─────────────────────────────────────────
        languages = detect_languages(all_files)

        # ── Detect dependencies ──────────────────────────────────────
        dependencies = detect_dependencies(all_files)

        # ── Detect frameworks ────────────────────────────────────────
        frameworks = detect_frameworks(all_files, dependencies)

        # ── Find important file categories ───────────────────────────
        entry_points = find_entry_points(all_files)

        config_files = find_files_by_category(all_files, FileCategory.CONFIG)
        test_files = find_files_by_category(all_files, FileCategory.TEST)

        # ── Optional LLM summary ─────────────────────────────────────
        summary: Optional[RepositorySummary] = None
        if inp.include_llm_summary:
            summary = await self._generate_summary(
                repo_name=full_name,
                branch=default_branch,
                languages=languages,
                frameworks=frameworks,
                entry_points=entry_points,
                config_files=config_files,
                structure=structure,
            )

        return RepositoryAnalysisOutput(
            repo_name=full_name,
            default_branch=default_branch,
            structure=structure,
            languages=languages,
            frameworks=frameworks,
            entry_points=entry_points,
            config_files=config_files,
            test_files=test_files,
            dependencies=dependencies,
            summary=summary,
        )

    async def _generate_summary(
        self,
        repo_name: str,
        branch: str,
        languages: List[LanguageInfo],
        frameworks: List[FrameworkInfo],
        entry_points: List[FileInfo],
        config_files: List[FileInfo],
        structure: RepositoryStructure,
    ) -> Optional[RepositorySummary]:
        """Use the configured LLM to produce a high-level summary.

        Falls back gracefully if the LLM call fails.
        """
        try:
            provider = llm_factory.get_provider()
        except Exception as exc:
            logger.warning("No LLM provider available for summary: %s", exc)
            return None

        lang_text = "\n".join(
            f"  - {l.name}: {l.file_count} files" for l in languages[:10]
        ) or "  (none detected)"

        fw_text = "\n".join(
            f"  - {f.name} (confidence: {f.confidence:.0%})" for f in frameworks[:10]
        ) or "  (none detected)"

        ep_text = "\n".join(
            f"  - {f.path}" for f in entry_points[:10]
        ) or "  (none found)"

        cfg_text = "\n".join(
            f"  - {f.path}" for f in config_files[:15]
        ) or "  (none found)"

        prompt = SUMMARIZE_PROMPT.format(
            repo_name=repo_name,
            branch=branch,
            languages=lang_text,
            frameworks=fw_text,
            entry_points=ep_text,
            config_files=cfg_text,
            total_files=structure.total_files,
            depth=structure.total_dirs,
            max_depth_levels=structure.depth,
        )

        messages = [
            LLMMessage(
                role="system",
                content="You are a technical architect summarising a repository analysis.",
            ),
            LLMMessage(role="user", content=prompt),
        ]

        try:
            response = await provider.chat(
                messages,
                config=LLMConfig(temperature=0.2, max_tokens=1024),
            )

            content = response.content.strip()
            # Parse the response into structured fields
            lines = content.split("\n")
            purpose = ""
            tech_stack = ""
            architecture = ""
            description = content  # fallback

            current_section = ""
            for line in lines:
                line_lower = line.strip().lower()
                if "purpose" in line_lower or "this project" in line_lower or "project appear" in line_lower:
                    current_section = "purpose"
                    continue
                elif "technology" in line_lower or "tech stack" in line_lower:
                    current_section = "tech"
                    continue
                elif "architecture" in line_lower or "architectural" in line_lower:
                    current_section = "architecture"
                    continue
                elif "description" in line_lower:
                    current_section = "description"
                    continue

                if current_section == "purpose":
                    purpose += line.strip() + " "
                elif current_section == "tech":
                    tech_stack += line.strip() + " "
                elif current_section == "architecture":
                    architecture += line.strip() + " "

            return RepositorySummary(
                description=description[:500],
                purpose=purpose.strip()[:300] or "See description",
                tech_stack_summary=tech_stack.strip()[:300] or "See description",
                architecture_notes=architecture.strip()[:300] or "No specific architectural notes detected",
            )

        except Exception as exc:
            logger.warning("LLM summary generation failed: %s", exc)
            return None
