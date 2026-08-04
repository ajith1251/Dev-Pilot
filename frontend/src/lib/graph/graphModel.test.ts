/**
 * Phase 19C — deterministic tests for the pure graph model.
 *
 * No DOM, no network, no live LLM. Layout is made deterministic via a seeded
 * PRNG inside `computeForceLayout`.
 */
import { describe, expect, it } from "vitest";
import {
  applyViewFilters,
  computeForceLayout,
  hexFor,
  matchesSearch,
  nodeTypeLabel,
  relHex,
  relLabel,
  snapshotFacets,
  summarizeDiff,
  truncate,
  type VizEdge,
  type VizNode,
} from "./graphModel";

function node(id: string, nodeType: string, repositoryId?: string): VizNode {
  return {
    id,
    label: `${nodeType}-${id}`,
    nodeType,
    repositoryId,
    sublabel: `ref://${id}`,
  };
}

function edge(
  id: string,
  source: string,
  target: string,
  relationship: string
): VizEdge {
  return { id, source, target, relationship, weight: 1 };
}

describe("label + color helpers", () => {
  it("converts snake_case node types to human labels", () => {
    expect(nodeTypeLabel("acceptance_criterion")).toBe("Acceptance Criterion");
    expect(nodeTypeLabel("repository")).toBe("Repository");
  });

  it("converts snake_case relationships to human labels", () => {
    expect(relLabel("depends_on_repository")).toBe("Depends On Repository");
    expect(relLabel("validated_by")).toBe("Validated By");
  });

  it("returns a color for known types and a neutral fallback otherwise", () => {
    expect(hexFor("repository")).toMatch(/^#[0-9a-f]{6}$/i);
    expect(hexFor("unknown_type")).toBe("#94a3b8");
    expect(relHex("calls")).toMatch(/^#[0-9a-f]{6}$/i);
    expect(relHex("unknown_rel")).toBe("#94a3b8");
  });

  it("truncates long strings and shortens neither below the bound", () => {
    expect(truncate("a".repeat(100), 10)).toHaveLength(11);
    expect(truncate("short", 10)).toBe("short");
  });
});

describe("computeForceLayout (d3-force, seeded)", () => {
  const nodes = [
    node("a", "file", "repoA"),
    node("b", "function", "repoA"),
    node("c", "test", "repoA"),
    node("d", "requirement", "repoB"),
  ];
  const edges = [
    edge("e1", "a", "b", "imports"),
    edge("e2", "b", "c", "tests"),
    edge("e3", "b", "d", "satisfies"),
  ];

  it("returns one point per node", () => {
    const pts = computeForceLayout(nodes, edges, { iterations: 60 });
    expect(pts).toHaveLength(4);
    for (const p of pts) {
      expect(typeof p.x).toBe("number");
      expect(typeof p.y).toBe("number");
      expect(Number.isFinite(p.x)).toBe(true);
    }
    expect(new Set(pts.map((p) => p.id))).toEqual(new Set(["a", "b", "c", "d"]));
  });

  it("is deterministic for identical inputs", () => {
    const a = computeForceLayout(nodes, edges, { iterations: 80 });
    const b = computeForceLayout(nodes, edges, { iterations: 80 });
    expect(JSON.stringify(a)).toBe(JSON.stringify(b));
  });

  it("seeds new nodes from initialPositions and keeps totals stable", () => {
    const first = computeForceLayout(nodes, edges, { iterations: 60 });
    const seedMap = Object.fromEntries(first.map((p) => [p.id, { x: p.x, y: p.y }]));
    const grownNodes = [...nodes, node("e", "evidence", "repoB")];
    const grownEdges = [...edges, edge("e4", "d", "e", "derived_from")];
    const grown = computeForceLayout(grownNodes, grownEdges, {
      iterations: 60,
      initialPositions: seedMap,
    });
    expect(grown).toHaveLength(5);
    expect(grown.map((p) => p.id).sort()).toEqual(
      ["a", "b", "c", "d", "e"].sort()
    );
  });

  it("ignores dangling edges without crashing", () => {
    const pts = computeForceLayout(nodes, [
      edge("dangling", "missing", "a", "contains"),
    ], { iterations: 20 });
    expect(pts).toHaveLength(4);
  });

  it("handles large snapshots (perf smoke)", () => {
    const bigNodes: VizNode[] = [];
    for (let i = 0; i < 500; i++) bigNodes.push(node(`n${i}`, "file", "repo"));
    const bigEdges: VizEdge[] = [];
    for (let i = 1; i < 500; i++) {
      bigEdges.push(edge(`e${i}`, `n${i - 1}`, `n${i}`, "imports"));
    }
    const pts = computeForceLayout(bigNodes, bigEdges, { iterations: 30 });
    expect(pts).toHaveLength(500);
  });
});

describe("applyViewFilters", () => {
  const nodes = [
    node("f1", "file", "repoA"),
    node("f2", "file", "repoB"),
    node("fn1", "function", "repoA"),
    node("req1", "requirement", "repoA"),
  ];
  const edges = [
    edge("e1", "f1", "fn1", "imports"),
    edge("e2", "f1", "req1", "satisfies"),
    edge("e3", "f2", "fn1", "depends_on"),
  ];

  it("filters by node type and drops orphaned edges", () => {
    const out = applyViewFilters(nodes, edges, {
      nodeTypes: new Set(["file", "function"]),
    });
    expect(out.nodes.map((n) => n.id).sort()).toEqual(["f1", "f2", "fn1"]);
    expect(out.edges.map((e) => e.id).sort()).toEqual(["e1", "e3"]);
    expect(out.hiddenNodes).toBe(1);
    expect(out.hiddenEdges).toBe(1);
  });

  it("filters by relationship type", () => {
    const out = applyViewFilters(nodes, edges, {
      relationships: new Set(["satisfies"]),
    });
    expect(out.edges.map((e) => e.id)).toEqual(["e2"]);
    expect(out.hiddenNodes).toBe(0);
    expect(out.hiddenEdges).toBe(2);
  });

  it("filters by repository", () => {
    const out = applyViewFilters(nodes, edges, {
      repositories: new Set(["repoA"]),
    });
    expect(out.nodes.map((n) => n.id).sort()).toEqual(["f1", "fn1", "req1"]);
    expect(out.edges.map((e) => e.id).sort()).toEqual(["e1", "e2"]);
  });

  it("filters by case-insensitive search across label/id/ref", () => {
    expect(matchesSearch(nodes[0], "f1")).toBe(true);
    expect(matchesSearch(nodes[0], "REPOA")).toBe(true);
    expect(matchesSearch(nodes[0], "ref://f1")).toBe(true);
    expect(matchesSearch(nodes[0], "zzz")).toBe(false);

    const out = applyViewFilters(nodes, edges, { search: "requirement" });
    expect(out.nodes.map((n) => n.id)).toEqual(["req1"]);
    expect(out.edges).toHaveLength(0);
  });

  it("empty filter sets mean 'no constraint'", () => {
    const out = applyViewFilters(nodes, edges, {
      nodeTypes: new Set(),
      relationships: new Set(),
      repositories: new Set(),
      search: "",
    });
    expect(out.nodes).toHaveLength(nodes.length);
    expect(out.edges).toHaveLength(edges.length);
    expect(out.hiddenNodes + out.hiddenEdges).toBe(0);
  });

  it("scales to large graphs (perf smoke)", () => {
    const bigNodes: VizNode[] = [];
    for (let i = 0; i < 2000; i++) bigNodes.push(node(`n${i}`, "file", "repo"));
    const bigEdges: VizEdge[] = [];
    for (let i = 1; i < 2000; i++) {
      bigEdges.push(edge(`e${i}`, `n${i - 1}`, `n${i}`, "imports"));
    }
    const out = applyViewFilters(bigNodes, bigEdges, { search: "n19" });
    expect(out.nodes.length).toBeGreaterThan(0);
    expect(out.nodes.length).toBeLessThan(bigNodes.length);
  });
});

describe("snapshotFacets", () => {
  it("returns distinct, sorted node types / relationships / repositories", () => {
    const facets = snapshotFacets(
      [node("a", "file", "repoB"), node("b", "file", "repoA"), node("c", "test")],
      [edge("e1", "a", "c", "tests"), edge("e2", "c", "a", "tests")]
    );
    expect(facets.nodeTypes).toEqual(["file", "test"]);
    expect(facets.relationships).toEqual(["tests"]);
    expect(facets.repositories).toEqual(["default", "repoA", "repoB"]);
  });
});

describe("summarizeDiff", () => {
  it("summarizes a version change-set", () => {
    const s = summarizeDiff({
      from_version: 2,
      to_version: 5,
      added_nodes: [{ node_id: "x", name: "x", node_type: "file" }],
      removed_nodes: [],
      changed_edges: [{ edge_id: "e" }],
      counts: { added: 1, removed: 2, changed_edges: 3 },
      per_version: [{ version: 3, run_id: "r", summary: "s", added: 1, removed: 0, changed_edges: 0 }],
    });
    expect(s.label).toBe("v2 → v5");
    expect(s.added).toBe(1);
    expect(s.removed).toBe(2);
    expect(s.changedEdges).toBe(3);
    expect(s.versions).toBe(1);
  });
});
