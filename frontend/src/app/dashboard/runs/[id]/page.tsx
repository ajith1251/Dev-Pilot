"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { runsApi } from "@/lib/api/client";
import { useRunWebSocket } from "@/lib/hooks/useRunWebSocket";
import RepositoryStatusCards from "@/components/runs/RepositoryStatusCards";
import RepositoryTimeline from "@/components/runs/RepositoryTimeline";
import OrganizationSummary from "@/components/runs/OrganizationSummary";
import RunHistoryPanel from "@/components/runs/RunHistoryPanel";
import { isValidRepository } from "@/lib/graph/repositoryStatusModel";
import type {
  RunStatus,
  StageType,
  StageResult,
  RunEvent,
  RepositoryPatchValidation,
  RepositoryStatus,
  OrganizationSummary as OrganizationSummaryType,
} from "@/lib/api/client";

// Reusable failure type matching the API client's RunDetail
interface FailureInfo {
  stage: string;
  code: string;
  message: string;
}

// ── Helpers ────────────────────────────────────────────────────

const STAGE_ORDER: StageType[] = [
  "initializing", "acquiring_repository", "analyzing_repository",
  "analyzing_task", "planning", "retrieving_context", "coding",
  "validating_patch", "applying_patch", "testing", "repairing",
  "reviewing", "quality_gate", "completed", "failed",
];

function statusColor(status: RunStatus): string {
  switch (status) {
    case "approved": return "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400 border-emerald-200 dark:border-emerald-800";
    case "rejected": return "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400 border-red-200 dark:border-red-800";
    case "needs_human_review": return "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400 border-amber-200 dark:border-amber-800";
    case "failed": return "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400";
    case "cancelled": return "bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-400";
    case "running": return "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400";
    case "pending": return "bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-400";
  }
}

function stageLabel(stage: string): string {
  return stage.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function stageIcon(st: string): [string, string] {
  switch (st) {
    case "succeeded": return ["✓", "text-emerald-500 bg-emerald-100 dark:bg-emerald-900/30 border-emerald-200 dark:border-emerald-800"];
    case "failed": return ["✗", "text-red-500 bg-red-100 dark:bg-red-900/30 border-red-200 dark:border-red-800"];
    case "skipped": return ["○", "text-slate-400 bg-slate-100 dark:bg-slate-700 border-slate-200 dark:border-slate-700"];
    case "cancelled": return ["—", "text-slate-400 bg-slate-100 dark:bg-slate-700 border-slate-200 dark:border-slate-700"];
    case "running": return ["●", "text-blue-500 bg-blue-100 dark:bg-blue-900/30 border-blue-200 dark:border-blue-800 animate-pulse"];
    default: return ["○", "text-slate-300 dark:text-slate-600 bg-slate-50 dark:bg-slate-800 border-slate-200 dark:border-slate-700"];
  }
}

function formatTimestamp(ts: string): string {
  try {
    return new Date(ts).toLocaleString(undefined, {
      month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    });
  } catch {
    return ts;
  }
}

function durationStr(start: string | null | undefined, finish: string | null | undefined): string {
  if (!start || !finish) return "—";
  try {
    const ms = new Date(finish).getTime() - new Date(start).getTime();
    if (ms < 1000) return `${ms}ms`;
    if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
    const m = Math.floor(ms / 60_000);
    const s = Math.floor((ms % 60_000) / 1000);
    return `${m}m ${s}s`;
  } catch {
    return "—";
  }
}

function statusBadge(status: RunStatus) {
  const labels: Record<string, string> = {
    approved: "APPROVED", rejected: "REJECTED",
    needs_human_review: "REVIEW", failed: "FAILED",
    cancelled: "CANCELLED", running: "RUNNING", pending: "PENDING",
  };
  return (
    <span className={`inline-flex items-center gap-1 text-xs font-bold px-2.5 py-1 rounded-md border ${statusColor(status)}`}>
      {status === "running" && (
        <span className="w-2 h-2 rounded-full bg-blue-500 animate-pulse" />
      )}
      {labels[status] || status.toUpperCase()}
    </span>
  );
}

// ── Decision Banner ────────────────────────────────────────────

function DecisionBanner({ status, failure, decision }: {
  status: RunStatus; failure?: FailureInfo | null; decision?: string;
}) {
  if (status === "running" || status === "pending") {
    return (
      <div className="rounded-xl border-2 border-blue-200 dark:border-blue-800 bg-blue-50 dark:bg-blue-900/10 p-6">
        <div className="flex items-center gap-4">
          <svg className="w-10 h-10 text-blue-500 animate-pulse" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182" />
          </svg>
          <div>
            <h2 className="text-xl font-bold text-blue-800 dark:text-blue-300">Run In Progress</h2>
            <p className="text-sm text-blue-600 dark:text-blue-400 mt-1">{decision || "Orchestrating multi-agent pipeline..."}</p>
          </div>
        </div>
      </div>
    );
  }

  const isPositive = status === "approved";
  const isNeutral = status === "needs_human_review" || status === "cancelled";
  const isNegative = status === "rejected" || status === "failed";

  const borderColor = isPositive
    ? "border-emerald-200 dark:border-emerald-800"
    : isNeutral
      ? "border-amber-200 dark:border-amber-800"
      : "border-red-200 dark:border-red-800";

  const bgColor = isPositive
    ? "bg-emerald-50 dark:bg-emerald-900/10"
    : isNeutral
      ? "bg-amber-50 dark:bg-amber-900/10"
      : "bg-red-50 dark:bg-red-900/10";

  const iconColor = isPositive ? "text-emerald-500" : isNeutral ? "text-amber-500" : "text-red-500";
  const textColor = isPositive
    ? "text-emerald-800 dark:text-emerald-300"
    : isNeutral
      ? "text-amber-800 dark:text-amber-300"
      : "text-red-800 dark:text-red-300";

  const subColor = isPositive
    ? "text-emerald-600 dark:text-emerald-400"
    : isNeutral
      ? "text-amber-600 dark:text-amber-400"
      : "text-red-600 dark:text-red-400";

  const icon = isPositive ? (
    <svg className="w-10 h-10" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
    </svg>
  ) : isNeutral ? (
    <svg className="w-10 h-10" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
    </svg>
  ) : (
    <svg className="w-10 h-10" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M9.75 9.75l4.5 4.5m0-4.5l-4.5 4.5M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
    </svg>
  );

  const failureMessage = failure?.message;

  return (
    <div className={`rounded-xl border-2 ${borderColor} ${bgColor} p-6 transition-all duration-500`}>
      <div className="flex items-center gap-4">
        <div className={iconColor}>{icon}</div>
        <div className="flex-1">
          <div className="flex items-center gap-3">
            <h2 className={`text-xl font-bold ${textColor}`}>
              {status === "approved" ? "Approved" :
               status === "rejected" ? "Rejected" :
               status === "needs_human_review" ? "Needs Human Review" :
               status === "failed" ? "Failed" :
               status === "cancelled" ? "Cancelled" : status}
            </h2>
          </div>
          <p className={`text-sm mt-1 ${subColor}`}>{failureMessage || ""}</p>
        </div>
      </div>
    </div>
  );
}

// ── Stage Timeline ─────────────────────────────────────────────

function StageTimeline({ stageResults, currentStage }: {
  stageResults: StageResult[]; currentStage: string;
}) {
  const resultMap = new Map<string, StageResult>();
  for (const sr of stageResults) {
    resultMap.set(sr.stage, sr);
  }

  const pipelineStages: string[] = [
    "acquiring_repository", "analyzing_repository", "analyzing_task",
    "planning", "retrieving_context", "coding",
    "validating_patch", "applying_patch", "testing",
    "repairing", "reviewing", "quality_gate",
  ];

  return (
    <div className="space-y-1">
      {pipelineStages.map((stage, i) => {
        const sr = resultMap.get(stage);
        const st = sr?.status || "pending";
        const [icon, iconClasses] = stageIcon(st);
        const isActive = stage === currentStage;
        const isLast = i === pipelineStages.length - 1;

        return (
          <div key={stage} className="flex items-start gap-3 group">
            {/* Timeline connector */}
            <div className="flex flex-col items-center shrink-0">
              <div className={`w-7 h-7 rounded-full border-2 flex items-center justify-center text-[10px] font-bold transition-all duration-300 ${
                isActive ? "ring-2 ring-blue-300 dark:ring-blue-700" : ""
              } ${iconClasses}`}>
                {icon}
              </div>
              {!isLast && (
                <div className={`w-0.5 h-6 transition-all duration-300 ${
                  st === "succeeded" ? "bg-emerald-300 dark:bg-emerald-700" :
                  st === "failed" ? "bg-red-300 dark:bg-red-700" :
                  "bg-slate-200 dark:bg-slate-700"
                }`} />
              )}
            </div>

            {/* Content */}
            <div className={`flex-1 pb-5 transition-all duration-200 ${
              isActive ? "opacity-100" : "opacity-70 group-hover:opacity-100"
            }`}>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className={`text-sm font-medium ${
                    isActive
                      ? "text-blue-700 dark:text-blue-300"
                      : "text-slate-700 dark:text-slate-300"
                  }`}>
                    {stageLabel(stage)}
                  </span>
                  <span className={`text-[10px] font-medium uppercase ${
                    st === "succeeded" ? "text-emerald-500" :
                    st === "failed" ? "text-red-500" :
                    st === "skipped" ? "text-slate-400" :
                    st === "running" ? "text-blue-500" :
                    "text-slate-400"
                  }`}>
                    {st === "pending" ? "" : st}
                  </span>
                </div>
                <span className="text-[11px] text-slate-400">
                  {sr?.started_at ? `${durationStr(sr.started_at, sr.finished_at)}` : ""}
                </span>
              </div>

              {/* Failure info */}
              {sr?.error && (
                <p className="text-[11px] text-red-600 dark:text-red-400 mt-1 font-mono">
                  {sr.error}
                </p>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ── Events Log ─────────────────────────────────────────────────

function EventsLog({ events }: { events: RunEvent[] }) {
  const [expanded, setExpanded] = useState(false);
  const displayEvents = expanded ? events : events.slice(-10);

  return (
    <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-5 py-3.5 text-left hover:bg-slate-50 dark:hover:bg-slate-700/50 transition-colors"
      >
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-slate-900 dark:text-white">
            Events
          </span>
          <span className="text-xs text-slate-500 dark:text-slate-400">
            ({events.length} total)
          </span>
        </div>
        <svg className={`w-4 h-4 text-slate-400 transition-transform duration-200 ${expanded ? "rotate-180" : ""}`} fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
        </svg>
      </button>

      <div className={`transition-all duration-300 ease-in-out overflow-hidden ${
        expanded ? "max-h-[3000px] opacity-100" : "max-h-[400px] opacity-100"
      }`}>
        <div className="px-5 pb-4 space-y-1">
          {displayEvents.length === 0 && (
            <p className="text-xs text-slate-400 py-2">No events recorded.</p>
          )}
          {displayEvents.map((evt) => (
            <div key={evt.event_id} className="flex items-start gap-2 py-1.5 text-xs border-b border-slate-100 dark:border-slate-700/50 last:border-0">
              <span className="font-mono text-slate-400 shrink-0 w-28">
                {formatTimestamp(evt.timestamp)}
              </span>
              <span className="font-medium text-slate-500 dark:text-slate-400 shrink-0 w-20">
                {evt.event_type.replace(/_/g, " ")}
              </span>
              <span className="font-mono text-slate-400 shrink-0 w-28">
                {evt.stage ? stageLabel(evt.stage) : ""}
              </span>
              <span className="text-slate-700 dark:text-slate-300">
                {evt.message}
              </span>
            </div>
          ))}
        </div>

        {!expanded && events.length > 10 && (
          <div className="px-5 pb-3 text-center">
            <button
              onClick={() => setExpanded(true)}
              className="text-xs text-primary-500 hover:text-primary-400 transition-colors"
            >
              Show all {events.length} events
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Summary Card ───────────────────────────────────────────────

function SummaryCard({ label, value }: { label: string; value?: string | null }) {
  if (!value) return null;
  return (
    <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-4">
      <p className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-1">{label}</p>
      <p className="text-sm text-slate-900 dark:text-white">{value}</p>
    </div>
  );
}

// ── Warnings ───────────────────────────────────────────────────

function WarningList({ warnings }: { warnings: string[] }) {
  if (!warnings || warnings.length === 0) return null;
  return (
    <div className="rounded-xl bg-amber-50 dark:bg-amber-900/10 border border-amber-200 dark:border-amber-800/50 p-4">
      <div className="flex items-center gap-2 mb-2">
        <svg className="w-4 h-4 text-amber-500" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" />
        </svg>
        <span className="text-xs font-semibold text-amber-700 dark:text-amber-400">Warnings</span>
      </div>
      <ul className="space-y-1">
        {warnings.map((w, i) => (
          <li key={i} className="text-xs text-amber-600 dark:text-amber-300 flex items-start gap-2">
            <span className="mt-1">-</span>
            {w}
          </li>
        ))}
      </ul>
    </div>
  );
}

// ── Cancel Button ──────────────────────────────────────────────

function CancelButton({ runId, status, onCancelled }: {
  runId: string; status: RunStatus; onCancelled: () => void;
}) {
  const [cancelling, setCancelling] = useState(false);

  if (status !== "running" && status !== "pending") return null;

  const handleCancel = useCallback(async () => {
    if (!confirm("Cancel this run?")) return;
    setCancelling(true);
    try {
      await runsApi.cancel(runId);
      onCancelled();
    } catch {
      // ignore
    } finally {
      setCancelling(false);
    }
  }, [runId, onCancelled]);

  return (
    <button
      onClick={handleCancel}
      disabled={cancelling}
      className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800/50 hover:bg-red-100 dark:hover:bg-red-900/30 disabled:opacity-50 transition-all"
    >
      {cancelling ? (
        <svg className="animate-spin w-3.5 h-3.5" viewBox="0 0 24 24" fill="none">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
      ) : (
        <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
        </svg>
      )}
      Cancel Run
    </button>
  );
}

// ── Resume Button ─────────────────────────────────────────────

function ResumeButton({ runId, status, wsConnected, onResumed }: {
  runId: string; status: RunStatus; wsConnected: boolean; onResumed: () => void;
}) {
  const [resuming, setResuming] = useState(false);

  // Only show for running/pending runs when WebSocket is disconnected
  // (suggests the backend may have restarted mid-execution)
  // Failed/review runs are terminal on the backend — resume always errors there.
  const isStuck = (status === "running" || status === "pending") && !wsConnected;

  if (!isStuck) return null;

  const confirmMsg = "This run may have been interrupted. Resume execution from the last checkpoint?";

  const handleResume = useCallback(async () => {
    if (!confirm(confirmMsg)) return;
    setResuming(true);
    try {
      await runsApi.resume(runId);
      onResumed();
    } catch (err) {
      console.error("Resume failed:", err);
      alert(err instanceof Error ? err.message : "Failed to resume run");
    } finally {
      setResuming(false);
    }
  }, [runId, confirmMsg, onResumed]);

  return (
    <button
      onClick={handleResume}
      disabled={resuming}
      className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-900/20 border border-emerald-200 dark:border-emerald-800/50 hover:bg-emerald-100 dark:hover:bg-emerald-900/30 disabled:opacity-50 transition-all"
    >
      {resuming ? (
        <svg className="animate-spin w-3.5 h-3.5" viewBox="0 0 24 24" fill="none">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
      ) : (
        <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182" />
        </svg>
      )}
      Resume Run
    </button>
  );
}

// ── Connection indicator ──────────────────────────────────────

function WsIndicator({ connected }: { connected: boolean }) {
  return (
    <span
      className={`inline-flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full transition-all ${
        connected
          ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"
          : "bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-400"
      }`}
      title={connected ? "Connected (real-time)" : "Disconnected (fallback to polling)"}
    >
      <span className={`w-1.5 h-1.5 rounded-full ${connected ? "bg-emerald-500" : "bg-slate-400"}`} />
      {connected ? "Live" : "Polling"}
    </span>
  );
}

// ── Helper: normalize WebSocket run data to match RunData shape ──

function normalizeRunData(data: {
  run_id: string;
  status: string;
  source?: {
    source_type?: string;
    title?: string;
    description?: string;
    repository_path?: string | null;
    acceptance_criteria?: string[];
    execution_budget?: Record<string, unknown>;
  };
  current_stage: string;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  stage_results?: StageResult[];
  failure?: FailureInfo | null;
  warnings?: string[];
  total_duration_ms?: number | null;
  cancellation_requested?: boolean;
  auxiliary_repositories?: Array<Record<string, unknown>>;
  repo_validation?: Array<Record<string, unknown>>;
  repositories?: Array<Record<string, unknown>>;
  organization_summary?: Record<string, unknown> | null;
}): {
  run_id: string;
  status: RunStatus;
  source: {
    source_type: string;
    title: string;
    description: string;
    repository_path: string | null;
    acceptance_criteria: string[];
    execution_budget: Record<string, unknown>;
  };
  current_stage: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  stage_results: StageResult[];
  failure: FailureInfo | null;
  warnings: string[];
  total_duration_ms: number | null;
  cancellation_requested: boolean;
  auxiliary_repositories: Array<Record<string, unknown>>;
  repo_validation: RepositoryPatchValidation[];
  repositories: RepositoryStatus[];
  organization_summary: OrganizationSummaryType | null;
} {
  return {
    run_id: data.run_id,
    status: data.status as RunStatus,
    source: {
      source_type: data.source?.source_type || "user_task",
      title: data.source?.title || "",
      description: data.source?.description || "",
      repository_path: data.source?.repository_path || null,
      acceptance_criteria: data.source?.acceptance_criteria || [],
      execution_budget: data.source?.execution_budget || {},
    },
    current_stage: data.current_stage,
    created_at: data.created_at,
    started_at: data.started_at || null,
    finished_at: data.finished_at || null,
    stage_results: data.stage_results || [],
    failure: data.failure || null,
    warnings: data.warnings || [],
    total_duration_ms: data.total_duration_ms || null,
    cancellation_requested: data.cancellation_requested || false,
    auxiliary_repositories: data.auxiliary_repositories || [],
    repo_validation: (data.repo_validation || ([] as unknown[])).filter(
      (rv): rv is RepositoryPatchValidation =>
        !!rv && typeof (rv as { repository_id?: unknown }).repository_id === "string"
    ),
    repositories: (data.repositories || ([] as unknown[])).filter(isValidRepository),
    organization_summary: (data.organization_summary as OrganizationSummaryType | null) || null,
  };
}

// ── Main Page ──────────────────────────────────────────────────

export default function RunDetailPage() {
  const params = useParams();
  const runId = params?.id as string;

  // WebSocket data shape — compatible with what the components expect
  interface RunData {
    run_id: string;
    status: RunStatus;
    source: {
      source_type: string;
      title: string;
      description?: string;
      repository_path?: string | null;
      issue_number?: number | null;
      acceptance_criteria?: string[];
      execution_budget?: Record<string, unknown>;
    };
    current_stage: string;
    created_at: string;
    started_at?: string | null;
    finished_at?: string | null;
    stage_results: StageResult[];
    failure?: FailureInfo | null;
    warnings: string[];
    total_duration_ms?: number | null;
    cancellation_requested: boolean;
    auxiliary_repositories?: Array<Record<string, unknown>>;
    repo_validation?: RepositoryPatchValidation[];
    repositories?: RepositoryStatus[];
    organization_summary?: OrganizationSummaryType | null;
  }

  const [run, setRun] = useState<RunData | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // WebSocket for real-time updates
  const wsState = useRunWebSocket(runId);

  // Merge WebSocket data into run state
  useEffect(() => {
    if (wsState.runData) {
      setRun(normalizeRunData(wsState.runData));
      setError(null);
      setLoading(false);
    }
  }, [wsState.runData]);

  // Merge WebSocket events
  useEffect(() => {
    if (wsState.events.length > 0) {
      setEvents(wsState.events);
    }
  }, [wsState.events]);

  // Initial REST fetch (used before WebSocket connects)
  const fetchRun = useCallback(async () => {
    if (!runId) return;
    try {
      const result = await runsApi.get(runId);
      // Only set if we don't already have WebSocket data
      setRun((prev) => prev ?? result.data);
      setError(null);

      // Initial events fetch
      try {
        const eventResult = await runsApi.events(runId);
        setEvents((prev) => (prev.length === 0 ? eventResult.data || [] : prev));
      } catch {
        // optional
      }
    } catch (err: unknown) {
      if (err instanceof Error && err.message.includes("404")) {
        setError("Run not found");
      } else {
        setError(err instanceof Error ? err.message : "Failed to load run");
      }
    } finally {
      setLoading(false);
    }
  }, [runId]);

  useEffect(() => {
    fetchRun();
  }, [fetchRun]);

  // Fallback polling when WebSocket is disconnected
  useEffect(() => {
    if (!run || wsState.connected) return;
    if (run.status !== "running" && run.status !== "pending") return;
    const interval = setInterval(fetchRun, 5000);
    return () => clearInterval(interval);
  }, [run?.status, wsState.connected, fetchRun]);

  // Loading
  if (loading && !run) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="text-center">
          <svg className="animate-spin w-10 h-10 text-primary-500 mx-auto mb-4" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          <p className="text-sm text-slate-500 dark:text-slate-400">Loading run...</p>
        </div>
      </div>
    );
  }

  // Error
  if (error && !run) {
    return (
      <div className="space-y-4">
        <Link
          href="/dashboard/runs"
          className="inline-flex items-center gap-1.5 text-xs font-medium text-primary-600 dark:text-primary-400 hover:text-primary-500 transition-colors"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 19.5l-6.75-6.75m0 0l6.75-6.75m-6.75 6.75H21" />
          </svg>
          Back to Runs
        </Link>
        <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-8 text-center">
          <svg className="w-12 h-12 mx-auto text-slate-300 dark:text-slate-600 mb-3" fill="none" viewBox="0 0 24 24" strokeWidth={1} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636" />
          </svg>
          <p className="text-sm text-slate-500 dark:text-slate-400 mb-3">{error}</p>
          <button
            onClick={fetchRun}
            className="px-4 py-2 rounded-lg bg-primary-600 text-white text-xs font-medium hover:bg-primary-500 transition-all"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (!run) return null;

  // Counts for stats
  const succeededStages = run.stage_results?.filter((s) => s.status === "succeeded").length || 0;
  const failedStages = run.stage_results?.filter((s) => s.status === "failed").length || 0;
  const skippedStages = run.stage_results?.filter((s) => s.status === "skipped").length || 0;
  const totalPipeline = 12;

  return (
    <div className="space-y-6">
      {/* Back link */}
      <Link
        href="/dashboard/runs"
        className="inline-flex items-center gap-1.5 text-xs font-medium text-primary-600 dark:text-primary-400 hover:text-primary-500 transition-colors"
      >
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 19.5l-6.75-6.75m0 0l6.75-6.75m-6.75 6.75H21" />
        </svg>
        Back to Runs
      </Link>

      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <h1 className="text-xl font-bold text-slate-900 dark:text-white font-mono">
              {run.run_id}
            </h1>
            {statusBadge(run.status)}
          </div>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            {run.source?.title || "Untitled Task"}
          </p>
          {run.source?.repository_path && (
            <p className="text-xs text-slate-400 mt-0.5">{run.source.repository_path}</p>
          )}
          <p className="text-xs text-slate-400 mt-1">
            Created {formatTimestamp(run.created_at)}
            {run.started_at && run.finished_at && (
              <> · Duration {durationStr(run.started_at, run.finished_at)}</>
            )}
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <WsIndicator connected={wsState.connected} />
          <ResumeButton runId={run.run_id} status={run.status} wsConnected={wsState.connected} onResumed={fetchRun} />
          <CancelButton runId={run.run_id} status={run.status} onCancelled={fetchRun} />
          <button
            onClick={fetchRun}
            className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium text-slate-600 dark:text-slate-400 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-700 transition-all"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182" />
            </svg>
            Refresh
          </button>
        </div>
      </div>

      {/* Decision Banner */}
      <DecisionBanner
        status={run.status}
        failure={run.failure}
      />

      {/* Phase 20A6: Repository Status Cards (live via WebSocket) */}
      {(run.repositories?.length ?? 0) > 0 && (
        <RepositoryStatusCards repositories={run.repositories || []} runId={run.run_id} />
      )}

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="p-4 rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700">
          <p className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">Stages</p>
          <p className="mt-1 text-2xl font-bold text-slate-900 dark:text-white">{succeededStages}/{totalPipeline}</p>
        </div>
        <div className="p-4 rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700">
          <p className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">Failed</p>
          <p className={`mt-1 text-2xl font-bold ${failedStages > 0 ? "text-red-600 dark:text-red-400" : "text-slate-400"}`}>{failedStages}</p>
        </div>
        <div className="p-4 rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700">
          <p className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">Skipped</p>
          <p className="mt-1 text-2xl font-bold text-slate-500 dark:text-slate-400">{skippedStages}</p>
        </div>
        <div className="p-4 rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700">
          <p className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">Events</p>
          <p className="mt-1 text-2xl font-bold text-slate-900 dark:text-white">{events.length}</p>
        </div>
      </div>

      {/* Warnings */}
      <WarningList warnings={run.warnings} />

      {/* Phase 20A6: Cross-Repository Execution Timeline */}
      {(run.repositories?.length ?? 0) > 0 && (
        <RepositoryTimeline repositories={run.repositories || []} />
      )}

      {/* Main content: Timeline + Events */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Pipeline Timeline */}
        <div className="lg:col-span-2">
          <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-5">
            <h3 className="text-sm font-semibold text-slate-900 dark:text-white mb-4">Pipeline Timeline</h3>
            <StageTimeline stageResults={run.stage_results} currentStage={run.current_stage} />
          </div>
        </div>

        {/* Sidebar */}
        <div className="space-y-4">
          <EventsLog events={events} />

          {/* Source Info */}
          <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-5">
            <h3 className="text-sm font-semibold text-slate-900 dark:text-white mb-3">Source</h3>
            <div className="space-y-2 text-xs">
              <div>
                <span className="text-slate-500 dark:text-slate-400">Type: </span>
                <span className="text-slate-900 dark:text-white capitalize">{run.source?.source_type?.replace(/_/g, " ") || "—"}</span>
              </div>
              <div>
                <span className="text-slate-500 dark:text-slate-400">Title: </span>
                <span className="text-slate-900 dark:text-white">{run.source?.title || "—"}</span>
              </div>
              {run.source?.description && (
                <div>
                  <span className="text-slate-500 dark:text-slate-400">Description: </span>
                  <span className="text-slate-900 dark:text-white">{run.source.description.slice(0, 200)}</span>
                </div>
              )}
              {run.source?.issue_number && (
                <div>
                  <span className="text-slate-500 dark:text-slate-400">Issue: </span>
                  <span className="text-slate-900 dark:text-white">#{run.source.issue_number}</span>
                </div>
              )}
              {(run.source?.acceptance_criteria?.length ?? 0) > 0 && (
                <div>
                  <span className="text-slate-500 dark:text-slate-400">Acceptance Criteria: </span>
                  <ul className="mt-1 space-y-0.5">
                    {run.source.acceptance_criteria!.map((c, i) => (
                      <li key={i} className="text-slate-900 dark:text-white text-[11px]">• {c}</li>
                    ))}
                  </ul>
                </div>
              )}
              {Object.keys(run.source?.execution_budget || {}).length > 0 && (
                <div>
                  <span className="text-slate-500 dark:text-slate-400">Execution Budget: </span>
                  <span className="text-slate-900 dark:text-white">
                    {Object.entries(run.source!.execution_budget!).map(([k, v]) => `${k}=${String(v)}`).join(", ")}
                  </span>
                </div>
              )}
              {(run.auxiliary_repositories?.length ?? 0) > 0 && (
                <div>
                  <span className="text-slate-500 dark:text-slate-400">Aux Repositories: </span>
                  <ul className="mt-1 space-y-1">
                    {run.auxiliary_repositories?.map((aux) => {
                      const rid = String(aux.repository_id ?? aux.namespace_id ?? "?");
                      const loc = String(aux.path || aux.owner || aux.repo || "");
                      return (
                        <li key={rid} className="text-slate-900 dark:text-white font-mono text-[11px]">
                          {rid}
                          {loc && <span className="text-slate-400 dark:text-slate-500"> · {loc}</span>}
                          {aux.source_type ? (
                            <span className="ml-1 text-[10px] uppercase tracking-wide text-slate-400 dark:text-slate-500">
                              {String(aux.source_type)}
                            </span>
                          ) : null}
                        </li>
                      );
                    })}
                  </ul>
                </div>
              )}
              {run.total_duration_ms != null && (
                <div>
                  <span className="text-slate-500 dark:text-slate-400">Total Duration: </span>
                  <span className="text-slate-900 dark:text-white">{(run.total_duration_ms / 1000).toFixed(1)}s</span>
                </div>
              )}
            </div>
          </div>

          {/* Repository Validation (Phase 20 A4/A5) */}
          {(run.repo_validation?.length ?? 0) > 0 && (
            <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-5">
              <h3 className="text-sm font-semibold text-slate-900 dark:text-white mb-3">Repository Validation</h3>
              <div className="space-y-2">
                {run.repo_validation?.map((rv) => {
                  const ok =
                    rv.validation_status === "validated" &&
                    rv.application_status !== "rejected";
                  return (
                    <div key={rv.repository_id} className="rounded-lg border border-slate-200 dark:border-slate-700 p-3">
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-mono text-xs text-slate-900 dark:text-white">{rv.repository_id}</span>
                        <span
                          className={`text-[10px] font-semibold uppercase tracking-wide px-2 py-0.5 rounded-full ${
                            ok
                              ? "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-400"
                              : "bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-400"
                          }`}
                        >
                          {rv.validation_status}
                        </span>
                      </div>
                      <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-slate-500 dark:text-slate-400">
                        <span>applied: <span className="text-slate-900 dark:text-white">{rv.application_status}</span></span>
                        <span>changes: <span className="text-slate-900 dark:text-white">{rv.changes_applied}/{rv.changes_attempted}</span></span>
                      </div>
                      {(rv.changed_files?.length ?? 0) > 0 && (
                        <div className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">
                          files: <span className="text-slate-900 dark:text-white font-mono">{rv.changed_files.slice(0, 10).join(", ")}</span>
                          {rv.changed_files.length > 10 ? "…" : ""}
                        </div>
                      )}
                      {rv.validation_errors?.length ? (
                        <p className="mt-1 text-[11px] text-red-600 dark:text-red-400">{rv.validation_errors[0]}</p>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Failure Detail */}
          {run.failure && (
            <div className="rounded-xl bg-red-50 dark:bg-red-900/10 border border-red-200 dark:border-red-800/50 p-5">
              <h3 className="text-sm font-semibold text-red-700 dark:text-red-400 mb-2">Failure</h3>
              <div className="space-y-1 text-xs">
                <p className="text-red-600 dark:text-red-300">Stage: {stageLabel(run.failure.stage)}</p>
                <p className="text-red-600 dark:text-red-300">Code: {run.failure.code}</p>
                <p className="text-red-600 dark:text-red-300">{run.failure.message}</p>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Phase 20A6: Organization Summary (run completion) */}
      <OrganizationSummary summary={run.organization_summary || null} />

      {/* Phase 20A6: Context & Run History */}
      <RunHistoryPanel
        runId={run.run_id}
        decisions={run.organization_summary?.engineering_decisions}
      />
    </div>
  );
}
