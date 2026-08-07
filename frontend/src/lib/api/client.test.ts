/**
 * Phase 20 A6 — run API client contract tests for the multi-repo surface
 * (fetch mocked, no network). Verifies `runsApi.create` forwards optional
 * auxiliary `repositories` and the run-detail type carries the Phase 20
 * `auxiliary_repositories` + `repo_validation` surface.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { operationsApi, orgApi, runsApi } from "./client";

function mockFetch(json: unknown) {
  const fn = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => json,
    text: async () => JSON.stringify(json),
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("runsApi.create (Phase 20 A6)", () => {
  it("POSTs /api/v1/runs with auxiliary repositories when provided", async () => {
    const fetchMock = mockFetch({
      success: true,
      data: { run_id: "RUN-1", status: "running" },
    });
    const result = await runsApi.create({
      title: "Multi-repo task",
      repository: "/tmp/primary",
      repositories: [
        { repository_id: "repo-b", source: "local", path: "/tmp/repo-b" },
        {
          repository_id: "repo-c",
          source: "github",
          owner: "acme",
          repo: "lib-c",
          ref: "main",
          depth: 2,
        },
      ],
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/runs");
    expect((init as RequestInit).method).toBe("POST");
    const body = JSON.parse((init as RequestInit).body as string);
    expect(body.repositories).toEqual([
      { repository_id: "repo-b", source: "local", path: "/tmp/repo-b" },
      {
        repository_id: "repo-c",
        source: "github",
        owner: "acme",
        repo: "lib-c",
        ref: "main",
        depth: 2,
      },
    ]);
    expect(body.repository).toBe("/tmp/primary");
    expect(result.data.run_id).toBe("RUN-1");
  });

  it("omits repositories when none are supplied", async () => {
    const fetchMock = mockFetch({
      success: true,
      data: { run_id: "RUN-2", status: "running" },
    });
    await runsApi.create({ title: "Single-repo task" });
    const [, init] = fetchMock.mock.calls[0];
    const body = JSON.parse((init as RequestInit).body as string);
    expect(body.repositories).toBeUndefined();
  });

  it("forwards acceptance criteria + execution budget (Phase 20A6)", async () => {
    const fetchMock = mockFetch({
      success: true,
      data: { run_id: "RUN-3", status: "running" },
    });
    await runsApi.create({
      title: "Budgeted task",
      acceptance_criteria: ["c1", "c2"],
      execution_budget: { max_iterations: 3, max_replans: 2 },
    });
    const [, init] = fetchMock.mock.calls[0];
    const body = JSON.parse((init as RequestInit).body as string);
    expect(body.acceptance_criteria).toEqual(["c1", "c2"]);
    expect(body.execution_budget).toEqual({ max_iterations: 3, max_replans: 2 });
  });
});

describe("operationsApi (Phase 20B)", () => {
  it("GETs /api/v1/operations/status and surfaces the readiness summary", async () => {
    const fetchMock = mockFetch({
      success: true,
      data: {
        summary: {
          ready: true,
          status: "ok",
          error_subsystems: {},
          checked_at: "2026-08-07T00:00:00Z",
        },
        subsystems: {
          providers: { status: "ok", detail: { configured_count: 3 } },
          database: { status: "unknown", detail: { configured: false } },
        },
      },
    });
    const result = await operationsApi.status();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/operations/status");
    expect(result.data.summary.ready).toBe(true);
    expect(result.data.subsystems.providers.status).toBe("ok");
  });

  it("GETs /api/v1/operations/metrics with run/resource sections", async () => {
    const fetchMock = mockFetch({
      success: true,
      data: {
        uptime_seconds: 42.5,
        runs: { active: 1, started_total: 3, completed_total: 2 },
        resources: { memory_mb: 128.4, active_ws_connections: 2, open_tasks: 9 },
        recorded_at: 123,
      },
    });
    const result = await operationsApi.metrics();
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/operations/metrics");
    expect(result.data.runs.completed_total).toBe(2);
    expect(result.data.resources.active_ws_connections).toBe(2);
  });

  it("GETs /api/v1/operations/startup-validation findings", async () => {
    const fetchMock = mockFetch({
      success: true,
      data: {
        strict: false,
        error_count: 0,
        warning_count: 1,
        findings: [{ severity: "warning", code: "CONFIG_NO_PROVIDER_KEYS", message: "no keys" }],
      },
    });
    const result = await operationsApi.startupValidation();
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/operations/startup-validation");
    expect(result.data.findings[0].code).toBe("CONFIG_NO_PROVIDER_KEYS");
  });

  it("fetches the /health/ready readiness probe", async () => {
    const fetchMock = mockFetch({
      success: true,
      status: "ok",
      ready: true,
      error_subsystems: {},
      checked_at: "2026-08-07T00:00:00Z",
    });
    const result = await operationsApi.ready();
    expect(fetchMock.mock.calls[0][0]).toBe("/health/ready");
    expect(result.ready).toBe(true);
  });
});

describe("orgApi.repositories (Phase 20A6)", () => {
  it("GETs /api/v1/graph/org/repositories with search + pagination params", async () => {
    const fetchMock = mockFetch({
      success: true,
      data: {
        repositories: [{ repository_id: "repo-b" }],
        count: 1,
        total: 42,
        limit: 25,
        offset: 0,
      },
    });
    const result = await orgApi.repositories({ q: "api", limit: 25, offset: 0 });
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/graph/org/repositories?q=api&limit=25&offset=0");
    expect(result.data.total).toBe(42);
    expect(result.data.repositories[0].repository_id).toBe("repo-b");
  });

  it("GETs per-repository EKG stats by id", async () => {
    const fetchMock = mockFetch({
      success: true,
      data: { repository_id: "repo-b", node_count: 5, edge_count: 4 },
    });
    const result = await orgApi.repository("repo-b");
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/graph/org/repositories/repo-b");
    expect(result.data.node_count).toBe(5);
  });
});
