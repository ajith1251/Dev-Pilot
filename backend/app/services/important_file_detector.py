"""
Important File Detector — identify files likely important for understanding
the repository's architecture and development workflow.
"""

from __future__ import annotations

from typing import Dict, List, Set

from app.models.profile import ImportantFile
from app.services.repository_scanner import ScannedFile

# Files with the highest importance
CRITICAL_FILES: Dict[str, str] = {
    "README.md": "Project documentation entry point",
    "CONTRIBUTING.md": "Contribution guidelines",
    "LICENSE": "License information",
    "package.json": "npm package manifest with dependencies and scripts",
    "pyproject.toml": "Python project metadata and build configuration",
    "requirements.txt": "Python package dependencies",
    "Cargo.toml": "Rust project manifest with dependencies",
    "go.mod": "Go module definition with dependencies",
    "composer.json": "PHP composer manifest",
    "Gemfile": "Ruby Bundler dependency manifest",
    "pom.xml": "Maven project object model",
    "build.gradle": "Gradle build configuration",
    "Dockerfile": "Docker container build definition",
    "docker-compose.yml": "Multi-container Docker setup",
    ".github/workflows": "CI/CD workflow configuration",
    "Makefile": "Build automation targets",
    "tsconfig.json": "TypeScript compiler configuration",
    "next.config.js": "Next.js framework configuration",
    "vite.config.ts": "Vite build tool configuration",
    "jest.config.js": "Jest test runner configuration",
    ".gitignore": "Git ignore patterns",
    ".env.example": "Environment variable template",
    "app/main.py": "Application entry point (Python/FastAPI)",
    "main.py": "Application entry point (Python)",
    "app.js": "Application entry point (Node.js)",
    "index.js": "Application entry point (Node.js)",
    "manage.py": "Django management script",
    "setup.py": "Python package setup script",
    "webpack.config.js": "Webpack build configuration",
}

# Extension-specific importance reasons
EXTENSION_IMPORTANCE: Dict[str, str] = {
    ".py": "Python source file",
    ".js": "JavaScript source file",
    ".ts": "TypeScript source file",
    ".tsx": "React TypeScript component",
    ".jsx": "React JavaScript component",
}

# Important directory names
IMPORTANT_DIRS: Dict[str, str] = {
    "routes": "API route definitions",
    "api": "API endpoint definitions",
    "controllers": "Controller/handler logic",
    "services": "Business logic layer",
    "models": "Data models and schemas",
    "schemas": "Data validation schemas",
    "middleware": "Middleware components",
    "migrations": "Database migration files",
    "db": "Database configuration",
    "config": "Configuration files",
    "utils": "Utility/helper modules",
    "components": "UI components",
    "pages": "Page components (Next.js)",
    "app": "Application code (Next.js 13+)",
}


class ImportantFileDetector:
    """Identify important files for understanding repository architecture."""

    def detect(
        self,
        files: List[ScannedFile],
        file_names: Set[str],
    ) -> List[ImportantFile]:
        """Identify important files.

        Args:
            files: List of all scanned files.
            file_names: Set of all file names.

        Returns:
            List of ImportantFile sorted by score descending.
        """
        important: List[ImportantFile] = []
        seen: Set[str] = set()

        # Check critical files by name
        for name, reason in CRITICAL_FILES.items():
            if name.endswith("/"):  # It's a directory pattern
                has_match = any(f.path.startswith(name.rstrip("/")) for f in files)
                if has_match:
                    important.append(ImportantFile(
                        path=name.rstrip("/"),
                        reason=reason,
                        score=1.0,
                    ))
                    seen.add(name.rstrip("/"))
            elif name in file_names:
                # Find the actual path
                for f in files:
                    if f.name == name and f.path not in seen:
                        important.append(ImportantFile(
                            path=f.path,
                            reason=reason,
                            score=0.95,
                        ))
                        seen.add(f.path)
                        break

        # Check important directories
        for f in files:
            path_lower = f.path.lower()
            for dir_name, reason in IMPORTANT_DIRS.items():
                if f"/{dir_name}/" in f"/{path_lower}/" or f.path == dir_name:
                    if f.path not in seen and f.extension in {".py", ".js", ".ts", ".tsx", ".go", ".rs", ".java"}:
                        important.append(ImportantFile(
                            path=f.path,
                            reason=reason,
                            score=0.7,
                        ))
                        seen.add(f.path)
                        break

        # Detect entry points by common names
        for f in files:
            if f.path not in seen and f.name in (
                "main.py", "app.py", "cli.py", "index.js", "index.ts",
                "server.js", "server.ts", "main.go", "main.rs", "main.java",
                "Main.java", "Program.cs",
            ):
                score = 0.9 if f.depth <= 2 else 0.6
                important.append(ImportantFile(
                    path=f.path,
                    reason=f"Likely application entry point ({f.name})",
                    score=score,
                ))
                seen.add(f.path)

        return sorted(important, key=lambda x: x.score, reverse=True)
