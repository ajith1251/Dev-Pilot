"""
File Classifier — classify files into categories for later analysis.

Categories: source, test, configuration, documentation, dependency_manifest,
lockfile, migration, ci_cd, infrastructure, generated, asset, data, script,
build, template, unknown
"""

from __future__ import annotations

import os
from typing import Dict, Set

from app.models.profile import FileCategory
from app.services.repository_scanner import ScannedFile

# Known config files by name
CONFIG_FILE_NAMES: Set[str] = {
    ".gitignore", ".gitattributes", ".editorconfig",
    ".env.example", ".env.sample",
    "tsconfig.json", ".eslintrc.js", ".eslintrc.json", ".eslintrc",
    ".prettierrc", ".prettierrc.js", ".prettierrc.json",
    "babel.config.js", "babel.config.json",
    "webpack.config.js", "vite.config.js", "vite.config.ts",
    "next.config.js", "next.config.ts", "nuxt.config.js",
    "jest.config.js", "jest.config.ts", "vitest.config.ts", "vitest.config.js",
    "playwright.config.ts", "playwright.config.js",
    "cypress.config.ts", "cypress.config.js",
    "tailwind.config.js", "tailwind.config.ts",
    "postcss.config.js",
    "setup.cfg", "pytest.ini", "tox.ini", "noxfile.py",
    "docker-compose.yml", "docker-compose.yaml",
    "nginx.conf", ".dockerignore",
    "codecov.yml", "codecov.yaml", ".codecov.yml",
    "pre-commit-config.yaml",
    "renovate.json", ".renovaterc",
}

# Dependency manifests
MANIFEST_NAMES: Set[str] = {
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "requirements.txt", "pyproject.toml", "poetry.lock", "Pipfile", "Pipfile.lock",
    "Cargo.toml", "Cargo.lock",
    "go.mod", "go.sum",
    "Gemfile", "Gemfile.lock",
    "composer.json", "composer.lock",
    "pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle",
    "mix.exs", "mix.lock",
}

# Lock files specifically
LOCK_FILE_NAMES: Set[str] = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "poetry.lock", "Pipfile.lock", "Cargo.lock", "go.sum",
    "Gemfile.lock", "composer.lock", "mix.lock",
}

# Test file prefixes and suffixes
TEST_PREFIXES = ("test_", "spec_", "Test", "Spec")
TEST_SUFFIXES = ("_test", "_spec", ".test.", ".spec.", "Test", "Spec")
TEST_DIR_NAMES = {"test", "tests", "spec", "specs", "__tests__", "test_utils"}

# Script extensions
SCRIPT_EXTENSIONS: Set[str] = {".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd", ".fish"}

# Documentation extensions
DOC_EXTENSIONS: Set[str] = {".md", ".rst", ".adoc", ".txt", ".tex"}

# Asset extensions
ASSET_EXTENSIONS: Set[str] = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".mp4", ".mp3", ".wav", ".pdf",
}

# Migration directories
MIGRATION_DIR_NAMES = {"migrations", "migrate", "migration"}


class FileClassifier:
    """Classify files into categories."""

    def classify(self, files: list[ScannedFile]) -> Dict[str, int]:
        """Classify files and return category counts.

        Args:
            files: List of scanned files.

        Returns:
            Dict of {category_name: count}.
        """
        counts: Dict[str, int] = {}

        for f in files:
            category = self._classify_single(f)
            key = category.value
            counts[key] = counts.get(key, 0) + 1

        return counts

    def classify_file(self, f: ScannedFile) -> FileCategory:
        """Classify a single file and return its category."""
        return self._classify_single(f)

    def _classify_single(self, f: ScannedFile) -> FileCategory:
        """Classify a single scanned file."""
        name = f.name
        path = f.path
        ext = f.extension

        # CI/CD files
        if ".github/workflows" in path and ext in (".yml", ".yaml"):
            return FileCategory.CI_CD

        # Build files (named, no extension)
        if name in {"Dockerfile", "Makefile", "CMakeLists.txt", "build.gradle", "build.gradle.kts"}:
            return FileCategory.BUILD

        # Dependency manifests
        if name in MANIFEST_NAMES:
            if name in LOCK_FILE_NAMES:
                return FileCategory.LOCKFILE
            return FileCategory.DEPENDENCY_MANIFEST

        # Known config files
        if name in CONFIG_FILE_NAMES:
            return FileCategory.CONFIGURATION

        # Migration files
        if any(f"/{d}/" in f"/{path}/" for d in MIGRATION_DIR_NAMES):
            return FileCategory.MIGRATION

        # Infrastructure (Docker, K8s, Terraform)
        if name in {"Dockerfile", ".dockerignore"}:
            return FileCategory.INFRASTRUCTURE
        if ext in {".tf", ".tfvars"}:
            return FileCategory.INFRASTRUCTURE
        if "kubernetes" in path.lower() or "k8s" in path.lower() and ext in (".yml", ".yaml"):
            return FileCategory.INFRASTRUCTURE

        # Test files
        if name.startswith(TEST_PREFIXES):
            return FileCategory.TEST
        for suffix in TEST_SUFFIXES:
            if name.endswith(suffix) or suffix in name:
                return FileCategory.TEST
        if any(f"/{d}/" in f"/{path}/" for d in TEST_DIR_NAMES):
            return FileCategory.TEST

        # Scripts
        if ext in SCRIPT_EXTENSIONS:
            return FileCategory.SCRIPT

        # Documentation
        if ext in DOC_EXTENSIONS:
            return FileCategory.DOCUMENTATION

        # Assets
        if ext in ASSET_EXTENSIONS or f.is_binary:
            return FileCategory.ASSET

        # Configuration by extension
        if ext in {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf"}:
            return FileCategory.CONFIGURATION

        # Source code
        if ext in {".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rs",
                   ".c", ".cpp", ".cs", ".rb", ".php", ".swift", ".kt"}:
            return FileCategory.SOURCE

        # Templates
        if ext in {".html", ".htm", ".vue", ".svelte", ".hbs", ".handlebars", ".jinja", ".jinja2"}:
            return FileCategory.TEMPLATE

        # Data
        if ext in {".sql", ".csv", ".tsv", ".xml"}:
            return FileCategory.DATA

        return FileCategory.UNKNOWN
