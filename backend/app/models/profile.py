"""
Repository Profile models for the Repository Intelligence Engine.

RepositoryProfile is the main output of Phase 2 — a comprehensive,
deterministic description of a local software repository.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


# ── Sub-enums ───────────────────────────────────────────────────


class ScanWarningType(str, Enum):
    MISSING = "missing"
    PERMISSION_DENIED = "permission_denied"
    EMPTY = "empty"
    LARGE_FILE = "large_file"
    SYMLINK_LOOP = "symlink_loop"
    MALFORMED_MANIFEST = "malformed_manifest"
    SENSITIVE_FILE = "sensitive_file"
    ECOSYSTEM_UNSUPPORTED = "ecosystem_unsupported"
    PARTIAL_METADATA = "partial_metadata"
    TRUNCATED = "truncated"


class FileCategory(str, Enum):
    SOURCE = "source"
    TEST = "test"
    CONFIGURATION = "configuration"
    DOCUMENTATION = "documentation"
    DEPENDENCY_MANIFEST = "dependency_manifest"
    LOCKFILE = "lockfile"
    MIGRATION = "migration"
    CI_CD = "ci_cd"
    INFRASTRUCTURE = "infrastructure"
    GENERATED = "generated"
    ASSET = "asset"
    DATA = "data"
    SCRIPT = "script"
    BUILD = "build"
    TEMPLATE = "template"
    UNKNOWN = "unknown"


class LanguageEntry(BaseModel):
    """A programming language detected in the repository."""

    name: str = Field(description="Language name (e.g. 'Python')")
    file_count: int = Field(default=0, ge=0)
    byte_count: int = Field(default=0, ge=0, description="Total bytes in language files")
    percentage: float = Field(default=0.0, ge=0.0, le=100.0, description="Percentage of total analyzed files")
    extensions: List[str] = Field(default_factory=list)


class TechnologyDetection(BaseModel):
    """A framework, library, or tool detected with evidence."""

    name: str = Field(description="Technology name (e.g. 'Next.js')")
    category: str = Field(default="other", description="frontend|backend|testing|database|devops|package_manager|build_tool|other")
    confidence: str = Field(default="medium", description="HIGH|MEDIUM|LOW|INFERRED")
    evidence: List[str] = Field(default_factory=list, description="Why we detected this")


class Dependency(BaseModel):
    """A single dependency extracted from a manifest."""

    name: str = Field(description="Package name")
    declared_version: Optional[str] = Field(default=None, description="Version constraint from manifest")
    type: str = Field(default="runtime", description="runtime|dev|optional|build|peer")
    ecosystem: str = Field(default="unknown", description="npm|pip|cargo|go|maven|gradle|rubygems")
    manifest_path: str = Field(default="", description="Relative path to the manifest file")


class PackageManager(BaseModel):
    """A package manager detected in the repository."""

    name: str = Field(description="Package manager name (e.g. 'npm')")
    ecosystem: str = Field(default="unknown")
    manifest_files: List[str] = Field(default_factory=list)
    lock_files: List[str] = Field(default_factory=list)
    detected_version: Optional[str] = Field(default=None)


class RepositoryCommand(BaseModel):
    """A command discovered in repository configuration."""

    name: str = Field(description="Command name (e.g. 'dev', 'build', 'test')")
    command: str = Field(description="The actual shell command")
    category: str = Field(default="other", description="install|dev|build|test|lint|format|typecheck|migration|other")
    source: str = Field(default="", description="Where this was discovered (e.g. 'package.json')")
    confidence: str = Field(default="medium", description="HIGH|MEDIUM|LOW")


class FileClassification(BaseModel):
    """A file with its classification."""

    path: str = Field(description="Relative path")
    name: str = Field(description="File name")
    extension: str = Field(default="")
    size_bytes: int = Field(default=0)
    category: FileCategory = Field(default=FileCategory.UNKNOWN)
    language: Optional[str] = Field(default=None)
    is_binary: bool = Field(default=False)
    depth: int = Field(default=0)


class ImportantFile(BaseModel):
    """A file deemed important for understanding the repository."""

    path: str = Field(description="Relative path")
    reason: str = Field(description="Why this file is important")
    score: float = Field(default=0.5, ge=0.0, le=1.0, description="Importance score 0-1")


class RepositoryModule(BaseModel):
    """A detected project/module within the repository (for monorepo support)."""

    path: str = Field(description="Root path of this module relative to repo root")
    name: str = Field(description="Module name (usually directory name)")
    type: str = Field(default="unknown", description="frontend|backend|mobile|library|tool|other")
    languages: List[str] = Field(default_factory=list)
    frameworks: List[str] = Field(default_factory=list)
    package_manager: Optional[str] = Field(default=None)
    manifests: List[str] = Field(default_factory=list)
    commands: List[RepositoryCommand] = Field(default_factory=list)


class RepositoryTree(BaseModel):
    """A compact repository tree representation."""

    text: str = Field(description="Compact text representation of the tree")
    max_depth: int = Field(default=0)
    total_dirs_shown: int = Field(default=0)
    total_files_shown: int = Field(default=0)


class ScanMetadata(BaseModel):
    """Metadata about the scan itself."""

    scanned_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    duration_seconds: float = Field(default=0.0)
    root_path: str = Field(default="")
    total_files_scanned: int = Field(default=0)
    total_dirs_scanned: int = Field(default=0)
    total_files_ignored: int = Field(default=0)
    total_bytes: int = Field(default=0)
    max_depth_reached: int = Field(default=0)
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


# ── Main Profile ────────────────────────────────────────────────


class RepositoryProfile(BaseModel):
    """The main output of the Repository Intelligence Engine."""

    name: str = Field(description="Repository directory name")
    scan: ScanMetadata = Field(default_factory=ScanMetadata)

    languages: List[LanguageEntry] = Field(default_factory=list)
    technologies: List[TechnologyDetection] = Field(default_factory=list)
    package_managers: List[PackageManager] = Field(default_factory=list)
    dependencies: List[Dependency] = Field(default_factory=list)
    commands: List[RepositoryCommand] = Field(default_factory=list)
    modules: List[RepositoryModule] = Field(default_factory=list)

    file_categories: Dict[str, int] = Field(default_factory=dict, description="Category -> count")
    important_files: List[ImportantFile] = Field(default_factory=list)
    tree: Optional[RepositoryTree] = Field(default=None)

    warnings: List[str] = Field(default_factory=list)
