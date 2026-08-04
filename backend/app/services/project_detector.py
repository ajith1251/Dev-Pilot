"""
Project Detector — identify separate modules/projects within a repository.

Supports monorepos by detecting manifest files at different directory levels.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Set

from app.models.profile import RepositoryCommand, RepositoryModule


class ProjectDetector:
    """Detect modules/projects within a repository (monorepo support)."""

    # Files that indicate a module boundary
    MODULE_INDICATORS: Set[str] = {
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "Pipfile",
        "Cargo.toml",
        "go.mod",
        "Gemfile",
        "composer.json",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "mix.exs",
    }

    def __init__(self, root_path: str) -> None:
        self._root = Path(root_path)

    def detect(
        self,
        file_paths: Set[str],
        file_map: Dict[str, Optional[str]],
        commands: List[RepositoryCommand],
    ) -> List[RepositoryModule]:
        """Detect modules within the repository.

        Args:
            file_paths: Set of all relative file paths.
            file_map: Dict of {relative_path: content_or_None}.
            commands: List of discovered commands.

        Returns:
            List of RepositoryModule.
        """
        modules: List[RepositoryModule] = []

        # Collect all module indicator files and group by directory
        indicator_dirs: Dict[str, List[str]] = {}

        for path in file_paths:
            name = Path(path).name
            if name in self.MODULE_INDICATORS:
                parent = str(Path(path).parent)
                if parent not in indicator_dirs:
                    indicator_dirs[parent] = []
                indicator_dirs[parent].append(name)

        # Root module (always exists)
        root_indicators = indicator_dirs.get(".", [])
        root_module = self._build_module(".", root_indicators, file_map, commands)
        if root_module:
            modules.append(root_module)

        # Sub-modules (skip root)
        for dir_path, indicators in indicator_dirs.items():
            if dir_path == ".":
                continue

            # Skip deeply nested modules (likely not actual projects)
            depth = dir_path.count("/")
            if depth > 3:
                continue

            module = self._build_module(dir_path, indicators, file_map, commands)
            if module:
                modules.append(module)

        return modules

    def _build_module(
        self,
        dir_path: str,
        indicators: List[str],
        file_map: Dict[str, Optional[str]],
        commands: List[RepositoryCommand],
    ) -> Optional[RepositoryModule]:
        """Build a RepositoryModule from detected indicators."""
        if not indicators and dir_path != ".":
            return None

        name = Path(dir_path).name if dir_path != "." else Path(self._root).name

        module_type = "unknown"
        languages: List[str] = []
        frameworks: List[str] = []
        package_manager: Optional[str] = None
        module_commands: List[RepositoryCommand] = []

        for indicator in indicators:
            if indicator == "package.json":
                package_manager = "npm"
                module_type = self._detect_npm_type(file_map, dir_path)
                languages.append("TypeScript" if self._has_tsconfig(file_map, dir_path) else "JavaScript")
            elif indicator == "pyproject.toml":
                package_manager = "poetry"
                if module_type == "unknown":
                    module_type = "backend"
                languages.append("Python")
            elif indicator == "requirements.txt":
                if not package_manager:
                    package_manager = "pip"
                languages.append("Python")
            elif indicator == "Cargo.toml":
                package_manager = "cargo"
                module_type = "library"
                languages.append("Rust")
            elif indicator == "go.mod":
                package_manager = "go"
                module_type = "backend"
                languages.append("Go")
            elif indicator in ("pom.xml", "build.gradle", "build.gradle.kts"):
                package_manager = "maven"
                languages.append("Java")
            elif indicator in ("Gemfile",):
                package_manager = "bundler"
                languages.append("Ruby")

        # Refine type based on indicators and module name
        if module_type == "unknown":
            name_lower = name.lower()
            if name_lower in ("frontend", "client", "web", "ui", "app"):
                module_type = "frontend"
            elif name_lower in ("backend", "server", "api", "service"):
                module_type = "backend"
            elif name_lower in ("mobile", "android", "ios"):
                module_type = "mobile"

        # Filter commands belonging to this module
        for cmd in commands:
            if cmd.source.startswith(dir_path.rstrip("/")):
                module_commands.append(cmd)

        return RepositoryModule(
            path=dir_path,
            name=name,
            type=module_type,
            languages=list(set(languages)),
            frameworks=frameworks,
            package_manager=package_manager,
            manifests=indicators,
            commands=module_commands,
        )

    def _detect_npm_type(self, file_map: Dict[str, Optional[str]], dir_path: str) -> str:
        """Detect module type from package.json content."""
        pkg_path = f"{dir_path}/package.json" if dir_path != "." else "package.json"
        content = file_map.get(pkg_path)
        if not content:
            return "frontend"  # default guess

        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return "frontend"

        name = (data.get("name", "") or "").lower()
        scripts = list((data.get("scripts", {}) or {}).keys())
        deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}

        # Check for frontend indicators
        if any(fw in deps for fw in ("next", "react", "vue", "nuxt", "svelte", "angular")):
            return "frontend"

        # Check for backend indicators
        if any(fw in deps for fw in ("express", "fastify", "@nestjs/core", "koa")):
            return "backend"

        # Check name
        if any(x in name for x in ("frontend", "client", "web", "ui")):
            return "frontend"
        if any(x in name for x in ("backend", "server", "api", "service")):
            return "backend"

        # Check scripts
        script_str = " ".join(scripts).lower()
        if any(x in script_str for x in ("dev", "build", "start")):
            if any(x in script_str for x in ("server", "api")):
                return "backend"
            return "frontend"

        return "library"

    def _has_tsconfig(self, file_map: Dict[str, Optional[str]], dir_path: str) -> bool:
        """Check if a tsconfig.json exists in the module directory."""
        ts_path = f"{dir_path}/tsconfig.json" if dir_path != "." else "tsconfig.json"
        ts_path_alt = f"{dir_path}/tsconfig.ts" if dir_path != "." else "tsconfig.ts"
        return ts_path in file_map or ts_path_alt in file_map


def get_supported_module_indicators() -> List[str]:
    """Return the list of module indicator file names."""
    return sorted(ProjectDetector.MODULE_INDICATORS)
