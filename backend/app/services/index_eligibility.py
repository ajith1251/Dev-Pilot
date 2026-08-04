"""
Index Eligibility Service — determines which files should enter the code index.

Reuses Phase 2 scanning/classification information to make deterministic
eligibility decisions. Protects against indexing sensitive, binary, or
otherwise inappropriate content.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Set

from app.models.profile import FileCategory, FileClassification
from app.models.rag import EligibilityReason, IndexEligibilityResult
from app.services.repository_scanner import ScannedFile


# File categories that ARE eligible for indexing
INDEXABLE_CATEGORIES: Set[FileCategory] = {
    FileCategory.SOURCE,
    FileCategory.TEST,
    FileCategory.DOCUMENTATION,
    FileCategory.CONFIGURATION,
    FileCategory.DEPENDENCY_MANIFEST,
    FileCategory.SCRIPT,
    FileCategory.BUILD,
    FileCategory.TEMPLATE,
}

# File categories that are explicitly NOT eligible
SKIP_CATEGORIES: Set[FileCategory] = {
    FileCategory.LOCKFILE,
    FileCategory.ASSET,
    FileCategory.GENERATED,
    FileCategory.DATA,
    FileCategory.INFRASTRUCTURE,
    FileCategory.UNKNOWN,
}

# Max file size for indexing (500KB)
MAX_INDEX_FILE_SIZE: int = 500 * 1024

# Extensions considered possibly minified
MINIFIED_EXTENSIONS: Set[str] = {".min.js", ".min.css", ".bundle.js", ".bundle.css"}

# Files we never index regardless of category
NEVER_INDEX_NAMES: Set[str] = {
    ".env", ".env.local", ".env.production", ".env.development",
    ".env.staging", ".env.test", ".env.example",
    "credentials.json", "credentials.yaml", "credentials.yml",
    "service-account.json", "service-account.yaml",
    "id_rsa", "id_rsa.pub", "id_ecdsa", "id_ed25519",
    ".npmrc", ".netrc", ".pgpass",
    "secret", "secrets", "secret.yaml", "secrets.yaml",
    "oauth.json", "oauth.yaml",
    "kubeconfig", "kubeconfig.yaml", "kubeconfig.yml",
    "Dockerfile",  # Skip Dockerfiles (infrastructure)
}

# Extensions of files we never index
NEVER_INDEX_EXTENSIONS: Set[str] = {
    ".pem", ".key", ".cert", ".p12", ".pfx", ".keystore",
    ".cred", ".credentials",
    ".exe", ".dll", ".so", ".dylib", ".class", ".jar", ".war",
    ".pyc", ".pyo", ".pyd",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".mp4", ".avi", ".mov", ".mkv", ".webm",
    ".mp3", ".wav", ".ogg", ".flac",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".tar", ".gz", ".bz2", ".rar", ".7z",
    ".o", ".obj", ".a", ".lib",
    ".wasm",
    ".DS_Store",
}

# Config file names that are specifically useful for indexing
USEFUL_CONFIG_NAMES: Set[str] = {
    "package.json", "pyproject.toml", "requirements.txt",
    "Cargo.toml", "go.mod", "composer.json", "Gemfile",
    "tsconfig.json", "next.config.js", "next.config.ts",
    "vite.config.ts", "vite.config.js",
    "jest.config.js", "jest.config.ts",
    "tailwind.config.js", "tailwind.config.ts",
    "postcss.config.js",
    ".gitignore", ".editorconfig",
    "setup.py", "setup.cfg",
}

# Architecture-relevant documentation
ARCH_DOC_NAMES: Set[str] = {
    "README.md", "CONTRIBUTING.md", "ARCHITECTURE.md",
    "CHANGELOG.md", "LICENSE",
}


class IndexEligibilityService:
    """Determines which files are eligible for code indexing.

    Uses Phase 2 file classification and additional heuristics
    to make safe, deterministic eligibility decisions.
    """

    def __init__(
        self,
        max_file_size: int = MAX_INDEX_FILE_SIZE,
    ) -> None:
        self.max_file_size = max_file_size

    def determine_eligibility(
        self,
        file: ScannedFile,
        category: Optional[FileCategory] = None,
    ) -> IndexEligibilityResult:
        """Determine whether a file should be indexed.

        Args:
            file: Scanned file metadata.
            category: Pre-determined file category (from FileClassifier).

        Returns:
            IndexEligibilityResult with eligible flag and reason.
        """
        # 1. Never index by name
        if file.name in NEVER_INDEX_NAMES:
            return IndexEligibilityResult(
                file_path=file.path,
                eligible=False,
                reason=EligibilityReason.SKIP_SENSITIVE,
                detail=f"Sensitive/forbidden file name: {file.name}",
                category="sensitive",
            )

        # 2. Never index by extension
        if file.extension.lower() in NEVER_INDEX_EXTENSIONS:
            reason = EligibilityReason.SKIP_SENSITIVE
            detail = f"Forbidden extension: {file.extension}"
            cat = "sensitive"
            if file.extension in {".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp"}:
                reason = EligibilityReason.SKIP_IMAGE
                detail = "Image file"
                cat = "image"
            elif file.extension in {
                ".exe", ".dll", ".so", ".dylib", ".class", ".jar", ".war",
                ".o", ".obj", ".pyc",
            }:
                reason = EligibilityReason.SKIP_BINARY
                detail = "Binary file"
                cat = "binary"
            return IndexEligibilityResult(
                file_path=file.path,
                eligible=False,
                reason=reason,
                detail=detail,
                category=cat,
            )

        # 3. Skip minified bundles
        if file.name.endswith(tuple(MINIFIED_EXTENSIONS)):
            return IndexEligibilityResult(
                file_path=file.path,
                eligible=False,
                reason=EligibilityReason.SKIP_MINIFIED,
                detail="Minified bundle",
                category="minified",
            )

        # 4. Skip oversized
        if file.size_bytes > self.max_file_size:
            return IndexEligibilityResult(
                file_path=file.path,
                eligible=False,
                reason=EligibilityReason.SKIP_OVERSIZED,
                detail=f"File too large: {file.size_bytes} bytes (max {self.max_file_size})",
                category="oversized",
            )

        # 5. Skip binary
        if file.is_binary:
            return IndexEligibilityResult(
                file_path=file.path,
                eligible=False,
                reason=EligibilityReason.SKIP_BINARY,
                detail="Binary content",
                category="binary",
            )

        # 6. Use category-based decision
        if category:
            if category in SKIP_CATEGORIES:
                skip_reasons = {
                    FileCategory.LOCKFILE: (EligibilityReason.SKIP_LOCKFILE, "Lockfile, skipping content", "lockfile"),
                    FileCategory.ASSET: (EligibilityReason.SKIP_IMAGE, "Asset file", "asset"),
                    FileCategory.GENERATED: (EligibilityReason.SKIP_GENERATED, "Generated file", "generated"),
                    FileCategory.UNKNOWN: (EligibilityReason.SKIP_UNKNOWN, "Unknown file type", "unknown"),
                }
                if category in skip_reasons:
                    r, detail, cat = skip_reasons[category]
                    return IndexEligibilityResult(
                        file_path=file.path,
                        eligible=False,
                        reason=r,
                        detail=detail,
                        category=cat,
                    )
                return IndexEligibilityResult(
                    file_path=file.path,
                    eligible=False,
                    reason=EligibilityReason.SKIP_UNKNOWN,
                    detail=f"Category not eligible: {category.value}",
                    category=category.value,
                )

            if category in INDEXABLE_CATEGORIES:
                reason_map = {
                    FileCategory.SOURCE: EligibilityReason.INDEX_SOURCE,
                    FileCategory.TEST: EligibilityReason.INDEX_TEST,
                    FileCategory.CONFIGURATION: EligibilityReason.INDEX_CONFIG,
                    FileCategory.DOCUMENTATION: EligibilityReason.INDEX_DOC,
                    FileCategory.SCRIPT: EligibilityReason.INDEX_SCRIPT,
                    FileCategory.DEPENDENCY_MANIFEST: EligibilityReason.INDEX_MANIFEST,
                }
                reason = reason_map.get(category, EligibilityReason.INDEX_SOURCE)
                return IndexEligibilityResult(
                    file_path=file.path,
                    eligible=True,
                    reason=reason,
                    detail=f"Indexing as {category.value}",
                    category=category.value,
                )

        # 7. Path-based fallback for config/docs that might be misclassified
        if file.name in USEFUL_CONFIG_NAMES:
            return IndexEligibilityResult(
                file_path=file.path,
                eligible=True,
                reason=EligibilityReason.INDEX_CONFIG,
                detail="Useful configuration file",
                category="configuration",
            )

        if file.name in ARCH_DOC_NAMES:
            return IndexEligibilityResult(
                file_path=file.path,
                eligible=True,
                reason=EligibilityReason.INDEX_DOC,
                detail="Architecture-relevant documentation",
                category="documentation",
            )

        # 8. Default: skip
        return IndexEligibilityResult(
            file_path=file.path,
            eligible=False,
            reason=EligibilityReason.SKIP_UNKNOWN,
            detail="Not classified as indexable content",
            category="unknown",
        )

    def filter_indexable_files(
        self,
        files: List[ScannedFile],
        categories: Optional[Dict[str, FileCategory]] = None,
    ) -> List[IndexEligibilityResult]:
        """Filter a list of scanned files to those eligible for indexing.

        Args:
            files: List of scanned files.
            categories: Optional dict of path -> FileCategory.

        Returns:
            List of eligibility results (both eligible and skipped).
        """
        results: List[IndexEligibilityResult] = []

        for f in files:
            cat = None
            if categories:
                cat = categories.get(f.path)
            result = self.determine_eligibility(f, category=cat)
            results.append(result)

        return results

    def get_eligible_files(
        self,
        files: List[ScannedFile],
        categories: Optional[Dict[str, FileCategory]] = None,
    ) -> List[IndexEligibilityResult]:
        """Return only eligible files with their results."""
        all_results = self.filter_indexable_files(files, categories)
        return [r for r in all_results if r.eligible]
