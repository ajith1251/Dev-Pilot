"""
Phase 15 (Phase 12d) — Cross-File Symbol Resolution.

Resolves import references across file boundaries so the semantic graph
can answer "what does AuthService (defined in auth/service.py) get used
by?" even when the importer lives in a different module.

The Phase 12 parsers already extract per-file `import` nodes with
metadata (module, name, as_name). This resolver links those import
nodes to the actual definition node in the target file when it can be
found, creating a `REFERENCES` edge with EXACT confidence.

Graceful degradation: files/modules that cannot be resolved simply
produce no new edges (targets may be external dependencies or stdlib).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from app.code_intelligence.semantic_graph import (
    ConfidenceLevel,
    RelationshipType,
    SemanticRepositoryGraph,
    make_symbol_id,
    normalize_qualified_name,
)


@dataclass
class ResolutionStats:
    """Statistics from a cross-file symbol resolution pass."""

    import_nodes_seen: int = 0
    resolved: int = 0
    unresolved: int = 0
    edges_added: int = 0
    warnings: List[str] = field(default_factory=list)


class CrossFileSymbolResolver:
    """Link import nodes to their definitions across files.

    Pass 1 — build a module index: module path -> file_path, and a
    per-file symbol table: (module path, short name) -> node id.

    Pass 2 — for each `import` node with metadata, resolve to the
    definition node and add a REFERENCES edge.
    """

    def __init__(self, graph: Optional[SemanticRepositoryGraph] = None) -> None:
        self._graph = graph

    def set_graph(self, graph: SemanticRepositoryGraph) -> None:
        self._graph = graph

    def resolve(self) -> ResolutionStats:
        """Run the two-pass cross-file resolution on the current graph."""
        stats = ResolutionStats()
        if self._graph is None:
            stats.warnings.append("No graph loaded")
            return stats

        # ── Pass 1: build module index ──────────────────────────
        module_to_file: Dict[str, str] = {}
        # (module_path, short_name) -> node id
        symbol_table: Dict[Tuple[str, str], str] = {}
        file_symbols: Dict[str, List[object]] = {}

        for node in self._graph.all_nodes():
            if node.kind == "module":
                module_to_file[node.qualified_name] = node.file_path

            if node.kind in ("class", "function", "async_function", "method", "interface", "type", "enum", "constant"):
                module = self._module_for_file(node.file_path)
                symbol_table[(module, node.name)] = node.id

            file_symbols.setdefault(node.file_path, []).append(node)

        # ── Pass 2: resolve import nodes ────────────────────────
        for node in self._graph.all_nodes():
            if node.kind != "import":
                continue
            stats.import_nodes_seen += 1

            resolved = self._resolve_import_node(
                node=node,
                module_to_file=module_to_file,
                symbol_table=symbol_table,
                stats=stats,
            )
            if resolved is None:
                stats.unresolved += 1

        return stats

    # ── Internal helpers ────────────────────────────────────────

    def _module_for_file(self, file_path: str) -> str:
        """Derive the dotted module path from a file path."""
        normalized = file_path.replace("\\", "/")
        if normalized.endswith(".py"):
            normalized = normalized[:-3]
        elif "." in normalized.rsplit("/", 1)[-1]:
            normalized = normalized.rsplit(".", 1)[0]
        return normalized.replace("/", ".").replace("__init__", "").rstrip(".")

    def _resolve_import_node(
        self,
        node: object,
        module_to_file: Dict[str, str],
        symbol_table: Dict[Tuple[str, str], str],
        stats: ResolutionStats,
    ) -> Optional[str]:
        """Attempt to resolve a single import node. Returns target node id."""
        if self._graph is None:
            return None

        # Figure out what this import references.
        target_module = None
        target_name = None
        metadata = getattr(node, "metadata", {}) or {}
        signature = getattr(node, "signature", "") or ""

        if metadata.get("module"):
            # from <module> import <name>
            target_module = metadata["module"].lstrip(".")
            target_name = metadata.get("name")
        elif signature.startswith("import "):
            # import <module>  → whole module reference
            target_module = signature[len("import "):].strip()
            target_name = None
        else:
            # Fallback: qualified_name may contain 'imports.<something>'
            qn = getattr(node, "qualified_name", "") or ""
            if "imports." in qn:
                tail = qn.split("imports.")[-1]
                if "." in tail:
                    target_module, target_name = tail.rsplit(".", 1)
                else:
                    target_name = tail

        if not target_module and not target_name:
            return None

        # Module-level import: link to the module node in the target file.
        if target_name is None:
            target_file = module_to_file.get(target_module)
            if target_file:
                mod_qn = self._module_for_file(target_file)
                target_id = make_symbol_id(target_file, mod_qn)
                if self._graph.has_node(target_id):
                    return self._add_reference(node, target_id, stats)
            return None

        # `from X import Y` — resolve within module X (or any module whose
        # last segment matches X, for flat namespaces).
        candidates: List[Tuple[str, str]] = []
        if target_module:
            candidates.append((target_module, target_name))
        # Also try by-name match in files of the same top-level package.
        candidates.append((target_name, target_name))

        seen_targets: Set[str] = set()
        for module, name in candidates:
            target_id = symbol_table.get((module, name))
            if target_id and target_id not in seen_targets:
                seen_targets.add(target_id)
                resolved = self._add_reference(node, target_id, stats)
                if resolved:
                    return resolved

        return None

    def _add_reference(
        self,
        import_node: object,
        target_id: str,
        stats: ResolutionStats,
    ) -> Optional[str]:
        """Add a REFERENCES edge from import node to target. Returns target id."""
        if self._graph is None:
            return None

        source_id = getattr(import_node, "id", None)
        if not source_id or not self._graph.has_node(source_id):
            return None
        if not self._graph.has_node(target_id):
            return None

        # Avoid duplicate edges.
        for edge in self._graph.get_edges(source_id):
            if edge.target_id == target_id and edge.metadata.relationship == RelationshipType.REFERENCES:
                return target_id

        self._graph.add_edge(
            source_id=source_id,
            target_id=target_id,
            relationship=RelationshipType.REFERENCES,
            confidence=ConfidenceLevel.EXACT,
            resolution_detail=f"cross-file symbol resolution: import → {target_id}",
        )
        stats.resolved += 1
        stats.edges_added += 1
        return target_id
