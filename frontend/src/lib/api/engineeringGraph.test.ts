/**
 * Phase 19C — API client URL/contract tests (fetch mocked, no network).
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { graphApi } from "./engineeringGraph";

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

describe("graphApi.diff (§6 timeline)", () => {
  it("builds from_version + to_version query params", async () => {
    const fetchMock = mockFetch({
      success: true,
      data: { from_version: 3, to_version: 7, added_nodes: [], removed_nodes: [], changed_edges: [], counts: { added: 0, removed: 0, changed_edges: 0 }, per_version: [] },
    });
    const res = await graphApi.diff(3, 7);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/graph/diff?from_version=3&to_version=7",
      expect.objectContaining({ headers: expect.anything() })
    );
    expect(res.from_version).toBe(3);
    expect(res.to_version).toBe(7);
  });

  it("omits to_version when not provided (defaults to current)", async () => {
    const fetchMock = mockFetch({ success: true, data: { from_version: 5, to_version: 9 } });
    await graphApi.diff(5);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/graph/diff?from_version=5",
      expect.anything()
    );
  });
});

describe("graphApi core endpoints", () => {
  it("version hits /api/v1/graph/version", async () => {
    const fetchMock = mockFetch({ success: true, data: { version: {}, history: [] } });
    await graphApi.version();
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/graph/version", expect.anything());
  });

  it("neighborhood URL-encodes the node id and depth/max params", async () => {
    const fetchMock = mockFetch({ success: true, data: { nodes: [], edges: [], truncated: false, total_nodes: 0, version: 1 } });
    await graphApi.neighborhood("EKN-A/B C", 2, 60);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/graph/neighborhood/EKN-A%2FB%20C?depth=2&max_nodes=60",
      expect.anything()
    );
  });
});
