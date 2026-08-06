# Phase 20A6 Completion Report — Multi-Repository Dashboard & Autonomous Run Experience

> **Status**: ✅ COMPLETE (Session 40, August 5, 2026)
> **Basis**: Phase 20A1–A5 DONE (Sessions 28–32) — repository specification, auxiliary
> materialization, org-scope planning context, per-repo scope enforcement, per-repo EKG
> ingestion. This slice completes the **user experience** on top of the existing
> orchestration, autonomy, Engineering Knowledge Graph, and Organization Graph — **no
> backend redesign**.
> **Commit**: working tree (see `git log` for the A6 commit hash)

---

## 1. Objective

Turn the single-repository dashboard into an **organization → repository selection →
cross-repository run → execution timeline → repository status → engineering summary**
flow. Every piece reuses existing orchestrator/autonomy/EKG/org-graph machinery; nothing
is re-implemented.

## 2. Test Baseline

- **Backend**: `1681 passed / 17 skipped / 1 failed / 54 deselected` on the
  deterministic suite (`-m "not live and not integration"`). The 1 failure is the
  **pre-existing** `test_wrapper_skips_cleanly_without_provider` env quirk (the `.env`
  Gemini key makes the wrapper subprocess run live) — not a regression.
- **Frontend**: vitest **63 passed (8 files)**; `next build` **EXIT=0** (18 routes).
- **Demo**: `python scripts/demo_phase20.py` demos **A–M ALL PASS** (deterministic,
  PostgreSQL persistence verified).

## 3. Files Created

| File | Purpose |
|---|---|
| `backend/app/services/run_dashboard.py` | Repository-aware view builder: `build_repository_view` (per-repo status cards + six-stage timeline) + `build_organization_summary` (org-level execution summary). Duck-typed for `DevPilotRun` and `DevPilotRunResult`. |
| `backend/tests/test_phase20a6_dashboard.py` | 25 deterministic tests: view builder, org summary (incl. repair-attribution), API surface, org repositories search/filter/pagination, per-repo stats, WS broadcast payload, CLI `--json`, PostgresRunStore A6 round-trip. |
| `frontend/src/lib/graph/repositoryStatusModel.ts` | Pure, testable mappers: `statusColor`/`statusLabel`/`stageProgress`/`repositoryTimeline`/`organizeSummary`/`counts`. |
| `frontend/src/lib/graph/repositoryStatusModel.test.ts` | 11 vitest tests for the mappers. |
| `frontend/src/components/runs/RepositorySelector.tsx` | Search/filter, lazy-load pagination, multi-select repository picker with dependency + status badges. |
| `frontend/src/components/runs/RepositoryStatusCards.tsx` | Live per-repository status cards (stage, progress, validation, EKG status) with EKG navigation links. |
| `frontend/src/components/runs/RepositoryTimeline.tsx` | Per-repository six-stage execution timeline. |
| `frontend/src/components/runs/OrganizationSummary.tsx` | Organization-level completion summary (repositories, duration, successful/failed/repaired, decisions, consensus, quality). |
| `frontend/src/components/runs/RunHistoryPanel.tsx` | Context & run history: recent runs, repository relationships, prior executions, engineering decisions. |
| `workflow-status/PHASE20A6_COMPLETION_REPORT.md` | This report. |

## 4. Files Modified

| File | Change |
|---|---|
| `backend/app/models/orchestration.py` | `RunSource.acceptance_criteria: List[str]` + `RunSource.execution_budget: Dict[str, Any]` (advisory run-creation metadata). |
| `backend/app/services/orchestration_service.py` | `_broadcast_update` payload gains `repositories` + `organization_summary` (built via `run_dashboard`); public `get_organization_graph()` accessor. |
| `backend/app/workflows/orchestration.py` | `run_user_task`/`run_github_issue` accept + forward `acceptance_criteria`/`execution_budget` into `RunSource`. |
| `backend/app/api/v1/orchestration.py` | `_sanitize_run`/`_sanitize_result` include `repositories` + `organization_summary`; source sanitization exposes `acceptance_criteria` + `execution_budget`; run list exposes `repository_count`; `POST /api/v1/runs` validates + forwards the new fields (400 on malformed). |
| `backend/app/api/v1/engineering_graph.py` | Org repositories endpoint gains `q`/`organization`/`limit`/`offset` search+filter+pagination; new `GET /org/repositories/{id}` per-repo stats endpoint (404 for unknown). |
| `backend/app/services/postgres_run_store.py` | `context_json` round-trips `repository_path`, `auxiliary_repositories`, `repo_patches` (list-of-model + plain-value serialization) → **restart recovery preserves dashboard state**. |
| `backend/app/cli.py` | `run` prints Participating Repositories + Organization Summary; `--json` is now pure JSON with `repositories` + `organization_summary` merged. |
| `backend/app/cli_engineering_graph.py` | `graph org-repositories` supports `--q/--organization/--limit/--offset`. |
| `frontend/src/lib/api/client.ts` | `AuxiliaryRepositorySpec` ordering/relationships, `RepositoryPatchValidation`, `OrgRepositorySummary`, `OrganizationSummary` types; `orgApi.repositories`/`repositoryStats`; `runsApi.create` acceptance criteria + budget. |
| `frontend/src/app/dashboard/runs/page.tsx` | `CreateRunModal`: org repository selector, acceptance criteria + execution budget fields, aux-repo ordering/relationships editors; run cards show multi-repo badges. |
| `frontend/src/app/dashboard/runs/[id]/page.tsx` | Run detail renders `RepositoryStatusCards`, `RepositoryTimeline`, `OrganizationSummary`, `RunHistoryPanel`; WS normalization handles repository-aware payloads. |
| `frontend/src/lib/hooks/useRunWebSocket.ts` | Types carry `repositories` + `organization_summary` in run-update messages. |
| `scripts/demo_phase20.py` | A6 demos H–M (create, track, live WS, org summary, EKG nav, restart recovery). |
| `workflow-status/PHASE20_ROADMAP.md`, `docs/ARCHITECTURE.md`, `workflow-status/PROJECT_STATE.md` | Documentation. |

## 5. Dashboard Architecture

```
Organization ──► Repository Selection ──► Cross-Repository Run ──► Execution Timeline
      ▲                                                                     │
      │                                                          Repository Status
      │                                                                     │
      └────────────────── Organization Summary ◄────── Engineering Summary ──┘
```

- **Single source of truth**: `run_dashboard.build_repository_view` / `build_organization_summary`
  derive everything from the EXISTING run object + org graph (`repository_stats`, `stats`).
  The API sanitizers, WebSocket broadcast, CLI, and frontend all consume the same builders —
  no duplicated logic, no divergence between live and final views.
- **Duck-typed shapes**: `DevPilotRun` (live run → `GET /runs/{id}`, WS) and
  `DevPilotRunResult` (final → `POST /runs`) both work through the same code path.
- **UI components** are pure-presentation: `RepositorySelector`, `RepositoryStatusCards`,
  `RepositoryTimeline`, `OrganizationSummary`, `RunHistoryPanel` render the builders' output;
  pure mappers in `repositoryStatusModel.ts` keep color/status/progress logic unit-testable.

## 6. Repository Selection

- `RepositorySelector` (CreateRunModal): search box, organization/source-type filter chips,
  lazy-loaded pages (`limit`/`offset` against `orgApi.repositories`), multi-select with
  dependency + status badges. Selected repos feed the aux-repository list with explicit
  **ordering** (up/down) and **relationships** editors (target repo + relationship kind) —
  matching the A2 `MultiRepoAcquisitionSpec` contract.

## 7. Execution Timeline

- `RepositoryTimeline` renders the six per-repository stages:
  **Planning → Coding → Testing → Repair → Review → Quality Gate**.
- `progress` per repo is derived deterministically: coding uses that repo's own
  patch validation/application outcome; the other stages map from the global stage
  results (repair `skipped` is preserved distinctly). Running/pending/succeeded/failed
  states color the timeline live via WebSocket.

## 8. Organization Summary

- Shown at run completion (and live on the run-detail page):
  participating repositories, execution duration, successful / failed / repaired
  repositories, engineering decisions (`decision_recorded` events), consensus summary
  (`consensus_built` / `conflict_detected` events), quality status + quality-gate detail,
  org-graph stats (repositories/nodes/edges/cross-edges/version).

## 9. Engineering Knowledge Graph Integration

- Each `RepositoryStatusCard`'s **graph block** is `org_service.repository_stats(repo_id)`
  (node/edge/run counts, outgoing/incoming links) — a navigable link target for the
  org-graph / EKG routes (`/dashboard/organization-graph`, engineering-graph neighborhood).
- Primary checkout (unregistered namespace) degrades gracefully to empty stats.

## 10. API

Reused + extended existing endpoints — **no duplicate APIs**:
- `GET /api/v1/runs/{id}` — `repositories` + `organization_summary` (+ existing
  `auxiliary_repositories`, `repo_validation`).
- `GET /api/v1/runs` — `repository_count` for multi-repo badges.
- `POST /api/v1/runs` — optional `acceptance_criteria` (list) + `execution_budget` (dict);
  400 on malformed types.
- `GET /api/v1/graph/org/repositories?q=&organization=&limit=&offset=` — search/filter/pagination.
- `GET /api/v1/graph/org/repositories/{id}` — per-repository EKG stats; 404 for unknown.

## 11. CLI

- `python -m app.cli run REPO --task T [--aux-repo ID=PATH ...]` prints **Participating
  Repositories** (per-repo timeline icon row, validation/apply/changed-files) and an
  **Organization Summary** block.
- `--json` output is now **pure JSON** (header/run-id prints suppressed) and merges
  `repositories` + `organization_summary`.
- `python -m app.cli graph org-repositories --q api --limit 10 --offset 0 [--json]`.

## 12. Frontend

- TypeScript types + `orgApi` in `client.ts`; `runsApi.create` forwards criteria/budget.
- `CreateRunModal`: org repository selector, acceptance criteria, execution budget,
  aux-repo ordering + relationships.
- Run detail: status cards (live WS), timeline, org summary, run history, EKG links.
- Run list: multi-repo badges.
- Vitest **63 passed** (8 files); `next build` EXIT=0.

## 13. Demonstrations (scripts/demo_phase20.py)

| Demo | Verifies |
|---|---|
| A–G | Phase 20A1–A5 (unchanged, all pass). |
| **H** Cross-repo run creation | Dashboard view lists primary + aux in order with stable ordering. |
| **I** Execution tracking | Mid-run view carries per-repo progress across the six stages. |
| **J** Live WS payload | Broadcast payload shape carries `repositories` + `organization_summary`. |
| **K** Organization summary | Completed run → repositories/duration/success/failed/decisions/consensus/quality. |
| **L** EKG navigation | Repository cards resolve to their OWN namespace stats; repo-b shows ingested run evidence. |
| **M** Restart recovery | Run persisted to PostgreSQL, reloaded via a FRESH store, view rebuilt identically. |

## 14. Security Review

- **Repository isolation**: the view only surfaces namespaces actually materialized for the
  run (primary checkout + `run.auxiliary_repositories`) — never the whole org.
- **Evidence-only**: no hidden reasoning, no chain-of-thought, no secrets, no provider
  credentials. Messages are truncated (200 chars) and sanitized.
- **Per-repo scope enforcement** (A4) is untouched: patches still validate/apply against
  their own checkout only; the dashboard merely *reports* those deterministic outcomes.
- **Graceful degradation**: no org service / unknown namespace → empty graph block, never fatal.

## 15. Known Limitations

- **Live run-creation demo** (full LLM pipeline) not exercised in the deterministic demo —
  the A6 demos build on the same mocked-stage machinery as A–G.
- Repository selector pagination is client-driven against the org repositories endpoint;
  very large orgs (>200 namespaces) may need server-side cursor pagination later.
- `PostgresRunStore` A6 round-trip covers `repository_path`/`auxiliary_repositories`/
  `repo_patches`; `RunSource.repositories` (the declared aux specs) is not separately
  persisted — the materialized namespaces are the durable record.

## 16. Phase 20B Contract

- **Do NOT begin Phase 20B** — this slice explicitly stops at the user experience.
- Phase 20B (provider routing, typed fallbacks, stream resilience, paid Gemini tier) and
  workstreams D/E were completed in earlier sessions and remain unaffected.
- Next candidate work: workstream C (live E2E) after a Gemini quota reset, then the
  enterprise roadmap (E1–E7).

---

```
PHASE 20A6 COMPLETE: YES

FINAL TEST BASELINE:
  1681 passed / 17 skipped / 1 failed (pre-existing env quirk) / 54 deselected (integration)
  Frontend vitest 63 passed (8 files); next build EXIT=0

MULTI-REPOSITORY DASHBOARD:  PASS
EXECUTION TIMELINE:          PASS
ORGANIZATION SUMMARY:        PASS
WEBSOCKET UPDATES:           PASS
FRONTEND:                    PASS

PHASE 20B READY: YES
```
