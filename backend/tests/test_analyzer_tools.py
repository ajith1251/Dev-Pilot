"""Tests for the repository analyzer tools (no network calls)."""

from __future__ import annotations

import pytest

from app.models.analysis import (
    DirectoryNode,
    FileCategory,
    FileInfo,
    FrameworkCategory,
    FrameworkInfo,
    LanguageInfo,
    RepositoryStructure,
)
from app.tools.analyzer import (
    collect_files,
    compute_max_depth,
    detect_dependencies,
    detect_frameworks,
    detect_languages,
    detect_file_category,
    find_entry_points,
    find_files_by_category,
)


# ── File classification tests ───────────────────────────────────


class TestDetectFileCategory:
    """File category classification."""

    def test_source_python(self) -> None:
        assert detect_file_category("main.py", "src/main.py") == FileCategory.SOURCE

    def test_source_typescript(self) -> None:
        assert detect_file_category("index.ts", "src/index.ts") == FileCategory.SOURCE

    def test_config_requirements(self) -> None:
        assert (
            detect_file_category("requirements.txt", "requirements.txt")
            == FileCategory.CONFIG
        )

    def test_config_package_json(self) -> None:
        assert (
            detect_file_category("package.json", "package.json")
            == FileCategory.CONFIG
        )

    def test_test_file_prefix(self) -> None:
        assert (
            detect_file_category("test_utils.py", "tests/test_utils.py")
            == FileCategory.TEST
        )

    def test_test_file_suffix(self) -> None:
        assert (
            detect_file_category("utils_test.py", "tests/utils_test.py")
            == FileCategory.TEST
        )

    def test_documentation_markdown(self) -> None:
        assert (
            detect_file_category("README.md", "README.md") == FileCategory.DOCUMENTATION
        )

    def test_build_dockerfile(self) -> None:
        """Dockerfile (no extension) should be classified as BUILD."""
        result = detect_file_category("Dockerfile", "Dockerfile")
        assert result == FileCategory.BUILD, f"Expected BUILD, got {result}"

    def test_build_makefile(self) -> None:
        """Makefile (no extension) should be classified as BUILD."""
        assert detect_file_category("Makefile", "Makefile") == FileCategory.BUILD

    def test_script_shell(self) -> None:
        assert detect_file_category("deploy.sh", "deploy.sh") == FileCategory.SCRIPT

    def test_unknown_binary(self) -> None:
        assert (
            detect_file_category("data.bin", "data.bin") == FileCategory.UNKNOWN
        )

    def test_entry_point(self) -> None:
        assert (
            detect_file_category("manage.py", "manage.py") == FileCategory.SOURCE
        )

    def test_vue_template(self) -> None:
        assert (
            detect_file_category("App.vue", "src/App.vue") == FileCategory.TEMPLATE
        )


# ── Language detection tests ────────────────────────────────────


class TestDetectLanguages:
    """Language detection from file lists."""

    def test_empty_list(self) -> None:
        assert detect_languages([]) == []

    def test_single_language(self) -> None:
        files = [
            FileInfo(path="main.py", name="main.py", extension=".py", language="Python"),
            FileInfo(
                path="utils.py", name="utils.py", extension=".py", language="Python"
            ),
        ]
        langs = detect_languages(files)
        assert len(langs) == 1
        assert langs[0].name == "Python"
        assert langs[0].file_count == 2

    def test_multiple_languages(self) -> None:
        files = [
            FileInfo(path="main.py", name="main.py", extension=".py", language="Python"),
            FileInfo(path="app.ts", name="app.ts", extension=".ts", language="TypeScript"),
            FileInfo(path="helper.py", name="helper.py", extension=".py", language="Python"),
            FileInfo(path="style.css", name="style.css", extension=".css", language="CSS"),
        ]
        langs = detect_languages(files)
        assert len(langs) == 3
        # Python should be first (2 files)
        assert langs[0].name == "Python"
        assert langs[0].file_count == 2

    def test_binary_files_ignored(self) -> None:
        files = [
            FileInfo(
                path="main.py",
                name="main.py",
                extension=".py",
                language="Python",
            ),
            FileInfo(
                path="image.png",
                name="image.png",
                extension=".png",
                language=None,
                is_binary=True,
            ),
        ]
        langs = detect_languages(files)
        assert len(langs) == 1
        assert langs[0].name == "Python"


# ── Framework detection tests ────────────────────────────────────


class TestDetectFrameworks:
    """Framework detection from files and dependencies."""

    def test_django_detected(self) -> None:
        files = [FileInfo(path="manage.py", name="manage.py", extension=".py")]
        fw = detect_frameworks(files, [])
        names = [f.name for f in fw]
        assert "Django" in names

    def test_nextjs_detected(self) -> None:
        files = [
            FileInfo(
                path="next.config.js", name="next.config.js", extension=".js"
            )
        ]
        fw = detect_frameworks(files, [])
        names = [f.name for f in fw]
        assert "Next.js" in names

    def test_multiple_frameworks(self) -> None:
        files = [
            FileInfo(path="manage.py", name="manage.py", extension=".py"),
            FileInfo(path="Dockerfile", name="Dockerfile", extension=""),
            FileInfo(path="requirements.txt", name="requirements.txt", extension=".txt"),
        ]
        fw = detect_frameworks(files, [])
        names = {f.name for f in fw}
        assert "Django" in names
        assert "Docker" in names
        assert "pip" in names
        assert "npm" not in names  # no package.json

    def test_github_actions_detected(self) -> None:
        files = [
            FileInfo(
                path=".github/workflows/ci.yml",
                name="ci.yml",
                extension=".yml",
            )
        ]
        fw = detect_frameworks(files, [])
        names = [f.name for f in fw]
        assert "GitHub Actions" in names

    def test_empty_files(self) -> None:
        assert detect_frameworks([], []) == []


# ── Dependency detection tests ──────────────────────────────────


class TestDetectDependencies:
    """Dependency detection from config files."""

    def test_pip_detected(self) -> None:
        files = [
            FileInfo(
                path="requirements.txt",
                name="requirements.txt",
                extension=".txt",
            )
        ]
        deps = detect_dependencies(files)
        assert len(deps) >= 1
        assert any(d.manager == "pip" for d in deps)

    def test_npm_detected(self) -> None:
        files = [
            FileInfo(
                path="package.json",
                name="package.json",
                extension=".json",
            )
        ]
        deps = detect_dependencies(files)
        assert any(d.manager == "npm" for d in deps)

    def test_multiple_managers(self) -> None:
        files = [
            FileInfo(
                path="requirements.txt",
                name="requirements.txt",
                extension=".txt",
            ),
            FileInfo(path="package.json", name="package.json", extension=".json"),
            FileInfo(path="Cargo.toml", name="Cargo.toml", extension=".toml"),
        ]
        deps = detect_dependencies(files)
        managers = {d.manager for d in deps}
        assert managers == {"pip", "npm", "cargo"}


# ── Tree operations tests ───────────────────────────────────────


class TestCollectFiles:
    """File collection from directory tree."""

    def test_empty_tree(self) -> None:
        node = DirectoryNode(path="", name="root")
        assert collect_files(node) == []

    def test_single_directory(self) -> None:
        node = DirectoryNode(
            path="",
            name="root",
            files=[
                FileInfo(path="a.py", name="a.py", extension=".py"),
                FileInfo(path="b.py", name="b.py", extension=".py"),
            ],
        )
        files = collect_files(node)
        assert len(files) == 2

    def test_nested_directories(self) -> None:
        inner = DirectoryNode(
            path="src",
            name="src",
            files=[FileInfo(path="src/main.py", name="main.py", extension=".py")],
        )
        root = DirectoryNode(
            path="",
            name="root",
            files=[FileInfo(path="README.md", name="README.md", extension=".md")],
            directories=[inner],
        )
        files = collect_files(root)
        assert len(files) == 2
        paths = {f.path for f in files}
        assert "README.md" in paths
        assert "src/main.py" in paths


class TestFindFilesByCategory:
    """File filtering by category."""

    def test_filter_source(self) -> None:
        files = [
            FileInfo(
                path="main.py", name="main.py", extension=".py",
                category=FileCategory.SOURCE,
            ),
            FileInfo(
                path="requirements.txt", name="requirements.txt",
                extension=".txt", category=FileCategory.CONFIG,
            ),
            FileInfo(
                path="test_main.py", name="test_main.py", extension=".py",
                category=FileCategory.TEST,
            ),
        ]
        src = find_files_by_category(files, FileCategory.SOURCE)
        assert len(src) == 1
        assert src[0].name == "main.py"

    def test_empty_list(self) -> None:
        assert find_files_by_category([], FileCategory.SOURCE) == []

    def test_no_match(self) -> None:
        files = [
            FileInfo(
                path="cfg.json", name="cfg.json", extension=".json",
                category=FileCategory.CONFIG,
            )
        ]
        assert find_files_by_category(files, FileCategory.TEST) == []


class TestFindEntryPoints:
    """Entry point detection."""

    def test_known_entry_point(self) -> None:
        files = [
            FileInfo(path="main.py", name="main.py", extension=".py"),
            FileInfo(path="utils.py", name="utils.py", extension=".py"),
        ]
        eps = find_entry_points(files)
        names = {f.name for f in eps}
        assert "main.py" in names

    def test_no_known_entry_points(self) -> None:
        """Root-level source files should be returned as potential entry points."""
        files = [
            FileInfo(
                path="utils.py", name="utils.py", extension=".py",
                category=FileCategory.SOURCE,
            ),
            FileInfo(
                path="helper.js", name="helper.js", extension=".js",
                category=FileCategory.SOURCE,
            ),
        ]
        eps = find_entry_points(files)
        # Both are root-level source files
        assert len(eps) == 2
        names = {f.name for f in eps}
        assert "utils.py" in names
        assert "helper.js" in names

    def test_known_entry_point_with_category(self) -> None:
        """Entry point known by name should be detected regardless of category."""
        files = [
            FileInfo(path="manage.py", name="manage.py", extension=".py"),
            FileInfo(path="app.py", name="app.py", extension=".py"),
        ]
        eps = find_entry_points(files)
        names = {f.name for f in eps}
        assert "manage.py" in names
        assert "app.py" in names


class TestComputeMaxDepth:
    """Directory tree depth computation."""

    def test_single_node(self) -> None:
        node = DirectoryNode(path="", name="root")
        assert compute_max_depth(node) == 0

    def test_nested_depth(self) -> None:
        level3 = DirectoryNode(path="a/b/c", name="c")
        level2 = DirectoryNode(path="a/b", name="b", directories=[level3])
        level1 = DirectoryNode(path="a", name="a", directories=[level2])
        root = DirectoryNode(path="", name="root", directories=[level1])

        assert compute_max_depth(root) == 3
