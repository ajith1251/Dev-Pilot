"""
Hashed n-gram embedding provider — deterministic, similarity-preserving.

Unlike FakeEmbeddingProvider (hash-random vectors where similar text does
NOT yield similar vectors), this provider maps overlapping word features
to the same signed buckets, so texts that share words / word-stems /
character trigrams get high cosine similarity and texts that share
nothing get near-zero similarity.

Design (all deterministic, no API):
- Tokenize to lowercase alphanumeric words; drop stopwords + 1-2 char
  tokens (removes the cross-text noise floor).
- Features per word: the whole word, its 4-char stem (so "caching" and
  "cache" overlap), and every within-word character trigram.
- Feature-hash each feature into a signed bucket (sha256 -> index + sign)
  and normalize to unit length.

Production deployments can swap in OpenAIEmbeddingProvider through the
existing EMBEDDING_PROVIDER setting; the EKG semantic layer degrades
gracefully to this provider when the configured provider is unavailable.
"""

from __future__ import annotations

import hashlib
import math
import re
import time
from typing import List

from app.rag.embeddings.base import EmbeddingResult, EmbeddingService

_NGRAM_N = 3

# Common English stopwords + the planner's stop list — removed before
# feature extraction so generic tokens cannot create spurious similarity.
_STOPWORDS = {
    "the", "and", "for", "this", "that", "with", "from", "were", "was",
    "has", "have", "had", "its", "are", "did", "does", "is", "of", "to",
    "in", "on", "a", "an", "it", "as", "at", "by", "or", "be", "but",
    "not", "you", "your", "we", "our", "they", "their", "them", "he",
    "she", "his", "her", "will", "would", "can", "could", "should", "may",
    "might", "must", "than", "then", "when", "where", "which", "who",
    "whom", "what", "all", "any", "each", "more", "most", "other", "some",
    "such", "only", "own", "same", "so", "too", "very", "just", "about",
    "into", "over", "after", "before", "between", "under", "again",
}


class HashedNGramEmbeddingProvider(EmbeddingService):
    """Deterministic hashed word/trigram embedding provider.

    Cosine similarity between two vectors measures how many word-level
    features (words, stems, trigrams) the texts share — a cheap,
    deterministic proxy for lexical/semantic overlap with a near-zero
    noise floor between unrelated texts.
    """

    def __init__(
        self,
        dimension: int = 256,
        model: str = "hashed-ngram",
        ngram: int = _NGRAM_N,
    ) -> None:
        super().__init__(model=model)
        self._dimension = dimension
        self._ngram = ngram

    # ── EmbeddingService interface ──────────────────────────────

    def embed_documents(self, texts: List[str]) -> EmbeddingResult:
        """Embed a list of document texts deterministically."""
        start = time.time()
        embeddings: List[List[float]] = []
        cache_hits = 0

        for text in texts:
            cached = self._check_cache(text)
            if cached is not None:
                embeddings.append(cached)
                cache_hits += 1
            else:
                emb = self._embed(text)
                self._set_cache(text, emb)
                embeddings.append(emb)

        duration = round(time.time() - start, 4)
        return EmbeddingResult(
            embeddings=embeddings,
            model=self._model,
            dimension=self._dimension,
            cache_hits=cache_hits,
            duration_seconds=duration,
        )

    def embed_query(self, text: str) -> List[float]:
        """Embed a single query string."""
        cached = self._check_cache(text)
        if cached is not None:
            return cached
        embedding = self._embed(text)
        self._set_cache(text, embedding)
        return embedding

    @property
    def dimension(self) -> int:
        return self._dimension

    # ── Core ────────────────────────────────────────────────────

    def _words(self, text: str) -> List[str]:
        """Lowercased, stopword-filtered word tokens (length >= 3)."""
        return [
            w for w in re.findall(r"[a-z0-9]+", text.lower())
            if len(w) >= 3 and w not in _STOPWORDS
        ]

    def _features(self, text: str) -> List[str]:
        """Word-level features: word, 4-char stem, within-word trigrams."""
        features: List[str] = []
        for w in self._words(text):
            features.append(w)
            features.append(w[:4])  # stem: "caching" ≈ "cache"
            for i in range(max(0, len(w) - self._ngram + 1)):
                features.append(w[i : i + self._ngram])
        return features

    def _embed(self, text: str) -> List[float]:
        """Feature-hash words/stems/trigrams into a signed, normalized vector."""
        vec = [0.0] * self._dimension
        for feat in self._features(text):
            h = hashlib.sha256(feat.encode("utf-8")).digest()
            idx = int.from_bytes(h[:4], "little") % self._dimension
            sign = 1.0 if h[4] % 2 == 0 else -1.0
            vec[idx] += sign

        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec
