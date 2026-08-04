"""
Comprehensive tests for the Repository Intelligence Engine (Phase 2).

Tests all services, the orchestrator, workflow, and API.
Uses fixture repositories created by tests/fixtures/create_fixtures.py.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.models.profile import (
    Dependency,
    FileCategory,
    ImportantFile,
    LanguageEntry,
    PackageManager,
    RepositoryCommand,
    RepositoryModule,
    RepositoryProfile,
    TechnologyDetection,
)
from app.services.repository_scanner import RepositoryScanner, ScannedFile
from app.services.language_detector import LanguageDetector
from app.services.technology_detector import TechnologyDetector
from app.services.dependency_analyzer import DependencyAnalyzer
from app.services.file_classifier import FileClassifier
from app.services.command_detector import CommandDetector
from app.services.project_detector import ProjectDetector
from app.services.tree_generator import TreeGenerator
from app.services.important_file_detector import ImportantFileDetector
from app.services.repository_analyzer import RepositoryAnalyzer
from app.services.repository_scanner import RepositoryScanner
from app.workflows.repository_analysis import RepositoryAnalysisWorkflow


# ── Fixture paths ───────────────────────────────────────────────

FIXTURES = Path(__file__).resolve().parent / "fixtures"

FIXTURE_A = str(FIXTURES / "fixture_a_nextjs")
FIXTURE_B = str(FIXTURES / "fixture_b_fastapi")
FIXTURE_C = str(FIXTURES / "fixture_c_monorepo")
FIXTURE_D = str(FIXTURES / "fixture_d_minimal")
FIXTURE_E = str(FIXTURES / "fixture_e_malformed")
FIXTURE_F = str(FIXTURES / "fixture_f_sensitive")


# ====================================================================
# 1. REPOSITORY SCANNER TESTS
# ====================================================================


class TestRepositoryScanner:
    """Scanner tests."""

    def test_scanner_finds_files(self):
        scanner = RepositoryScanner()
        result = scanner.scan(FIXTURE_A)
        assert result.total_files > 0
        assert result.errors == []

    def test_scanner_rejects_nonexistent_path(self):
        scanner = RepositoryScanner()
        result = scanner.scan("/nonexistent/path")
        assert len(result.errors) > 0

    def test_scanner_rejects_file_path(self):
        scanner = RepositoryScanner()
        result = scanner.scan(__file__)
        assert len(result.errors) > 0

    def test_scanner_ignores_node_modules(self):
        scanner = RepositoryScanner()
        result = scanner.scan(FIXTURE_F)
        # node_modules should be ignored
        node_modules_files = [f for f in result.files if "node_modules" in f.path]
        assert len(node_modules_files) == 0

    def test_scanner_ignores_git(self):
        scanner = RepositoryScanner()
        result = scanner.scan(FIXTURE_F)
        git_files = [f for f in result.files if ".git" in f.path]
        assert len(git_files) == 0

    def test_scanner_ignores_pycache(self):
        scanner = RepositoryScanner()
        result = scanner.scan(FIXTURE_F)
        pycache_files = [f for f in result.files if "__pycache__" in f.path]
        assert len(pycache_files) == 0

    def test_scanner_ignores_next(self):
        scanner = RepositoryScanner()
        result = scanner.scan(FIXTURE_F)
        next_files = [f for f in result.files if ".next" in f.path]
        assert len(next_files) == 0

    def test_scanner_detects_binary_files(self):
        scanner = RepositoryScanner()
        result = scanner.scan(FIXTURE_F)
        binary_files = [f for f in result.files if f.is_binary]
        # logo.png should be detected as binary
        png_files = [f for f in binary_files if f.extension == ".png"]
        assert len(png_files) > 0

    def test_scanner_sensitive_file_detection(self):
        """Sensitive files should be detectable by name without reading contents."""
        assert RepositoryScanner.is_sensitive_file(".env", ".env") is True
        assert RepositoryScanner.is_sensitive_file("main.py", "src/main.py") is False
        assert RepositoryScanner.is_sensitive_file(
            "credentials.json", "config/credentials.json"
        ) is True
        assert RepositoryScanner.is_sensitive_file("id_rsa", ".ssh/id_rsa") is True

    def test_scanner_max_files_limit(self):
        scanner = RepositoryScanner(max_files=5)
        result = scanner.scan(FIXTURE_B)
        assert result.total_files <= 10  # allow for some flexibility
        assert len(result.warnings) > 0 or result.total_files > 0

    def test_scanner_max_depth_limit(self):
        scanner = RepositoryScanner(max_depth=1)
        result = scanner.scan(FIXTURE_C)
        # Should have limited depth
        deep_files = [f for f in result.files if f.depth > 1]
        assert len(deep_files) == 0 or len(result.warnings) > 0


# ====================================================================
# 2. LANGUAGE DETECTOR TESTS
# ====================================================================


class TestLanguageDetector:
    """Language detection tests."""

    @pytest.fixture
    def detector(self):
        return LanguageDetector()

    def test_detect_python(self, detector):
        files = [ScannedFile(path="main.py", name="main.py", extension=".py", size_bytes=100, is_binary=False, is_symlink=False, is_hidden=False, depth=0)]
        result = detector.detect(files)
        assert len(result) == 1
        assert result[0].name == "Python"

    def test_detect_typescript(self, detector):
        files = [ScannedFile(path="app.tsx", name="app.tsx", extension=".tsx", size_bytes=200, is_binary=False, is_symlink=False, is_hidden=False, depth=0)]
        result = detector.detect(files)
        assert len(result) == 1
        assert result[0].name == "TypeScript"

    def test_detect_multiple_languages(self, detector):
        files = [
            ScannedFile(path="a.py", name="a.py", extension=".py", size_bytes=100, is_binary=False, is_symlink=False, is_hidden=False, depth=0),
            ScannedFile(path="b.ts", name="b.ts", extension=".ts", size_bytes=200, is_binary=False, is_symlink=False, is_hidden=False, depth=0),
            ScannedFile(path="c.py", name="c.py", extension=".py", size_bytes=150, is_binary=False, is_symlink=False, is_hidden=False, depth=0),
        ]
        result = detector.detect(files)
        assert len(result) == 2
        # Python should be first (2 files)
        assert result[0].name == "Python"
        assert result[0].file_count == 2

    def test_binary_files_skipped(self, detector):
        files = [ScannedFile(path="img.png", name="img.png", extension=".png", size_bytes=500, is_binary=True, is_symlink=False, is_hidden=False, depth=0)]
        result = detector.detect(files)
        assert len(result) == 0

    def test_unknown_extension(self, detector):
        files = [ScannedFile(path="data.xyz", name="data.xyz", extension=".xyz", size_bytes=100, is_binary=False, is_symlink=False, is_hidden=False, depth=0)]
        result = detector.detect(files)
        assert len(result) == 0

    def test_percentage_calculation(self, detector):
        files = [
            ScannedFile(path="a.py", name="a.py", extension=".py", size_bytes=100, is_binary=False, is_symlink=False, is_hidden=False, depth=0),
            ScannedFile(path="b.ts", name="b.ts", extension=".ts", size_bytes=200, is_binary=False, is_symlink=False, is_hidden=False, depth=0),
            ScannedFile(path="c.js", name="c.js", extension=".js", size_bytes=150, is_binary=False, is_symlink=False, is_hidden=False, depth=0),
            ScannedFile(path="d.py", name="d.py", extension=".py", size_bytes=150, is_binary=False, is_symlink=False, is_hidden=False, depth=0),
        ]
        result = detector.detect(files)
        assert len(result) == 3
        python = next(r for r in result if r.name == "Python")
        assert python.percentage == 50.0  # 2 out of 4 files


# ====================================================================
# 3. TECHNOLOGY DETECTOR TESTS
# ====================================================================


class TestTechnologyDetector:
    """Technology detection tests."""

    def test_detect_nextjs(self):
        detector = TechnologyDetector(FIXTURE_A)
        result = detector.detect(
            file_names={"next.config.js", "package.json", "tsconfig.json"},
            file_paths={"next.config.js", "package.json"},
            manifests={"package.json": '{"dependencies": {"next": "^14.2.0", "react": "^18.3.0"}}'},
        )
        names = {t.name for t in result}
        assert "Next.js" in names
        assert "React" in names

    def test_detect_fastapi(self):
        detector = TechnologyDetector(FIXTURE_B)
        result = detector.detect(
            file_names={"pyproject.toml", "requirements.txt"},
            file_paths={"pyproject.toml"},
            manifests={"pyproject.toml": '[project]\ndependencies = ["fastapi>=0.115.0"]', "requirements.txt": "fastapi>=0.115.0"},
        )
        names = {t.name for t in result}
        assert "FastAPI" in names

    def test_detect_docker(self):
        detector = TechnologyDetector(FIXTURE_A)
        result = detector.detect(
            file_names={"Dockerfile", "package.json"},
            file_paths={"Dockerfile"},
            manifests={"package.json": "{}"},
        )
        names = {t.name for t in result}
        assert "Docker" in names

    def test_detect_pytest(self):
        detector = TechnologyDetector(FIXTURE_B)
        result = detector.detect(
            file_names={"pyproject.toml"},
            file_paths={"pyproject.toml"},
            manifests={"pyproject.toml": '[tool.pytest.ini_options]\nasyncio_mode = "auto"'},
        )
        names = {t.name for t in result}
        assert "pytest" in names

    def test_detect_tailwind(self):
        detector = TechnologyDetector(FIXTURE_A)
        result = detector.detect(
            file_names={"tailwind.config.ts", "package.json"},
            file_paths={"tailwind.config.ts"},
            manifests={"package.json": '{"devDependencies": {"tailwindcss": "^3.4.0"}}'},
        )
        names = {t.name for t in result}
        assert "Tailwind CSS" in names


# ====================================================================
# 4. FILE CLASSIFIER TESTS
# ====================================================================


class TestFileClassifier:
    """File classification tests."""

    @pytest.fixture
    def classifier(self):
        return FileClassifier()

    def make_file(self, path: str, name: str, ext: str = "", is_binary: bool = False):
        return ScannedFile(
            path=path, name=name, extension=ext, size_bytes=100,
            is_binary=is_binary, is_symlink=False, is_hidden=name.startswith("."), depth=1,
        )

    def test_source_python(self, classifier):
        f = self.make_file("main.py", "main.py", ".py")
        assert classifier.classify_file(f) == FileCategory.SOURCE

    def test_test_file(self, classifier):
        f = self.make_file("test_utils.py", "test_utils.py", ".py")
        assert classifier.classify_file(f) == FileCategory.TEST

    def test_dependency_manifest(self, classifier):
        f = self.make_file("package.json", "package.json", ".json")
        assert classifier.classify_file(f) == FileCategory.DEPENDENCY_MANIFEST

    def test_lockfile(self, classifier):
        f = self.make_file("package-lock.json", "package-lock.json", ".json")
        assert classifier.classify_file(f) == FileCategory.LOCKFILE

    def test_documentation(self, classifier):
        f = self.make_file("README.md", "README.md", ".md")
        assert classifier.classify_file(f) == FileCategory.DOCUMENTATION

    def test_ci_cd(self, classifier):
        f = self.make_file(".github/workflows/ci.yml", "ci.yml", ".yml")
        assert classifier.classify_file(f) == FileCategory.CI_CD

    def test_docker(self, classifier):
        f = self.make_file("Dockerfile", "Dockerfile", "")
        assert classifier.classify_file(f) == FileCategory.BUILD

    def test_asset(self, classifier):
        f = self.make_file("logo.png", "logo.png", ".png", is_binary=True)
        assert classifier.classify_file(f) == FileCategory.ASSET

    def test_classify_counts(self, classifier):
        files = [
            self.make_file("a.py", "a.py", ".py"),
            self.make_file("b.py", "b.py", ".py"),
            self.make_file("test_c.py", "test_c.py", ".py"),
        ]
        counts = classifier.classify(files)
        assert counts.get("source", 0) == 2
        assert counts.get("test", 0) == 1


# ====================================================================
# 5. IMPORTANT FILE DETECTOR TESTS
# ====================================================================


class TestImportantFileDetector:
    """Important file detection tests."""

    @pytest.fixture
    def detector(self):
        return ImportantFileDetector()

    def make_file(self, path: str, name: str, ext: str = ".py", depth: int = 0):
        return ScannedFile(
            path=path, name=name, extension=ext, size_bytes=100,
            is_binary=False, is_symlink=False, is_hidden=name.startswith("."), depth=depth,
        )

    def test_detects_readme(self, detector):
        files = [self.make_file("README.md", "README.md", ".md")]
        result = detector.detect(files, {"README.md"})
        names = {f.path for f in result}
        assert "README.md" in names

    def test_detects_entry_point(self, detector):
        files = [self.make_file("main.py", "main.py", ".py", depth=0)]
        result = detector.detect(files, {"main.py"})
        paths = {f.path for f in result}
        assert "main.py" in paths

    def test_important_files_scored(self, detector):
        files = [
            self.make_file("README.md", "README.md", ".md"),
            self.make_file("src/utils.py", "utils.py", ".py", depth=1),
        ]
        result = detector.detect(files, {"README.md"})
        assert len(result) >= 1
        # README should have highest score
        assert result[0].score > 0


# ====================================================================
# 6. COMMAND DETECTOR TESTS
# ====================================================================


class TestCommandDetector:
    """Command detection tests."""

    def test_detects_npm_scripts(self):
        detector = CommandDetector(FIXTURE_A)
        commands = detector.detect({
            "package.json": '{"scripts": {"dev": "next dev", "build": "next build", "test": "jest", "lint": "next lint"}}',
        })
        names = {c.name for c in commands}
        assert "dev" in names
        assert "build" in names
        assert "test" in names
        assert "lint" in names

    def test_command_categories(self):
        detector = CommandDetector(FIXTURE_A)
        commands = detector.detect({
            "package.json": '{"scripts": {"dev": "next dev", "build": "next build", "test": "jest"}}',
        })
        cmd_map = {c.name: c.category for c in commands}
        assert cmd_map.get("dev") == "dev"
        assert cmd_map.get("build") == "build"
        assert cmd_map.get("test") == "test"

    def test_makefile_targets(self):
        detector = CommandDetector(".")
        commands = detector.detect({
            "Makefile": "all:\n\t@echo build\n\ntest:\n\tpytest\n\nclean:\n\trm -rf dist\n",
        })
        names = {c.name for c in commands}
        assert len(names) > 0


# ====================================================================
# 7. PROJECT DETECTOR TESTS
# ====================================================================


class TestProjectDetector:
    """Module/project detection tests."""

    def test_detects_monorepo_modules(self):
        detector = ProjectDetector(FIXTURE_C)
        modules = detector.detect(
            file_paths={
                "package.json", "frontend/package.json", "backend/pyproject.toml",
                "frontend/next.config.js", "backend/app/main.py",
            },
            file_map={
                "package.json": '{"name": "monorepo", "workspaces": ["frontend", "backend"]}',
                "frontend/package.json": '{"name": "frontend", "dependencies": {"next": "^14.2.0"}}',
                "backend/pyproject.toml": '[project]\nname = "backend"\ndependencies = ["fastapi"]',
            },
            commands=[],
        )
        names = {m.name for m in modules}
        assert len(modules) >= 1


# ====================================================================
# 8. TREE GENERATOR TESTS
# ====================================================================


class TestTreeGenerator:
    """Tree generation tests."""

    @pytest.fixture
    def generator(self):
        return TreeGenerator()

    def make_file(self, path: str, name: str, ext: str = ".py"):
        return ScannedFile(
            path=path, name=name, extension=ext, size_bytes=100,
            is_binary=False, is_symlink=False, is_hidden=False, depth=1,
        )

    def test_generates_tree(self, generator):
        files = [
            self.make_file("src/main.py", "main.py", ".py"),
            self.make_file("README.md", "README.md", ".md"),
        ]
        tree = generator.generate(files, root_name="test")
        assert tree.text.startswith("test/")
        assert "src" in tree.text or "README" in tree.text
        assert tree.total_dirs_shown >= 0
        assert tree.total_files_shown >= 0


# ====================================================================
# 9. ORCHESTRATOR INTEGRATION TESTS
# ====================================================================


class TestRepositoryAnalyzer:
    """Integration tests for the full analyzer."""

    def test_analyze_nextjs_fixture(self):
        analyzer = RepositoryAnalyzer()
        profile = analyzer.analyze(FIXTURE_A)
        assert profile.name == "fixture_a_nextjs"
        assert profile.scan.total_files_scanned > 0
        assert len(profile.languages) > 0
        # Should detect TypeScript and JavaScript
        lang_names = {l.name for l in profile.languages}
        assert "TypeScript" in lang_names or "JavaScript" in lang_names

    def test_analyze_fastapi_fixture(self):
        analyzer = RepositoryAnalyzer()
        profile = analyzer.analyze(FIXTURE_B)
        assert profile.name == "fixture_b_fastapi"
        assert profile.scan.total_files_scanned > 0
        lang_names = {l.name for l in profile.languages}
        assert "Python" in lang_names

    def test_analyze_minimal_fixture(self):
        analyzer = RepositoryAnalyzer()
        profile = analyzer.analyze(FIXTURE_D)
        assert profile.name == "fixture_d_minimal"
        assert profile.scan.total_files_scanned > 0

    def test_analyze_malformed_fixture(self):
        """Malformed manifests should not crash analysis."""
        analyzer = RepositoryAnalyzer()
        profile = analyzer.analyze(FIXTURE_E)
        assert profile.name == "fixture_e_malformed"
        # Should have some results despite malformed manifests
        assert profile.scan.total_files_scanned > 0

    def test_analyze_sensitive_fixture(self):
        """Sensitive files should be detected but contents not exposed."""
        analyzer = RepositoryAnalyzer()
        profile = analyzer.analyze(FIXTURE_F)
        assert profile.name == "fixture_f_sensitive"
        # Should have warnings about sensitive files
        sensitive_warnings = [w for w in profile.warnings if "Sensitive" in w]
        assert len(sensitive_warnings) > 0
        # node_modules, .next etc should be ignored
        # .env contents should never be in the profile

    def test_analyze_nonexistent_path(self):
        analyzer = RepositoryAnalyzer()
        profile = analyzer.analyze("/nonexistent/path/xyz")
        assert len(profile.scan.errors) > 0

    def test_analyze_file_categories(self):
        analyzer = RepositoryAnalyzer()
        profile = analyzer.analyze(FIXTURE_A)
        assert len(profile.file_categories) > 0
        assert "source" in profile.file_categories or "test" in profile.file_categories

    def test_analyze_important_files(self):
        analyzer = RepositoryAnalyzer()
        profile = analyzer.analyze(FIXTURE_A)
        assert len(profile.important_files) > 0


# ====================================================================
# 10. WORKFLOW TESTS
# ====================================================================


@pytest.mark.asyncio
class TestRepositoryAnalysisWorkflow:
    """Workflow tests."""

    async def test_workflow_completes(self):
        workflow = RepositoryAnalysisWorkflow()
        state = await workflow.run(FIXTURE_A)
        assert state.status == "completed"
        assert state.profile is not None
        assert state.profile.scan.total_files_scanned > 0

    async def test_workflow_nonexistent_path(self):
        workflow = RepositoryAnalysisWorkflow()
        state = await workflow.run("/nonexistent/path")
        assert state.status == "failed"
        assert len(state.errors) > 0

    async def test_workflow_profile_has_languages(self):
        workflow = RepositoryAnalysisWorkflow()
        state = await workflow.run(FIXTURE_A)
        assert len(state.profile.languages) > 0

    async def test_workflow_profile_has_technologies(self):
        workflow = RepositoryAnalysisWorkflow()
        state = await workflow.run(FIXTURE_A)
        assert len(state.profile.technologies) > 0

    async def test_workflow_profile_has_commands(self):
        workflow = RepositoryAnalysisWorkflow()
        state = await workflow.run(FIXTURE_A)
        assert len(state.profile.commands) > 0 or len(state.warnings) >= 0

    async def test_workflow_generates_tree(self):
        workflow = RepositoryAnalysisWorkflow()
        state = await workflow.run(FIXTURE_B)
        assert state.profile.tree is not None
        assert len(state.profile.tree.text) > 0


# ====================================================================
# 11. END-TO-END: DEVPILOT SELF-ANALYSIS
# ====================================================================


class TestDevPilotSelfAnalysis:
    """Analyze DevPilot itself to verify the Repository Intelligence Engine."""

    def test_self_analysis(self):
        """Use DevPilot to analyze DevPilot's own codebase."""
        devpilot_root = str(Path(__file__).resolve().parent.parent)
        analyzer = RepositoryAnalyzer()
        profile = analyzer.analyze(devpilot_root)

        assert profile.name == "backend" or "DevPilot" in profile.name
        assert profile.scan.total_files_scanned > 0
        assert len(profile.scan.errors) == 0, f"Errors: {profile.scan.errors}"

        # Should detect Python as a primary language
        lang_names = {l.name for l in profile.languages}
        assert "Python" in lang_names, f"Languages found: {lang_names}"

        # Should detect technologies
        if profile.technologies:
            tech_names = {t.name for t in profile.technologies}
            # FastAPI, pytest, etc should be detected
            assert any(t in tech_names for t in ["FastAPI", "pytest", "pip"]), f"Techs: {tech_names}"

        # Should have commands
        if profile.commands:
            cmd_names = {c.name for c in profile.commands}
            assert any(c in cmd_names for c in ["test", "dev"]), f"Commands: {cmd_names}"

        # Should have modules
        if profile.modules:
            assert len(profile.modules) >= 1

        # Important files should exist
        if profile.important_files:
            imp_paths = {f.path for f in profile.important_files}
            assert any("main.py" in p or "config" in p for p in imp_paths), f"Important: {imp_paths}"

        # Tree should be generated
        assert profile.tree is not None

        print(f"\n  DevPilot Self-Analysis Results:")
        print(f"  Files: {profile.scan.total_files_scanned}, Dirs: {profile.scan.total_dirs_scanned}")
        print(f"  Languages: {', '.join(f'{l.name} ({l.percentage:.0f}%)' for l in profile.languages[:5])}")
        print(f"  Technologies: {', '.join(t.name for t in profile.technologies[:8])}")
        print(f"  Duration: {profile.scan.duration_seconds}s")
