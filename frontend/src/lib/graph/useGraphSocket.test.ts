/**
 * Phase 19C — WebSocket URL derivation (pure, DOM-free).
 */
import { describe, expect, it } from "vitest";
import { deriveGraphWsUrl } from "./useGraphSocket";

describe("deriveGraphWsUrl", () => {
  it("maps an http API base to ws", () => {
    expect(deriveGraphWsUrl("http://api.example.com", "http:", "x")).toBe(
      "ws://api.example.com/api/v1/ws/graph"
    );
  });

  it("maps an https API base to wss", () => {
    expect(deriveGraphWsUrl("https://api.example.com", "http:", "x")).toBe(
      "wss://api.example.com/api/v1/ws/graph"
    );
  });

  it("falls back to the same-origin host for http pages", () => {
    expect(deriveGraphWsUrl(undefined, "http:", "localhost:3000")).toBe(
      "ws://localhost:3000/api/v1/ws/graph"
    );
  });

  it("falls back to the same-origin host for https pages", () => {
    expect(deriveGraphWsUrl("", "https:", "pilot.example.com")).toBe(
      "wss://pilot.example.com/api/v1/ws/graph"
    );
  });
});
