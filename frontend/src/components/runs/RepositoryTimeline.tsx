"use client";

/**
 * Phase 20A6 — Cross-Repository Execution Timeline.
 *
 * Shows the six pipeline stages (planning → coding → testing → repair →
 * review → quality gate) per repository, derived from the server's
 * repository status payload. Used both inside repository status cards and
 * as a compact grouped strip.
 */

import {
  REPOSITORY_STAGES,
  normalizeStageStatus,
  type RepositoryStage,
  type StageStatus,
} from "@/lib/graph/repositoryStatusModel";
import type { RepositoryStatus } from "@/lib/api/client";

function stageIcon(st: StageStatus): [string, string] {
  switch (st) {
    case "succeeded": return ["✓", "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-400 border-emerald-200 dark:border-emerald-800"];
    case "failed": return ["✗", "bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400 border-red-200 dark:border-red-800"];
    case "running": return ["●", "bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 border-blue-200 dark:border-blue-800 animate-pulse"];
    case "skipped": return ["○", "bg-slate-100 dark:bg-slate-700/50 text-slate-400 border-slate-200 dark:border-slate-700"];
    case "cancelled": return ["—", "bg-slate-100 dark:bg-slate-700/50 text-slate-400 border-slate-200 dark:border-slate-700"];
    default: return ["·", "bg-slate-50 dark:bg-slate-800 text-slate-300 dark:text-slate-600 border-slate-200 dark:border-slate-700"];
  }
}

const STAGE_LABEL: Record<RepositoryStage, string> = {
  planning: "Planning",
  coding: "Coding",
  testing: "Testing",
  repair: "Repair",
  review: "Review",
  quality_gate: "Quality Gate",
};

export function StageStrip({ progress }: { progress: RepositoryStatus["progress"] }) {
  return (
    <div className="flex items-center gap-1">
      {REPOSITORY_STAGES.map((stage, i) => {
        const st = normalizeStageStatus(progress?.[stage]);
        const [icon, iconClasses] = stageIcon(st);
        return (
          <div key={stage} className="flex items-center gap-1" title={`${STAGE_LABEL[stage]}: ${st}`}>
            <span
              className={`w-5 h-5 rounded-full border flex items-center justify-center text-[9px] font-bold transition-all duration-300 ${iconClasses}`}
            >
              {icon}
            </span>
            {i < REPOSITORY_STAGES.length - 1 && (
              <span className={`w-1.5 h-0.5 rounded ${
                st === "succeeded"
                  ? "bg-emerald-300 dark:bg-emerald-700"
                  : st === "failed"
                    ? "bg-red-300 dark:bg-red-700"
                    : "bg-slate-200 dark:bg-slate-700"
              }`} />
            )}
          </div>
        );
      })}
    </div>
  );
}

export default function RepositoryTimeline({
  repositories,
}: {
  repositories: RepositoryStatus[];
}) {
  return (
    <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 overflow-hidden">
      <div className="px-5 py-3.5 border-b border-slate-200 dark:border-slate-700">
        <h3 className="text-sm font-semibold text-slate-900 dark:text-white">
          Cross-Repository Execution Timeline
        </h3>
        <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5">
          Per-repository progress across the six pipeline stages — planning · coding · testing · repair · review · quality gate
        </p>
      </div>
      <div className="px-5 py-3 space-y-3">
        {repositories.map((repo) => (
          <div
            key={repo.repository_id}
            className="flex items-center justify-between gap-4 py-1.5"
          >
            <div className="flex items-center gap-2 min-w-0 w-48 shrink-0">
              {repo.is_primary ? (
                <span className="text-[9px] uppercase tracking-wide font-bold px-1.5 py-0.5 rounded bg-primary-100 dark:bg-primary-900/30 text-primary-700 dark:text-primary-300">
                  primary
                </span>
              ) : (
                <span className="text-[9px] uppercase tracking-wide font-bold px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-700 text-slate-500 dark:text-slate-400">
                  aux
                </span>
              )}
              <span className="font-mono text-xs text-slate-900 dark:text-white truncate">
                {repo.repository_id}
              </span>
            </div>
            <div className="flex-1 min-w-0 overflow-x-auto">
              <StageStrip progress={repo.progress} />
            </div>
            <div className="w-24 text-right shrink-0 text-[11px] text-slate-500 dark:text-slate-400 capitalize">
              {repo.current_stage.replace(/_/g, " ")}
            </div>
          </div>
        ))}
        {repositories.length === 0 && (
          <p className="text-xs text-slate-400 py-2">No repository data available.</p>
        )}
      </div>
    </div>
  );
}
