"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { orgGraphApi } from "@/lib/api/organizationGraph";
import type {
  OrgCrossEdge,
  OrgQueryResult,
  OrgRepository,
  OrgStats,
} from "@/lib/api/organizationGraph";
import { ForceGraph, hexFor, nodeTypeLabel } from "@/components/graph/ForceDirectedGraph";
import type { VizEdge, VizNode } from "@/components/graph/ForceDirectedGraph";

const CROSS_RELATIONSHIPS = [
  "depends_on_repository",
  "shares_library",
  "imports_package",
  "implements_shared_interface",
  "references_shared_component",
  "uses_shared_memory",
  "calls_external_service",
];

function fmtTime(ts: string): string {
  try {
    return new Date(ts).toLocaleString();
  } catch {
    return ts || "";
  }
}

const REPO_PREFIX = "repo:";

function repoVizId(repositoryId: string): string {
  return `${REPO_PREFIX}${repositoryId}`;
}

function repoNodeId(repositoryId: string): string {
  return `REPO::${repositoryId}`.slice(0, 40);
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

const inputCls =
  "w-full rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-3 py-2 text-sm text-slate-900 dark:text-white placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-primary-500";
const labelCls = "block text-[11px] font-semibold uppercase tracking-wide text-slate-400 mb-1";
const btnPrimary =
  "px-4 py-2 rounded-lg bg-primary-600 hover:bg-primary-700 disabled:opacity-50 text-white text-sm font-medium transition-colors";
const btnGhost =
  "px-3 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 text-xs font-medium text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors";

// ── Main page ──────────────────────────────────────────────────

export default function OrganizationGraphPage() {
  const [stats, setStats] = useState<OrgStats | null>(null);
  const [repos, setRepos] = useState<OrgRepository[]>([]);
  const [crossEdges, setCrossEdges] = useState<OrgCrossEdge[]>([]);
  const [vizNodes, setVizNodes] = useState<VizNode[]>([]);
  const [vizEdges, setVizEdges] = useState<VizEdge[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [query, setQuery] = useState("");
  const [scope, setScope] = useState<"auto" | "local" | "organization">("auto");
  const [localRepoId, setLocalRepoId] = useState("");
  const [queryResult, setQueryResult] = useState<OrgQueryResult | null>(null);
  const [queryError, setQueryError] = useState<string | null>(null);
  const [viewReset, setViewReset] = useState(0);

  const [repoForm, setRepoForm] = useState({
    repository_id: "",
    name: "",
    path: "",
    source_type: "local",
  });
  const [linkForm, setLinkForm] = useState({
    source_repository_id: "",
    target_repository_id: "",
    relationship: "shares_library",
    weight: 0.8,
  });

  const loadOrg = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [s, r, ce] = await Promise.all([
        orgGraphApi.stats(),
        orgGraphApi.repositories(),
        orgGraphApi.crossEdges(),
      ]);
      setStats(s);
      setRepos(r);
      setCrossEdges(ce);
      setVizNodes(
        r.map((repo) => ({
          id: repoVizId(repo.repository_id),
          label: repo.name || repo.repository_id,
          nodeType: "repository",
          repositoryId: repo.repository_id,
          sublabel: repo.source_type,
          data: repo as unknown as { [k: string]: unknown },
        }))
      );
      setVizEdges(
        ce.map((e) => ({
          id: e.edge_id,
          source: repoVizId(e.source_repository_id),
          target: repoVizId(e.target_repository_id),
          relationship: e.relationship,
          weight: e.weight,
        }))
      );
    } catch (e: any) {
      setError(e?.message || "Failed to load organization graph");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadOrg();
  }, [loadOrg]);

  const mergeResult = useCallback(
    (res: OrgQueryResult, expandSource?: string) => {
      setVizNodes((prev) => {
        const m = new Map(prev.map((n) => [n.id, n]));
        for (const n of res.nodes) {
          if (!m.has(n.node_id)) {
            m.set(n.node_id, {
              id: n.node_id,
              label: n.name || n.node_id,
              nodeType: n.node_type,
              repositoryId: (n as { repository_id?: string }).repository_id,
              sublabel: n.source_ref,
              data: n as unknown as { [k: string]: unknown },
            });
          }
        }
        return [...m.values()];
      });
      setVizEdges((prev) => {
        const m = new Map(prev.map((e) => [e.id, e]));
        for (const e of res.edges) {
          const id = e.edge_id || `${e.source_id}->${e.target_id}`;
          if (!m.has(id)) {
            m.set(id, {
              id,
              source: e.source_id,
              target: e.target_id,
              relationship: e.relationship,
              weight: e.weight,
            });
          }
        }
        // Cluster every result node under its owning repository.
        const cluster = new Map(m);
        for (const n of res.nodes) {
          const rid = (n as { repository_id?: string }).repository_id;
          if (rid && rid !== "default") {
            cluster.set(`virt:${rid}:${n.node_id}`, {
              id: `virt:${rid}:${n.node_id}`,
              source: repoVizId(rid),
              target: n.node_id,
              relationship: "in_repository",
              weight: 0.3,
              virtual: true,
            });
          }
        }
        return [...cluster.values()];
      });
      setSelectedId(expandSource || null);
    },
    []
  );

  const runQuery = useCallback(
    async (text?: string) => {
      const q = (text ?? query).trim();
      if (!q) return;
      setLoading(true);
      setQueryError(null);
      try {
        const res = await orgGraphApi.query(q, {
          scope,
          repositoryId: scope === "local" ? localRepoId : undefined,
          limit: 30,
        });
        setQueryResult(res);
        mergeResult(res);
      } catch (e: any) {
        setQueryError(e?.message || "Query failed");
      } finally {
        setLoading(false);
      }
    },
    [query, scope, localRepoId, mergeResult]
  );

  const expandNode = useCallback(
    async (id: string) => {
      setLoading(true);
      setError(null);
      try {
        // Repo nodes in the org graph are addressed as REPO::<id>.
        const root = id.startsWith(REPO_PREFIX)
          ? repoNodeId(id.slice(REPO_PREFIX.length))
          : id;
        const res = await orgGraphApi.traversal(root, { depth: 2, maxNodes: 60 });
        const payload: OrgQueryResult = {
          query: `expand:${id}`,
          strategy: "cross_repository",
          scope: "organization",
          repository_ids: [],
          repositories: {},
          nodes: res.nodes,
          edges: res.edges,
          truncated: res.truncated,
          total_nodes: res.total_nodes,
          version: res.version,
        };
        mergeResult(payload, id);
      } catch (e: any) {
        setError(e?.message || "Expansion failed");
      } finally {
        setLoading(false);
      }
    },
    [mergeResult]
  );

  const registerRepo = useCallback(
    async (ev: React.FormEvent) => {
      ev.preventDefault();
      if (!repoForm.repository_id.trim()) return;
      setError(null);
      try {
        await orgGraphApi.registerRepository(repoForm);
        setRepoForm({ repository_id: "", name: "", path: "", source_type: "local" });
        await loadOrg();
      } catch (e: any) {
        setError(e?.message || "Registration failed");
      }
    },
    [repoForm, loadOrg]
  );

  const linkRepos = useCallback(
    async (ev: React.FormEvent) => {
      ev.preventDefault();
      if (!linkForm.source_repository_id || !linkForm.target_repository_id) return;
      setError(null);
      try {
        await orgGraphApi.link(linkForm);
        await loadOrg();
      } catch (e: any) {
        setError(e?.message || "Linking failed");
      }
    },
    [linkForm, loadOrg]
  );

  // ── Inspector data ───────────────────────────────────────────

  const selectedNode = useMemo(
    () => vizNodes.find((n) => n.id === selectedId) || null,
    [vizNodes, selectedId]
  );
  const selectedRepo =
    selectedNode && selectedNode.nodeType === "repository"
      ? repos.find((r) => r.repository_id === selectedNode.repositoryId)
      : null;
  const selectedEdges = useMemo(() => {
    if (!selectedId) return [];
    return crossEdges.filter(
      (e) =>
        repoVizId(e.source_repository_id) === selectedId ||
        repoVizId(e.target_repository_id) === selectedId
    );
  }, [selectedId, crossEdges]);

  const showNodeLabel = (node: VizNode) =>
    node.nodeType === "repository" ||
    selectedId === node.id ||
    hoveredId === node.id;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-900 dark:text-white">
          Organization Knowledge Graph
        </h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Force-directed view of repository namespaces and their explicit
          cross-repository links. Repositories stay isolated — only
          deterministic links bridge the boundary.
        </p>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard
          label="Repositories"
          value={stats?.repository_count ?? "—"}
          accent="text-indigo-600 dark:text-indigo-400"
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
          label="Cross-repo Edges"
          value={stats?.cross_edge_count ?? "—"}
          accent="text-rose-600 dark:text-rose-400"
        />
      </div>

      {error && (
        <div className="text-sm text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 rounded-lg px-3 py-2">
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left column: query + management */}
        <div className="space-y-6">
          <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5">
            <h2 className="text-base font-semibold text-slate-900 dark:text-white mb-4">
              Explore
            </h2>
            <div className="space-y-3">
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && runQuery()}
                placeholder="e.g. shared across repositories"
                className={inputCls}
              />
              <div className="flex gap-2">
                <select
                  value={scope}
                  onChange={(e) =>
                    setScope(e.target.value as "auto" | "local" | "organization")
                  }
                  className={inputCls}
                >
                  <option value="auto">auto</option>
                  <option value="local">local (isolated)</option>
                  <option value="organization">organization-wide</option>
                </select>
                <button
                  onClick={() => runQuery()}
                  disabled={loading || !query.trim()}
                  className={`${btnPrimary} shrink-0`}
                >
                  {loading ? "…" : "Query"}
                </button>
              </div>
              {scope === "local" && (
                <select
                  value={localRepoId}
                  onChange={(e) => setLocalRepoId(e.target.value)}
                  className={inputCls}
                >
                  <option value="">— repository (isolated) —</option>
                  {repos.map((r) => (
                    <option key={r.repository_id} value={r.repository_id}>
                      {r.name || r.repository_id}
                    </option>
                  ))}
                </select>
              )}
              <button
                onClick={() => {
                  setVizNodes(
                    repos.map((r) => ({
                      id: repoVizId(r.repository_id),
                      label: r.name || r.repository_id,
                      nodeType: "repository",
                      repositoryId: r.repository_id,
                      sublabel: r.source_type,
                      data: r as unknown as { [k: string]: unknown },
                    }))
                  );
                  setVizEdges(
                    crossEdges.map((e) => ({
                      id: e.edge_id,
                      source: repoVizId(e.source_repository_id),
                      target: repoVizId(e.target_repository_id),
                      relationship: e.relationship,
                      weight: e.weight,
                    }))
                  );
                  setSelectedId(null);
                }}
                className={btnGhost}
              >
                Show organization view
              </button>
            </div>

            {queryError && (
              <div className="mt-3 text-sm text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 rounded-lg px-3 py-2">
                {queryError}
              </div>
            )}
            {queryResult && (
              <div className="mt-4">
                <div className="text-xs font-semibold text-slate-500 mb-2">
                  {queryResult.scope} · {queryResult.strategy} ·{" "}
                  {queryResult.total_nodes} nodes
                  {queryResult.truncated && " · truncated"}
                </div>
                <div className="space-y-1 max-h-[180px] overflow-y-auto">
                  {queryResult.nodes.slice(0, 20).map((n) => (
                    <button
                      key={n.node_id}
                      onClick={() => setSelectedId(n.node_id)}
                      className={`w-full text-left flex items-start gap-2 px-2 py-1.5 rounded-md text-xs ${
                        selectedId === n.node_id
                          ? "bg-indigo-50 dark:bg-indigo-900/30 ring-1 ring-indigo-500/40"
                          : "hover:bg-slate-50 dark:hover:bg-slate-700/60"
                      }`}
                    >
                      <span
                        className="mt-1 w-2 h-2 rounded-full shrink-0"
                        style={{ background: hexFor(n.node_type) }}
                      />
                      <span className="min-w-0">
                        <span className="block text-slate-700 dark:text-slate-200 truncate">
                          {n.name || n.node_id}
                        </span>
                        <span className="block text-[10px] font-mono text-slate-400 truncate">
                          {nodeTypeLabel(n.node_type)} · {(n as { repository_id?: string }).repository_id}
                        </span>
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>

          <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5">
            <h2 className="text-base font-semibold text-slate-900 dark:text-white mb-3">
              Register Repository
            </h2>
            <form onSubmit={registerRepo} className="space-y-3">
              <div>
                <label className={labelCls}>repository_id *</label>
                <input
                  value={repoForm.repository_id}
                  onChange={(e) =>
                    setRepoForm({ ...repoForm, repository_id: e.target.value })
                  }
                  placeholder="repo-acme-api"
                  className={inputCls}
                />
              </div>
              <div>
                <label className={labelCls}>name</label>
                <input
                  value={repoForm.name}
                  onChange={(e) => setRepoForm({ ...repoForm, name: e.target.value })}
                  placeholder="Acme API"
                  className={inputCls}
                />
              </div>
              <div>
                <label className={labelCls}>path</label>
                <input
                  value={repoForm.path}
                  onChange={(e) => setRepoForm({ ...repoForm, path: e.target.value })}
                  placeholder="/org/acme-api"
                  className={inputCls}
                />
              </div>
              <div>
                <label className={labelCls}>source_type</label>
                <select
                  value={repoForm.source_type}
                  onChange={(e) =>
                    setRepoForm({ ...repoForm, source_type: e.target.value })
                  }
                  className={inputCls}
                >
                  <option value="local">local</option>
                  <option value="github">github</option>
                  <option value="shared">shared</option>
                </select>
              </div>
              <button type="submit" disabled={!repoForm.repository_id.trim()} className={`${btnPrimary} w-full`}>
                Register
              </button>
            </form>
          </div>

          <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5">
            <h2 className="text-base font-semibold text-slate-900 dark:text-white mb-3">
              Link Repositories
            </h2>
            <form onSubmit={linkRepos} className="space-y-3">
              <div>
                <label className={labelCls}>source</label>
                <select
                  value={linkForm.source_repository_id}
                  onChange={(e) =>
                    setLinkForm({ ...linkForm, source_repository_id: e.target.value })
                  }
                  className={inputCls}
                >
                  <option value="">— select —</option>
                  {repos.map((r) => (
                    <option key={r.repository_id} value={r.repository_id}>
                      {r.name || r.repository_id}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className={labelCls}>target</label>
                <select
                  value={linkForm.target_repository_id}
                  onChange={(e) =>
                    setLinkForm({ ...linkForm, target_repository_id: e.target.value })
                  }
                  className={inputCls}
                >
                  <option value="">— select —</option>
                  {repos.map((r) => (
                    <option key={r.repository_id} value={r.repository_id}>
                      {r.name || r.repository_id}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className={labelCls}>relationship</label>
                <select
                  value={linkForm.relationship}
                  onChange={(e) =>
                    setLinkForm({ ...linkForm, relationship: e.target.value })
                  }
                  className={inputCls}
                >
                  {CROSS_RELATIONSHIPS.map((r) => (
                    <option key={r} value={r}>
                      {r.replace(/_/g, " ")}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className={labelCls}>weight</label>
                <input
                  type="number"
                  min={0}
                  max={1}
                  step={0.1}
                  value={linkForm.weight}
                  onChange={(e) =>
                    setLinkForm({ ...linkForm, weight: Number(e.target.value) })
                  }
                  className={inputCls}
                />
              </div>
              <button
                type="submit"
                disabled={!linkForm.source_repository_id || !linkForm.target_repository_id}
                className={`${btnPrimary} w-full`}
              >
                Link
              </button>
            </form>
          </div>
        </div>

        {/* Right: canvas + inspector */}
        <div className="lg:col-span-2 space-y-6">
          <div>
            <div className="flex items-center justify-between mb-2">
              <h2 className="text-base font-semibold text-slate-900 dark:text-white">
                Force-Directed Graph
              </h2>
              <div className="flex gap-2">
                <button
                  onClick={() => setViewReset((v) => v + 1)}
                  className={btnGhost}
                >
                  Reset view
                </button>
                <button
                  onClick={loadOrg}
                  disabled={loading}
                  className={btnGhost}
                >
                  {loading ? "…" : "Refresh"}
                </button>
              </div>
            </div>
            <ForceGraph
              nodes={vizNodes}
              edges={vizEdges}
              selectedId={selectedId}
              hoveredId={hoveredId}
              resetToken={viewReset}
              onSelect={setSelectedId}
              onHover={setHoveredId}
            />
            {selectedId && selectedNode && (
              <div className="mt-2 text-[11px] text-slate-400">
                {showNodeLabel(selectedNode)
                  ? `${nodeTypeLabel(selectedNode.nodeType)} selected`
                  : "Node selected"}
              </div>
            )}
          </div>

          {/* Inspector */}
          {selectedNode && (
            <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5">
              <div className="flex items-start justify-between gap-4 mb-4">
                <div className="flex items-start gap-3">
                  <span
                    className="mt-1 w-3 h-3 rounded-full shrink-0"
                    style={{ background: hexFor(selectedNode.nodeType) }}
                  />
                  <div className="min-w-0">
                    <div className="font-semibold text-slate-900 dark:text-white break-words">
                      {selectedNode.label}
                    </div>
                    <div className="text-xs text-slate-500 font-mono break-all">
                      {selectedNode.id}
                    </div>
                    <div className="mt-1 flex flex-wrap gap-1.5">
                      <span className="text-[10px] font-semibold px-2 py-0.5 rounded-md bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300">
                        {nodeTypeLabel(selectedNode.nodeType)}
                      </span>
                      {selectedNode.repositoryId && (
                        <span className="text-[10px] font-semibold px-2 py-0.5 rounded-md bg-indigo-100 dark:bg-indigo-900/40 text-indigo-700 dark:text-indigo-300">
                          {selectedNode.repositoryId}
                        </span>
                      )}
                    </div>
                  </div>
                </div>
                <div className="flex gap-2 shrink-0">
                  <button onClick={() => expandNode(selectedNode.id)} className={btnGhost}>
                    Expand
                  </button>
                  <button onClick={() => setSelectedId(null)} className={btnGhost}>
                    Close
                  </button>
                </div>
              </div>

              {selectedNode.nodeType === "repository" && selectedRepo && (
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs">
                  <div>
                    <div className={labelCls}>Path</div>
                    <div className="font-mono text-slate-600 dark:text-slate-300 break-all">
                      {selectedRepo.path || "—"}
                    </div>
                  </div>
                  <div>
                    <div className={labelCls}>Source</div>
                    <div className="text-slate-600 dark:text-slate-300">
                      {selectedRepo.source_type}
                    </div>
                  </div>
                  <div>
                    <div className={labelCls}>Registered</div>
                    <div className="text-slate-600 dark:text-slate-300">
                      {fmtTime(selectedRepo.created_at)}
                    </div>
                  </div>
                </div>
              )}

              {selectedEdges.length > 0 && (
                <div className="mt-4">
                  <div className={labelCls}>
                    Cross-repository links ({selectedEdges.length})
                  </div>
                  <div className="space-y-1">
                    {selectedEdges.map((e) => (
                      <div
                        key={e.edge_id}
                        className="flex items-center gap-2 text-xs font-mono"
                      >
                        <span className="text-slate-500 truncate">
                          {e.source_repository_id}
                        </span>
                        <span className="text-rose-500 font-semibold shrink-0">
                          ─[{e.relationship}]→
                        </span>
                        <span className="text-slate-600 dark:text-slate-300 truncate">
                          {e.target_repository_id}
                        </span>
                        <span className="text-slate-400 shrink-0">w{e.weight}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {selectedNode.data && selectedNode.nodeType !== "repository" && (
                <div className="mt-4 grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
                  <div>
                    <div className={labelCls}>Source ref</div>
                    <div className="font-mono text-slate-600 dark:text-slate-300 break-all">
                      {(selectedNode.data as { source_ref?: string }).source_ref || "—"}
                    </div>
                  </div>
                  <div>
                    <div className={labelCls}>Status</div>
                    <div className="text-slate-600 dark:text-slate-300">
                      {(selectedNode.data as { status?: string }).status || "—"}
                    </div>
                  </div>
                </div>
              )}

              {selectedNode.sublabel && (
                <div className="mt-3 text-[11px] font-mono text-slate-400 break-all">
                  {selectedNode.sublabel}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
