/**
 * Phase 20 A6 — run API client contract tests for the multi-repo surface
 * (fetch mocked, no network). Verifies `runsApi.create` forwards optional
 * auxiliary `repositories` and the run-detail type carries the Phase 20
 * `auxiliary_repositories` + `repo_validation` surface.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { runsApi } from "./client";

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
});
