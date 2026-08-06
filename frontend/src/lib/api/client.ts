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
  acceptance_criteria?: string[];
  execution_budget?: Record<string, unknown>;
}

// ── Phase 20A6: repository-aware dashboard types ───────────────

/** Per-repository status card (derived server-side from run + org graph). */
export interface RepositoryStatus {
  repository_id: string;
  name: string;
  namespace: string;
  organization: string;
  path: string;
  source_type: string;
  is_primary: boolean;
  ordering: number;
  current_stage: string;
  progress: {
    planning: string;
    coding: string;
    testing: string;
    repair: string;
    review: string;
    quality_gate: string;
  };
  validation_status: string;
  application_status: string;
  changed_files: string[];
  validation_errors: string[];
  quality_gate: string;
  quality_gate_result: string;
  graph: {
    available: boolean;
    node_count?: number;
    edge_count?: number;
    run_count?: number;
    namespace?: {
      repository_id: string;
      organization_id: string;
      name: string;
      source_type: string;
    } | null;
    outgoing_links?: Array<{ repository_id: string; relationship: string }>;
    incoming_links?: Array<{ repository_id: string; relationship: string }>;
  };
}

/** Organization-level execution summary (run completion). */
export interface OrganizationSummary {
  repository_count: number;
  participating_repositories: Array<{
    repository_id: string;
    name: string;
    is_primary: boolean;
    status: string;
  }>;
  successful_repositories: string[];
  failed_repositories: string[];
  repaired_repositories: string[];
  duration_seconds?: number | null;
  engineering_decisions: {
    count: number;
    recent: string[];
  };
  consensus_summary: {
    count: number;
    contradictions: number;
    recent: string[];
  };
  quality_status: string;
  quality_gate?: {
    decision?: string | null;
    score?: number | null;
    requirements_satisfied: number;
    requirements_unsatisfied: number;
    verification_status: string;
  } | null;
  graph?: {
    repository_count: number;
    node_count: number;
    edge_count: number;
    cross_edge_count: number;
    version: number;
  } | null;
}

/** Registered organization repository namespace (org graph). */
export interface OrgRepository {
  repository_id: string;
  namespace_id: string;
  organization_id: string;
  name: string;
  path: string;
  source_type: string;
  created_at: string;
}

/** Per-repository EKG stats + dependency links. */
export interface OrgRepositoryStats {
  repository_id: string;
  namespace: {
    repository_id: string;
    organization_id: string;
    name: string;
    path: string;
    source_type: string;
  } | null;
  node_count: number;
  edge_count: number;
  run_count: number;
  node_types: Record<string, number>;
  outgoing_links: Array<{ repository_id: string; relationship: string }>;
  incoming_links: Array<{ repository_id: string; relationship: string }>;
}

/** Auxiliary repository spec for a Phase 20 multi-repo run (mirrors backend
 * `RepositorySpec` / org-graph `MultiRepoAcquisitionSpec`). */
export interface AuxiliaryRepositorySpec {
  repository_id: string;
  name?: string;
  source?: "local" | "github";
  owner?: string;
  repo?: string;
  path?: string;
  ref?: string;
  depth?: number;
  relationships?: Array<{
    target_repository_id: string;
    relationship: string;
    weight?: number;
  }>;
}

/** Per-repository validation/application summary (Phase 20A4/A5). */
export interface RepositoryPatchValidation {
  repository_id: string;
  repository_namespace: string;
  workspace_path?: string;
  patch_id?: string;
  validation_status: string;
  validation_errors: string[];
  application_status: string;
  application_errors: string[];
  rejected_paths: string[];
  deterministic_findings: string[];
  changes_applied: number;
  changes_attempted: number;
  changed_files: string[];
  status: string;
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
  repository_count?: number;
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
  auxiliary_repositories?: Array<Record<string, unknown>>;
  repo_validation?: RepositoryPatchValidation[];
  repositories?: RepositoryStatus[];
  organization_summary?: OrganizationSummary;
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
  auxiliary_repositories?: Array<Record<string, unknown>>;
  repo_validation?: RepositoryPatchValidation[];
  repositories?: RepositoryStatus[];
  organization_summary?: OrganizationSummary;
}

export interface RunListStats {
  total: number;
  pending: number;
  running: number;
  approved: number;
  rejected: number;
  needs_human_review: number;
  failed: number;
  cancelled: number;
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

// ── Durability report (Phase 19) ───────────────────────────────

/** Shape of the JSON emitted by scripts/durability_report.py. */
export interface DurabilityRunApi {
  run_id: string;
  run_status: string;
  handoffs: number;
  decisions: number;
  consensus_via_api: number;
  consensus_recovered: number;
  runs_in_table: number;
}

export interface DurabilityGoalApi {
  goal_id: string;
  goal_state: string;
  goal_runs: string[];
  goal_run_statuses: Record<string, string>;
  goal_latest_run_status: string;
  goal_handoffs: number;
  goal_decisions: number;
  goal_consensus: number;
  goal_recovered: string;
}

export interface DurabilityReport {
  mode: "live" | "skipped" | "error";
  reason?: string;
  error?: string;
  run_api?: DurabilityRunApi;
  goal_api?: DurabilityGoalApi;
  gates?: string[];
  passed?: boolean;
}

// ── Provider router (Phase 19B) ────────────────────────────────

export type ProviderStatus =
  | "healthy" | "degraded" | "unhealthy" | "unknown";

export interface CircuitSnapshot {
  state: "closed" | "open" | "half_open";
  consecutive_failures: number;
  failure_threshold: number;
  cooldown_seconds: number;
  half_open_max_calls: number;
}

export interface ProviderHealthSnapshot {
  provider: string;
  status: ProviderStatus;
  total_requests: number;
  successful_requests: number;
  failed_requests: number;
  success_rate: number | null;
  consecutive_failures: number;
  retries: number;
  failovers: number;
  avg_latency_ms: number | null;
  last_latency_ms: number | null;
  last_success_at: number | null;
  last_failure_at: number | null;
  uptime_seconds: number;
}

export interface ProviderEntryOverview {
  name: string;
  priority: number;
  configured: boolean;
  enabled: boolean;
  default_model: string | null;
  status: ProviderStatus;
  active: boolean;
  circuit: CircuitSnapshot;
  health: ProviderHealthSnapshot;
}

export interface ProviderOverviewData {
  routing_enabled: boolean;
  active_provider: string | null;
  priority: string[];
  providers: ProviderEntryOverview[];
}

export interface ProviderHealthData {
  routing_enabled: boolean;
  active_provider: string | null;
  providers: Array<{
    name: string;
    configured: boolean;
    enabled: boolean;
    status: ProviderStatus;
    circuit_state: string;
    default_model: string | null;
    health: ProviderHealthSnapshot;
  }>;
}

export interface FailoverEvent {
  timestamp: number;
  from: string;
  to: string;
  reason: string;
}

export interface ProviderTotals {
  total_requests: number;
  successful_requests: number;
  failed_requests: number;
  retries: number;
  failovers: number;
}

export interface ProviderMetricsData {
  totals: ProviderTotals;
  per_provider: Record<string, ProviderHealthSnapshot & { circuit_state: string }>;
  failover_events: FailoverEvent[];
  uptime_seconds: Record<string, number>;
  persisted?: Record<string, ProviderHealthSnapshot & { circuit_state: string }>;
}

export interface ProviderConfigData {
  routing_enabled: boolean;
  provider_priority: string[];
  timeout_seconds: number;
  retry: {
    max_retries: number;
    base_backoff_seconds: number;
    max_backoff_seconds: number;
  };
  circuit_breaker: {
    failure_threshold: number;
    cooldown_seconds: number;
    half_open_max_calls: number;
  };
  health: {
    window: number;
    degraded_success_rate: number;
    unhealthy_success_rate: number;
  };
  providers: Record<string, { configured: boolean; key: string }>;
}

export interface ProviderTestData {
  provider: string;
  content: string;
  finish_reason: string;
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

export async function request<T>(
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
    repositories?: AuxiliaryRepositorySpec[];
    acceptance_criteria?: string[];
    execution_budget?: Record<string, unknown>;
  }): Promise<{ success: boolean; data: RunResult }> {
    return request("/api/v1/runs", {
      method: "POST",
      body: JSON.stringify(params),
    });
  },

  /** List runs with optional filtering, sorting, and date range */
  async list(params?: {
    status?: string;
    limit?: number;
    offset?: number;
    sort_by?: string;
    created_after?: string;
    created_before?: string;
  }): Promise<{ success: boolean; data: RunSummary[]; count: number; total_count: number; stats?: RunListStats }> {
    const query = new URLSearchParams();
    if (params?.status) query.set("status", params.status);
    if (params?.limit) query.set("limit", String(params.limit));
    if (params?.offset) query.set("offset", String(params.offset));
    if (params?.sort_by) query.set("sort_by", params.sort_by);
    if (params?.created_after) query.set("created_after", params.created_after);
    if (params?.created_before) query.set("created_before", params.created_before);
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

// ── Durability API ────────────────────────────────────────────

export const durabilityApi = {
  /** Fetch the latest durability_report.py JSON (run_api/goal_api summary) */
  async report(): Promise<{
    success: boolean;
    data: DurabilityReport;
  }> {
    return request("/api/v1/durability/report");
  },
};

// ── Provider Router API ────────────────────────────────────────

export const providersApi = {
  /** Registered providers, priority order and the active provider */
  async overview(): Promise<{ success: boolean; data: ProviderOverviewData }> {
    return request("/api/v1/providers");
  },

  /** Per-provider health: status, circuit state, latency, success rate */
  async health(): Promise<{ success: boolean; data: ProviderHealthData }> {
    return request("/api/v1/providers/health");
  },

  /** Runtime metrics: totals, per-provider counters, failover events */
  async metrics(): Promise<{ success: boolean; data: ProviderMetricsData }> {
    return request("/api/v1/providers/metrics");
  },

  /** Persisted metric history for a single provider (newest first) */
  async history(
    provider: string,
    limit = 20
  ): Promise<{ success: boolean; data: ProviderHealthSnapshot[] }> {
    return request(
      `/api/v1/providers/metrics/history?provider=${encodeURIComponent(provider)}&limit=${limit}`
    );
  },

  /** Redacted routing configuration */
  async config(): Promise<{ success: boolean; data: ProviderConfigData }> {
    return request("/api/v1/providers/config");
  },

  /** Route one benign test call through the router */
  async test(message?: string): Promise<{ success: boolean; data: ProviderTestData }> {
    return request("/api/v1/providers/test", {
      method: "POST",
      body: JSON.stringify(message ? { message } : {}),
    });
  },
};

// ── Organization Graph API (Phase 19A / 20A6) ────────────────

export const orgApi = {
  /** List registered repository namespaces with search + pagination.
   *  Powers the Phase 20A6 repository selector. */
  async repositories(params?: {
    q?: string;
    organization_id?: string;
    limit?: number;
    offset?: number;
  }): Promise<{
    success: boolean;
    data: {
      repositories: OrgRepository[];
      count: number;
      total: number;
      limit: number;
      offset: number;
    };
  }> {
    const query = new URLSearchParams();
    if (params?.q) query.set("q", params.q);
    if (params?.organization_id) query.set("organization_id", params.organization_id);
    if (params?.limit != null) query.set("limit", String(params.limit));
    if (params?.offset != null) query.set("offset", String(params.offset));
    const qs = query.toString();
    return request(`/api/v1/graph/org/repositories${qs ? `?${qs}` : ""}`);
  },

  /** Per-repository EKG stats + dependency links (repo status cards). */
  async repository(
    repositoryId: string
  ): Promise<{ success: boolean; data: OrgRepositoryStats }> {
    return request(
      `/api/v1/graph/org/repositories/${encodeURIComponent(repositoryId)}`
    );
  },

  /** Organization-wide graph statistics. */
  async stats(): Promise<{ success: boolean; data: Record<string, unknown> }> {
    return request("/api/v1/graph/org/stats");
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
