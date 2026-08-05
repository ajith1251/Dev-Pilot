/**
 * Phase 20 — Workstream D: org-graph → React Flow view-model tests.
 *
 * Pure, DOM-free unit tests for the mappers in `orgGraphModel.ts`.
 */
import { describe, expect, it } from "vitest";
import {
  IN_REPOSITORY_REL,
  REPO_PREFIX,
  VIRTUAL_EDGE_PREFIX,
  clusterVirtualEdges,
  crossEdgesToVizEdges,
  mergeOrgGraph,
  orgEdgesToVizEdges,
  orgNodesToVizNodes,
  repoNodeId,
  repoVizId,
  reposToVizNodes,
} from "./orgGraphModel";
import type { OrgCrossEdge, OrgRepository } from "@/lib/api/organizationGraph";
import type { GraphEdge, GraphNode } from "@/lib/api/engineeringGraph";

const repo = (id: string, name = id): OrgRepository => ({
  repository_id: id,
  namespace_id: `ns-${id}`,
  organization_id: "default",
  name,
  path: `/org/${id}`,
  source_type: "local",
  created_at: "2026-01-01T00:00:00Z",
});

const crossEdge = (id: string, a: string, b: string): OrgCrossEdge => ({
  edge_id: id,
  source_repository_id: a,
  target_repository_id: b,
  relationship: "shares_library",
  weight: 0.8,
  graph_version: 3,
  created_at: "2026-01-01T00:00:00Z",
});

const graphNode = (id: string, type = "file", repositoryId?: string): GraphNode => ({
  node_id: id,
  node_type: type,
  name: `${id}-name`,
  qualified_name: id,
  kind: "file",
  source_ref: `ref://${id}`,
  source_type: "code",
  status: "active",
  graph_version: 3,
  repository_id: repositoryId,
  payload: {},
  provenance: {},
  created_at: "2026-01-01T00:00:00Z",
});

const graphEdge = (
  id: string | undefined,
  source: string,
  target: string,
  relationship = "imports"
): GraphEdge => ({
  edge_id: id,
  source_id: source,
  target_id: target,
  relationship,
  weight: 1,
  graph_version: 3,
  created_at: "2026-01-01T00:00:00Z",
});

describe("repo id helpers", () => {
  it("builds the repo:<id> viz id", () => {
    expect(repoVizId("acme-api")).toBe(`${REPO_PREFIX}acme-api`);
  });

  it("builds the REPO::<id> backend node id, capped at 40 chars", () => {
    expect(repoNodeId("acme-api")).toBe("REPO::acme-api");
    const long = "a".repeat(80);
    expect(repoNodeId(long)).toHaveLength(40);
    expect(repoNodeId(long)).toBe("REPO::" + "a".repeat(34));
  });
});

describe("reposToVizNodes", () => {
  it("maps namespaces to repository VizNodes", () => {
    const out = reposToVizNodes([repo("a", "Acme A"), repo("b")]);
    expect(out).toHaveLength(2);
    expect(out[0]).toMatchObject({
      id: "repo:a",
      label: "Acme A",
      nodeType: "repository",
      repositoryId: "a",
      sublabel: "local",
    });
    expect(out[1].id).toBe("repo:b");
  });
});

describe("crossEdgesToVizEdges", () => {
  it("maps cross-edges to repo-node endpoints", () => {
    const out = crossEdgesToVizEdges([crossEdge("e1", "a", "b")]);
    expect(out).toEqual([
      {
        id: "e1",
        source: "repo:a",
        target: "repo:b",
        relationship: "shares_library",
        weight: 0.8,
      },
    ]);
  });
});

describe("orgNodesToVizNodes", () => {
  it("maps result nodes with their owning repository", () => {
    const out = orgNodesToVizNodes([graphNode("n1", "file", "repo-a")]);
    expect(out[0]).toMatchObject({
      id: "n1",
      nodeType: "file",
      repositoryId: "repo-a",
      sublabel: "ref://n1",
    });
  });

  it("falls back to the node id as label when name is empty", () => {
    const n = graphNode("n2", "file", "repo-a");
    n.name = "";
    expect(orgNodesToVizNodes([n])[0].label).toBe("n2");
  });
});

describe("orgEdgesToVizEdges", () => {
  it("maps result edges and synthesizes an id when missing", () => {
    const out = orgEdgesToVizEdges([
      graphEdge("e1", "a", "b"),
      graphEdge(undefined, "c", "d", "references"),
    ]);
    expect(out[0].id).toBe("e1");
    expect(out[1]).toMatchObject({
      id: "c->d",
      source: "c",
      target: "d",
      relationship: "references",
    });
  });
});

describe("clusterVirtualEdges", () => {
  it("attaches each result node to its owning repository via a virtual edge", () => {
    const out = clusterVirtualEdges(
      [graphNode("n1", "file", "repo-a"), graphNode("n2", "file")],
      [graphEdge("e1", "a", "b")]
    );
    expect(out).toHaveLength(2);
    expect(out[1]).toMatchObject({
      id: `${VIRTUAL_EDGE_PREFIX}repo-a:n1`,
      source: "repo:repo-a",
      target: "n1",
      relationship: IN_REPOSITORY_REL,
      weight: 0.3,
      virtual: true,
    });
  });

  it("skips nodes without an owning repository or with default", () => {
    expect(clusterVirtualEdges([graphNode("n1")], [])).toHaveLength(0);
    expect(
      clusterVirtualEdges([graphNode("n2", "file", "default")], [])
    ).toHaveLength(0);
  });
});

describe("mergeOrgGraph", () => {
  it("dedup-merges nodes and edges add-only", () => {
    const base = {
      nodes: [reposToVizNodes([repo("a")])[0]],
      edges: [crossEdgesToVizEdges([crossEdge("e1", "a", "b")])[0]],
    };
    const incoming = {
      nodes: [reposToVizNodes([repo("a")])[0], orgNodesToVizNodes([graphNode("n1")])[0]],
      edges: [crossEdgesToVizEdges([crossEdge("e1", "a", "b")])[0]],
    };
    const out = mergeOrgGraph(base, incoming);
    expect(out.nodes).toHaveLength(2);
    expect(out.edges).toHaveLength(1);
  });
});
