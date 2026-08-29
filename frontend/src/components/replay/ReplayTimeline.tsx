"use client";

/**
 * Phase 21 — stage-by-stage replay timeline.
 *
 * Renders the replay stage views (deterministic / LLM-proposed /
 * observational classification, comparison status, artifact fingerprints).
 * Never exposes chain-of-thought — only recorded evidence + verdicts.
 */

import {
  replayStageViews,
  stageKindLabel,
  stageKindTone,
  verdictTone,
} from "@/lib/replay/replayModel";
import type {
  ReplayManifest,
  ReplayResult,
  ReplayStageRecord,
} from "@/lib/api/client";

function stageLabel(stage: string): string {
  return stage.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export default function ReplayTimeline({
  manifest,
  result,
}: {
  manifest: ReplayManifest | null;
  result: ReplayResult | null;
}) {
  const stages: ReplayStageRecord[] = manifest?.stages || [];
  const views = replayStageViews(
    stages,
    result?.stage_comparisons || []
  );

  if (views.length === 0) {
    return (
      <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-5">
        <h3 className="text-sm font-semibold text-slate-900 dark:text-white mb-3">
          Replay Timeline
        </h3>
        <p className="text-xs text-slate-400 italic">
          No stage evidence recorded for this run yet.
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 overflow-hidden">
      <div className="px-5 py-3.5 border-b border-slate-200 dark:border-slate-700">
        <h3 className="text-sm font-semibold text-slate-900 dark:text-white">
          Replay Timeline
        </h3>
        <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5">
          Stage-by-stage replay result — deterministic stages are re-executed;
          LLM proposals are recorded, never replayed
        </p>
      </div>

      <div className="px-5 py-3 space-y-1">
        {views.map((v) => {
          const tone = verdictTone(
            v.matched === null || v.matched === undefined
              ? undefined
              : v.matched
                ? "match"
                : "drift"
          );
          const kind = stageKindTone(v.kind);
          return (
            <div
              key={v.stage}
              className="flex items-center gap-3 py-2 border-b border-slate-100 dark:border-slate-700/50 last:border-0"
            >
              {/* Comparison status */}
              <div className="w-20 shrink-0">
                {v.matched === null || v.matched === undefined ? (
                  <span className="text-[10px] font-bold text-slate-400">
                    {v.kind === "deterministic" ? "REPLAYED" : "RECORDED"}
                  </span>
                ) : (
                  <span
                    className={`inline-flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded-full border ${tone.badge}`}
                  >
                    <span className={`w-1.5 h-1.5 rounded-full ${tone.dot}`} />
                    {tone.label}
                  </span>
                )}
              </div>

              {/* Stage name + classification */}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-slate-800 dark:text-slate-200">
                    {stageLabel(v.stage)}
                  </span>
                  <span
                    className={`text-[9px] font-semibold px-1.5 py-0.5 rounded ${kind.classes}`}
                  >
                    {kind.label}
                  </span>
                  <span className="text-[9px] text-slate-400 uppercase tracking-wide">
                    {stageKindLabel(v.kind)}
                  </span>
                </div>
                {v.detail && (
                  <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5 truncate">
                    {v.detail}
                  </p>
                )}
              </div>

              {/* Artifact fingerprints */}
              <div className="hidden md:flex items-center gap-2 shrink-0 font-mono text-[10px] text-slate-400">
                {v.recordedHash && (
                  <span title={`recorded ${v.recordedHash}`}>
                    rec {v.recordedHash.slice(0, 10)}…
                  </span>
                )}
                {v.replayHash && (
                  <span title={`replay ${v.replayHash}`}>
                    rep {v.replayHash.slice(0, 10)}…
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
