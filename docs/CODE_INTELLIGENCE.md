# Code Intelligence — Phase 5 + Phase 12

## Overview

DevPilot's Code Intelligence provides two layers of repository understanding:

- **Phase 5** — Code-Aware Repository Indexing & Hybrid RAG (file/chunk-oriented retrieval)
- **Phase 12** — Advanced Code Intelligence + Semantic Repository Graph (structural understanding)

Together, they answer both "what files contain X" and "how do these symbols relate" questions.

---

## Phase 5 — Code-Aware Indexing & RAG

Phase 5 treats code as chunked text for retrieval. See `app/rag/` for implementation.

### Components
- **PythonParser** — AST-based symbol extraction for Python
- **FallbackParser** — Regex-based parsing for unsupported languages
- **CodeChunker** — Splits files at semantic boundaries (class, function)
- **LexicalIndex** — Full-text search of code chunks
- **SymbolIndex** — Symbol name search
- **VectorIndex** — Embedding-based similarity search (fake mode by default)
- **HybridRetriever** — Weighted fusion of all indexes
- **PlanContextRetriever** — Plan-aware retrieval for multi-step plans

---

## Phase 12 — Semantic Repository Graph

Phase 12 builds a **structural directed graph** of the repository connecting symbols through typed relationships.

### Architecture

```text
CodeIntelligenceService
        │
        ├── PythonSymbolParser       (stdlib AST)
        ├── TypeScriptJSParser       (regex + brace-matching)
        ├── JavaSymbolParser          (tree-sitter)
        ├── GoSymbolParser            (tree-sitter)
        ├── RustSymbolParser          (tree-sitter)
        ├── CppSymbolParser           (tree-sitter — C & C++)
        ├── CSharpSymbolParser        (tree-sitter)
        ├── KotlinSymbolParser        (tree-sitter)
        ├── SwiftSymbolParser         (tree-sitter)
        ├── RubySymbolParser          (tree-sitter)
        ├── PhpSymbolParser           (tree-sitter)
        │
        ├── SemanticRepositoryGraph
        │       ├── Nodes (symbols)
        │       └── Edges (relationships)
        │
        ├── ImpactAnalysisService
        ├── IncrementalIndexer
        └── GraphAwareRetriever
```

### Parsers — Language Coverage (11 languages)

| Language | Parser | Engine | Status | Key Symbols Extracted |
|----------|--------|--------|--------|-----------------------|
| Python | `PythonSymbolParser` | stdlib `ast` | ✅ Complete | modules, classes, functions, async functions, decorators, imports, calls, test classes/files |
| TypeScript/JS | `TypeScriptJSParser` | regex + brace-matching | ✅ Complete | classes, interfaces, types, enums, functions (async/gen), methods, constructors, get/set, imports/exports, const/let/var |
| Java | `JavaSymbolParser` | tree-sitter 0.26 | ✅ Complete | classes, interfaces, enums, records, methods, constructors, fields, imports, annotations, extends/implements |
| Go | `GoSymbolParser` | tree-sitter 0.26 | ✅ Complete | functions, methods (receiver MEMBER_OF), structs (fields), interfaces (methods), grouped imports, const/var |
| Rust | `RustSymbolParser` | tree-sitter 0.26 | ✅ Complete | functions, structs (fields), enums (variants), traits, impl blocks (methods), use declarations, const/static, type aliases, modules |
| C/C++ | `CppSymbolParser` | tree-sitter 0.26 | ✅ Complete | functions, structs, classes (methods/fields/inheritance), includes (imports), templates, enums, typedefs (C + C++ in one parser) |
| C# | `CSharpSymbolParser` | tree-sitter 0.26 | ✅ Complete | classes, interfaces, structs, enums, methods, properties, fields, usings, namespaces, inheritance |
| Kotlin | `KotlinSymbolParser` | tree-sitter 0.26 | ✅ Complete | classes (data/sealed), functions, objects, companion objects, interfaces, imports, packages |
| Swift | `SwiftSymbolParser` | tree-sitter 0.26 | ✅ Complete | classes/structs (properties/methods/initializers), functions, protocols, enums, imports, inheritance |
| Ruby | `RubySymbolParser` | tree-sitter 0.26 | ✅ Complete | classes, modules (with nesting tracking), methods, requires, requires_relative, constants |
| PHP | `PhpSymbolParser` | tree-sitter 0.26 | ✅ Complete | classes (abstract/final), interfaces, traits, functions, methods, properties, namespaces, use declarations |

Parser failures **degrade gracefully** — syntax errors in one file never fail the entire repository analysis. Each parser returns warnings for malformed files and continues to the next file.

### Semantic Entities (Nodes)

| Entity | Kind | Languages | Description |
|--------|------|-----------|-------------|
| File | `file` | All | Source file node |
| Module | `module` | Python, Ruby | Module/package definition |
| Class | `class` | All OOP languages | Class definition |
| Interface | `interface` | Java, TS, C#, Go, Kotlin, PHP, Swift | Interface/protocol definition |
| Struct | `struct` | Go, Rust, C/C++, C#, Swift | Struct/record definition |
| Enum | `enum` | Java, TS, Rust, C/C++, C#, Kotlin, Swift | Enumeration definition |
| Record | `record` | Java, C# | Immutable data record |
| Trait | `trait` | Rust, PHP | Trait definition |
| Object | `object` | Kotlin, Ruby | Singleton object |
| Function | `function` | All languages | Top-level function |
| Method | `method` | All OOP languages | Class/struct/impl method |
| Constructor | `constructor` | Java, TS, C++, C#, Kotlin, Swift | Object constructor |
| Async Function | `async_function` | Python, TS, JS, Rust | Async function |
| Generator | `generator` | Python, JS | Generator function |
| Property | `property` | Python, C#, Swift | Class property/attribute |
| Field | `field` | Java, Go, C/C++, Kotlin | Class/struct field member |
| Getter/Setter | `getter`/`setter` | TS, C#, Swift, Kotlin | Accessor methods |
| Type Alias | `type` | TypeScript, Rust, C/C++ | Type alias/typedef |
| Interface Method | `interface_method` | Go, Java | Method signature in interface |
| Annotation | `annotation` | Java, Kotlin, C# | Annotation/decorator/attribute |
| Import | `import` | All languages | Import/include/use directive |
| Constant | `constant` | Python, Go, Rust, C/C++, Ruby | Immutable constant |
| Variable | `variable` | Go, Swift, Rust | Top-level variable |
| Static | `static` | Rust, C/C++, C# | Static/associated item |
| Test Class | `test_class` | Python, C#, Java, Kotlin | Test class |
| Test File | `test_file` | All | Test file marker |

### Relationship Types (Edges)

| Relationship | Description | Example | Languages |
|-------------|-------------|---------|-----------|
| `CONTAINS` | Parent-child containment | File → Class, Class → Method | All |
| `IMPORTS` | Module dependency | File → Import/Include/Use | All |
| `EXPORTS` | Export declaration | File → Exported symbol | TS/JS, Go, Rust, PHP |
| `CALLS` | Function/method call | Method → Called function | Python, TS/JS |
| `INHERITS` | Class/interface inheritance | Class → Parent class, includes `extends` | Java, TS, C#, Kotlin, Swift, C++, PHP, Ruby |
| `IMPLEMENTS` | Interface/trait implementation | Class → Interface | Java, TS, C#, Rust, PHP |
| `MEMBER_OF` | Symbol belongs to parent | Method → Containing struct/class | Go (receiver) |
| `DEPENDS_ON` | General dependency | Module → Module | All |
| `TESTS` | Test coverage | Test file → Implementation | Python (test class naming) |
| `REFERENCES` | Symbol reference | Code → Referenced symbol | Python, TS/JS |
| `COMPOSES` | Composition/DI | Class → Injected dependency | Python, TS/JS |

### Confidence Levels

| Level | Meaning |
|-------|---------|
| `EXACT` | Statically verifiable (import, inheritance, struct field) |
| `HIGH` | Strong evidence (method call with matching name, tree-sitter receiver) |
| `MEDIUM` | Probable but ambiguous (dynamic call, regex-based resolution) |
| `UNRESOLVED` | Relationship exists but target not found in graph |

Edges never present uncertain relationships as guaranteed facts. Tree-sitter parsers produce higher-confidence edges due to full CST traversal.

### Graph Architecture

`SemanticRepositoryGraph` is an **in-memory directed graph** with:

- Forward and reverse edge indexes (O(1) lookups)
- BFS-based traversal with cycle detection
- Bounded limits: depth (max 5), fan-out (max 100), total nodes (max 500)
- Serialization to/from dict for persistence
- Name, file, and kind indexes for fast lookups

**Protected against:**
- Infinite loops (visited set per traversal)
- Cycles (cycles are traversed safely but don't repeat)
- Huge fan-out (configurable limit)
- Deep recursion (level-based BFS, not recursive)

### Incremental Indexing

`IncrementalIndexer` supports partial re-indexing:

1. SHA-256 content hash per file
2. Detect changes: ADDED, MODIFIED, DELETED, UNCHANGED
3. Remove stale symbols/edges from graph
4. Re-parse changed files only
5. Insert new symbols/edges

### Impact Analysis

`ImpactAnalysisService` determines what code is affected by a change:

- Traverses dependents (callers, inheritors, tests)
- Assigns risk levels: CRITICAL, HIGH, MEDIUM, LOW, NONE
- Returns related test files
- Evidence-backed results — no LLM-invented dependencies
- Bounded by configurable depth and max nodes

### Graph-Aware Retrieval

`GraphAwareRetriever` extends Phase 5 retrieval with graph context:

- Direct symbol matches from graph
- Neighborhood expansion (callers, callees, tests, dependencies)
- Relevance scoring combining graph distance and relationship priority
- `get_agent_context()` provides formatted context for LLM agent prompts

---

## API Endpoints (Phase 12)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/code-intelligence-v2/index` | Build semantic index |
| GET | `/api/v1/code-intelligence-v2/status` | Index status |
| POST | `/api/v1/code-intelligence-v2/index/reset` | Reset index |
| GET | `/api/v1/code-intelligence-v2/symbols` | List symbols |
| GET | `/api/v1/code-intelligence-v2/symbol/{id}` | Symbol detail |
| POST | `/api/v1/code-intelligence-v2/impact` | Impact analysis |
| POST | `/api/v1/code-intelligence-v2/retrieve` | Graph retrieval |
| GET | `/api/v1/code-intelligence-v2/capabilities` | List capabilities |

All graph queries are bounded by depth, limit, and result count.

---

## CLI Commands (Phase 12)

```bash
python -m app.cli code-index <path>          # Build semantic index
python -m app.cli code-symbols <path>         # List symbols
python -m app.cli code-symbol <path> <id>     # Symbol detail
python -m app.cli code-impact <path> <sym>    # Impact analysis
python -m app.cli code-retrieve <path> <sym>  # Graph retrieval
python -m app.cli code-status <path>          # Index status
```

---

## Database Persistence

Phase 12 adds three database tables via Alembic migration `003_add_code_intelligence.py`:

- `code_symbols` — Persistent symbol storage
- `code_relationships` — Persistent edge storage
- `repository_indexes` — Index metadata tracking

The graph abstraction remains storage-independent and can be migrated to other backends if needed.

---

## Security

- Parsing is **static analysis only** — never executes repository code
- No `import`, `exec`, `eval`, or `subprocess` calls on repository code
- Constant symbols store **type only** (not raw values) — secrets never leak into metadata
- Sensitive file patterns (`.env`, `*.pem`) are recognized and excluded
- All configuration from `.env` (gitignored)
- Tree-sitter parsers operate on byte content only — no module loading or code execution

---

## Performance & Limits

| Setting | Default | Purpose |
|---------|---------|---------|
| Max files | 10,000 | Repository file limit |
| Max file size | 500 KB | Per-file parse limit |
| Max graph depth | 5 | Traversal depth |
| Max fan-out | 100 | Edges per node limit |
| Max traversal nodes | 500 | Total traversal limit |
| Max query results | 500 | API result limit |

---

## Known Limitations

### Parser Coverage

- **TypeScript/JavaScript** — Uses regex + brace-matching rather than a full AST. May miss deeply nested structures, complex generics, or decorator-heavy code. Does not resolve JSX expressions beyond tag matching.
- **Tree-sitter parsers** (Java, Go, Rust, C/C++, C#, Kotlin, Swift, Ruby, PHP) — Full CST traversal but limited to symbol declarations and structural relations. Do not extract:
  - Control flow graphs or data flow
  - Runtime type information
  - Macro expansion (Rust declarative macros)
  - Conditional compilation branches (C `#ifdef`, Rust `#[cfg]`)
  - Metaprogramming (Ruby `define_method`, C# source generators)
  - Dynamic code (PHP `eval()`, Ruby `send()`, Python `getattr()`)

### Call Resolution

- **Python/TypeScript call extraction** — Name-based matching only. Does not resolve:
  - Virtual dispatch (polymorphic method calls)
  - Duck-typed interfaces
  - Decorator/wrapper interception
  - Monkey-patched or runtime-defined methods
- **Tree-sitter parsers** — Do not extract `CALLS` or `REFERENCES` edges. Call sites require analysis beyond declaration-level CST walking.

### Graph Completeness

- **Single-repository only** — No cross-repository symbol resolution. External dependencies (stdlib, third-party packages) are not included in the graph.
- **IncrementalIndexer language gap** — The incremental indexer only supports Python and TypeScript/JavaScript. Tree-sitter languages (Java, Go, Rust, C/C++, C#, Kotlin, Swift, Ruby, PHP) are not wired into the incremental update path and require a full re-index if changed.
- **Go receiver types** — `MEMBER_OF` relationships are created for pointer and value receivers, but the type resolution uses structural nesting rather than full type inference.

### Symbol Resolution

- **No cross-file symbol resolution** — The graph connects symbols within the same file reliably (CONTAINS, INHERITS, MEMBER_OF) but does not resolve references across files beyond import statements.
- **Import resolution** — Import paths are extracted but not validated against the filesystem. An import of `foo.Bar` creates an edge to a symbol `Bar` only if `Bar` exists in the graph.
- **Rust `use` declarations** — Grouped imports (`use std::{A, B};`) are extracted as a single import rather than individual symbols.
- **Ruby `require`** — Only extracts the require path string; no filesystem resolution to the actual file.

### Agent Integration

- Graph-aware context must be explicitly wired into each agent's prompt-building step (Planner, Coding, Test, Repair, Review). Currently implemented for Planner and Coding agents; Test, Repair, and Review agents lack graph integration.
- Graph evidence is text-based (formatted in `get_agent_context()`) — agents receive summaries, not raw graph queries.

### Performance

- **File discovery** walks the entire repository tree (respecting skip patterns). Very large monorepos (50k+ files) may hit the 10,000 file limit.
- **In-memory graph** — Active graph is not persisted to disk automatically. Requires explicit `save()` / `load()` calls (via PostgresRunStore or JSON serialization).
- **No incremental tree-sitter parser caching** — Each parse creates a fresh CST. No AST reuse across sequential indexing runs of unchanged files.

### Tree-Sitter Dependencies

Requires tree-sitter language packages (`tree-sitter-java`, `tree-sitter-go`, `tree-sitter-rust`, etc.) available at runtime. These are installed via pip but are platform-specific compiled extensions. Graceful degradation: if a language package is missing, the parser returns `([], [], [diagnostic])` and processing continues.
