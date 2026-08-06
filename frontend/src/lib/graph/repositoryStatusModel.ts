/**
 * Phase 20A6 — pure mappers for the multi-repository dashboard.
 *
 * All helpers are deterministic and side-effect free so they can be unit
 * tested without a server. They translate the server-derived repository
 * status payload into display-ready shapes (progress, timeline ordering,
 * organization summary classification).
 */

import type {
  OrganizationSummary,
  RepositoryStatus,
} from "@/lib/api/client";

/** The six stages shown per repository (server contract order). */
export const REPOSITORY_STAGES = [
  "planning",
  "coding",
  "testing",
  "repair",
  "review",
  "quality_gate",
] as const;

export type RepositoryStage = (typeof REPOSITORY_STAGES)[number];
export type StageStatus =
  | "pending" | "running" | "succeeded" | "failed" | "skipped" | "cancelled";

const STATUS_RANK: Record<StageStatus, number> = {
  pending: 0,
  skipped: 1,
  succeeded: 2,
  failed: 3,
  cancelled: 3,
  running: 4, // live activity outranks everything for the overall status
};

/** Normalize a raw stage status string to the display enum. */
export function normalizeStageStatus(raw: string | undefined): StageStatus {
  switch (raw) {
    case "running": return "running";
    case "succeeded": return "succeeded";
    case "failed": return "failed";
    case "skipped": return "skipped";
    case "cancelled": return "cancelled";
    default: return "pending";
  }
}

/** Overall timeline status for a repository (highest-rank stage wins). */
export function deriveTimelineStatus(
  repo: Pick<RepositoryStatus, "progress">
): StageStatus {
  const stages = REPOSITORY_STAGES.map((s) =>
    normalizeStageStatus(repo.progress?.[s])
  );
  if (stages.length === 0) return "pending";
  return stages.reduce<StageStatus>((best, cur) =>
    STATUS_RANK[cur] > STATUS_RANK[best] ? cur : best
  , "pending");
}

/** Completion fraction 0..1 (succeeded/failed/skipped count as done). */
export function computeRepoProgress(
  repo: Pick<RepositoryStatus, "progress">
): number {
  const stages = REPOSITORY_STAGES.map((s) =>
    normalizeStageStatus(repo.progress?.[s])
  );
  if (stages.length === 0) return 0;
  const done = stages.filter(
    (s) => s === "succeeded" || s === "failed" || s === "skipped"
  ).length;
  return done / stages.length;
}

/** Validate the repository payload shape (defensive guard). */
export function isValidRepository(repo: unknown): repo is RepositoryStatus {
  if (!repo || typeof repo !== "object") return false;
  const r = repo as Record<string, unknown>;
  return (
    typeof r.repository_id === "string" &&
    typeof r.progress === "object" &&
    r.progress !== null
  );
}

/** Aggregate org-level counts from the run's repository view. */
export function classifyRepositories(repos: RepositoryStatus[]): {
  successful: string[];
  failed: string[];
  repaired: string[];
} {
  return {
    successful: repos
      .filter(
        (r) =>
          r.validation_status === "validated" &&
          r.application_status === "applied"
      )
      .map((r) => r.repository_id),
    failed: repos
      .filter(
        (r) =>
          r.validation_status === "rejected" ||
          r.application_status === "rejected"
      )
      .map((r) => r.repository_id),
    repaired: [],
  };
}

/** Short human label for a repository source type. */
export function sourceTypeLabel(sourceType: string): string {
  switch (sourceType) {
    case "github": return "github";
    case "local": return "local";
    case "org": return "org";
    default: return sourceType || "local";
  }
}

/** Link targets for a repository status card (EKG navigation). */
export function repositoryLinks(repo: RepositoryStatus, runId: string) {
  const encodedId = encodeURIComponent(repo.repository_id);
  return {
    repositoryGraph: `/dashboard/organization-graph?repository_id=${encodedId}&scope=local`,
    organizationGraph: `/dashboard/organization-graph`,
    engineeringHistory: `/dashboard/engineering-graph`,
    runDetail: `/dashboard/runs/${encodeURIComponent(runId)}`,
    notebook: `/dashboard/runs/${encodeURIComponent(runId)}?view=notebook`,
    consensus: `/dashboard/runs/${encodeURIComponent(runId)}?view=consensus`,
    repositoryMemory: `/dashboard/engineering-graph?repository_id=${encodedId}`,
  };
}

/** Classify a repository card's display status. */
export function cardStatus(repo: RepositoryStatus): {
  tone: "success" | "danger" | "active" | "muted";
  label: string;
} {
  if (repo.validation_status === "rejected" || repo.application_status === "rejected") {
    return { tone: "danger", label: "Blocked" };
  }
  if (
    repo.validation_status === "validated" &&
    repo.application_status === "applied"
  ) {
    return { tone: "success", label: "Verified" };
  }
  if (repo.current_stage === "completed" || repo.current_stage === "failed") {
    return { tone: "muted", label: "Idle" };
  }
  return { tone: "active", label: "Active" };
}

/** Safe accessor for org summary with defaults (server may omit). */
export function normalizeSummary(
  summary?: OrganizationSummary | null
): OrganizationSummary | null {
  if (!summary) return null;
  return {
    ...summary,
    repository_count: summary.repository_count ?? 0,
    participating_repositories: summary.participating_repositories ?? [],
    successful_repositories: summary.successful_repositories ?? [],
    failed_repositories: summary.failed_repositories ?? [],
    repaired_repositories: summary.repaired_repositories ?? [],
    engineering_decisions: summary.engineering_decisions ?? { count: 0, recent: [] },
    consensus_summary: summary.consensus_summary ?? { count: 0, contradictions: 0, recent: [] },
  };
}
