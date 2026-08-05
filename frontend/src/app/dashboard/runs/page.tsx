"use client";

import { useState, useEffect, useCallback, useRef, Suspense } from "react";
import Link from "next/link";
import { useSearchParams, useRouter } from "next/navigation";
import { runsApi, orchestrationApi } from "@/lib/api/client";
import { useRunListWebSocket } from "@/lib/hooks/useRunWebSocket";
import type {
  RunStatus,
  StageType,
  RunSummary,
  Capabilities,
  AuxiliaryRepositorySpec,
} from "@/lib/api/client";

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

function timeAgo(ts: string): string {
  try {
    const ms = Date.now() - new Date(ts).getTime();
    if (ms < 60_000) return "just now";
    if (ms < 3_600_000) return `${Math.floor(ms / 60_000)}m ago`;
    if (ms < 86_400_000) return `${Math.floor(ms / 3_600_000)}h ago`;
    return `${Math.floor(ms / 86_400_000)}d ago`;
  } catch {
    return "";
  }
}

function statusBadge(status: RunStatus) {
  const labels: Record<string, string> = {
    approved: "APPROVED", rejected: "REJECTED",
    needs_human_review: "REVIEW", failed: "FAILED",
    cancelled: "CANCELLED", running: "RUNNING", pending: "PENDING",
  };
  return (
    <span className={`inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded-md border ${statusColor(status)}`}>
      {status === "running" && (
        <span className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse" />
      )}
      {labels[status] || status.toUpperCase()}
    </span>
  );
}

// ── Constants ──────────────────────────────────────────────────

const PAGE_SIZE = 20;

const STATUS_OPTIONS: { label: string; value: string | null }[] = [
  { label: "All", value: null },
  { label: "Running", value: "running" },
  { label: "Pending", value: "pending" },
  { label: "Approved", value: "approved" },
  { label: "Rejected", value: "rejected" },
  { label: "Review", value: "needs_human_review" },
  { label: "Failed", value: "failed" },
  { label: "Cancelled", value: "cancelled" },
];

const SORT_OPTIONS: { label: string; value: string }[] = [
  { label: "Newest", value: "newest" },
  { label: "Oldest", value: "oldest" },
  { label: "Duration", value: "duration" },
];

// ── Stat Card ──────────────────────────────────────────────────

function StatCard({ label, value, sub, color }: {
  label: string; value: string | number; sub?: string; color: string;
}) {
  return (
    <div className="p-4 rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 transition-all duration-200 hover:shadow-md">
      <p className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">{label}</p>
      <p className={`mt-1 text-2xl font-bold ${color}`}>{value}</p>
      {sub && <p className="mt-1 text-[11px] text-slate-400">{sub}</p>}
    </div>
  );
}

// ── Status Filter Bar ──────────────────────────────────────────

function StatusFilter({ current, onChange }: {
  current: string | null; onChange: (status: string | null) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {STATUS_OPTIONS.map((opt) => {
        const isActive = current === opt.value;
        return (
          <button
            key={opt.label}
            onClick={() => onChange(opt.value)}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all duration-150 ${
              isActive
                ? "bg-primary-100 dark:bg-primary-900/30 text-primary-700 dark:text-primary-300 border border-primary-200 dark:border-primary-800/50 shadow-sm"
                : "bg-white dark:bg-slate-800 text-slate-600 dark:text-slate-400 border border-slate-200 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-700/50"
            }`}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

// ── Sort Dropdown ──────────────────────────────────────────────

function SortDropdown({ current, onChange }: {
  current: string; onChange: (sort: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Close on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const currentLabel = SORT_OPTIONS.find((o) => o.value === current)?.label || "Newest";

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all duration-150 bg-white dark:bg-slate-800 text-slate-600 dark:text-slate-400 border border-slate-200 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-700/50"
      >
        <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6.75h16.5M3.75 12h9.75m-9.75 5.25h5.25" />
        </svg>
        {currentLabel}
        <svg className={`w-3 h-3 transition-transform ${open ? "rotate-180" : ""}`} fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
        </svg>
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-1 z-20 min-w-[140px] bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg shadow-lg py-1">
          {SORT_OPTIONS.map((opt) => {
            const isActive = current === opt.value;
            return (
              <button
                key={opt.value}
                onClick={() => { onChange(opt.value); setOpen(false); }}
                className={`w-full flex items-center gap-2 px-3 py-1.5 text-xs font-medium transition-colors ${
                  isActive
                    ? "bg-primary-50 dark:bg-primary-900/20 text-primary-700 dark:text-primary-300"
                    : "text-slate-600 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-700/50"
                }`}
              >
                {isActive && (
                  <svg className="w-3.5 h-3.5 shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth={2.5} stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                  </svg>
                )}
                <span className={isActive ? "" : "ml-5"}>{opt.label}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── Date Range Filter ──────────────────────────────────────────

const DATE_PRESETS: { label: string; value: string; days?: number }[] = [
  { label: "All time", value: "all" },
  { label: "Last 7 days", value: "7d", days: 7 },
  { label: "Last 30 days", value: "30d", days: 30 },
  { label: "Custom", value: "custom" },
];

function formatISO(d: Date): string {
  return d.toISOString().slice(0, 19) + "Z";
}

function DateRangeFilter({ current, onChange }: {
  current: string;
  onChange: (preset: string, createdAfter?: string, createdBefore?: string) => void;
}) {
  const isCustom = current === "custom";
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {DATE_PRESETS.map((preset) => {
        const isActive = current === preset.value;
        return (
          <button
            key={preset.value}
            onClick={() => {
              if (preset.value === "custom") {
                onChange("custom", undefined, undefined);
              } else if (preset.days) {
                const after = new Date();
                after.setDate(after.getDate() - preset.days);
                after.setHours(0, 0, 0, 0);
                setFromDate("");
                setToDate("");
                onChange(preset.value, formatISO(after), undefined);
              } else {
                setFromDate("");
                setToDate("");
                onChange("all", undefined, undefined);
              }
            }}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all duration-150 ${
              isActive
                ? "bg-primary-100 dark:bg-primary-900/30 text-primary-700 dark:text-primary-300 border border-primary-200 dark:border-primary-800/50 shadow-sm"
                : "bg-white dark:bg-slate-800 text-slate-600 dark:text-slate-400 border border-slate-200 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-700/50"
            }`}
          >
            {preset.label}
          </button>
        );
      })}

      {isCustom && (
        <div className="flex items-center gap-2 ml-2 pl-3 border-l border-slate-200 dark:border-slate-700">
          <input
            type="date"
            value={fromDate}
            onChange={(e) => {
              const val = e.target.value;
              setFromDate(val);
              if (val && toDate) {
                onChange("custom", formatISO(new Date(val + "T00:00:00Z")), formatISO(new Date(toDate + "T23:59:59Z")));
              } else if (val) {
                onChange("custom", formatISO(new Date(val + "T00:00:00Z")), undefined);
              }
            }}
            className="px-2 py-1.5 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-xs text-slate-700 dark:text-slate-300 focus:outline-none focus:ring-2 focus:ring-primary-500"
          />
          <span className="text-xs text-slate-400">to</span>
          <input
            type="date"
            value={toDate}
            onChange={(e) => {
              const val = e.target.value;
              setToDate(val);
              if (fromDate && val) {
                onChange("custom", formatISO(new Date(fromDate + "T00:00:00Z")), formatISO(new Date(val + "T23:59:59Z")));
              } else if (val) {
                onChange("custom", undefined, formatISO(new Date(val + "T23:59:59Z")));
              }
            }}
            className="px-2 py-1.5 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-xs text-slate-700 dark:text-slate-300 focus:outline-none focus:ring-2 focus:ring-primary-500"
          />
        </div>
      )}
    </div>
  );
}

// ── Pagination Bar ─────────────────────────────────────────────

function PaginationBar({ page, total, limit, onChange }: {
  page: number; total: number; limit: number; onChange: (page: number) => void;
}) {
  const totalPages = Math.max(1, Math.ceil(total / limit));
  const start = total === 0 ? 0 : page * limit + 1;
  const end = Math.min((page + 1) * limit, total);

  return (
    <div className="flex items-center justify-between px-1 py-3 text-xs text-slate-500 dark:text-slate-400">
      <span>
        Showing <strong>{start}–{end}</strong> of <strong>{total}</strong>
      </span>
      <div className="flex items-center gap-2">
        <button
          onClick={() => onChange(page - 1)}
          disabled={page <= 0}
          className="flex items-center gap-1 px-3 py-1.5 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 hover:bg-slate-50 dark:hover:bg-slate-700/50 disabled:opacity-40 disabled:cursor-not-allowed transition-all"
        >
          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5L8.25 12l7.5-7.5" />
          </svg>
          Previous
        </button>
        <span className="px-2 font-medium text-slate-400">
          {page + 1} / {totalPages}
        </span>
        <button
          onClick={() => onChange(page + 1)}
          disabled={page >= totalPages - 1}
          className="flex items-center gap-1 px-3 py-1.5 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 hover:bg-slate-50 dark:hover:bg-slate-700/50 disabled:opacity-40 disabled:cursor-not-allowed transition-all"
        >
          Next
          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
          </svg>
        </button>
      </div>
    </div>
  );
}

// ── Capabilities Badge ─────────────────────────────────────────

function CapBadge({ label, present }: { label: string; present: boolean }) {
  return (
    <div className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium transition-all ${
      present
        ? "bg-emerald-50 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-400 border border-emerald-200 dark:border-emerald-800/50"
        : "bg-slate-50 dark:bg-slate-700/30 text-slate-400 dark:text-slate-500 border border-slate-200 dark:border-slate-700"
    }`}>
      {present ? (
        <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
        </svg>
      ) : (
        <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
        </svg>
      )}
      {label}
    </div>
  );
}

// ── Activity Chart (Run Count by Day) ─────────────────────────

interface DayCount {
  date: string;          // "Jan 15"
  fullDate: string;      // ISO date key "2026-01-15"
  total: number;
  approved: number;
  failed: number;
  running: number;
  other: number;
}

function computeDailyCounts(runs: RunSummary[], after?: string, before?: string): DayCount[] {
  const countMap = new Map<string, DayCount>();

  // Determine date range
  const now = new Date();
  const start = after ? new Date(after) : new Date(now.getTime() - 30 * 86400000);
  const end = before ? new Date(before) : now;

  // Ensure start ≤ end
  if (start > end) return [];

  // Initialize all days in range
  const cursor = new Date(start);
  cursor.setHours(0, 0, 0, 0);
  const endDay = new Date(end);
  endDay.setHours(23, 59, 59, 999);

  while (cursor <= endDay) {
    const key = cursor.toISOString().slice(0, 10);
    const label = cursor.toLocaleDateString("en-US", { month: "short", day: "numeric" });
    countMap.set(key, { date: label, fullDate: key, total: 0, approved: 0, failed: 0, running: 0, other: 0 });
    cursor.setDate(cursor.getDate() + 1);
  }

  // Count runs by day
  for (const run of runs) {
    try {
      const day = new Date(run.created_at).toISOString().slice(0, 10);
      const entry = countMap.get(day);
      if (!entry) continue;
      entry.total += 1;
      if (run.status === "approved") entry.approved += 1;
      else if (run.status === "failed" || run.status === "rejected") entry.failed += 1;
      else if (run.status === "running" || run.status === "pending") entry.running += 1;
      else entry.other += 1;
    } catch { /* skip malformed dates */ }
  }

  return Array.from(countMap.values());
}

function ActivityChart({ runs, after, before }: {
  runs: RunSummary[]; after?: string; before?: string;
}) {
  const days = computeDailyCounts(runs, after, before);
  if (days.length === 0) return null;

  const maxTotal = Math.max(...days.map((d) => d.total), 1);
  const barMaxHeight = 80; // px

  // Only show every Nth label to avoid crowding
  const labelInterval = days.length > 14 ? Math.ceil(days.length / 7) : 1;

  // Legend
  const hasData = days.some((d) => d.total > 0);

  return (
    <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-xs font-semibold text-slate-600 dark:text-slate-400 uppercase tracking-wider">
          Run Activity
        </h3>
        <div className="flex items-center gap-3">
          <LegendDot color="bg-emerald-500" label="Approved" />
          <LegendDot color="bg-red-500" label="Failed" />
          <LegendDot color="bg-blue-500" label="Pending/Active" />
          <LegendDot color="bg-slate-400" label="Other" />
        </div>
      </div>

      <div className="flex items-end gap-[2px] h-20">
        {days.map((day) => {
          const approvedH = (day.approved / maxTotal) * barMaxHeight;
          const failedH = (day.failed / maxTotal) * barMaxHeight;
          const runningH = (day.running / maxTotal) * barMaxHeight;
          const otherH = (day.other / maxTotal) * barMaxHeight;
          const dayIndex = days.indexOf(day);
          const showLabel = dayIndex % labelInterval === 0 || dayIndex === days.length - 1;

          return (
            <div key={day.fullDate} className="flex flex-col items-center flex-1 min-w-0 group relative">
              {/* Stacked bar */}
              <div className="w-full max-w-[20px] flex flex-col-reverse items-stretch" style={{ height: `${barMaxHeight}px` }}>
                {day.approved > 0 && (
                  <div
                    className="w-full rounded-t-[2px] bg-emerald-500 dark:bg-emerald-400 transition-all duration-150 group-hover:opacity-80"
                    style={{ height: `${Math.max(approvedH, 2)}px` }}
                  />
                )}
                {day.failed > 0 && (
                  <div
                    className="w-full bg-red-500 dark:bg-red-400 transition-all duration-150 group-hover:opacity-80"
                    style={{ height: `${Math.max(failedH, 2)}px` }}
                  />
                )}
                {day.running > 0 && (
                  <div
                    className="w-full bg-blue-500 dark:bg-blue-400 transition-all duration-150 group-hover:opacity-80"
                    style={{ height: `${Math.max(runningH, 2)}px` }}
                  />
                )}
                {day.other > 0 && (
                  <div
                    className="w-full rounded-b-[2px] bg-slate-400 dark:bg-slate-500 transition-all duration-150 group-hover:opacity-80"
                    style={{ height: `${Math.max(otherH, 2)}px` }}
                  />
                )}
              </div>
              {/* Day label */}
              {showLabel && (
                <span className="mt-1 text-[9px] text-slate-400 dark:text-slate-500 whitespace-nowrap">
                  {day.date}
                </span>
              )}
              {/* Tooltip on hover */}
              {day.total > 0 && (
                <div className="absolute bottom-full mb-1 hidden group-hover:flex flex-col items-center z-10">
                  <div className="px-2 py-1 rounded bg-slate-800 dark:bg-slate-700 text-[10px] text-white whitespace-nowrap shadow-lg">
                    <strong>{day.date}</strong> — {day.total} run{day.total !== 1 ? "s" : ""}
                    {day.approved > 0 && <span className="ml-1 text-emerald-300">✓{day.approved}</span>}
                    {day.failed > 0 && <span className="ml-1 text-red-300">✗{day.failed}</span>}
                    {day.running > 0 && <span className="ml-1 text-blue-300">●{day.running}</span>}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {!hasData && (
        <p className="text-[11px] text-slate-400 text-center mt-2">
          No runs in the selected period
        </p>
      )}
    </div>
  );
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <div className="flex items-center gap-1">
      <span className={`w-2 h-2 rounded-full ${color}`} />
      <span className="text-[10px] text-slate-500 dark:text-slate-400">{label}</span>
    </div>
  );
}

// ── Run Card ───────────────────────────────────────────────────

function RunCard({ run }: { run: RunSummary }) {
  const totalStages = STAGE_ORDER.filter((s) => s !== "completed" && s !== "failed").length;

  return (
    <Link
      href={`/dashboard/runs/${run.run_id}`}
      className="block rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-5 hover:shadow-lg hover:border-primary-300 dark:hover:border-primary-700 transition-all duration-200 group"
    >
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-xs font-mono font-semibold text-slate-900 dark:text-white">
              {run.run_id}
            </span>
            {statusBadge(run.status)}
          </div>

          <h3 className="text-sm font-medium text-slate-900 dark:text-white truncate">
            {run.title || "Untitled Task"}
          </h3>

          <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5 truncate">
            {run.source}
          </p>
        </div>

        <div className="shrink-0 text-right">
          <p className="text-[11px] text-slate-400">
            {timeAgo(run.created_at)}
          </p>
          {run.total_duration_ms != null && (
            <p className="text-[11px] text-slate-400 mt-0.5">
              {(run.total_duration_ms / 1000).toFixed(1)}s
            </p>
          )}
          {run.status === "running" && run.current_stage && (
            <p className="text-[11px] text-blue-500 font-medium mt-0.5">
              {stageLabel(run.current_stage)}
            </p>
          )}
        </div>
      </div>

      <div className="mt-3 flex items-center gap-2 text-[10px] text-slate-400">
        <span>{totalStages} pipeline stages</span>
        <span>·</span>
        <span className="capitalize">{run.source.replace(/_/g, " ")}</span>
      </div>
    </Link>
  );
}

// ── Create Run Modal ───────────────────────────────────────────

function CreateRunModal({ onClose, onCreated }: {
  onClose: () => void; onCreated: (id: string) => void;
}) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [repo, setRepo] = useState("");
  const [auxRepos, setAuxRepos] = useState<AuxiliaryRepositorySpec[]>([]);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const setAuxRepo = useCallback((i: number, patch: Partial<AuxiliaryRepositorySpec>) => {
    setAuxRepos((prev) => prev.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  }, []);

  const removeAuxRepo = useCallback((i: number) => {
    setAuxRepos((prev) => prev.filter((_, idx) => idx !== i));
  }, []);

  const handleCreate = useCallback(async () => {
    if (!title.trim()) { setError("Title is required"); return; }
    // Drop empty/incomplete aux-repo rows (a row needs an id AND a location).
    const repositories = auxRepos
      .map((r) => ({
        repository_id: r.repository_id.trim(),
        name: r.name?.trim() || undefined,
        source: r.source || "local",
        owner: r.source === "github" ? r.owner?.trim() || undefined : undefined,
        repo: r.source === "github" ? r.repo?.trim() || undefined : undefined,
        path: r.source === "local" ? r.path?.trim() || undefined : undefined,
        ref: r.source === "github" ? r.ref?.trim() || undefined : undefined,
        depth: r.source === "github" && r.depth ? r.depth : undefined,
      }))
      .filter(
        (r) =>
          r.repository_id &&
          (r.source === "github"
            ? Boolean(r.owner && r.repo)
            : Boolean(r.path))
      );
    setCreating(true);
    setError(null);
    try {
      const result = await runsApi.create({
        title: title.trim(),
        description: description.trim(),
        repository: repo.trim() || undefined,
        repositories: repositories.length > 0 ? repositories : undefined,
      });
      if (result.data?.run_id) {
        onCreated(result.data.run_id);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to create run");
    } finally {
      setCreating(false);
    }
  }, [title, description, repo, auxRepos, onCreated]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm" onClick={onClose}>
      <div
        className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 shadow-2xl w-full max-w-xl mx-4 p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-slate-900 dark:text-white">New Run</h2>
          <button onClick={onClose} className="p-1 rounded-lg text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors">
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="space-y-3">
          <div>
            <label className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1">Title *</label>
            <input
              type="text" value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="e.g. Add quantity validation"
              className="w-full px-3 py-2 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 transition-all"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1">Description</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
              placeholder="Describe the task to implement..."
              className="w-full px-3 py-2 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 transition-all resize-none"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1">Repository Path</label>
            <input
              type="text" value={repo}
              onChange={(e) => setRepo(e.target.value)}
              placeholder="/path/to/repo or GitHub URL"
              className="w-full px-3 py-2 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 transition-all"
            />
          </div>

          {/* Auxiliary repositories (Phase 20 A6) */}
          <div className="pt-1">
            <div className="flex items-center justify-between mb-1">
              <label className="block text-xs font-medium text-slate-600 dark:text-slate-400">
                Auxiliary Repositories <span className="text-slate-400 dark:text-slate-500">(optional)</span>
              </label>
              <button
                type="button"
                onClick={() => setAuxRepos((prev) => [...prev, { repository_id: "", source: "local", depth: 1 }])}
                className="text-[11px] font-medium text-primary-600 dark:text-primary-400 hover:text-primary-500 transition-colors"
              >
                + Add repo
              </button>
            </div>
            <p className="text-[11px] text-slate-400 dark:text-slate-500 mb-2">
              Additional repositories materialized via the org graph and kept isolated from the primary checkout.
            </p>
            <div className="space-y-2">
              {auxRepos.length === 0 && (
                <p className="text-[11px] text-slate-400 dark:text-slate-500 italic">
                  No auxiliary repositories — the run will target only the primary repository.
                </p>
              )}
              {auxRepos.map((aux, i) => (
                <div key={i} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800/50 p-3 space-y-2">
                  <div className="flex items-center gap-2">
                    <input
                      type="text" value={aux.repository_id}
                      onChange={(e) => setAuxRepo(i, { repository_id: e.target.value })}
                      placeholder="repo-id (stable namespace)"
                      className="flex-1 px-3 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 transition-all"
                    />
                    <select
                      value={aux.source || "local"}
                      onChange={(e) => setAuxRepo(i, { source: e.target.value as "local" | "github" })}
                      className="px-2 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 transition-all"
                    >
                      <option value="local">local</option>
                      <option value="github">github</option>
                    </select>
                    <button
                      type="button"
                      onClick={() => removeAuxRepo(i)}
                      className="p-1.5 rounded-lg text-slate-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors"
                      aria-label="Remove auxiliary repository"
                    >
                      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                      </svg>
                    </button>
                  </div>
                  {aux.source === "github" ? (
                    <div className="grid grid-cols-3 gap-2">
                      <input
                        type="text" value={aux.owner || ""}
                        onChange={(e) => setAuxRepo(i, { owner: e.target.value })}
                        placeholder="owner"
                        className="px-3 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 transition-all"
                      />
                      <input
                        type="text" value={aux.repo || ""}
                        onChange={(e) => setAuxRepo(i, { repo: e.target.value })}
                        placeholder="repo"
                        className="px-3 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 transition-all"
                      />
                      <input
                        type="text" value={aux.ref || ""}
                        onChange={(e) => setAuxRepo(i, { ref: e.target.value })}
                        placeholder="ref (optional)"
                        className="px-3 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 transition-all"
                      />
                    </div>
                  ) : (
                    <input
                      type="text" value={aux.path || ""}
                      onChange={(e) => setAuxRepo(i, { path: e.target.value })}
                      placeholder="/path/to/auxiliary/repo"
                      className="w-full px-3 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 transition-all"
                    />
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>

        {error && (
          <p className="mt-3 text-xs text-red-600 dark:text-red-400">{error}</p>
        )}

        <div className="mt-5 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-lg text-xs font-medium text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleCreate}
            disabled={creating || !title.trim()}
            className="px-4 py-2 rounded-lg text-xs font-medium bg-primary-600 text-white hover:bg-primary-500 disabled:opacity-50 disabled:cursor-not-allowed transition-all flex items-center gap-1.5"
          >
            {creating ? (
              <>
                <svg className="animate-spin w-3.5 h-3.5" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                Creating...
              </>
            ) : "Create Run"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Main Page ──────────────────────────────────────────────────

function RunsPageInner() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);

  // ── Filter, Sort & Pagination state ────────────────────────────
  const [statusFilter, setStatusFilter] = useState<string | null>(null);
  const [sortBy, setSortBy] = useState("newest");
  const [page, setPage] = useState(0);
  const [totalCount, setTotalCount] = useState(0);

  // Date range filter state
  const [datePreset, setDatePreset] = useState("all");
  const [createdAfter, setCreatedAfter] = useState<string | undefined>(undefined);
  const [createdBefore, setCreatedBefore] = useState<string | undefined>(undefined);

  // ── URL Search Params Sync ────────────────────────────────────
  const searchParams = useSearchParams();
  const router = useRouter();
  const [initialized, setInitialized] = useState(false);

  // On mount: read initial state from URL search params
  useEffect(() => {
    const sp = searchParams;
    const status = sp.get("status");
    const sort = sp.get("sort");
    const date = sp.get("date");
    const after = sp.get("after");
    const before = sp.get("before");
    const pageStr = sp.get("page");

    if (status) setStatusFilter(status);
    if (sort) setSortBy(sort);
    if (date) {
      setDatePreset(date);
      if (date === "7d" || date === "30d") {
        const days = date === "7d" ? 7 : 30;
        const d = new Date();
        d.setDate(d.getDate() - days);
        d.setHours(0, 0, 0, 0);
        setCreatedAfter(formatISO(d));
      } else if (date === "custom") {
        if (after) setCreatedAfter(after);
        if (before) setCreatedBefore(before);
      }
    }
    if (pageStr) setPage(parseInt(pageStr, 10) || 0);

    setInitialized(true);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Sync state → URL whenever filters change
  useEffect(() => {
    if (!initialized) return;
    const params = new URLSearchParams();
    if (statusFilter) params.set("status", statusFilter);
    if (sortBy !== "newest") params.set("sort", sortBy);
    if (datePreset !== "all") params.set("date", datePreset);
    if (createdAfter) params.set("after", createdAfter);
    if (createdBefore) params.set("before", createdBefore);
    if (page !== 0) params.set("page", String(page));

    const qs = params.toString();
    const newPath = qs ? `/dashboard/runs?${qs}` : `/dashboard/runs`;
    router.replace(newPath, { scroll: false });
  }, [initialized, statusFilter, sortBy, datePreset, createdAfter, createdBefore, page, router]);

  // Larger data sample for activity chart (fetched separately from paginated list)
  const [chartRuns, setChartRuns] = useState<RunSummary[]>([]);

  // Aggregate stats (unfiltered — for stat cards)
  const [stats, setStats] = useState<{
    total: number; pending: number; running: number;
    approved: number; rejected: number; needs_human_review: number;
    failed: number; cancelled: number;
  } | null>(null);

  // WebSocket for real-time run list updates
  const wsListState = useRunListWebSocket();

  // Merge WebSocket data only for unfiltered first-page view with default sort
  useEffect(() => {
    if (wsListState.runs.length > 0 && !statusFilter && page === 0 && sortBy === "newest") {
      setRuns(wsListState.runs as RunSummary[]);
      setTotalCount(wsListState.runs.length);
      setError(null);
      setLoading(false);
    }
  }, [wsListState.runs, statusFilter, page, sortBy]);

  const fetchRuns = useCallback(async () => {
    try {
      const result = await runsApi.list({
        status: statusFilter || undefined,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
        sort_by: sortBy,
        created_after: createdAfter,
        created_before: createdBefore,
      });
      setRuns(result.data || []);
      setTotalCount(result.total_count ?? result.count ?? result.data?.length ?? 0);
      if (result.stats) {
        setStats(result.stats);
      }
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load runs");
      setRuns([]);
    } finally {
      setLoading(false);
    }
  }, [statusFilter, page, sortBy, createdAfter, createdBefore]);

  // Separate fetch for chart data — only depends on filter state, not page/sort
  useEffect(() => {
    runsApi.list({
      status: statusFilter || undefined,
      limit: 200,
      offset: 0,
      sort_by: "newest",
      created_after: createdAfter,
      created_before: createdBefore,
    })
      .then((result) => setChartRuns(result.data || []))
      .catch(() => setChartRuns([]));
  }, [statusFilter, createdAfter, createdBefore]);

  useEffect(() => {
    orchestrationApi.capabilities()
      .then((r) => setCapabilities(r.data))
      .catch(() => {});
  }, []);

  // Fetch on filter/page change
  useEffect(() => {
    setLoading(true);
    fetchRuns();
  }, [fetchRuns]);

  // Only poll as fallback when WebSocket is disconnected and on unfiltered sort=newest page 0
  useEffect(() => {
    if (!autoRefresh || wsListState.connected) return;
    if (statusFilter || page !== 0 || sortBy !== "newest") return;
    const interval = setInterval(fetchRuns, 5000);
    return () => clearInterval(interval);
  }, [fetchRuns, autoRefresh, wsListState.connected, statusFilter, page, sortBy]);

  const handleCreated = useCallback((runId: string) => {
    setShowCreateModal(false);
    window.location.href = `/dashboard/runs/${runId}`;
  }, []);

  const handleFilterChange = useCallback((status: string | null) => {
    setStatusFilter(status);
    setPage(0); // Reset to first page on filter change
  }, []);

  const handleSortChange = useCallback((sort: string) => {
    setSortBy(sort);
    setPage(0); // Reset to first page on sort change
  }, []);

  const handleDateRangeChange = useCallback((preset: string, after?: string, before?: string) => {
    setDatePreset(preset);
    setCreatedAfter(after);
    setCreatedBefore(before);
    setPage(0); // Reset to first page on date range change
  }, []);

  const [filterResetKey, setFilterResetKey] = useState(0);

  const clearAllFilters = useCallback(() => {
    setStatusFilter(null);
    setSortBy("newest");
    setDatePreset("all");
    setCreatedAfter(undefined);
    setCreatedBefore(undefined);
    setPage(0);
    setFilterResetKey((k) => k + 1);
  }, []);

  // Determine if any non-default filter is active (controls visibility of clear button)
  const hasActiveFilters =
    statusFilter !== null ||
    sortBy !== "newest" ||
    datePreset !== "all" ||
    page !== 0;

  // Use API aggregate stats (unfiltered) when available, fall back to local computation
  const displayStats = stats ?? {
    total: runs.length,
    approved: runs.filter((r) => r.status === "approved").length,
    rejected: runs.filter((r) => r.status === "rejected").length,
    failed: runs.filter((r) => r.status === "failed").length,
    running: runs.filter((r) => r.status === "running").length,
    pending: 0, needs_human_review: 0, cancelled: 0,
  };
  const rejectedTotal = displayStats.rejected + displayStats.failed;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white">Runs</h1>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
            End-to-end multi-agent orchestration runs — persistent via PostgreSQL.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Link
            href="/dashboard/durability"
            className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium transition-all bg-white dark:bg-slate-800 text-slate-500 dark:text-slate-400 border border-slate-200 dark:border-slate-700 hover:border-cyan-300 dark:hover:border-cyan-700 hover:text-cyan-700 dark:hover:text-cyan-400 hover:shadow-sm"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 6.042A8.967 8.967 0 006 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 016 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 016-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0018 18a8.967 8.967 0 00-6 2.292m0-14.25v14.25" />
            </svg>
            Durability
          </Link>
          <button
            onClick={() => setAutoRefresh(!autoRefresh)}
            className={`flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium transition-all ${
              autoRefresh
                ? "bg-blue-50 text-blue-700 dark:bg-blue-900/20 dark:text-blue-400 border border-blue-200 dark:border-blue-800/50"
                : "bg-white dark:bg-slate-800 text-slate-500 dark:text-slate-400 border border-slate-200 dark:border-slate-700"
            }`}
          >
            <svg className={`w-3.5 h-3.5 ${autoRefresh ? "animate-spin" : ""}`} fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182" />
            </svg>
            Auto-refresh
          </button>
          <button
            onClick={() => setShowCreateModal(true)}
            className="px-4 py-2 rounded-lg bg-primary-600 text-white text-xs font-medium hover:bg-primary-500 transition-all flex items-center gap-1.5"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
            </svg>
            New Run
          </button>
        </div>
      </div>

      {capabilities && (
        <div className="flex flex-wrap items-center gap-2 px-4 py-3 rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700">
          <span className="text-xs font-medium text-slate-500 dark:text-slate-400 mr-1">Capabilities:</span>
          <CapBadge label={`${capabilities.stages.length} stages`} present={true} />
          <CapBadge label="Cancellation" present={capabilities.cancellation_mode !== "none"} />
          <CapBadge label="Repair" present={capabilities.repair_enabled} />
          <CapBadge label="Review" present={capabilities.review_enabled} />
          <CapBadge label="GitHub write" present={capabilities.github_write_enabled} />
          <CapBadge label={capabilities.persistence_mode} present={true} />
        </div>
      )}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard label="Total" value={displayStats.total} color="text-slate-900 dark:text-white" />
        <StatCard label="Approved" value={displayStats.approved} color="text-emerald-600 dark:text-emerald-400" />
        <StatCard label="Rejected" value={rejectedTotal} sub={`${displayStats.rejected} rejected, ${displayStats.failed} failed`} color="text-red-600 dark:text-red-400" />
        <StatCard label="Running" value={displayStats.running} color="text-blue-600 dark:text-blue-400" />
      </div>

      {/* Activity chart — computed from up to 200 recent runs in the filter range */}
      {!loading && chartRuns.length > 0 && (
        <ActivityChart runs={chartRuns} after={createdAfter} before={createdBefore} />
      )}

      {/* Date Range Filter — key force-remounts to clear internal state on reset */}
      <DateRangeFilter
        key={filterResetKey}
        current={datePreset}
        onChange={handleDateRangeChange}
      />

      {/* Filter bar + Sort + Clear */}
      <div className="flex items-center justify-between gap-3">
        <StatusFilter current={statusFilter} onChange={handleFilterChange} />
        <div className="flex items-center gap-2 shrink-0">
          <SortDropdown current={sortBy} onChange={handleSortChange} />
          {hasActiveFilters && (
            <button
              onClick={clearAllFilters}
              className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium transition-all duration-150 text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800/50 hover:bg-red-100 dark:hover:bg-red-900/30 hover:shadow-sm"
            >
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
              Clear
            </button>
          )}
        </div>
      </div>

      {loading && (
        <div className="flex items-center justify-center py-12">
          <svg className="animate-spin w-8 h-8 text-primary-500" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
        </div>
      )}

      {!loading && error && runs.length === 0 && (
        <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-8 text-center">
          <svg className="w-12 h-12 mx-auto text-slate-300 dark:text-slate-600 mb-3" fill="none" viewBox="0 0 24 24" strokeWidth={1} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636" />
          </svg>
          <p className="text-sm text-slate-500 dark:text-slate-400 mb-3">{error}</p>
          <button onClick={fetchRuns} className="px-4 py-2 rounded-lg bg-primary-600 text-white text-xs font-medium hover:bg-primary-500 transition-all">Retry</button>
        </div>
      )}

      {!loading && !error && runs.length === 0 && (
        <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-12 text-center">
          <svg className="w-16 h-16 mx-auto text-slate-200 dark:text-slate-700 mb-4" fill="none" viewBox="0 0 24 24" strokeWidth={1} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 3v13.5A2.25 2.25 0 006 18.75h13.5M3 15.75l5.25-5.25 3.75 3.75L16.5 9.75 21 14.25" />
          </svg>
          <h3 className="text-base font-semibold text-slate-700 dark:text-slate-300 mb-1">No runs yet</h3>
          <p className="text-sm text-slate-500 dark:text-slate-400 mb-4">Create a new run to start the multi-agent orchestration pipeline.</p>
          <button onClick={() => setShowCreateModal(true)} className="px-5 py-2.5 rounded-lg bg-primary-600 text-white text-sm font-medium hover:bg-primary-500 transition-all">Create First Run</button>
        </div>
      )}

      {!loading && runs.length > 0 && (
        <div className="space-y-3">
          {runs.map((run) => (
            <RunCard key={run.run_id} run={run} />
          ))}
          <PaginationBar
            page={page}
            total={totalCount}
            limit={PAGE_SIZE}
            onChange={setPage}
          />
        </div>
      )}

      {showCreateModal && (
        <CreateRunModal onClose={() => setShowCreateModal(false)} onCreated={handleCreated} />
      )}
    </div>
  );
}

export default function RunsPage() {
  return (
    <Suspense fallback={
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-slate-900 dark:text-white">Runs</h1>
            <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Loading...</p>
          </div>
        </div>
        <div className="flex items-center justify-center py-12">
          <svg className="animate-spin w-8 h-8 text-primary-500" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
        </div>
      </div>
    }>
      <RunsPageInner />
    </Suspense>
  );
}
