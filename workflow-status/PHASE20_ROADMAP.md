# Phase 20 Roadmap — Cross-Repository Autonomous Engineering & Production Readiness

> **Status**: COMPLETE — A1–A6 DONE (Sessions 28–33), Phase 20B DONE (Sessions 34/35/37),
> workstream E DONE (Session 38)
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
  **✅ DONE (Session 32):**
  `OrganizationKnowledgeGraphService.record_run_across_namespaces(run,
  reasoning_outcome=None)` — shared evidence via the org-level graph's
  `record_run`, then each per-repo patch result is ingested into ITS OWN repo
  namespace (`_ingest_run_into_repository_namespace`: RUN + REPOSITORY + PATCH +
  FILE nodes, REFERENCES RUN→REPO / RUN→PATCH + MODIFIES PATCH→FILE edges,
  PATCH payload carries `files_changed`/`files`/`validation_status`/
  `application_status`/`changes_applied`/`changes_attempted`), then org-level
  REFERENCES edges link the RUN node to each involved repo node
  (`_link_run_to_repositories`, cross-namespace edge target id like
  `REPO::repo-b`). Orchestrator `_ingest_into_graph` delegates to
  `record_run_across_namespaces` for any cross-repo run (per-repo patches, source
  `repo_patches`, or `auxiliary_repositories`); single-repo runs fall back to
  `record_run`. `RepositoryPatchResult.changed_files` added and populated
  (`[c.path for c in patch.changes]`). Fixed pre-existing latent bug:
  `_validate_single_repo_patch` never `await`ed `_enrich_patch_hashes`
  (per-repo MODIFY/DELETE always rejected). 13 new tests
  (`tests/test_phase20_repo_ingestion.py`); `scripts/demo_phase20.py` demo G
  (per-repo ingest evidence) added, demos A–G ALL PASS.
- **A6. API/CLI/frontend** — `POST /api/v1/runs` + `python -m app.cli run`
  accept `repositories`; dashboard run form exposes optional aux repos.
  **✅ DONE (Session 33):** API run-detail surface — `_sanitize_run`
  (`backend/app/api/v1/orchestration.py`) now exposes `auxiliary_repositories`
  (raw spec list) + `repo_validation` (per-repo `RepositoryPatchResult.summary()`
  list, incl. `changed_files`) on `GET /api/v1/runs/{id}` (the create-side
  `repositories` field + CLI `--aux-repo` were already wired in A1/A2).
  Frontend: `AuxiliaryRepositorySpec` + `RepositoryPatchValidation` types in
  `frontend/src/lib/api/client.ts`, `runsApi.create` accepts optional
  `repositories`; `CreateRunModal` (`frontend/src/app/dashboard/runs/page.tsx`)
  gained an aux-repo editor (dynamic add/remove, local path OR
  github owner/repo/ref, invalid rows dropped client-side, submitted as
  `repositories`); run-detail page (`dashboard/runs/[id]/page.tsx`) renders the
  aux repos in the Source card + a "Repository Validation" card
  (status/changes/changed_files/errors). Tests: 2 new backend tests
  (`TestRunDetailApiSurface` in `tests/test_phase20_repo_ingestion.py`, 15 total
  in that file) + new `frontend/src/lib/api/client.test.ts` (2 tests); frontend
  vitest 39/39, `next build` EXIT=0. Also hardened the TTL-boundary flake:
  `test_exhausted_marker_expires_after_ttl` simulated exactly
  `marked_at + ttl` but the provider prunes with `now - ts >= ttl`, so float
  rounding could flip the `>=`; now uses `marked_at + 3601.0` (same hardening
  previously applied to `test_chat_recovers_preferred_model_after_ttl`). Full
  suite **1655 passed / 18 skipped / 1 pre-existing env failure**;
  `scripts/demo_phase20.py` demos A–G ALL PASS.

### B. Production Reliability (recommendation 3 follow-through)

- **B1.** Billing on the Gemini key or **Vertex AI** (IAM, no training) — infra
  decision, needs user action (recommendation 3).
  **✅ DONE (Session 37) — billing on the existing Gemini key (user decision).**
  `DEVPILOT_GEMINI_TIER` (free|paid, default free) + optional
  `DEVPILOT_GEMINI_PAID_MODELS` (first = default) config knobs; paid tier keeps
  `GEMINI_API_KEY` (same key format, billing attached in AI Studio), disables
  the free-tier daily-quota failover and 24h exhaustion markers, fails fast with
  a clear "check your plan and billing" error on a genuine quota/billing issue,
  and still retries transient per-minute 429s. `GeminiProvider.tier` +
  `model_candidates` introspection; `GET /api/v1/providers/config` exposes
  `data.gemini.{tier,paid_models}`; `POST /api/v1/providers/test` returns
  `gemini_tier`/`gemini_models` for a paid-key self-check. 12 new tests (5
  paid-tier provider + 6 config parsing + 1 config_snapshot); full suite
  **1684 passed / 18 skipped / 1 pre-existing env failure**.
- **B2.** Typed fallback lists per capability
  (`DEVPILOT_LLM_PROVIDER_FALLBACKS`, `MULTI_PROVIDER_ROUTING.md` §2.7).
  **✅ DONE (Session 34):** `Capability` enum (analysis/planning/coding/testing/
  review/reasoning/general), `LLMConfig.capability` field, `LLM_PROVIDER_FALLBACKS`
  config field + validator (parses `cap:prov1,prov2;cap2:prov3` or JSON dict),
  `ProviderRouter._priority_for`/`_ordered_entries(capability)` authoritative
  typed chains (no leakage into the global list; no viable typed provider →
  `ProviderNotAvailableError`), capability-only providers registered for
  health/circuit/observability, all 7 agent stages label their calls, snapshots
  expose `provider_fallbacks` (and real `provider_priority` names). 12 new tests
  (7 router capability + 4 config parsing + 1 planner wiring); full suite
  **1667 passed / 18 skipped / 1 pre-existing env failure**.
- **B3.** Mid-stream token-loss failover (resend prompt with full prefix) for long
  generations. **✅ DONE (Session 35):** `chat_stream` resumes a stream that
  drops AFTER delivering tokens on the next provider in the chain, resending the
  full prompt with the partial output injected as continuation context
  (`_continuation_messages`: `<partial>…</partial>` + do-not-repeat instruction)
  so the response continues instead of restarting — no duplicated tokens, no lost
  generation. Bounded per call by `DEVPILOT_PROVIDER_STREAM_RESUME_MAX` (default
  3, range 0–20; `0` disables recovery and restores surfaces-as-error). Each
  hand-off is observable: `record_resume()` increments a per-provider `resumes`
  counter surfaced in `provider_snapshots()`/`metrics_snapshot().totals`, and the
  failover event carries `reason=mid_stream_token_loss`, `mid_stream=true`.
  Docs: `MULTI_PROVIDER_ROUTING.md` §2.8. 5 new tests (4 resume behaviour + 1
  config parse; `test_provider_router.py` now 59); full suite **1672 passed / 18
  skipped / 1 pre-existing env failure**.

### C. Live E2E Verification (recommendation 4)

- Re-run `scripts/demo_phase17.py --live` and
  `scripts/verify_api_durability.py --live` after a Gemini quota reset; refresh
  `docs/GEMINI_API_KEY_REPORT.md`.

### D. Org-Graph UI Parity (polish)

- Upgrade `/dashboard/organization-graph` from the legacy `ForceDirectedGraph` to
  the React Flow engine + timeline diff + live WS used on
  `/dashboard/engineering-graph` (`InteractiveGraph.tsx`, `useGraphSocket.ts`).
  **✅ DONE (Session 36):** org page migrated onto `InteractiveGraph.tsx` with
  search filter (`applyViewFilters`), neighbor highlight/focus, Timeline Diff
  section (`graphApi.version`/`diff` + `summarizeDiff`), and a live-WS badge +
  auto-refresh on `version_incremented`. Pure mappers extracted to
  `frontend/src/lib/graph/orgGraphModel.ts` (`repoVizId`, `repoNodeId`,
  `reposToVizNodes`, `crossEdgesToVizEdges`, `orgNodesToVizNodes`,
  `orgEdgesToVizEdges`, `clusterVirtualEdges`, `mergeOrgGraph`); optional
  `GraphNode.repository_id` added (backend always emits it). Legacy
  `ForceDirectedGraph.tsx` deleted (sole consumer migrated; duplicated
  registries removed). 10 new vitest tests — frontend **49 passed (7 files)**,
  `next build` EXIT=0 (18 routes).

### E. Test-Framework Parsers (extension)

- Add pytest-style parsers for unittest XML / Vitest JSON / Jest JSON
  (`docs/TESTING_AND_EXECUTION.md:250` notes the current pytest-only parser).
  **✅ DONE (Session 38):** three new dedicated parsers in
  `backend/app/testing/parsers/` mirroring the `TestResultParser` contract —
  `UnittestXMLParser` (`unittest_xml_parser.py`, JUnit-style `testsuites`/
  `testsuite`/`testcase` XML via `xml.etree.ElementTree`; aggregates tests/
  failures/errors/skipped, `failure`/`error`/`skipped` children, message +
  `type` + traceback text, line-number extraction, module/class→path
  heuristic), `VitestJsonParser` (`vitest_json_parser.py`, `testResults` +
  `numTotalTests`/`numPassedTests`/`numFailedTests`/`numPendingTests`, suites
  discriminated from Jest by the ABSENCE of `perfStats`), `JestJsonParser`
  (`jest_json_parser.py`, same shape, discriminated by `perfStats` presence;
  `failureMessages` → message + stack + line). Both JSON parsers locate JSON
  embedded in surrounding runner text and never fabricate counts. Wired into
  `TestingService._parsers` in priority order
  (pytest → unittest → vitest → jest → generic fallback). 12 new tests in
  `tests/test_testing.py` (4 unittest XML + 4 Vitest + 4 Jest incl. service
  chain order); full suite **1696 passed / 18 skipped / 1 pre-existing env
  failure**. Docs: `docs/TESTING_AND_EXECUTION.md` framework table updated
  (unittest/Vitest/Jest rows now dedicated parsers).

---

## 3. Recommended sequencing

| Order | Slice | Why first |
|---|---|---|
| 1 | **A1 + A2** (multi-repo run surface + acquisition) | smallest coherent vertical: create a run over 2 local repos, both acquired + linked — **✅ DONE (Session 28)** |
| 2 | **A3 + A5** (cross-repo context + per-repo ingestion) | planner sees org evidence; evidence lands per-namespace — **A3 ✅ DONE (Session 29)**, **A5 ✅ DONE (Session 32)** |
| 3 | **A4** (per-repo scope enforcement) | safety gate before any patch crosses checkouts — **✅ DONE (Session 31)** |
| 4 | **A6** (API/CLI/frontend surface) | surfaces the vertical for demo + tests — **✅ DONE (Session 33)**: dashboard run form + run-detail multi-repo surface |
| 5 | B/D/E | hardening + polish (**B1, B2, B3 ✅ DONE (Sessions 34/35/37)**; B2/B3 in `test_provider_router.py`, B1 paid tier in `test_llm_providers.py`; **D ✅ DONE (Session 36)**, **E ✅ DONE (Session 38)**) |

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
  **✅ A4 covered (21 tests in `tests/test_phase20_repo_scope.py`);**
  **✅ A5 covered (13 tests in `tests/test_phase20_repo_ingestion.py`).**
- Full deterministic suite stays green (**1696 passed / 18 skipped / 1 pre-existing
  env failure**).
- `scripts/demo_phase20.py` demos A–G ALL PASS (deterministic, no paid LLM).

---

## 6. Next

**Phase 20 COMPLETE**: workstream A (multi-repo runs, Sessions 28–33), workstream B
(B1 paid Gemini tier — Session 37; B2 typed fallbacks — Session 34; B3 mid-stream
token-loss failover — Session 35), workstream D (org-graph UI parity — Session 36)
and workstream E (unittest XML / Vitest JSON / Jest JSON parsers — Session 38) are
all DONE. Remaining: workstream C (live E2E) re-runs after a Gemini quota reset
now that a paid tier is available.
