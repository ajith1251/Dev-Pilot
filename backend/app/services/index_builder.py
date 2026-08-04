"""
Repository Index Builder — orchestrates the full indexing pipeline.

Coordinates:
1. Repository scanning (via existing RepositoryScanner)
2. File classification (via existing FileClassifier)
3. Index eligibility (via IndexEligibilityService)
4. File reading (safe, limited)
5. Parser selection and symbol extraction
6. Code chunking
7. Lexical index construction
8. Symbol index construction
9. Embedding generation (if enabled)
10. RepositoryCodeIndex assembly
"""

from __future__ import annotations

import hashlib
import os
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from app.core.logging import logger
from app.models.profile import FileCategory, FileClassification
from app.models.rag import (
    ChunkType,
    CodeChunk,
    CodeSymbol,
    IndexStatistics,
    RepositoryCodeIndex,
    RepositorySnapshot,
)
from app.rag.embeddings import EmbeddingService, create_embedding_service
from app.config import settings
from app.rag.indexes import LexicalIndex, SymbolIndex, VectorIndex
from app.rag.parsers import CodeParser, FallbackParser, ParseResult, PythonParser
from app.services.code_chunker import CodeChunker
from app.services.file_classifier import FileClassifier
from app.services.index_eligibility import IndexEligibilityService
from app.services.repository_scanner import RepositoryScanner, ScannedFile


class RepositoryIndexBuilder:
    """Builds a RepositoryCodeIndex from a local repository path.

    Usage:
        builder = RepositoryIndexBuilder()
        index = builder.build("/path/to/repo")
        # index is a RepositoryCodeIndex with all data
    """

    def __init__(
        self,
        scanner: Optional[RepositoryScanner] = None,
        file_classifier: Optional[FileClassifier] = None,
        eligibility_service: Optional[IndexEligibilityService] = None,
        chunker: Optional[CodeChunker] = None,
        embedding_service: Optional[EmbeddingService] = None,
        enable_embeddings: bool = False,
        max_files_to_index: int = 500,
    ) -> None:
        self.scanner = scanner or RepositoryScanner()
        self.file_classifier = file_classifier or FileClassifier()
        self.eligibility_service = eligibility_service or IndexEligibilityService()
        self.chunker = chunker or CodeChunker()
        self.embedding_service = embedding_service
        # If no embedding service provided but embeddings enabled, create from config
        if enable_embeddings and self.embedding_service is None:
            self.embedding_service = create_embedding_service(
                provider=settings.EMBEDDING_PROVIDER,
                model=settings.EMBEDDING_MODEL,
                dimension=settings.EMBEDDING_DIMENSION,
            )
        self.enable_embeddings = enable_embeddings
        self.max_files_to_index = max_files_to_index

        # Parser registry
        self._parsers: List[CodeParser] = [
            PythonParser(),
            FallbackParser(),
        ]

    def build(
        self,
        repo_path: str,
        ref: Optional[str] = None,
        commit_sha: Optional[str] = None,
        snapshot_id: Optional[str] = None,
    ) -> RepositoryCodeIndex:
        """Build a complete repository code index.

        Args:
            repo_path: Absolute path to the local repository.
            ref: Optional branch/ref name.
            commit_sha: Optional commit SHA.
            snapshot_id: Optional snapshot ID (generated if not provided).

        Returns:
            RepositoryCodeIndex with all indexed data.
        """
        start_time = time.time()
        stats = IndexStatistics()
        resolved_path = os.path.abspath(repo_path)

        logger.info("Index build started: %s", resolved_path)

        # ── Step 1: Scan ────────────────────────────────────────
        scan_result = self.scanner.scan(resolved_path)
        files = scan_result.files

        if not files:
            stats.errors.append(f"No files found in {resolved_path}")
            stats.duration_seconds = round(time.time() - start_time, 3)
            return self._make_empty_index(resolved_path, snapshot_id, stats)

        stats.files_considered = len(files)

        # ── Step 2: Classify files ──────────────────────────────
        file_categories: Dict[str, FileCategory] = {}
        for f in files:
            cat = self.file_classifier.classify_file(f)
            file_categories[f.path] = cat

        # ── Step 3: Determine eligibility ───────────────────────
        eligibility_results = self.eligibility_service.filter_indexable_files(
            files, file_categories
        )

        eligible_results = [r for r in eligibility_results if r.eligible]
        eligible_files = [
            f for f in files
            if any(r.file_path == f.path for r in eligible_results)
        ]

        # Limit to max files
        eligible_files = eligible_files[:self.max_files_to_index]

        stats.files_indexed = len(eligible_files)
        stats.files_skipped = len(files) - len(eligible_files)

        logger.info(
            "Index build: %d eligible out of %d files",
            len(eligible_files), len(files),
        )

        # ── Step 4: Create snapshot ─────────────────────────────
        fingerprint = self._compute_fingerprint(eligible_files)
        snap_id = snapshot_id or self._generate_snapshot_id(resolved_path, fingerprint)

        snapshot = RepositorySnapshot(
            snapshot_id=snap_id,
            repository_id=resolved_path,
            repository_path=resolved_path,
            ref=ref,
            commit_sha=commit_sha,
            content_fingerprint=fingerprint,
            file_count=len(eligible_files),
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        # ── Step 5: Parse, extract symbols, chunk ──────────────
        all_symbols: List[CodeSymbol] = []
        all_chunks: List[CodeChunk] = []
        indexed_file_paths: List[str] = []

        for sc_file in eligible_files:
            try:
                content = self._read_file_safe(resolved_path, sc_file.path)
                if content is None:
                    continue

                language = self._detect_language(sc_file)
                category = file_categories.get(sc_file.path, FileCategory.UNKNOWN)

                # Parse
                parse_result = self._parse_file(sc_file.path, content, language)
                if parse_result.errors:
                    stats.warnings.append(
                        f"Parse warnings for {sc_file.path}: {'; '.join(parse_result.errors)}"
                    )

                # Chunk
                chunks = self.chunker.chunk_file(
                    file_path=sc_file.path,
                    content=content,
                    language=language,
                    snapshot=snapshot,
                    symbols=parse_result.symbols if parse_result.symbols else None,
                )

                all_symbols.extend(parse_result.symbols)
                all_chunks.extend(chunks)
                indexed_file_paths.append(sc_file.path)

                logger.debug(
                    "Indexed %s: %d symbols, %d chunks",
                    sc_file.path,
                    len(parse_result.symbols),
                    len(chunks),
                )

            except Exception as exc:
                stats.warnings.append(f"Failed to index {sc_file.path}: {exc}")
                continue

        stats.symbols_extracted = len(all_symbols)
        stats.chunks_created = len(all_chunks)

        # ── Step 6: Build indexes ───────────────────────────────
        lexical_index = LexicalIndex()
        lexical_index.build(all_chunks)

        symbol_index = SymbolIndex()
        symbol_index.build(all_symbols)

        vector_index = VectorIndex()

        # ── Step 7: Generate embeddings (if enabled) ────────────
        if self.enable_embeddings and self.embedding_service:
            try:
                embedding_cache_hits = 0
                texts_to_embed = []
                chunk_ids_to_embed = []

                for chunk in all_chunks:
                    texts_to_embed.append(chunk.content)
                    chunk_ids_to_embed.append(chunk.chunk_id)

                if texts_to_embed:
                    embed_result = self.embedding_service.embed_documents(texts_to_embed)
                    embedding_cache_hits += embed_result.cache_hits

                    for chunk_id, embedding in zip(chunk_ids_to_embed, embed_result.embeddings):
                        chunk = lexical_index.get_chunk(chunk_id) or \
                                next((c for c in all_chunks if c.chunk_id == chunk_id), None)
                        if chunk:
                            vector_index.add(chunk_id, embedding, chunk)

                    stats.embedding_count = len(texts_to_embed)
                    stats.embedding_cache_hits = embedding_cache_hits

                    logger.debug(
                        "Generated %d embeddings with %d cache hits",
                        len(texts_to_embed), embedding_cache_hits,
                    )
            except Exception as exc:
                stats.warnings.append(f"Embedding generation failed: {exc}")

        # ── Step 8: Assemble index ──────────────────────────────
        duration = round(time.time() - start_time, 3)
        stats.duration_seconds = duration

        index = RepositoryCodeIndex(
            snapshot=snapshot,
            files=indexed_file_paths,
            symbols=all_symbols,
            chunks=all_chunks,
            statistics=stats,
            metadata={
                "lexical_terms": lexical_index.stats().get("unique_terms", 0),
                "symbol_count": symbol_index.size,
                "vector_count": vector_index.size,
                "embeddings_enabled": self.enable_embeddings,
            },
        )

        logger.info(
            "Index build complete: %d files, %d symbols, %d chunks (%.1fs)",
            len(indexed_file_paths),
            len(all_symbols),
            len(all_chunks),
            duration,
        )

        return index

    def build_with_indexes(
        self,
        repo_path: str,
        ref: Optional[str] = None,
        commit_sha: Optional[str] = None,
    ) -> tuple:
        """Build and return the index plus individual indexes for retrieval.

        Returns:
            Tuple of (RepositoryCodeIndex, LexicalIndex, SymbolIndex, VectorIndex)
            where the indexes are pre-built from the same data.
        """
        start_time = time.time()
        resolved_path = os.path.abspath(repo_path)

        # Use shared scan-parse logic
        code_index = self.build(repo_path, ref=ref, commit_sha=commit_sha)

        # Build indexes from the code_index data
        lex_idx = LexicalIndex()
        lex_idx.build(code_index.chunks)

        sym_idx = SymbolIndex()
        sym_idx.build(code_index.symbols)

        vec_idx = VectorIndex()

        # Embeddings if enabled
        if self.enable_embeddings and self.embedding_service and code_index.chunks:
            texts = [c.content for c in code_index.chunks]
            if texts:
                result = self.embedding_service.embed_documents(texts)
                for cid, emb in zip([c.chunk_id for c in code_index.chunks], result.embeddings):
                    ch = next(c for c in code_index.chunks if c.chunk_id == cid)
                    vec_idx.add(cid, emb, ch)

        # Store indexes as instance attributes for workflow access
        self._last_lexical_index = lex_idx
        self._last_symbol_index = sym_idx
        self._last_vector_index = vec_idx

        return code_index, lex_idx, sym_idx, vec_idx

    @property
    def lexical_index(self) -> Optional[LexicalIndex]:
        return getattr(self, '_last_lexical_index', None)

    @property
    def symbol_index(self) -> Optional[SymbolIndex]:
        return getattr(self, '_last_symbol_index', None)

    @property
    def vector_index(self) -> Optional[VectorIndex]:
        return getattr(self, '_last_vector_index', None)

    # ── Helpers ───────────────────────────────────────────

    def _read_file_safe(self, root_path: str, rel_path: str) -> Optional[str]:
        """Read a file safely with encoding handling."""
        full_path = os.path.join(root_path, rel_path)
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read(500_000)  # Limit to 500KB
        except Exception:
            return None

    def _parse_file(
        self, file_path: str, content: str, language: str
    ) -> ParseResult:
        """Parse a file using the best available parser."""
        for parser in self._parsers:
            if parser.supports_language(language):
                return parser.parse(file_path, content)

        # Fallback
        return ParseResult(file_path=file_path, language=language, success=True)

    def _detect_language(self, file: ScannedFile) -> str:
        """Detect language from file."""
        from app.services.language_detector import LanguageDetector
        lang = LanguageDetector._detect_language(file.name, file.extension)
        return lang or "unknown"

    @staticmethod
    def _compute_fingerprint(files: List[ScannedFile]) -> str:
        """Compute a deterministic fingerprint of the indexed file set."""
        hasher = hashlib.sha256()
        for f in sorted(files, key=lambda x: x.path):
            hasher.update(f.path.encode("utf-8"))
            hasher.update(str(f.size_bytes).encode("utf-8"))
            hasher.update(str(f.modification_time if hasattr(f, 'modification_time') else 0).encode("utf-8"))
        return hasher.hexdigest()[:32]

    @staticmethod
    def _generate_snapshot_id(repo_path: str, fingerprint: str) -> str:
        """Generate a deterministic snapshot ID."""
        import hashlib
        raw = f"{repo_path}::{fingerprint}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _make_empty_index(
        repo_path: str, snapshot_id: Optional[str], stats: IndexStatistics
    ) -> RepositoryCodeIndex:
        """Create an empty index for a failed build."""
        snap_id = snapshot_id or "empty"
        snapshot = RepositorySnapshot(
            snapshot_id=snap_id,
            repository_id=repo_path,
            repository_path=repo_path,
            content_fingerprint="empty",
            file_count=0,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        return RepositoryCodeIndex(
            snapshot=snapshot,
            statistics=stats,
        )
