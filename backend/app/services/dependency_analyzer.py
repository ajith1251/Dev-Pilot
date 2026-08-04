"""
Dependency Analyzer — parse package manifests and extract structured dependency info.

Supports: package.json, requirements.txt, pyproject.toml, Pipfile,
Cargo.toml, go.mod, pom.xml, build.gradle*.kts
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from app.models.profile import Dependency, PackageManager


class DependencyAnalyzer:
    """Parse dependency manifests and extract structured information."""

    def __init__(self, root_path: str) -> None:
        self._root = Path(root_path)

    def analyze(self, file_map: Dict[str, Optional[str]]) -> Tuple[List[Dependency], List[PackageManager]]:
        """Analyze all discovered manifests.

        Args:
            file_map: Dict of {relative_path: content_or_None}.

        Returns:
            Tuple of (dependencies, package_managers).
        """
        deps: List[Dependency] = []
        managers: Dict[str, PackageManager] = {}

        # package.json (npm / yarn / pnpm)
        if "package.json" in file_map:
            pkg_deps, npm_mgr = self._parse_package_json(file_map["package.json"])
            deps.extend(pkg_deps)
            if npm_mgr:
                npm_mgr.manifest_files.append("package.json")
                if "package-lock.json" in file_map:
                    npm_mgr.lock_files.append("package-lock.json")
                if "yarn.lock" in file_map:
                    npm_mgr.lock_files.append("yarn.lock")
                if "pnpm-lock.yaml" in file_map:
                    npm_mgr.lock_files.append("pnpm-lock.yaml")
                managers["npm"] = npm_mgr

        # requirements.txt (pip)
        if "requirements.txt" in file_map:
            pip_deps, pip_mgr = self._parse_requirements_txt(file_map["requirements.txt"])
            deps.extend(pip_deps)
            if pip_mgr:
                pip_mgr.manifest_files.append("requirements.txt")
                managers["pip"] = pip_mgr

        # pyproject.toml (Poetry / PDM / PEP 621)
        if "pyproject.toml" in file_map:
            py_deps, py_mgr = self._parse_pyproject_toml(file_map["pyproject.toml"])
            deps.extend(py_deps)
            if py_mgr:
                py_mgr.manifest_files.append("pyproject.toml")
                if "poetry.lock" in file_map:
                    py_mgr.lock_files.append("poetry.lock")
                managers["poetry"] = py_mgr

        # Pipfile (Pipenv)
        if "Pipfile" in file_map:
            pf_deps, pf_mgr = self._parse_pipfile(file_map["Pipfile"])
            deps.extend(pf_deps)
            if pf_mgr:
                pf_mgr.manifest_files.append("Pipfile")
                if "Pipfile.lock" in file_map:
                    pf_mgr.lock_files.append("Pipfile.lock")
                managers["pipenv"] = pf_mgr

        # Cargo.toml (Cargo / Rust)
        if "Cargo.toml" in file_map:
            cargo_deps, cargo_mgr = self._parse_cargo_toml(file_map["Cargo.toml"])
            deps.extend(cargo_deps)
            if cargo_mgr:
                cargo_mgr.manifest_files.append("Cargo.toml")
                if "Cargo.lock" in file_map:
                    cargo_mgr.lock_files.append("Cargo.lock")
                managers["cargo"] = cargo_mgr

        # go.mod (Go modules)
        if "go.mod" in file_map:
            go_deps, go_mgr = self._parse_go_mod(file_map["go.mod"])
            deps.extend(go_deps)
            if go_mgr:
                go_mgr.manifest_files.append("go.mod")
                if "go.sum" in file_map:
                    go_mgr.lock_files.append("go.sum")
                managers["go"] = go_mgr

        return deps, list(managers.values())

    def _parse_package_json(self, content: Optional[str]) -> Tuple[List[Dependency], Optional[PackageManager]]:
        """Parse package.json dependencies."""
        if not content:
            return [], None
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return [], PackageManager(name="npm", ecosystem="npm")

        deps: List[Dependency] = []
        mgr = PackageManager(name="npm", ecosystem="npm")

        for dep_type in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
            section = data.get(dep_type, {})
            dep_type_map = {
                "dependencies": "runtime",
                "devDependencies": "dev",
                "peerDependencies": "peer",
                "optionalDependencies": "optional",
            }
            for name, version in section.items():
                deps.append(Dependency(
                    name=name,
                    declared_version=version,
                    type=dep_type_map.get(dep_type, "runtime"),
                    ecosystem="npm",
                    manifest_path="package.json",
                ))

        return deps, mgr

    def _parse_requirements_txt(self, content: Optional[str]) -> Tuple[List[Dependency], Optional[PackageManager]]:
        """Parse requirements.txt dependencies."""
        if not content:
            return [], None

        deps: List[Dependency] = []
        mgr = PackageManager(name="pip", ecosystem="pip")

        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            # Parse version specifiers
            match = re.match(r"^([a-zA-Z0-9_.-]+)\s*([><=!~]+\s*[\w.*-]+(?:\s*[><=!]+\s*[\w.*-]+)*)?", line)
            if match:
                name = match.group(1)
                version = match.group(2) or ""
                deps.append(Dependency(
                    name=name,
                    declared_version=version.strip() if version else None,
                    type="runtime",
                    ecosystem="pip",
                    manifest_path="requirements.txt",
                ))

        return deps, mgr

    def _parse_pyproject_toml(self, content: Optional[str]) -> Tuple[List[Dependency], Optional[PackageManager]]:
        """Parse pyproject.toml dependencies (simplified)."""
        if not content:
            return [], None

        deps: List[Dependency] = []
        mgr = PackageManager(name="poetry", ecosystem="pip")

        # Detect build system
        if "poetry" in content:
            mgr.name = "poetry"
        elif "pdm" in content:
            mgr.name = "pdm"
        else:
            mgr.name = "pip"

        # Parse dependencies sections (simplified — matches [tool.poetry.dependencies] etc.)
        current_section = ""
        dep_type = "runtime"

        for line in content.splitlines():
            line_stripped = line.strip()

            # Section headers
            section_match = re.match(r"^\[(.+)\]$", line_stripped)
            if section_match:
                current_section = section_match.group(1)
                if "dev" in current_section.lower():
                    dep_type = "dev"
                elif "optional" in current_section.lower():
                    dep_type = "optional"
                else:
                    dep_type = "runtime"
                continue

            # Skip non-dependency sections
            if "depend" not in current_section and "project" not in current_section:
                continue

            # Parse name = "version" lines
            dep_match = re.match(r'^([a-zA-Z0-9_.-]+)\s*=\s*"?([^"]*)"?', line_stripped)
            if dep_match and not dep_match.group(1).startswith("["):
                name = dep_match.group(1)
                version = dep_match.group(2) if dep_match.group(2) and dep_match.group(2) != "*" else None
                deps.append(Dependency(
                    name=name,
                    declared_version=version,
                    type=dep_type,
                    ecosystem="pip",
                    manifest_path="pyproject.toml",
                ))

        return deps, mgr

    def _parse_pipfile(self, content: Optional[str]) -> Tuple[List[Dependency], Optional[PackageManager]]:
        """Parse Pipfile (simplified TOML-like)."""
        if not content:
            return [], None

        deps: List[Dependency] = []
        dep_type = "runtime"

        for line in content.splitlines():
            line_stripped = line.strip()
            if "[packages]" in line_stripped:
                dep_type = "runtime"
            elif "[dev-packages]" in line_stripped:
                dep_type = "dev"
            else:
                match = re.match(r'^([a-zA-Z0-9_.-]+)\s*=\s*"?([^"]*)"?', line_stripped)
                if match:
                    name = match.group(1)
                    version = match.group(2) if match.group(2) and match.group(2) != "*" else None
                    deps.append(Dependency(
                        name=name,
                        declared_version=version,
                        type=dep_type,
                        ecosystem="pip",
                        manifest_path="Pipfile",
                    ))

        mgr = PackageManager(name="pipenv", ecosystem="pip", manifest_files=["Pipfile"])
        return deps, mgr

    def _parse_cargo_toml(self, content: Optional[str]) -> Tuple[List[Dependency], Optional[PackageManager]]:
        """Parse Cargo.toml dependencies (simplified)."""
        if not content:
            return [], None

        deps: List[Dependency] = []
        current_section = ""

        for line in content.splitlines():
            line_stripped = line.strip()
            section_match = re.match(r"^\[(.+)\]$", line_stripped)
            if section_match:
                current_section = section_match.group(1)
                continue

            if "depend" not in current_section:
                continue

            dep_match = re.match(r'^([a-zA-Z0-9_.-]+)\s*=\s*{?\s*"?([^"}]*)"?', line_stripped)
            if dep_match:
                name = dep_match.group(1)
                version = dep_match.group(2) if dep_match.group(2) != "}" else None
                deps.append(Dependency(
                    name=name,
                    declared_version=version,
                    type="runtime" if "dev" not in current_section else "dev",
                    ecosystem="cargo",
                    manifest_path="Cargo.toml",
                ))

        mgr = PackageManager(name="cargo", ecosystem="cargo", manifest_files=["Cargo.toml"])
        return deps, mgr

    def _parse_go_mod(self, content: Optional[str]) -> Tuple[List[Dependency], Optional[PackageManager]]:
        """Parse go.mod dependencies."""
        if not content:
            return [], None

        deps: List[Dependency] = []

        for line in content.splitlines():
            line_stripped = line.strip()
            # Match: module/path v1.2.3
            match = re.match(r"^(\S+)\s+(v\S+)", line_stripped)
            if match and not line_stripped.startswith("go ") and not line_stripped.startswith("require"):
                name = match.group(1)
                version = match.group(2)
                deps.append(Dependency(
                    name=name,
                    declared_version=version,
                    type="runtime",
                    ecosystem="go",
                    manifest_path="go.mod",
                ))

        mgr = PackageManager(name="go", ecosystem="go", manifest_files=["go.mod"])
        return deps, mgr
