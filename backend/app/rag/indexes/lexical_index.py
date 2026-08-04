"""
Lexical Index — deterministic inverted index for code text search.

Supports natural language terms and code identifiers (camelCase, snake_case,
PascalCase) with normalization so queries like "password reset token" can
match identifiers like "passwordResetToken" or "PasswordResetService".
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import defaultdict, Counter
from typing import Dict, List, Optional, Set, Tuple

from app.core.logging import logger
from app.models.rag import CodeChunk


# Minimum term length to index
MIN_TERM_LENGTH = 2

# Maximum term length to index
MAX_TERM_LENGTH = 100

# Stop words (common English words not useful for code search)
STOP_WORDS: Set[str] = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "shall", "can",
    "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "as", "into", "through", "during", "before", "after", "above",
    "below", "between", "out", "off", "over", "under", "again",
    "further", "then", "once", "here", "there", "when", "where",
    "why", "how", "all", "each", "every", "both", "few", "more",
    "most", "other", "some", "such", "no", "nor", "not", "only",
    "own", "same", "so", "than", "too", "very", "just", "because",
    "but", "and", "or", "if", "while",
}


class LexicalIndex:
    """Inverted index for lexical code search with identifier normalization.

    Thread-safe for reads. Not thread-safe for writes.
    """

    def __init__(self) -> None:
        # Inverted index: term -> {chunk_id -> term_frequency}
        self._index: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))

        # Document lengths: chunk_id -> total_terms
        self._doc_lengths: Dict[str, int] = defaultdict(int)

        # Total documents
        self._total_docs: int = 0

        # Term -> document frequency (how many docs contain this term)
        self._df: Dict[str, int] = defaultdict(int)

        # Chunk reference: chunk_id -> chunk
        self._chunks: Dict[str, CodeChunk] = {}

        # Content hash -> chunk_id (dedup)
        self._content_hash_to_id: Dict[str, str] = {}

        # Track if built
        self._built: bool = False

    def build(self, chunks: List[CodeChunk]) -> None:
        """Build or rebuild the index from a list of chunks.

        Args:
            chunks: List of CodeChunk to index.
        """
        self.clear()

        for chunk in chunks:
            # Deduplicate by content hash
            if chunk.content_hash in self._content_hash_to_id:
                continue

            chunk_id = chunk.chunk_id
            self._chunks[chunk_id] = chunk
            self._total_docs += 1
            self._content_hash_to_id[chunk.content_hash] = chunk_id

            # Tokenize content
            terms = self._tokenize(chunk.content)
            term_counts = Counter(terms)

            for term, count in term_counts.items():
                self._index[term][chunk_id] += count
                self._doc_lengths[chunk_id] += count

        # Calculate document frequencies
        for term, postings in self._index.items():
            self._df[term] = len(postings)

        self._built = True
        logger.debug(
            "LexicalIndex built: %d terms, %d documents",
            len(self._index), self._total_docs,
        )

    def search(
        self,
        query: str,
        top_k: int = 10,
    ) -> List[Tuple[str, float]]:
        """Search the index with BM25-like ranking.

        Args:
            query: Search query string.
            top_k: Maximum results to return.

        Returns:
            List of (chunk_id, score) tuples sorted by score descending.
        """
        if not self._built or not query.strip():
            return []

        query_terms = self._tokenize(query)
        if not query_terms:
            return []

        # Score each document with BM25-like formula
        avg_doc_len = (
            sum(self._doc_lengths.values()) / max(self._total_docs, 1)
        )

        # BM25 parameters
        k1 = 1.5
        b = 0.75

        scores: Dict[str, float] = defaultdict(float)

        for term in set(query_terms):
            if term not in self._index:
                continue

            df = self._df[term]  # Document frequency
            idf = math.log((self._total_docs - df + 0.5) / (df + 0.5) + 1.0)

            for chunk_id, tf in self._index[term].items():
                doc_len = self._doc_lengths[chunk_id]
                denom = tf + k1 * (1 - b + b * doc_len / max(avg_doc_len, 1))
                scores[chunk_id] += idf * tf / denom

        # Sort by score descending
        sorted_results = sorted(scores.items(), key=lambda x: x[1], reverse=True)

        return sorted_results[:top_k]

    def get_chunk(self, chunk_id: str) -> Optional[CodeChunk]:
        """Get a chunk by ID."""
        return self._chunks.get(chunk_id)

    def clear(self) -> None:
        """Clear the index."""
        self._index.clear()
        self._doc_lengths.clear()
        self._total_docs = 0
        self._df.clear()
        self._chunks.clear()
        self._content_hash_to_id.clear()
        self._built = False

    @property
    def size(self) -> int:
        """Number of indexed documents."""
        return self._total_docs

    @property
    def built(self) -> bool:
        return self._built

    def stats(self) -> dict:
        """Return index statistics."""
        return {
            "total_documents": self._total_docs,
            "unique_terms": len(self._index),
            "built": self._built,
        }

    # ── Tokenization ────────────────────────────────────────────

    def _tokenize(self, text: str) -> List[str]:
        """Tokenize text into searchable terms.

        Handles:
        - Split camelCase, PascalCase, snake_case
        - Lowercase all terms
        - Filter stop words and short/long terms
        """
        # First, split camelCase and PascalCase
        text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
        text = re.sub(r"([A-Z])([A-Z][a-z])", r"\1 \2", text)

        # Replace underscores, dots, slashes, hyphens with spaces
        text = re.sub(r"[_.\-/\\\\]", " ", text)

        # Extract alphanumeric tokens (including numbers as part of identifiers)
        tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9]*", text)

        # Normalize and filter
        result: List[str] = []
        for token in tokens:
            token_lower = token.lower()
            if (
                len(token_lower) >= MIN_TERM_LENGTH
                and len(token_lower) <= MAX_TERM_LENGTH
                and token_lower not in STOP_WORDS
                and not token_lower.isdigit()
            ):
                result.append(token_lower)
                # Also add the original case form for code identifiers
                if token != token_lower and len(token) >= MIN_TERM_LENGTH:
                    result.append(token)

        return result
