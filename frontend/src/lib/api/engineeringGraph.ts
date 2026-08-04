/**
 * DevPilot Engineering Knowledge Graph (Phase 18) API client.
 *
 * Bounded, evidence-only endpoints (§16):
 *   GET /graph/query            — planner-driven graph query
 *   GET /graph/node/{id}        — node info + edges
 *   GET /graph/history/{id}     — temporal history
 *   GET /graph/neighborhood/{id}— bounded traversal
 *   GET /graph/explain/{id}     — provenance + related evidence
 *   GET /graph/version          — current version + stats
 */

import { request } from "./client";

// ── Types ─────────────────────────────────────────────────────

export interface GraphNode {
  node_id: string;
  node_type: string;
  name: string;
  qualified_name: string;
  kind: string;
  source_ref: string;
  source_type: string;
  status: string;
  graph_version: number;
  payload: Record<string, unknown>;
  provenance: Record<string, unknown>;
  created_at: string;
}

export interface GraphEdge {
  edge_id: string;
  source_id: string;
  target_id: string;
  relationship: string;
  weight: number;
  graph_version: number;
  created_at: string;
}

export interface GraphQueryResult {
  query: string;
  strategy: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  truncated: boolean;
  total_nodes: number;
  version: number;
  plan?: string | null;
}

export interface NodeDetail {
  node: GraphNode;
  outgoing_edges: GraphEdge[];
  incoming_edges: GraphEdge[];
}

export interface HistoryEntry {
  node_id: string;
  graph_version: number;
  status: string;
  payload_keys: string[];
  created_at: string;
}

export interface NodeHistory {
  node_id: string;
  current?: GraphNode | null;
  entries: HistoryEntry[];
}

export interface RelatedEvidence {
  edge_id: string;
  relationship: string;
  direction: string;
  node_id: string;
  node_type: string;
  name: string;
  source_ref: string;
  source_type: string;
}

export interface ExplainResult {
  node_id: string;
  found: boolean;
  node?: Record<string, unknown>;
  provenance?: Record<string, unknown>;
  payload?: Record<string, unknown>;
  related?: RelatedEvidence[];
  history_entries?: string[];
}

export interface GraphVersionRecord {
  version: number;
  run_id: string;
  summary: string;
  updated_nodes: number;
  updated_edges: number;
  superseded_nodes: number;
  timestamp: string;
}

export interface GraphStats {
  version: number;
  node_count: number;
  edge_count: number;
  node_types: Record<string, number>;
  relationship_types: Record<string, number>;
  run_count: number;
  repository_count: number;
  last_updated: string;
}

// ── EKG API ───────────────────────────────────────────────────

export const graphApi = {
  /** Query the engineering knowledge graph (planner-driven). */
  async query(q: string, limit = 10): Promise<GraphQueryResult> {
    const params = new URLSearchParams({ q, limit: String(limit) });
    const res = await request<{
      success: boolean;
      data: GraphQueryResult;
    }>(`/api/v1/graph/query?${params.toString()}`);
    return res.data;
  },

  /** Get node information + incident edges. */
  async node(nodeId: string): Promise<NodeDetail> {
    const res = await request<{ success: boolean; data: NodeDetail }>(
      `/api/v1/graph/node/${encodeURIComponent(nodeId)}`
    );
    return res.data;
  },

  /** Get temporal history of a node. */
  async history(nodeId: string): Promise<NodeHistory> {
    const res = await request<{ success: boolean; data: NodeHistory }>(
      `/api/v1/graph/history/${encodeURIComponent(nodeId)}`
    );
    return res.data;
  },

  /** Bounded neighborhood traversal. */
  async neighborhood(
    nodeId: string,
    depth = 2,
    maxNodes = 50
  ): Promise<GraphQueryResult> {
    const params = new URLSearchParams({
      depth: String(depth),
      max_nodes: String(maxNodes),
    });
    const res = await request<{ success: boolean; data: GraphQueryResult }>(
      `/api/v1/graph/neighborhood/${encodeURIComponent(nodeId)}?${params.toString()}`
    );
    return res.data;
  },

  /** Provenance + related evidence. */
  async explain(nodeId: string): Promise<ExplainResult> {
    const res = await request<{ success: boolean; data: ExplainResult }>(
      `/api/v1/graph/explain/${encodeURIComponent(nodeId)}`
    );
    return res.data;
  },

  /** Current graph version + stats + history. */
  async version(): Promise<{
    version: GraphStats;
    history: GraphVersionRecord[];
  }> {
    const res = await request<{
      success: boolean;
      data: { version: GraphStats; history: GraphVersionRecord[] };
    }>(`/api/v1/graph/version`);
    return res.data;
  },
};
