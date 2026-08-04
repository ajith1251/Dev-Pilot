"""
CodeIntelligenceService — orchestrator for Phase 12 code intelligence.

Coordinates:
- Language-aware parsing
- Semantic repository graph construction
- Persistence of graph/index metadata
- Graph-aware retrieval
- Incremental indexing
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from app.code_intelligence.impact_analyzer import ImpactAnalysisService
from app.code_intelligence.incremental_indexer import FileChange, IncrementalIndexer
from app.code_intelligence.parsers.c_cpp_parser import CppSymbolParser
from app.code_intelligence.parsers.csharp_parser import CSharpSymbolParser
from app.code_intelligence.parsers.go_parser import GoSymbolParser
from app.code_intelligence.parsers.java_parser import JavaSymbolParser
from app.code_intelligence.parsers.kotlin_parser import KotlinSymbolParser
from app.code_intelligence.parsers.php_parser import PhpSymbolParser
from app.code_intelligence.parsers.python_parser import PythonSymbolParser
from app.code_intelligence.parsers.ruby_parser import RubySymbolParser
from app.code_intelligence.parsers.rust_parser import RustSymbolParser
from app.code_intelligence.parsers.swift_parser import SwiftSymbolParser
from app.code_intelligence.parsers.ts_parser import TypeScriptJSParser
from app.code_intelligence.semantic_graph import (
    ConfidenceLevel,
    GraphNode,
    RelationshipType,
    SemanticRepositoryGraph,
    make_symbol_id,
    normalize_qualified_name,
)
from app.config import settings
from app.core.logging import logger


@dataclass
class IndexStats:
    """Statistics for an indexing operation."""

    files_scanned: int = 0
    files_parsed: int = 0
    files_failed: int = 0
    symbols_extracted: int = 0
    edges_created: int = 0
    duration_seconds: float = 0.0
    languages: Dict[str, int] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


@dataclass
class IndexResult:
    """Result of a code intelligence indexing operation."""

    repository_path: str
    repository_id: str
    graph: SemanticRepositoryGraph
    stats: IndexStats
    index_id: str
    content_fingerprint: str


# File extension to language mapping
EXT_TO_LANG: Dict[str, str] = {
    ".py": "Python",
    ".pyi": "Python",
    ".pyx": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript React",
    ".js": "JavaScript",
    ".jsx": "JavaScript React",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".mts": "TypeScript",
    ".cts": "TypeScript",
    ".java": "Java",
    ".go": "Go",
    ".rs": "Rust",
    ".c": "C",
    ".h": "C",
    ".cpp": "C++",
    ".cc": "C++",
    ".cxx": "C++",
    ".hpp": "C++",
    ".hh": "C++",
    ".cs": "C#",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".swift": "Swift",
    ".rb": "Ruby",
    ".php": "PHP",
}

# Directories to skip
SKIP_DIRS: Set[str] = {
    ".git", ".hg", ".svn", "__pycache__", "node_modules",
    ".venv", "venv", "env", ".env", "dist", "build",
    ".next", ".nuxt", ".svelte-kit", "target",
    ".tox", ".eggs", "*.egg-info", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", ".hypothesis",
}

# Files to skip
SKIP_FILES: Set[str] = {
    ".gitignore", ".gitkeep", ".npmrc", ".yarnrc",
    ".editorconfig", ".prettierrc", ".eslintrc",
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
}

# Max file size to parse (500KB)
MAX_FILE_SIZE = 500 * 1024

# Max files to index
MAX_FILES = 10_000


class CodeIntelligenceService:
    """Main orchestrator for Phase 12 code intelligence operations.

    Coordinates parsing, graph construction, persistence, and retrieval.
    """

    def __init__(
        self,
        max_files: int = MAX_FILES,
        max_file_size: int = MAX_FILE_SIZE,
        store: Optional[Any] = None,
        vector_store: Optional[Any] = None,
    ) -> None:
        """Initialize the code intelligence service.

        Args:
            max_files: Maximum number of files to index.
            max_file_size: Maximum file size in bytes.
            store: Optional PostgresRunStore instance for graph persistence.
            vector_store: Optional Phase 15 VectorStore for persisted
                symbol embeddings (gracefully degrades when unavailable).
        """
        self.max_files = max_files
        self.max_file_size = max_file_size
        self._graph: Optional[SemanticRepositoryGraph] = None
        self._index_id: Optional[str] = None
        self._repository_path: Optional[str] = None
        self._repository_id: Optional[str] = None
        self._store: Optional[Any] = store
        self._vector_store: Optional[Any] = vector_store

    # ── Indexing ─────────────────────────────────────────────────

    def index_repository(self, repo_path: str) -> IndexResult:
        """Build a full semantic repository index.

        Args:
            repo_path: Path to the repository.

        Returns:
            IndexResult with graph and statistics.
        """
        start_time = time.time()
        path = Path(repo_path).resolve()

        if not path.is_dir():
            raise ValueError(f"Not a directory: {repo_path}")

        graph = SemanticRepositoryGraph()
        stats = IndexStats()
        repo_id = path.name

        # Discover files
        files = self._discover_files(str(path))
        stats.files_scanned = len(files)

        logger.info("Indexing %d files in %s", len(files), repo_id)

        # Parse each file
        for file_path, language in files:
            if stats.files_parsed >= self.max_files:
                stats.warnings.append("Reached max file limit; some files skipped")
                break

            try:
                full_path = os.path.join(str(path), file_path)
                if self._is_binary(full_path):
                    stats.warnings.append(f"Skipping binary file: {file_path}")
                    continue

                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()

                if len(content) > self.max_file_size:
                    stats.warnings.append(f"Skipping large file: {file_path}")
                    continue

                symbols, relationships, diagnostics = self._parse_file(
                    file_path, content, language
                )

                if diagnostics:
                    stats.warnings.extend(
                        f"{file_path}: {d}" for d in diagnostics
                    )
                    stats.files_failed += 1

                # Add symbols to graph
                for sym in symbols:
                    graph.add_node(sym)
                    stats.symbols_extracted += 1

                # Add relationships to graph
                for rel in relationships:
                    try:
                        graph.add_edge(
                            source_id=rel["source_id"],
                            target_id=rel["target_id"],
                            relationship=RelationshipType(rel["relationship"]),
                            confidence=ConfidenceLevel(rel.get("confidence", "medium")),
                            source_lines=rel.get("source_lines"),
                            resolution_detail=rel.get("resolution_detail"),
                        )
                        stats.edges_created += 1
                    except ValueError as exc:
                        # Source node may not exist (e.g., module-level call)
                        pass

                stats.files_parsed += 1
                stats.languages[language] = stats.languages.get(language, 0) + 1

            except Exception as exc:
                stats.errors.append(f"Error parsing {file_path}: {exc}")
                stats.files_failed += 1

        # Add file-level nodes
        self._add_file_nodes(graph, files)

        # Phase 15 (12d): cross-file symbol resolution — link import nodes
        # to their definitions across files (best effort, never fatal).
        try:
            from app.code_intelligence.symbol_resolver import CrossFileSymbolResolver

            resolver = CrossFileSymbolResolver(graph=graph)
            resolution = resolver.resolve()
            if resolution.edges_added:
                stats.edges_created += resolution.edges_added
        except Exception as exc:
            stats.warnings.append(f"Cross-file symbol resolution skipped: {exc}")

        stats.duration_seconds = round(time.time() - start_time, 3)

        # Generate content fingerprint
        fp_input = f"{repo_id}:{stats.files_parsed}:{stats.symbols_extracted}:{stats.duration_seconds}"
        content_fingerprint = hashlib.sha256(fp_input.encode()).hexdigest()[:16]

        index_id = f"idx_{repo_id}_{int(time.time())}"

        self._repository_id = repo_id
        self._graph = graph
        self._index_id = index_id
        self._repository_path = str(path)

        logger.info(
            "Indexing complete: %d files, %d symbols, %d edges in %.2fs",
            stats.files_parsed, stats.symbols_extracted, stats.edges_created,
            stats.duration_seconds,
        )

        return IndexResult(
            repository_path=str(path),
            repository_id=repo_id,
            graph=graph,
            stats=stats,
            index_id=index_id,
            content_fingerprint=content_fingerprint,
        )

    def _discover_files(self, root_path: str) -> List[Tuple[str, str]]:
        """Discover source files to index.

        Returns list of (relative_path, language) tuples.
        """
        files = []
        root = Path(root_path)

        for dirpath, dirnames, filenames in os.walk(root):
            # Skip ignored directories
            rel_dir = Path(dirpath).relative_to(root).as_posix()
            dirnames[:] = [
                d for d in dirnames
                if d not in SKIP_DIRS and not d.startswith(".")
            ]

            for filename in filenames:
                if filename in SKIP_FILES:
                    continue

                ext = os.path.splitext(filename)[1].lower()
                language = EXT_TO_LANG.get(ext)
                if language is None:
                    continue

                rel_path = os.path.join(rel_dir, filename).replace("\\", "/")
                files.append((rel_path, language))

        return files

    def _parse_file(
        self, file_path: str, content: str, language: str
    ) -> Tuple[List[GraphNode], List[dict], List[str]]:
        """Parse a file and extract symbols + relationships.

        Returns:
            Tuple of (symbols, relationships, diagnostics)
        """
        lang_lower = language.lower()

        if "python" in lang_lower:
            parser = PythonSymbolParser(file_path, content)
            return parser.parse()
        elif "typescript" in lang_lower or "javascript" in lang_lower:
            parser = TypeScriptJSParser(file_path, content)
            return parser.parse()
        elif lang_lower == "java":
            parser = JavaSymbolParser(file_path, content)
            return parser.parse()
        elif lang_lower in ("go", "golang"):
            parser = GoSymbolParser(file_path, content)
            return parser.parse()
        elif lang_lower in ("rust", "rs"):
            parser = RustSymbolParser(file_path, content)
            return parser.parse()
        elif lang_lower in ("c", "c++", "cpp"):
            parser = CppSymbolParser(file_path, content)
            return parser.parse()
        elif lang_lower in ("c#", "csharp"):
            parser = CSharpSymbolParser(file_path, content)
            return parser.parse()
        elif lang_lower == "kotlin":
            parser = KotlinSymbolParser(file_path, content)
            return parser.parse()
        elif lang_lower == "swift":
            parser = SwiftSymbolParser(file_path, content)
            return parser.parse()
        elif lang_lower == "ruby":
            parser = RubySymbolParser(file_path, content)
            return parser.parse()
        elif lang_lower == "php":
            parser = PhpSymbolParser(file_path, content)
            return parser.parse()
        else:
            return [], [], [f"Unsupported language: {language}"]

    def _add_file_nodes(
        self, graph: SemanticRepositoryGraph, files: List[Tuple[str, str]]
    ) -> None:
        """Add file-level nodes to the graph."""
        for file_path, language in files:
            file_id = make_symbol_id(file_path, file_path)
            graph.add_node(GraphNode(
                id=file_id,
                name=file_path.split("/")[-1],
                qualified_name=file_path,
                kind="file",
                file_path=file_path,
                language=language,
            ))

    def _is_binary(self, file_path: str) -> bool:
        """Check if a file is binary by reading its first few bytes."""
        try:
            with open(file_path, "rb") as f:
                chunk = f.read(1024)
            return b"\0" in chunk
        except Exception:
            return True

    # ── Graph Access ─────────────────────────────────────────────

    # ── Persistence ──────────────────────────────────────────────

    async def persist_graph(self) -> Optional[Dict[str, Any]]:
        """Persist the current graph to PostgreSQL via the configured store.

        Requires a PostgresRunStore to have been provided at construction.
        Gracefully degrades if no store, no graph, or store error.

        Returns:
            Dict with persistence result, or None if nothing persisted.
        """
        if not self._store:
            logger.debug("persist_graph: no store configured, skipping")
            return None
        if not self._graph:
            logger.debug("persist_graph: no graph loaded, skipping")
            return None
        if not self._index_id or not self._repository_path or not self._repository_id:
            logger.debug("persist_graph: no index metadata, skipping")
            return None

        try:
            stats = self._graph.stats()

            result = await self._store.save_graph(
                graph=self._graph,
                repository_id=self._repository_id,
                index_id=self._index_id,
                repository_path=self._repository_path,
                content_fingerprint=hashlib.sha256(
                    f"{self._index_id}:{self._graph.node_count()}".encode()
                ).hexdigest()[:16],
                language_coverage=None,
                file_count=stats.get("file_count", 0),
            )
            logger.info(
                "Graph persisted: index=%s, %d symbols, %d relationships",
                result.get("index_id"),
                result.get("symbol_count", 0),
                result.get("relationship_count", 0),
            )
            return result
        except Exception as exc:
            logger.warning("Graph persistence failed (non-fatal): %s", exc)
            return None

    def get_current_graph(self) -> Optional[SemanticRepositoryGraph]:
        """Get the currently loaded graph (if any)."""
        return self._graph

    # ── Symbol Embedding Persistence (Phase 12d) ────────────────

    async def persist_symbol_embeddings(self) -> int:
        """Persist deterministic embeddings for all graph symbols.

        Uses the configured embedding provider (defaults to the
        deterministic fake provider). Stores via the VectorStore,
        which falls back to in-memory when pgvector is unavailable.
        Gracefully degrades: returns 0 when no store, no graph, or
        no embedding service.

        Returns:
            Number of symbol embeddings persisted.
        """
        if not self._vector_store:
            return 0
        if not self._graph or not self._index_id or not self._repository_id:
            return 0

        try:
            emb_service = self._get_embedding_service()
            texts = []
            ids = []
            for node in self._graph.all_nodes():
                ids.append(node.id)
                texts.append(node.qualified_name or node.name)
            if not texts:
                return 0

            result = emb_service.embed_documents(texts)
            items = [
                {"symbol_id": sid, "embedding": emb}
                for sid, emb in zip(ids, result.embeddings)
                if emb
            ]
            return await self._vector_store.save_embeddings(
                repository_id=self._repository_id,
                index_id=self._index_id,
                items=items,
            )
        except Exception as exc:
            logger.debug("Symbol embedding persistence skipped: %s", exc)
            return 0

    async def search_symbol_embeddings(
        self,
        query_text: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Semantic search over persisted symbol embeddings.

        Returns list of {"symbol_id", "score"} nearest matches, or []
        when the vector store is unavailable or query embedding fails.
        """
        if not self._vector_store:
            return []

        try:
            emb_service = self._get_embedding_service()
            query_embedding = emb_service.embed_query(query_text)
            if not query_embedding:
                return []
            return await self._vector_store.search(
                query_embedding=query_embedding,
                repository_id=self._repository_id,
                limit=limit,
            )
        except Exception as exc:
            logger.debug("Symbol embedding search skipped: %s", exc)
            return []

    def _get_embedding_service(self) -> Any:
        """Create the configured embedding service (deterministic fake
        provider by default, matching Phase 5 conventions)."""
        from app.rag.embeddings import create_embedding_service

        return create_embedding_service(
            provider=settings.EMBEDDING_PROVIDER,
            model=settings.EMBEDDING_MODEL,
            dimension=settings.EMBEDDING_DIMENSION,
        )

    def has_graph(self) -> bool:
        return self._graph is not None

    def get_index_id(self) -> Optional[str]:
        return self._index_id

    def get_repository_path(self) -> Optional[str]:
        return self._repository_path

    # ── High-level Queries ───────────────────────────────────────

    def get_symbol(
        self, symbol_id: str
    ) -> Optional[GraphNode]:
        """Look up a symbol by ID."""
        graph = self._graph
        if not graph:
            return None
        return graph.get_node(symbol_id)

    def find_symbol(self, name: str, file_path: Optional[str] = None) -> Optional[GraphNode]:
        """Find a symbol by name, optionally filtered by file."""
        graph = self._graph
        if not graph:
            return None
        matches = graph.find_symbols_by_name(name)
        if file_path:
            matches = [m for m in matches if m.file_path == file_path]
        return matches[0] if matches else None

    def find_symbols(self, name: str) -> List[GraphNode]:
        """Find all symbols with a given name."""
        graph = self._graph
        if not graph:
            return []
        return graph.find_symbols_by_name(name)

    def symbols_in_file(self, file_path: str) -> List[GraphNode]:
        """Get all symbols in a file."""
        graph = self._graph
        if not graph:
            return []
        return graph.symbols_in_file(file_path)

    # ── Impact Analysis ──────────────────────────────────────────

    def analyze_impact(
        self,
        symbol_ids: List[str],
        max_depth: int = 3,
        max_nodes: int = 100,
    ) -> Any:
        """Run impact analysis on a set of symbols."""
        analyzer = ImpactAnalysisService(
            graph=self._graph,
            max_depth=max_depth,
            max_nodes=max_nodes,
        )
        return analyzer.analyze(symbol_ids)

    # ── Reset ────────────────────────────────────────────────────

    def reset(self) -> None:
        """Reset the service state."""
        self._graph = None
        self._index_id = None
        self._repository_path = None
        self._repository_id = None
