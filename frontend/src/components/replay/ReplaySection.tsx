"use client";

/**
 * Phase 21 — Replay & Audit section (run detail).
 *
 * Answers, from recorded evidence alone (never another LLM call):
 *  1. What happened?                 -> manifest summary
 *  2. What evidence was recorded?    -> manifest fingerprint + stage records
 *  3. Can it be reproduced?          -> EXACT / DETERMINISTIC replay
 *  4. Was the replay identical?      -> verdict (MATCH / DRIFT / INVALID / INCOMPLETE)
 *  5/6. Where and why did it differ? -> DifferenceViewer + audit evidence
 *  7. Which checks support it?       -> deterministic checks in the audit
 *
 * Replay execution is synchronous on the backend (the POST returns the
 * completed result), so the phase machine is local: idle → starting →
 * running → completed/failed. No large WebSocket system is introduced;
 * when the run's WebSocket pushes a terminal run status, this section
 * refreshes the manifest + history so a freshly captured manifest appears
 * automatically. While disconnected, the section still refreshes on user
 * action and after each replay.
 */

import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { replayApi, runsApi } from "@/lib/api/client";
import type {
  ReplayAudit,
  ReplayManifest,
  ReplayMode,
  ReplayResult,
  RunSummary,
} from "@/lib/api/client";
import {
  formatTimestamp,
  replayModeLabel,
  replayRunReducer,
  verdictTone,
} from "@/lib/replay/replayModel";
import ReplayTimeline from "./ReplayTimeline";
import DifferenceViewer from "./DifferenceViewer";
import AuditReport from "./AuditReport";
import ReplayHistory from "./ReplayHistory";

const INITIAL_STATE = {
  phase: "idle" as const,
  mode: null,
  error: null,
  result: null,
};

export default function ReplaySection({
  runId,
  sourceRunStatus,
  liveRunStatus,
}: {
  runId: string;
  sourceRunStatus: string;
  /** Live run status from the run WebSocket. When it transitions to a
   * terminal state, this section refreshes the manifest + history so a
   * freshly captured manifest appears automatically. */
  liveRunStatus?: string;
}) {
  const [manifest, setManifest] = useState<ReplayManifest | null>(null);
  const [manifestLoading, setManifestLoading] = useState(true);
  const [manifestError, setManifestError] = useState<string | null>(null);
  const [latestResult, setLatestResult] = useState<ReplayResult | null>(null);
  const [audit, setAudit] = useState<ReplayAudit | null>(null);
  const [auditLoading, setAuditLoading] = useState(false);
  const [auditError, setAuditError] = useState<string | null>(null);
  const [showAudit, setShowAudit] = useState(false);
  const [runState, dispatch] = useReducer(replayRunReducer, INITIAL_STATE);
  const [compareRuns, setCompareRuns] = useState<RunSummary[]>([]);
  const [compareRunId, setCompareRunId] = useState("");
  const [compareLoading, setCompareLoading] = useState(false);
  const mountedRef = useRef(true);

  // ── Data loading ─────────────────────────────────────────────

  const loadData = useCallback(
    async (quiet = false) => {
      if (!quiet) setManifestLoading(true);
      setManifestError(null);
      try {
        const [manifestResult, historyResult] = await Promise.all([
          replayApi.manifest(runId),
          replayApi.history(runId, { limit: 1, offset: 0 }),
        ]);
        if (!mountedRef.current) return;
        setManifest(manifestResult.data);
        const latest = historyResult.data?.[0] || null;
        setLatestResult(latest);
        // Keep an already-shown audit honest: refresh it when the latest
        // replay changed (newly executed replay).
        setAudit((prev) => {
          if (!prev || !latest) return prev;
          if (prev.replay?.replay_id !== latest.replay_id) return null;
          return prev;
        });
      } catch (err: unknown) {
        if (!mountedRef.current) return;
        setManifestError(
          err instanceof Error ? err.message : "Failed to load replay data"
        );
      } finally {
        if (mountedRef.current) setManifestLoading(false);
      }
    },
    [runId]
  );

  // Load candidate runs for COMPARE mode (bounded: newest 50).
  const loadCompareRuns = useCallback(async () => {
    setCompareLoading(true);
    try {
      const result = await runsApi.list({ limit: 50, sort_by: "newest" });
      if (!mountedRef.current) return;
      setCompareRuns((result.data || []).filter((r) => r.run_id !== runId));
    } catch {
      // Optional — compare picker simply stays empty on failure.
    } finally {
      if (mountedRef.current) setCompareLoading(false);
    }
  }, [runId]);

  useEffect(() => {
    mountedRef.current = true;
    loadData();
    // Populate the COMPARE picker lazily-but-once (bounded 50-run list call).
    loadCompareRuns();
    return () => {
      mountedRef.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadData]);

  // Refresh when the run reaches a terminal state (manifest capture happens
  // at run completion — e.g. via the run WebSocket pushing the final state).
  const terminalStates = ["approved", "rejected", "needs_human_review", "failed", "cancelled"];
  const prevLiveStatusRef = useRef<string | null>(null);
  useEffect(() => {
    if (!liveRunStatus) return;
    const wasTerminal = prevLiveStatusRef.current
      ? terminalStates.includes(prevLiveStatusRef.current)
      : false;
    const nowTerminal = terminalStates.includes(liveRunStatus);
    prevLiveStatusRef.current = liveRunStatus;
    if (nowTerminal && !wasTerminal) {
      loadData(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveRunStatus]);

  // ── Start replay ─────────────────────────────────────────────

  const startReplay = useCallback(
    async (mode: ReplayMode) => {
      if (mode === "compare" && !compareRunId) return;
      dispatch({ type: "start", mode });
      try {
        const result = await replayApi.execute(runId, {
          mode,
          ...(mode === "compare" ? { otherRunId: compareRunId } : {}),
        });
        if (!mountedRef.current) return;
        dispatch({ type: "complete", result: result.data });
        setLatestResult(result.data);
        setAudit(null);
        setShowAudit(false);
        loadData(true);
      } catch (err: unknown) {
        if (!mountedRef.current) return;
        dispatch({
          type: "fail",
          error: err instanceof Error ? err.message : "Replay failed",
        });
      }
    },
    [runId, compareRunId, loadData]
  );

  const runAudit = useCallback(async () => {
    setAuditLoading(true);
    setAuditError(null);
    try {
      const result = await replayApi.audit(runId);
      if (!mountedRef.current) return;
      setAudit(result.data);
      setShowAudit(true);
    } catch (err: unknown) {
      if (!mountedRef.current) return;
      setAuditError(
        err instanceof Error ? err.message : "Failed to build audit report"
      );
    } finally {
      if (mountedRef.current) setAuditLoading(false);
    }
  }, [runId]);

  // ── Derived presentation ─────────────────────────────────────

  const verdict = latestResult?.verdict || null;
  const verdictToneValue = verdictTone(verdict);
  const mode = latestResult?.mode || null;
  const running =
    runState.phase === "starting" || runState.phase === "running";
  const busy = running || manifestLoading || compareLoading;

  const modeButton = (
    m: ReplayMode,
    disabled: boolean,
    title: string
  ) => (
    <button
      key={m}
      disabled={disabled || busy}
      onClick={() => startReplay(m)}
      title={title}
      className="px-3 py-1.5 rounded-lg text-[11px] font-bold uppercase tracking-wide text-slate-600 dark:text-slate-300 bg-slate-100 dark:bg-slate-700/60 border border-slate-200 dark:border-slate-600 hover:bg-slate-200 dark:hover:bg-slate-600 disabled:opacity-40 disabled:cursor-not-allowed transition-all"
    >
      {replayModeLabel(m)}
    </button>
  );

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-5">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <h2 className="text-sm font-semibold text-slate-900 dark:text-white flex items-center gap-2">
              <svg className="w-4 h-4 text-primary-500" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182" />
              </svg>
              Replay &amp; Audit
            </h2>
            <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5">
              Deterministic reproduction of this run from recorded evidence —
              never calls an LLM
            </p>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={() => loadData()}
              disabled={busy}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-medium text-slate-600 dark:text-slate-300 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-600 hover:bg-slate-50 dark:hover:bg-slate-700 disabled:opacity-40 transition-all"
            >
              <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182" />
              </svg>
              Refresh
            </button>
            <button
              onClick={runAudit}
              disabled={auditLoading || busy}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-bold text-white bg-primary-600 hover:bg-primary-500 disabled:opacity-40 disabled:cursor-not-allowed transition-all"
            >
              {auditLoading ? (
                <svg className="animate-spin w-3 h-3" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
              ) : (
                <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
                </svg>
              )}
              Audit Report
            </button>
          </div>
        </div>

        {/* Availability + manifest status */}
        <div className="mt-4 grid grid-cols-2 md:grid-cols-4 gap-3">
          <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/40 p-3">
            <p className="text-[9px] font-semibold text-slate-400 uppercase tracking-wider">Manifest Status</p>
            <p className="mt-1 text-xs font-semibold text-slate-800 dark:text-slate-200">
              {manifestLoading
                ? "Loading…"
                : manifest?.exists
                  ? "Available"
                  : manifestError
                    ? "Unavailable"
                    : "Not available"}
            </p>
            {manifest?.manifest_id && (
              <p className="text-[10px] font-mono text-slate-400 mt-0.5 truncate" title={manifest.manifest_id}>
                {manifest.manifest_id}
              </p>
            )}
          </div>
          <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/40 p-3">
            <p className="text-[9px] font-semibold text-slate-400 uppercase tracking-wider">Repository Fingerprint</p>
            <p className="mt-1 text-xs font-mono text-slate-800 dark:text-slate-200 truncate" title={manifest?.repository_state?.fingerprint}>
              {manifest?.repository_state?.fingerprint || "—"}
            </p>
          </div>
          <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/40 p-3">
            <p className="text-[9px] font-semibold text-slate-400 uppercase tracking-wider">Stages Recorded</p>
            <p className="mt-1 text-xs font-semibold text-slate-800 dark:text-slate-200">
              {manifest?.stage_count ?? "—"}
            </p>
          </div>
          <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/40 p-3">
            <p className="text-[9px] font-semibold text-slate-400 uppercase tracking-wider">Last Replay</p>
            <p className="mt-1 text-xs font-semibold text-slate-800 dark:text-slate-200 capitalize">
              {latestResult ? replayModeLabel(latestResult.mode) : "None"}
            </p>
            {latestResult && (
              <p className="text-[10px] text-slate-400 mt-0.5">
                {formatTimestamp(latestResult.created_at)}
              </p>
            )}
          </div>
        </div>

        {manifestError && (
          <div className="mt-3 rounded-lg bg-red-50 dark:bg-red-900/10 border border-red-200 dark:border-red-800/40 px-3 py-2">
            <p className="text-[11px] text-red-600 dark:text-red-400">{manifestError}</p>
            <button
              onClick={() => loadData()}
              className="mt-1 text-[11px] font-medium text-red-600 dark:text-red-400 underline"
            >
              Retry
            </button>
          </div>
        )}

        {/* Verdict banner */}
        {verdict && (
          <div className={`mt-4 rounded-lg border-2 ${verdictToneValue.banner} px-4 py-3 flex items-center gap-3`}>
            <span className={`w-2.5 h-2.5 rounded-full ${verdictToneValue.dot}`} />
            <div className="flex-1">
              <p className="text-sm font-bold">
                {verdictToneValue.label}
                {mode && (
                  <span className="ml-2 text-[10px] font-semibold uppercase text-slate-500 dark:text-slate-400">
                    {replayModeLabel(mode)} replay
                  </span>
                )}
              </p>
              {latestResult?.summary && (
                <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5">
                  {latestResult.summary}
                </p>
              )}
            </div>
            <div className="text-right shrink-0">
              <p className="text-[10px] text-slate-500 dark:text-slate-400">
                {latestResult?.checks_passed ?? 0}/{latestResult?.checks_total ?? 0} checks
              </p>
              {latestResult && (latestResult.checks_failed ?? 0) > 0 && (
                <p className="text-[10px] text-red-500 font-semibold">
                  {latestResult.checks_failed} failed
                </p>
              )}
              {(latestResult?.checks_not_replayable ?? 0) > 0 && (
                <p className="text-[10px] text-amber-500 font-semibold">
                  {latestResult!.checks_not_replayable} not replayable
                </p>
              )}
            </div>
          </div>
        )}

        {/* Run state (starting/running/completed/failed) */}
        {running && (
          <div className="mt-4 flex items-center gap-2 text-xs text-blue-600 dark:text-blue-400">
            <svg className="animate-spin w-4 h-4" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
            {runState.phase === "starting"
              ? `Starting ${replayModeLabel(runState.mode || "")} replay…`
              : `Replaying deterministic stages (${replayModeLabel(runState.mode || "")})…`}
          </div>
        )}
        {runState.phase === "failed" && (
          <div className="mt-4 rounded-lg bg-red-50 dark:bg-red-900/10 border border-red-200 dark:border-red-800/40 px-3 py-2">
            <p className="text-[11px] text-red-600 dark:text-red-400">
              Replay failed: {runState.error}
            </p>
            <button
              onClick={() => dispatch({ type: "reset" })}
              className="mt-1 text-[11px] font-medium text-red-600 dark:text-red-400 underline"
            >
              Dismiss
            </button>
          </div>
        )}

        {/* Start replay controls */}
        <div className="mt-4 pt-4 border-t border-slate-100 dark:border-slate-700/60">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider mr-1">
              Start replay
            </span>
            {modeButton(
              "exact",
              false,
              "Re-execute deterministic stages offline from recorded evidence"
            )}
            {modeButton(
              "deterministic",
              false,
              "EXACT plus live workspace verification (fingerprint, application, tests)"
            )}
            {modeButton(
              "compare",
              compareRunId === "",
              "Compare this run against another run stage by stage"
            )}
          </div>

          {/* COMPARE picker */}
          <div className="mt-2 flex items-center gap-2 flex-wrap">
            <span className="text-[10px] text-slate-400">Compare against:</span>
            <select
              value={compareRunId}
              onChange={(e) => setCompareRunId(e.target.value)}
              disabled={compareLoading || busy}
              className="text-[11px] px-2 py-1 rounded-md bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-600 text-slate-700 dark:text-slate-200 disabled:opacity-40 max-w-[320px]"
            >
              <option value="">{compareLoading ? "Loading runs…" : "Select a run…"}</option>
              {compareRuns.slice(0, 20).map((r) => (
                <option key={r.run_id} value={r.run_id}>
                  {r.run_id} — {r.title?.slice(0, 40) || "untitled"}
                </option>
              ))}
            </select>
            <button
              onClick={loadCompareRuns}
              disabled={busy}
              className="text-[10px] font-medium text-primary-600 dark:text-primary-400 hover:text-primary-500 disabled:opacity-40 transition-colors"
            >
              reload
            </button>
          </div>

          {/* Source run status + manifest fingerprint */}
          <div className="mt-3 flex items-center gap-3 flex-wrap text-[10px] text-slate-400">
            <span>
              Original run status:{" "}
              <span className="capitalize text-slate-600 dark:text-slate-300">
                {sourceRunStatus.replace(/_/g, " ")}
              </span>
            </span>
            {manifest?.content_hash && (
              <span>
                Manifest fingerprint:{" "}
                <span className="font-mono text-slate-600 dark:text-slate-300">
                  {manifest.content_hash}
                </span>
              </span>
            )}
            {manifest?.created_at && (
              <span>
                Captured {formatTimestamp(manifest.created_at)}
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Audit error */}
      {auditError && (
        <div className="rounded-xl bg-red-50 dark:bg-red-900/10 border border-red-200 dark:border-red-800/40 p-4">
          <p className="text-xs text-red-600 dark:text-red-400">{auditError}</p>
          <button
            onClick={runAudit}
            className="mt-1 text-[11px] font-medium text-red-600 dark:text-red-400 underline"
          >
            Retry
          </button>
        </div>
      )}

      {/* Audit report */}
      {showAudit && audit && <AuditReport audit={audit} />}

      {/* Replay timeline + differences */}
      {(latestResult || manifest?.stages?.length) && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <ReplayTimeline manifest={manifest} result={latestResult} />
          <DifferenceViewer manifest={manifest} result={latestResult} />
        </div>
      )}

      {/* History */}
      <ReplayHistory runId={runId} />
    </div>
  );
}
