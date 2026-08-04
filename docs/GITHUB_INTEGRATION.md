# GitHub Read Integration — Phase 3

> Safely bridge GitHub and the Repository Intelligence Engine.

## Overview

Phase 3 establishes a READ-ONLY GitHub integration that validates URLs, fetches repository metadata, lists branches and issues, safely acquires a local repository snapshot (shallow clone), and feeds it through the existing Phase 2 Repository Intelligence Engine for deterministic analysis.

## Architecture

```
GitHub URL
    │
    ▼
┌──────────────────────┐
│   RemoteAnalysisWorkflow    │
├──────────────────────┤
│ 1. validate_input    │  ← URL format / owner+repo / host check
│ 2. run_analysis      │  ← fetch metadata → acquire → analyze
│    └─ RemoteRepositoryAnalyzer
│       ├─ GitHubService.get_repo_metadata()
│       ├─ RepositoryAcquisitionService.acquire()
│       ├─ RepositoryAnalyzer.analyze()
│       └─ cleanup()
└──────┬───────────────┘
       │
       ▼
┌──────────────────────┐
│ RemoteRepositoryProfile   │
│ ┌─ GitHubRepoMetadata     │ ← GitHub source of truth
│ ├─ RepositoryProfile      │ ← Phase 2 intelligence engine
│ ├─ AcquisitionMetadata    │ ← clone details
│ └─ warnings / errors      │
└──────────────────────────┘
```

## Components

### 1. GitHub Client (`app/services/github.py`)

Upgraded from Phase 1 with:

| Feature | Description |
|---------|-------------|
| **Pagination** | `_get_paginated()` with configurable max pages and per-page limits |
| **Rate-limit tracking** | Captures `X-RateLimit-Remaining`, `Limit`, `Reset` headers |
| **Retry logic** | Retries on 5xx and timeout errors only (never on 4xx) |
| **Token redaction** | `_redact_token()` masks tokens in error messages |
| **Branch support** | `list_branches()`, `get_default_branch()`, `branch_exists()` |
| **Issue reading** | `get_issue()`, `list_issues()` with state filter |
| **PR detection** | `_parse_issue()` detects `pull_request` key in API responses |
| **URL parsing** | `parse_any_url()` handles repo, issue, tree/blob URLs |

### 2. GitHub Models (`app/models/github.py`)

Typed Pydantic models for all GitHub resources:

| Model | Fields |
|-------|--------|
| `GitHubRepositoryRef` | owner, repo, ref |
| `GitHubIssueRef` | owner, repo, number |
| `GitHubRepoMetadata` | full_name, description, default_branch, stars, forks, topics, etc. |
| `GitHubBranch` | name, sha, protected |
| `GitHubIssue` | number, title, body, state, labels, PR flag, etc. |
| `RateLimitInfo` | remaining, limit, reset |
| `RemoteRepositoryProfile` | github metadata + intelligence profile + acquisition info |

### 3. Repository Acquisition (`app/services/acquisition.py`)

Safe Git-based acquisition with:

| Control | Implementation |
|---------|---------------|
| **No shell injection** | `asyncio.create_subprocess_exec()` with argument array |
| **Owner/repo validation** | Regex `^[a-zA-Z0-9_.-]+$` |
| **Token auth** | `https://x-access-token:{token}@github.com/...` for private repos |
| **Token protection** | Never logged, never in git config, never in error messages |
| **Shallow clone** | `--depth 1 --no-tags` by default |
| **Timeout** | 120s for clone, 30s for git operations |
| **Workspace isolation** | UUID-based temp dirs under configurable base |
| **Cleanup** | Automatic on failure, explicit `cleanup()` on success |
| **Hook safety** | Removes execute permissions from `.git/hooks/` |
| **Resource limits** | `FORBIDDEN_WORKSPACE_PARENTS` prevents system-directory workspace |

### 4. Remote Analyzer (`app/services/remote_analyzer.py`)

Orchestrator that connects GitHub → Acquisition → Intelligence Engine:

```
RemoteRepositoryAnalyzer.analyze(url, ref)
    1. Parse URL → owner/repo
    2. Fetch GitHub metadata via GitHubService
    3. Resolve ref (default branch if none specified)
    4. Shallow clone via RepositoryAcquisitionService
    5. Run RepositoryAnalyzer.analyze() on local snapshot
    6. Combine into RemoteRepositoryProfile
    7. Clean up workspace
    8. Return result
```

### 5. API Endpoints (`app/api/v1/github.py`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/github/repositories/analyze` | Full remote analysis (fetch + acquire + analyze) |
| GET | `/api/v1/github/repositories/{owner}/{repo}` | Repository metadata |
| GET | `/api/v1/github/repositories/{owner}/{repo}/branches` | List branches |
| GET | `/api/v1/github/repositories/{owner}/{repo}/issues` | List issues |
| GET | `/api/v1/github/repositories/{owner}/{repo}/issues/{number}` | Get single issue |

### 6. CLI

```bash
# Full remote analysis
python -m app.cli github analyze https://github.com/owner/repo
python -m app.cli github analyze https://github.com/owner/repo --ref main

# Fetch issue
python -m app.cli github issue https://github.com/owner/repo/issues/42

# Repository info
python -m app.cli github info https://github.com/owner/repo
```

## Authentication

- Token is configured via `GITHUB_TOKEN` environment variable or `.env`
- Optional for public repositories
- Required for private repositories and higher rate limits (5000/hr vs 60/hr)
- Token is injected into clone URL as `x-access-token:{token}@github.com`
- Token is **never logged**, **never returned by API**, **never persisted in profiles**
- Safe preview format: `abcd***` (first 4 chars + asterisks)

## Security Boundaries

| Threat | Protection |
|--------|-----------|
| Malicious URL | `parse_any_url()` validates format; `_validate_repo_url()` checks owner/repo with safe regex |
| Command injection | `subprocess_exec()` with argument array — no shell interpolation |
| Path traversal | UUID-based temp dirs, forbidden parents check |
| Symlink attacks | Git hooks disabled (execute removed) |
| Secret leakage | Token redacted from logs, errors, git config |
| Code execution | Never runs `npm install`, `pip install`, tests, or build scripts |
| Oversized repos | Default shallow clone; configurable timeouts |
| Rate limit abuse | Handles 429/403; never blindly retries auth failures |

## Rate Limiting

- GitHub API rate limit is tracked per-response via `X-RateLimit-*` headers
- `GitHubService.get_rate_limit_info()` returns last known limit state
- Warning emitted when remaining < 10
- `GitHubRateLimitError` raised on 429 or 403 with zero remaining
- No automatic retry on rate-limit errors

## Pagination

- All list endpoints use `_get_paginated()` with configurable `max_pages` and `per_page`
- Default: 30 items per page, max 3 pages (~90 items)
- API endpoints expose `max_pages` and `per_page` query parameters with validated limits
- Pagination stops early when server returns fewer items than `per_page`

## Private Repository Behavior

- When `GITHUB_TOKEN` is set, acquisition uses authenticated clone URL
- Token must have at least `repo` scope for private repositories
- If token is missing or invalid, private repositories will fail with authentication error
- Token is never written to git config files

## Limitations

| Limitation | Impact | Future Work |
|-----------|--------|-------------|
| No OAuth flow | Token must be configured via env var | OAuth login for web UI |
| No commit/tag listing | Only branches are listed | Extend branch service |
| No webhook support | Real-time updates not available | Webhook integration |
| No GitHub App auth | Only PAT/token authentication | GitHub App support |
| Acquired repos read-only | No push/PR capabilities | Phase 4+ GitHub write ops |
| No cache | Each API call goes to GitHub | In-memory caching |

## Testing

Unit tests use mocked HTTP responses — no live GitHub dependency:

```bash
pytest tests/test_github_integration.py -v
```

Optional live integration test (requires network):

```bash
LIVE_GITHUB=true python -m pytest tests/test_github_integration.py -v -k live
# Or use the standalone script:
python scripts/test_github_integration.py https://github.com/octocat/Hello-World
```
