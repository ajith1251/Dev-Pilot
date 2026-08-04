"""
Command Detector — discover build, test, lint, and other commands
from repository configuration files.

Does NOT execute any discovered commands.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional

from app.models.profile import RepositoryCommand


class CommandDetector:
    """Discover commands from repository configuration."""

    def __init__(self, root_path: str) -> None:
        self._root = Path(root_path)

    def detect(
        self,
        file_map: Dict[str, Optional[str]],
        existing_commands: Optional[List[RepositoryCommand]] = None,
    ) -> List[RepositoryCommand]:
        """Discover commands from all configurations.

        Args:
            file_map: Dict of {relative_path: content_or_None}.
            existing_commands: Pre-existing commands to extend.

        Returns:
            List of discovered RepositoryCommand.
        """
        commands: List[RepositoryCommand] = existing_commands or []

        # package.json scripts
        if "package.json" in file_map:
            commands.extend(self._from_package_json(file_map["package.json"]))

        # Makefile targets
        if "Makefile" in file_map:
            commands.extend(self._from_makefile(file_map["Makefile"]))

        # pyproject.toml scripts
        if "pyproject.toml" in file_map:
            commands.extend(self._from_pyproject_toml(file_map["pyproject.toml"]))

        # Common npm/pnpm scripts that might not be in package.json
        # but are conventionally understood
        commands.extend(self._from_conventions(file_map))

        return commands

    def _from_package_json(self, content: Optional[str]) -> List[RepositoryCommand]:
        """Extract commands from package.json scripts section."""
        if not content:
            return []
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return []

        commands: List[RepositoryCommand] = []
        scripts = data.get("scripts", {})

        category_map = {
            "dev": "dev", "start": "dev", "serve": "dev",
            "build": "build", "compile": "build",
            "test": "test", "test:": "test",
            "lint": "lint",
            "format": "format",
            "typecheck": "typecheck", "type-check": "typecheck", "typescript": "typecheck",
        }

        for name, script in scripts.items():
            if isinstance(script, str):
                category = "other"
                for key, cat in category_map.items():
                    if name == key or name.startswith(key):
                        category = cat
                        break
                commands.append(RepositoryCommand(
                    name=name,
                    command=script,
                    category=category,
                    source="package.json",
                    confidence="HIGH",
                ))

        return commands

    def _from_makefile(self, content: Optional[str]) -> List[RepositoryCommand]:
        """Extract Makefile targets."""
        if not content:
            return []

        commands: List[RepositoryCommand] = []
        for line in content.splitlines():
            stripped = line.strip()
            # Match target lines: target: [dependencies]
            match = re.match(r"^([a-zA-Z0-9_-]+)\s*:", stripped)
            if match and not stripped.startswith(".") and not stripped.startswith("\t"):
                target = match.group(1)
                category_map = {
                    "all": "build", "install": "install",
                    "dev": "dev", "run": "dev",
                    "build": "build",
                    "test": "test", "check": "test",
                    "lint": "lint",
                    "format": "format", "fmt": "format",
                    "clean": "build",
                    "deploy": "other",
                }
                category = category_map.get(target, "other")
                commands.append(RepositoryCommand(
                    name=target,
                    command=f"make {target}",
                    category=category,
                    source="Makefile",
                    confidence="MEDIUM",
                ))

        return commands

    def _from_pyproject_toml(self, content: Optional[str]) -> List[RepositoryCommand]:
        """Extract commands from pyproject.toml scripts/tasks."""
        if not content:
            return []

        commands: List[RepositoryCommand] = []
        in_scripts = False

        for line in content.splitlines():
            stripped = line.strip()

            if stripped.startswith("[tool.poetry.scripts]"):
                in_scripts = True
                continue
            if stripped.startswith("[") and in_scripts:
                in_scripts = False
                continue

            if in_scripts:
                match = re.match(r'^([a-zA-Z0-9_-]+)\s*=\s*"([^"]+)"', stripped)
                if match:
                    commands.append(RepositoryCommand(
                        name=match.group(1),
                        command=match.group(2),
                        category="other",
                        source="pyproject.toml",
                        confidence="MEDIUM",
                    ))

        return commands

    def _from_conventions(self, file_map: Dict[str, Optional[str]]) -> List[RepositoryCommand]:
        """Add conventional commands based on detected ecosystem."""
        commands: List[RepositoryCommand] = []

        has_npm = "package.json" in file_map
        has_python = "requirements.txt" in file_map or "pyproject.toml" in file_map

        if has_npm:
            # Only add conventional commands if package.json doesn't have them
            # (They'll be found by _from_package_json)
            pass

        if has_python:
            if "pytest.ini" in file_map or "pyproject.toml" in file_map:
                # Check if pytest is already a command
                commands.append(RepositoryCommand(
                    name="test",
                    command="pytest",
                    category="test",
                    source="convention (pytest config detected)",
                    confidence="MEDIUM",
                ))

        return commands


def get_supported_command_sources() -> List[str]:
    """Return list of command sources this detector supports."""
    return ["package.json", "Makefile", "pyproject.toml", "convention"]
