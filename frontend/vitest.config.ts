import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";

/**
 * Vitest for Phase 19C deterministic graph logic.
 *
 * `next build` remains the frontend type gate; vitest covers the pure,
 * DOM-free model/contract logic (layout, filtering, registries, WS URL,
 * API client URL shapes).
 */
export default defineConfig({
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
    globals: false,
  },
});
