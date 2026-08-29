# Changelog

All notable changes to DevPilot will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-08-29

### Added

#### Phase 21 - Run Replay & Deterministic Reproduction
- ReplayManifest model for capturing run state
- ReplayService with EXACT, DETERMINISTIC, and COMPARE modes
- Replay API endpoints (manifest, run, compare, audit, history)
- Frontend replay components (ReplaySection, ReplayTimeline, DifferenceViewer, AuditReport, ReplayHistory)
- Replay model tests
- CLI commands for replay operations
- Database migration 015 for replay persistence

#### Phase 20B - Production Reliability & Operational Hardening
- Provider health probing and recovery detection
- Adaptive timeouts based on observed latency
- Post-failure cooldown mechanism
- Health-based provider selection
- Startup configuration validation
- Operations Dashboard
- Correlation-ID middleware
- Request-size limits
- 50 new backend tests

#### Phase 20A - Cross-Repository Autonomous Runs
- Multi-repository runs support
- Per-repo scope enforcement
- Per-repo EKG ingestion
- Repository dashboard view builder
- Auxiliary repository editor

#### Phase 19B - Multi-Provider Failover & Reliability
- ProviderRouter with circuit breakers
- Bounded retry with exponential backoff
- Quota-aware failover
- Streaming failover
- Mid-stream token-loss recovery
- Per-capability provider chains
- Provider health metrics

#### Phase 18 - Engineering Knowledge Graph
- Temporal graph with nodes, edges, versions
- Provenance tracking
- Cross-repository support
- Interactive visualization with React Flow

#### Phase 17 - Collaborative Reasoning
- Evidence consensus detection
- Contradiction identification
- Engineering notebook
- Confidence scoring

#### Phase 16 - Autonomous Execution
- Goal tracking
- Dynamic replanning
- Budget enforcement
- Safe termination

#### Phase 15 - Multi-Agent Collaboration
- Handoffs between agents
- Decision recording
- Conflict detection
- Memory promotion

#### Phase 14 - Hardening & Documentation
- Integration tests
- Documentation improvements
- Security hardening

#### Phase 13 - Context Engineering
- Repository memory
- Context injection
- Intelligent reasoning

#### Phase 12 - Code Intelligence
- Semantic graph
- Code symbols
- Impact analysis
- pgvector integration

#### Phase 11 - Database Infrastructure
- PostgreSQL async engine
- Connection pooling
- Secret redaction
- CLI diagnostics

#### Phase 10 - Orchestration
- DevPilotRun model
- RunStateMachine
- Event system
- WebSocket updates

#### Phase 9 - Review & Quality Gate
- Deterministic review (21 checks)
- Evidence validation
- Quality gate decisions
- Hallucination protection

#### Phase 8 - Repair
- Failure diagnosis service
- Bounded repair loop
- Progress detection
- Rollback support

#### Phase 7 - Testing
- TestAgent
- ExecutionPolicy
- ControlledExecutionEngine
- Multiple framework parsers

#### Phase 6 - Coding
- CodingAgent
- PatchValidator
- SafePatchEngine
- Workspace management

#### Phase 5 - Code Retrieval
- Hybrid RAG
- Lexical index
- Symbol index
- Vector index

#### Phase 4 - Planning
- PlannerAgent
- PlanValidator
- Structured requirements

#### Phase 3 - GitHub Integration
- GitHub REST API
- Repository acquisition
- Issue fetching

#### Phase 2 - Repository Intelligence
- 9 detector services
- Language detection
- Technology detection
- Dependency parsing

#### Phase 1 - Foundation
- FastAPI application
- LLM abstraction
- Agent framework
- GitHub service

### Changed

- Upgraded to NVIDIA NIM as default provider
- Improved provider failover chain
- Enhanced error handling

### Fixed

- JSON repair for doubled braces
- FixAgent plan_id attribute error
- TTL boundary flakes in tests

---

## [Unreleased]

### Planned

- Enterprise features (multi-tenancy, SSO/RBAC)
- Self-hosted inference fabric
- Fine-tuning capabilities
- GitHub App integration
