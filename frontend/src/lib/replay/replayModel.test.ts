/**
 * Phase 21 — replay model unit tests (node environment, no DOM).
 */
import { describe, expect, it } from "vitest";
import {
  categoryForCheck,
  checkStatusTone,
  differencesFromResult,
  formatDurationMs,
  formatTimestamp,
  replayModeLabel,
  replayRunReducer,
  replayStageViews,
  severityForStatus,
  stageKindLabel,
  stageKindTone,
  verdictTone,
} from "./replayModel";
import type {
  ReplayCheck,
  ReplayManifest,
  ReplayResult,
} from "@/lib/api/client";

describe("verdictTone", () => {
  it("maps the four replay verdicts to distinct tones", () => {
    const match = verdictTone("match");
    const drift = verdictTone("drift");
    const invalid = verdictTone("invalid");
    const incomplete = verdictTone("incomplete");

    expect(match.label).toBe("MATCH");
    expect(drift.label).toBe("DRIFT");
    expect(invalid.label).toBe("INVALID");
    expect(incomplete.label).toBe("INCOMPLETE");
    // Distinct badge classes so verdicts are visually obvious.
    const badges = [match.badge, drift.badge, invalid.badge, incomplete.badge];
    expect(new Set(badges).size).toBe(4);
  });

  it("falls back to UNKNOWN for anything else", () => {
    expect(verdictTone("nonsense").label).toBe("UNKNOWN");
    expect(verdictTone(undefined).label).toBe("UNKNOWN");
  });
});

describe("checkStatusTone / stageKindLabel / stageKindTone", () => {
  it("maps check statuses to PASS/FAIL/NOT REPLAYABLE/SKIPPED", () => {
    expect(checkStatusTone("passed").label).toBe("PASS");
    expect(checkStatusTone("failed").label).toBe("FAIL");
    expect(checkStatusTone("not_replayable").label).toBe("NOT REPLAYABLE");
    expect(checkStatusTone("skipped").label).toBe("SKIPPED");
    expect(checkStatusTone(undefined).label).toBe("SKIPPED");
  });

  it("classifies stage kinds", () => {
    expect(stageKindLabel("deterministic")).toBe("DETERMINISTIC");
    expect(stageKindLabel("llm_proposed")).toBe("LLM PROPOSED");
    expect(stageKindLabel("observational")).toBe("OBSERVATIONAL");
    expect(stageKindLabel(undefined)).toBe("");
    expect(stageKindTone("deterministic").label).toBe("DET");
    expect(stageKindTone("llm_proposed").label).toBe("LLM");
    expect(stageKindTone("observational").label).toBe("OBS");
  });
});

describe("formatting", () => {
  it("formats durations", () => {
    expect(formatDurationMs(null)).toBe("—");
    expect(formatDurationMs(500)).toBe("500ms");
    expect(formatDurationMs(2500)).toBe("2.5s");
    expect(formatDurationMs(90_000)).toBe("1m 30s");
  });

  it("formats timestamps safely", () => {
    expect(formatTimestamp("")).toBe("—");
    expect(formatTimestamp(null)).toBe("—");
    expect(formatTimestamp("not-a-date")).toBe("not-a-date");
    // Valid timestamps are rendered (locale/timezone dependent — just assert
    // it is not the raw input nor an invalid marker).
    const rendered = formatTimestamp("2026-08-14T00:00:00Z");
    expect(rendered).not.toBe("2026-08-14T00:00:00Z");
    expect(rendered).not.toContain("Invalid");
  });

  it("labels replay modes", () => {
    expect(replayModeLabel("exact")).toBe("EXACT");
    expect(replayModeLabel("deterministic")).toBe("DETERMINISTIC");
    expect(replayModeLabel("compare")).toBe("COMPARE");
    expect(replayModeLabel(undefined)).toBe("");
  });
});

describe("categoryForCheck / severityForStatus", () => {
  it("maps check names to difference categories", () => {
    expect(categoryForCheck("repository_fingerprint")).toBe("repository drift");
    expect(categoryForCheck("repository_scope")).toBe("configuration drift");
    expect(categoryForCheck("patch_structure")).toBe("artifact drift");
    expect(categoryForCheck("application_outcome")).toBe("artifact drift");
    expect(categoryForCheck("manifest_fidelity")).toBe("stage-output drift");
    expect(categoryForCheck("pipeline_sequence")).toBe("stage-input drift");
    expect(categoryForCheck("handoffs")).toBe("decision drift");
    expect(categoryForCheck("consensus")).toBe("decision drift");
    expect(categoryForCheck("contradictions")).toBe("decision drift");
    expect(categoryForCheck("testing")).toBe("test-result drift");
    expect(categoryForCheck("quality_gate")).toBe("quality-gate drift");
    expect(categoryForCheck("unknown_check")).toBe("stage-output drift");
  });

  it("derives severity from check status", () => {
    expect(severityForStatus("failed")).toBe("high");
    expect(severityForStatus("not_replayable")).toBe("medium");
    expect(severityForStatus("skipped")).toBe("low");
    expect(severityForStatus("passed")).toBe("low");
  });
});

describe("differencesFromResult", () => {
  const failedChecks: ReplayCheck[] = [
    {
      stage: "testing",
      check: "testing",
      status: "failed",
      expected: "tests pass",
      actual: "2 failed, 1 passed",
      note: "test execution result changed",
    },
    {
      stage: "quality_gate",
      check: "quality_gate",
      status: "failed",
      expected: "approved",
      actual: "rejected",
      note: "gate decision diverged",
    },
  ];

  it("returns [] with no result", () => {
    expect(differencesFromResult(null, null)).toEqual([]);
  });

  it("builds differences from failed checks with deterministic evidence", () => {
    const result: ReplayResult = {
      replay_id: "REP-1",
      run_id: "RUN-1",
      mode: "deterministic",
      verdict: "drift",
      checks_total: 2,
      checks_passed: 0,
      checks_failed: 2,
      checks_skipped: 0,
      checks_not_replayable: 0,
      stages_matched: 0,
      stages_diverged: 0,
      divergences: [],
      summary: "",
      created_at: "2026-08-14T00:00:00Z",
      checks: failedChecks,
    };
    const diffs = differencesFromResult(null, result);

    expect(diffs).toHaveLength(2);
    expect(diffs[0].category).toBe("test-result drift");
    expect(diffs[0].severity).toBe("high");
    expect(diffs[0].stage).toBe("testing");
    expect(diffs[0].evidence).toContain("actual: 2 failed, 1 passed");
    expect(diffs[1].category).toBe("quality-gate drift");
    expect(diffs[1].evidence).toContain("gate decision diverged");
  });

  it("flags deterministic stages without a recorded snapshot as missing evidence", () => {
    const manifest: ReplayManifest = {
      exists: true,
      run_id: "RUN-1",
      created_at: "2026-08-14T00:00:00Z",
      stages: [
        {
          stage: "validating_patch",
          kind: "deterministic",
          status: "succeeded",
          output_hash: "abc",
          decision: {},
          captured: false,
        },
      ],
    };
    const result: ReplayResult = {
      replay_id: "REP-1",
      run_id: "RUN-1",
      mode: "exact",
      verdict: "incomplete",
      checks_total: 0,
      checks_passed: 0,
      checks_failed: 0,
      checks_skipped: 0,
      checks_not_replayable: 0,
      stages_matched: 0,
      stages_diverged: 0,
      divergences: [],
      summary: "",
      created_at: "2026-08-14T00:00:00Z",
    };
    const diffs = differencesFromResult(manifest, result);
    expect(diffs.some((d) => d.category === "missing evidence")).toBe(true);
    expect(diffs[0].severity).toBe("medium");
  });

  it("derives stage-output drift from non-matching stage comparisons", () => {
    const result: ReplayResult = {
      replay_id: "REP-1",
      run_id: "RUN-1",
      mode: "compare",
      verdict: "drift",
      checks_total: 0,
      checks_passed: 0,
      checks_failed: 0,
      checks_skipped: 0,
      checks_not_replayable: 0,
      stages_matched: 2,
      stages_diverged: 1,
      divergences: [],
      summary: "",
      created_at: "2026-08-14T00:00:00Z",
      stage_comparisons: [
        { stage: "planning", kind: "llm_proposed", recorded_hash: "aaa", replay_hash: "bbb", matched: false, detail: "plan differed" },
        { stage: "testing", kind: "deterministic", recorded_hash: "ccc", replay_hash: "ccc", matched: true, detail: "" },
      ],
    };
    const diffs = differencesFromResult(null, result);
    expect(diffs).toHaveLength(1);
    expect(diffs[0].category).toBe("stage-output drift");
    expect(diffs[0].stage).toBe("planning");
    expect(diffs[0].original).toBe("aaa");
    expect(diffs[0].replay).toBe("bbb");
  });

  it("deduplicates a stage that appears in both checks and comparisons", () => {
    const result: ReplayResult = {
      replay_id: "REP-1",
      run_id: "RUN-1",
      mode: "deterministic",
      verdict: "drift",
      checks_total: 1,
      checks_passed: 0,
      checks_failed: 1,
      checks_skipped: 0,
      checks_not_replayable: 0,
      stages_matched: 0,
      stages_diverged: 0,
      divergences: [],
      summary: "",
      created_at: "2026-08-14T00:00:00Z",
      checks: [
        { stage: "testing", check: "testing", status: "failed", expected: "pass", actual: "fail", note: "" },
      ],
      stage_comparisons: [
        { stage: "testing", kind: "deterministic", recorded_hash: "aaa", replay_hash: "bbb", matched: false, detail: "" },
      ],
    };
    const diffs = differencesFromResult(null, result);
    // testing appears once (check-based diff wins; stage comparison deduped).
    expect(diffs.filter((d) => d.stage === "testing")).toHaveLength(1);
  });

  it("is bounded to 50 differences", () => {
    const checks: ReplayCheck[] = Array.from({ length: 80 }, (_, i) => ({
      stage: `s${i}`,
      check: `check_${i}`,
      status: "failed",
      expected: "e",
      actual: "a",
      note: "",
    }));
    const result: ReplayResult = {
      replay_id: "REP-1",
      run_id: "RUN-1",
      mode: "exact",
      verdict: "drift",
      checks_total: 80,
      checks_passed: 0,
      checks_failed: 80,
      checks_skipped: 0,
      checks_not_replayable: 0,
      stages_matched: 0,
      stages_diverged: 0,
      divergences: [],
      summary: "",
      created_at: "2026-08-14T00:00:00Z",
      checks,
    };
    expect(differencesFromResult(null, result).length).toBeLessThanOrEqual(50);
  });
});

describe("replayStageViews", () => {
  it("merges manifest stages with comparison statuses", () => {
    const views = replayStageViews(
      [
        { stage: "planning", kind: "llm_proposed", status: "succeeded", output_hash: "h1", decision: {}, captured: false },
        { stage: "testing", kind: "deterministic", status: "succeeded", output_hash: "h2", decision: {}, captured: true },
      ],
      [
        { stage: "planning", kind: "llm_proposed", recorded_hash: "h1", replay_hash: "h1", matched: true, detail: "" },
        { stage: "testing", kind: "deterministic", recorded_hash: "h2", replay_hash: "h3", matched: false, detail: "drifted" },
      ]
    );
    expect(views).toHaveLength(2);
    expect(views[0].matched).toBe(true);
    expect(views[1].matched).toBe(false);
    expect(views[1].replayHash).toBe("h3");
  });

  it("includes comparison-only stages (COMPARE mode)", () => {
    const views = replayStageViews(
      [{ stage: "planning", kind: "llm_proposed", status: "succeeded", output_hash: "h1", decision: {}, captured: false }],
      [
        { stage: "planning", kind: "llm_proposed", recorded_hash: "h1", replay_hash: "h1", matched: true, detail: "" },
        { stage: "reviewing", kind: "llm_proposed", recorded_hash: "r1", replay_hash: "r2", matched: false, detail: "" },
      ]
    );
    expect(views.map((v) => v.stage)).toEqual(["planning", "reviewing"]);
  });
});

describe("replayRunReducer (start-replay state machine)", () => {
  it("transitions idle → starting → running → completed", () => {
    let state = replayRunReducer(undefined as never, { type: "start", mode: "exact" });
    expect(state.phase).toBe("starting");
    expect(state.mode).toBe("exact");

    const result = {
      replay_id: "REP-1",
      run_id: "RUN-1",
      mode: "exact" as const,
      verdict: "match" as const,
      checks_total: 8,
      checks_passed: 8,
      checks_failed: 0,
      checks_skipped: 0,
      checks_not_replayable: 0,
      stages_matched: 0,
      stages_diverged: 0,
      divergences: [],
      summary: "",
      created_at: "2026-08-14T00:00:00Z",
    };
    state = replayRunReducer(state, { type: "complete", result });
    expect(state.phase).toBe("completed");
    expect(state.result?.verdict).toBe("match");
  });

  it("transitions to failed with the error preserved", () => {
    let state = replayRunReducer(undefined as never, { type: "start", mode: "deterministic" });
    state = replayRunReducer(state, { type: "fail", error: "boom" });
    expect(state.phase).toBe("failed");
    expect(state.error).toBe("boom");
    expect(state.result).toBeNull();
  });

  it("resets to idle", () => {
    let state = replayRunReducer(undefined as never, { type: "start", mode: "compare" });
    state = replayRunReducer(state, { type: "reset" });
    expect(state.phase).toBe("idle");
    expect(state.mode).toBeNull();
  });
});
