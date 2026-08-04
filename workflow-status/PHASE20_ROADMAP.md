# Phase 20 Roadmap — Cross-Repository Autonomous Engineering & Production Readiness

> **Status**: PROPOSED (not started)
> **Date**: August 4, 2026
> **Basis**: Phase 19C is fully complete (`5cc371a` + `1644fb3` + `2cc929b`). The
> knowledge layer (EKG + organization graph) is now **cross-repository**, but the
> execution layer (`OrchestrationService`) is still bound to **one repository per
> run**. Phase 20 closes that gap and hardens the platform for production.

---

## 1. Why Phase 20

DevPilot today can:

- acquire + link several repositories into an organization graph
  (`POST /org/acquire-multi`, `OrganizationKnowledgeGraphService.acquire_multi`);
- retrieve cross-repository evidence at planning time
  (`ContextEngine._build_organization_graph_context`, org-scope query);
- run an end-to-end engineering loop against a **single** repository
  (`OrchestrationService.execute_run`, `run.source.repository_path`).

The execution pipeline still cannot span repositories: a run is acquired from one
path (`orchestration_service._stage_acquisition`), analysed/planned/coded/tested
inside that one workspace, and its patches are validated against that one
checkout. Phase 20 makes the orchestrator **multi-repository aware** while keeping
the deterministic gates (LLMs propose, gates decide) and per-repo isolation intact.

---

## 2. Workstreams

### A. Cross-Repository Autonomous Runs (core Phase 20)

Close the execution gap on top of the org graph.

- **A1. RunSpec multi-repo surface** — extend `RunSource`/run-creation
  (`backend/app/models/orchestration.py:238`) with an optional
  `repositories: List[RepoSpec]` (one primary + auxiliary repos). Backwards
  compatible: a single `repository_path` keeps today's behavior.
- **A2. Orchestrator acquisition** — `_stage_acquisition` acquires the primary
  repo as today and materializes auxiliary repos via `acquire_multi`
  (deterministic `source=local`, or `github` when an acquisition service is
  injected); each aux repo is registered in the org graph + linked by declared
  relationships. Bounded by `MAX_REPOSITORIES_PER_ORG`.
- **A3. Cross-repo planning context** — feed org-scope retrieval
  (`QueryScope.AUTO`/`ORGANIZATION`) into the planner stage when the task spans
  repos (ContextEngine hook already exists — wire it through planning).
- **A4. Per-repo scope enforcement** — extend the deterministic scope controller
  (`autonomy_service.ScopeController`) + `deterministic_review._check_file_scope`
  to track which changed path belongs to which repository; a patch is validated
  against its own repo's checkout (`SafePatchEngine`), never cross-checkout.
- **A5. Per-repo EKG ingestion** — `record_run` already stamps `repository_id`;
  ensure cross-repo runs ingest their patches into each repo's namespace and link
  the run across namespaces via the org graph.
- **A6. API/CLI/frontend** — `POST /api/v1/runs` + `python -m app.cli run`
  accept `repositories`; dashboard run form exposes optional aux repos.

### B. Production Reliability (recommendation 3 follow-through)

- **B1.** Billing on the Gemini key or **Vertex AI** (IAM, no training) — infra
  decision, needs user action (recommendation 3).
- **B2.** Typed fallback lists per capability
  (`DEVPILOT_LLM_PROVIDER_FALLBACKS`, `MULTI_PROVIDER_ROUTING.md` §9).
- **B3.** Mid-stream token-loss failover (resend prompt with full prefix) for long
  generations.

### C. Live E2E Verification (recommendation 4)

- Re-run `scripts/demo_phase17.py --live` and
  `scripts/verify_api_durability.py --live` after a Gemini quota reset; refresh
  `docs/GEMINI_API_KEY_REPORT.md`.

### D. Org-Graph UI Parity (polish)

- Upgrade `/dashboard/organization-graph` from the legacy `ForceDirectedGraph` to
  the React Flow engine + timeline diff + live WS used on
  `/dashboard/engineering-graph` (`InteractiveGraph.tsx`, `useGraphSocket.ts`).

### E. Test-Framework Parsers (extension)

- Add pytest-style parsers for unittest XML / Vitest JSON / Jest JSON
  (`docs/TESTING_AND_EXECUTION.md:250` notes the current pytest-only parser).

---

## 3. Recommended sequencing

| Order | Slice | Why first |
|---|---|---|
| 1 | **A1 + A2** (multi-repo run surface + acquisition) | smallest coherent vertical: create a run over 2 local repos, both acquired + linked |
| 2 | **A3 + A5** (cross-repo context + per-repo ingestion) | planner sees org evidence; evidence lands per-namespace |
| 3 | **A4** (per-repo scope enforcement) | safety gate before any patch crosses checkouts |
| 4 | **A6** (API/CLI/frontend surface) | surfaces the vertical for demo + tests |
| 5 | B/D/E | hardening + polish (B1 needs a user infra decision) |

---

## 4. Invariants preserved

- LLMs only propose; deterministic gates decide (`SafePatchEngine` sole mutator,
  `ControlledExecutionEngine` sole runner).
- Repository isolation: private nodes only surface through explicit
  `link_repositories` bridges; a patch never touches another repo's checkout.
- Evidence-only: no chain-of-thought, credentials, or provider config in outputs.
- Bounded: `MAX_REPOSITORIES_PER_ORG`, `MAX_QUERY_RESULTS`, budget enforcement.

---

## 5. Verification

- New deterministic tests: multi-repo run creation + acquisition (2 local repos),
  org-scope planning context inclusion, per-repo scope-violation rejection,
  per-repo EKG namespace ingestion, API/CLI contract.
- Full deterministic suite stays green (1602 passed / 18 skipped / 1 pre-existing
  env failure).
- `scripts/demo_phase20.py` demos A–E (deterministic, no paid LLM).
