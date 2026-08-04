/**
 * DevPilot Organization Knowledge Graph (Phase 19A) API client.
 *
 * Bounded, evidence-only endpoints (§19A):
 *   GET  /org/stats             — organization-wide statistics
 *   GET  /org/repositories      — registered repository namespaces
 *   GET  /org/cross-edges       — explicit cross-repository edges
 *   GET  /org/query             — scope-routed org-wide query
 *   GET  /org/traversal/{id}    — bounded cross-repository traversal
 *   POST /org/repositories      — register a repository namespace
 *   POST /org/link              — create an explicit cross-repository edge
 *
 * The org graph only exposes explicitly linked repositories through the
 * organization scope — repository isolation is enforced structurally.
 */

import { request } from "./client";
import type { GraphNode, GraphEdge } from "./engineeringGraph";

// ── Types ─────────────────────────────────────────────────────

export interface OrgRepository {
  repository_id: string;
  namespace_id: string;
  organization_id: string;
  name: string;
  path: string;
  source_type: string;
  created_at: string;
}

export interface OrgCrossEdge {
  edge_id: string;
  source_repository_id: string;
  target_repository_id: string;
  relationship: string;
  weight: number;
  graph_version: number;
  created_at: string;
}

export interface OrgStats {
  organization_id: string;
  repository_count: number;
  node_count: number;
  edge_count: number;
  cross_edge_count: number;
  cross_relationship_types: Record<string, number>;
  repositories: string[];
  last_updated: string;
}

export interface OrgQueryResult {
  query: string;
  strategy: string;
  scope: string;
  repository_ids: string[];
  repositories: Record<string, number>;
  nodes: GraphNode[];
  edges: GraphEdge[];
  truncated: boolean;
  total_nodes: number;
  version: number;
  plan?: string | null;
}

export interface OrgTraversalResult {
  root: string;
  depth: number;
  nodes: GraphNode[];
  edges: GraphEdge[];
  truncated: boolean;
  total_nodes: number;
  version: number;
}

// ── Org-graph API ─────────────────────────────────────────────

export const orgGraphApi = {
  /** Organization-wide graph statistics. */
  async stats(): Promise<OrgStats> {
    const res = await request<{ success: boolean; data: OrgStats }>(
      `/api/v1/graph/org/stats`
    );
    return res.data;
  },

  /** List registered repository namespaces. */
  async repositories(): Promise<OrgRepository[]> {
    const res = await request<{
      success: boolean;
      data: { repositories: OrgRepository[] };
    }>(`/api/v1/graph/org/repositories`);
    return res.data.repositories;
  },

  /** List explicit cross-repository edges. */
  async crossEdges(): Promise<OrgCrossEdge[]> {
    const res = await request<{
      success: boolean;
      data: { cross_edges: OrgCrossEdge[] };
    }>(`/api/v1/graph/org/cross-edges`);
    return res.data.cross_edges;
  },

  /** Organization-wide query with scope routing (auto | local | organization). */
  async query(
    q: string,
    opts: { scope?: string; repositoryId?: string; limit?: number } = {}
  ): Promise<OrgQueryResult> {
    const params = new URLSearchParams({
      q,
      scope: opts.scope || "auto",
      limit: String(opts.limit || 10),
    });
    if (opts.repositoryId) params.set("repository_id", opts.repositoryId);
    const res = await request<{ success: boolean; data: OrgQueryResult }>(
      `/api/v1/graph/org/query?${params.toString()}`
    );
    return res.data;
  },

  /** Bounded cross-repository traversal from a node. */
  async traversal(
    nodeId: string,
    opts: { depth?: number; maxNodes?: number } = {}
  ): Promise<OrgTraversalResult> {
    const params = new URLSearchParams({
      depth: String(opts.depth || 2),
      max_nodes: String(opts.maxNodes || 50),
    });
    const res = await request<{ success: boolean; data: OrgTraversalResult }>(
      `/api/v1/graph/org/traversal/${encodeURIComponent(nodeId)}?${params.toString()}`
    );
    return res.data;
  },

  /** Register a repository namespace. */
  async registerRepository(payload: {
    repository_id: string;
    name?: string;
    path?: string;
    source_type?: string;
  }): Promise<OrgRepository> {
    const res = await request<{ success: boolean; data: { namespace: OrgRepository } }>(
      `/api/v1/graph/org/repositories`,
      { method: "POST", body: JSON.stringify(payload) }
    );
    return res.data.namespace;
  },

  /** Create an explicit cross-repository edge (deterministic only). */
  async link(payload: {
    source_repository_id: string;
    target_repository_id: string;
    relationship: string;
    weight?: number;
  }): Promise<OrgCrossEdge> {
    const res = await request<{ success: boolean; data: { cross_edge: OrgCrossEdge } }>(
      `/api/v1/graph/org/link`,
      { method: "POST", body: JSON.stringify(payload) }
    );
    return res.data.cross_edge;
  },

  /**
   * Acquire + link multiple repositories into the organization graph
   * (Phase 19C). Takes a flat manifest of repository specs; each spec may
   * declare explicit cross-repository relationships. `source` may be "local"
   * (deterministic path-based ingest) or "github" (requires an injected
   * acquisition service on the server).
   */
  async acquireMulti(payload: {
    repositories: Array<{
      repository_id: string;
      name?: string;
      source?: "local" | "github";
      owner?: string;
      repo?: string;
      path?: string;
      ref?: string;
      depth?: number;
      relationships?: Array<{
        target_repository_id: string;
        relationship: string;
        weight?: number;
      }>;
    }>;
    ingest?: boolean;
  }): Promise<OrgAcquireMultiResult> {
    if (!payload.repositories || payload.repositories.length === 0) {
      throw new Error("acquireMulti requires at least one repository spec");
    }
    const res = await request<{ success: boolean; data: OrgAcquireMultiResult }>(
      `/api/v1/graph/org/acquire-multi`,
      { method: "POST", body: JSON.stringify(payload.repositories) }
    );
    return res.data;
  },
};

export interface OrgAcquireMultiResult {
  organization_id: string;
  repositories_acquired: number;
  namespaces: Array<{
    repository_id: string;
    namespace_id: string;
    organization_id: string;
    name: string;
    path: string;
    source_type: string;
  }>;
  cross_edges: OrgCrossEdge[];
  relationships: number;
  ingested_files: number;
  persisted_records: number;
  scope: string;
}
