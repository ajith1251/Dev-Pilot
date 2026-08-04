/**
 * Phase 19C — organization-graph API client URL/contract tests
 * (fetch mocked, no network).
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { orgGraphApi } from "./organizationGraph";

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

describe("orgGraphApi.stats / repositories / crossEdges", () => {
  it("stats hits /api/v1/graph/org/stats and unwraps data", async () => {
    const fetchMock = mockFetch({
      success: true,
      data: {
        organization_id: "default",
        repository_count: 2,
        node_count: 10,
        edge_count: 8,
        cross_edge_count: 1,
        cross_relationship_types: { imports_package: 1 },
        repositories: ["a", "b"],
        last_updated: "2026-01-01T00:00:00Z",
      },
    });
    const res = await orgGraphApi.stats();
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/graph/org/stats", expect.anything());
    expect(res.repository_count).toBe(2);
  });

  it("repositories unwraps the nested list", async () => {
    const fetchMock = mockFetch({
      success: true,
      data: { repositories: [{ repository_id: "a", namespace_id: "ns-a" }] },
    });
    const res = await orgGraphApi.repositories();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/graph/org/repositories",
      expect.anything()
    );
    expect(res).toHaveLength(1);
    expect(res[0].repository_id).toBe("a");
  });

  it("crossEdges unwraps the nested list", async () => {
    const fetchMock = mockFetch({
      success: true,
      data: { cross_edges: [{ edge_id: "e1" }] },
    });
    const res = await orgGraphApi.crossEdges();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/graph/org/cross-edges",
      expect.anything()
    );
    expect(res).toHaveLength(1);
  });
});

describe("orgGraphApi.query (§19A scope routing)", () => {
  it("sends scope + limit params, defaulting to auto/10", async () => {
    const fetchMock = mockFetch({ success: true, data: { nodes: [], edges: [] } });
    await orgGraphApi.query("find the entrypoint");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/graph/org/query?q=find+the+entrypoint&scope=auto&limit=10",
      expect.anything()
    );
  });

  it("honors explicit organization scope and limit", async () => {
    const fetchMock = mockFetch({ success: true, data: { nodes: [], edges: [] } });
    await orgGraphApi.query("who imports lib-x", { scope: "organization", limit: 25 });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/graph/org/query?q=who+imports+lib-x&scope=organization&limit=25",
      expect.anything()
    );
  });
});

describe("orgGraphApi.traversal", () => {
  it("URL-encodes the node id and sends depth + max_nodes", async () => {
    const fetchMock = mockFetch({
      success: true,
      data: { root: "REPO::a", depth: 2, nodes: [], edges: [], truncated: false, total_nodes: 0, version: 1 },
    });
    await orgGraphApi.traversal("REPO::a", { depth: 2, maxNodes: 250 });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/graph/org/traversal/REPO%3A%3Aa?depth=2&max_nodes=250",
      expect.anything()
    );
  });
});

describe("orgGraphApi.acquireMulti (§19C multi-repo acquisition)", () => {
  it("POSTs the flat manifest to /org/acquire-multi", async () => {
    const fetchMock = mockFetch({
      success: true,
      data: {
        organization_id: "default",
        repositories_acquired: 2,
        namespaces: [],
        cross_edges: [],
        relationships: 1,
        ingested_files: 4,
        persisted_records: 3,
        scope: "organization",
      },
    });
    const res = await orgGraphApi.acquireMulti({
      repositories: [
        {
          repository_id: "api",
          name: "api",
          source: "local",
          path: "C:/tmp/api",
          relationships: [
            { target_repository_id: "web", relationship: "imports_package" },
          ],
        },
        { repository_id: "web", name: "web", source: "local", path: "C:/tmp/web" },
      ],
    });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/graph/org/acquire-multi");
    expect(init.method).toBe("POST");
    const body = JSON.parse((init.body as string) ?? "[]");
    expect(body).toHaveLength(2);
    expect(body[0].repository_id).toBe("api");
    expect(body[0].relationships[0].target_repository_id).toBe("web");
    expect(res.repositories_acquired).toBe(2);
    expect(res.ingested_files).toBe(4);
  });

  it("rejects an empty manifest client-side by raising before fetch", async () => {
    const fetchMock = mockFetch({ success: true, data: {} });
    await expect(orgGraphApi.acquireMulti({ repositories: [] })).rejects.toThrow();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
