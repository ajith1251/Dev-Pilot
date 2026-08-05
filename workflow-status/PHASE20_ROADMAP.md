# Phase 20 Roadmap — Cross-Repository Autonomous Engineering & Production Readiness

> **Status**: IN PROGRESS — slices A1–A4 DONE (Sessions 28–31), A5 next
> **Date**: August 5, 2026
> **Basis**: Phase 19C is fully complete (`5cc371a` + `1644fb3` + `2cc929b`). The
> knowledge layer (EKG + organization graph) is now **cross-repository**, but the
> execution layer (`OrchestrationService`) was bound to **one repository per
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
  **✅ DONE (Session 28):** `RepositorySpec` + `RunSource.repositories` +
  `DevPilotRun.auxiliary_repositories` (+ result field + `AUXILIARY_REPOSITORIES_ACQUIRED` event).
- **A2. Orchestrator acquisition** — `_stage_acquisition` acquires the primary
  repo as today and materializes auxiliary repos via `acquire_multi`
  (deterministic `source=local`, or `github` when an acquisition service is
  injected); each aux repo is registered in the org graph + linked by declared
  relationships. Bounded by `MAX_REPOSITORIES_PER_ORG`.
  **✅ DONE (Session 28):** `_materialize_auxiliary_repositories` delegates to
  `OrganizationKnowledgeGraphService.acquire_and_link_repositories`, wired into
  `execute_run` after the acquisition branch; API/CLI/`POST /api/v1/runs`
  accept `repositories` (`--aux-repo ID=PATH`). 10 new tests;
  full suite 1612 passed / 18 skipped / 1 pre-existing env failure.
- **A3. Cross-repo planning context** — feed org-scope retrieval
  (`QueryScope.AUTO`/`ORGANIZATION`) into the planner stage when the task spans
  repos (ContextEngine hook already exists — wire it through planning).
  **✅ DONE (Session 29):** `ContextEngine.build_context` gained
  `include_organization_context: bool = False`; when set, `_build_organization_graph_context`
  queries with `QueryScope.ORGANIZATION` (bypassing the AUTO vocabulary gate).
  `OrchestrationService` now holds one shared `_get_org_graph()` instance
  (refactored from the module-level helper) and injects it into the ContextEngine
  (`_get_context_engine`), so auxiliary repos materialized by this run are
  immediately visible; the planner's `_build_agent_context` passes
  `include_organization_context=True` only when the run is explicitly multi-repo
  AND materialized (`source.repositories` + `auxiliary_repositories` both set) —
  single-repo runs stay isolated. 7 new tests (3 engine-level + 4 orchestrator-level);
  full suite **1619 passed / 18 skipped / 1 pre-existing env failure**.
- **A4. Per-repo scope enforcement** — extend the deterministic scope controller
  (`autonomy_service.ScopeController`) + `deterministic_review._check_file_scope`
  to track which changed path belongs to which repository; a patch is validated
  against its own repo's checkout (`SafePatchEngine`), never cross-checkout.
  **✅ DONE (Session 31):** `RepositoryScopeRegistry` (new
  `app/services/repository_scope.py`) + `PatchSet.repository_id` provenance +
  `RepositoryPatchInput`/`RepositoryPatchResult`; `SafePatchEngine`
  `check_repository_ownership` gate wired into `dry_run`/`apply`;
  `DeterministicReview` DET-020 (blocking) + `ScopeController` repository
  scopes; orchestrator validates + applies each repo's patch against ITS OWN
  checkout (`_stage_patch_validation`/`_stage_patch_application`,
  `_validate_single_repo_patch`/`_apply_single_repo_patch`), review
  `extra_context` carries `repository_patch_results`/`repository_scopes`,
  `_build_result` aggregates `repo_validation`, autonomy evidence populates
  `repository_validation`; API `POST /api/v1/runs` `repo_patches` (400 on
  malformed) + CLI `--repo-patch ID=WORKSPACE=PATCH_JSON`. 21 new tests
  (`tests/test_phase20_repo_scope.py`); `scripts/demo_phase20.py` demos A–F ALL
  PASS; full suite 1640 passed / 18 skipped / 1 pre-existing env failure.
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
| 1 | **A1 + A2** (multi-repo run surface + acquisition) | smallest coherent vertical: create a run over 2 local repos, both acquired + linked — **✅ DONE (Session 28)** |
| 2 | **A3 + A5** (cross-repo context + per-repo ingestion) | planner sees org evidence; evidence lands per-namespace — **A3 ✅ DONE (Session 29)**, A5 next |
| 3 | **A4** (per-repo scope enforcement) | safety gate before any patch crosses checkouts — **✅ DONE (Session 31)** |
| 4 | **A6** (API/CLI/frontend surface) | surfaces the vertical for demo + tests — API/CLI already accept `repositories` (done with A1/A2); remaining: dashboard run form |
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
  **✅ A1+A2 covered (10 tests in `tests/test_phase20_multi_repo_run.py`);**
  **✅ A3 covered (7 tests: 3 in `test_organization_graph.py` +
  4 in `test_phase20_multi_repo_run.py`);**
  **✅ A4 covered (21 tests in `tests/test_phase20_repo_scope.py`).**
- Full deterministic suite stays green (**1640 passed / 18 skipped / 1 pre-existing
  env failure**).
- `scripts/demo_phase20.py` demos A–F ALL PASS (deterministic, no paid LLM).
