/**
 * DevPilot API Client
 *
 * Centralized typed client for communicating with the FastAPI backend.
 * All API calls go through this module — no raw fetch() in components.
 *
 * Configuration:
 *   NEXT_PUBLIC_API_BASE_URL — base URL for the FastAPI backend
 *   Defaults to "" (same origin via Next.js rewrite proxy) for production.
 */

const BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "";

// ── Types ─────────────────────────────────────────────────────

export type RunStatus =
  | "pending" | "running" | "approved" | "rejected"
  | "needs_human_review" | "failed" | "cancelled";

export type StageType =
  | "initializing" | "acquiring_repository" | "analyzing_repository"
  | "analyzing_task" | "planning" | "retrieving_context" | "coding"
  | "validating_patch" | "applying_patch" | "testing" | "repairing"
  | "reviewing" | "quality_gate" | "completed" | "failed";

export type RunSourceType = "user_task" | "github_issue";

export interface RunSource {
  source_type: RunSourceType;
  title: string;
  description?: string;
  repository_path?: string;
  issue_number?: number;
  issue_url?: string;
}

export interface StageResult {
  stage: string;
  status: string;
  started_at?: string | null;
  finished_at?: string | null;
  duration_ms?: number | null;
  error?: string | null;
}

export interface RunEvent {
  event_id: string;
  event_type: string;
  stage?: string | null;
  message: string;
  timestamp: string;
  sequence?: number;
}

export interface RunSummary {
  run_id: string;
  status: RunStatus;
  source: string;
  title: string;
  current_stage: string;
  created_at: string;
  total_duration_ms?: number | null;
}

export interface RunDetail {
  run_id: string;
  status: RunStatus;
  source: RunSource;
  current_stage: string;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  stage_results: StageResult[];
  failure?: {
    stage: string;
    code: string;
    message: string;
  } | null;
  warnings: string[];
  total_duration_ms?: number | null;
  cancellation_requested: boolean;
}

export interface RunResult {
  run_id: string;
  status: RunStatus;
  source: { source_type: string; title: string };
  repository?: string | null;
  stages: any[];
  events: any[];
  failure?: any;
  warnings: string[];
  started_at?: string | null;
  finished_at?: string | null;
  duration_seconds?: number | null;
}

export interface Capabilities {
  supported_sources: string[];
  stages: string[];
  cancellation_mode: string;
  persistence_mode: string;
  repair_enabled: boolean;
  review_enabled: boolean;
  github_write_enabled: boolean;
  version: string;
}

export interface RecoveryResult {
  store_type: string;
  recovery_supported: boolean;
  recoverable_found?: number;
  marked_stale?: number;
  recoverable_ids?: string[];
  error?: string;
}

// ── Generic API helpers ───────────────────────────────────────

class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const url = `${BASE_URL}${path}`;
  const res = await fetch(url, {
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
    },
    ...options,
  });

  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new ApiError(
      `API ${res.status}: ${text.slice(0, 200)}`,
      res.status
    );
  }

  const data = await res.json();
  return data as T;
}

// ── Run API ───────────────────────────────────────────────────

export const runsApi = {
  /** Create and execute a new run */
  async create(params: {
    title: string;
    description?: string;
    repository?: string;
    source?: string;
    issue_number?: number;
    workspace_root?: string;
  }): Promise<{ success: boolean; data: RunResult }> {
    return request("/api/v1/runs", {
      method: "POST",
      body: JSON.stringify(params),
    });
  },

  /** List runs with optional filtering */
  async list(params?: {
    status?: string;
    limit?: number;
    offset?: number;
  }): Promise<{ success: boolean; data: RunSummary[]; count: number }> {
    const query = new URLSearchParams();
    if (params?.status) query.set("status", params.status);
    if (params?.limit) query.set("limit", String(params.limit));
    if (params?.offset) query.set("offset", String(params.offset));
    const qs = query.toString();
    return request(`/api/v1/runs${qs ? `?${qs}` : ""}`);
  },

  /** Get run details */
  async get(runId: string): Promise<{ success: boolean; data: RunDetail }> {
    return request(`/api/v1/runs/${runId}`);
  },

  /** Cancel a running run */
  async cancel(
    runId: string
  ): Promise<{ success: boolean; message: string }> {
    return request(`/api/v1/runs/${runId}/cancel`, { method: "POST" });
  },

  /** Resume a previous interrupted run */
  async resume(
    runId: string,
    workspace_root?: string
  ): Promise<{ success: boolean; data: RunResult }> {
    return request(`/api/v1/runs/${runId}/resume`, {
      method: "POST",
      body: JSON.stringify({ workspace_root }),
    });
  },

  /** Get events for a run */
  async events(
    runId: string
  ): Promise<{ success: boolean; data: RunEvent[]; count: number }> {
    return request(`/api/v1/runs/${runId}/events`);
  },
};

// ── Orchestration API ─────────────────────────────────────────

export const orchestrationApi = {
  /** Get orchestration capabilities */
  async capabilities(): Promise<{
    success: boolean;
    data: Capabilities;
  }> {
    return request("/api/v1/orchestration/capabilities");
  },

  /** Check for recoverable runs after restart */
  async recovery(): Promise<{
    success: boolean;
    data: RecoveryResult;
  }> {
    return request("/api/v1/orchestration/recovery", { method: "POST" });
  },
};
