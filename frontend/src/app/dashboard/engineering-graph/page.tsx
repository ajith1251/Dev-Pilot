"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { graphApi } from "@/lib/api/engineeringGraph";
import type {
  GraphNode,
  GraphEdge,
  GraphQueryResult,
  NodeDetail,
  NodeHistory,
  ExplainResult,
  GraphStats,
  GraphVersionRecord,
} from "@/lib/api/engineeringGraph";
import { ForceGraph, nodeTypeLabel, truncate } from "@/components/graph/ForceDirectedGraph";
import type { VizEdge, VizNode } from "@/components/graph/ForceDirectedGraph";

// ── Type colors ────────────────────────────────────────────────

const NODE_COLORS: Record<string, string> = {
  repository: "bg-indigo-500",
  folder: "bg-indigo-400",
  file: "bg-sky-500",
  module: "bg-sky-400",
  package: "bg-cyan-500",
  class: "bg-blue-500",
  interface: "bg-violet-500",
  function: "bg-emerald-500",
  method: "bg-emerald-400",
  requirement: "bg-amber-500",
  acceptance_criterion: "bg-amber-400",
  implementation_plan: "bg-orange-500",
  plan_version: "bg-orange-400",
  goal: "bg-fuchsia-500",
  patch: "bg-rose-500",
  commit_candidate: "bg-rose-400",
  test: "bg-green-500",
  test_suite: "bg-green-400",
  review_finding: "bg-yellow-500",
  quality_gate: "bg-purple-500",
  evidence: "bg-teal-500",
  consensus: "bg-teal-400",
  contradiction: "bg-red-500",
  notebook_entry: "bg-cyan-600",
  decision: "bg-pink-500",
  run: "bg-slate-500",
  agent: "bg-gray-500",
  repository_memory: "bg-lime-500",
};

function nodeColor(nodeType: string): string {
  return NODE_COLORS[nodeType] || "bg-slate-400";
}

function fmtTime(ts: string): string {
  try {
    return new Date(ts).toLocaleString();
  } catch {
    return ts || "";
  }
}

// ── Sub-components ─────────────────────────────────────────────

function StatCard({
  label,
  value,
  accent,
}: {
  label: string;
  value: string | number;
  accent: string;
}) {
  return (
    <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-4">
      <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">
        {label}
      </div>
      <div className={`mt-1 text-2xl font-bold ${accent}`}>{value}</div>
    </div>
  );
}

function NodeRow({
  node,
  selected,
  onClick,
}: {
  node: GraphNode;
  selected: boolean;
  onClick: (n: GraphNode) => void;
}) {
  return (
    <button
      onClick={() => onClick(node)}
      className={`w-full text-left flex items-start gap-3 px-3 py-2.5 rounded-lg transition-all ${
        selected
          ? "bg-primary-50 dark:bg-primary-900/30 ring-1 ring-primary-500/40"
          : "hover:bg-slate-50 dark:hover:bg-slate-700/60"
      }`}
    >
      <span
        className={`mt-1 w-2.5 h-2.5 rounded-full shrink-0 ${nodeColor(node.node_type)}`}
      />
      <span className="min-w-0">
        <span className="block text-sm font-medium text-slate-800 dark:text-slate-100 truncate">
          {node.name || node.node_id}
        </span>
        <span className="block text-[11px] text-slate-400 truncate">
          {nodeTypeLabel(node.node_type)} · v{node.graph_version} ·{" "}
          {node.status}
        </span>
        {node.source_ref && (
          <span className="block text-[11px] font-mono text-slate-400 truncate">
            {node.source_ref}
          </span>
        )}
      </span>
    </button>
  );
}

function EdgeList({
  title,
  edges,
  onSelect,
}: {
  title: string;
  edges: GraphEdge[];
  onSelect: (nodeId: string) => void;
}) {
  if (!edges.length) return null;
  return (
    <div>
      <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400 mb-2">
        {title} ({edges.length})
      </div>
      <div className="space-y-1">
        {edges.map((e) => (
          <button
            key={e.edge_id}
            onClick={() => onSelect(e.target_id)}
            className="w-full text-left flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-slate-50 dark:hover:bg-slate-700/60 text-xs"
          >
            <span className="font-mono text-slate-500 truncate">
              {truncate(e.source_id, 22)}
            </span>
            <span className="text-primary-500 font-semibold shrink-0">
              ─[{e.relationship}]→
            </span>
            <span className="font-mono text-slate-600 dark:text-slate-300 truncate">
              {truncate(e.target_id, 22)}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

// ── Main page ──────────────────────────────────────────────────

export default function EngineeringGraphPage() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<GraphQueryResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [queryError, setQueryError] = useState<string | null>(null);
  const [stats, setStats] = useState<GraphStats | null>(null);
  const [versions, setVersions] = useState<GraphVersionRecord[]>([]);
  const [selected, setSelected] = useState<GraphNode | null>(null);
  const [nodeDetail, setNodeDetail] = useState<NodeDetail | null>(null);
  const [history, setHistory] = useState<NodeHistory | null>(null);
  const [explain, setExplain] = useState<ExplainResult | null>(null);
  const [nodeLoading, setNodeLoading] = useState(false);
  const [nodeError, setNodeError] = useState<string | null>(null);
  const detailRef = useRef<HTMLDivElement>(null);

  // ── Force-directed neighborhood ──────────────────────────────
  const [vizNodes, setVizNodes] = useState<VizNode[]>([]);
  const [vizEdges, setVizEdges] = useState<VizEdge[]>([]);
  const [selectedVizId, setSelectedVizId] = useState<string | null>(null);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [neighborhoodDepth, setNeighborhoodDepth] = useState(2);
  const [neighborhoodRoot, setNeighborhoodRoot] = useState<string | null>(null);
  const [vizLoading, setVizLoading] = useState(false);
  const [vizError, setVizError] = useState<string | null>(null);
  const [viewReset, setViewReset] = useState(0);

  const loadNeighborhood = useCallback(
    async (nodeId: string, depth: number = neighborhoodDepth) => {
      setVizLoading(true);
      setVizError(null);
      try {
        const res = await graphApi.neighborhood(nodeId, depth, 60);
        setNeighborhoodRoot(nodeId);
        setSelectedVizId(nodeId);
        setVizNodes(
          res.nodes.map((n) => ({
            id: n.node_id,
            label: n.name || n.node_id,
            nodeType: n.node_type,
            repositoryId: (n as { repository_id?: string }).repository_id,
            sublabel: n.source_ref,
            data: n as unknown as { [k: string]: unknown },
          }))
        );
        setVizEdges(
          res.edges.map((e) => ({
            id: e.edge_id,
            source: e.source_id,
            target: e.target_id,
            relationship: e.relationship,
            weight: e.weight,
          }))
        );
      } catch (e: any) {
        setVizError(e?.message || "Neighborhood traversal failed");
      } finally {
        setVizLoading(false);
      }
    },
    [neighborhoodDepth]
  );

  const loadVersion = useCallback(async () => {
    try {
      const v = await graphApi.version();
      setStats(v.version);
      setVersions(v.history);
    } catch {
      // Non-fatal: version panel stays empty.
    }
  }, []);

  useEffect(() => {
    loadVersion();
  }, [loadVersion]);

  const runQuery = useCallback(
    async (text?: string) => {
      const q = (text ?? query).trim();
      if (!q) return;
      setLoading(true);
      setQueryError(null);
      try {
        const res = await graphApi.query(q);
        setResults(res);
      } catch (e: any) {
        setQueryError(e?.message || "Query failed");
        setResults(null);
      } finally {
        setLoading(false);
      }
    },
    [query]
  );

  const selectNode = useCallback(
    async (node: GraphNode) => {
      setSelected(node);
      setNodeDetail(null);
      setHistory(null);
      setExplain(null);
      setNodeError(null);
      setNodeLoading(true);
      try {
        const [detail, hist, expl] = await Promise.all([
          graphApi.node(node.node_id),
          graphApi.history(node.node_id),
          graphApi.explain(node.node_id),
        ]);
        setNodeDetail(detail);
        setHistory(hist);
        setExplain(expl);
        loadNeighborhood(node.node_id);
      } catch (e: any) {
        setNodeError(e?.message || "Failed to load node");
      } finally {
        setNodeLoading(false);
      }
      detailRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    },
    [loadNeighborhood]
  );

  const jumpToNode = useCallback(
    async (nodeId: string) => {
      try {
        const detail = await graphApi.node(nodeId);
        setNodeDetail(detail);
        setSelected(detail.node);
        setHistory(null);
        setExplain(null);
        setNodeError(null);
        const [hist, expl] = await Promise.all([
          graphApi.history(nodeId),
          graphApi.explain(nodeId),
        ]);
        setHistory(hist);
        setExplain(expl);
        loadNeighborhood(nodeId);
      } catch (e: any) {
        setNodeError(e?.message || "Failed to load node");
      }
      detailRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    },
    [loadNeighborhood]
  );

  const focusVizNode = useCallback(
    (id: string | null) => {
      if (!id) {
        setSelectedVizId(null);
        return;
      }
      setSelectedVizId(id);
      const vn = vizNodes.find((v) => v.id === id);
      if (vn?.data) selectNode(vn.data as unknown as GraphNode);
    },
    [vizNodes, selectNode]
  );

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-900 dark:text-white">
          Engineering Knowledge Graph
        </h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Unified, temporal graph over code, requirements, goals, plans,
          evidence, consensus, notebook, memory and runs — evidence-only.
        </p>
      </div>

      {/* Version stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard
          label="Graph Version"
          value={stats ? `v${stats.version}` : "—"}
          accent="text-primary-600 dark:text-primary-400"
        />
        <StatCard
          label="Nodes"
          value={stats?.node_count ?? "—"}
          accent="text-slate-800 dark:text-slate-100"
        />
        <StatCard
          label="Edges"
          value={stats?.edge_count ?? "—"}
          accent="text-slate-800 dark:text-slate-100"
        />
        <StatCard
          label="Runs / Repos"
          value={
            stats
              ? `${stats.run_count} / ${stats.repository_count}`
              : "—"
          }
          accent="text-slate-800 dark:text-slate-100"
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Query panel */}
        <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5">
          <div className="flex items-center gap-2 mb-4">
            <svg className="w-5 h-5 text-primary-500" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" />
            </svg>
            <h2 className="text-base font-semibold text-slate-900 dark:text-white">
              Query the Graph
            </h2>
          </div>
          <div className="flex gap-2">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && runQuery()}
              placeholder="e.g. why was auth implemented? or affected tests for auth"
              className="flex-1 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-3 py-2 text-sm text-slate-900 dark:text-white placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-primary-500"
            />
            <button
              onClick={() => runQuery()}
              disabled={loading || !query.trim()}
              className="px-4 py-2 rounded-lg bg-primary-600 hover:bg-primary-700 disabled:opacity-50 text-white text-sm font-medium transition-colors"
            >
              {loading ? "…" : "Query"}
            </button>
          </div>

          {queryError && (
            <div className="mt-3 text-sm text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 rounded-lg px-3 py-2">
              {queryError}
            </div>
          )}

          {results && (
            <div className="mt-4">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-semibold text-slate-500">
                  Strategy:{" "}
                  <span className="text-primary-600 dark:text-primary-400">
                    {results.strategy}
                  </span>{" "}
                  · {results.total_nodes} nodes · v{results.version}
                </span>
                {results.truncated && (
                  <span className="text-[11px] text-amber-600 dark:text-amber-400">
                    truncated
                  </span>
                )}
              </div>
              {results.plan && (
                <div className="mb-3 text-[11px] font-mono text-slate-400 bg-slate-50 dark:bg-slate-900 rounded-lg px-2.5 py-1.5">
                  plan: {results.plan}
                </div>
              )}
              <div className="space-y-0.5 max-h-[420px] overflow-y-auto">
                {results.nodes.map((n) => (
                  <NodeRow
                    key={n.node_id}
                    node={n}
                    selected={selected?.node_id === n.node_id}
                    onClick={selectNode}
                  />
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Node detail panel */}
        <div
          ref={detailRef}
          className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5"
        >
          <h2 className="text-base font-semibold text-slate-900 dark:text-white mb-4">
            Node Inspector
          </h2>

          {nodeLoading && (
            <div className="text-sm text-slate-400 animate-pulse">
              Loading node…
            </div>
          )}
          {nodeError && (
            <div className="text-sm text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 rounded-lg px-3 py-2">
              {nodeError}
            </div>
          )}

          {!nodeLoading && !nodeError && selected && nodeDetail && (
            <div className="space-y-4">
              <div className="flex items-start gap-3">
                <span
                  className={`mt-1 w-3 h-3 rounded-full shrink-0 ${nodeColor(selected.node_type)}`}
                />
                <div className="min-w-0">
                  <div className="font-semibold text-slate-900 dark:text-white break-words">
                    {selected.name || selected.node_id}
                  </div>
                  <div className="text-xs text-slate-500 font-mono break-all">
                    {selected.node_id}
                  </div>
                  <div className="mt-1 flex flex-wrap gap-1.5">
                    <span className="text-[10px] font-semibold px-2 py-0.5 rounded-md bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300">
                      {nodeTypeLabel(selected.node_type)}
                    </span>
                    <span className="text-[10px] font-semibold px-2 py-0.5 rounded-md bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300">
                      status: {selected.status}
                    </span>
                    <span className="text-[10px] font-semibold px-2 py-0.5 rounded-md bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300">
                      v{selected.graph_version}
                    </span>
                    {selected.source_type && (
                      <span className="text-[10px] font-semibold px-2 py-0.5 rounded-md bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300">
                        {selected.source_type}
                      </span>
                    )}
                  </div>
                </div>
              </div>

              {/* Edges */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <EdgeList
                  title="Outgoing"
                  edges={nodeDetail.outgoing_edges}
                  onSelect={jumpToNode}
                />
                <EdgeList
                  title="Incoming"
                  edges={nodeDetail.incoming_edges}
                  onSelect={jumpToNode}
                />
              </div>

              {/* Provenance */}
              {explain?.provenance &&
                Object.keys(explain.provenance).length > 0 && (
                  <div>
                    <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400 mb-2">
                      Provenance
                    </div>
                    <div className="space-y-1">
                      {Object.entries(explain.provenance).map(([k, v]) => (
                        <div
                          key={k}
                          className="flex items-start gap-2 text-xs font-mono"
                        >
                          <span className="text-primary-500 font-semibold shrink-0">
                            {k}:
                          </span>
                          <span className="text-slate-600 dark:text-slate-300 break-all">
                            {typeof v === "object"
                              ? JSON.stringify(v)
                              : String(v)}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

              {/* Related evidence */}
              {explain?.related && explain.related.length > 0 && (
                <div>
                  <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400 mb-2">
                    Related Evidence ({explain.related.length})
                  </div>
                  <div className="space-y-1">
                    {explain.related.slice(0, 12).map((r) => (
                      <button
                        key={r.edge_id}
                        onClick={() => jumpToNode(r.node_id)}
                        className="w-full text-left flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-slate-50 dark:hover:bg-slate-700/60 text-xs"
                      >
                        <span
                          className={`w-2 h-2 rounded-full shrink-0 ${nodeColor(r.node_type)}`}
                        />
                        <span className="text-slate-600 dark:text-slate-300 truncate">
                          {r.name || r.node_id}
                        </span>
                        <span className="text-primary-500 font-mono shrink-0 text-[10px]">
                          [{r.relationship}]
                        </span>
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {/* Temporal history */}
              {history && history.entries.length > 0 && (
                <div>
                  <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400 mb-2">
                    Temporal History ({history.entries.length})
                  </div>
                  <div className="space-y-1.5">
                    {history.entries.slice(0, 12).map((h, i) => (
                      <div
                        key={i}
                        className="flex items-center gap-2 text-xs font-mono"
                      >
                        <span className="text-primary-500 font-semibold shrink-0 w-8">
                          v{h.graph_version}
                        </span>
                        <span className="text-slate-500 shrink-0">
                          {h.status}
                        </span>
                        <span className="text-slate-400 truncate">
                          {fmtTime(h.created_at)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Payload */}
              {selected.payload &&
                Object.keys(selected.payload).length > 0 && (
                  <div>
                    <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400 mb-2">
                      Payload
                    </div>
                    <pre className="text-[11px] font-mono bg-slate-50 dark:bg-slate-900 rounded-lg p-3 overflow-x-auto text-slate-600 dark:text-slate-300 max-h-40">
                      {JSON.stringify(selected.payload, null, 2)}
                    </pre>
                  </div>
                )}
            </div>
          )}

          {!nodeLoading && !nodeError && !selected && (
            <div className="text-sm text-slate-400">
              Select a node from the query results to inspect provenance,
              history and relationships.
            </div>
          )}
        </div>
      </div>

      {/* Force-directed neighborhood */}
      <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5">
        <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
          <div>
            <h2 className="text-base font-semibold text-slate-900 dark:text-white">
              Force-Directed Neighborhood
            </h2>
            <p className="text-xs text-slate-400">
              Bounded traversal over the real{" "}
              <code className="font-mono">/graph/neighborhood</code> responses
              — selecting a node re-roots the layout on it.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <label className="text-xs text-slate-500 flex items-center gap-1.5">
              depth
              <select
                value={neighborhoodDepth}
                onChange={(e) => setNeighborhoodDepth(Number(e.target.value))}
                className="rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1.5 text-xs text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-primary-500"
              >
                <option value={1}>1</option>
                <option value={2}>2</option>
                <option value={3}>3</option>
              </select>
            </label>
            <button
              onClick={() => neighborhoodRoot && loadNeighborhood(neighborhoodRoot, neighborhoodDepth)}
              disabled={vizLoading || !neighborhoodRoot}
              className="px-3 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 text-xs font-medium text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors disabled:opacity-50"
            >
              {vizLoading ? "…" : "Reload"}
            </button>
            <button
              onClick={() => setViewReset((v) => v + 1)}
              className="px-3 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 text-xs font-medium text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors"
            >
              Reset view
            </button>
            {selectedVizId && (
              <button
                onClick={() => {
                  setSelectedVizId(null);
                  setNeighborhoodRoot(null);
                  setVizNodes([]);
                  setVizEdges([]);
                }}
                className="px-3 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 text-xs font-medium text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors"
              >
                Clear
              </button>
            )}
          </div>
        </div>

        {vizError && (
          <div className="mb-3 text-sm text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 rounded-lg px-3 py-2">
            {vizError}
          </div>
        )}

        <ForceGraph
          nodes={vizNodes}
          edges={vizEdges}
          selectedId={selectedVizId}
          hoveredId={hoveredId}
          resetToken={viewReset}
          onSelect={focusVizNode}
          onHover={setHoveredId}
        />

        {neighborhoodRoot && vizNodes.length > 0 && (
          <div className="mt-2 text-[11px] font-mono text-slate-400 break-all">
            root: {neighborhoodRoot} · {vizNodes.length} nodes ·{" "}
            {vizEdges.length} edges
          </div>
        )}
        {!neighborhoodRoot && (
          <div className="mt-2 text-[11px] text-slate-400">
            Query the graph and select a node to visualize its neighborhood.
          </div>
        )}
      </div>

      {/* Version history */}
      <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-base font-semibold text-slate-900 dark:text-white">
            Graph Version History
          </h2>
          <button
            onClick={loadVersion}
            className="text-xs text-primary-600 hover:text-primary-700 dark:text-primary-400 font-medium"
          >
            Refresh
          </button>
        </div>
        {versions.length === 0 ? (
          <div className="text-sm text-slate-400">
            No version increments yet — run an orchestration to enrich the
            graph.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="text-[11px] uppercase tracking-wide text-slate-400 border-b border-slate-200 dark:border-slate-700">
                  <th className="py-2 pr-4">Version</th>
                  <th className="py-2 pr-4">Run</th>
                  <th className="py-2 pr-4">Summary</th>
                  <th className="py-2 pr-4">Nodes</th>
                  <th className="py-2 pr-4">Edges</th>
                  <th className="py-2">Timestamp</th>
                </tr>
              </thead>
              <tbody>
                {versions.map((v) => (
                  <tr
                    key={v.version}
                    className="border-b border-slate-100 dark:border-slate-700/50"
                  >
                    <td className="py-2 pr-4 font-mono text-primary-600 dark:text-primary-400 font-semibold">
                      v{v.version}
                    </td>
                    <td className="py-2 pr-4 font-mono text-slate-500">
                      {v.run_id || "—"}
                    </td>
                    <td className="py-2 pr-4 text-slate-700 dark:text-slate-200 max-w-md">
                      {truncate(v.summary, 70)}
                    </td>
                    <td className="py-2 pr-4 text-slate-600 dark:text-slate-300">
                      +{v.updated_nodes}
                    </td>
                    <td className="py-2 pr-4 text-slate-600 dark:text-slate-300">
                      +{v.updated_edges}
                    </td>
                    <td className="py-2 text-slate-500">
                      {fmtTime(v.timestamp)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Node type distribution */}
      {stats && Object.keys(stats.node_types).length > 0 && (
        <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5">
          <h2 className="text-base font-semibold text-slate-900 dark:text-white mb-3">
            Node Distribution
          </h2>
          <div className="flex flex-wrap gap-2">
            {Object.entries(stats.node_types)
              .sort((a, b) => b[1] - a[1])
              .map(([t, c]) => (
                <span
                  key={t}
                  className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300"
                >
                  <span
                    className={`w-2 h-2 rounded-full ${nodeColor(t)}`}
                  />
                  {nodeTypeLabel(t)} · {c}
                </span>
              ))}
          </div>
        </div>
      )}
    </div>
  );
}
