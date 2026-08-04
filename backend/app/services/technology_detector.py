"""
Technology Detector — deterministic framework/library detection from evidence.

No LLM calls. Detects technologies from manifest contents, file names,
and configuration patterns.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Set

from app.models.profile import TechnologyDetection

# Evidence patterns: { technology_name: { category, check_function } }

class TechnologyDetector:
    """Detect frameworks, libraries, and tools from repository files."""

    def __init__(self, root_path: str) -> None:
        self._root = Path(root_path)

    def detect(
        self,
        file_names: Set[str],
        file_paths: Set[str],
        manifests: Dict[str, Optional[str]],
    ) -> List[TechnologyDetection]:
        """Detect technologies based on file evidence and manifest content.

        Args:
            file_names: Set of all file names in the repo.
            file_paths: Set of all relative file paths.
            manifests: Dict of {filename: content_or_none} for parsed manifests.

        Returns:
            List of TechnologyDetection sorted by confidence.
        """
        found: Dict[str, TechnologyDetection] = {}

        # ── Frontend frameworks ─────────────────────────────────
        if "next.config.js" in file_names or "next.config.ts" in file_names:
            self._add(found, "Next.js", "frontend", "HIGH", ["next.config.js / next.config.ts"])
        if "nuxt.config.js" in file_names or "nuxt.config.ts" in file_names:
            self._add(found, "Nuxt.js", "frontend", "HIGH", ["nuxt.config.js"])
        if "vite.config.js" in file_names or "vite.config.ts" in file_names:
            self._add(found, "Vite", "frontend", "HIGH", ["vite.config.js / vite.config.ts"])
        if "angular.json" in file_names:
            self._add(found, "Angular", "frontend", "HIGH", ["angular.json"])
        if "svelte.config.js" in file_names:
            self._add(found, "Svelte", "frontend", "HIGH", ["svelte.config.js"])
        if "vue.config.js" in file_names:
            self._add(found, "Vue.js", "frontend", "HIGH", ["vue.config.js"])

        # Check package.json for frontend deps
        pkg_json = self._safe_json_parse(manifests.get("package.json"))
        if pkg_json:
            deps = {**pkg_json.get("dependencies", {}), **pkg_json.get("devDependencies", {})}
            self._check_package_deps(found, deps)

        # ── Backend frameworks ──────────────────────────────────
        if "manage.py" in file_names:
            self._add(found, "Django", "backend", "HIGH", ["manage.py"])
        if "wsgi.py" in file_names:
            # Check for more specific framework from file names
            pass
        for path in file_paths:
            if path.endswith("asgi.py"):
                self._add(found, "ASGI Server", "backend", "MEDIUM", [path])
                break

        # Check pyproject.toml for Python deps
        pyproject = manifests.get("pyproject.toml")
        if pyproject:
            if "fastapi" in pyproject.lower() or "[project.urls]" in pyproject:
                if "fastapi" in pyproject.lower():
                    self._add(found, "FastAPI", "backend", "HIGH", ["pyproject.toml dependency"])
            if "flask" in pyproject.lower():
                self._add(found, "Flask", "backend", "HIGH", ["pyproject.toml dependency"])
            if "django" in pyproject.lower():
                self._add(found, "Django", "backend", "HIGH", ["pyproject.toml dependency"])

        # Check requirements.txt for Python deps
        req_txt = manifests.get("requirements.txt", "")
        if req_txt:
            if "fastapi" in req_txt.lower():
                self._add(found, "FastAPI", "backend", "HIGH", ["requirements.txt"])
            if "flask" in req_txt.lower():
                self._add(found, "Flask", "backend", "HIGH", ["requirements.txt"])
            if "django" in req_txt.lower():
                self._add(found, "Django", "backend", "HIGH", ["requirements.txt"])

        # ── CSS frameworks ──────────────────────────────────────
        if "tailwind.config.js" in file_names or "tailwind.config.ts" in file_names:
            self._add(found, "Tailwind CSS", "frontend", "HIGH", ["tailwind.config.*"])
        if "postcss.config.js" in file_names:
            if found.get("Tailwind CSS") is None:
                pass  # PostCSS alone isn't enough for detection

        # ── Testing frameworks ──────────────────────────────────
        if "vitest.config.ts" in file_names or "vitest.config.js" in file_names:
            self._add(found, "Vitest", "testing", "HIGH", ["vitest.config.*"])
        if "jest.config.js" in file_names or "jest.config.ts" in file_names:
            self._add(found, "Jest", "testing", "HIGH", ["jest.config.*"])
        if "pytest.ini" in file_names or "setup.cfg" in file_names:
            self._add(found, "pytest", "testing", "MEDIUM", ["pytest config"])
        if "pyproject.toml" in file_names:
            if pyproject and "[tool.pytest" in pyproject:
                self._add(found, "pytest", "testing", "HIGH", ["pyproject.toml [tool.pytest]"])
        if "playwright.config.ts" in file_names or "playwright.config.js" in file_names:
            self._add(found, "Playwright", "testing", "HIGH", ["playwright.config.*"])
        if "cypress.config.ts" in file_names or "cypress.config.js" in file_names:
            self._add(found, "Cypress", "testing", "HIGH", ["cypress.config.*"])

        # ── Build tools ──────────────────────────────────────────
        if "webpack.config.js" in file_names:
            self._add(found, "Webpack", "build_tool", "HIGH", ["webpack.config.js"])
        if "rollup.config.js" in file_names:
            self._add(found, "Rollup", "build_tool", "HIGH", ["rollup.config.js"])
        if "esbuild.config.js" in file_names:
            self._add(found, "esbuild", "build_tool", "HIGH", ["esbuild.config.js"])
        if "Makefile" in file_names:
            self._add(found, "Make", "build_tool", "HIGH", ["Makefile"])

        # ── Infrastructure ──────────────────────────────────────
        if "Dockerfile" in file_names:
            self._add(found, "Docker", "devops", "HIGH", ["Dockerfile"])
        if "docker-compose.yml" in file_names or "docker-compose.yaml" in file_names:
            self._add(found, "Docker Compose", "devops", "HIGH", ["docker-compose.yml"])
        for path in file_paths:
            if ".github/workflows" in path and path.endswith(".yml"):
                self._add(found, "GitHub Actions", "devops", "HIGH", [path])
                break

        # ── Databases / ORMs ─────────────────────────────────────
        if pyproject:
            for db in ["sqlalchemy", "psycopg", "asyncpg", "aiomysql", "tortoise-orm"]:
                if db in pyproject.lower():
                    name = db.replace("-", " ").title()
                    self._add(found, name, "database", "INFERRED", [f"pyproject.toml dependency: {db}"])
        if pkg_json:
            for db in ["prisma", "typeorm", "sequelize", "mongoose", "knex"]:
                if db in pkg_json.get("dependencies", {}):
                    self._add(found, db.title(), "database", "HIGH", [f"package.json dependency: {db}"])
                if db in pkg_json.get("devDependencies", {}):
                    self._add(found, db.title(), "database", "MEDIUM", [f"package.json devDependency: {db}"])

        return sorted(found.values(), key=lambda x: self._confidence_score(x.confidence), reverse=True)

    def _add(
        self,
        found: Dict[str, TechnologyDetection],
        name: str,
        category: str,
        confidence: str,
        evidence: List[str],
    ) -> None:
        """Add or update a technology detection."""
        if name in found:
            existing = found[name]
            # Upgrade confidence if higher
            if self._confidence_score(confidence) > self._confidence_score(existing.confidence):
                existing.confidence = confidence
            existing.evidence.extend(evidence)
        else:
            found[name] = TechnologyDetection(
                name=name,
                category=category,
                confidence=confidence,
                evidence=evidence,
            )

    def _check_package_deps(
        self,
        found: Dict[str, TechnologyDetection],
        deps: Dict[str, str],
    ) -> None:
        """Check package.json dependencies for frameworks."""
        # Frontend
        if "react" in deps:
            self._add(found, "React", "frontend", "HIGH", ["package.json dependency: react"])
        if "next" in deps:
            self._add(found, "Next.js", "frontend", "HIGH", ["package.json dependency: next"])
        if "vue" in deps:
            self._add(found, "Vue.js", "frontend", "HIGH", ["package.json dependency: vue"])
        if "nuxt" in deps:
            self._add(found, "Nuxt.js", "frontend", "HIGH", ["package.json dependency: nuxt"])
        if "svelte" in deps:
            self._add(found, "Svelte", "frontend", "HIGH", ["package.json dependency: svelte"])
        if "angular" in deps or "@angular/core" in deps:
            self._add(found, "Angular", "frontend", "HIGH", ["package.json dependency: @angular/core"])

        # Backend
        if "express" in deps:
            self._add(found, "Express", "backend", "HIGH", ["package.json dependency: express"])
        if "fastify" in deps:
            self._add(found, "Fastify", "backend", "HIGH", ["package.json dependency: fastify"])
        if "@nestjs/core" in deps:
            self._add(found, "NestJS", "backend", "HIGH", ["package.json dependency: @nestjs/core"])
        if "next" in deps:
            pass  # Already handled above

        # Testing
        if "jest" in deps:
            self._add(found, "Jest", "testing", "HIGH", ["package.json dependency: jest"])
        if "vitest" in deps:
            self._add(found, "Vitest", "testing", "HIGH", ["package.json dependency: vitest"])
        if "playwright" in deps:
            self._add(found, "Playwright", "testing", "HIGH", ["package.json dependency: playwright"])
        if "cypress" in deps:
            self._add(found, "Cypress", "testing", "HIGH", ["package.json dependency: cypress"])

        # CSS
        if "tailwindcss" in deps:
            self._add(found, "Tailwind CSS", "frontend", "HIGH", ["package.json dependency: tailwindcss"])

    def _safe_json_parse(self, content: Optional[str]) -> Optional[Dict]:
        """Safely parse JSON content."""
        if not content:
            return None
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _confidence_score(confidence: str) -> int:
        return {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "INFERRED": 0}.get(confidence, 0)


def get_supported_technologies() -> List[str]:
    """Return list of technology names this detector can identify."""
    return [
        "Next.js", "Nuxt.js", "Vite", "Angular", "Svelte", "Vue.js",
        "React", "Tailwind CSS",
        "Django", "FastAPI", "Flask", "Express", "Fastify", "NestJS",
        "Jest", "Vitest", "Playwright", "Cypress", "pytest",
        "Docker", "Docker Compose", "GitHub Actions",
        "Webpack", "Rollup", "esbuild", "Make",
        "SQLAlchemy", "Prisma", "TypeORM",
    ]
