/**
 * Phase 20A6 — pure model tests for the multi-repository dashboard mappers.
 *
 * DOM-free unit tests for `repositoryStatusModel.ts`: stage normalization,
 * timeline derivation, progress computation, payload validation, org-level
 * classification, and link building.
 */
import { describe, expect, it } from "vitest";
import {
  REPOSITORY_STAGES,
  cardStatus,
  classifyRepositories,
  computeRepoProgress,
  deriveTimelineStatus,
  isValidRepository,
  normalizeStageStatus,
  normalizeSummary,
  repositoryLinks,
} from "./repositoryStatusModel";
import type { RepositoryStatus } from "@/lib/api/client";

const repo = (overrides: Partial<RepositoryStatus> = {}): RepositoryStatus => ({
  repository_id: "repo-b",
  name: "Repo B",
  namespace: "repo-b",
  organization: "default",
  path: "/org/repo-b",
  source_type: "local",
  is_primary: false,
  ordering: 1,
  current_stage: "completed",
  progress: {
    planning: "succeeded",
    coding: "succeeded",
    testing: "succeeded",
    repair: "skipped",
    review: "succeeded",
    quality_gate: "succeeded",
  },
  validation_status: "validated",
  application_status: "applied",
  changed_files: ["feature.py"],
  validation_errors: [],
  quality_gate: "succeeded",
  quality_gate_result: "approved",
  graph: { available: true, node_count: 4, edge_count: 3, run_count: 1 },
  ...overrides,
});

describe("normalizeStageStatus", () => {
  it("maps known statuses and defaults unknown to pending", () => {
    expect(normalizeStageStatus("succeeded")).toBe("succeeded");
    expect(normalizeStageStatus("running")).toBe("running");
    expect(normalizeStageStatus("failed")).toBe("failed");
    expect(normalizeStageStatus("skipped")).toBe("skipped");
    expect(normalizeStageStatus(undefined)).toBe("pending");
    expect(normalizeStageStatus("weird")).toBe("pending");
  });
});

describe("deriveTimelineStatus", () => {
  it("returns the highest-rank stage status", () => {
    expect(deriveTimelineStatus(repo())).toBe("succeeded");
    expect(
      deriveTimelineStatus(
        repo({ progress: { ...repo().progress, coding: "running" } })
      )
    ).toBe("running");
    expect(
      deriveTimelineStatus(
        repo({ progress: { ...repo().progress, coding: "failed" } })
      )
    ).toBe("failed");
  });

  it("is pending for an empty progress map", () => {
    expect(deriveTimelineStatus({ progress: {} as never })).toBe("pending");
  });
});

describe("computeRepoProgress", () => {
  it("counts succeeded/failed/skipped as done", () => {
    expect(computeRepoProgress(repo())).toBe(1);
    expect(
      computeRepoProgress(
        repo({
          progress: {
            planning: "succeeded",
            coding: "running",
            testing: "pending",
            repair: "pending",
            review: "pending",
            quality_gate: "pending",
          },
        })
      )
    ).toBe(1 / 6);
  });

  it("handles empty progress", () => {
    expect(computeRepoProgress({ progress: {} as never })).toBe(0);
  });
});

describe("isValidRepository", () => {
  it("accepts a well-formed payload and rejects garbage", () => {
    expect(isValidRepository(repo())).toBe(true);
    expect(isValidRepository(null)).toBe(false);
    expect(isValidRepository(undefined)).toBe(false);
    expect(isValidRepository({ repository_id: "x" })).toBe(false);
  });
});

describe("classifyRepositories", () => {
  it("classifies validated+applied as successful and rejected as failed", () => {
    const ok = repo();
    const blocked = repo({
      repository_id: "repo-c",
      validation_status: "rejected",
      application_status: "rejected",
    });
    const out = classifyRepositories([ok, blocked]);
    expect(out.successful).toEqual(["repo-b"]);
    expect(out.failed).toEqual(["repo-c"]);
  });
});

describe("cardStatus", () => {
  it("reports blocked / verified / active tones", () => {
    expect(cardStatus(repo())).toEqual({ tone: "success", label: "Verified" });
    expect(
      cardStatus(repo({ validation_status: "rejected" }))
    ).toEqual({ tone: "danger", label: "Blocked" });
    expect(
      cardStatus(
        repo({
          validation_status: "not_attempted",
          current_stage: "coding",
        })
      )
    ).toEqual({ tone: "active", label: "Active" });
    expect(
      cardStatus(
        repo({ validation_status: "not_attempted", current_stage: "failed" })
      )
    ).toEqual({ tone: "muted", label: "Idle" });
  });
});

describe("repositoryLinks", () => {
  it("builds EKG navigation links scoped to the repository", () => {
    const links = repositoryLinks(repo({ repository_id: "repo-b" }), "RUN-1");
    expect(links.repositoryGraph).toContain("organization-graph");
    expect(links.repositoryGraph).toContain("repository_id=repo-b");
    expect(links.organizationGraph).toBe("/dashboard/organization-graph");
    expect(links.runDetail).toBe("/dashboard/runs/RUN-1");
    expect(links.notebook).toContain("RUN-1");
    expect(links.consensus).toContain("RUN-1");
  });
});

describe("normalizeSummary", () => {
  it("fills defaults and returns null for empty input", () => {
    expect(normalizeSummary(null)).toBeNull();
    expect(normalizeSummary(undefined)).toBeNull();
    const out = normalizeSummary({
      repository_count: 2,
      participating_repositories: [],
      successful_repositories: ["a"],
      failed_repositories: [],
      repaired_repositories: [],
      engineering_decisions: { count: 1, recent: [] },
      consensus_summary: { count: 0, contradictions: 0, recent: [] },
      quality_status: "approved",
    });
    expect(out?.repository_count).toBe(2);
    expect(out?.engineering_decisions).toEqual({ count: 1, recent: [] });
  });

  it("has the canonical six-stage order", () => {
    expect(REPOSITORY_STAGES).toEqual([
      "planning",
      "coding",
      "testing",
      "repair",
      "review",
      "quality_gate",
    ]);
  });
});
