"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import { durabilityApi } from "@/lib/api/client";
import type { DurabilityReport } from "@/lib/api/client";

// ── Helpers ────────────────────────────────────────────────────

function verdictColor(status: string): string {
  switch (status) {
    case "approved": return "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400 border-emerald-200 dark:border-emerald-800";
    case "rejected": return "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400 border-red-200 dark:border-red-800";
    case "needs_human_review": return "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400 border-amber-200 dark:border-amber-800";
    case "failed": return "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400 border-red-200 dark:border-red-800";
    case "cancelled": return "bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-400 border-slate-200 dark:border-slate-700";
    case "running": return "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400 border-blue-200 dark:border-blue-800";
    default: return "bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-400 border-slate-200 dark:border-slate-700";
  }
}

function VerdictBadge({ status }: { status: string }) {
  const label = status.replace(/_/g, " ").toUpperCase();
  return (
    <span className={`inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded-md border ${verdictColor(status)}`}>
      {status === "running" && <span className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse" />}
      {label}
    </span>
  );
}

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

function SectionHeader({ title, sub }: { title: string; sub: string }) {
  return (
    <div className="mb-4">
      <h2 className="text-sm font-semibold text-slate-900 dark:text-white">{title}</h2>
      <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{sub}</p>
    </div>
  );
}

// ── Mode Banner ────────────────────────────────────────────────

function ModeBanner({ report }: { report: DurabilityReport }) {
  if (report.mode === "skipped") {
    return (
      <div className="flex items-start gap-3 rounded-xl bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800/50 p-4">
        <svg className="w-5 h-5 text-amber-500 shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" />
        </svg>
        <div>
          <p className="text-sm font-semibold text-amber-800 dark:text-amber-300">Report skipped</p>
          <p className="mt-0.5 text-xs text-amber-700 dark:text-amber-400">
            {report.reason || "No live LLM provider or test-named PostgreSQL configured."} Generate one with{" "}
            <code className="font-mono text-[10px] px-1 py-0.5 rounded bg-amber-100 dark:bg-amber-900/40">
              python scripts/durability_report.py --out &lt;path&gt;
            </code>
            {" "}then set <code className="font-mono text-[10px] px-1 py-0.5 rounded bg-amber-100 dark:bg-amber-900/40">DURABILITY_REPORT_PATH</code>.
          </p>
        </div>
      </div>
    );
  }
  if (report.mode === "error") {
    return (
      <div className="flex items-start gap-3 rounded-xl bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800/50 p-4">
        <svg className="w-5 h-5 text-red-500 shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
        </svg>
        <div>
          <p className="text-sm font-semibold text-red-800 dark:text-red-300">Live run crashed</p>
          <p className="mt-0.5 text-xs text-red-700 dark:text-red-400 font-mono">{report.error}</p>
        </div>
      </div>
    );
  }
  const passed = report.passed ?? false;
  return (
    <div className={`flex items-start gap-3 rounded-xl border p-4 ${
      passed
        ? "bg-emerald-50 dark:bg-emerald-900/20 border-emerald-200 dark:border-emerald-800/50"
        : "bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800/50"
    }`}>
      <svg className={`w-5 h-5 shrink-0 mt-0.5 ${passed ? "text-emerald-500" : "text-red-500"}`} fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
        {passed ? (
          <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
        ) : (
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
        )}
      </svg>
      <div className="flex-1">
        <p className={`text-sm font-semibold ${passed ? "text-emerald-800 dark:text-emerald-300" : "text-red-800 dark:text-red-300"}`}>
          {passed ? "All terminal gates passed" : `${(report.gates || []).length} gate failure(s)`}
        </p>
        {!passed && (report.gates || []).length > 0 && (
          <ul className="mt-2 space-y-1">
            {report.gates!.map((g, i) => (
              <li key={i} className="text-xs text-red-700 dark:text-red-400 flex items-start gap-1.5">
                <span className="mt-0.5 w-1 h-1 rounded-full bg-red-500 shrink-0" />
                {g}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

// ── Run API card ──────────────────────────────────────────────

function RunApiCard({ run }: { run: NonNullable<DurabilityReport["run_api"]> }) {
  return (
    <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 overflow-hidden">
      <div className="px-5 py-4 border-b border-slate-200 dark:border-slate-700 flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-slate-900 dark:text-white">Run API</h3>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">Real <code className="font-mono">execute_run</code> via HTTP</p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs font-mono font-semibold text-slate-900 dark:text-white">{run.run_id}</span>
          <VerdictBadge status={run.run_status} />
        </div>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 p-5">
        <StatCard label="Handoffs" value={run.handoffs} color="text-slate-900 dark:text-white" />
        <StatCard label="Decisions" value={run.decisions} color="text-slate-900 dark:text-white" />
        <StatCard label="Consensus (API)" value={run.consensus_via_api} color="text-violet-600 dark:text-violet-400" />
        <StatCard label="Consensus (recovered)" value={run.consensus_recovered} color="text-violet-600 dark:text-violet-400" />
      </div>
      <div className="px-5 pb-4">
        <div className="flex items-center gap-2 text-[11px] text-slate-500 dark:text-slate-400">
          <span>Runs in <code className="font-mono">runs</code> table:</span>
          <span className="font-semibold text-slate-900 dark:text-white">{run.runs_in_table}</span>
          <span className="ml-auto text-emerald-600 dark:text-emerald-400">
            persisted via PostgresRunStore
          </span>
        </div>
      </div>
    </div>
  );
}

// ── Goal API card ─────────────────────────────────────────────

function GoalApiCard({ goal }: { goal: NonNullable<DurabilityReport["goal_api"]> }) {
  return (
    <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 overflow-hidden">
      <div className="px-5 py-4 border-b border-slate-200 dark:border-slate-700 flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-slate-900 dark:text-white">Goal API</h3>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">Real autonomous loop via <code className="font-mono">POST /api/v1/autonomy/run</code></p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs font-mono font-semibold text-slate-900 dark:text-white">{goal.goal_id}</span>
          <VerdictBadge status={goal.goal_state} />
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 p-5">
        <StatCard label="Goal runs" value={goal.goal_runs.length} sub={`${goal.goal_runs.length} persisted run(s)`} color="text-slate-900 dark:text-white" />
        <StatCard label="Latest run" value={goal.goal_latest_run_status.replace(/_/g, " ")} color={goal.goal_latest_run_status === "approved" ? "text-emerald-600 dark:text-emerald-400" : goal.goal_latest_run_status === "failed" || goal.goal_latest_run_status === "rejected" ? "text-red-600 dark:text-red-400" : "text-slate-900 dark:text-white"} />
        <StatCard label="Handoffs" value={goal.goal_handoffs} color="text-slate-900 dark:text-white" />
        <StatCard label="Decisions" value={goal.goal_decisions} color="text-slate-900 dark:text-white" />
        <StatCard label="Consensus" value={goal.goal_consensus} color="text-violet-600 dark:text-violet-400" />
        <StatCard label="Recovery state" value={goal.goal_recovered.replace(/_/g, " ")} sub="restart rehydration" color="text-blue-600 dark:text-blue-400" />
      </div>

      {/* Per-run audit trail */}
      {goal.goal_runs.length > 0 && (
        <div className="px-5 pb-5">
          <p className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-2">
            Run audit trail (retry attempts included)
          </p>
          <div className="space-y-1.5">
            {goal.goal_runs.map((runId) => {
              const status = goal.goal_run_statuses?.[runId] || "unknown";
              return (
                <Link
                  key={runId}
                  href={`/dashboard/runs/${runId}`}
                  className="flex items-center justify-between px-3 py-2 rounded-lg border border-slate-200 dark:border-slate-700 hover:border-primary-300 dark:hover:border-primary-700 hover:bg-slate-50 dark:hover:bg-slate-700/30 transition-all group"
                >
                  <span className="text-xs font-mono text-slate-900 dark:text-white group-hover:text-primary-600 dark:group-hover:text-primary-400 transition-colors">
                    {runId}
                  </span>
                  <VerdictBadge status={status} />
                </Link>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────

export default function DurabilityPage() {
  const [report, setReport] = useState<DurabilityReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [missing, setMissing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastChecked, setLastChecked] = useState<string | null>(null);

  const fetchReport = useCallback(async () => {
    try {
      const result = await durabilityApi.report();
      setReport(result.data);
      setMissing(false);
      setError(null);
    } catch (err: unknown) {
      setReport(null);
      if (err instanceof Error && err.message.includes("404")) {
        setMissing(true);
        setError(null);
      } else {
        setMissing(false);
        setError(err instanceof Error ? err.message : "Failed to load report");
      }
    } finally {
      setLoading(false);
      setLastChecked(new Date().toLocaleTimeString());
    }
  }, []);

  useEffect(() => {
    fetchReport();
  }, [fetchReport]);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white">Durability Report</h1>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
            Live-LLM end-to-end validation: raw-HTTP run + autonomous goal against a real provider &amp; PostgreSQL.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {lastChecked && (
            <span className="text-[11px] text-slate-400 dark:text-slate-500">
              Last checked {lastChecked}
            </span>
          )}
          <button
            onClick={fetchReport}
            disabled={loading}
            className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium transition-all bg-primary-600 text-white hover:bg-primary-500 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <svg className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182" />
            </svg>
            Refresh
          </button>
        </div>
      </div>

      {loading && (
        <div className="flex items-center justify-center py-16">
          <svg className="animate-spin w-8 h-8 text-primary-500" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
        </div>
      )}

      {!loading && missing && (
        <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-12 text-center">
          <svg className="w-16 h-16 mx-auto text-slate-200 dark:text-slate-700 mb-4" fill="none" viewBox="0 0 24 24" strokeWidth={1} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
          </svg>
          <h3 className="text-base font-semibold text-slate-700 dark:text-slate-300 mb-1">No durability report yet</h3>
          <p className="text-sm text-slate-500 dark:text-slate-400 max-w-md mx-auto mb-4">
            Run the live validation to generate one (requires a live LLM provider + test-named PostgreSQL;
            skips cleanly without them), then set <code className="font-mono text-[11px] px-1 py-0.5 rounded bg-slate-100 dark:bg-slate-700">DURABILITY_REPORT_PATH</code>:
          </p>
          <div className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-slate-50 dark:bg-slate-700/50 border border-slate-200 dark:border-slate-700 font-mono text-[11px] text-slate-600 dark:text-slate-300">
            <span className="text-emerald-500">$</span> python scripts/durability_report.py --out durability_report.json
          </div>
          <div className="mt-5">
            <button onClick={fetchReport} className="px-5 py-2.5 rounded-lg bg-primary-600 text-white text-sm font-medium hover:bg-primary-500 transition-all">
              Check again
            </button>
          </div>
        </div>
      )}

      {!loading && error && (
        <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-8 text-center">
          <p className="text-sm text-slate-500 dark:text-slate-400 mb-3">{error}</p>
          <button onClick={fetchReport} className="px-4 py-2 rounded-lg bg-primary-600 text-white text-xs font-medium hover:bg-primary-500 transition-all">Retry</button>
        </div>
      )}

      {!loading && !missing && !error && report && (
        <>
          <ModeBanner report={report} />

          {report.mode === "live" && report.run_api && report.goal_api && (
            <>
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <div>
                  <SectionHeader title="Run API" sub="Raw-HTTP execute_run path" />
                  <RunApiCard run={report.run_api} />
                </div>
                <div>
                  <SectionHeader title="Goal API" sub="Autonomous goal loop path" />
                  <GoalApiCard goal={report.goal_api} />
                </div>
              </div>

              {/* Raw JSON viewer */}
              <details className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 overflow-hidden group">
                <summary className="px-5 py-3.5 flex items-center justify-between cursor-pointer text-sm font-medium text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-700/30 transition-colors">
                  <span>Raw report JSON</span>
                  <svg className="w-4 h-4 text-slate-400 group-open:rotate-180 transition-transform" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
                  </svg>
                </summary>
                <pre className="px-5 py-4 overflow-x-auto text-[11px] leading-relaxed font-mono text-slate-600 dark:text-slate-300 bg-slate-50 dark:bg-slate-900/40 border-t border-slate-200 dark:border-slate-700">
{JSON.stringify(report, null, 2)}
                </pre>
              </details>
            </>
          )}
        </>
      )}
    </div>
  );
}
