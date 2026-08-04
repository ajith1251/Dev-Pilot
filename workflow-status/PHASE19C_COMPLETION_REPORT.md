# Phase 19C Completion Report — Interactive EKG Visualization, Multi-Repo Acquisition & Org-Scope Queries

> **Status**: COMPLETE ✅
> **Date**: August 4, 2026
> **Scope**: Production graph engine for `/dashboard/engineering-graph`
> (`@xyflow/react` React Flow v12 + d3-force used only as a seeded, deterministic
> layout algorithm), incremental neighborhood expansion with cached positions,
> node/relationship/repository filtering + text search, jump-to-node/repo,
> live `WS /api/v1/ws/graph` updates, graph timeline via `GET /api/v1/graph/diff`
> (added/removed/changed per-version change-sets), evidence-only provenance
> panel, 3000-node performance bounds, 29 frontend vitest tests + backend
> diff/WS suites, demos A–F. **Closed out (Sessions 26–27):** multi-repo remote
> acquisition (`POST /api/v1/graph/org/acquire-multi`, CLI, frontend manifest
> form), org-scope queries across linked repositories (API/CLI/frontend,
> `scope=auto|local|organization`), and the demo-H stale-PG fix
> (`select_tests_for_changes` newest-suite scoping). **Phase 19D is NOT started.**

---

## 1. Status & Test Baseline

| Metric | Before (Phase 19C part 1) | After (Phase 19C COMPLETE) |
|--------|---------------------------|--------------------------|
| Backend deterministic suite (`-m "not live"`) | 1568 passed / 18 skipped / 1 failed | **1602 passed / 18 skipped / 1 failed** (the 1 failure is the pre-existing `test_wrapper_skips_cleanly_without_provider` env quirk) |
| Backend graph + WebSocket suites | green | **83 passed** (incl. `TestVersionDiff`, diff endpoints, `TestGraphWebSocket`, `TestBroadcastGraphUpdate`) |
| Organization-graph + multi-repo acquisition suites | 37 passed | **57 passed** |
| Frontend vitest | — (no runner) | **29 passed** (4 files) |
| Frontend `next build` | ✅ | ✅ EXIT=0 (engineering-graph route 74.7 kB / 162 kB) |
| `scripts/demo_phase19c.py` (demos A–F) | — | **ALL PASS** |
| `scripts/demo_phase18.py` (demos A–I, incl. org-scope query + impact test selection) | — | **ALL PASS** |
| Live LLM calls in tests | 0 | **0** (fully deterministic) |

| Path | Result |
|---|---|
| Backend full suite | **1602 passed · 18 skipped · 1 pre-existing failure** |
| Backend graph + WS targeted | **83 passed · 0 failed** |
| Organization-graph + multi-repo acquisition | **57 passed · 0 failed** |
| Frontend vitest | **29 passed · 0 failed** |
| Frontend `next build` | ✅ EXIT=0 |
| Demo A–F (interactive / cross-repo / highlighting / timeline / WS / perf) | **ALL PASS** |
| Demo A–I (incl. demo G org-scope query, demo H impact test selection, demo I namespaces) | **ALL PASS** |

---

## 2. Summary

Phase 19C part 2 turned the Phase 18 EKG explorer page into an interactive,
live, production-grade graph view. The legacy custom SVG simulation
(`ForceDirectedGraph.tsx`) is retained only for the organization-graph page;
the engineering-graph page now runs on `@xyflow/react` (React Flow v12) which
owns pan/zoom/drag/fit/minimap/controls/fullscreen/virtualization, while
`d3-force@3` is used exclusively as a **layout algorithm** — seeded
(`Math.random` patched with an LCG, default seed 42), deterministic,
unit-tested, and cached per graph signature with `initialPositions` reseeding
for incremental expansion.

Backend additions keep the same invariant as all of Phase 18 — LLMs only
propose, deterministic gates decide — and extend the evidence-only API with a
version-diff change-set endpoint and a live WebSocket broadcast channel.

**Close-out (Sessions 26–27):** multi-repo remote acquisition materializes
several repository namespaces in one deterministic pass (`source=local` offline
ingest or `source=github` clone; declared cross-repo `relationships` registered
via `link_repositories` only — never LLM-inferred); org-scope queries route
retrieval across explicitly linked repositories (`scope=auto|local|organization`)
through `GET /api/v1/graph/org/query` and `org/traversal/{id}`, surfaced in the
CLI (`graph org-query`) and the `/dashboard/organization-graph` scope selector;
and `select_tests_for_changes` was hardened against accumulated-PG drift by
scoping each changed path to its newest `TEST_SUITE` (keyed on `graph_version`).

---

## 3. Files Created

Backend:

- `backend/scripts/demo_phase19c.py` — demos A–F (deterministic, no paid LLM;
  `--json`/`--pg` support). Verified: **ALL PASS**.

Frontend:

- `frontend/src/components/graph/InteractiveGraph.tsx` — React Flow engine:
  custom `GraphNodeView` node (color dot, truncated label, type badge,
  repo id, `source_ref` sublabel, Handles, dim/highlight/root states),
  cached deterministic layout (graph-signature memo + `resetToken` cache
  clears), highlight-and-dim on selection, animated + labeled incident edges,
  MiniMap (color-by-type) + Controls (relayout / fullscreen), virtualization
  above 200 nodes, fit/center effects, `nodes · edges` + `virtualized` badges,
  hint bar.
- `frontend/src/lib/graph/graphModel.ts` — pure model (framework-free,
  Node-testable): `VizNode`/`VizEdge`/`LayoutPoint`, `NODE_CATEGORY`
  (10 categories) + `NODE_HEX` (28 node types) + `RELATIONSHIP_HEX` (27
  relationships, all `EKRelationshipType` values), `computeForceLayout`
  (seeded LCG + `initialPositions`), `applyViewFilters` (edge survives only
  when both endpoints survive), `snapshotFacets`, `summarizeDiff`.
- `frontend/src/lib/graph/useGraphSocket.ts` — module-level singleton
  WebSocket to `WS /api/v1/ws/graph`, `useSyncExternalStore` snapshot
  `{status, latestEvent, error}`, exponential-backoff reconnects (1s→15s
  cap), pure `deriveGraphWsUrl`, `useLatestGraphEvent`.
- `frontend/src/lib/api/engineeringGraph.ts` — `GraphDiff`/`GraphDiffNode`/
  `GraphDiffEdge`/`GraphDiffPerVersion`/`GraphUpdateEvent` types +
  `graphApi.diff()`.
- `frontend/src/lib/api/engineeringGraph.test.ts` — mocked-fetch URL/contract
  tests.
- `frontend/src/lib/graph/graphModel.test.ts` (12 tests), `frontend/src/lib/
  graph/registryContract.test.ts` (4 tests), `frontend/src/lib/graph/
  useGraphSocket.test.ts` (4 tests).
- `frontend/vitest.config.ts` — `@` alias, Node environment.
- `docs/GRAPH_VISUALIZATION.md` — design/architecture doc.
- `workflow-status/PHASE19C_COMPLETION_REPORT.md` — this file.

---

## 4. Files Modified

Backend:

- `backend/app/services/engineering_graph_service.py` — `diff_versions`
  (incremental change-set with per-version breakdown; `ValueError` on invalid
  versions), `_fire_graph_broadcast`/`_run_graph_broadcast` +
  `increment_version` broadcast hook (fire-and-forget).
- `backend/app/services/ws_manager.py` — `broadcast_graph_update` on the
  `__graph__` channel.
- `backend/app/api/v1/ws.py` — `WS /api/v1/ws/graph` (snapshot on connect +
  live `version_incremented`).
- `backend/app/api/v1/engineering_graph.py` — `GET /api/v1/graph/diff`
  (HTTP 400 on invalid versions).
- `backend/tests/test_engineering_graph.py` — `TestVersionDiff`, diff-endpoint
  tests, `TestGraphWebSocket`.
- `backend/tests/test_ws_manager.py` — `TestBroadcastGraphUpdate`.

Frontend:

- `frontend/src/app/dashboard/engineering-graph/page.tsx` — full rework:
  query bar, live WS badge + notice, stats cards, toolbar (search term,
  ChipPicker node-type/relationship filters with counts, repo select, depth
  1–3, Fit/Relayout/Refresh/Collapse/Clear), `MAX_VIS_NODES=250` cap +
  `mergeGraph` dedup, InteractiveGraph + provenance panel (evidence-only,
  prohibited-key filter), relationship legend, timeline + version-diff panel,
  version history, breadcrumbs, keyboard shortcuts (F/R/Esc).
- `frontend/src/app/globals.css` — React Flow light/dark theming.
- `frontend/src/app/layout.tsx` — imports `@xyflow/react/dist/style.css`.
- `frontend/package.json` — `@xyflow/react ^12.11.2`, `d3-force ^3.0.0`,
  `@types/d3-force ^3.0.10`, vitest 4.1.10, `test`/`test:watch` scripts.

Docs/state:

- `README.md`, `docs/ARCHITECTURE.md`, `docs/ENGINEERING_KNOWLEDGE_GRAPH.md`
  (API/WS/frontend/testing/demos + new §22), `workflow-status/PROJECT_STATE.md`
  (Session 26).

### Session 27 close-out (multi-repo acquisition + org-graph UI wiring + demo-H fix)

Backend:

- `app/services/organization_graph_service.py` — `acquire_multi(manifest)`
  (deterministic multi-repo materialization; `source=local|github`; declared
  cross-repo relationships), org-scope query routing (`QueryScope.AUTO/LOCAL/
  ORGANIZATION`).
- `app/services/engineering_graph_service.py` — `select_tests_for_changes`
  scoped to the newest TEST_SUITE per changed path (keyed on `graph_version`;
  raw reverse-index walk to bypass `MAX_EDGES_PER_NODE`).
- `app/api/v1/engineering_graph.py` — `POST /org/acquire-multi` +
  org query/traversal endpoints. Orchestrator + CLI wiring.
- `backend/tests/test_organization_graph.py`,
  `backend/tests/test_multi_repo_acquisition.py` (28 new tests),
  `backend/tests/test_engineering_graph.py` (2 demo-H regression tests).

Frontend:

- `src/lib/api/organizationGraph.ts` — `acquireMulti()`, `query(scope)`,
  `traversal()` contract; `src/lib/api/organizationGraph.test.ts`.
- `src/app/dashboard/organization-graph/page.tsx` — scope selector
  (auto/local/organization), query→graph merge + `in_repository` clustering,
  node Expand → cross-repo traversal, acquire-manifest form.

---

## 5. Visualization Architecture

```text
page.tsx  ── graph snapshot {nodes, edges} (mergeGraph-capped)
   │
   ├─ graphApi.query / neighborhood / node / history / explain / version / diff
   ├─ useGraphSocket  ── WS /api/v1/ws/graph  (snapshot + version_incremented)
   │
   ▼
graphModel.ts (pure, vitest-tested)
   computeForceLayout   seeded LCG (42) · d3-force · initialPositions
   applyViewFilters     node type / relationship / repository / search
   snapshotFacets       distinct types/rels/repos in the current view
   summarizeDiff        timeline change-set summary
   │
   ▼
InteractiveGraph.tsx (React Flow v12)
   custom GraphNodeView · MiniMap · Controls · virtualization · fit/center
```

### Engine split

| Concern | Owner |
|---|---|
| Pan / zoom / drag / fit / selection / minimap / controls / fullscreen / virtualization | `@xyflow/react` v12 |
| Edge routing + animated/selected edge styling | React Flow default edges |
| Initial layout positions | `d3-force@3` (layout algorithm only) |
| Layout determinism + caching | `computeForceLayout` + per-signature cache |
| Live graph feed | native `WebSocket` + `useSyncExternalStore` |

---

## 6. Key invariants

- **Deterministic layout** — `withSeededRandom` patches `Math.random` for the
  simulation duration; identical snapshots → identical layouts; cached per
  graph signature (`id::` joined); `initialPositions` reseeds incremental
  expansions; `resetToken` clears the cache (Relayout / `R`).
- **100% registry contract** — `NODE_HEX`/`NODE_CATEGORY` cover all 28
  `EKNodeType` values, `RELATIONSHIP_HEX` all 27 `EKRelationshipType` values;
  frozen by `registryContract.test.ts` + demo C.
- **Filtering invariant** — an edge survives only when both endpoints survive
  (`applyViewFilters`).
- **Bounded views** — `MAX_VIS_NODES = 250` cap with truncation notice;
  neighborhood fetches bounded (`depth` + `max_nodes=60`).
- **Live but non-blocking** — WS broadcasts are fire-and-forget; version
  increments never fail because a broadcast is pending.
- **Evidence-only** — provenance panel filters
  `chain_?of_?thought|hidden_?prompt|api_?key|secret`; the API never emits
  chain-of-thought / hidden prompts / provider config.

---

## 7. API / WebSocket surface (Phase 19C additions)

| Surface | Behavior |
|---|---|
| `GET /api/v1/graph/diff?from_version=&to_version=` | change-set: `added_nodes`, `removed_nodes`, `changed_edges`, `counts`, `per_version`; 400 on invalid versions |
| `WS /api/v1/ws/graph` | `{"type":"graph_update","event_type":"snapshot",...}` on connect; `version_incremented` on every bump |
| `broadcast_graph_update` (ws_manager) | `__graph__` channel; fire-and-forget |
| `GET /api/v1/graph/org/query?q=&scope=auto\|local\|organization&repository_id=&limit=` | org-scope retrieval across explicitly linked repos; local scope isolated to one namespace |
| `GET /api/v1/graph/org/traversal/{id}?depth=&max_nodes=` | bounded cross-repository traversal |
| `GET /api/v1/graph/org/{stats,repositories,cross-edges}` · `POST /org/repositories` · `/org/link` | org graph administration + evidence |
| `POST /api/v1/graph/org/acquire-multi` | manifest of repo specs → acquire + link + ingest (Phase 19C) |
| CLI `graph org-{stats,repositories,cross-edges,query,traversal,acquire-multi}` | org operations from the terminal with `--json` |

---

## 8. Performance

- page caps visible nodes (250) with dedup merge; React Flow
  `onlyRenderVisibleElements` virtualizes above 200 nodes
- vitest smoke: **500-node** layout + **2000-node** filter under Node
- demo F: 3000 synthetic nodes — ingest 0.22 s, query 0.17 s, neighborhood
  <0.01 s, all under the 10 s budget with bounded result caps

---

## 9. Security review

- Backend diff endpoint and WS payloads carry node/edge evidence only (names,
  types, versions, run/summary), never reasoning traces
- Frontend provenance panel applies an additional key-level filter for
  chain-of-thought / hidden-prompt / secret-shaped keys
- Cross-repo isolation preserved by the org layer: private nodes only appear
  via explicit deterministic `link_repositories` bridges
- No new env vars; no secrets in code; the demo is deterministic (no paid LLM)

---

## 10. Known limitations (carried forward)

- Pre-existing `test_wrapper_skips_cleanly_without_provider` env failure
  (`.env` Gemini key makes the wrapper subprocess run live) — unrelated to 19C
- ~~Demo H stale-PG flake in `demo_phase18.py`~~ — **FIXED**: `select_tests_for_changes`
  now scopes to the newest TEST_SUITE per changed path, keyed on `graph_version`
  (then `created_at`, `node_id`), and reads the raw reverse index to bypass
  `MAX_EDGES_PER_NODE` (2 regression tests in `test_engineering_graph.py`)
- Layout caching is per-page-session (browser memory), not persisted
- The timeline diff is node/edge-level (no positional or payload diffs)
- GitHub-source acquisition in `org/acquire-multi` requires an injected
  acquisition service (deterministic `source=local` works offline); remote
  cloning is exercised via the acquisition service unit tests, not a paid network
- Org-scope queries are exposed via API/CLI/frontend but the org-graph page
  remains a force-directed view (no React Flow timeline/WS surface yet — Phase 19D)

---

## Verdict

- **PHASE 19C COMPLETE**: **YES** ✅
- **FINAL TEST BASELINE**: backend **1602 passed / 18 skipped / 1 failed**
  (pre-existing env quirk only); graph+WS **83 passed**; org-graph + multi-repo
  acquisition **57 passed**; frontend **29 vitest passed**; `next build` EXIT=0;
  demos A–F (`demo_phase19c.py`) and demos A–I (`demo_phase18.py`) **ALL PASS**
- **Per-capability**:
  - Interactive exploration — **PASS** (demo A)
  - Cross-repo navigation — **PASS** (demo B, org backend + demo I namespaces)
  - Relationship highlighting / palette contract — **PASS** (demo C +
    registryContract tests)
  - Graph timeline / version diff — **PASS** (demo D + `TestVersionDiff`)
  - Live WebSocket updates — **PASS** (demo E + `TestGraphWebSocket`)
  - Search & filter performance — **PASS** (demo F + perf smoke tests)
  - Multi-repo remote acquisition — **PASS** (demo G + `test_multi_repo_acquisition.py`)
  - Org-scope queries / cross-repo namespaces — **PASS** (API/CLI/frontend +
    `test_organization_graph.py`)
  - EKG-driven test selection vs accumulated PG — **PASS** (demo H + 2 regression tests)
  - Frontend build + tests — **PASS**
- **PHASE 19D READY**: **YES** (Phase 19D not started; scope guarded)
