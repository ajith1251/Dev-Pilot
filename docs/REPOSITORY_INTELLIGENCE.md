# Repository Intelligence Engine

> Phase 2 of DevPilot — Deterministic local repository analysis.

## Overview

The Repository Intelligence Engine converts an arbitrary local software repository into a structured `RepositoryProfile` that future DevPilot agents can reason over. It answers: *"What is this repository, how is it structured, what technologies does it use, how is it built/tested, and which files matter?"*

**Core principle: deterministic first, AI second.** The engine works fully without an LLM API key. No LLM calls are made during analysis.

## Architecture

```
Repository Path (local)
         │
         ▼
┌─────────────────────────────────────────────┐
│            RepositoryAnalyzer                │
│  (orchestrator — coordinates all detectors)  │
└──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬─────┘
   │  │  │  │  │  │  │  │  │  │  │  │  │
   ▼  ▼  ▼  ▼  ▼  ▼  ▼  ▼  ▼  ▼  ▼  ▼  ▼
  1. RepositoryScanner — safe file walker
  2. LanguageDetector — extension → language
  3. TechnologyDetector — evidence-based tech detection
  4. DependencyAnalyzer — manifest parsing
  5. CommandDetector — build/test/lint discovery
  6. FileClassifier — file category classification
  7. ProjectDetector — monorepo module detection
  8. ImportantFileDetector — architecture-critical files
  9. TreeGenerator — compact tree representation
         │
         ▼
┌─────────────────────────────────────────────┐
│            RepositoryProfile                 │
│  (Pydantic model — comprehensive output)     │
└─────────────────────────────────────────────┘
```

## Services

### 1. RepositoryScanner (`app/services/repository_scanner.py`)

Safe, recursive file system traversal with:
- Configurable exclusions (`.git`, `node_modules`, `__pycache__`, etc.)
- Symlink loop detection
- Large file limits (default 10 MB per file, 500 MB total)
- Max depth limits (default 50)
- Max file count limits (default 100,000)
- `.gitignore` pattern support (simplified)
- Binary file detection by extension
- Sensitive file detection (by name/extension — never reads contents)

### 2. LanguageDetector (`app/services/language_detector.py`)

Extension-to-language mapping for 50+ languages:
- Python, JavaScript, TypeScript, Java, Go, Rust
- C, C++, C#, Ruby, PHP, Swift
- HTML, CSS, SCSS, Less, SQL
- Shell, PowerShell, Batch
- Markup/Data: JSON, YAML, TOML, XML, Markdown
- Vue, Svelte, JSX, TSX
- And many more...

Produces file count, byte count, and percentage statistics.

### 3. TechnologyDetector (`app/services/technology_detector.py`)

Evidence-based framework/tool detection. Each detection includes:
- **Name**: e.g. "Next.js", "FastAPI", "pytest"
- **Category**: frontend|backend|testing|database|devops|build_tool
- **Confidence**: HIGH|MEDIUM|LOW|INFERRED
- **Evidence**: List of specific file/config patterns matched

Supported technologies (30+):
- **Frontend**: Next.js, React, Vue, Angular, Svelte, Vite, Tailwind CSS
- **Backend**: FastAPI, Flask, Django, Express, Fastify, NestJS
- **Testing**: Jest, Vitest, Playwright, Cypress, pytest
- **DevOps**: Docker, Docker Compose, GitHub Actions
- **Build**: Webpack, Rollup, esbuild, Make
- **Database**: SQLAlchemy, Prisma (detected from dependencies)

### 4. DependencyAnalyzer (`app/services/dependency_analyzer.py`)

Parse common package manifests:
- `package.json` (npm/yarn/pnpm)
- `requirements.txt` (pip)
- `pyproject.toml` (Poetry/PDM/PEP 621)
- `Pipfile` (Pipenv)
- `Cargo.toml` (Cargo)
- `go.mod` (Go modules)

Returns structured `Dependency` objects with name, version, type (runtime/dev/optional), ecosystem, and manifest path.

### 5. CommandDetector (`app/services/command_detector.py`)

Discover commands from configuration:
- `package.json` scripts → dev, build, test, lint, typecheck, etc.
- `Makefile` targets
- `pyproject.toml` scripts (Poetry)
- Conventional commands (pytest)

Does NOT execute commands — only identifies them.

### 6. FileClassifier (`app/services/file_classifier.py`)

Classifies files into 16 categories:
- source, test, configuration, documentation
- dependency_manifest, lockfile, migration, ci_cd
- infrastructure, generated, asset, data, script, build, template, unknown

### 7. ProjectDetector (`app/services/project_detector.py`)

Monorepo support: detects modules by finding manifest files (`package.json`, `pyproject.toml`, etc.) at different directory depths. Each module gets a type, language list, and optional package manager.

### 8. ImportantFileDetector (`app/services/important_file_detector.py`)

Identifies files critical for understanding architecture:
- README, LICENSE, CONTRIBUTING
- Package manifests and build configs
- Application entry points
- Routes, controllers, models, services directories

Each file gets an importance score (0-1) and a human-readable reason.

### 9. TreeGenerator (`app/services/tree_generator.py`)

Generates compact tree representation:
```
project/
├── frontend/
│   ├── app/
│   ├── components/
│   └── package.json
├── backend/
│   ├── app/
│   └── pyproject.toml
└── README.md
```

Configurable max depth and file count. Collapses non-important files.

## Output Model

`RepositoryProfile` (`app/models/profile.py`):

| Field | Type | Description |
|-------|------|-------------|
| `name` | str | Repository directory name |
| `scan` | ScanMetadata | Duration, file counts, errors/warnings |
| `languages` | List[LanguageEntry] | Detected languages with percentages |
| `technologies` | List[TechnologyDetection] | Frameworks/tools with evidence |
| `package_managers` | List[PackageManager] | npm, pip, cargo, etc. |
| `dependencies` | List[Dependency] | Extracted dependencies |
| `commands` | List[RepositoryCommand] | Build/test/lint commands |
| `modules` | List[RepositoryModule] | Monorepo modules |
| `file_categories` | Dict[str, int] | Category → count |
| `important_files` | List[ImportantFile] | Architecture-critical files |
| `tree` | RepositoryTree | Compact tree representation |
| `warnings` | List[str] | Analysis warnings |

## Security

- **Read-only**: Never executes code, installs deps, or modifies files
- **Sensitive file protection**: Detects `.env`, credentials, keys by name — never reads contents
- **Path validation**: Rejects paths outside allowed roots, path traversal attempts
- **Resource limits**: File size (10MB), total scan (500MB), file count (100K), depth (50)
- **Symlink protection**: Loop detection, optional symlink following
- **Ignored directories**: `.git`, `node_modules`, `__pycache__`, build artifacts, IDE metadata

## API

### POST `/api/v1/repositories/analyze`

```json
{
  "path": "/path/to/repository",
  "max_depth": 10
}
```

Response:
```json
{
  "success": true,
  "data": {
    "name": "my-project",
    "languages": [...],
    "technologies": [...],
    "commands": [...],
    "modules": [...],
    ...
  },
  "message": "Analysis completed"
}
```

### GET `/api/v1/repositories/capabilities`

Returns supported languages, technologies, module indicators, and command sources.

## CLI

```bash
python -m app.cli analyze /path/to/repository
python -m app.cli analyze . --depth 5
```

## Workflow

The `RepositoryAnalysisWorkflow` (`app/workflows/repository_analysis.py`) follows a three-node graph:

```
START → validate_repository → analyze_repository → validate_profile → END
```

State is represented by `AnalysisState` — a dataclass following the pattern used by parent-repo LangGraph agents. Future migration to actual `langgraph.StateGraph` is straightforward: each node maps directly to a LangGraph node function.

## Limitations

- No semantic RAG/embeddings (Phase 5)
- No remote GitHub repository fetching (Phase 3)
- `.gitignore` support is simplified (directory-level patterns only)
- Monorepo support detects modules but doesn't infer dependency relationships
- Technology detection is heuristic — may miss exotic setups
- Dependency parsing is simplified (may fail on complex version specifiers)
- Makefile target detection is basic (handles `target:` lines only)
