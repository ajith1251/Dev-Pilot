"""
Symbol Index — direct symbol lookup and ranking.

Supports exact match, qualified name, and partial identifier search
for code symbols. Symbol matches receive strong ranking weight in
the hybrid retrieval pipeline.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

from app.models.rag import CodeSymbol, SymbolKind


class SymbolIndex:
    """Index for fast symbol lookup and ranking.

    Maintains multiple lookup tables for different query modes:
    - Exact name match
    - Qualified name match
    - Normalized (lowercase) match
    - Partial identifier match
    """

    def __init__(self) -> None:
        # name -> [symbol]
        self._by_name: Dict[str, List[CodeSymbol]] = defaultdict(list)

        # qualified_name -> symbol
        self._by_qualified: Dict[str, CodeSymbol] = {}

        # lowercase name -> [symbol]
        self._by_normalized: Dict[str, List[CodeSymbol]] = defaultdict(list)

        # All symbols by ID
        self._by_id: Dict[str, CodeSymbol] = {}

        # Partial index: fragments of identifiers
        self._partial_index: Dict[str, Set[str]] = defaultdict(set)

        self._built: bool = False

    def build(self, symbols: List[CodeSymbol]) -> None:
        """Build the symbol index.

        Args:
            symbols: List of CodeSymbol to index.
        """
        self.clear()

        for symbol in symbols:
            sym_id = symbol.id
            name = symbol.name
            qname = symbol.qualified_name
            name_lower = name.lower()

            self._by_id[sym_id] = symbol
            self._by_name[name].append(symbol)
            self._by_qualified[qname] = symbol
            self._by_normalized[name_lower].append(symbol)

            # Build partial index (tokens from qualified name)
            tokens = set()
            for part in qname.replace(".", " ").replace("::", " ").split():
                tokens.add(part.lower())
                # Add subtokens for camelCase/PascalCase
                self._add_subtokens(part, tokens)

            for token in tokens:
                if len(token) >= 2:
                    self._partial_index[token].add(sym_id)

        self._built = True

    def search(
        self,
        query: str,
        top_k: int = 10,
    ) -> List[Tuple[str, CodeSymbol, float]]:
        """Search for symbols matching a query.

        Returns list of (symbol_id, symbol, score) sorted by score desc.

        Scoring:
        - Exact qualified name match: 1.0
        - Exact name match: 0.95
        - Normalized name match: 0.8
        - Partial identifier match: 0.5
        """
        results: Dict[str, Tuple[CodeSymbol, float]] = {}
        query_lower = query.lower()

        # 1. Exact qualified name match
        if query in self._by_qualified:
            sym = self._by_qualified[query]
            if sym.id not in results or results[sym.id][1] < 1.0:
                results[sym.id] = (sym, 1.0)

        # 2. Exact name match
        if query in self._by_name:
            for sym in self._by_name[query]:
                if sym.id not in results or results[sym.id][1] < 0.95:
                    results[sym.id] = (sym, 0.95)

        # 3. Normalized name match
        if query_lower in self._by_normalized:
            for sym in self._by_normalized[query_lower]:
                score = 0.8
                if sym.id in results:
                    existing = results[sym.id][1]
                    score = max(existing, 0.8)
                results[sym.id] = (sym, score)

        # 4. Partial/qualified token match
        query_tokens = set(
            t for t in query_lower.replace(".", " ").replace("::", " ").split()
            if len(t) >= 2
        )

        if query_tokens:
            candidate_ids: Set[str] = set()
            for token in query_tokens:
                if token in self._partial_index:
                    candidate_ids.update(self._partial_index[token])

            for sym_id in candidate_ids:
                if sym_id not in results:
                    sym = self._by_id.get(sym_id)
                    if sym:
                        # Calculate partial match score
                        name_tokens = set(
                            t.lower() for t in sym.qualified_name
                            .replace(".", " ").replace("::", " ").split()
                        )
                        matches = len(query_tokens & name_tokens)
                        if matches > 0:
                            score = 0.5 * (matches / max(len(query_tokens), 1))
                            results[sym_id] = (sym, score)

        # Sort by score descending
        sorted_results = sorted(
            results.items(),
            key=lambda x: x[1][1],
            reverse=True,
        )

        return [
            (sym_id, sym, score)
            for sym_id, (sym, score) in sorted_results[:top_k]
        ]

    def get_by_id(self, symbol_id: str) -> Optional[CodeSymbol]:
        """Get a symbol by its ID."""
        return self._by_id.get(symbol_id)

    def get_by_name(self, name: str) -> List[CodeSymbol]:
        """Get all symbols with the given name."""
        return list(self._by_name.get(name, []))

    def clear(self) -> None:
        """Clear the index."""
        self._by_name.clear()
        self._by_qualified.clear()
        self._by_normalized.clear()
        self._by_id.clear()
        self._partial_index.clear()
        self._built = False

    @property
    def size(self) -> int:
        return len(self._by_id)

    @property
    def built(self) -> bool:
        return self._built

    def stats(self) -> dict:
        return {
            "total_symbols": len(self._by_id),
            "built": self._built,
        }

    @staticmethod
    def _add_subtokens(text: str, tokens: Set[str]) -> None:
        """Split camelCase/PascalCase identifier into subtokens."""
        import re
        parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)", text)
        for part in parts:
            normalized = part.lower()
            if len(normalized) >= 2:
                tokens.add(normalized)
