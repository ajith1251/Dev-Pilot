"use client";

/**
 * Phase 21 — bounded difference viewer.
 *
 * For every replay difference shows: category, stage, original vs replay
 * value/fingerprint, severity and the deterministic supporting evidence.
 * No causality is claimed beyond what the backend recorded.
 */

import { differencesFromResult } from "@/lib/replay/replayModel";
import type { ReplayManifest, ReplayResult } from "@/lib/api/client";

function severityClasses(severity: string): string {
  switch (severity) {
    case "high":
      return "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400 border-red-200 dark:border-red-800/60";
    case "medium":
      return "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400 border-amber-200 dark:border-amber-800/60";
    default:
      return "bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-400 border-slate-200 dark:border-slate-700/60";
  }
}

function severityLabel(severity: string): string {
  switch (severity) {
    case "high":
      return "HIGH";
    case "medium":
      return "MEDIUM";
    default:
      return "LOW";
  }
}

function stageLabel(stage: string): string {
  if (!stage) return "manifest";
  return stage.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export default function DifferenceViewer({
  manifest,
  result,
}: {
  manifest: ReplayManifest | null;
  result: ReplayResult | null;
}) {
  const diffs = differencesFromResult(manifest, result);

  if (diffs.length === 0) {
    return (
      <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-5">
        <h3 className="text-sm font-semibold text-slate-900 dark:text-white mb-1">
          Differences
        </h3>
        <p className="text-xs text-slate-400 italic">
          No differences — every replayed stage produced an identical result.
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 overflow-hidden">
      <div className="px-5 py-3.5 border-b border-slate-200 dark:border-slate-700 flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-slate-900 dark:text-white">
            Differences
          </h3>
          <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5">
            {diffs.length} difference{diffs.length === 1 ? "" : "s"} identified
            from deterministic evidence
          </p>
        </div>
        {diffs.length >= 50 && (
          <span className="text-[10px] text-slate-400">
            showing first 50
          </span>
        )}
      </div>

      <div className="divide-y divide-slate-100 dark:divide-slate-700/50">
        {diffs.map((d) => (
          <div key={d.id} className="px-5 py-3">
            <div className="flex items-center gap-2 flex-wrap">
              <span
                className={`text-[9px] font-bold px-1.5 py-0.5 rounded border ${severityClasses(d.severity)}`}
              >
                {severityLabel(d.severity)}
              </span>
              <span className="text-[11px] font-semibold text-slate-700 dark:text-slate-300">
                {d.category}
              </span>
              <span className="text-[11px] text-slate-400">
                · {stageLabel(d.stage)}
              </span>
            </div>

            <div className="mt-1.5 grid grid-cols-1 md:grid-cols-2 gap-2 text-[11px]">
              <div className="rounded-md bg-slate-50 dark:bg-slate-900/40 border border-slate-200 dark:border-slate-700/50 px-2.5 py-1.5">
                <span className="text-slate-400 uppercase tracking-wide text-[9px] font-semibold">
                  Original
                </span>
                <p className="font-mono text-slate-600 dark:text-slate-300 mt-0.5 break-all">
                  {d.original || "—"}
                </p>
              </div>
              <div className="rounded-md bg-red-50 dark:bg-red-900/10 border border-red-200 dark:border-red-800/40 px-2.5 py-1.5">
                <span className="text-red-400 uppercase tracking-wide text-[9px] font-semibold">
                  Replay
                </span>
                <p className="font-mono text-red-700 dark:text-red-300 mt-0.5 break-all">
                  {d.replay || "—"}
                </p>
              </div>
            </div>

            {d.evidence.length > 0 && (
              <ul className="mt-1.5 space-y-0.5">
                {d.evidence.slice(0, 3).map((e, i) => (
                  <li
                    key={i}
                    className="text-[10px] text-slate-500 dark:text-slate-400 font-mono break-all"
                  >
                    • {e}
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
