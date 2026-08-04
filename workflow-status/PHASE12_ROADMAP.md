# Phase 12 — Advanced Code Intelligence + Semantic Repository Graph

> **Status**: 🔜 Planned
> **Prerequisite**: Phase 11 (Persistent State + PostgreSQL) ✅ Complete

---

## Executive Summary

Phase 12 transforms DevPilot's code understanding from **flat RAG retrieval** (Phase 5) into a **rich, relational, multi-dimensional code knowledge base**. Instead of just finding "chunks of code that match a query," Phase 12 builds a **Semantic Repository Graph** — a directed graph connecting symbols, files, modules, dependencies, data flows, and documentation — enabling the orchestrator to reason about code at the architecture level.

**Current limitation (Phase 5):** The Phase 5 index treats code as a bag of chunks. It can find "files about password reset" but cannot answer "what functions call `authenticate()`?" or "which modules depend on the auth service?"

**Phase 12 goal:** Give DevPilot architectural awareness — the ability to navigate code like a senior engineer who understands the whole system, not just isolated fragments.

---

## 1. Semantic Repository Graph

### 1.1 What is a Semantic Repository Graph?

A multi-layer directed graph where:

| Layer | Nodes | Edges | Example |
|-------|-------|-------|---------|
| **Symbol** | Functions, classes, methods, interfaces | `calls`, `extends`, `implements`, `contains` | `AuthService.login → UserRepository.findByEmail` |
| **File/Module** | Files, directories, packages | `imports`, `depends_on` | `auth/service.py → auth/models.py` |
| **Data Flow** | Variables, parameters, return values | `produces`, `consumes`, `transforms` | `processPayment(order) → PaymentReceipt` |
| **Call Graph** | Function call sites | `calls`, `called_by` | `resetPassword → tokenService.generate → emailService.send` |
| **Dependency** | External packages, libraries | `requires`, `version_pin` | `app → fastapi==0.110.0` |
| **Test Coverage** | Test functions, source symbols | `tests`, `covers` | `test_auth_login → AuthService.login` |
| **Documentation** | Docstrings, comments, READMEs | `documents`, `references` | `ARCHITECTURE.md → auth/service.py` |

### 1.2 Graph Storage

Persist the graph in PostgreSQL using the Phase 11 infrastructure:

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `graph_nodes` | All symbols, files, modules | node_id, kind, qualified_name, file_path, signature |
| `graph_edges` | All relationships | source_id, target_id, relationship_type, weight, metadata |
| `graph_snapshots` | Repository state at build time | snapshot_id, repo_path, fingerprint, created_at |
| `call_sites` | Detailed call locations | caller_id, callee_id, file_path, line_number |
| `data_flows` | Variable/parameter flow edges | source_node, target_node, flow_type, context |

### 1.3 Build Pipeline

```
Repository
    ↓
Phase 2 Intelligence (reuse)
    ├── Language detection
    ├── Module organization
    └── File classification
    ↓
Static Analysis (NEW in Phase 12)
    ├── Full symbol resolution (across files)
    ├── Call graph construction
    ├── Inheritance hierarchy resolution
    ├── Import/dependency chain resolution
    ├── Data flow tracing (variable → usage)
    └── Test-to-code mapping
    ↓
Dependency Resolution
    ├── Internal: cross-file import resolution
    ├── External: package.json, pyproject.toml, Cargo.toml parsing
    └── Version constraint extraction
    ↓
Graph Construction
    ├── Node creation (dedup, qualified names)
    ├── Edge creation (typed, weighted)
    ├── Cycle detection
    └── Graph statistics
    ↓
Persistence
    └── PostgreSQL (via Phase 11 pattern)
```

---

## 2. Key Components

### 2.1 Cross-File Symbol Resolver

**Current (Phase 5):** Symbol extraction is per-file. `AuthService` in `auth/service.py` and `UserRepository` in `users/repository.py` are stored as isolated symbols with no knowledge of each other.

**Phase 12:** Resolve symbols across file boundaries:

- **Import resolution**: Parse `from auth.service import AuthService` and link the usage site to the definition
- **Qualified name resolution**: Build fully-qualified names even for complex multi-file patterns
- **Type resolution**: When possible, resolve type annotations to their defining symbols
- **Inheritance resolution**: Link classes to their parents across module boundaries

**Implementation approach:** Use a two-pass strategy:
1. **Pass 1 per-file**: Extract all symbols and their exports/imports
2. **Pass 2 global**: Resolve cross-file references using a symbol table

```
File: auth/service.py
    exports: AuthService
    imports: UserRepository (from users/repository)
    
File: users/repository.py
    exports: UserRepository
    
→ Graph edge: AuthService --calls--> UserRepository
```

### 2.2 Call Graph Builder

Construct a complete call graph of the repository:

| Feature | Detail |
|---------|--------|
| **Direct calls** | `function_a()` calls `function_b()` within same file |
| **Cross-file calls** | Module A calls Module B's exported function |
| **Async calls** | `await service.process()`, `asyncio.gather(...)` |
| **Decorator calls** | `@app.get("/route")` → Flask/FastAPI route registration |
| **Constructor calls** | `UserService(db_session)` → class instantiation |
| **Method calls on objects** | `user.save()` → link to method definition (when type-known) |

**Use cases in the pipeline:**
- "What code paths does this change affect?" → impact analysis
- "Which tests cover this function?" → test selection
- "What are all the callers of this deprecated API?" → migration planning

### 2.3 Import & Dependency Graph

Build the module-level dependency graph to answer architecture questions:

```
┌─────────────┐     imports     ┌──────────────┐
│  api/v1/    │ ──────────────→ │  services/   │
│  auth.py    │                 │  auth.py     │
└─────────────┘                 └──────┬───────┘
                                       │ imports
                                       ▼
                               ┌──────────────┐
                               │  models/     │
                               │  user.py     │
                               └──────────────┘
```

**Analysis capabilities:**
- **Dependency cycle detection**: Find circular imports before they cause bugs
- **Layer violation detection**: Detect when a low-level module imports a high-level one
- **Change impact analysis**: "If I modify `models/user.py`, which services and APIs are affected?"
- **Dead code detection**: Find modules with zero dependents (potential dead code)
- **Tiered architecture validation**: Enforce layering rules (e.g., API → Service → Model → DB)

### 2.4 Code Change Impact Analyzer

Given a set of changed files (from a Phase 6 patch), compute the **impact frontier**:

```python
impact_frontier = ImpactAnalyzer(graph).analyze(
    changed_files=["auth/service.py"],
    max_depth=3,  # How far to traverse
    include_tests=True,
)
# Returns:
# - Direct dependents (importers of changed modules)
# - Transitive dependents (importers of importers)
# - Related tests (tests covering impacted code)
# - Risk score per impacted file
```

This directly benefits:
- **Phase 7**: Smarter test selection (run only tests on impacted code)
- **Phase 8**: More targeted repair (know what might have broken)
- **Phase 9**: Better review context (show all impacted files)

### 2.5 Enhanced Parsers

Replace regex-based fallback parsers with proper **tree-sitter** grammars for major languages:

| Language | Phase 5 | Phase 12 |
|----------|---------|----------|
| Python | ✅ AST (stdlib) | ✅ AST + tree-sitter (for cross-file) |
| TypeScript/JavaScript | ⚠️ Regex fallback | ✅ tree-sitter-typescript |
| Java | ⚠️ Regex fallback | ✅ tree-sitter-java |
| Go | ⚠️ Regex fallback | ✅ tree-sitter-go |
| Rust | ⚠️ Regex fallback | ✅ tree-sitter-rust |
| Others | ❌ No support | 🔜 tree-sitter generic |

**Benefits of tree-sitter:**
- **Robust error recovery**: Can parse files with syntax errors (common during development)
- **Language-agnostic**: Single framework for all languages
- **Rich AST**: Captures comments, string literals, generics, type annotations
- **Incremental parsing**: Efficient re-parsing after edits (future: live indexing)

### 2.6 Persisted Index Storage

**Current (Phase 5):** All in-memory. Lost on restart. Must rebuild every time.

**Phase 12:** Persist all indexes to PostgreSQL using the Phase 11 patterns:

| Index | Storage | Benefits |
|-------|---------|----------|
| Lexical Index | PostgreSQL FTS + JSONB | Survives restarts, supports full-text search |
| Symbol Index | `graph_nodes` table | Cross-session queries, shares with graph |
| Vector Index | pgvector extension | Native PostgreSQL vector search, ACID compliance |
| Graph | `graph_nodes` + `graph_edges` | All relationships persisted, queryable via SQL |

**Key compatibility:** pgvector enables `SELECT ... ORDER BY embedding <=> query_embedding LIMIT 10` — no separate vector database needed.

### 2.7 Incremental Indexing

**Current:** Full rebuild required on every repository change.

**Phase 12** (post-MVP):
- Track file modification times
- Only re-parse changed files
- Incremental graph update (add/remove nodes and edges)
- Detect staleness and trigger re-index

---

## 3. Retrieval Enhancements

### 3.1 Graph-Aware Retrieval

Instead of bag-of-chunks retrieval:

```python
# Phase 5 (current)
query = "password reset"
results = hybrid_retriever.retrieve(query)
# Returns: [chunk_a, chunk_b, chunk_c]  (flat list)

# Phase 12 (graph-aware)
query = "password reset flow"
results = graph_retriever.retrieve(query)
# Returns: 
#   ├── reset_password() function (matched)
#   ├── → calls token_service.generate() (related via call graph)
#   ├── → calls email_service.send() (related via call graph)
#   ├── test_reset_password() (related via test coverage)
#   └── password_reset.html template (related via path proximity)
```

### 3.2 Multi-Hop Retrieval

Navigate the graph to find context that Phase 5 cannot reach:

| Query Type | Phase 5 | Phase 12 |
|-----------|---------|----------|
| "How does reset password work?" | Code chunks mentioning "password" | Full call graph: route → controller → service → model → template |
| "What does this change break?" | Nothing (not supported) | Impact analysis: all dependents, transitive dependents, related tests |
| "Why is this function slow?" | Only the function's code | Upstream data producers, downstream consumers, IO calls |
| "Which tests cover this PR?" | Nothing (not supported) | Test coverage edges from changed symbols to test functions |
| "Is this module dependency allowed?" | Nothing (not supported) | Layer validation against defined architecture rules |

### 3.3 Architecture-Aware Ranking

Rank retrieved results not just by text similarity, but by **architectural relevance**:

```
Score = w1 * TextSimilarity + w2 * GraphProximity + w3 * ImpactRelevance + w4 * TestCoverage

Where:
- TextSimilarity: Phase 5 hybrid score (lexical + semantic + symbol + structure)
- GraphProximity: Distance from query-related nodes in the graph
- ImpactRelevance: How many downstream nodes would be affected
- TestCoverage: Whether the chunk has associated tests
```

---

## 4. Integration with Existing Pipeline

### 4.1 Phase 5 → Phase 12 Migration Path

| Phase 5 Component | Phase 12 Replacement | Compatibility |
|-------------------|----------------------|---------------|
| `LexicalIndex` | PostgreSQL FTS + persisted BM25 | Drop-in replacement via `IndexStore` protocol |
| `SymbolIndex` | `graph_nodes` table + SQL queries | Extended interface (adds relationship queries) |
| `VectorIndex` | pgvector + persisted embeddings | Same interface, now persistent |
| `HybridRetriever` | `GraphAwareRetriever` | Backward-compatible (same `RetrievedContext` output) |
| `PlanContextRetriever` | `GraphPlanContextRetriever` | Same input/output, richer context |
| In-memory storage | PostgreSQL (Phase 11 pattern) | Configurable via `DATABASE_URL` |

### 4.2 Downstream Phase Benefits

| Phase | Current Limitation | Phase 12 Benefit |
|-------|-------------------|------------------|
| Phase 6 (Coding) | Context limited to "chunks matching the plan step" | Graph provides architectural context: "this function is called by X, depends on Y, and is part of Z flow" |
| Phase 7 (Testing) | Runs all tests or heuristic-based selection | Impact analysis: run only tests covering changed code and its dependents |
| Phase 8 (Repair) | Must re-discover dependencies during diagnosis | Graph tells the fix agent: "failure in function F; here are F's callers, callees, and data dependencies" |
| Phase 9 (Review) | Review is code-only | Graph enables architecture-level review: "this change introduces a circular dependency" |
| Phase 10 (Orchestration) | No cross-run knowledge | Informs orchestrator about which stages need re-execution based on change impact |

---

## 5. API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/code-graph/build` | Build or rebuild the semantic graph for a repository |
| POST | `/api/v1/code-graph/query` | Query the graph (symbols, relationships, paths) |
| POST | `/api/v1/code-graph/impact` | Compute change impact for a set of files |
| GET | `/api/v1/code-graph/symbol/{qualified_name}` | Get symbol details and relationships |
| GET | `/api/v1/code-graph/callers/{symbol_name}` | Get all callers of a function/method |
| GET | `/api/v1/code-graph/callees/{symbol_name}` | Get all callees of a function/method |
| GET | `/api/v1/code-graph/dependents/{file_path}` | Get all files that depend on a given file |
| GET | `/api/v1/code-graph/dependencies/{file_path}` | Get all dependencies of a given file |
| GET | `/api/v1/code-graph/stats` | Graph statistics (nodes, edges, density) |
| POST | `/api/v1/code-intelligence/retrieval/graph-aware` | Graph-aware retrieval (Phase 5 extended) |
| POST | `/api/v1/code-intelligence/impact/test-selection` | Select tests based on change impact |

---

## 6. File Structure (New/Modified)

```
backend/app/
├── graph/                          ← NEW Phase 12 package
│   ├── __init__.py
│   ├── builder.py                  ← GraphBuilder orchestrator
│   ├── models.py                   ← GraphNode, GraphEdge, etc.
│   ├── symbol_resolver.py          ← Cross-file symbol resolution
│   ├── call_graph.py               ← Call graph construction
│   ├── dependency_graph.py         ← Module dependency resolution
│   ├── impact_analyzer.py          ← Change impact computation
│   ├── layer_validator.py          ← Architecture layer validation
│   └── stores/
│       ├── __init__.py
│       ├── node_store.py           ← PostgreSQL-backed node storage
│       ├── edge_store.py           ← PostgreSQL-backed edge storage
│       └── graph_store.py          ← Composite store (nodes + edges)
├── rag/
│   ├── indexes/
│   │   ├── lexical_index.py        ← MODIFIED: persisted version
│   │   ├── symbol_index.py         ← MODIFIED: persisted version
│   │   └── vector_index.py         ← MODIFIED: pgvector version
│   └── retrieval/
│       ├── hybrid_retriever.py     ← MODIFIED: uses graph ranking
│       ├── graph_aware_retriever.py ← NEW: graph-proximity boosting
│       └── impact_retriever.py     ← NEW: impact-aware retrieval
├── parsers/                        ← NEW: tree-sitter based
│   ├── __init__.py
│   ├── base.py                     ← Abstract tree-sitter parser
│   ├── python_parser.py            ← tree-sitter-python
│   ├── typescript_parser.py        ← tree-sitter-typescript
│   ├── java_parser.py              ← tree-sitter-java
│   ├── go_parser.py                ← tree-sitter-go
│   └── rust_parser.py              ← tree-sitter-rust
├── db/
│   ├── models.py                   ← MODIFIED: add graph_* tables
│   └── database.py                 ← MODIFIED: pgvector extension check
├── api/v1/
│   ├── code_graph.py               ← NEW: Phase 12 endpoints
│   └── code_intelligence.py        ← MODIFIED: new retrieval modes
└── cli.py                          ← MODIFIED: graph build/query CLI

alembic/
├── versions/
│   └── 003_add_graph_tables.py     ← NEW: migration for graph schema
```

---

## 7. Database Schema (New Tables)

```sql
-- Graph nodes (symbols, files, modules)
CREATE TABLE graph_nodes (
    id SERIAL PRIMARY KEY,
    node_id VARCHAR(64) UNIQUE NOT NULL,
    snapshot_id VARCHAR(64) NOT NULL,
    kind VARCHAR(32) NOT NULL,  -- 'function', 'class', 'method', 'file', 'module', etc.
    qualified_name VARCHAR(512) NOT NULL,
    short_name VARCHAR(256),
    file_path VARCHAR(1024),
    language VARCHAR(32),
    signature TEXT,
    doc_summary VARCHAR(500),
    start_line INTEGER,
    end_line INTEGER,
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_graph_nodes_snapshot ON graph_nodes(snapshot_id);
CREATE INDEX idx_graph_nodes_kind ON graph_nodes(kind);
CREATE INDEX idx_graph_nodes_qualified ON graph_nodes(qualified_name);

-- Graph edges (relationships between nodes)
CREATE TABLE graph_edges (
    id SERIAL PRIMARY KEY,
    snapshot_id VARCHAR(64) NOT NULL,
    source_node_id VARCHAR(64) NOT NULL REFERENCES graph_nodes(node_id) ON DELETE CASCADE,
    target_node_id VARCHAR(64) NOT NULL REFERENCES graph_nodes(node_id) ON DELETE CASCADE,
    relationship_type VARCHAR(32) NOT NULL,  -- 'calls', 'imports', 'extends', 'contains', 'tests', etc.
    weight FLOAT DEFAULT 1.0,
    file_path VARCHAR(1024),
    line_number INTEGER,
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_graph_edges_source ON graph_edges(source_node_id);
CREATE INDEX idx_graph_edges_target ON graph_edges(target_node_id);
CREATE INDEX idx_graph_edges_type ON graph_edges(relationship_type);
CREATE INDEX idx_graph_edges_snapshot ON graph_edges(snapshot_id);

-- Graph snapshots (repository state tracking)
CREATE TABLE graph_snapshots (
    id SERIAL PRIMARY KEY,
    snapshot_id VARCHAR(64) UNIQUE NOT NULL,
    repository_path VARCHAR(1024) NOT NULL,
    content_fingerprint VARCHAR(64),
    node_count INTEGER DEFAULT 0,
    edge_count INTEGER DEFAULT 0,
    build_duration_ms FLOAT,
    status VARCHAR(32) DEFAULT 'building',  -- 'building', 'ready', 'stale', 'failed'
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_graph_snapshots_fingerprint ON graph_snapshots(content_fingerprint);
```

---

## 8. Dependencies (New)

| Package | Purpose | Type |
|---------|---------|------|
| `tree-sitter` | Language parsing framework | Required for enhanced parsers |
| `tree-sitter-python` | Python grammar | Required |
| `tree-sitter-typescript` | TypeScript/JavaScript grammar | Recommended |
| `tree-sitter-java` | Java grammar | Recommended |
| `tree-sitter-go` | Go grammar | Recommended |
| `tree-sitter-rust` | Rust grammar | Recommended |
| `pgvector` | PostgreSQL vector search extension | Required for persisted vector index |

---

## 9. Testing Strategy

| Area | Tests | Approach |
|------|-------|----------|
| **Cross-file symbol resolution** | 15+ | Unit tests with multi-file fixtures |
| **Call graph construction** | 10+ | Known call patterns → verify graph edges |
| **Import resolution** | 10+ | Complex import patterns (relative, absolute, aliases) |
| **Dependency graph** | 8+ | Module dependency verification, cycle detection |
| **Impact analysis** | 10+ | Known change → verify impacted set |
| **Graph-aware retrieval** | 8+ | Query → verify graph-proximity boosted results |
| **Tree-sitter parsers** | 5 per language | Extract known symbols, compare to AST-based python parser |
| **Persisted storage** | 15+ | PostgreSQL integration tests (Phase 11 pattern) |
| **Layer validation** | 5+ | Known architecture rules → detect violations |
| **API endpoints** | 10+ | Request/response contract tests |
| **End-to-end pipeline** | 3+ | Full graph build → query → impact → retrieve |
| **Regression** | 5+ | Phase 5 queries produce same or better results |

**Estimated total new tests:** 100+

---

## 10. Implementation Phases

### Phase 12a — Graph Foundation (MVP)
- Cross-file symbol resolver (Python first)
- Call graph construction
- Persist graph nodes/edges to PostgreSQL
- Graph build CLI command
- Graph stats API

### Phase 12b — Import & Dependency Graph
- Import resolution across all supported languages
- Module-level dependency graph
- Cycle detection
- Layer validation

### Phase 12c — Impact Analysis & Graph-Aware Retrieval
- Change impact analyzer
- Graph-aware retrieval with proximity boosting
- Integration with Phase 6/7/8/9
- Smart test selection for Phase 7

### Phase 12d — Production Hardening
- tree-sitter parsers for all major languages
- Incremental indexing
- pgvector integration for persisted vector search
- Performance optimization for large repositories (100k+ symbols)
- 100+ tests

---

## 11. Success Metrics

| Metric | Phase 5 Baseline | Phase 12 Target |
|--------|------------------|-----------------|
| Cross-file relationships | 0 (none) | ✅ All imports resolved |
| Symbol relationships | Per-file only | ✅ Full cross-file graph |
| Query enrichment | Flat chunks | ✅ Graph-proximity boosting |
| "What breaks if I change X?" | ❌ Not possible | ✅ Impact analysis |
| "Which tests cover this code?" | ❌ Not possible | ✅ Test coverage edges |
| Architecture validation | ❌ Not possible | ✅ Layer rules enforced |
| Repository scale | <10k files | ✅ 100k+ files |
| Index persistence | ❌ In-memory | ✅ PostgreSQL |
| Parser quality | Regex fallback for 5+ languages | ✅ tree-sitter for 5+ languages |

---

## 12. Prior Art & Inspiration

| Tool/Project | Concept | DevPilot Application |
|-------------|---------|---------------------|
| **Sourcegraph** (SCIP indexing) | Cross-file symbol graph, precise code navigation | Symbol resolution, call graph |
| **Tree-sitter** | Robust incremental parsing | Language-agnostic code analysis |
| **pyright/pylance** | Type inference, symbol resolution | Type-aware code graph (future) |
| **dependency-cruiser** (JS) | Module dependency graph + rules | Dependency graph, layer validation |
| **rust-analyzer** | IDE-grade code intelligence | Full symbol index with references |
| **CodeBERT / GraphCodeBERT** | Code + data flow graph embeddings | Enhanced semantic retrieval |
| **OpenGrok** | Universal code search + cross-ref | Cross-reference database |
| **Ast-grep** | AST pattern matching | Structural code search queries |

---

## 13. Risk & Mitigation

| Risk | Impact | Mitigation |
|------|--------|------------|
| tree-sitter adds heavy dependency | Medium | Make optional; fall back to regex parsers |
| Large repositories strain graph storage | Medium | Partition by snapshot; limit edge count per build; archive old snapshots |
| Cross-file resolution is slow | High | Two-pass strategy; parallel per-file parsing; incremental updates |
| Cycle detection is expensive on large graphs | Low | Tarjan's algorithm; abort early if cycles found |
| pgvector not available | Medium | Fall back to in-memory vector index (Phase 5 behavior) |
| Graph quality depends on language support | Medium | Python first (full support); other languages phased in |
