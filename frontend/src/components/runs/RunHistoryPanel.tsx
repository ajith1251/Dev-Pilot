"use client";

/**
 * Phase 20A6 — Context & Run History.
 *
 * Shows historical multi-repository runs + recent engineering decisions,
 * reusing the existing run-list API and the run's own decision events.
 * Repository relationships surface via the repository status cards' graph
 * links; this panel gives the historical context (previous successful
 * multi-repo executions).
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { runsApi } from "@/lib/api/client";
import type { RunSummary } from "@/lib/api/client";

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

export default function RunHistoryPanel({
  runId,
  decisions,
}: {
  runId: string;
  decisions?: { count: number; recent: string[] };
}) {
  const [multiRepos, setMultiRepos] = useState<RunSummary[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    runsApi
      .list({ limit: 50, offset: 0, sort_by: "newest" })
      .then((result) => {
        if (cancelled) return;
        setMultiRepos(
          (result.data || []).filter(
            (r) => (r.repository_count ?? 1) > 1 && r.run_id !== runId
          ).slice(0, 5)
        );
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [runId]);

  const hasDecisions = (decisions?.count ?? 0) > 0;

  if (!loading && multiRepos.length === 0 && !hasDecisions) return null;

  return (
    <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 overflow-hidden">
      <div className="px-5 py-3.5 border-b border-slate-200 dark:border-slate-700">
        <h3 className="text-sm font-semibold text-slate-900 dark:text-white">
          Context &amp; Run History
        </h3>
        <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5">
          Previous multi-repository executions and recent engineering decisions
        </p>
      </div>

      <div className="px-5 py-4 grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Historical multi-repo runs */}
        <div>
          <p className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-2">
            Recent Multi-Repository Runs
          </p>
          {multiRepos.length === 0 ? (
            <p className="text-[11px] text-slate-400 italic">
              No previous multi-repository runs.
            </p>
          ) : (
            <div className="space-y-1.5">
              {multiRepos.map((r) => (
                <Link
                  key={r.run_id}
                  href={`/dashboard/runs/${r.run_id}`}
                  className="block rounded-lg border border-slate-200 dark:border-slate-700 px-3 py-2 hover:border-primary-300 dark:hover:border-primary-700 hover:bg-slate-50 dark:hover:bg-slate-700/40 transition-all"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-[11px] text-slate-900 dark:text-white truncate">
                      {r.run_id}
                    </span>
                    <span className="text-[10px] text-slate-400 shrink-0">
                      {timeAgo(r.created_at)}
                    </span>
                  </div>
                  <div className="flex items-center justify-between gap-2 mt-0.5">
                    <span className="text-[11px] text-slate-500 dark:text-slate-400 truncate">
                      {r.title}
                    </span>
                    <span className="text-[10px] shrink-0 px-1.5 py-0.5 rounded-full bg-primary-100 dark:bg-primary-900/30 text-primary-700 dark:text-primary-300">
                      {r.repository_count} repos
                    </span>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </div>

        {/* Recent engineering decisions */}
        <div>
          <p className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-2">
            Engineering Decisions
          </p>
          {!hasDecisions ? (
            <p className="text-[11px] text-slate-400 italic">
              No decisions recorded yet.
            </p>
          ) : (
            <ul className="space-y-1.5">
              {(decisions?.recent ?? []).slice(0, 6).map((d, i) => (
                <li
                  key={i}
                  className="flex items-start gap-2 text-[11px] text-slate-600 dark:text-slate-300"
                >
                  <span className="mt-1 w-1.5 h-1.5 rounded-full bg-primary-400 shrink-0" />
                  <span className="leading-snug">{d}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
