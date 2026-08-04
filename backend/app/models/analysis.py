"""
Data models for repository analysis.

Defines the input payload the Repository Analyzer accepts
and the structured output it returns.
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


# ── Enums ───────────────────────────────────────────────────────


class FileCategory(str, Enum):
    """Classification of a file's role in the project."""

    SOURCE = "source"
    TEST = "test"
    CONFIG = "config"
    DOCUMENTATION = "documentation"
    BUILD = "build"
    DATA = "data"
    SCRIPT = "script"
    TEMPLATE = "template"
    UNKNOWN = "unknown"


class FrameworkCategory(str, Enum):
    """Category of detected framework."""

    WEB = "web"
    BACKEND = "backend"
    FRONTEND = "frontend"
    TESTING = "testing"
    DATABASE = "database"
    ORM = "orm"
    BUILD_TOOL = "build_tool"
    PACKAGE_MANAGER = "package_manager"
    AI_ML = "ai_ml"
    DEVOPS = "devops"
    OTHER = "other"


# ── File / Directory Models ─────────────────────────────────────


class FileInfo(BaseModel):
    """Metadata about a single file in the repository."""

    path: str = Field(description="Relative path from repo root")
    name: str = Field(description="File name with extension")
    extension: str = Field(default="", description="File extension (e.g. '.py')")
    size_bytes: int = Field(default=0, description="File size in bytes")
    category: FileCategory = Field(
        default=FileCategory.UNKNOWN, description="Classified role of this file"
    )
    language: Optional[str] = Field(
        default=None, description="Detected programming language"
    )
    is_binary: bool = Field(default=False)
    summary: Optional[str] = Field(
        default=None, description="Brief description of file purpose"
    )


class DirectoryNode(BaseModel):
    """A node in the repository directory tree."""

    path: str = Field(description="Relative path from repo root")
    name: str = Field(description="Directory name")
    directories: List[DirectoryNode] = Field(
        default_factory=list, description="Subdirectories"
    )
    files: List[FileInfo] = Field(
        default_factory=list, description="Files in this directory"
    )


class LanguageInfo(BaseModel):
    """Detected programming language."""

    name: str = Field(description="Language name (e.g. 'Python')")
    file_count: int = Field(default=0, description="Number of files in this language")
    extensions: List[str] = Field(default_factory=list, description="File extensions")
    total_bytes: int = Field(default=0, description="Total size in bytes")


class FrameworkInfo(BaseModel):
    """Detected framework, library, or tool."""

    name: str = Field(description="Framework name (e.g. 'FastAPI')")
    category: FrameworkCategory = Field(default=FrameworkCategory.OTHER)
    confidence: float = Field(
        default=0.5, ge=0.0, le=1.0, description="Detection confidence"
    )
    evidence: List[str] = Field(
        default_factory=list,
        description="Files or patterns that support this detection",
    )


class DependencyInfo(BaseModel):
    """A detected dependency / package."""

    name: str = Field(description="Package name")
    version_spec: Optional[str] = Field(
        default=None, description="Version constraint if available"
    )
    manager: str = Field(
        default="unknown", description="Package manager (npm, pip, cargo, etc.)"
    )


class RepositoryStructure(BaseModel):
    """Full repository structure representation."""

    tree: DirectoryNode = Field(description="Directory tree")
    total_files: int = Field(default=0)
    total_dirs: int = Field(default=0)
    depth: int = Field(default=0, description="Maximum directory depth")


class RepositorySummary(BaseModel):
    """High-level summary of the repository."""

    description: str = Field(default="", description="Brief description of the project")
    purpose: str = Field(default="", description="What the project does")
    tech_stack_summary: str = Field(
        default="", description="Summary of technologies used"
    )
    architecture_notes: str = Field(
        default="", description="Notable architectural patterns"
    )


# ── Input / Output ──────────────────────────────────────────────


class RepositoryAnalysisInput(BaseModel):
    """Input payload for the Repository Analyzer agent."""

    repo_url: str = Field(
        description="GitHub repository URL (e.g. https://github.com/owner/repo)"
    )
    branch: Optional[str] = Field(
        default=None, description="Branch to analyze (default: repository default)"
    )
    max_depth: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum directory depth for traversal",
    )
    max_files_to_analyze: int = Field(
        default=50,
        ge=1,
        le=500,
        description="Maximum number of files to read for deep analysis",
    )
    include_llm_summary: bool = Field(
        default=True,
        description="Whether to use LLM for generating a summary",
    )


class RepositoryAnalysisOutput(BaseModel):
    """Structured output from the Repository Analyzer agent."""

    repo_name: str = Field(description="Repository name (owner/repo)")
    default_branch: str = Field(default="main", description="Default branch")
    structure: RepositoryStructure = Field(description="Directory structure")
    languages: List[LanguageInfo] = Field(
        default_factory=list, description="Detected languages sorted by usage"
    )
    frameworks: List[FrameworkInfo] = Field(
        default_factory=list, description="Detected frameworks"
    )
    entry_points: List[FileInfo] = Field(
        default_factory=list, description="Likely entry point files"
    )
    config_files: List[FileInfo] = Field(
        default_factory=list, description="Configuration files found"
    )
    test_files: List[FileInfo] = Field(
        default_factory=list, description="Test files found"
    )
    dependencies: List[DependencyInfo] = Field(
        default_factory=list, description="Dependencies detected"
    )
    summary: Optional[RepositorySummary] = Field(
        default=None, description="LLM-generated high-level summary"
    )
    error: Optional[str] = Field(
        default=None, description="Error message if analysis failed"
    )
