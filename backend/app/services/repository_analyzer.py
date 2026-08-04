"""
Repository Analyzer — high-level orchestrator for the Repository Intelligence Engine.

Coordinates all detectors to produce a RepositoryProfile from a local path.
Keeps individual detectors modular — no 1,000-line file.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Set

from app.core.logging import logger
from app.models.profile import (
    RepositoryCommand,
    RepositoryProfile,
    ScanMetadata,
)
from app.services.command_detector import CommandDetector
from app.services.dependency_analyzer import DependencyAnalyzer
from app.services.file_classifier import FileClassifier
from app.services.important_file_detector import ImportantFileDetector
from app.services.language_detector import LanguageDetector
from app.services.project_detector import ProjectDetector
from app.services.repository_scanner import RepositoryScanner, ScannedFile
from app.services.technology_detector import TechnologyDetector
from app.services.tree_generator import TreeGenerator


class RepositoryAnalyzer:
    """High-level orchestrator for repository analysis.

    Usage:
        analyzer = RepositoryAnalyzer()
        profile = analyzer.analyze("/path/to/repo")
    """

    def __init__(
        self,
        max_depth: int = 50,
        max_files: int = 100_000,
        max_file_size: int = 10 * 1024 * 1024,
        extra_ignored_dirs: Optional[Set[str]] = None,
    ) -> None:
        self.scanner = RepositoryScanner(
            max_depth=max_depth,
            max_files=max_files,
            max_file_size=max_file_size,
            extra_ignored_dirs=extra_ignored_dirs,
        )
        self.language_detector = LanguageDetector()
        self.technology_detector = None  # Initialized per-path
        self.dependency_analyzer = None
        self.command_detector = None
        self.file_classifier = FileClassifier()
        self.project_detector = None
        self.tree_generator = TreeGenerator()
        self.important_file_detector = ImportantFileDetector()

    def analyze(self, repo_path: str) -> RepositoryProfile:
        """Analyze a local repository and produce a RepositoryProfile.

        Args:
            repo_path: Absolute or relative path to a local repository.

        Returns:
            RepositoryProfile with all detected information.
        """
        start_time = time.time()
        path = Path(repo_path).resolve()

        if not path.is_dir():
            return RepositoryProfile(
                name=path.name,
                scan=ScanMetadata(
                    root_path=str(path),
                    errors=[f"Path is not a directory: {repo_path}"],
                ),
            )

        logger.info("Repository analysis started: %s", path)

        # ── Step 1: Scan ──────────────────────────────────────────
        scan_result = self.scanner.scan(str(path))
        files = scan_result.files
        file_names: Set[str] = {f.name for f in files}
        file_paths: Set[str] = {f.path for f in files}

        # ── Step 2: Build file maps ───────────────────────────────
        file_map = self._build_file_map(files, str(path))

        # ── Step 3: Classify files ────────────────────────────────
        file_categories = self.file_classifier.classify(files)

        # ── Step 4: Detect languages ──────────────────────────────
        languages = self.language_detector.detect(files)

        # ── Step 5: Detect technologies ──────────────────────────
        tech_detector = TechnologyDetector(str(path))
        technologies = tech_detector.detect(file_names, file_paths, file_map)

        # ── Step 6: Analyze dependencies ──────────────────────────
        dep_analyzer = DependencyAnalyzer(str(path))
        dependencies, package_managers = dep_analyzer.analyze(file_map)

        # ── Step 7: Discover commands ────────────────────────────
        cmd_detector = CommandDetector(str(path))
        commands = cmd_detector.detect(file_map)

        # ── Step 8: Detect modules ────────────────────────────────
        proj_detector = ProjectDetector(str(path))
        modules = proj_detector.detect(file_paths, file_map, commands)

        # ── Step 9: Identify important files ──────────────────────
        important_files = self.important_file_detector.detect(files, file_names)

        # ── Step 10: Generate tree ────────────────────────────────
        tree = self.tree_generator.generate(files, root_name=path.name)

        # ── Step 11: Build profile ───────────────────────────────
        duration = round(time.time() - start_time, 3)

        scan_metadata = ScanMetadata(
            duration_seconds=duration,
            root_path=str(path),
            total_files_scanned=scan_result.total_files,
            total_dirs_scanned=scan_result.total_dirs,
            total_files_ignored=scan_result.total_ignored + scan_result.total_ignored_dirs,
            total_bytes=scan_result.total_bytes,
            max_depth_reached=scan_result.max_depth,
            errors=scan_result.errors,
            warnings=scan_result.warnings,
        )

        # Detect sensitive files without reading contents
        sensitive_files = [
            f.path for f in files
            if RepositoryScanner.is_sensitive_file(f.name, f.path)
        ]
        for sf in sensitive_files:
            scan_metadata.warnings.append(f"Sensitive file detected: {sf}")

        profile = RepositoryProfile(
            name=path.name,
            scan=scan_metadata,
            languages=languages,
            technologies=technologies,
            package_managers=package_managers,
            dependencies=dependencies,
            commands=commands,
            modules=modules,
            file_categories=file_categories,
            important_files=important_files,
            tree=tree,
            warnings=scan_metadata.warnings + scan_result.errors,
        )

        logger.info(
            "Repository analysis complete: %s (%d files in %.1fs)",
            path.name,
            scan_result.total_files,
            duration,
        )

        return profile

    def _build_file_map(
        self, files: List[ScannedFile], root_path: str
    ) -> Dict[str, Optional[str]]:
        """Read relevant file contents for analysis.

        Only reads files that are needed for detection (manifests, configs).
        Never reads binary files or files over 500KB for content analysis.
        Never exposes sensitive file contents.
        """
        file_map: Dict[str, Optional[str]] = {}

        for f in files:
            # Only read manifest and config files
            if f.name not in {
                "package.json", "pyproject.toml", "requirements.txt",
                "Pipfile", "Cargo.toml", "go.mod", "Makefile",
                "Gemfile", "composer.json", "pom.xml",
                "build.gradle", "build.gradle.kts",
                "pytest.ini", "tox.ini", "noxfile.py",
                ".gitignore",
            }:
                continue

            # Skip binary and large files
            if f.is_binary or f.size_bytes > 500_000:
                continue

            # Skip sensitive files — never read their contents
            if RepositoryScanner.is_sensitive_file(f.name, f.path):
                continue

            try:
                full_path = os.path.join(root_path, f.path)
                with open(full_path, "r", encoding="utf-8", errors="ignore") as fh:
                    file_map[f.path] = fh.read(100_000)  # Limit to 100KB
            except Exception:
                file_map[f.path] = None  # Record that it exists but couldn't be read

        # Also add entries for files that exist but we won't read contents
        for f in files:
            if f.path not in file_map and f.name in {
                "package.json", "pyproject.toml", "requirements.txt",
                "Cargo.toml", "go.mod",
                "Pipfile", "Gemfile",
            }:
                file_map[f.path] = None  # Mark as exists but unread

        return file_map
