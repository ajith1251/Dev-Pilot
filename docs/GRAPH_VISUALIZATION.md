# Interactive Engineering Knowledge Graph Visualization (Phase 19C)

> **Status**: Complete.
> **Last updated**: August 4, 2026
> **Test baseline**: backend 1580 passed / 18 skipped / 1 failed (the 1 failure is the
> pre-existing `test_wrapper_skips_cleanly_without_provider` env quirk) · frontend 29 vitest
> tests green · `npm run build` clean (EXIT 0) · `scripts/demo_phase19c.py` ALL PASS.

---

## 1. Overview

Phase 19C part 2 turns `/dashboard/engineering-graph` into an **interactive,
live, production-grade graph explorer** on top of the Phase 18 Engineering
Knowledge Graph (EKG) and Phase 19C part 1 organization namespaces.

What the page now does, all from real `/api/v1/graph/*` endpoints (no mock data):

- renders a force-directed graph with a **production engine** (`@xyflow/react`,
  React Flow v12) — pan / zoom / drag / fit-view / minimap / controls /
  fullscreen come from the library, nothing custom
- **incremental expansion** — click a node to inspect, double-click to expand
  its 1–3 hop neighborhood; existing node positions are cached and reseeded
  into the force simulation so the layout evolves smoothly
- **filtering** by node type / relationship / repository plus free-text search
  over name / id / source ref (an edge survives only when both endpoints
  survive)
- **jump-to-node / jump-to-repository** with smooth fit transitions
- **live WebSocket updates** — a `live graph` badge receives `snapshot` on
  connect and `version_incremented` broadcasts, then refreshes stats and the
  visible neighborhood without a page reload
- **graph timeline / version diff** — pick two versions and see added/removed
  nodes and changed edges, with a per-version breakdown
- an **evidence-only provenance panel** (outgoing/incoming relationships,
  related evidence, consensus/notebook, quality findings, runs/agents,
  sanitized provenance, temporal history) — chain-of-thought / hidden prompts /
  secrets are filtered client-side and never surfaced by the API

---

## 2. Engine selection

The view is deliberately built on a production graph engine, not a custom one:

| Concern | Owner |
|---|---|
| Pan / zoom / drag / fit / selection | `@xyflow/react` v12 (React Flow) |
| Minimap / controls / fullscreen | React Flow (`<MiniMap/>`, `<Controls/>`, Fullscreen API) |
| Edge routing, animated/selected edges | React Flow default edges |
| Virtualized rendering on large graphs | React Flow `onlyRenderVisibleElements` |
| **Initial layout positions** | `d3-force@3` — used **only** as a layout algorithm |
| Layout determinism / caching | `computeForceLayout` (seeded PRNG + per-signature cache) |
| WebSocket live feed | native `WebSocket` + `useSyncExternalStore` |

The previous hand-rolled SVG simulation (`frontend/src/components/graph/
ForceDirectedGraph.tsx`) remains for the organization-graph page; the 19C
interactive view uses `InteractiveGraph.tsx`.

---

## 3. Architecture / data flow

```text
GET /api/v1/graph/query?q=...           → GraphQueryResult  ──┐
GET /api/v1/graph/neighborhood/{id}?depth=&max_nodes=          │  mergeGraph (capped)
GET /api/v1/graph/node/{id}                                   │  → graph state
GET /api/v1/graph/explain/{id}                                │
GET /api/v1/graph/version                                     ├── stats + version history
GET /api/v1/graph/diff?from_version=&to_version=              ├── timeline change-set
WS  /api/v1/ws/graph                 (snapshot + live)        ── live refresh
                            │
                            ▼
                 graphModel.ts (pure, unit-tested)
      computeForceLayout · applyViewFilters · snapshotFacets · summarizeDiff
                            │
                            ▼
                 InteractiveGraph.tsx (React Flow engine)
      custom GraphNodeView · cached layout · highlighted dimming
                            │
        page.tsx (engineering-graph) — toolbar, provenance, timeline
```

`page.tsx` keeps `{ nodes, edges }` in a `useState` graph snapshot; every fetch
merges through `mergeGraph` which dedupes by id and caps visible nodes at
`MAX_VIS_NODES = 250`. The pure `graphModel.ts` transforms (layout, filters,
facets, diff summary) are framework-free and unit-tested under Node.

---

## 4. Deterministic layout & caching

`computeForceLayout` in `frontend/src/lib/graph/graphModel.ts`:

- patches `Math.random` with a **seeded LCG** (`seed = 42` by default) via
  `withSeededRandom`, so identical snapshots always produce identical layouts —
  unit-tested by layout-determinism tests
- runs a bounded d3-force simulation (link / charge / center / x / y /
  collide), defaulting to 200 iterations
- accepts `initialPositions` so incremental expansion reseeds existing node
  positions and only places new nodes in a ring around the center — the layout
  evolves, it never re-explodes
- `InteractiveGraph` memoizes the layout by a **graph signature**
  (`nodes.join("|")::edges.join("|")`) and keeps a `Map<id, LayoutPoint>` cache;
  a `resetToken` bump clears the cache (Relayout button / `R` key)

Result: the same query renders identically every time, and expansions are
stable across re-renders and refreshes.

---

## 5. Registries & palette contract

`NODE_CATEGORY`, `NODE_HEX`, and `RELATIONSHIP_HEX` in `graphModel.ts` are the
single source of truth for the view:

- `NODE_HEX` covers all **28** `EKNodeType` values (repository…repository_memory)
- `RELATIONSHIP_HEX` covers all **27** `EKRelationshipType` values
  (calls…calls_external_service)
- `registryContract.test.ts` (frontend vitest) and demo C (backend) both assert
  the frontend palette covers **100%** of the backend enums, keeping the
  registries in lock-step with the authoritative backend vocabulary

`NODE_CATEGORY` groups node types into 10 categories used by the legend /
filtering (structure, code, requirement, plan, goal, artifact, verification,
review, evidence, reasoning, process, memory).

---

## 6. Filtering & search

`applyViewFilters(nodes, edges, filters)`:

- **search** — case-insensitive substring over name / id / sublabel / source_ref
- **node types** — ChipPicker with live per-type counts
- **relationships** — ChipPicker with live per-relationship counts (from the
  current snapshot facets)
- **repositories** — single-select dropdown (values from `snapshotFacets`),
  plus a "repo:" jump button on the selected node

Invariant: **an edge survives only when both endpoints survive** the filter.
The toolbar shows `showing X/Y nodes · A/B edges` and a cap notice when the
view is truncated. Filtering runs purely client-side over the already-bounded
graph snapshot, so it is instant at any depth.

---

## 7. Interactive engine (`InteractiveGraph.tsx`)

- custom node `GraphNodeView` — colored dot, truncated label, type badge,
  optional `· repository_id`, `source_ref` sublabel, target/source Handles;
  `dimmed`/`highlighted`/`root` emphasis states
- click node = inspect (loads provenance), double-click = expand neighbors,
  drag = move, wheel = zoom; pane click deselects
- **highlighting**: selecting a node dims non-neighbors (`highlightedIds`
  derived from incident edges) and animates + labels the selected node's edges
  (`animated`, `label` = relationship name, thicker stroke scaled by weight)
- MiniMap colors nodes by `nodeType`; Controls add a **relayout** button (re-run
  force layout) and a **fullscreen** toggle
- fit-on-mount, fit-on-`fitToken` (`F` key), center-on-`focusId` (cross-repo
  jumps, breadcrumbs) with smooth durations
- **virtualization**: `onlyRenderVisibleElements` kicks in above 200 nodes; a
  `virtualized` badge plus a `nodes · edges` counter appear top-right
- keyboard: `F` fit, `R` relayout, `Esc` deselect

---

## 8. Provenance panel (evidence-only)

The right column is a strict evidence surface:

- breadcrumbs across expansion hops (jump back = center + select)
- node header: name, id, type, status, version, repo-jump button
- outgoing/incoming relationships (click to navigate)
- related evidence grouped as **Evidence**, **Consensus & Notebook**,
  **Quality**, **Runs & Agents**
- sanitized **Provenance** — keys matching
  `chain_?of_?thought|hidden_?prompt|api_?key|secret` are filtered before
  render; the backend also never emits those keys
- temporal history list (version + status + timestamp)

---

## 9. Graph timeline / version diff

Backed by the new `diff_versions(from, to)` (backend) surfaced at
`GET /api/v1/graph/diff?from_version=&to_version=`, the panel returns:

- summary chips: `vX → vY`, `+N nodes`, `−M nodes`, `K edges changed`
- **Added** list (clickable → jumps/selects), **Removed** list
- **Per-version** breakdown (version, run_id, summary, added/removed/changed
  counts)

The backend diff is incremental — it walks `_node_history` / version records,
never rebuilds the graph, and raises `ValueError` for invalid versions (mapped
to HTTP 400 by the endpoint).

---

## 10. Live WebSocket updates

`frontend/src/lib/graph/useGraphSocket.ts`:

- module-level **singleton** `WebSocket` to `WS /api/v1/ws/graph` (the
  `__graph__` broadcast channel), shared across components
- `useSyncExternalStore` exposes `{ status, latestEvent, error }`; consumers
  re-render only when a new event lands (`useLatestGraphEvent` memoizes)
- reconnect with **exponential backoff** (1s → 15s cap), auto-resets on success
- pure `deriveGraphWsUrl(base, protocol, host)` mirrors the HTTP base handling
  (`NEXT_PUBLIC_API_BASE_URL` or same-origin rewrite), unit-tested without a DOM

On `version_incremented` the page shows a live notice (`Graph updated to vN by
<run>`), refreshes stats, and re-fetches the root neighborhood — no reload.

---

## 11. Performance

- page caps visible nodes at `MAX_VIS_NODES = 250` (with a truncation notice)
- `mergeGraph` dedupes incoming nodes/edges against the existing snapshot
- React Flow `onlyRenderVisibleElements` virtualizes rendering above 200 nodes
- layout is cached per graph signature; expansion reseeds, doesn't recompute
  from scratch
- vitest smoke tests: **500-node** layout + **2000-node** filter under Node
- demo F (backend) ingests **3000 synthetic nodes** and asserts query +
  neighborhood latency stay under a 10 s budget with bounded result caps
  (measured 0.22 s ingest, 0.17 s query)

---

## 12. New backend surface (Phase 19C)

| Surface | Location | Behavior |
|---|---|---|
| `diff_versions(from, to)` | `app/services/engineering_graph_service.py` | incremental change-set; `ValueError` on bad versions |
| `GET /api/v1/graph/diff` | `app/api/v1/engineering_graph.py` | HTTP 400 on invalid versions |
| `broadcast_graph_update` | `app/services/ws_manager.py` | `__graph__` channel |
| `WS /api/v1/ws/graph` | `app/api/v1/ws.py` | `snapshot` on connect + live `version_incremented` |
| broadcast hook | `increment_version` | fire-and-forget (needs a running event loop; sync TestClient uses `client.portal.call`) |

Broadcasts are best-effort: `_fire_graph_broadcast` is fire-and-forget and never
blocks or fails the version increment.

---

## 13. File map

Frontend (all Phase 19C):

- `frontend/src/app/dashboard/engineering-graph/page.tsx` — page orchestration,
  toolbar, provenance panel, timeline, live badge
- `frontend/src/components/graph/InteractiveGraph.tsx` — React Flow engine +
  custom node
- `frontend/src/lib/graph/graphModel.ts` — pure model: registries, seeded layout,
  filters, facets, diff summary
- `frontend/src/lib/graph/useGraphSocket.ts` — WebSocket singleton hook
- `frontend/src/lib/api/engineeringGraph.ts` — API client + `GraphDiff`,
  `GraphUpdateEvent`, `diff()` types
- `frontend/src/app/globals.css` — React Flow dark/light theming
- `frontend/src/app/layout.tsx` — imports `@xyflow/react/dist/style.css`
- tests: `graphModel.test.ts`, `registryContract.test.ts`,
  `useGraphSocket.test.ts`, `engineeringGraph.test.ts` (29 tests)

Backend (Phase 19C):

- `app/services/engineering_graph_service.py` — `diff_versions`,
  `_fire_graph_broadcast`, `increment_version` broadcast hook
- `app/services/ws_manager.py` — `broadcast_graph_update`
- `app/api/v1/ws.py` — `/ws/graph` websocket
- `app/api/v1/engineering_graph.py` — `/graph/diff`
- tests: `tests/test_engineering_graph.py` (`TestVersionDiff`, diff endpoints,
  `TestGraphWebSocket`), `tests/test_ws_manager.py` (`TestBroadcastGraphUpdate`)
- `scripts/demo_phase19c.py` — demos A–F

---

## 14. Security

- graph API responses are evidence-only: never chain-of-thought, hidden
  prompts, or provider config
- the provenance panel applies an additional client-side filter for
  chain-of-thought / hidden-prompt / secret keys
- cross-repo isolation is preserved by the org layer — private nodes only reach
  the view via explicit deterministic `link_repositories` bridges

---

## 15. Known limitations / future work

- the timeline diff is node/edge-level (no positional or payload diffs)
- layout caching is per-page-session (browser memory), not persisted server-side
- the page loads from the single-repository EKG namespace by default; org-scope
  queries and multi-repo acquisition wiring into the graph UI remain open
  Phase 19C items
- virtualized rendering trades node decorators for performance above 200 nodes
