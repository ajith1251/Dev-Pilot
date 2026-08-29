"use client";

/**
 * Phase 21 — replay history for a run.
 *
 * Lists past replay executions (mode, verdict, started, duration) using the
 * existing `GET /runs/{run_id}/replay` API with limit/offset pagination.
 * Multiple replays of the same original run are supported.
 */

import { useEffect, useState } from "react";
import { replayApi } from "@/lib/api/client";
import type { ReplayHistoryEntry } from "@/lib/api/client";
import {
  formatTimestamp,
  replayModeLabel,
  verdictTone,
} from "@/lib/replay/replayModel";

const PAGE_SIZE = 10;

export default function ReplayHistory({ runId }: { runId: string }) {
  const [entries, setEntries] = useState<ReplayHistoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState<number | null>(null);

  const load = (nextOffset: number) => {
    setLoading(true);
    setError(null);
    replayApi
      .history(runId, { limit: PAGE_SIZE, offset: nextOffset })
      .then((result) => {
        const data = result.data || [];
        setEntries(data);
        setOffset(nextOffset);
        // The API does not return a total — infer "has more" from a full page.
        setTotal(data.length);
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "Failed to load replay history");
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  const hasPrevious = offset > 0;
  const hasMore = entries.length === PAGE_SIZE;

  return (
    <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 overflow-hidden">
      <div className="px-5 py-3.5 border-b border-slate-200 dark:border-slate-700 flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-slate-900 dark:text-white">
            Replay History
          </h3>
          <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5">
            Past replay executions of this run
          </p>
        </div>
        <button
          onClick={() => load(0)}
          className="text-[11px] font-medium text-primary-600 dark:text-primary-400 hover:text-primary-500 transition-colors"
        >
          Refresh
        </button>
      </div>

      <div className="px-5 py-3">
        {error && (
          <div className="rounded-lg bg-red-50 dark:bg-red-900/10 border border-red-200 dark:border-red-800/40 px-3 py-2 mb-2">
            <p className="text-[11px] text-red-600 dark:text-red-400">{error}</p>
            <button
              onClick={() => load(offset)}
              className="mt-1 text-[11px] font-medium text-red-600 dark:text-red-400 underline"
            >
              Retry
            </button>
          </div>
        )}

        {loading && entries.length === 0 && (
          <p className="text-[11px] text-slate-400 italic">Loading replay history…</p>
        )}

        {!loading && entries.length === 0 && !error && (
          <p className="text-[11px] text-slate-400 italic">
            No replays executed yet. Start one above.
          </p>
        )}

        <div className="space-y-1.5">
          {entries.map((e) => {
            const tone = verdictTone(e.verdict);
            return (
              <div
                key={e.replay_id}
                className="flex items-center gap-3 py-2 border-b border-slate-100 dark:border-slate-700/50 last:border-0"
              >
                <span className="font-mono text-[10px] text-slate-400 shrink-0 w-24 truncate">
                  {e.replay_id}
                </span>
                <span className="text-[10px] font-bold text-slate-500 dark:text-slate-400 uppercase shrink-0 w-24">
                  {replayModeLabel(e.mode)}
                </span>
                <span
                  className={`inline-flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded-full border shrink-0 ${tone.badge}`}
                >
                  <span className={`w-1.5 h-1.5 rounded-full ${tone.dot}`} />
                  {tone.label}
                </span>
                <span className="text-[10px] text-slate-400 hidden sm:inline">
                  {e.checks_passed}/{e.checks_total} checks
                </span>
                <span className="ml-auto text-[10px] text-slate-400 shrink-0">
                  {formatTimestamp(e.created_at)}
                </span>
              </div>
            );
          })}
        </div>

        {(hasPrevious || hasMore) && (
          <div className="flex items-center justify-between mt-3 pt-2 border-t border-slate-100 dark:border-slate-700/50">
            <button
              disabled={!hasPrevious || loading}
              onClick={() => load(Math.max(0, offset - PAGE_SIZE))}
              className="text-[11px] font-medium text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-white disabled:opacity-40 transition-colors"
            >
              ← Previous
            </button>
            <span className="text-[10px] text-slate-400">
              {hasMore ? `${offset + 1}–${offset + entries.length}+` : `${offset + 1}–${offset + entries.length}`}
            </span>
            <button
              disabled={!hasMore || loading}
              onClick={() => load(offset + PAGE_SIZE)}
              className="text-[11px] font-medium text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-white disabled:opacity-40 transition-colors"
            >
              Next →
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
