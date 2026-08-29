"use client";

/**
 * Phase 21 — enterprise audit report.
 *
 * Renders the backend `audit()` payload (manifest + EXACT replay + verdict)
 * as a concise enterprise summary: run/repository identity, replay mode,
 * verdict, stage summary, deterministic checks, differences and evidence
 * references. Never exposes chain-of-thought.
 */

import { useState } from "react";
import {
  checkStatusTone,
  formatTimestamp,
  verdictTone,
} from "@/lib/replay/replayModel";
import type { ReplayAudit, ReplayCheck } from "@/lib/api/client";

function stageLabel(stage: string): string {
  return stage.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export default function AuditReport({ audit }: { audit: ReplayAudit | null }) {
  const [showChecks, setShowChecks] = useState(false);

  if (!audit || !audit.available) {
    return (
      <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-5">
        <h3 className="text-sm font-semibold text-slate-900 dark:text-white mb-1">
          Audit Report
        </h3>
        <p className="text-xs text-slate-400 italic">
          {audit?.error || "Audit unavailable for this run."}
        </p>
      </div>
    );
  }

  const verdict = audit.verdict || "unknown";
  const tone = verdictTone(verdict);
  const manifest = audit.manifest as Record<string, unknown> | undefined;
  const checks: ReplayCheck[] = audit.checks || [];
  const divergences = audit.divergences || [];
  const replay = (audit.replay || {}) as Record<string, unknown>;
  const stages = audit.stages || [];
  const failedChecks = checks.filter((c) => c.status === "failed");
  const primaryDifference =
    failedChecks.length > 0
      ? failedChecks[0].check.toUpperCase().replace(/_/g, "_")
      : divergences[0]?.toUpperCase() || null;

  const isMatch = verdict === "match";
  const isDrift = verdict === "drift";

  return (
    <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 overflow-hidden">
      {/* Enterprise summary banner */}
      <div className={`border-b-2 ${tone.banner} px-5 py-4`}>
        <div className="flex items-center gap-3">
          <span className={`w-3 h-3 rounded-full ${tone.dot}`} />
          <h3 className="text-base font-bold tracking-wide">
            {isMatch
              ? "AUDIT RESULT"
              : isDrift
                ? "DRIFT DETECTED"
                : "AUDIT RESULT"}
          </h3>
          <span
            className={`ml-auto inline-flex items-center gap-1 text-xs font-bold px-2.5 py-1 rounded-md border ${tone.badge}`}
          >
            {tone.label}
          </span>
        </div>

        <div className="mt-3 grid grid-cols-2 md:grid-cols-4 gap-2 text-[11px]">
          <div>
            <span className="text-slate-400 uppercase tracking-wide text-[9px] font-semibold block">
              Deterministic checks
            </span>
            <span className="text-slate-800 dark:text-slate-200 font-semibold">
              {checks.length}
            </span>
          </div>
          <div>
            <span className="text-slate-400 uppercase tracking-wide text-[9px] font-semibold block">
              Differences
            </span>
            <span className="text-slate-800 dark:text-slate-200 font-semibold">
              {divergences.length}
            </span>
          </div>
          <div>
            <span className="text-slate-400 uppercase tracking-wide text-[9px] font-semibold block">
              Stages
            </span>
            <span className="text-slate-800 dark:text-slate-200 font-semibold">
              {stages.length}
            </span>
          </div>
          <div>
            <span className="text-slate-400 uppercase tracking-wide text-[9px] font-semibold block">
              Replay mode
            </span>
            <span className="text-slate-800 dark:text-slate-200 font-semibold uppercase">
              {String(replay.mode || "exact")}
            </span>
          </div>
        </div>

        {isDrift && primaryDifference && (
          <div className="mt-3 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800/40 px-3 py-2">
            <p className="text-[10px] text-red-500 dark:text-red-400 font-semibold uppercase tracking-wide">
              Primary difference
            </p>
            <p className="text-xs font-mono text-red-800 dark:text-red-300 mt-0.5">
              {primaryDifference}
            </p>
            {failedChecks[0]?.actual && (
              <p className="text-[10px] text-red-600 dark:text-red-400 mt-0.5 font-mono">
                Supporting evidence: {failedChecks[0].actual.slice(0, 160)}
              </p>
            )}
          </div>
        )}
      </div>

      <div className="px-5 py-4 space-y-4">
        {/* Identity */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-1.5 text-[11px]">
          <div className="flex justify-between gap-3">
            <span className="text-slate-400">Run</span>
            <span className="font-mono text-slate-800 dark:text-slate-200">
              {audit.run_id}
            </span>
          </div>
          <div className="flex justify-between gap-3">
            <span className="text-slate-400">Manifest</span>
            <span className="font-mono text-slate-800 dark:text-slate-200">
              {String(manifest?.manifest_id || "—")}
            </span>
          </div>
          <div className="flex justify-between gap-3">
            <span className="text-slate-400">Repository</span>
            <span className="font-mono text-slate-800 dark:text-slate-200 truncate max-w-[60%]">
              {String(
                (manifest?.repository_state as Record<string, unknown> | undefined)
                  ?.path || "—"
              )}
            </span>
          </div>
          <div className="flex justify-between gap-3">
            <span className="text-slate-400">Repository fingerprint</span>
            <span className="font-mono text-slate-800 dark:text-slate-200">
              {String(
                (manifest?.repository_state as Record<string, unknown> | undefined)
                  ?.fingerprint || "—"
              )}
            </span>
          </div>
          <div className="flex justify-between gap-3">
            <span className="text-slate-400">Run status</span>
            <span className="capitalize text-slate-800 dark:text-slate-200">
              {String(manifest?.source_run_status || "—").replace(/_/g, " ")}
            </span>
          </div>
          <div className="flex justify-between gap-3">
            <span className="text-slate-400">Audited at</span>
            <span className="text-slate-800 dark:text-slate-200">
              {formatTimestamp(String(replay.created_at || ""))}
            </span>
          </div>
        </div>

        {/* Stage summary */}
        <div>
          <p className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-1.5">
            Stage Summary
          </p>
          <div className="flex flex-wrap gap-1.5">
            {stages.map((s) => {
              const record = s as unknown as {
                stage: string;
                kind?: string;
                status?: string;
              };
              return (
                <span
                  key={record.stage}
                  className="inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-md bg-slate-100 dark:bg-slate-700/60 text-slate-600 dark:text-slate-300"
                  title={`${record.kind || "unknown"} · ${record.status || ""}`}
                >
                  {stageLabel(record.stage)}
                  {record.kind === "deterministic" && (
                    <span className="text-blue-500 font-bold">•</span>
                  )}
                </span>
              );
            })}
          </div>
        </div>

        {/* Checks */}
        <div>
          <button
            onClick={() => setShowChecks(!showChecks)}
            className="flex items-center justify-between w-full text-left group"
          >
            <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider">
              Deterministic Checks ({checks.length})
            </span>
            <svg
              className={`w-3.5 h-3.5 text-slate-400 transition-transform duration-200 ${showChecks ? "rotate-180" : ""}`}
              fill="none"
              viewBox="0 0 24 24"
              strokeWidth={2}
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M19.5 8.25l-7.5 7.5-7.5-7.5"
              />
            </svg>
          </button>
          {showChecks && (
            <div className="mt-2 space-y-1 max-h-72 overflow-y-auto">
              {checks.length === 0 && (
                <p className="text-[11px] text-slate-400 italic">
                  No checks executed.
                </p>
              )}
              {checks.map((c, i) => {
                const tone = checkStatusTone(c.status);
                return (
                  <div
                    key={`${c.check}-${i}`}
                    className="flex items-start gap-2 py-1.5 border-b border-slate-100 dark:border-slate-700/50 last:border-0"
                  >
                    <span
                      className={`text-[9px] font-bold px-1.5 py-0.5 rounded shrink-0 mt-0.5 ${tone.classes}`}
                    >
                      {tone.label}
                    </span>
                    <div className="min-w-0">
                      <p className="text-[11px] font-medium text-slate-700 dark:text-slate-300">
                        {c.check.replace(/_/g, " ")}
                        {c.stage && (
                          <span className="text-slate-400"> · {stageLabel(c.stage)}</span>
                        )}
                      </p>
                      {(c.expected || c.actual) && (
                        <p className="text-[10px] text-slate-500 dark:text-slate-400 font-mono break-all">
                          {c.expected ? `expected: ${c.expected.slice(0, 120)}` : ""}
                          {c.expected && c.actual ? " | " : ""}
                          {c.actual ? `actual: ${c.actual.slice(0, 120)}` : ""}
                        </p>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
