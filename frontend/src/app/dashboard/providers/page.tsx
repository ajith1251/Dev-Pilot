"use client";

import { useState, useEffect, useCallback } from "react";
import { providersApi } from "@/lib/api/client";
import type {
  ProviderOverviewData,
  ProviderHealthData,
  ProviderMetricsData,
  ProviderConfigData,
  ProviderEntryOverview,
  ProviderStatus,
  FailoverEvent,
} from "@/lib/api/client";

// ── Helpers ────────────────────────────────────────────────────

function statusColor(status: ProviderStatus): string {
  switch (status) {
    case "healthy": return "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400 border-emerald-200 dark:border-emerald-800";
    case "degraded": return "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400 border-amber-200 dark:border-amber-800";
    case "unhealthy": return "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400 border-red-200 dark:border-red-800";
    default: return "bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-400 border-slate-200 dark:border-slate-700";
  }
}

function circuitColor(state: string): string {
  switch (state) {
    case "closed": return "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400 border-emerald-200 dark:border-emerald-800";
    case "half_open": return "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400 border-amber-200 dark:border-amber-800";
    case "open": return "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400 border-red-200 dark:border-red-800";
    default: return "bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-400 border-slate-200 dark:border-slate-700";
  }
}

function StatusBadge({ status }: { status: ProviderStatus | string }) {
  const normalized = (status || "unknown") as ProviderStatus;
  const label = normalized.replace(/_/g, " ").toUpperCase();
  return (
    <span className={`inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded-md border ${statusColor(normalized)}`}>
      {normalized === "healthy" && <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />}
      {normalized === "degraded" && <span className="w-1.5 h-1.5 rounded-full bg-amber-500" />}
      {normalized === "unhealthy" && <span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" />}
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

function formatRate(rate: number | null | undefined): string {
  if (rate === null || rate === undefined) return "—";
  return `${(rate * 100).toFixed(1)}%`;
}

function formatMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  return `${ms.toFixed(0)}ms`;
}

function formatUptime(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 60) return `${seconds.toFixed(0)}s`;
  if (seconds < 3600) return `${(seconds / 60).toFixed(1)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

function formatEventTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString();
}

// ── Provider card ──────────────────────────────────────────────

function ProviderCard({ provider }: { provider: ProviderEntryOverview }) {
  return (
    <div className={`rounded-xl bg-white dark:bg-slate-800 border overflow-hidden transition-all duration-200 hover:shadow-md ${
      provider.active
        ? "border-primary-300 dark:border-primary-700 ring-1 ring-primary-200 dark:ring-primary-800/50"
        : "border-slate-200 dark:border-slate-700"
    }`}>
      <div className="px-5 py-4 border-b border-slate-200 dark:border-slate-700 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className={`w-9 h-9 rounded-lg flex items-center justify-center text-xs font-bold uppercase ${
            provider.active
              ? "bg-primary-600 text-white"
              : "bg-slate-100 dark:bg-slate-700 text-slate-500 dark:text-slate-400"
          }`}>
            {provider.name.slice(0, 2)}
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-semibold text-slate-900 dark:text-white">{provider.name}</h3>
              {provider.active && (
                <span className="inline-flex items-center gap-1 text-[10px] font-semibold px-1.5 py-0.5 rounded-md bg-primary-50 dark:bg-primary-900/30 text-primary-700 dark:text-primary-300 border border-primary-200 dark:border-primary-800">
                  <span className="w-1 h-1 rounded-full bg-primary-500" />
                  ACTIVE
                </span>
              )}
            </div>
            <p className="mt-0.5 text-[11px] font-mono text-slate-500 dark:text-slate-400">
              {provider.default_model || "no default model"}
            </p>
          </div>
        </div>
        <StatusBadge status={provider.status} />
      </div>

      <div className="grid grid-cols-3 gap-3 p-5">
        <StatCard label="Priority" value={`#${provider.priority}`} color="text-slate-900 dark:text-white" />
        <StatCard label="Success rate" value={formatRate(provider.health.success_rate)} color="text-slate-900 dark:text-white" />
        <StatCard label="Avg latency" value={formatMs(provider.health.avg_latency_ms)} color="text-slate-900 dark:text-white" />
      </div>

      <div className="px-5 pb-4 space-y-2.5">
        <div className="flex items-center justify-between text-[11px] text-slate-500 dark:text-slate-400">
          <span>Circuit</span>
          <span className={`inline-flex items-center gap-1 text-[10px] font-semibold px-2 py-0.5 rounded-md border ${circuitColor(provider.circuit.state)}`}>
            {provider.circuit.state.replace(/_/g, " ").toUpperCase()}
            <span className="font-normal opacity-70">
              {provider.circuit.consecutive_failures}/{provider.circuit.failure_threshold}
            </span>
          </span>
        </div>
        <div className="flex items-center justify-between text-[11px] text-slate-500 dark:text-slate-400">
          <span>Requests</span>
          <span className="font-mono text-slate-700 dark:text-slate-300">
            {provider.health.successful_requests} ok / {provider.health.failed_requests} fail
          </span>
        </div>
        <div className="flex items-center justify-between text-[11px] text-slate-500 dark:text-slate-400">
          <span>Retries / failovers</span>
          <span className="font-mono text-slate-700 dark:text-slate-300">
            {provider.health.retries} / {provider.health.failovers}
          </span>
        </div>
        <div className="flex items-center justify-between text-[11px] text-slate-500 dark:text-slate-400">
          <span>Uptime</span>
          <span className="font-mono text-slate-700 dark:text-slate-300">{formatUptime(provider.health.uptime_seconds)}</span>
        </div>
        <div className="flex items-center justify-between text-[11px] text-slate-500 dark:text-slate-400">
          <span>Configured</span>
          <span className={provider.configured ? "text-emerald-600 dark:text-emerald-400" : "text-red-500"}>
            {provider.configured ? "yes" : "no"}
          </span>
        </div>
      </div>
    </div>
  );
}

// ── Failover events table ─────────────────────────────────────

function FailoverEvents({ events }: { events: FailoverEvent[] }) {
  if (events.length === 0) {
    return (
      <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-8 text-center">
        <p className="text-sm text-slate-500 dark:text-slate-400">
          No failover events recorded yet — the router has not needed to switch providers.
        </p>
      </div>
    );
  }
  return (
    <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 overflow-hidden">
      <table className="w-full text-left">
        <thead>
          <tr className="border-b border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/40">
            <th className="px-4 py-2.5 text-[10px] font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">Time</th>
            <th className="px-4 py-2.5 text-[10px] font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">From</th>
            <th className="px-4 py-2.5 text-[10px] font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">To</th>
            <th className="px-4 py-2.5 text-[10px] font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">Reason</th>
          </tr>
        </thead>
        <tbody>
          {events.map((ev, i) => (
            <tr key={i} className="border-b border-slate-100 dark:border-slate-800 last:border-0">
              <td className="px-4 py-2.5 text-[11px] font-mono text-slate-500 dark:text-slate-400">{formatEventTime(ev.timestamp)}</td>
              <td className="px-4 py-2.5 text-[11px] font-mono text-red-500">{ev.from}</td>
              <td className="px-4 py-2.5 text-[11px] font-mono text-emerald-600 dark:text-emerald-400">{ev.to}</td>
              <td className="px-4 py-2.5 text-[11px] text-slate-600 dark:text-slate-300">{ev.reason}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Config panel ──────────────────────────────────────────────

function ConfigRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-center justify-between py-2 border-b border-slate-100 dark:border-slate-800 last:border-0">
      <span className="text-[11px] text-slate-500 dark:text-slate-400">{label}</span>
      <span className={`text-[11px] font-semibold text-slate-800 dark:text-slate-200 ${mono ? "font-mono" : ""}`}>{value}</span>
    </div>
  );
}

function ConfigPanel({ config }: { config: ProviderConfigData }) {
  const providerNames = Object.keys(config.providers);
  return (
    <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-5">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div>
          <p className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-2">Routing</p>
          <ConfigRow label="Enabled" value={config.routing_enabled ? "yes" : "no"} />
          <ConfigRow label="Timeout" value={`${config.timeout_seconds}s`} mono />
          <ConfigRow label="Retries" value={String(config.retry.max_retries)} />
          <ConfigRow label="Backoff" value={`${config.retry.base_backoff_seconds}s → ${config.retry.max_backoff_seconds}s`} mono />
          <ConfigRow label="Circuit threshold" value={String(config.circuit_breaker.failure_threshold)} />
          <ConfigRow label="Circuit cooldown" value={`${config.circuit_breaker.cooldown_seconds}s`} mono />
          <ConfigRow label="Half-open probes" value={String(config.circuit_breaker.half_open_max_calls)} />
          <ConfigRow label="Health window" value={String(config.health.window)} />
          <ConfigRow label="Degraded <" value={`${(config.health.degraded_success_rate * 100).toFixed(0)}%`} />
          <ConfigRow label="Unhealthy <" value={`${(config.health.unhealthy_success_rate * 100).toFixed(0)}%`} />
        </div>
        <div>
          <p className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-2">Priority</p>
          <div className="flex flex-wrap gap-1.5 mb-4">
            {config.provider_priority.map((name, i) => (
              <span key={name} className="inline-flex items-center gap-1 text-[11px] font-mono px-2 py-1 rounded-md bg-slate-50 dark:bg-slate-700/50 border border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-300">
                {i + 1}. {name}
              </span>
            ))}
          </div>
          <p className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-2">Provider keys</p>
          <div className="space-y-1.5">
            {providerNames.map((name) => {
              const p = config.providers[name];
              return (
                <div key={name} className="flex items-center justify-between text-[11px]">
                  <span className="font-mono text-slate-700 dark:text-slate-300">{name}</span>
                  <span className={p.configured ? "text-emerald-600 dark:text-emerald-400 font-mono" : "text-red-500 font-mono"}>
                    {p.key}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────

export default function ProvidersPage() {
  const [overview, setOverview] = useState<ProviderOverviewData | null>(null);
  const [health, setHealth] = useState<ProviderHealthData | null>(null);
  const [metrics, setMetrics] = useState<ProviderMetricsData | null>(null);
  const [config, setConfig] = useState<ProviderConfigData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastChecked, setLastChecked] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<string | null>(null);
  const [testError, setTestError] = useState<string | null>(null);

  const fetchAll = useCallback(async () => {
    try {
      const [o, h, m, c] = await Promise.all([
        providersApi.overview(),
        providersApi.health(),
        providersApi.metrics(),
        providersApi.config(),
      ]);
      setOverview(o.data);
      setHealth(h.data);
      setMetrics(m.data);
      setConfig(c.data);
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load provider data");
    } finally {
      setLoading(false);
      setLastChecked(new Date().toLocaleTimeString());
    }
  }, []);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  const runTest = useCallback(async () => {
    setTesting(true);
    setTestResult(null);
    setTestError(null);
    try {
      const res = await providersApi.test();
      setTestResult(`provider=${res.data.provider} · finish=${res.data.finish_reason} · "${res.data.content}"`);
    } catch (err: unknown) {
      setTestError(err instanceof Error ? err.message : "Test call failed");
    } finally {
      setTesting(false);
    }
  }, []);

  const totals = metrics?.totals;
  const failoverEvents = metrics?.failover_events || [];
  const persisted = metrics?.persisted;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white">Provider Router</h1>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
            Health-aware, failover-capable routing across LLM providers with circuit breaking, retries &amp; metrics.
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

      {!loading && !error && overview && health && metrics && config && (
        <>
          {/* Overview stats */}
          <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
            <StatCard
              label="Active provider"
              value={health.active_provider || "—"}
              sub="serving requests now"
              color={health.active_provider ? "text-emerald-600 dark:text-emerald-400" : "text-slate-400"}
            />
            <StatCard label="Routing" value={health.routing_enabled ? "enabled" : "disabled"} color="text-slate-900 dark:text-white" />
            <StatCard label="Total requests" value={totals?.total_requests ?? 0} color="text-slate-900 dark:text-white" />
            <StatCard
              label="Success rate"
              value={totals && totals.total_requests > 0 ? formatRate(totals.successful_requests / totals.total_requests) : "—"}
              color="text-slate-900 dark:text-white"
            />
            <StatCard label="Failovers" value={failoverEvents.length} sub={`${totals?.failovers ?? 0} counted`} color="text-amber-600 dark:text-amber-400" />
          </div>

          {/* Test route */}
          <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-5 flex items-center justify-between flex-wrap gap-3">
            <div>
              <p className="text-sm font-semibold text-slate-900 dark:text-white">Route a live test call</p>
              <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                Sends one benign call through the router — exercises the same priority chain, retries &amp; failover agents use.
              </p>
              {testResult && (
                <p className="mt-2 text-[11px] font-mono text-emerald-600 dark:text-emerald-400">{testResult}</p>
              )}
              {testError && (
                <p className="mt-2 text-[11px] font-mono text-red-500">test failed: {testError}</p>
              )}
            </div>
            <button
              onClick={runTest}
              disabled={testing}
              className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-medium transition-all bg-primary-600 text-white hover:bg-primary-500 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {testing && <svg className="animate-spin w-3.5 h-3.5" viewBox="0 0 24 24" fill="none"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" /><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" /></svg>}
              {testing ? "Routing…" : "Run test call"}
            </button>
          </div>

          {/* Provider cards */}
          <div>
            <SectionHeader title="Providers" sub="Registered providers in priority order — circuit state and rolling health" />
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
              {overview.providers.map((p) => (
                <ProviderCard key={p.name} provider={p} />
              ))}
            </div>
          </div>

          {/* Failover events */}
          <div>
            <SectionHeader title="Failover events" sub="Every provider switch, most recent first (bounded ring buffer)" />
            <FailoverEvents events={failoverEvents} />
          </div>

          {/* Persisted snapshot */}
          {persisted && Object.keys(persisted).length > 0 && (
            <div>
              <SectionHeader title="Persisted snapshot" sub="Latest metrics snapshot recovered from PostgreSQL (across restarts)" />
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
                {Object.entries(persisted).map(([name, snap]) => (
                  <div key={name} className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-5">
                    <div className="flex items-center justify-between mb-3">
                      <h3 className="text-sm font-semibold text-slate-900 dark:text-white">{name}</h3>
                      <StatusBadge status={snap.status} />
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      <StatCard label="Success rate" value={formatRate(snap.success_rate)} color="text-slate-900 dark:text-white" />
                      <StatCard label="Requests" value={snap.total_requests} color="text-slate-900 dark:text-white" />
                      <StatCard label="Retries" value={snap.retries} color="text-slate-900 dark:text-white" />
                      <StatCard label="Failovers" value={snap.failovers} color="text-slate-900 dark:text-white" />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Config */}
          <div>
            <SectionHeader title="Routing configuration" sub="Secrets are masked — only key presence and redacted suffixes are shown" />
            <ConfigPanel config={config} />
          </div>
        </>
      )}
    </div>
  );
}
