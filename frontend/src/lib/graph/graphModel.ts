/**
 * Phase 19C — deterministic graph model + transforms.
 *
 * Pure, framework-free helpers shared by the interactive graph view:
 *   - node/relationship registries (colors, labels, categories)
 *   - force-directed layout (d3-force; a layout algorithm, not a graph engine)
 *   - view filtering (node type / relationship / repository / search)
 *   - version-diff summary (timeline)
 *
 * These functions are unit-tested under Node (vitest) — no DOM required.
 */

import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
} from "d3-force";

// ── Shared graph data model ────────────────────────────────────

export interface VizNode {
  id: string;
  label: string;
  nodeType: string;
  repositoryId?: string;
  sublabel?: string;
  data?: { [k: string]: unknown };
}

export interface VizEdge {
  id: string;
  source: string;
  target: string;
  relationship: string;
  weight: number;
  virtual?: boolean;
}

export interface LayoutPoint {
  id: string;
  x: number;
  y: number;
}

// ── Node registry ──────────────────────────────────────────────

/** Phase 19C node categories used for grouping/filtering. */
export const NODE_CATEGORY: Record<string, string> = {
  repository: "structure",
  folder: "structure",
  file: "structure",
  module: "structure",
  package: "structure",
  class: "code",
  interface: "code",
  function: "code",
  method: "code",
  requirement: "requirement",
  acceptance_criterion: "requirement",
  implementation_plan: "plan",
  plan_version: "plan",
  goal: "goal",
  patch: "artifact",
  commit_candidate: "artifact",
  test: "verification",
  test_suite: "verification",
  review_finding: "review",
  quality_gate: "review",
  evidence: "evidence",
  consensus: "reasoning",
  contradiction: "reasoning",
  notebook_entry: "reasoning",
  decision: "reasoning",
  run: "process",
  agent: "process",
  repository_memory: "memory",
};

export const NODE_HEX: Record<string, string> = {
  repository: "#4f46e5",
  folder: "#818cf8",
  file: "#0ea5e9",
  module: "#38bdf8",
  package: "#06b6d4",
  class: "#3b82f6",
  interface: "#8b5cf6",
  function: "#10b981",
  method: "#34d399",
  requirement: "#f59e0b",
  acceptance_criterion: "#fbbf24",
  implementation_plan: "#f97316",
  plan_version: "#fb923c",
  goal: "#d946ef",
  patch: "#f43f5e",
  commit_candidate: "#fb7185",
  test: "#22c55e",
  test_suite: "#4ade80",
  review_finding: "#eab308",
  quality_gate: "#a855f7",
  evidence: "#14b8a6",
  consensus: "#2dd4bf",
  contradiction: "#ef4444",
  notebook_entry: "#0891b2",
  decision: "#ec4899",
  run: "#64748b",
  agent: "#6b7280",
  repository_memory: "#84cc16",
};

export function hexFor(nodeType: string): string {
  return NODE_HEX[nodeType] || "#94a3b8";
}

export function nodeTypeLabel(nodeType: string): string {
  return nodeType.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function nodeCategory(nodeType: string): string {
  return NODE_CATEGORY[nodeType] || "other";
}

export function truncate(s: string, n = 90): string {
  return s.length > n ? `${s.slice(0, n)}…` : s;
}

// ── Relationship registry ──────────────────────────────────────

/** Phase 19C relationship palette — one color per backend EKRelationshipType. */
export const RELATIONSHIP_HEX: Record<string, string> = {
  calls: "#8b5cf6",
  imports: "#0ea5e9",
  contains: "#6366f1",
  depends_on: "#f59e0b",
  implements: "#7c3aed",
  tests: "#16a34a",
  references: "#94a3b8",
  affects: "#f97316",
  modifies: "#f43f5e",
  satisfies: "#10b981",
  created_during: "#64748b",
  produced_by: "#64748b",
  derived_from: "#ec4899",
  supports: "#14b8a6",
  contradicts: "#ef4444",
  supersedes: "#475569",
  uses_memory: "#84cc16",
  validated_by: "#22c55e",
  reviewed_by: "#eab308",
  approved_by: "#a855f7",
  depends_on_repository: "#4f46e5",
  shares_library: "#0ea5e9",
  imports_package: "#38bdf8",
  implements_shared_interface: "#8b5cf6",
  references_shared_component: "#6366f1",
  uses_shared_memory: "#84cc16",
  calls_external_service: "#f43f5e",
};

export function relHex(relationship: string): string {
  return RELATIONSHIP_HEX[relationship] || "#94a3b8";
}

export function relLabel(relationship: string): string {
  return relationship.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

// ── Force-directed layout (d3-force) ───────────────────────────

export interface ForceLayoutOptions {
  width?: number;
  height?: number;
  iterations?: number;
  linkDistance?: number;
  charge?: number;
  radius?: number;
  /**
   * Determinism seed. d3-force uses `Math.random()` internally; we patch it
   * with a seeded PRNG for the duration of the simulation so identical
   * snapshots always produce identical layouts (unit-testable).
   */
  seed?: number;
  /**
   * Seed positions from a previously computed layout (incremental expansion).
   * Nodes missing from this map are placed in a ring around the center.
   */
  initialPositions?: Record<string, { x: number; y: number }>;
}

/** Run `fn` with `Math.random` replaced by a deterministic LCG. */
function withSeededRandom<T>(seed: number, fn: () => T): T {
  const original = Math.random;
  let s = seed >>> 0 || 1;
  Math.random = () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 4294967296;
  };
  try {
    return fn();
  } finally {
    Math.random = original;
  }
}

/**
 * Compute a deterministic force-directed layout for a graph snapshot.
 *
 * Uses d3-force as a layout *algorithm* on top of the production graph
 * engine (@xyflow/react) — this is NOT a custom graph engine.
 */
export function computeForceLayout(
  nodes: VizNode[],
  edges: VizEdge[],
  options: ForceLayoutOptions = {}
): LayoutPoint[] {
  const {
    width = 1400,
    height = 900,
    iterations = 200,
    linkDistance = 140,
    charge = -360,
    radius = 26,
    seed = 42,
    initialPositions,
  } = options;

  if (!nodes.length) return [];

  return withSeededRandom(seed, () => {
    const seeded = initialPositions ?? {};

    const simNodes = nodes.map((n, i) => {
      const seedPoint = seeded[n.id];
      const angle = (i / Math.max(nodes.length, 1)) * Math.PI * 2;
      const ringR = Math.min(width, height) / 3;
      return {
        id: n.id,
        index: i,
        x: seedPoint?.x ?? width / 2 + Math.cos(angle) * ringR * 0.9,
        y: seedPoint?.y ?? height / 2 + Math.sin(angle) * ringR * 0.9,
        vx: 0,
        vy: 0,
      };
    });
    const simEdges = edges
      .filter(
        (e) =>
          nodes.some((n) => n.id === e.source) &&
          nodes.some((n) => n.id === e.target)
      )
      .map((e) => ({ source: e.source, target: e.target }));

    const sim = forceSimulation(simNodes as never)
      .force(
        "link",
        forceLink(simEdges)
          .id((d) => (d as { id: string }).id)
          .distance(linkDistance)
          .strength(0.55)
      )
      .force("charge", forceManyBody().strength(charge))
      .force("center", forceCenter(width / 2, height / 2))
      .force("x", forceX(width / 2).strength(0.06))
      .force("y", forceY(height / 2).strength(0.06))
      .force("collide", forceCollide(radius))
      .stop();

    for (let i = 0; i < iterations; i++) sim.tick();
    sim.stop();

    return simNodes.map((n) => ({
      id: n.id,
      x: Math.round(n.x * 10) / 10,
      y: Math.round(n.y * 10) / 10,
    }));
  });
}

// ── View filtering ─────────────────────────────────────────────

export interface ViewFilters {
  /** Node types to show; null = all. */
  nodeTypes?: Set<string> | null;
  /** Relationships to show; null = all. */
  relationships?: Set<string> | null;
  /** Repository namespaces to show; null = all. */
  repositories?: Set<string> | null;
  /** Case-insensitive substring over name / id / sublabel / source_ref. */
  search?: string;
}

export interface FilteredGraph {
  nodes: VizNode[];
  edges: VizEdge[];
  hiddenNodes: number;
  hiddenEdges: number;
}

export function matchesSearch(n: VizNode, term: string): boolean {
  const t = term.trim().toLowerCase();
  if (!t) return true;
  const haystack = [
    n.label,
    n.id,
    n.sublabel,
    n.repositoryId,
    n.nodeType,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return haystack.includes(t);
}

/**
 * Filter a graph snapshot by node type / relationship / repository / search.
 * An edge survives only when BOTH endpoints survive.
 */
export function applyViewFilters(
  nodes: VizNode[],
  edges: VizEdge[],
  filters: ViewFilters
): FilteredGraph {
  const { nodeTypes, relationships, repositories, search } = filters;

  const keepNode = (n: VizNode): boolean => {
    if (search && !matchesSearch(n, search)) return false;
    if (nodeTypes && nodeTypes.size > 0 && !nodeTypes.has(n.nodeType)) return false;
    if (repositories && repositories.size > 0) {
      const rid = n.repositoryId || "default";
      if (!repositories.has(rid)) return false;
    }
    return true;
  };

  const kept = new Map<string, boolean>();
  let hiddenNodes = 0;
  const filteredNodes: VizNode[] = [];
  for (const n of nodes) {
    const ok = keepNode(n);
    kept.set(n.id, ok);
    if (ok) filteredNodes.push(n);
    else hiddenNodes += 1;
  }

  const filteredEdges: VizEdge[] = [];
  let hiddenEdges = 0;
  for (const e of edges) {
    if (relationships && relationships.size > 0 && !relationships.has(e.relationship)) {
      hiddenEdges += 1;
      continue;
    }
    if (kept.get(e.source) && kept.get(e.target)) {
      filteredEdges.push(e);
    } else {
      hiddenEdges += 1;
    }
  }

  return { nodes: filteredNodes, edges: filteredEdges, hiddenNodes, hiddenEdges };
}

/** Distinct node types / relationships / repositories present in a snapshot. */
export function snapshotFacets(
  nodes: VizNode[],
  edges: VizEdge[]
): {
  nodeTypes: string[];
  relationships: string[];
  repositories: string[];
} {
  const types = new Set<string>();
  for (const n of nodes) types.add(n.nodeType);
  const rels = new Set<string>();
  for (const e of edges) rels.add(e.relationship);
  const repos = new Set<string>();
  for (const n of nodes) repos.add(n.repositoryId || "default");
  return {
    nodeTypes: [...types].sort(),
    relationships: [...rels].sort(),
    repositories: [...repos].sort(),
  };
}

// ── Version diff summary (timeline) ────────────────────────────

export interface DiffInput {
  from_version: number;
  to_version: number;
  added_nodes: { node_id: string; name: string; node_type: string }[];
  removed_nodes: { node_id: string; name: string; node_type: string }[];
  changed_edges: { edge_id: string }[];
  counts: { added: number; removed: number; changed_edges: number };
  per_version: {
    version: number;
    run_id: string;
    summary: string;
    added: number;
    removed: number;
    changed_edges: number;
  }[];
}

export function summarizeDiff(diff: DiffInput): {
  label: string;
  added: number;
  removed: number;
  changedEdges: number;
  versions: number;
} {
  return {
    label: `v${diff.from_version} → v${diff.to_version}`,
    added: diff.counts.added,
    removed: diff.counts.removed,
    changedEdges: diff.counts.changed_edges,
    versions: diff.per_version.length,
  };
}
