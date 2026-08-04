"""
Hybrid Retriever — combines lexical, symbol, semantic, and structural
signals with configurable rank fusion for context retrieval.

This is the core Phase 5 retrieval capability.
"""

from __future__ import annotations

import math
import os
import time
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

from app.models.rag import (
    ChunkType,
    CodeChunk,
    CodeSymbol,
    RetrievalFilter,
    RetrievalQuery,
    RetrievedContext,
    RetrievedContextItem,
    SymbolKind,
)
from app.rag.embeddings import EmbeddingService
from app.rag.indexes import LexicalIndex, SymbolIndex, VectorIndex

# Default weights for hybrid scoring
DEFAULT_WEIGHT_LEXICAL: float = 0.30
DEFAULT_WEIGHT_SEMANTIC: float = 0.25
DEFAULT_WEIGHT_SYMBOL: float = 0.25
DEFAULT_WEIGHT_STRUCTURAL: float = 0.20

# Score boost for exact symbol matches in content
SYMBOL_EXACT_MATCH_BONUS: float = 0.15

# Score boost for test files when query mentions testing
TEST_FILE_BOOST: float = 0.10


class HybridRetriever:
    """Retrieves relevant code context using multiple signals.

    Combines:
    1. Lexical search (BM25-like term matching)
    2. Symbol search (exact/normalized symbol names)
    3. Semantic search (embedding similarity)
    4. Structural/path relevance (module/directory matching)

    Uses configurable weighted fusion with score normalization.
    """

    def __init__(
        self,
        lexical_index: Optional[LexicalIndex] = None,
        symbol_index: Optional[SymbolIndex] = None,
        vector_index: Optional[VectorIndex] = None,
        embedding_service: Optional[EmbeddingService] = None,
        weight_lexical: float = DEFAULT_WEIGHT_LEXICAL,
        weight_semantic: float = DEFAULT_WEIGHT_SEMANTIC,
        weight_symbol: float = DEFAULT_WEIGHT_SYMBOL,
        weight_structural: float = DEFAULT_WEIGHT_STRUCTURAL,
    ) -> None:
        self.lexical_index = lexical_index
        self.symbol_index = symbol_index
        self.vector_index = vector_index
        self.embedding_service = embedding_service

        self.weight_lexical = weight_lexical
        self.weight_semantic = weight_semantic
        self.weight_symbol = weight_symbol
        self.weight_structural = weight_structural

        # Cache of chunks for retrieval
        self._chunks: Dict[str, CodeChunk] = {}

        # Pre-computed module paths
        self._file_modules: Dict[str, str] = {}

    def set_indexes(
        self,
        lexical: LexicalIndex,
        symbol: SymbolIndex,
        vector: VectorIndex,
        chunks: List[CodeChunk],
    ) -> None:
        """Set the indexes and chunk cache for retrieval."""
        self.lexical_index = lexical
        self.symbol_index = symbol
        self.vector_index = vector

        self._chunks = {c.chunk_id: c for c in chunks}
        for c in chunks:
            module = c.file_path.replace("/", ".").rsplit(".", 1)[0] if "." in c.file_path else c.file_path
            self._file_modules[c.file_path] = module

    def retrieve(self, query: RetrievalQuery) -> RetrievedContext:
        """Execute hybrid retrieval using all available signals.

        Args:
            query: RetrievalQuery with text, filters, and options.

        Returns:
            RetrievedContext with ranked items and explanations.
        """
        start_time = time.time()
        warnings: List[str] = []

        query_text = query.text.strip()
        if not query_text:
            warnings.append("Empty query text")

        top_k = query.top_k

        # ── Gather candidates from each signal ──────────────
        lexical_results: List[Tuple[str, float]] = []
        symbol_results: List[Tuple[str, CodeSymbol, float]] = []
        semantic_results: List[Tuple[str, float]] = []
        structural_results: List[Tuple[str, float]] = []

        # 1. Lexical search
        if self.lexical_index and self.lexical_index.built and query_text:
            lexical_results = self.lexical_index.search(query_text, top_k=top_k * 2)

        # 2. Symbol search
        if self.symbol_index and self.symbol_index.built and query_text:
            symbol_results = self.symbol_index.search(query_text, top_k=top_k)

        # 3. Semantic search
        if (self.vector_index and self.vector_index.built
                and self.embedding_service and query_text):
            try:
                query_embedding = self.embedding_service.embed_query(query_text)
                semantic_results = self.vector_index.search(
                    query_embedding, top_k=top_k * 2
                )
            except Exception as exc:
                warnings.append(f"Semantic search failed: {exc}")

        # 4. Structural/path relevance
        if query_text:
            structural_results = self._structural_search(
                query_text, top_k=top_k * 2
            )

        # ── Fuse scores ─────────────────────────────────────
        fused_scores: Dict[str, Dict[str, float]] = {}

        # Normalize to 0-1 range for each signal
        lexical_scores = self._normalize_scores(lexical_results)
        semantic_scores = self._normalize_scores(semantic_results)
        symbol_scores_map = self._symbol_to_chunk_scores(symbol_results, self._chunks)
        structural_scores = self._normalize_scores(structural_results)

        # Apply weights and fuse
        all_candidate_ids: Set[str] = set()
        all_candidate_ids.update(lexical_scores.keys())
        all_candidate_ids.update(semantic_scores.keys())
        all_candidate_ids.update(symbol_scores_map.keys())
        all_candidate_ids.update(structural_scores.keys())

        for chunk_id in all_candidate_ids:
            chunk = self._chunks.get(chunk_id)
            if not chunk:
                continue

            # Apply filters
            if not self._passes_filters(chunk, query.filters):
                continue

            lex_score = lexical_scores.get(chunk_id, 0.0)
            sem_score = semantic_scores.get(chunk_id, 0.0)
            sym_score = symbol_scores_map.get(chunk_id, 0.0)
            str_score = structural_scores.get(chunk_id, 0.0)

            # Apply query-specific weights
            w_lex = query.weight_lexical if query.weight_lexical is not None else self.weight_lexical
            w_sem = query.weight_semantic if query.weight_semantic is not None else self.weight_semantic
            w_sym = query.weight_symbol if query.weight_symbol is not None else self.weight_symbol
            w_str = query.weight_structural if query.weight_structural is not None else self.weight_structural

            # Normalize weights to sum to 1.0
            total_w = w_lex + w_sem + w_sym + w_str
            if total_w > 0:
                w_lex /= total_w
                w_sem /= total_w
                w_sym /= total_w
                w_str /= total_w

            combined = (
                w_lex * lex_score
                + w_sem * sem_score
                + w_sym * sym_score
                + w_str * str_score
            )

            fused_scores[chunk_id] = {
                "combined": combined,
                "lexical": round(lex_score, 4),
                "semantic": round(sem_score, 4),
                "symbol": round(sym_score, 4),
                "structural": round(str_score, 4),
            }

        # ── Sort and build results ──────────────────────────
        sorted_chunks = sorted(
            fused_scores.items(),
            key=lambda x: x[1]["combined"],
            reverse=True,
        )[:top_k]

        items: List[RetrievedContextItem] = []
        seen_hashes: Set[str] = set()
        file_chunk_count: Dict[str, int] = defaultdict(int)

        for chunk_id, scores in sorted_chunks:
            chunk = self._chunks.get(chunk_id)
            if not chunk:
                continue

            # Deduplication by content hash
            if chunk.content_hash in seen_hashes:
                continue
            seen_hashes.add(chunk.content_hash)

            # Max chunks per file
            if file_chunk_count[chunk.file_path] >= query.max_chunks_per_file:
                continue
            file_chunk_count[chunk.file_path] += 1

            # Build reasons
            reasons = self._build_reasons(
                chunk, scores, query_text,
                symbol_results,
            )

            items.append(RetrievedContextItem(
                chunk=chunk,
                score=round(scores["combined"], 4),
                lexical_score=scores["lexical"],
                semantic_score=scores["semantic"],
                symbol_score=scores["symbol"],
                structural_score=scores["structural"],
                reasons=reasons,
            ))

        # ── Apply context budget ────────────────────────────
        items = self._apply_context_budget(items, query.max_total_chars)

        duration = round(time.time() - start_time, 3)

        return RetrievedContext(
            query=query,
            snapshot_id=self._chunks[items[0].chunk.chunk_id].snapshot_id if items else "",
            items=items,
            total_candidates=len(all_candidate_ids),
            duration_seconds=duration,
            warnings=warnings,
            trust_level="UNTRUSTED_REPOSITORY_CONTENT",
        )

    # ── Structural Search ───────────────────────────────────

    def _structural_search(
        self, query: str, top_k: int = 10
    ) -> List[Tuple[str, float]]:
        """Score chunks by path/module relevance to the query.

        Analyzes query terms against file paths, module names,
        and directory structures to identify structurally relevant code.
        """
        query_lower = query.lower()
        query_tokens = set(
            t for t in query_lower.replace("/", " ").replace(".", " ").replace("_", " ").replace("-", " ").split()
            if len(t) >= 2
        )

        if not query_tokens:
            return []

        scores: List[Tuple[str, float]] = []

        for chunk_id, chunk in self._chunks.items():
            file_path_lower = chunk.file_path.lower()
            module = self._file_modules.get(chunk.file_path, "").lower()
            path_tokens = set(
                t for t in file_path_lower.replace("/", " ").replace(".", " ").replace("_", " ").replace("-", " ").split()
                if len(t) >= 2
            )

            # Calculate term overlap with query
            overlap = query_tokens & path_tokens
            if overlap:
                score = len(overlap) / max(len(query_tokens), 1)

                # Boost for matching file name
                file_name = os.path.splitext(os.path.basename(chunk.file_path))[0].lower()
                if file_name in query_tokens or any(q in file_name for q in query_tokens):
                    score += 0.2

                # Boost for matching module name
                if module and any(m in query_lower for m in module.split(".") if m):
                    score += 0.1

                scores.append((chunk_id, min(score, 1.0)))

        return sorted(scores, key=lambda x: x[1], reverse=True)[:top_k]

    # ── Symbol → Chunk mapping ─────────────────────────────

    def _symbol_to_chunk_scores(
        self,
        symbol_results: List[Tuple[str, CodeSymbol, float]],
        chunks: Dict[str, CodeChunk],
    ) -> Dict[str, float]:
        """Map symbol search results to chunk scores.

        A symbol match gives its score to all chunks within
        that symbol's line range.
        """
        chunk_scores: Dict[str, float] = defaultdict(float)

        for sym_id, symbol, score in symbol_results:
            # Find chunks that contain this symbol
            for chunk_id, chunk in chunks.items():
                if chunk.file_path == symbol.file_path:
                    # Check if chunk overlaps symbol's line range
                    if (chunk.start_line <= symbol.end_line
                            and chunk.end_line >= symbol.start_line):
                        current = chunk_scores[chunk_id]
                        chunk_scores[chunk_id] = max(current, score)

                        # If symbol name also appears in chunk content, boost
                        if symbol.name in chunk.content:
                            chunk_scores[chunk_id] = min(
                                chunk_scores[chunk_id] + SYMBOL_EXACT_MATCH_BONUS,
                                1.0,
                            )

        return dict(chunk_scores)

    # ── Score normalization ─────────────────────────────────

    @staticmethod
    def _normalize_scores(
        results: List[Tuple[str, float]],
    ) -> Dict[str, float]:
        """Min-max normalize a list of (id, score) to [0, 1]."""
        if not results:
            return {}

        scores = [s for _, s in results]
        min_s = min(scores)
        max_s = max(scores)

        if max_s == min_s:
            return {rid: 1.0 for rid, _ in results}

        normalized = {}
        for rid, score in results:
            normalized[rid] = (score - min_s) / (max_s - min_s)

        return normalized

    # ── Filtering ──────────────────────────────────────────

    @staticmethod
    def _passes_filters(chunk: CodeChunk, filters: Optional[RetrievalFilter]) -> bool:
        """Check if a chunk passes the given filters."""
        if not filters:
            return True

        if filters.languages and chunk.language.lower() not in {
            l.lower() for l in filters.languages
        }:
            return False

        if filters.path_prefix and not chunk.file_path.startswith(filters.path_prefix):
            return False

        if filters.module and chunk.module:
            query_module = filters.module.lower()
            chunk_module = chunk.module.lower()
            if query_module not in chunk_module and chunk_module not in query_module:
                return False

        if filters.symbol_kinds and chunk.symbol_kind:
            if chunk.symbol_kind not in filters.symbol_kinds:
                return False

        if not filters.include_tests and "/test" in chunk.file_path.lower():
            return False

        return True

    # ── Reason generation ──────────────────────────────────

    def _build_reasons(
        self,
        chunk: CodeChunk,
        scores: Dict[str, float],
        query_text: str,
        symbol_results: List[Tuple[str, CodeSymbol, float]],
    ) -> List[str]:
        """Build human-readable reasons explaining why this chunk was retrieved."""
        reasons: List[str] = []

        if scores["lexical"] > 0.3:
            # Find matching terms
            query_terms = set(query_text.lower().split())
            content_lower = chunk.content.lower()
            matching = [t for t in query_terms if t in content_lower and len(t) >= 3]
            if matching:
                reasons.append(
                    f"Lexical overlap: terms '{', '.join(matching[:5])}' found in content"
                )

        if scores["symbol"] > 0.3:
            # Find which symbol matched
            for _, symbol, sym_score in symbol_results:
                if symbol.file_path == chunk.file_path:
                    reasons.append(
                        f"Symbol match: {symbol.qualified_name} (score: {sym_score:.2f})"
                    )
                    break

        if scores["semantic"] > 0.3:
            reasons.append(
                f"Semantic similarity: {scores['semantic']:.2f}"
            )

        if scores["structural"] > 0.3:
            reasons.append(
                f"Path matched affected area: {chunk.file_path}"
            )

        if chunk.symbol_name and chunk.symbol_name in query_text:
            reasons.append(
                f"Exact symbol name in query: {chunk.symbol_name}"
            )

        if not reasons:
            reasons.append(f"Combined score: {scores['combined']:.4f}")

        return reasons

    # ── Context budget ─────────────────────────────────────

    @staticmethod
    def _apply_context_budget(
        items: List[RetrievedContextItem],
        max_chars: int,
    ) -> List[RetrievedContextItem]:
        """Trim results to fit within the character budget."""
        if not max_chars or max_chars <= 0:
            return items

        total_chars = 0
        result: List[RetrievedContextItem] = []

        for item in items:
            chars = len(item.chunk.content) if item.chunk.content else 0
            if total_chars + chars > max_chars:
                break
            total_chars += chars
            result.append(item)

        return result
