"use client";

/**
 * Phase 20A6 — Repository Status Cards.
 *
 * One card per participating repository: current stage, execution progress,
 * validation status, Engineering Knowledge Graph status, repository memory
 * updates, and quality gate result. Updates live via the existing WebSocket
 * infrastructure (the server re-broadcasts the repository view on every
 * stage transition). Each card links into the EKG surfaces (repository
 * graph, organization graph, engineering history, notebook, consensus,
 * repository memory).
 */

import Link from "next/link";
import { StageStrip } from "@/components/runs/RepositoryTimeline";
import {
  cardStatus,
  computeRepoProgress,
  repositoryLinks,
  sourceTypeLabel,
} from "@/lib/graph/repositoryStatusModel";
import type { RepositoryStatus } from "@/lib/api/client";

function ProgressBar({ pct }: { pct: number }) {
  return (
    <div className="h-1.5 rounded-full bg-slate-100 dark:bg-slate-700 overflow-hidden">
      <div
        className="h-full rounded-full bg-primary-500 transition-all duration-500"
        style={{ width: `${Math.round(pct * 100)}%` }}
      />
    </div>
  );
}

function NavLink({
  href,
  children,
}: {
  href: string;
  children: React.ReactNode;
}) {
  return (
    <Link
      href={href}
      className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-[10px] font-medium text-slate-600 dark:text-slate-400 bg-slate-50 dark:bg-slate-700/40 border border-slate-200 dark:border-slate-700 hover:text-primary-600 dark:hover:text-primary-400 hover:border-primary-300 dark:hover:border-primary-700 transition-all"
    >
      {children}
    </Link>
  );
}

function RepositoryCard({
  repo,
  runId,
}: {
  repo: RepositoryStatus;
  runId: string;
}) {
  const pct = computeRepoProgress(repo);
  const status = cardStatus(repo);
  const links = repositoryLinks(repo, runId);

  const toneClasses: Record<string, string> = {
    success: "text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-900/20 border-emerald-200 dark:border-emerald-800/60",
    danger: "text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800/60",
    active: "text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/20 border-blue-200 dark:border-blue-800/60",
    muted: "text-slate-500 dark:text-slate-400 bg-slate-50 dark:bg-slate-700/30 border-slate-200 dark:border-slate-700",
  };

  const graph = repo.graph;
  const ok = repo.validation_status === "validated";

  return (
    <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-4 transition-all duration-200 hover:shadow-md">
      {/* Header */}
      <div className="flex items-start justify-between gap-2 mb-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-mono text-xs font-semibold text-slate-900 dark:text-white truncate">
              {repo.repository_id}
            </span>
            {repo.is_primary ? (
              <span className="text-[9px] uppercase tracking-wide font-bold px-1.5 py-0.5 rounded bg-primary-100 dark:bg-primary-900/30 text-primary-700 dark:text-primary-300">
                primary
              </span>
            ) : (
              <span className="text-[9px] uppercase tracking-wide font-bold px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-700 text-slate-500 dark:text-slate-400">
                aux
              </span>
            )}
          </div>
          <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5 truncate">
            {repo.name}
            {repo.organization !== "default" ? ` · ${repo.organization}` : ""} ·{" "}
            {sourceTypeLabel(repo.source_type)}
          </p>
        </div>
        <span className={`shrink-0 text-[10px] font-semibold uppercase tracking-wide px-2 py-0.5 rounded-full border ${toneClasses[status.tone]}`}>
          {status.label}
        </span>
      </div>

      {/* Current stage + progress */}
      <div className="mb-3">
        <div className="flex items-center justify-between text-[11px] mb-1">
          <span className="text-slate-500 dark:text-slate-400 capitalize">
            {repo.current_stage.replace(/_/g, " ")}
          </span>
          <span className="text-slate-400">{Math.round(pct * 100)}%</span>
        </div>
        <ProgressBar pct={pct} />
      </div>

      {/* Timeline strip */}
      <div className="mb-3">
        <StageStrip progress={repo.progress} />
      </div>

      {/* Validation + quality gate */}
      <div className="grid grid-cols-2 gap-2 text-[11px] mb-3">
        <div className="rounded-lg border border-slate-200 dark:border-slate-700 p-2">
          <p className="text-[9px] font-semibold text-slate-400 uppercase tracking-wider">Validation</p>
          <p className={`mt-0.5 font-medium capitalize ${ok ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}`}>
            {repo.validation_status}
          </p>
          <p className="text-[10px] text-slate-400">
            {repo.changed_files?.length ?? 0} file{(repo.changed_files?.length ?? 0) === 1 ? "" : "s"} changed
          </p>
        </div>
        <div className="rounded-lg border border-slate-200 dark:border-slate-700 p-2">
          <p className="text-[9px] font-semibold text-slate-400 uppercase tracking-wider">Quality Gate</p>
          <p className={`mt-0.5 font-medium capitalize ${
            repo.quality_gate_result === "approved"
              ? "text-emerald-600 dark:text-emerald-400"
              : repo.quality_gate_result === "rejected" || repo.quality_gate_result === "failed"
                ? "text-red-600 dark:text-red-400"
                : "text-slate-600 dark:text-slate-300"
          }`}>
            {repo.quality_gate_result || repo.quality_gate || "—"}
          </p>
          <p className="text-[10px] text-slate-400">apply: {repo.application_status}</p>
        </div>
      </div>

      {/* EKG status */}
      <div className="rounded-lg bg-slate-50 dark:bg-slate-700/30 border border-slate-200 dark:border-slate-700 p-2 mb-3">
        <div className="flex items-center justify-between">
          <p className="text-[9px] font-semibold text-slate-400 uppercase tracking-wider">
            Engineering Knowledge Graph
          </p>
          <span className="text-[10px] text-slate-500 dark:text-slate-400">
            {graph?.available
              ? `${graph.node_count ?? 0} nodes · ${graph.edge_count ?? 0} edges · ${graph.run_count ?? 0} runs`
              : "unavailable"}
          </span>
        </div>
        {(graph?.outgoing_links?.length ?? 0) > 0 && (
          <p className="mt-1 text-[10px] text-slate-500 dark:text-slate-400 truncate">
            depends on: {graph.outgoing_links!.map((l) => `${l.repository_id} [${l.relationship}]`).join(", ")}
          </p>
        )}
        {(graph?.incoming_links?.length ?? 0) > 0 && (
          <p className="text-[10px] text-slate-500 dark:text-slate-400 truncate">
            depended by: {graph.incoming_links!.map((l) => l.repository_id).join(", ")}
          </p>
        )}
      </div>

      {/* Navigation */}
      <div className="flex flex-wrap gap-1.5">
        <NavLink href={links.repositoryGraph}>Repo graph</NavLink>
        <NavLink href={links.organizationGraph}>Org graph</NavLink>
        <NavLink href={links.engineeringHistory}>History</NavLink>
        <NavLink href={links.notebook}>Notebook</NavLink>
        <NavLink href={links.consensus}>Consensus</NavLink>
        <NavLink href={links.repositoryMemory}>Memory</NavLink>
      </div>

      {repo.validation_errors?.length > 0 && (
        <p className="mt-2 text-[10px] text-red-600 dark:text-red-400">
          {repo.validation_errors[0]}
        </p>
      )}
    </div>
  );
}

export default function RepositoryStatusCards({
  repositories,
  runId,
}: {
  repositories: RepositoryStatus[];
  runId: string;
}) {
  if (!repositories || repositories.length === 0) return null;

  const primary = repositories.find((r) => r.is_primary);
  const aux = repositories.filter((r) => !r.is_primary);

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-slate-900 dark:text-white">
          Repository Status
        </h3>
        <span className="text-[11px] text-slate-400">
          {repositories.length} participating
        </span>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        {primary && <RepositoryCard key={primary.repository_id} repo={primary} runId={runId} />}
        {aux.map((r) => (
          <RepositoryCard key={r.repository_id} repo={r} runId={runId} />
        ))}
      </div>
    </div>
  );
}
