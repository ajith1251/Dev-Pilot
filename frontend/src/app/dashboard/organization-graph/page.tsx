"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { orgGraphApi } from "@/lib/api/organizationGraph";
import type {
  OrgCrossEdge,
  OrgQueryResult,
  OrgRepository,
  OrgStats,
} from "@/lib/api/organizationGraph";
import { graphApi } from "@/lib/api/engineeringGraph";
import type { GraphDiff } from "@/lib/api/engineeringGraph";
import { InteractiveGraph } from "@/components/graph/InteractiveGraph";
import {
  applyViewFilters,
  hexFor,
  nodeTypeLabel,
  relHex,
  relLabel,
  summarizeDiff,
  truncate,
  type VizEdge,
  type VizNode,
} from "@/lib/graph/graphModel";
import {
  REPO_PREFIX,
  clusterVirtualEdges,
  crossEdgesToVizEdges,
  mergeOrgGraph,
  orgEdgesToVizEdges,
  orgNodesToVizNodes,
  repoNodeId,
  repoVizId,
  reposToVizNodes,
} from "@/lib/graph/orgGraphModel";
import { useGraphSocket, useLatestGraphEvent } from "@/lib/graph/useGraphSocket";

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

function SectionCard({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
        <div>
          <h2 className="text-base font-semibold text-slate-900 dark:text-white">
            {title}
          </h2>
          {subtitle && <p className="text-xs text-slate-400 mt-0.5">{subtitle}</p>}
        </div>
      </div>
      {children}
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
  const [graph, setGraph] = useState<{ nodes: VizNode[]; edges: VizEdge[] }>({
    nodes: [],
    edges: [],
  });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [rootId, setRootId] = useState<string | null>(null);
  const [focusId, setFocusId] = useState<string | null>(null);
  const [resetToken, setResetToken] = useState(0);
  const [fitToken, setFitToken] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [searchTerm, setSearchTerm] = useState("");
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState<"auto" | "local" | "organization">("auto");
  const [localRepoId, setLocalRepoId] = useState("");
  const [queryResult, setQueryResult] = useState<OrgQueryResult | null>(null);
  const [queryError, setQueryError] = useState<string | null>(null);

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

  // Phase 19C — multi-repo acquisition manifest.
  const [manifestText, setManifestText] = useState(
    JSON.stringify(
      [
        {
          repository_id: "repo-a",
          name: "repo-a",
          source: "local",
          path: "/path/to/repo-a",
        },
      ],
      null,
      2
    )
  );
  const [acquireBusy, setAcquireBusy] = useState(false);
  const [acquireNote, setAcquireNote] = useState<string | null>(null);

  // Phase 20D — live graph updates + timeline diff.
  const ws = useGraphSocket();
  const liveEvent = useLatestGraphEvent();
  const [liveNotice, setLiveNotice] = useState<string | null>(null);
  const [versions, setVersions] = useState<
    { version: number; run_id: string; summary: string; timestamp: string }[]
  >([]);
  const [fromVersion, setFromVersion] = useState<number | null>(null);
  const [toVersion, setToVersion] = useState<number | null>(null);
  const [diff, setDiff] = useState<GraphDiff | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [diffError, setDiffError] = useState<string | null>(null);

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
      setGraph({
        nodes: reposToVizNodes(r),
        edges: crossEdgesToVizEdges(ce),
      });
    } catch (e: any) {
      setError(e?.message || "Failed to load organization graph");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadVersions = useCallback(async () => {
    try {
      const v = await graphApi.version();
      setVersions([...v.history].sort((a, b) => a.version - b.version));
    } catch {
      // Non-fatal.
    }
  }, []);

  useEffect(() => {
    loadOrg();
    loadVersions();
  }, [loadOrg, loadVersions]);

  // ── Live updates (Phase 20D): refresh org data on version increments ──
  useEffect(() => {
    if (!liveEvent) return;
    if (liveEvent.event_type === "version_incremented") {
      setLiveNotice(
        `Graph updated to v${liveEvent.data.version}${
          liveEvent.data.run_id ? ` by ${truncate(liveEvent.data.run_id, 40)}` : ""
        }`
      );
      void loadOrg();
      void loadVersions();
    }
  }, [liveEvent, loadOrg, loadVersions]);

  const mergeResult = useCallback(
    (res: OrgQueryResult, expandSource?: string) => {
      setGraph((prev) =>
        mergeOrgGraph(prev, {
          nodes: orgNodesToVizNodes(res.nodes),
          edges: clusterVirtualEdges(res.nodes, orgEdgesToVizEdges(res.edges)),
        })
      );
      setSelectedId(expandSource || null);
      if (expandSource) setFocusId(expandSource);
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
        setRootId((prev) => prev ?? id);
        mergeResult(payload, id);
      } catch (e: any) {
        setError(e?.message || "Expansion failed");
      } finally {
        setLoading(false);
      }
    },
    [mergeResult]
  );

  const selectNode = useCallback((id: string | null) => {
    setSelectedId(id);
    if (id) setFocusId(id);
  }, []);

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

  const acquireRepos = useCallback(
    async (ev: React.FormEvent) => {
      ev.preventDefault();
      let repos;
      try {
        repos = JSON.parse(manifestText);
      } catch {
        setError("Acquisition manifest is not valid JSON");
        return;
      }
      if (!Array.isArray(repos) || repos.length === 0) {
        setError("Manifest must be a non-empty JSON array of repository specs");
        return;
      }
      setError(null);
      setAcquireNote(null);
      setAcquireBusy(true);
      try {
        const res = await orgGraphApi.acquireMulti({ repositories: repos });
        setAcquireNote(
          `Acquired ${res.repositories_acquired} repo(s), ${res.relationships} ` +
            `cross-edge(s), ${res.ingested_files} evidence file(s)`
        );
        await loadOrg();
      } catch (e: any) {
        setError(e?.message || "Acquisition failed");
      } finally {
        setAcquireBusy(false);
      }
    },
    [manifestText, loadOrg]
  );

  // ── View filtering + selection highlighting ──────────────────

  const visible = useMemo(
    () => applyViewFilters(graph.nodes, graph.edges, { search: searchTerm }),
    [graph.nodes, graph.edges, searchTerm]
  );

  const highlightedIds = useMemo(() => {
    if (!selectedId) return null;
    const set = new Set<string>([selectedId]);
    for (const e of graph.edges) {
      if (e.source === selectedId) set.add(e.target);
      if (e.target === selectedId) set.add(e.source);
    }
    return set;
  }, [selectedId, graph.edges]);

  // ── Timeline diff (Phase 20D) ────────────────────────────────

  useEffect(() => {
    if (versions.length && fromVersion == null && toVersion == null) {
      setFromVersion(versions[0].version);
      setToVersion(versions[versions.length - 1].version);
    }
  }, [versions, fromVersion, toVersion]);

  const runDiff = useCallback(async () => {
    if (fromVersion == null || toVersion == null) return;
    setDiffLoading(true);
    setDiffError(null);
    try {
      setDiff(await graphApi.diff(fromVersion, toVersion));
    } catch (e: any) {
      setDiffError(e?.message || "Diff failed");
      setDiff(null);
    } finally {
      setDiffLoading(false);
    }
  }, [fromVersion, toVersion]);

  // ── Inspector data ───────────────────────────────────────────

  const selectedNode = useMemo(
    () => visible.nodes.find((n) => n.id === selectedId) || null,
    [visible.nodes, selectedId]
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

  const diffSummary = diff ? summarizeDiff(diff) : null;

  return (
    <div className="space-y-6">
      {/* Header + live badge */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white">
            Organization Knowledge Graph
          </h1>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
            Interactive view of repository namespaces and their explicit
            cross-repository links, on the React Flow engine. Repositories stay
            isolated — only deterministic links bridge the boundary.
          </p>
        </div>
        <div className="flex flex-col items-end gap-1.5">
          <span
            className={`inline-flex items-center gap-1.5 text-[11px] font-semibold px-2.5 py-1 rounded-full ${
              ws.status === "open"
                ? "bg-green-100 dark:bg-green-900/40 text-green-700 dark:text-green-300"
                : ws.status === "reconnecting" || ws.status === "connecting"
                  ? "bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300"
                  : "bg-slate-100 dark:bg-slate-700 text-slate-500 dark:text-slate-300"
            }`}
          >
            <span
              className={`w-2 h-2 rounded-full ${
                ws.status === "open"
                  ? "bg-green-500"
                  : ws.status === "reconnecting" || ws.status === "connecting"
                    ? "bg-amber-400 animate-pulse"
                    : "bg-slate-400"
              }`}
            />
            live graph · {ws.status}
          </span>
          {liveNotice && (
            <span className="text-[11px] font-mono text-primary-600 dark:text-primary-400 bg-primary-50 dark:bg-primary-900/20 rounded px-2 py-1">
              {liveNotice}
            </span>
          )}
        </div>
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
                  setGraph({
                    nodes: reposToVizNodes(repos),
                    edges: crossEdgesToVizEdges(crossEdges),
                  });
                  setSelectedId(null);
                  setRootId(null);
                  setFocusId(null);
                  setSearchTerm("");
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
                      onClick={() => selectNode(n.node_id)}
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
                          {nodeTypeLabel(n.node_type)} · {n.repository_id}
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

          <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5">
            <h2 className="text-base font-semibold text-slate-900 dark:text-white mb-3">
              Acquire + Link (Phase 19C)
            </h2>
            <p className="text-xs text-slate-500 dark:text-slate-400 mb-3">
              Materialize several repositories and wire their cross-repository
              edges in one deterministic, evidence-only pass. Specs are a JSON
              array of{" "}
              <code className="font-mono">{"{repository_id, source, path}"}</code>{" "}
              objects; use{" "}
              <code className="font-mono">source: "local"</code> for existing
              checkouts (offline) or{" "}
              <code className="font-mono">source: "github"</code> with{" "}
              <code className="font-mono">owner</code>/<code className="font-mono">repo</code>.
            </p>
            <form onSubmit={acquireRepos} className="space-y-3">
              <div>
                <label className={labelCls}>manifest (JSON)</label>
                <textarea
                  value={manifestText}
                  onChange={(e) => setManifestText(e.target.value)}
                  spellCheck={false}
                  rows={7}
                  className={`${inputCls} font-mono text-xs`}
                />
              </div>
              {acquireNote && (
                <div className="text-xs text-emerald-600 dark:text-emerald-400">
                  {acquireNote}
                </div>
              )}
              <button
                type="submit"
                disabled={acquireBusy || !manifestText.trim()}
                className={`${btnPrimary} w-full`}
              >
                {acquireBusy ? "Acquiring…" : "Acquire"}
              </button>
            </form>
          </div>
        </div>

        {/* Right: canvas + inspector */}
        <div className="lg:col-span-2 space-y-6">
          <SectionCard
            title="Interactive Graph"
            subtitle="React Flow engine · d3-force layout · click to inspect, double-click to expand neighbors"
          >
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <input
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                placeholder="Filter current graph: name, symbol, ref…"
                className={inputCls}
              />
              <button
                onClick={() => setFitToken((t) => t + 1)}
                title="Fit graph to view"
                className={btnGhost}
              >
                Fit
              </button>
              <button
                onClick={() => setResetToken((t) => t + 1)}
                title="Re-run force layout"
                className={btnGhost}
              >
                Relayout
              </button>
              <button
                onClick={loadOrg}
                disabled={loading}
                className={btnGhost}
              >
                {loading ? "…" : "Refresh"}
              </button>
            </div>
            <InteractiveGraph
              nodes={visible.nodes}
              edges={visible.edges}
              selectedId={selectedId}
              highlightedIds={highlightedIds}
              rootId={rootId}
              focusId={focusId}
              resetToken={resetToken}
              fitToken={fitToken}
              heightClass="h-[560px]"
              onSelectNode={selectNode}
              onExpandNode={(id) => void expandNode(id)}
              onRelayout={() => setResetToken((t) => t + 1)}
            />
            <div className="mt-2 flex flex-wrap items-center gap-3 text-[11px] text-slate-400">
              <span>keys: F fit · R relayout · Esc deselect</span>
              <span className="font-mono">
                showing {visible.nodes.length}/{graph.nodes.length} nodes ·{" "}
                {visible.edges.length}/{graph.edges.length} edges
              </span>
            </div>
          </SectionCard>

          {/* Timeline diff (Phase 20D) */}
          <SectionCard
            title="Timeline Diff"
            subtitle="Compare any two graph versions (runs, cross-links, evidence)"
          >
            <div className="flex flex-wrap items-end gap-3">
              <div>
                <label className={labelCls}>from</label>
                <select
                  value={fromVersion ?? ""}
                  onChange={(e) => setFromVersion(Number(e.target.value))}
                  disabled={versions.length === 0}
                  className={inputCls}
                >
                  <option value="" disabled>
                    —
                  </option>
                  {versions.map((v) => (
                    <option key={v.version} value={v.version}>
                      v{v.version}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className={labelCls}>to</label>
                <select
                  value={toVersion ?? ""}
                  onChange={(e) => setToVersion(Number(e.target.value))}
                  disabled={versions.length === 0}
                  className={inputCls}
                >
                  <option value="" disabled>
                    —
                  </option>
                  {versions.map((v) => (
                    <option key={v.version} value={v.version}>
                      v{v.version}
                    </option>
                  ))}
                </select>
              </div>
              <button
                onClick={() => void runDiff()}
                disabled={diffLoading || fromVersion == null || toVersion == null}
                className={btnPrimary}
              >
                {diffLoading ? "…" : "Diff"}
              </button>
            </div>

            {diffError && (
              <div className="mt-3 text-sm text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 rounded-lg px-3 py-2">
                {diffError}
              </div>
            )}

            {diffSummary && diff && (
              <div className="mt-4 space-y-3">
                <div className="flex flex-wrap gap-3 text-xs">
                  <span className="font-semibold text-slate-700 dark:text-slate-200">
                    {diffSummary.label}
                  </span>
                  <span className="text-emerald-600 dark:text-emerald-400">
                    +{diffSummary.added} added
                  </span>
                  <span className="text-rose-600 dark:text-rose-400">
                    −{diffSummary.removed} removed
                  </span>
                  <span className="text-slate-400">
                    {diffSummary.changedEdges} edges changed
                  </span>
                </div>
                {diff.added_nodes.length > 0 && (
                  <div>
                    <div className={labelCls}>Added nodes</div>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-1 max-h-[160px] overflow-y-auto">
                      {diff.added_nodes.slice(0, 40).map((n) => (
                        <button
                          key={n.node_id}
                          onClick={() => selectNode(n.node_id)}
                          className="flex items-center gap-2 text-left text-xs rounded-md px-2 py-1 hover:bg-slate-50 dark:hover:bg-slate-700/60"
                        >
                          <span
                            className="w-2 h-2 rounded-full shrink-0"
                            style={{ background: hexFor(n.node_type) }}
                          />
                          <span className="min-w-0 truncate text-slate-700 dark:text-slate-200">
                            {n.name || n.node_id}
                          </span>
                          <span className="ml-auto shrink-0 text-[10px] font-mono text-slate-400">
                            {nodeTypeLabel(n.node_type)}
                          </span>
                        </button>
                      ))}
                    </div>
                  </div>
                )}
                {diff.removed_nodes.length > 0 && (
                  <div>
                    <div className={labelCls}>Removed nodes</div>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-1 max-h-[160px] overflow-y-auto">
                      {diff.removed_nodes.slice(0, 40).map((n) => (
                        <div
                          key={n.node_id}
                          className="flex items-center gap-2 text-xs rounded-md px-2 py-1 opacity-70"
                        >
                          <span
                            className="w-2 h-2 rounded-full shrink-0"
                            style={{ background: hexFor(n.node_type) }}
                          />
                          <span className="min-w-0 truncate text-slate-600 dark:text-slate-300">
                            {n.name || n.node_id}
                          </span>
                          <span className="ml-auto shrink-0 text-[10px] font-mono text-slate-400">
                            {nodeTypeLabel(n.node_type)}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                {diff.per_version.length > 0 && (
                  <div>
                    <div className={labelCls}>Per-version</div>
                    <div className="space-y-1">
                      {diff.per_version.map((v) => (
                        <div
                          key={v.version}
                          className="flex items-center gap-2 text-[11px] font-mono text-slate-500"
                        >
                          <span className="font-semibold">v{v.version}</span>
                          <span className="truncate">{truncate(v.summary || "", 80)}</span>
                          <span className="ml-auto shrink-0 text-slate-400">
                            +{v.added} / −{v.removed} / {v.changed_edges} edges
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </SectionCard>

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
                  <button onClick={() => selectNode(null)} className={btnGhost}>
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
                        <span
                          className="inline-flex items-center gap-1.5 shrink-0"
                          title={relLabel(e.relationship)}
                        >
                          <span
                            className="w-3 h-0.5 rounded"
                            style={{ backgroundColor: relHex(e.relationship) }}
                          />
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
