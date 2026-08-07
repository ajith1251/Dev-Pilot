"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import { operationsApi, providersApi, runsApi } from "@/lib/api/client";
import type {
  OperationsStatusData,
  OperationsMetricsData,
  StartupValidationData,
  ProviderHealthData,
  ProviderMetricsData,
  RunListStats,
  SubsystemStatus,
} from "@/lib/api/client";

// ── Helpers ────────────────────────────────────────────────────

function statusColor(status: SubsystemStatus | string): string {
  switch (status) {
    case "ok": return "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400 border-emerald-200 dark:border-emerald-800";
    case "degraded": return "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400 border-amber-200 dark:border-amber-800";
    case "error": return "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400 border-red-200 dark:border-red-800";
    default: return "bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-400 border-slate-200 dark:border-slate-700";
  }
}

function StatusBadge({ status }: { status: SubsystemStatus | string }) {
  const normalized = (status || "unknown") as SubsystemStatus;
  const label = normalized.replace(/_/g, " ").toUpperCase();
  return (
    <span className={`inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded-md border ${statusColor(normalized)}`}>
      {normalized === "ok" && <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />}
      {normalized === "degraded" && <span className="w-1.5 h-1.5 rounded-full bg-amber-500" />}
      {normalized === "error" && <span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" />}
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

function formatUptime(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 60) return `${seconds.toFixed(0)}s`;
  if (seconds < 3600) return `${(seconds / 60).toFixed(1)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

function formatDurationMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${ms.toFixed(0)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function formatDurationSec(sec: number | null | undefined): string {
  if (sec === null || sec === undefined) return "—";
  if (sec < 60) return `${sec.toFixed(1)}s`;
  if (sec < 3600) return `${(sec / 60).toFixed(1)}m`;
  return `${(sec / 3600).toFixed(1)}h`;
}

// ── Subsystem card ─────────────────────────────────────────────

const subsystemLabels: Record<string, { label: string; blurb: string }> = {
  providers: { label: "Providers", blurb: "LLM router health & circuit state" },
  database: { label: "PostgreSQL", blurb: "Database connectivity" },
  graph: { label: "Knowledge Graph", blurb: "Engineering Knowledge Graph availability" },
  repository_memory: { label: "Repository Memory", blurb: "Memory service availability" },
  inference: { label: "Inference", blurb: "Routing + active provider" },
  orchestration: { label: "Orchestration", blurb: "Run throughput & active runs" },
  websocket: { label: "WebSocket", blurb: "Live connections per channel" },
  resources: { label: "Resources", blurb: "Process memory & open tasks" },
};

function SubsystemCard({ name, entry }: {
  name: string; entry: { status: SubsystemStatus; detail: Record<string, unknown> };
}) {
  const meta = subsystemLabels[name] || { label: name, blurb: "" };
  const detail = entry.detail || {};
  const subBits: string[] = [];
  if (name === "database") {
    if (detail.configured === false) subBits.push("not configured");
    if (detail.connected === true) subBits.push("connected");
    if (detail.server_version) subBits.push(`v${detail.server_version}`);
  } else if (name === "websocket") {
    subBits.push(`${detail.active_connections ?? 0} connections`);
  } else if (name === "resources") {
    if (typeof detail.memory_mb === "number") subBits.push(`${detail.memory_mb.toFixed(0)} MiB`);
    if (typeof detail.open_tasks === "number") subBits.push(`${detail.open_tasks} tasks`);
  } else if (name === "providers") {
    subBits.push(`${detail.configured_count ?? 0} configured`);
    if (detail.active_provider) subBits.push(`active: ${detail.active_provider}`);
  } else if (name === "inference") {
    if (detail.active_provider) subBits.push(`active: ${detail.active_provider}`);
    if (detail.routing_enabled === false) subBits.push("routing disabled");
  } else if (name === "graph") {
    if (detail.node_count != null) subBits.push(`${detail.node_count} nodes`);
    if (detail.edge_count != null) subBits.push(`${detail.edge_count} edges`);
  } else if (name === "orchestration") {
    if (typeof detail.completed_total === "number") subBits.push(`${detail.completed_total} completed`);
    if (typeof detail.active_runs === "number") subBits.push(`${detail.active_runs} active`);
  } else if (name === "repository_memory") {
    if (detail.available === false) subBits.push("unavailable");
  }
  const errorText = typeof detail.error === "string" ? detail.error : null;

  return (
    <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-5 transition-all duration-200 hover:shadow-md">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-sm font-semibold text-slate-900 dark:text-white">{meta.label}</h3>
        <StatusBadge status={entry.status} />
      </div>
      <p className="text-[11px] text-slate-500 dark:text-slate-400">{meta.blurb}</p>
      {(subBits.length > 0 || errorText) && (
        <div className="mt-3 space-y-1">
          {subBits.map((bit) => (
            <p key={bit} className="text-[11px] font-mono text-slate-600 dark:text-slate-300">{bit}</p>
          ))}
          {errorText && (
            <p className="text-[11px] font-mono text-red-500 truncate" title={errorText}>{errorText}</p>
          )}
        </div>
      )}
    </div>
  );
}

// ── Startup validation panel ───────────────────────────────────

function StartupValidationPanel({ validation }: { validation: StartupValidationData }) {
  if (validation.findings.length === 0) {
    return (
      <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-5 flex items-center justify-between">
        <div>
          <p className="text-sm font-semibold text-slate-900 dark:text-white">Configuration validation</p>
          <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
            No startup configuration findings — settings are coherent.
          </p>
        </div>
        <span className="inline-flex items-center gap-1.5 text-[11px] font-semibold px-2.5 py-1 rounded-md bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400 border border-emerald-200 dark:border-emerald-800">
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
          CLEAN
        </span>
      </div>
    );
  }
  return (
    <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-5">
      <div className="flex items-center justify-between mb-3">
        <div>
          <p className="text-sm font-semibold text-slate-900 dark:text-white">Configuration validation</p>
          <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
            {validation.error_count} error(s) · {validation.warning_count} warning(s)
            {validation.strict ? " · strict mode: fail-fast" : ""}
          </p>
        </div>
        <StatusBadge status={validation.error_count > 0 ? "error" : validation.warning_count > 0 ? "degraded" : "ok"} />
      </div>
      <div className="space-y-2">
        {validation.findings.map((f, i) => (
          <div key={i} className="flex items-start gap-2.5 rounded-lg bg-slate-50 dark:bg-slate-900/40 border border-slate-100 dark:border-slate-800 px-3 py-2.5">
            <span className={`mt-0.5 inline-block w-2 h-2 rounded-full shrink-0 ${f.severity === "error" ? "bg-red-500" : "bg-amber-500"}`} />
            <div className="min-w-0">
              <p className="text-[11px] font-mono text-slate-500 dark:text-slate-400">{f.code}</p>
              <p className="mt-0.5 text-[11px] text-slate-600 dark:text-slate-300">{f.message}</p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Main page ──────────────────────────────────────────────────

export default function OperationsPage() {
  const [status, setStatus] = useState<OperationsStatusData | null>(null);
  const [metrics, setMetrics] = useState<OperationsMetricsData | null>(null);
  const [validation, setValidation] = useState<StartupValidationData | null>(null);
  const [health, setHealth] = useState<ProviderHealthData | null>(null);
  const [providerMetrics, setProviderMetrics] = useState<ProviderMetricsData | null>(null);
  const [runStats, setRunStats] = useState<RunListStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastChecked, setLastChecked] = useState<string | null>(null);

  const fetchAll = useCallback(async () => {
    try {
      const [s, m, v, h, pm, runs] = await Promise.all([
        operationsApi.status(),
        operationsApi.metrics(),
        operationsApi.startupValidation(),
        providersApi.health(),
        providersApi.metrics(),
        runsApi.list({ limit: 1 }),
      ]);
      setStatus(s.data);
      setMetrics(m.data);
      setValidation(v.data);
      setHealth(h.data);
      setProviderMetrics(pm.data);
      setRunStats(runs.stats ?? null);
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load operations data");
    } finally {
      setLoading(false);
      setLastChecked(new Date().toLocaleTimeString());
    }
  }, []);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  const ready = status?.summary.ready ?? false;
  const totals = providerMetrics?.totals;
  const resources = metrics?.resources;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white">Operations</h1>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
            Live system health, reliability metrics &amp; resource utilization for this deployment.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {lastChecked && (
            <span className="text-[11px] text-slate-400 dark:text-slate-500">
              Last checked {lastChecked}
            </span>
          )}
          <button
            onClick={fetchAll}
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

      {/* Readiness banner */}
      {status && (
        <div className={`rounded-xl border px-5 py-4 flex items-center justify-between flex-wrap gap-3 transition-all ${
          ready
            ? "bg-emerald-50 dark:bg-emerald-900/20 border-emerald-200 dark:border-emerald-800"
            : "bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800"
        }`}>
          <div className="flex items-center gap-3">
            <div className={`w-10 h-10 rounded-xl flex items-center justify-center ${ready ? "bg-emerald-500 text-white" : "bg-red-500 text-white animate-pulse"}`}>
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                {ready ? (
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                ) : (
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" />
                )}
              </svg>
            </div>
            <div>
              <p className="text-sm font-semibold text-slate-900 dark:text-white">
                {ready ? "System ready" : "System not ready"}
              </p>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                {ready
                  ? "All required subsystems are healthy."
                  : `Unhealthy: ${Object.keys(status.summary.error_subsystems).join(", ")}`}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2 text-[11px] font-mono text-slate-500 dark:text-slate-400">
            {status.summary.checked_at && <span>{new Date(status.summary.checked_at).toLocaleTimeString()}</span>}
            <span>·</span>
            <span>{Object.keys(status.subsystems).length} subsystems</span>
          </div>
        </div>
      )}

      {loading && (
        <div className="flex items-center justify-center py-16">
          <svg className="animate-spin w-8 h-8 text-primary-500" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
        </div>
      )}

      {!loading && error && (
        <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-8 text-center">
          <p className="text-sm text-slate-500 dark:text-slate-400 mb-3">{error}</p>
          <button onClick={fetchAll} className="px-4 py-2 rounded-lg bg-primary-600 text-white text-xs font-medium hover:bg-primary-500 transition-all">Retry</button>
        </div>
      )}

      {!loading && !error && status && metrics && validation && (
        <>
          {/* Key stats */}
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4">
            <StatCard
              label="Active runs"
              value={metrics.runs.active}
              sub={`${metrics.runs.started_total} started · ${metrics.runs.completed_total} completed`}
              color="text-slate-900 dark:text-white"
            />
            <StatCard
              label="Throughput"
              value={`${metrics.runs.throughput_per_minute}/min`}
              sub="completed runs (60s window)"
              color="text-slate-900 dark:text-white"
            />
            <StatCard
              label="Avg run duration"
              value={formatDurationMs(metrics.runs.avg_duration_ms)}
              sub={metrics.runs.recent_duration_ms.length > 0 ? `${metrics.runs.recent_duration_ms.length} recent runs` : "no completed runs"}
              color="text-slate-900 dark:text-white"
            />
            <StatCard
              label="Failovers"
              value={totals?.failovers ?? 0}
              sub={`${totals?.retries ?? 0} retries · ${totals?.recoveries ?? 0} recoveries`}
              color={totals && totals.failovers > 0 ? "text-amber-600 dark:text-amber-400" : "text-slate-900 dark:text-white"}
            />
            <StatCard
              label="Active WS"
              value={resources?.active_ws_connections ?? 0}
              sub="live WebSocket connections"
              color="text-slate-900 dark:text-white"
            />
            <StatCard
              label="Memory"
              value={resources?.memory_mb != null ? `${resources.memory_mb.toFixed(0)} MiB` : "—"}
              sub={`${resources?.open_tasks ?? 0} open tasks`}
              color="text-slate-900 dark:text-white"
            />
          </div>

          {/* Run queue summary */}
          {runStats && (
            <div>
              <SectionHeader title="Run queue" sub="Live run-state breakdown (from the runs API)" />
              <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-5">
                <div className="flex flex-wrap items-center gap-4">
                  {([
                    ["total", runStats.total, "text-slate-900 dark:text-white"],
                    ["running", runStats.running, "text-blue-600 dark:text-blue-400"],
                    ["pending", runStats.pending, "text-slate-500 dark:text-slate-400"],
                    ["approved", runStats.approved, "text-emerald-600 dark:text-emerald-400"],
                    ["needs review", runStats.needs_human_review, "text-amber-600 dark:text-amber-400"],
                    ["failed", runStats.failed, "text-red-500"],
                    ["cancelled", runStats.cancelled, "text-slate-400 dark:text-slate-500"],
                  ] as Array<[string, number, string]>).map(([label, value, color]) => (
                    <div key={label} className="flex items-baseline gap-2">
                      <span className={`text-xl font-bold ${color}`}>{value}</span>
                      <span className="text-[11px] text-slate-500 dark:text-slate-400 uppercase tracking-wider">{label}</span>
                    </div>
                  ))}
                  <Link
                    href="/dashboard/runs"
                    className="ml-auto text-[11px] font-medium text-primary-600 dark:text-primary-400 hover:underline"
                  >
                    Open runs →
                  </Link>
                </div>
              </div>
            </div>
          )}

          {/* Subsystem matrix */}
          <div>
            <SectionHeader title="Subsystem status" sub="Readiness matrix — same truth served by GET /health/ready" />
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
              {Object.entries(status.subsystems).map(([name, entry]) => (
                <SubsystemCard key={name} name={name} entry={entry} />
              ))}
            </div>
          </div>

          {/* Provider health strip */}
          {health && (
            <div>
              <SectionHeader title="Provider status" sub="Configured providers and circuit states (from the provider router)" />
              <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 overflow-hidden">
                <table className="w-full text-left">
                  <thead>
                    <tr className="border-b border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/40">
                      <th className="px-4 py-2.5 text-[10px] font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">Provider</th>
                      <th className="px-4 py-2.5 text-[10px] font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">Status</th>
                      <th className="px-4 py-2.5 text-[10px] font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">Circuit</th>
                      <th className="px-4 py-2.5 text-[10px] font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">Probes</th>
                      <th className="px-4 py-2.5 text-[10px] font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">Recoveries</th>
                      <th className="px-4 py-2.5 text-[10px] font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">Latency</th>
                    </tr>
                  </thead>
                  <tbody>
                    {health.providers.map((p) => (
                      <tr key={p.name} className="border-b border-slate-100 dark:border-slate-800 last:border-0">
                        <td className="px-4 py-2.5">
                          <div className="flex items-center gap-2">
                            <span className="text-[11px] font-mono font-semibold text-slate-800 dark:text-slate-200">{p.name}</span>
                            {p.name === health.active_provider && (
                              <span className="inline-flex items-center gap-1 text-[10px] font-semibold px-1.5 py-0.5 rounded-md bg-primary-50 dark:bg-primary-900/30 text-primary-700 dark:text-primary-300 border border-primary-200 dark:border-primary-800">
                                <span className="w-1 h-1 rounded-full bg-primary-500" />ACTIVE
                              </span>
                            )}
                          </div>
                        </td>
                        <td className="px-4 py-2.5"><StatusBadge status={p.status} /></td>
                        <td className="px-4 py-2.5">
                          <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-md border ${p.circuit_state === "open" ? "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400 border-red-200 dark:border-red-800" : p.circuit_state === "half_open" ? "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400 border-amber-200 dark:border-amber-800" : "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400 border-emerald-200 dark:border-emerald-800"}`}>
                            {p.circuit_state.replace(/_/g, " ").toUpperCase()}
                          </span>
                        </td>
                        <td className="px-4 py-2.5 text-[11px] font-mono text-slate-600 dark:text-slate-300">
                          {p.health.probes ?? 0} <span className="text-slate-400">/ {p.health.failed_probes ?? 0} failed</span>
                        </td>
                        <td className="px-4 py-2.5 text-[11px] font-mono text-slate-600 dark:text-slate-300">{p.health.recoveries ?? 0}</td>
                        <td className="px-4 py-2.5 text-[11px] font-mono text-slate-600 dark:text-slate-300">
                          {p.health.avg_latency_ms != null ? `${p.health.avg_latency_ms.toFixed(0)}ms` : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Autonomy + repositories strip */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <div>
              <SectionHeader title="Autonomous execution" sub="Goals tracked by the autonomy service" />
              <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-5">
                <div className="grid grid-cols-3 gap-3">
                  <StatCard label="Active goals" value={metrics.autonomy.active_goals} color="text-slate-900 dark:text-white" />
                  <StatCard label="Goals total" value={metrics.autonomy.goals_total} color="text-slate-900 dark:text-white" />
                  <StatCard label="Avg duration" value={formatDurationSec(metrics.autonomy.avg_duration_seconds)} color="text-slate-900 dark:text-white" />
                </div>
                {metrics.autonomy.recent_states.length > 0 && (
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {metrics.autonomy.recent_states.slice(-8).map((s, i) => (
                      <span key={i} className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-slate-50 dark:bg-slate-700/50 border border-slate-200 dark:border-slate-700 text-slate-500 dark:text-slate-400">
                        {s}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
            <div>
              <SectionHeader title="Repository processing" sub="Per-repository analysis time (multi-repo runs)" />
              <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-5">
                <div className="grid grid-cols-2 gap-3">
                  <StatCard label="Processed" value={metrics.repositories.processed_total} color="text-slate-900 dark:text-white" />
                  <StatCard label="Avg processing" value={metrics.repositories.avg_processing_seconds != null ? `${metrics.repositories.avg_processing_seconds.toFixed(1)}s` : "—"} color="text-slate-900 dark:text-white" />
                </div>
                {metrics.repositories.recent_seconds.length > 0 && (
                  <div className="mt-3">
                    <p className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-1.5">Recent (s)</p>
                    <div className="flex items-end gap-1 h-10">
                      {metrics.repositories.recent_seconds.slice(-16).map((sec, i) => (
                        <div
                          key={i}
                          className="flex-1 rounded-t bg-primary-500/70 hover:bg-primary-500 transition-colors"
                          style={{ height: `${Math.min(100, Math.max(6, (sec / (Math.max(...metrics.repositories.recent_seconds.slice(-16)) || 1)) * 100))}%` }}
                          title={`${sec.toFixed(1)}s`}
                        />
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Startup validation */}
          <div>
            <SectionHeader title="Configuration" sub="Startup validation findings — fail-fast diagnostics, live-revalidated" />
            <StartupValidationPanel validation={validation} />
          </div>
        </>
      )}
    </div>
  );
}
