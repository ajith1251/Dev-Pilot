"use client";

/**
 * Phase 20A6 — Organization Summary.
 *
 * Shown at run completion: participating repositories, execution duration,
 * successful / failed / repaired repositories, engineering decisions,
 * consensus summary, and organization-level quality status. Reuses existing
 * APIs — the server derives this from the run's deterministic evidence.
 */

import { normalizeSummary } from "@/lib/graph/repositoryStatusModel";
import type { OrganizationSummary as OrgSummaryType } from "@/lib/api/client";

function StatTile({
  label,
  value,
  tone,
}: {
  label: string;
  value: string | number;
  tone?: "success" | "danger" | "active" | "muted";
}) {
  const colors: Record<string, string> = {
    success: "text-emerald-600 dark:text-emerald-400",
    danger: "text-red-600 dark:text-red-400",
    active: "text-blue-600 dark:text-blue-400",
    muted: "text-slate-900 dark:text-white",
  };
  return (
    <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-3">
      <p className="text-[9px] font-semibold text-slate-400 uppercase tracking-wider">{label}</p>
      <p className={`mt-1 text-xl font-bold ${colors[tone ?? "muted"]}`}>{value}</p>
    </div>
  );
}

export default function OrganizationSummary({
  summary,
}: {
  summary?: OrgSummaryType | null;
}) {
  const s = normalizeSummary(summary);
  if (!s) return null;

  const isTerminal = ["approved", "rejected", "failed", "cancelled", "needs_human_review"].includes(
    s.quality_status
  );

  return (
    <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 overflow-hidden">
      <div className="px-5 py-3.5 border-b border-slate-200 dark:border-slate-700 flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-slate-900 dark:text-white">
            Organization Summary
          </h3>
          <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5">
            Cross-repository execution outcome
          </p>
        </div>
        {isTerminal && (
          <span
            className={`text-[10px] font-bold uppercase tracking-wide px-2.5 py-1 rounded-full border ${
              s.quality_status === "approved"
                ? "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-400 border-emerald-200 dark:border-emerald-800"
                : s.quality_status === "needs_human_review"
                  ? "bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400 border-amber-200 dark:border-amber-800"
                  : "bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-400 border-red-200 dark:border-red-800"
            }`}
          >
            {s.quality_status.replace(/_/g, " ")}
          </span>
        )}
      </div>

      <div className="px-5 py-4 space-y-4">
        {/* Counts */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          <StatTile label="Repositories" value={s.repository_count} />
          <StatTile label="Successful" value={s.successful_repositories.length} tone="success" />
          <StatTile label="Failed" value={s.failed_repositories.length} tone="danger" />
          <StatTile
            label="Repaired"
            value={s.repaired_repositories.length}
            tone={s.repaired_repositories.length > 0 ? "active" : "muted"}
          />
        </div>

        {/* Participating repositories */}
        {s.participating_repositories.length > 0 && (
          <div>
            <p className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-1.5">
              Participating Repositories
            </p>
            <div className="flex flex-wrap gap-1.5">
              {s.participating_repositories.map((p) => (
                <span
                  key={p.repository_id}
                  className={`inline-flex items-center gap-1.5 px-2 py-1 rounded-md text-[11px] font-mono border ${
                    p.status === "ok"
                      ? "bg-emerald-50 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-400 border-emerald-200 dark:border-emerald-800/60"
                      : "bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400 border-red-200 dark:border-red-800/60"
                  }`}
                >
                  {p.is_primary && <span className="text-[9px] uppercase">★</span>}
                  {p.repository_id}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Duration + decisions + consensus */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs">
          <div className="rounded-lg border border-slate-200 dark:border-slate-700 p-3">
            <p className="text-[9px] font-semibold text-slate-400 uppercase tracking-wider">Duration</p>
            <p className="mt-1 text-sm font-semibold text-slate-900 dark:text-white">
              {s.duration_seconds != null ? `${s.duration_seconds.toFixed(1)}s` : "—"}
            </p>
          </div>
          <div className="rounded-lg border border-slate-200 dark:border-slate-700 p-3">
            <p className="text-[9px] font-semibold text-slate-400 uppercase tracking-wider">Engineering Decisions</p>
            <p className="mt-1 text-sm font-semibold text-slate-900 dark:text-white">
              {s.engineering_decisions.count}
            </p>
            {s.engineering_decisions.recent.slice(0, 2).map((d, i) => (
              <p key={i} className="mt-1 text-[10px] text-slate-500 dark:text-slate-400 truncate" title={d}>
                {d}
              </p>
            ))}
          </div>
          <div className="rounded-lg border border-slate-200 dark:border-slate-700 p-3">
            <p className="text-[9px] font-semibold text-slate-400 uppercase tracking-wider">Consensus</p>
            <p className="mt-1 text-sm font-semibold text-slate-900 dark:text-white">
              {s.consensus_summary.count}
              <span className="ml-2 text-[10px] font-normal text-slate-400">
                {s.consensus_summary.contradictions} contradictions
              </span>
            </p>
            {s.consensus_summary.recent.slice(0, 2).map((c, i) => (
              <p key={i} className="mt-1 text-[10px] text-slate-500 dark:text-slate-400 truncate" title={c}>
                {c}
              </p>
            ))}
          </div>
        </div>

        {/* Org graph stats */}
        {s.graph && (
          <div className="text-[11px] text-slate-500 dark:text-slate-400">
            Org graph: <strong className="text-slate-900 dark:text-white">{s.graph.node_count}</strong> nodes ·{" "}
            <strong className="text-slate-900 dark:text-white">{s.graph.edge_count}</strong> edges ·{" "}
            <strong className="text-slate-900 dark:text-white">{s.graph.cross_edge_count}</strong> cross-repo edges · v{s.graph.version}
          </div>
        )}
      </div>
    </div>
  );
}
