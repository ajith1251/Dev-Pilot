/**
 * Phase 21 — pure replay/audit presentation logic.
 *
 * DOM-free helpers consumed by the replay dashboard components and covered
 * by vitest (node environment). All functions are deterministic and never
 * touch the network:
 *
 * - verdictTone / checkStatusTone / stageKindLabel  — presentation mapping
 * - differencesFromResult                            — bounded difference
 *   viewer model (category, stage, severity, evidence)
 * - replayRunReducer                                 — start-replay state
 *   machine (idle → starting → running → completed/failed)
 */

import type {
  ReplayCheck,
  ReplayManifest,
  ReplayResult,
  ReplayStageComparison,
  ReplayStageRecord,
} from "@/lib/api/client";

// ── Verdict presentation ───────────────────────────────────────

export interface VerdictTone {
  label: string;
  /** Tailwind classes for the badge */ 
  badge: string;
  /** Tailwind classes for a large verdict banner */
  banner: string;
  dot: string;
}

export function verdictTone(verdict: string | undefined | null): VerdictTone {
  switch (verdict) {
    case "match":
      return {
        label: "MATCH",
        badge:
          "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400 border-emerald-200 dark:border-emerald-800",
        banner:
          "border-emerald-200 dark:border-emerald-800 bg-emerald-50 dark:bg-emerald-900/10 text-emerald-800 dark:text-emerald-300",
        dot: "bg-emerald-500",
      };
    case "drift":
      return {
        label: "DRIFT",
        badge:
          "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400 border-red-200 dark:border-red-800",
        banner:
          "border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-900/10 text-red-800 dark:text-red-300",
        dot: "bg-red-500",
      };
    case "invalid":
      return {
        label: "INVALID",
        badge:
          "bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-400 border-slate-200 dark:border-slate-700",
        banner:
          "border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 text-slate-700 dark:text-slate-300",
        dot: "bg-slate-400",
      };
    case "incomplete":
      return {
        label: "INCOMPLETE",
        badge:
          "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400 border-amber-200 dark:border-amber-800",
        banner:
          "border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/10 text-amber-800 dark:text-amber-300",
        dot: "bg-amber-500",
      };
    default:
      return {
        label: "UNKNOWN",
        badge:
          "bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-400 border-slate-200 dark:border-slate-700",
        banner:
          "border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 text-slate-500 dark:text-slate-400",
        dot: "bg-slate-300",
      };
  }
}

export function checkStatusTone(status: string | undefined): {
  label: string;
  classes: string;
} {
  switch (status) {
    case "passed":
      return {
        label: "PASS",
        classes:
          "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400",
      };
    case "failed":
      return {
        label: "FAIL",
        classes:
          "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400",
      };
    case "not_replayable":
      return {
        label: "NOT REPLAYABLE",
        classes:
          "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400",
      };
    case "skipped":
    default:
      return {
        label: "SKIPPED",
        classes:
          "bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-400",
      };
  }
}

export function stageKindLabel(kind: string | undefined): string {
  switch (kind) {
    case "deterministic":
      return "DETERMINISTIC";
    case "llm_proposed":
      return "LLM PROPOSED";
    case "observational":
      return "OBSERVATIONAL";
    default:
      return "";
  }
}

export function stageKindTone(kind: string | undefined): {
  label: string;
  classes: string;
} {
  switch (kind) {
    case "deterministic":
      return {
        label: "DET",
        classes:
          "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400",
      };
    case "llm_proposed":
      return {
        label: "LLM",
        classes:
          "bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400",
      };
    case "observational":
      return {
        label: "OBS",
        classes:
          "bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-400",
      };
    default:
      return { label: "", classes: "" };
  }
}

export function formatTimestamp(ts: string | undefined | null): string {
  if (!ts) return "—";
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return ts;
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function formatDurationMs(ms: number | undefined | null): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const m = Math.floor(ms / 60_000);
  const s = Math.round((ms % 60_000) / 1000);
  return `${m}m ${s}s`;
}

// ── Difference viewer model ────────────────────────────────────

export interface ReplayDifference {
  id: string;
  category: string;
  stage: string;
  original: string;
  replay: string;
  severity: "high" | "medium" | "low";
  evidence: string[];
  timestamp?: string;
}

/** Map a deterministic check name to a difference category. */
export function categoryForCheck(check: string): string {
  switch (check) {
    case "repository_fingerprint":
      return "repository drift";
    case "repository_scope":
      return "configuration drift";
    case "patch_structure":
      return "artifact drift";
    case "application_outcome":
      return "artifact drift";
    case "manifest_fidelity":
      return "stage-output drift";
    case "pipeline_sequence":
      return "stage-input drift";
    case "handoffs":
      return "decision drift";
    case "consensus":
      return "decision drift";
    case "contradictions":
      return "decision drift";
    case "testing":
      return "test-result drift";
    case "quality_gate":
      return "quality-gate drift";
    default:
      return "stage-output drift";
  }
}

/** Severity for a check status. */
export function severityForStatus(status: string): "high" | "medium" | "low" {
  switch (status) {
    case "failed":
      return "high";
    case "not_replayable":
      return "medium";
    case "skipped":
      return "low";
    default:
      return "low";
  }
}

/**
 * Build the bounded difference list for a replay result.
 *
 * Evidence is deterministic-only: failed/not-replayable checks carry
 * expected/actual/note; per-stage comparisons carry recorded vs replay
 * hashes. No causality is claimed beyond what the backend recorded.
 */
export function differencesFromResult(
  manifest: ReplayManifest | null,
  result: ReplayResult | null
): ReplayDifference[] {
  if (!result) return [];

  const diffs: ReplayDifference[] = [];
  const checks = result.checks || [];

  for (const check of checks) {
    if (check.status === "passed" || check.status === "skipped") continue;
    const evidence: string[] = [];
    if (check.expected) evidence.push(`expected: ${check.expected}`);
    if (check.actual) evidence.push(`actual: ${check.actual}`);
    if (check.note) evidence.push(check.note);
    diffs.push({
      id: `check-${check.check}`,
      category: categoryForCheck(check.check),
      stage: check.stage || "manifest",
      original: check.expected || "—",
      replay: check.actual || "—",
      severity: severityForStatus(check.status),
      evidence,
      timestamp: result.created_at,
    });
  }

  // Per-stage comparison mismatches (COMPARE mode / replay classification).
  // A stage already covered by a failed check is not duplicated here.
  const coveredStages = new Set(diffs.map((d) => d.stage));
  for (const cmp of result.stage_comparisons || []) {
    if (cmp.matched !== false) continue;
    if (coveredStages.has(cmp.stage)) continue;
    diffs.push({
      id: `stage-${cmp.stage}`,
      category: "stage-output drift",
      stage: cmp.stage,
      original: cmp.recorded_hash || "recorded",
      replay: cmp.replay_hash || "replay",
      severity: "high",
      evidence: cmp.detail ? [cmp.detail] : [],
      timestamp: result.created_at,
    });
  }

  // Manifest-derived missing evidence: deterministic stages without a
  // recorded decision snapshot are not re-executable.
  const manifestStages = manifest?.stages || ([] as ReplayStageRecord[]);
  for (const s of manifestStages) {
    if (s.kind === "deterministic" && !s.captured) {
      diffs.push({
        id: `missing-${s.stage}`,
        category: "missing evidence",
        stage: s.stage,
        original: "recorded decision snapshot",
        replay: "absent",
        severity: "medium",
        evidence: [
          "deterministic inputs were not fully recorded — stage is not re-executable",
        ],
        timestamp: manifest?.created_at,
      });
    }
  }

  // Deduplicate by id (a stage can appear in both checks and comparisons).
  const seen = new Set<string>();
  const unique = diffs.filter((d) => {
    if (seen.has(d.id)) return false;
    seen.add(d.id);
    return true;
  });

  // Bound the viewer: never render more than 50 differences.
  return unique.slice(0, 50);
}

// ── Start-replay state machine ─────────────────────────────────

export type ReplayRunPhase =
  | "idle"
  | "starting"
  | "running"
  | "completed"
  | "failed";

export interface ReplayRunState {
  phase: ReplayRunPhase;
  mode: string | null;
  error: string | null;
  result: ReplayResult | null;
}

export type ReplayRunAction =
  | { type: "start"; mode: string }
  | { type: "complete"; result: ReplayResult }
  | { type: "fail"; error: string }
  | { type: "reset" };

export function replayRunReducer(
  state: ReplayRunState,
  action: ReplayRunAction
): ReplayRunState {
  switch (action.type) {
    case "start":
      return { phase: "starting", mode: action.mode, error: null, result: null };
    case "complete":
      return {
        phase: "completed",
        mode: action.result.mode,
        error: null,
        result: action.result,
      };
    case "fail":
      return {
        phase: "failed",
        mode: state.mode,
        error: action.error,
        result: null,
      };
    case "reset":
      return { phase: "idle", mode: null, error: null, result: null };
    default:
      return state;
  }
}

/** Human-readable labels for the three replay modes. */
export const REPLAY_MODE_LABELS: Record<string, string> = {
  exact: "EXACT",
  deterministic: "DETERMINISTIC",
  compare: "COMPARE",
};

export function replayModeLabel(mode: string | undefined): string {
  return REPLAY_MODE_LABELS[mode || ""] || (mode || "").toUpperCase();
}

/** Replay-stage summary used by the timeline (comparison + kind). */
export interface ReplayStageView {
  stage: string;
  kind: string;
  captured: boolean;
  status: string;
  matched: boolean | null;
  recordedHash: string;
  replayHash: string;
  detail: string;
}

export function replayStageViews(
  manifestStages: ReplayStageRecord[] | undefined,
  comparisons: ReplayStageComparison[] | undefined
): ReplayStageView[] {
  const cmpMap = new Map<string, ReplayStageComparison>();
  for (const c of comparisons || []) cmpMap.set(c.stage, c);

  const seen = new Set<string>();
  const views: ReplayStageView[] = [];

  for (const ms of manifestStages || []) {
    const cmp = cmpMap.get(ms.stage);
    views.push({
      stage: ms.stage,
      kind: ms.kind,
      captured: ms.captured,
      status: ms.status,
      matched: cmp ? cmp.matched : null,
      recordedHash: cmp?.recorded_hash || ms.output_hash,
      replayHash: cmp?.replay_hash || "",
      detail: cmp?.detail || "",
    });
    seen.add(ms.stage);
  }

  // Comparisons may reference stages absent from the manifest (COMPARE mode).
  for (const c of comparisons || []) {
    if (seen.has(c.stage)) continue;
    views.push({
      stage: c.stage,
      kind: c.kind,
      captured: false,
      status: "",
      matched: c.matched,
      recordedHash: c.recorded_hash,
      replayHash: c.replay_hash,
      detail: c.detail,
    });
  }

  return views;
}
