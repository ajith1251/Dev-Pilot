/**
 * Phase 20 — Workstream D: organization-graph → React Flow view model.
 *
 * Pure mappers that turn org-graph API payloads (registered repositories,
 * explicit cross-edges, org query/traversal results) into the shared
 * `VizNode`/`VizEdge` model consumed by `InteractiveGraph`. Framework-free
 * and unit-tested under Node (vitest) — no DOM required.
 */

import type { GraphEdge, GraphNode } from "@/lib/api/engineeringGraph";
import type {
  OrgCrossEdge,
  OrgRepository,
} from "@/lib/api/organizationGraph";
import type { VizEdge, VizNode } from "./graphModel";

export const REPO_PREFIX = "repo:";
export const VIRTUAL_EDGE_PREFIX = "virt:";
export const IN_REPOSITORY_REL = "in_repository";
export const MAX_REPO_NODE_ID_LEN = 40;

/** Stable viz id for a repository-namespace node. */
export function repoVizId(repositoryId: string): string {
  return `${REPO_PREFIX}${repositoryId}`;
}

/** Backend node id for a repository namespace (`REPO::<id>`, length-capped). */
export function repoNodeId(repositoryId: string): string {
  return `REPO::${repositoryId}`.slice(0, MAX_REPO_NODE_ID_LEN);
}

/** Map registered namespaces to repository `VizNode`s. */
export function reposToVizNodes(repos: OrgRepository[]): VizNode[] {
  return repos.map((r) => ({
    id: repoVizId(r.repository_id),
    label: r.name || r.repository_id,
    nodeType: "repository",
    repositoryId: r.repository_id,
    sublabel: r.source_type,
    data: r as unknown as Record<string, unknown>,
  }));
}

/** Map explicit cross-repository edges to `VizEdge`s (id-less ones get a stable key). */
export function crossEdgesToVizEdges(edges: OrgCrossEdge[]): VizEdge[] {
  return edges.map((e) => ({
    id: e.edge_id,
    source: repoVizId(e.source_repository_id),
    target: repoVizId(e.target_repository_id),
    relationship: e.relationship,
    weight: e.weight,
  }));
}

/** Map org query/traversal result nodes to `VizNode`s. */
export function orgNodesToVizNodes(nodes: GraphNode[]): VizNode[] {
  return nodes.map((n) => ({
    id: n.node_id,
    label: n.name || n.node_id,
    nodeType: n.node_type,
    repositoryId: n.repository_id,
    sublabel: n.source_ref,
    data: n as unknown as Record<string, unknown>,
  }));
}

/** Map org query/traversal result edges to `VizEdge`s. */
export function orgEdgesToVizEdges(edges: GraphEdge[]): VizEdge[] {
  return edges.map((e) => {
    const id = e.edge_id || `${e.source_id}->${e.target_id}`;
    return {
      id,
      source: e.source_id,
      target: e.target_id,
      relationship: e.relationship,
      weight: e.weight,
    };
  });
}

/**
 * Add virtual `in_repository` cluster edges so result nodes visually attach to
 * their owning repository node in the org view.
 */
export function clusterVirtualEdges(
  nodes: GraphNode[],
  edges: VizEdge[]
): VizEdge[] {
  const out = [...edges];
  for (const n of nodes) {
    const rid = n.repository_id;
    if (rid && rid !== "default") {
      out.push({
        id: `${VIRTUAL_EDGE_PREFIX}${rid}:${n.node_id}`,
        source: repoVizId(rid),
        target: n.node_id,
        relationship: IN_REPOSITORY_REL,
        weight: 0.3,
        virtual: true,
      });
    }
  }
  return out;
}

/**
 * Dedup-merge incoming viz nodes/edges into an existing snapshot (add-only —
 * the org view grows incrementally as queries/traversals land).
 */
export function mergeOrgGraph(
  base: { nodes: VizNode[]; edges: VizEdge[] },
  incoming: { nodes: VizNode[]; edges: VizEdge[] }
): { nodes: VizNode[]; edges: VizEdge[] } {
  const nodeMap = new Map(base.nodes.map((n) => [n.id, n]));
  for (const n of incoming.nodes) {
    if (!nodeMap.has(n.id)) nodeMap.set(n.id, n);
  }
  const edgeMap = new Map(base.edges.map((e) => [e.id, e]));
  for (const e of incoming.edges) {
    if (!edgeMap.has(e.id)) edgeMap.set(e.id, e);
  }
  return { nodes: [...nodeMap.values()], edges: [...edgeMap.values()] };
}
