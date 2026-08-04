"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { graphApi } from "@/lib/api/engineeringGraph";
import { orgGraphApi } from "@/lib/api/organizationGraph";
import type {
  OrgAcquireMultiResult,
  OrgCrossEdge,
  OrgRepository,
  OrgStats,
} from "@/lib/api/organizationGraph";
import type {
  GraphDiff,
  GraphEdge,
  GraphNode,
  GraphQueryResult,
  GraphStats,
  GraphVersionRecord,
  NodeDetail,
  NodeHistory,
  ExplainResult,
  RelatedEvidence,
} from "@/lib/api/engineeringGraph";
import { InteractiveGraph } from "@/components/graph/InteractiveGraph";
import {
  applyViewFilters,
  hexFor,
  nodeTypeLabel,
  relHex,
  relLabel,
  snapshotFacets,
  summarizeDiff,
  truncate,
  NODE_HEX,
  RELATIONSHIP_HEX,
  NODE_CATEGORY,
  type VizEdge,
  type VizNode,
} from "@/lib/graph/graphModel";
import { useGraphSocket, useLatestGraphEvent } from "@/lib/graph/useGraphSocket";

// ── Constants ─────────────────────────────────────────────────

const MAX_VIS_NODES = 250;
const ALL_NODE_TYPES = Object.keys(NODE_HEX).sort();
const ALL_RELATIONSHIPS = Object.keys(RELATIONSHIP_HEX).sort();

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

function nodeToViz(n: GraphNode): VizNode {
  const payload = (n.payload ?? {}) as Record<string, unknown>;
  const prov = (n.provenance ?? {}) as Record<string, unknown>;
  const repoId =
    (typeof payload.repository_id === "string" && payload.repository_id) ||
    (typeof payload.repository === "string" && payload.repository) ||
    (typeof prov.repository_id === "string" && prov.repository_id) ||
    undefined;
  return {
    id: n.node_id,
    label: n.name || n.node_id,
    nodeType: n.node_type,
    repositoryId: repoId,
    sublabel: n.source_ref || (typeof payload.source_ref === "string" ? payload.source_ref : undefined),
    data: n as unknown as { [k: string]: unknown },
  };
}

function edgeToViz(e: GraphEdge): VizEdge {
  return {
    id: e.edge_id,
    source: e.source_id,
    target: e.target_id,
    relationship: e.relationship,
    weight: e.weight,
  };
}

interface GraphState {
  nodes: VizNode[];
  edges: VizEdge[];
}

function mergeGraph(
  base: GraphState,
  incoming: GraphState,
  max = MAX_VIS_NODES
): { graph: GraphState; truncated: boolean } {
  const nodeMap = new Map<string, VizNode>(base.nodes.map((n) => [n.id, n]));
  const edgeMap = new Map<string, VizEdge>(base.edges.map((e) => [e.id, e]));
  let truncated = false;
  for (const n of incoming.nodes) {
    if (!nodeMap.has(n.id)) {
      if (nodeMap.size >= max) {
        truncated = true;
        continue;
      }
      nodeMap.set(n.id, n);
    }
  }
  for (const e of incoming.edges) {
    if (!edgeMap.has(e.id)) edgeMap.set(e.id, e);
  }
  return {
    graph: { nodes: [...nodeMap.values()], edges: [...edgeMap.values()] },
    truncated,
  };
}

// ── Small UI atoms ─────────────────────────────────────────────

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
  right,
  children,
}: {
  title: string;
  subtitle?: string;
  right?: React.ReactNode;
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
        {right}
      </div>
      {children}
    </div>
  );
}

function ChipPicker({
  label,
  options,
  selected,
  counts,
  onChange,
  swatch,
}: {
  label: string;
  options: string[];
  selected: Set<string>;
  counts?: Record<string, number>;
  onChange: (next: Set<string>) => void;
  swatch: (opt: string) => string;
}) {
  return (
    <details className="relative group">
      <summary className="cursor-pointer list-none rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2.5 py-1.5 text-xs font-medium text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors select-none">
        <span className="inline-flex items-center gap-1.5">
          {label}
          <span className="font-mono text-[10px] text-slate-400">
            {selected.size ? `${selected.size}` : "all"}
          </span>
        </span>
      </summary>
      <div className="absolute z-30 mt-1 w-64 max-h-80 overflow-y-auto rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 shadow-xl p-3 space-y-1">
        <div className="flex items-center justify-between pb-1.5 border-b border-slate-100 dark:border-slate-700">
          <button
            onClick={() => onChange(new Set())}
            className="text-[11px] font-medium text-primary-600 dark:text-primary-400 hover:underline"
          >
            All
          </button>
          <button
            onClick={() => onChange(new Set(options))}
            className="text-[11px] font-medium text-primary-600 dark:text-primary-400 hover:underline"
          >
            None
          </button>
        </div>
        {options.map((opt) => {
          const checked = selected.has(opt);
          return (
            <label
              key={opt}
              className="flex items-center gap-2 px-1.5 py-1 rounded-md hover:bg-slate-50 dark:hover:bg-slate-700/60 cursor-pointer"
            >
              <input
                type="checkbox"
                checked={checked}
                onChange={() => {
                  const next = new Set(selected);
                  if (next.has(opt)) next.delete(opt);
                  else next.add(opt);
                  onChange(next);
                }}
                className="accent-indigo-500"
              />
              <span
                className="w-2.5 h-2.5 rounded-full shrink-0"
                style={{ backgroundColor: swatch(opt) }}
              />
              <span className="text-xs text-slate-700 dark:text-slate-200 truncate">
                {nodeTypeLabel(opt)}
              </span>
              {counts && counts[opt] != null && (
                <span className="ml-auto font-mono text-[10px] text-slate-400">
                  {counts[opt]}
                </span>
              )}
            </label>
          );
        })}
      </div>
    </details>
  );
}

function RelRow({ rel, onSelect }: { rel: string; onSelect?: () => void }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[10px] font-mono px-2 py-0.5 rounded-full bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300">
      <span className="w-3 h-0.5 rounded" style={{ backgroundColor: relHex(rel) }} />
      {rel}
    </span>
  );
}

// ── Organization (cross-repository) panel — Phase 19A/19C ──────

function OrgPanel({
  orgStats,
  orgRepos,
  orgCrossEdges,
  orgLoading,
  orgError,
  onRefresh,
  onAcquired,
}: {
  orgStats: OrgStats | null;
  orgRepos: OrgRepository[];
  orgCrossEdges: OrgCrossEdge[];
  orgLoading: boolean;
  orgError: string | null;
  onRefresh: () => void;
  onAcquired: (res: OrgAcquireMultiResult) => void;
}) {
  const [registerRepoId, setRegisterRepoId] = useState("");
  const [registerName, setRegisterName] = useState("");
  const [registerPath, setRegisterPath] = useState("");
  const [linkSource, setLinkSource] = useState("");
  const [linkTarget, setLinkTarget] = useState("");
  const [linkRel, setLinkRel] = useState("imports_package");
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
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionNote, setActionNote] = useState<string | null>(null);

  const clearAction = () => {
    setActionError(null);
    setActionNote(null);
  };

  const handleRegister = async () => {
    if (!registerRepoId.trim()) {
      setActionError("repository_id is required");
      return;
    }
    clearAction();
    setBusy("register");
    try {
      await orgGraphApi.registerRepository({
        repository_id: registerRepoId.trim(),
        name: registerName.trim() || registerRepoId.trim(),
        path: registerPath.trim() || undefined,
        source_type: "local",
      });
      setRegisterRepoId("");
      setRegisterName("");
      setRegisterPath("");
      setActionNote(`Registered ${registerRepoId.trim()}`);
      onRefresh();
    } catch (e: unknown) {
      setActionError(e instanceof Error ? e.message : "Register failed");
    } finally {
      setBusy(null);
    }
  };

  const handleLink = async () => {
    if (!linkSource.trim() || !linkTarget.trim() || !linkRel.trim()) {
      setActionError("source, target and relationship are required");
      return;
    }
    clearAction();
    setBusy("link");
    try {
      await orgGraphApi.link({
        source_repository_id: linkSource.trim(),
        target_repository_id: linkTarget.trim(),
        relationship: linkRel.trim(),
      });
      setLinkSource("");
      setLinkTarget("");
      setActionNote(`Linked ${linkSource.trim()} → ${linkTarget.trim()}`);
      onRefresh();
    } catch (e: unknown) {
      setActionError(e instanceof Error ? e.message : "Link failed");
    } finally {
      setBusy(null);
    }
  };

  const handleAcquire = async () => {
    let repos;
    try {
      repos = JSON.parse(manifestText);
    } catch {
      setActionError("manifest is not valid JSON");
      return;
    }
    if (!Array.isArray(repos) || repos.length === 0) {
      setActionError("manifest must be a non-empty array of repository specs");
      return;
    }
    clearAction();
    setBusy("acquire");
    try {
      const res = await orgGraphApi.acquireMulti({ repositories: repos });
      setActionNote(
        `Acquired ${res.repositories_acquired} repo(s), ${res.relationships} cross-edge(s), ` +
          `${res.ingested_files} evidence file(s)`
      );
      onAcquired(res);
    } catch (e: unknown) {
      setActionError(e instanceof Error ? e.message : "Acquire failed");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-slate-900 dark:text-white">
          Organization graph
        </h2>
        <div className="flex items-center gap-2">
          {orgLoading && (
            <span className="text-[11px] text-slate-400">loading…</span>
          )}
          <button
            onClick={onRefresh}
            disabled={orgLoading}
            className="px-3 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 text-xs font-medium text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors disabled:opacity-50"
          >
            Refresh
          </button>
        </div>
      </div>

      {orgError && (
        <p className="mt-2 text-xs text-rose-600 dark:text-rose-400">{orgError}</p>
      )}
      {actionError && (
        <p className="mt-2 text-xs text-rose-600 dark:text-rose-400">{actionError}</p>
      )}
      {actionNote && (
        <p className="mt-2 text-xs text-emerald-600 dark:text-emerald-400">{actionNote}</p>
      )}

      <div className="mt-3 grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
        <div className="rounded-lg bg-slate-50 dark:bg-slate-900 px-3 py-2">
          <div className="text-slate-400">Repositories</div>
          <div className="font-mono text-lg text-slate-900 dark:text-white">
            {orgStats?.repository_count ?? "—"}
          </div>
        </div>
        <div className="rounded-lg bg-slate-50 dark:bg-slate-900 px-3 py-2">
          <div className="text-slate-400">Cross-edges</div>
          <div className="font-mono text-lg text-slate-900 dark:text-white">
            {orgStats?.cross_edge_count ?? "—"}
          </div>
        </div>
        <div className="rounded-lg bg-slate-50 dark:bg-slate-900 px-3 py-2">
          <div className="text-slate-400">Namespace nodes</div>
          <div className="font-mono text-lg text-slate-900 dark:text-white">
            {orgStats?.node_count ?? "—"}
          </div>
        </div>
        <div className="rounded-lg bg-slate-50 dark:bg-slate-900 px-3 py-2">
          <div className="text-slate-400">Last updated</div>
          <div className="font-mono text-[11px] text-slate-900 dark:text-white truncate">
            {orgStats ? new Date(orgStats.last_updated).toLocaleString() : "—"}
          </div>
        </div>
      </div>

      <div className="mt-4 grid lg:grid-cols-2 gap-4">
        <div>
          <h3 className="text-xs font-medium text-slate-500 dark:text-slate-400">
            Registered repositories
          </h3>
          {orgRepos.length === 0 ? (
            <p className="mt-1 text-[11px] text-slate-400">
              None registered yet — register one or acquire from a manifest.
            </p>
          ) : (
            <ul className="mt-1 space-y-1">
              {orgRepos.map((r) => (
                <li
                  key={r.repository_id}
                  className="flex items-center justify-between gap-2 rounded-lg border border-slate-200 dark:border-slate-700 px-2.5 py-1.5 text-xs"
                >
                  <span className="font-mono text-slate-800 dark:text-slate-200">
                    {truncate(r.repository_id, 28)}
                  </span>
                  <span className="text-[10px] text-slate-400">{r.source_type}</span>
                </li>
              ))}
            </ul>
          )}

          <div className="mt-3 rounded-lg border border-slate-200 dark:border-slate-700 p-2.5">
            <div className="flex items-center gap-2">
              <input
                value={registerRepoId}
                onChange={(e) => setRegisterRepoId(e.target.value)}
                placeholder="repository_id"
                className="flex-1 min-w-0 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1 text-xs focus:outline-none focus:ring-2 focus:ring-primary-500"
              />
              <input
                value={registerName}
                onChange={(e) => setRegisterName(e.target.value)}
                placeholder="name"
                className="flex-1 min-w-0 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1 text-xs focus:outline-none focus:ring-2 focus:ring-primary-500"
              />
            </div>
            <div className="mt-2 flex items-center gap-2">
              <input
                value={registerPath}
                onChange={(e) => setRegisterPath(e.target.value)}
                placeholder="path (local checkout)"
                className="flex-1 min-w-0 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1 text-xs focus:outline-none focus:ring-2 focus:ring-primary-500"
              />
              <button
                onClick={handleRegister}
                disabled={busy === "register"}
                className="shrink-0 px-3 py-1.5 rounded-lg bg-primary-600 text-white text-xs font-medium hover:bg-primary-700 disabled:opacity-50"
              >
                {busy === "register" ? "…" : "Register"}
              </button>
            </div>
          </div>
        </div>

        <div>
          <h3 className="text-xs font-medium text-slate-500 dark:text-slate-400">
            Cross-repository edges
          </h3>
          {orgCrossEdges.length === 0 ? (
            <p className="mt-1 text-[11px] text-slate-400">No cross-edges yet.</p>
          ) : (
            <ul className="mt-1 space-y-1">
              {orgCrossEdges.map((e) => (
                <li
                  key={e.edge_id}
                  className="flex items-center justify-between gap-2 rounded-lg border border-slate-200 dark:border-slate-700 px-2.5 py-1.5 text-xs"
                >
                  <span className="font-mono text-slate-800 dark:text-slate-200">
                    {truncate(e.source_repository_id, 14)} →{" "}
                    {truncate(e.target_repository_id, 14)}
                  </span>
                  <RelRow rel={e.relationship} />
                </li>
              ))}
            </ul>
          )}

          <div className="mt-3 rounded-lg border border-slate-200 dark:border-slate-700 p-2.5">
            <div className="flex flex-wrap items-center gap-2">
              <input
                value={linkSource}
                onChange={(e) => setLinkSource(e.target.value)}
                placeholder="source repo id"
                className="flex-1 min-w-0 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1 text-xs focus:outline-none focus:ring-2 focus:ring-primary-500"
              />
              <span className="text-[10px] text-slate-400">→</span>
              <input
                value={linkTarget}
                onChange={(e) => setLinkTarget(e.target.value)}
                placeholder="target repo id"
                className="flex-1 min-w-0 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1 text-xs focus:outline-none focus:ring-2 focus:ring-primary-500"
              />
            </div>
            <div className="mt-2 flex items-center gap-2">
              <select
                value={linkRel}
                onChange={(e) => setLinkRel(e.target.value)}
                className="flex-1 min-w-0 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1 text-xs focus:outline-none focus:ring-2 focus:ring-primary-500"
              >
                <option value="imports_package">imports_package</option>
                <option value="shares_library">shares_library</option>
                <option value="depends_on_repository">depends_on_repository</option>
              </select>
              <button
                onClick={handleLink}
                disabled={busy === "link"}
                className="shrink-0 px-3 py-1.5 rounded-lg bg-primary-600 text-white text-xs font-medium hover:bg-primary-700 disabled:opacity-50"
              >
                {busy === "link" ? "…" : "Link"}
              </button>
            </div>
          </div>
        </div>
      </div>

      <div className="mt-4 rounded-lg border border-slate-200 dark:border-slate-700 p-2.5">
        <h3 className="text-xs font-medium text-slate-500 dark:text-slate-400">
          Acquire + link from manifest (Phase 19C)
        </h3>
        <textarea
          value={manifestText}
          onChange={(e) => setManifestText(e.target.value)}
          spellCheck={false}
          rows={5}
          className="mt-2 w-full rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1 font-mono text-[11px] text-slate-800 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-primary-500"
        />
        <div className="mt-2 flex items-center gap-2">
          <span className="text-[11px] text-slate-400">
            Manifest: JSON array of repository specs with{" "}
            <code className="font-mono">repository_id</code>,{" "}
            <code className="font-mono">source</code> ("local" | "github"), and{" "}
            <code className="font-mono">path</code> /{" "}
            <code className="font-mono">owner:repo</code>. Local sources are
            deterministic and offline.
          </span>
          <button
            onClick={handleAcquire}
            disabled={busy === "acquire"}
            className="ml-auto shrink-0 px-3 py-1.5 rounded-lg bg-primary-600 text-white text-xs font-medium hover:bg-primary-700 disabled:opacity-50"
          >
            {busy === "acquire" ? "…" : "Acquire"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Main page ──────────────────────────────────────────────────

export default function EngineeringGraphPage() {
  // Semantic query (§5)
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<GraphQueryResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [queryError, setQueryError] = useState<string | null>(null);

  // Stats / versions (§6)
  const [stats, setStats] = useState<GraphStats | null>(null);
  const [versions, setVersions] = useState<GraphVersionRecord[]>([]);

  // Live updates (§10)
  const ws = useGraphSocket();
  const liveEvent = useLatestGraphEvent();
  const [liveNotice, setLiveNotice] = useState<string | null>(null);

  // Interactive graph (§1–§4)
  const [graph, setGraph] = useState<GraphState>({ nodes: [], edges: [] });
  const [rootId, setRootId] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [focusId, setFocusId] = useState<string | null>(null);
  const [resetToken, setResetToken] = useState(0);
  const [fitToken, setFitToken] = useState(0);
  const [vizLoading, setVizLoading] = useState(false);
  const [vizError, setVizError] = useState<string | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [depth, setDepth] = useState(2);
  const [crumb, setCrumb] = useState<{ id: string; label: string }[]>([]);

  // View filters (§5)
  const [searchTerm, setSearchTerm] = useState("");
  const [nodeFilter, setNodeFilter] = useState<Set<string>>(new Set());
  const [relFilter, setRelFilter] = useState<Set<string>>(new Set());
  const [repoFilter, setRepoFilter] = useState<string | null>(null);

  // Provenance (§7)
  const [nodeDetail, setNodeDetail] = useState<NodeDetail | null>(null);
  const [history, setHistory] = useState<NodeHistory | null>(null);
  const [explain, setExplain] = useState<ExplainResult | null>(null);
  const [nodeLoading, setNodeLoading] = useState(false);
  const [nodeError, setNodeError] = useState<string | null>(null);

  // Timeline diff (§6)
  const [fromVersion, setFromVersion] = useState<number | null>(null);
  const [toVersion, setToVersion] = useState<number | null>(null);
  const [diff, setDiff] = useState<GraphDiff | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [diffError, setDiffError] = useState<string | null>(null);

  // Phase 19C — Organization (cross-repository) graph mode (§19A).
  const [graphMode, setGraphMode] = useState<"kbg" | "org">("kbg");
  const [orgStats, setOrgStats] = useState<OrgStats | null>(null);
  const [orgRepos, setOrgRepos] = useState<OrgRepository[]>([]);
  const [orgCrossEdges, setOrgCrossEdges] = useState<OrgCrossEdge[]>([]);
  const [orgLoading, setOrgLoading] = useState(false);
  const [orgError, setOrgError] = useState<string | null>(null);

  const detailRef = useRef<HTMLDivElement>(null);

  const selectedNode = useMemo(() => {
    if (!selectedId) return null;
    const vn = graph.nodes.find((n) => n.id === selectedId);
    return vn ?? null;
  }, [selectedId, graph.nodes]);

  // ── Graph loading helpers ────────────────────────────────────

  const loadVersion = useCallback(async () => {
    try {
      const v = await graphApi.version();
      setStats(v.version);
      setVersions([...v.history].sort((a, b) => a.version - b.version));
    } catch {
      // Non-fatal.
    }
  }, []);

  const loadOrg = useCallback(async () => {
    if (graphMode !== "org") return;
    setOrgLoading(true);
    setOrgError(null);
    try {
      const [st, repos, edges] = await Promise.all([
        orgGraphApi.stats(),
        orgGraphApi.repositories(),
        orgGraphApi.crossEdges(),
      ]);
      setOrgStats(st);
      setOrgRepos(repos);
      setOrgCrossEdges(edges);
    } catch (e: unknown) {
      setOrgError(e instanceof Error ? e.message : "Org load failed");
    } finally {
      setOrgLoading(false);
    }
  }, [graphMode]);

  useEffect(() => {
    void loadVersion();
    void loadOrg();
  }, [loadVersion, loadOrg]);

  const loadDetail = useCallback(async (id: string) => {
    setNodeLoading(true);
    setNodeError(null);
    setNodeDetail(null);
    setHistory(null);
    setExplain(null);
    try {
      const [detail, hist, expl] = await Promise.all([
        graphApi.node(id),
        graphApi.history(id),
        graphApi.explain(id),
      ]);
      setNodeDetail(detail);
      setHistory(hist);
      setExplain(expl);
      setCrumb((prev) =>
        prev.some((c) => c.id === id)
          ? prev
          : [...prev, { id, label: detail.node.name || id }]
      );
    } catch (e: unknown) {
      setNodeError(e instanceof Error ? e.message : "Failed to load node");
    } finally {
      setNodeLoading(false);
    }
  }, []);

  /** Select a node: load provenance + center it in the graph. */
  const selectNode = useCallback(
    (id: string | null) => {
      if (!id) {
        setSelectedId(null);
        return;
      }
      setSelectedId(id);
      setFocusId(id);
      void loadDetail(id);
    },
    [loadDetail]
  );

  /** Incremental expansion: merge a bounded neighborhood into the graph. */
  const expandNode = useCallback(
    async (id: string, d?: number) => {
      const useDepth = d ?? depth;
      setVizLoading(true);
      setVizError(null);
      try {
        let res;
        if (graphMode === "org") {
          const t = await orgGraphApi.traversal(id, {
            depth: useDepth,
            maxNodes: 250,
          });
          res = t;
        } else {
          res = await graphApi.neighborhood(id, useDepth, 60);
        }
        setRootId((prev) => prev ?? id);
        const merged = mergeGraph(graph, {
          nodes: res.nodes.map(nodeToViz),
          edges: res.edges.map(edgeToViz),
        });
        setGraph(merged.graph);
        setTruncated(merged.truncated);
        setFocusId(id);
        setSelectedId(id);
        void loadDetail(id);
      } catch (e: unknown) {
        setVizError(
          e instanceof Error ? e.message : "Neighborhood traversal failed"
        );
      } finally {
        setVizLoading(false);
      }
    },
    [depth, graph, graphMode, loadDetail]
  );

  const removeNode = useCallback((id: string) => {
    setGraph((prev) => ({
      nodes: prev.nodes.filter((n) => n.id !== id),
      edges: prev.edges.filter((e) => e.source !== id && e.target !== id),
    }));
    setSelectedId(null);
  }, []);

  const clearGraph = useCallback(() => {
    setGraph({ nodes: [], edges: [] });
    setRootId(null);
    setSelectedId(null);
    setCrumb([]);
    setTruncated(false);
  }, []);

  // ── Semantic query (§5) ──────────────────────────────────────

  const runQuery = useCallback(
    async (text?: string) => {
      const q = (text ?? query).trim();
      if (!q) return;
      setLoading(true);
      setQueryError(null);
      try {
        let res;
        if (graphMode === "org") {
          res = await orgGraphApi.query(q, {
            scope: "organization",
            limit: 25,
          });
        } else {
          res = await graphApi.query(q);
        }
        setResults(res);
        if (res.nodes.length) {
          setGraph((prev) =>
            mergeGraph(prev, {
              nodes: res.nodes.map(nodeToViz),
              edges: res.edges.map(edgeToViz),
            }).graph
          );
          setRootId((prev) => prev ?? res.nodes[0].node_id);
        }
      } catch (e: unknown) {
        setQueryError(e instanceof Error ? e.message : "Query failed");
        setResults(null);
      } finally {
        setLoading(false);
      }
    },
    [query, graphMode]
  );

  const refreshOrg = useCallback(() => {
    void loadOrg();
  }, [loadOrg]);

  // ── Live updates (§10): apply version increments without reload ──

  useEffect(() => {
    if (!liveEvent) return;
    if (liveEvent.event_type === "version_incremented") {
      setLiveNotice(
        `Graph updated to v${liveEvent.data.version}${
          liveEvent.data.run_id ? ` by ${truncate(liveEvent.data.run_id, 40)}` : ""
        }`
      );
      if (liveEvent.data.stats) setStats(liveEvent.data.stats);
      void loadVersion();
      if (rootId && graph.nodes.length) {
        void graphApi
          .neighborhood(rootId, Math.max(depth, 1), 60)
          .then((res) => {
            const merged = mergeGraph(graph, {
              nodes: res.nodes.map(nodeToViz),
              edges: res.edges.map(edgeToViz),
            });
            setGraph(merged.graph);
            setTruncated(merged.truncated);
          })
          .catch(() => undefined);
      }
    }
  }, [liveEvent, rootId, depth, graph, loadVersion]);

  const refreshNeighborhood = useCallback(() => {
    if (!rootId) return;
    void expandNode(rootId, depth);
  }, [rootId, depth, expandNode]);

  // ── View filtering (§5) ──────────────────────────────────────

  const facets = useMemo(
    () => snapshotFacets(graph.nodes, graph.edges),
    [graph.nodes, graph.edges]
  );

  const nodeTypeOptions = useMemo(() => {
    const present = new Set(facets.nodeTypes);
    return ALL_NODE_TYPES.filter((t) => present.has(t) || stats?.node_types?.[t]);
  }, [facets.nodeTypes, stats]);

  const categoryTypes = useMemo(() => {
    const acc: Record<string, string[]> = {};
    for (const [type, cat] of Object.entries(NODE_CATEGORY)) {
      (acc[cat] ??= []).push(type);
    }
    return acc;
  }, []);

  const relOptions = useMemo(() => {
    const present = new Set(facets.relationships);
    return ALL_RELATIONSHIPS.filter((r) => present.has(r));
  }, [facets.relationships]);

  const nodeCounts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const n of graph.nodes) c[n.nodeType] = (c[n.nodeType] ?? 0) + 1;
    return c;
  }, [graph.nodes]);

  const relCounts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const e of graph.edges) c[e.relationship] = (c[e.relationship] ?? 0) + 1;
    return c;
  }, [graph.edges]);

  const repoOptions = useMemo(
    () => facets.repositories.filter((r) => r !== "default"),
    [facets.repositories]
  );

  const visible = useMemo(() => {
    return applyViewFilters(graph.nodes, graph.edges, {
      nodeTypes: nodeFilter.size ? nodeFilter : null,
      relationships: relFilter.size ? relFilter : null,
      repositories: repoFilter ? new Set([repoFilter]) : null,
      search: searchTerm,
    });
  }, [graph.nodes, graph.edges, nodeFilter, relFilter, repoFilter, searchTerm]);

  const highlightedIds = useMemo(() => {
    if (!selectedId) return null;
    const set = new Set<string>([selectedId]);
    for (const e of graph.edges) {
      if (e.source === selectedId) set.add(e.target);
      if (e.target === selectedId) set.add(e.source);
    }
    return set;
  }, [selectedId, graph.edges]);

  // ── Provenance helper (§7) ───────────────────────────────────

  const relatedOfType = useCallback(
    (types: string[]) =>
      (explain?.related ?? []).filter((r) => types.includes(r.node_type)),
    [explain]
  );

  const isForbiddenProvenanceKey = (k: string) =>
    /chain_?of_?thought|hidden_?prompt|api_?key|secret/i.test(k);

  // ── Timeline diff (§6) ───────────────────────────────────────

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
    } catch (e: unknown) {
      setDiffError(e instanceof Error ? e.message : "Diff failed");
      setDiff(null);
    } finally {
      setDiffLoading(false);
    }
  }, [fromVersion, toVersion]);

  const jumpToRepo = useCallback(
    (repo: string) => {
      setRepoFilter(repo);
      const first = graph.nodes.find(
        (n) => (n.repositoryId ?? "default") === repo
      );
      if (first) setFocusId(first.id);
    },
    [graph.nodes]
  );

  // ── Keyboard shortcuts (§11) ─────────────────────────────────

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (
        el &&
        (el.tagName === "INPUT" ||
          el.tagName === "TEXTAREA" ||
          el.tagName === "SELECT" ||
          el.isContentEditable)
      )
        return;
      if (e.key.toLowerCase() === "f") {
        e.preventDefault();
        setFitToken((t) => t + 1);
      } else if (e.key.toLowerCase() === "r") {
        e.preventDefault();
        setResetToken((t) => t + 1);
      } else if (e.key === "Escape") {
        setSelectedId(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const selectedVizNode = selectedNode;

  return (
    <div className="space-y-6">
      {/* Header + live badge */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white">
            Engineering Knowledge Graph
          </h1>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
            Unified, temporal graph over code, requirements, goals, plans,
            evidence, consensus, notebook, memory and runs — evidence-only.
            Interactive force-directed exploration.
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
          value={stats ? `${stats.run_count} / ${stats.repository_count}` : "—"}
          accent="text-slate-800 dark:text-slate-100"
        />
      </div>

      {/* Toolbar: search + filters + actions */}
      {graphMode === "org" && (
        <OrgPanel
          orgStats={orgStats}
          orgRepos={orgRepos}
          orgCrossEdges={orgCrossEdges}
          orgLoading={orgLoading}
          orgError={orgError}
          onRefresh={refreshOrg}
          onAcquired={(res) => {
            setOrgStats((prev) =>
              prev
                ? { ...prev, repository_count: res.repositories_acquired }
                : prev
            );
            void refreshOrg();
          }}
        />
      )}
      <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <div
            role="group"
            aria-label="Graph scope"
            className="inline-flex rounded-lg border border-slate-300 dark:border-slate-600 p-0.5"
          >
            <button
              type="button"
              onClick={() => setGraphMode("kbg")}
              className={`px-2.5 py-1 rounded-md text-xs font-medium transition-colors ${
                graphMode === "kbg"
                  ? "bg-primary-600 text-white"
                  : "text-slate-500 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-700"
              }`}
              title="Single-repository knowledge graph"
            >
              KBG
            </button>
            <button
              type="button"
              onClick={() => setGraphMode("org")}
              className={`px-2.5 py-1 rounded-md text-xs font-medium transition-colors ${
                graphMode === "org"
                  ? "bg-primary-600 text-white"
                  : "text-slate-500 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-700"
              }`}
              title="Organization (cross-repository) graph"
            >
              Org
            </button>
          </div>
          <input
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            placeholder="Filter current graph: name, symbol, ref…"
            className="flex-1 min-w-[200px] rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-3 py-1.5 text-sm text-slate-900 dark:text-white placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-primary-500"
          />
          <ChipPicker
            label="Node types"
            options={nodeTypeOptions}
            selected={nodeFilter}
            counts={nodeCounts}
            onChange={setNodeFilter}
            swatch={hexFor}
          />
          <ChipPicker
            label="Relationships"
            options={relOptions}
            selected={relFilter}
            counts={relCounts}
            onChange={setRelFilter}
            swatch={relHex}
          />
          <label className="flex items-center gap-1.5 text-xs text-slate-500">
            repo
            <select
              value={repoFilter ?? ""}
              onChange={(e) => setRepoFilter(e.target.value || null)}
              className="rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1.5 text-xs text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-primary-500 max-w-[180px]"
            >
              <option value="">all</option>
              {repoOptions.map((r) => (
                <option key={r} value={r}>
                  {truncate(r, 24)}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-1.5 text-xs text-slate-500">
            depth
            <select
              value={depth}
              onChange={(e) => setDepth(Number(e.target.value))}
              className="rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1.5 text-xs text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-primary-500"
            >
              <option value={1}>1</option>
              <option value={2}>2</option>
              <option value={3}>3</option>
            </select>
          </label>
          <span className="w-px h-6 bg-slate-200 dark:bg-slate-700" />
          <button
            onClick={() => setFitToken((t) => t + 1)}
            title="Fit graph to view (F)"
            className="px-3 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 text-xs font-medium text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors"
          >
            Fit
          </button>
          <button
            onClick={() => setResetToken((t) => t + 1)}
            title="Re-run force layout (R)"
            className="px-3 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 text-xs font-medium text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors"
          >
            Relayout
          </button>
          <button
            onClick={refreshNeighborhood}
            disabled={vizLoading || !rootId}
            className="px-3 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 text-xs font-medium text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors disabled:opacity-50"
          >
            {vizLoading ? "…" : "Refresh graph"}
          </button>
          {selectedId && (
            <button
              onClick={() => removeNode(selectedId)}
              title="Remove the selected node from the current view"
              className="px-3 py-1.5 rounded-lg border border-rose-300 dark:border-rose-700 text-xs font-medium text-rose-600 dark:text-rose-300 hover:bg-rose-50 dark:hover:bg-rose-900/20 transition-colors"
            >
              Collapse node
            </button>
          )}
          {graph.nodes.length > 0 && (
            <button
              onClick={clearGraph}
              className="px-3 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 text-xs font-medium text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors"
            >
              Clear
            </button>
          )}
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-3 text-[11px] text-slate-400">
          <span>keys: F fit · R relayout · Esc deselect</span>
          <span className="font-mono">
            showing {visible.nodes.length}/{graph.nodes.length} nodes ·{" "}
            {visible.edges.length}/{graph.edges.length} edges
          </span>
          {truncated && (
            <span className="text-amber-600 dark:text-amber-400 font-medium">
              view capped at {MAX_VIS_NODES} nodes — expand selectively
            </span>
          )}
        </div>
      </div>

      {/* Graph + provenance column */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        {/* Interactive graph engine */}
        <div className="xl:col-span-2 space-y-4">
          <SectionCard
            title="Interactive Graph"
            subtitle="React Flow engine · d3-force layout · click to inspect, double-click to expand neighbors"
          >
            {vizError && (
              <div className="mb-3 text-sm text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 rounded-lg px-3 py-2">
                {vizError}
              </div>
            )}
            <InteractiveGraph
              nodes={visible.nodes}
              edges={visible.edges}
              selectedId={selectedId}
              highlightedIds={highlightedIds}
              rootId={rootId}
              focusId={focusId}
              resetToken={resetToken}
              fitToken={fitToken}
              heightClass="h-[640px]"
              onSelectNode={selectNode}
              onExpandNode={(id) => void expandNode(id)}
              onRelayout={() => setResetToken((t) => t + 1)}
            />
          </SectionCard>

          {/* Query the graph (§5) */}
          <SectionCard
            title="Query the Graph"
            subtitle="Planner-driven search across requirements, symbols, runs and memory"
          >
            <div className="flex gap-2">
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && void runQuery()}
                placeholder="e.g. why was auth implemented? or affected tests for auth"
                className="flex-1 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-3 py-2 text-sm text-slate-900 dark:text-white placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-primary-500"
              />
              <button
                onClick={() => void runQuery()}
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
                <div className="space-y-0.5 max-h-[280px] overflow-y-auto">
                  {results.nodes.map((n) => (
                    <button
                      key={n.node_id}
                      onClick={() => selectNode(n.node_id)}
                      className={`w-full text-left flex items-start gap-3 px-3 py-2.5 rounded-lg transition-all ${
                        selectedId === n.node_id
                          ? "bg-primary-50 dark:bg-primary-900/30 ring-1 ring-primary-500/40"
                          : "hover:bg-slate-50 dark:hover:bg-slate-700/60"
                      }`}
                    >
                      <span
                        className={`mt-1 w-2.5 h-2.5 rounded-full shrink-0 ${nodeColor(n.node_type)}`}
                      />
                      <span className="min-w-0">
                        <span className="block text-sm font-medium text-slate-800 dark:text-slate-100 truncate">
                          {n.name || n.node_id}
                        </span>
                        <span className="block text-[11px] text-slate-400 truncate">
                          {nodeTypeLabel(n.node_type)} · v{n.graph_version} · {n.status}
                        </span>
                        {n.source_ref && (
                          <span className="block text-[11px] font-mono text-slate-400 truncate">
                            {n.source_ref}
                          </span>
                        )}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </SectionCard>
        </div>

        {/* Provenance panel (§7) */}
        <div
          ref={detailRef}
          className="space-y-4"
        >
          <SectionCard
            title="Provenance & Navigation"
            subtitle="Evidence-only — never chain-of-thought or hidden reasoning"
            right={
              crumb.length > 1 && (
                <span className="text-[10px] font-mono text-slate-400">
                  {crumb.length} hops
                </span>
              )
            }
          >
            {/* Breadcrumbs (§2/§11) */}
            {crumb.length > 0 && (
              <div className="flex flex-wrap items-center gap-1 mb-3 text-[11px] font-mono">
                {crumb.map((c, i) => (
                  <span key={c.id} className="inline-flex items-center gap-1">
                    {i > 0 && <span className="text-slate-300 dark:text-slate-600">›</span>}
                    <button
                      onClick={() => selectNode(c.id)}
                      className="max-w-[140px] truncate text-primary-600 dark:text-primary-400 hover:underline"
                      title={c.id}
                    >
                      {truncate(c.label, 26)}
                    </button>
                  </span>
                ))}
              </div>
            )}

            {nodeLoading && (
              <div className="text-sm text-slate-400 animate-pulse">Loading node…</div>
            )}
            {nodeError && (
              <div className="text-sm text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 rounded-lg px-3 py-2">
                {nodeError}
              </div>
            )}

            {!nodeLoading && !nodeError && selectedId && nodeDetail && (
              <div className="space-y-4">
                {/* Node header */}
                <div className="flex items-start gap-3">
                  <span
                    className={`mt-1 w-3 h-3 rounded-full shrink-0 ${nodeColor(nodeDetail.node.node_type)}`}
                  />
                  <div className="min-w-0">
                    <div className="font-semibold text-slate-900 dark:text-white break-words">
                      {nodeDetail.node.name || nodeDetail.node.node_id}
                    </div>
                    <div className="text-xs text-slate-500 font-mono break-all">
                      {nodeDetail.node.node_id}
                    </div>
                    <div className="mt-1 flex flex-wrap gap-1.5">
                      <span className="text-[10px] font-semibold px-2 py-0.5 rounded-md bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300">
                        {nodeTypeLabel(nodeDetail.node.node_type)}
                      </span>
                      <span className="text-[10px] font-semibold px-2 py-0.5 rounded-md bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300">
                        status: {nodeDetail.node.status}
                      </span>
                      <span className="text-[10px] font-semibold px-2 py-0.5 rounded-md bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300">
                        v{nodeDetail.node.graph_version}
                      </span>
                      {selectedVizNode?.repositoryId && (
                        <button
                          onClick={() => jumpToRepo(selectedVizNode.repositoryId!)}
                          title="Focus this repository in the graph"
                          className="text-[10px] font-semibold px-2 py-0.5 rounded-md bg-indigo-100 dark:bg-indigo-900/40 text-indigo-700 dark:text-indigo-300 hover:bg-indigo-200 dark:hover:bg-indigo-800/50 transition-colors"
                        >
                          repo: {truncate(selectedVizNode.repositoryId, 20)}
                        </button>
                      )}
                    </div>
                  </div>
                </div>

                {/* Relationships */}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  {(
                    [
                      ["Outgoing", nodeDetail.outgoing_edges],
                      ["Incoming", nodeDetail.incoming_edges],
                    ] as const
                  ).map(([title, edges]) => (
                    <div key={title}>
                      <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400 mb-2">
                        {title} ({edges.length})
                      </div>
                      {edges.length ? (
                        <div className="space-y-1">
                          {edges.slice(0, 10).map((e) => (
                            <button
                              key={e.edge_id}
                              onClick={() => selectNode(e.target_id)}
                              className="w-full text-left flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-slate-50 dark:hover:bg-slate-700/60 text-xs"
                            >
                              <span className="font-mono text-slate-500 truncate">
                                {truncate(e.source_id, 18)}
                              </span>
                              <span className="font-mono text-primary-500 font-semibold shrink-0" title={e.relationship}>
                                ─[{e.relationship}]→
                              </span>
                              <span className="font-mono text-slate-600 dark:text-slate-300 truncate">
                                {truncate(e.target_id, 18)}
                              </span>
                            </button>
                          ))}
                        </div>
                      ) : (
                        <div className="text-xs text-slate-400">none</div>
                      )}
                    </div>
                  ))}
                </div>

                {/* Related evidence (evidence only) */}
                {relatedOfType(["evidence"]).length > 0 && (
                  <EvidenceSection
                    title={`Evidence (${relatedOfType(["evidence"]).length})`}
                    items={relatedOfType(["evidence"])}
                    onSelect={selectNode}
                    nodeColor={nodeColor}
                  />
                )}

                {/* Consensus / decisions / notebook */}
                {relatedOfType(["consensus", "decision", "contradiction", "notebook_entry"]).length >
                  0 && (
                  <EvidenceSection
                    title={`Consensus & Notebook (${relatedOfType([
                      "consensus",
                      "decision",
                      "contradiction",
                      "notebook_entry",
                    ]).length})`}
                    items={relatedOfType(["consensus", "decision", "contradiction", "notebook_entry"])}
                    onSelect={selectNode}
                    nodeColor={nodeColor}
                  />
                )}

                {/* Quality decisions / review */}
                {relatedOfType(["quality_gate", "review_finding"]).length > 0 && (
                  <EvidenceSection
                    title={`Quality (${relatedOfType(["quality_gate", "review_finding"]).length})`}
                    items={relatedOfType(["quality_gate", "review_finding"])}
                    onSelect={selectNode}
                    nodeColor={nodeColor}
                  />
                )}

                {/* Historical runs */}
                {relatedOfType(["run", "agent"]).length > 0 && (
                  <EvidenceSection
                    title={`Runs & Agents (${relatedOfType(["run", "agent"]).length})`}
                    items={relatedOfType(["run", "agent"])}
                    onSelect={selectNode}
                    nodeColor={nodeColor}
                  />
                )}

                {/* Explain provenance (sanitized, evidence-only) */}
                {explain?.provenance &&
                  Object.keys(explain.provenance).filter(
                    (k) => !isForbiddenProvenanceKey(k)
                  ).length > 0 && (
                    <div>
                      <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400 mb-2">
                        Provenance
                      </div>
                      <div className="space-y-1">
                        {Object.entries(explain.provenance)
                          .filter(([k]) => !isForbiddenProvenanceKey(k))
                          .slice(0, 8)
                          .map(([k, v]) => (
                            <div key={k} className="flex items-start gap-2 text-xs font-mono">
                              <span className="text-primary-500 font-semibold shrink-0">{k}:</span>
                              <span className="text-slate-600 dark:text-slate-300 break-all">
                                {typeof v === "object" ? JSON.stringify(v) : String(v)}
                              </span>
                            </div>
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
                    <div className="space-y-1.5 max-h-40 overflow-y-auto">
                      {history.entries.slice(0, 20).map((h, i) => (
                        <div key={i} className="flex items-center gap-2 text-xs font-mono">
                          <span className="text-primary-500 font-semibold shrink-0 w-8">
                            v{h.graph_version}
                          </span>
                          <span className="text-slate-500 shrink-0">{h.status}</span>
                          <span className="text-slate-400 truncate">{fmtTime(h.created_at)}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}

            {!nodeLoading && !nodeError && !selectedId && (
              <div className="text-sm text-slate-400">
                Select a node in the graph (or from query results) to inspect
                provenance, evidence, history and relationships.
              </div>
            )}
          </SectionCard>

          {/* Relationship legend (§11) */}
          <SectionCard
            title="Relationship Legend"
            subtitle="Colors used for edges; filter from the toolbar"
          >
            {relOptions.length ? (
              <div className="flex flex-wrap gap-1.5">
                {relOptions.map((r) => (
                  <span key={r} className="inline-flex items-center gap-1.5 text-[10px] font-mono px-2 py-0.5 rounded-full bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300">
                    <span className="w-3 h-0.5 rounded" style={{ backgroundColor: relHex(r) }} />
                    {relLabel(r)}
                    <span className="text-slate-400">{relCounts[r]}</span>
                  </span>
                ))}
              </div>
            ) : (
              <div className="text-xs text-slate-400">
                No relationships in the current view — expand a node to populate the legend.
              </div>
            )}
          </SectionCard>
        </div>
      </div>

      {/* Timeline (§6) */}
      <SectionCard
        title="Graph Timeline & Version Comparison"
        subtitle="Inspect how the graph evolved across versions — added/removed nodes and changed relationships"
      >
        <div className="flex flex-wrap items-center gap-2 mb-3">
          <span className="text-xs text-slate-500">Compare</span>
          <select
            value={fromVersion ?? ""}
            onChange={(e) => setFromVersion(Number(e.target.value))}
            className="rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1.5 text-xs text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-primary-500"
          >
            {versions.map((v) => (
              <option key={v.version} value={v.version}>
                v{v.version}
              </option>
            ))}
          </select>
          <span className="text-xs text-slate-500">→</span>
          <select
            value={toVersion ?? ""}
            onChange={(e) => setToVersion(Number(e.target.value))}
            className="rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1.5 text-xs text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-primary-500"
          >
            {versions.map((v) => (
              <option key={v.version} value={v.version}>
                v{v.version}
              </option>
            ))}
          </select>
          <button
            onClick={() => void runDiff()}
            disabled={diffLoading || fromVersion == null || toVersion == null}
            className="px-3 py-1.5 rounded-lg bg-primary-600 hover:bg-primary-700 disabled:opacity-50 text-white text-xs font-medium transition-colors"
          >
            {diffLoading ? "…" : "Compare"}
          </button>
        </div>

        {diffError && (
          <div className="mb-3 text-sm text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 rounded-lg px-3 py-2">
            {diffError}
          </div>
        )}

        {diff && (
          <div className="mb-4">
            <div className="flex flex-wrap gap-2 mb-3">
              <span className="inline-flex items-center gap-1.5 text-xs font-semibold px-2.5 py-1 rounded-md bg-primary-50 dark:bg-primary-900/30 text-primary-700 dark:text-primary-300">
                {summarizeDiff(diff).label}
              </span>
              <span className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-md bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-300">
                +{summarizeDiff(diff).added} nodes
              </span>
              <span className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-md bg-rose-50 dark:bg-rose-900/30 text-rose-700 dark:text-rose-300">
                −{summarizeDiff(diff).removed} nodes
              </span>
              <span className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-md bg-amber-50 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300">
                {summarizeDiff(diff).changedEdges} edges changed
              </span>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
              <div>
                <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400 mb-2">
                  Added ({diff.added_nodes.length})
                </div>
                <div className="space-y-1 max-h-56 overflow-y-auto">
                  {diff.added_nodes.slice(0, 30).map((n) => (
                    <button
                      key={n.node_id}
                      onClick={() => selectNode(n.node_id)}
                      className="w-full text-left flex items-center gap-2 px-2 py-1 rounded-md hover:bg-green-50 dark:hover:bg-green-900/20 text-xs"
                    >
                      <span
                        className={`w-2 h-2 rounded-full shrink-0 ${nodeColor(n.node_type)}`}
                      />
                      <span className="truncate text-slate-600 dark:text-slate-300">
                        {n.name || n.node_id}
                      </span>
                      <span className="ml-auto shrink-0 text-[10px] font-mono text-slate-400">
                        {n.node_type}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
              <div>
                <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400 mb-2">
                  Removed ({diff.removed_nodes.length})
                </div>
                <div className="space-y-1 max-h-56 overflow-y-auto">
                  {diff.removed_nodes.slice(0, 30).map((n) => (
                    <div
                      key={n.node_id}
                      className="flex items-center gap-2 px-2 py-1 text-xs"
                    >
                      <span
                        className={`w-2 h-2 rounded-full shrink-0 ${nodeColor(n.node_type)}`}
                      />
                      <span className="truncate text-slate-500 line-through">
                        {n.name || n.node_id}
                      </span>
                      <span className="ml-auto shrink-0 text-[10px] font-mono text-slate-400">
                        {n.node_type}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
              <div>
                <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400 mb-2">
                  Per-version ({(diff.per_version || []).length})
                </div>
                <div className="space-y-1 max-h-56 overflow-y-auto">
                  {(diff.per_version || []).map((v) => (
                    <div key={v.version} className="px-2 py-1.5 rounded-md bg-slate-50 dark:bg-slate-900 text-xs">
                      <div className="flex items-center gap-2">
                        <span className="font-mono font-semibold text-primary-600 dark:text-primary-400">
                          v{v.version}
                        </span>
                        <span className="text-slate-400 font-mono truncate">
                          {v.run_id || "—"}
                        </span>
                      </div>
                      <div className="mt-0.5 text-slate-500 dark:text-slate-300 truncate" title={v.summary}>
                        {v.summary}
                      </div>
                      <div className="mt-0.5 font-mono text-[10px] text-slate-400">
                        +{v.added} · −{v.removed} · {v.changed_edges} edges
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        )}

        {versions.length === 0 ? (
          <div className="text-sm text-slate-400">
            No version increments yet — run an orchestration to enrich the graph.
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
                    className="border-b border-slate-100 dark:border-slate-700/50 hover:bg-slate-50 dark:hover:bg-slate-700/40"
                  >
                    <td className="py-2 pr-4 font-mono text-primary-600 dark:text-primary-400 font-semibold">
                      v{v.version}
                    </td>
                    <td className="py-2 pr-4 font-mono text-slate-500">
                      {truncate(v.run_id || "—", 34)}
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
                    <td className="py-2 text-slate-500">{fmtTime(v.timestamp)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </SectionCard>

      {/* Node distribution + category legend */}
      {stats && Object.keys(stats.node_types).length > 0 && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <SectionCard title="Node Distribution" subtitle="Across the whole graph (v{stats.version})">
            <div className="flex flex-wrap gap-2">
              {Object.entries(stats.node_types)
                .sort((a, b) => b[1] - a[1])
                .map(([t, c]) => (
                  <span
                    key={t}
                    className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300"
                  >
                    <span className={`w-2 h-2 rounded-full ${nodeColor(t)}`} />
                    {nodeTypeLabel(t)} · {c}
                  </span>
                ))}
            </div>
          </SectionCard>
          <SectionCard
            title="Node Type Legend"
            subtitle="Colors used for nodes, grouped by category"
          >
            <div className="space-y-3 max-h-80 overflow-y-auto pr-1">
              {Object.entries(categoryTypes).map(([cat, types]) => (
                <div key={cat}>
                  <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-400 mb-1.5">
                    {cat}
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {types.map((t) => (
                      <span
                        key={t}
                        className="inline-flex items-center gap-1.5 text-[11px] px-2 py-0.5 rounded-md bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300"
                      >
                        <span
                          className="w-2 h-2 rounded-full"
                          style={{ backgroundColor: hexFor(t) }}
                        />
                        {nodeTypeLabel(t)}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </SectionCard>
        </div>
      )}
    </div>
  );
}

// ── Evidence section (§7) ──────────────────────────────────────

function EvidenceSection({
  title,
  items,
  onSelect,
  nodeColor,
}: {
  title: string;
  items: RelatedEvidence[];
  onSelect: (id: string) => void;
  nodeColor: (t: string) => string;
}) {
  return (
    <div>
      <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400 mb-2">
        {title}
      </div>
      <div className="space-y-1 max-h-48 overflow-y-auto">
        {items.slice(0, 15).map((r) => (
          <button
            key={r.edge_id}
            onClick={() => onSelect(r.node_id)}
            className="w-full text-left flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-slate-50 dark:hover:bg-slate-700/60 text-xs"
          >
            <span className={`w-2 h-2 rounded-full shrink-0 ${nodeColor(r.node_type)}`} />
            <span className="text-slate-600 dark:text-slate-300 truncate">
              {r.name || r.node_id}
            </span>
            <span className="ml-auto shrink-0">
              <RelRow rel={r.relationship} />
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
