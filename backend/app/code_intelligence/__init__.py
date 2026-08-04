"""
Phase 12 — Advanced Code Intelligence & Semantic Repository Graph.

Provides:
- Language parsers (Python, TypeScript, JavaScript)
- SemanticRepositoryGraph (in-memory directed graph)
- CodeIntelligenceService (orchestrator)
- ImpactAnalysisService
- IncrementalIndexer
- Graph-aware retrieval
"""

from app.code_intelligence.semantic_graph import (
    ConfidenceLevel,
    EdgeMetadata,
    GraphEdge,
    GraphNode,
    RelationshipType,
    SemanticRepositoryGraph,
    TraversalResult,
)

from app.code_intelligence.code_intelligence_service import (
    CodeIntelligenceService,
    IndexResult,
    IndexStats,
)
from app.code_intelligence.impact_analyzer import ImpactAnalysisResult, ImpactAnalysisService, RiskLevel
from app.code_intelligence.incremental_indexer import FileChange, IncrementalIndexer, IncrementalResult
from app.code_intelligence.parsers.python_parser import PythonSymbolParser
from app.code_intelligence.parsers.ts_parser import TypeScriptJSParser

__all__ = [
    # Graph
    "SemanticRepositoryGraph",
    "GraphNode",
    "GraphEdge",
    "EdgeMetadata",
    "RelationshipType",
    "ConfidenceLevel",
    "TraversalResult",
    # Service
    "CodeIntelligenceService",
    "IndexResult",
    "IndexStats",
    # Impact
    "ImpactAnalysisService",
    "ImpactAnalysisResult",
    "RiskLevel",
    # Incremental
    "IncrementalIndexer",
    "IncrementalResult",
    "FileChange",
    # Parsers
    "PythonSymbolParser",
    "TypeScriptJSParser",
]
