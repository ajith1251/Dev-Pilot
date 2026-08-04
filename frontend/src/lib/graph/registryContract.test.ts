/**
 * Phase 19C — frontend ↔ backend registry contract.
 *
 * The EKG model enums (`EKNodeType` / `EKRelationshipType` in
 * backend/app/models/engineering_graph.py) define the authoritative type
 * vocabulary. These tests freeze that vocabulary here so the frontend color /
 * category registries can never silently drift out of sync.
 *
 * Update the lists below only when the backend enums change.
 */
import { describe, expect, it } from "vitest";
import {
  NODE_CATEGORY,
  NODE_HEX,
  RELATIONSHIP_HEX,
  hexFor,
} from "./graphModel";

// Mirror of backend EKNodeType (all 28 values).
const BACKEND_NODE_TYPES = [
  "repository",
  "folder",
  "file",
  "module",
  "package",
  "class",
  "interface",
  "function",
  "method",
  "requirement",
  "acceptance_criterion",
  "implementation_plan",
  "plan_version",
  "goal",
  "patch",
  "commit_candidate",
  "test",
  "test_suite",
  "review_finding",
  "quality_gate",
  "evidence",
  "consensus",
  "contradiction",
  "notebook_entry",
  "decision",
  "run",
  "agent",
  "repository_memory",
];

// Mirror of backend EKRelationshipType (all 27 values).
const BACKEND_RELATIONSHIP_TYPES = [
  "calls",
  "imports",
  "contains",
  "depends_on",
  "implements",
  "tests",
  "references",
  "affects",
  "modifies",
  "satisfies",
  "created_during",
  "produced_by",
  "derived_from",
  "supports",
  "contradicts",
  "supersedes",
  "uses_memory",
  "validated_by",
  "reviewed_by",
  "approved_by",
  "depends_on_repository",
  "shares_library",
  "imports_package",
  "implements_shared_interface",
  "references_shared_component",
  "uses_shared_memory",
  "calls_external_service",
];

describe("node type registry contract (EKNodeType)", () => {
  it("covers every backend node type with a color", () => {
    for (const t of BACKEND_NODE_TYPES) {
      expect(NODE_HEX[t], `missing color for node type '${t}'`).toMatch(
        /^#[0-9a-f]{6}$/i
      );
    }
  });

  it("covers every backend node type with a category", () => {
    for (const t of BACKEND_NODE_TYPES) {
      expect(NODE_CATEGORY[t], `missing category for node type '${t}'`).toBeTruthy();
    }
  });

  it("hexFor returns a hex color for every backend node type", () => {
    for (const t of BACKEND_NODE_TYPES) {
      expect(hexFor(t)).toMatch(/^#[0-9a-f]{6}$/i);
    }
  });
});

describe("relationship registry contract (EKRelationshipType)", () => {
  it("covers every backend relationship type with a color", () => {
    for (const r of BACKEND_RELATIONSHIP_TYPES) {
      expect(RELATIONSHIP_HEX[r], `missing color for relationship '${r}'`).toMatch(
        /^#[0-9a-f]{6}$/i
      );
    }
  });
});
