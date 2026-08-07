# Production Reliability & Operational Hardening (Phase 20B)

> Phase 20B hardens DevPilot for long-running enterprise deployments — reliability,
> observability, resilience, and operational stability on top of the existing
> pipeline. No architectural redesign: every capability extends the provider
> router, the health API, the dashboard, or the startup path.

## Operational Architecture

```
                         ┌────────────────────────────────────────────┐
                         │            app/main.py lifespan             │
                         │                                            │
   Startup               │  validate_settings()  (fail-fast optional)  │
      │                  │  recovery of stale runs (PostgresRunStore) │
      ▼                  │                                            │
  ┌───────────────┐      │  ┌──────────────────────────┐               │
  │ ProviderHealth│◄─────┼──│ ProviderHealthProbe loop │  (probe every  │
  │ Probe (bg)    │      │  └──────────────────────────┘   interval s)  │
  └───────────────┘      │                                            │
  ┌───────────────┐      │  ┌──────────────────────────────┐           │
  │ ProviderMetric│◄─────┼──│ ProviderMetricsPersistence   │  (snapshot │
  │ Persistence   │      │  └──────────────────────────────┘   to PG)   │
  └───────────────┘      │                                            │
                         └──────────────┬───────────────────────────────┘
                                        │
                        ┌───────────────▼────────────────┐
                        │         ProviderRouter          │
                        │  health-based selection         │
                        │  adaptive timeouts              │
                        │  post-failure cooldown          │
                        │  circuit breaker (probe priority)│
                        │  recovery detection + warm-up   │
                        │  mid-stream token-loss resume   │
                        └───────────────┬────────────────┘
                                        │
                        ┌───────────────▼────────────────┐
                        │      SystemMetricsService       │
                        │  runs · repos · autonomy ·      │
                        │  providers · resources          │
                        └───────────────┬────────────────┘
                                        │
            ┌───────────────────────────┼───────────────────────────┐
            ▼                           ▼                           ▼
   /health/live                /health/ready             /api/v1/operations/*
   /api/v1/providers/*         subsystem matrix          status · metrics ·
                                                            startup-validation
            └───────────────────────────┬───────────────────────────┘
                                        ▼
                            ┌──────────────────────────┐
                            │  /dashboard/operations   │  (live, no mock data)
                            └──────────────────────────┘
```

### Layering

1. **LLM calls** — every agent call flows through `ProviderRouter`
   (`app/llm/router.py`): health-based selection, adaptive per-call timeouts,
   bounded retry, post-failure cooldown, circuit breakers, and failover.
2. **Background loops** — the app lifespan starts `ProviderHealthProbe`
   (passive health probing of configured providers) and
   `ProviderMetricsPersistence` (bounded snapshots of router metrics into
   PostgreSQL). Both stop cleanly on shutdown.
3. **Observability surface** — `SystemMetricsService` (bounded in-process
   counters) + `subsystem_status.py` (readiness matrix) feed the health
   endpoints and the Operations Dashboard.
4. **Startup** — `validate_settings()` runs deterministic configuration
   checks; `DEVPILOT_STARTUP_VALIDATION_STRICT=true` fails fast.
5. **Request path** — a correlation-ID middleware tags every request and a
   request-size limit rejects oversized bodies (413).

## Reliability Strategy

### Provider reliability (extends Phase 19B/20B router)

| Capability | Mechanism | Config |
|---|---|---|
| Automatic health probing | Passive probes (`probe_provider`/`probe_all`) run in the background; probe outcomes never enter the traffic success-rate window | `DEVPILOT_PROVIDER_HEALTH_PROBE_ENABLED`, `_INTERVAL_SECONDS`, `_TIMEOUT_SECONDS` |
| Recovery detection | A success after a failure spell (failed calls, failed probe, open/half-open circuit) records a **recovery** and marks the provider **warming** | `DEVPILOT_PROVIDER_WARM_UP_SECONDS` |
| Post-failure cooldown | After a failed attempt a provider is skipped entirely for a configurable window | `DEVPILOT_PROVIDER_COOLDOWN_AFTER_FAILURE_SECONDS` |
| Smarter selection | Health-based ranking: recovering probe > healthy > warming > unknown > degraded > unhealthy; priority is the stable tie-breaker | `DEVPILOT_PROVIDER_HEALTH_BASED_SELECTION` |
| Minimum sample guard | A provider needs ≥ `DEVPILOT_PROVIDER_HEALTH_MIN_SAMPLES` real-traffic samples before its success rate may rank it degraded/unhealthy — a cold-start single failure never starves it | `DEVPILOT_PROVIDER_HEALTH_MIN_SAMPLES` (default 5) |
| Adaptive timeout | Per-call budget scales from observed latency: `max(base, avg_latency × multiplier)` capped at the max | `DEVPILOT_PROVIDER_ADAPTIVE_TIMEOUT_*` |
| Circuit probe priority | An OPEN circuit past cooldown is ranked first so it gets its half-open recovery probe instead of being starved by health-based selection | — |
| Mid-stream token-loss resume | A stream cut off after tokens resumes on the next provider with the partial output as continuation context | `DEVPILOT_PROVIDER_STREAM_RESUME_MAX` |

Selection rank order (lower is tried first):

```
-1  recovering — OPEN past cooldown, or HALF_OPEN (probe budget active)
 0  healthy
 1  warming (recently recovered)
 2  unknown (no traffic, or fewer than PROVIDER_HEALTH_MIN_SAMPLES samples)
 3  degraded (success rate < degraded threshold)
 4  unhealthy (success rate < unhealthy threshold, or OPEN within cooldown)
```

### Operational resilience

- **Transient network failures** — classified by `classify_failure`
  (network/timeout/server/rate-limit are retryable with exponential backoff;
  quota/permanent 4xx fail over immediately).
- **API rate limits** — 429s retry with backoff; permanent daily/billing caps
  are detected and fail over without burning backoff time.
- **Provider cold starts** — `DEVPILOT_PROVIDER_TIMEOUT_SECONDS=60` bounds a
  cold NVIDIA NIM pod (60–370s first call) so the router fails over to the
  sub-second backups and NVIDIA re-enters rotation once warm.
- **Temporary outages** — circuit breakers skip a failing provider entirely;
  post-failure cooldown keeps it out of rotation until it recovers; the probe
  loop detects the recovery.
- **PostgreSQL reconnects** — every database check reconnects through the
  async engine (pre-ping); a transient outage degrades readiness (`/health/ready`
  → 503) and recovers automatically without a restart.
- **WebSocket reconnects** — the frontend graph socket reconnects with
  backoff; `ws_manager` tracks per-channel connection counts and closes all
  connections on shutdown.

## Resource Management

| Resource | Audit result | Guard |
|---|---|---|
| Async tasks | Background loops are single tasks, stopped in shutdown; `SystemMetricsService.open_task_count()` observes any growth | lifespan `finally` block stops loops |
| DB sessions | Engine disposed on shutdown; metrics persistence opens/closes its own scoped sessions per snapshot | `async with session.begin()` |
| WebSockets | Connections tracked per channel; `close_all()` on shutdown | `ws_manager.active_connections` |
| Workspaces | `WorkspaceService.cleanup_workspace` removes the isolated copy after use; demo C verifies zero leftovers | `create_workspace` + `cleanup_workspace` |
| Temporary files | Workspaces live under a base dir; cleanup is exercised in demo C | `base_dir` parameter |
| Memory | Bounded deques everywhere (`PROVIDER_HEALTH_WINDOW`, `OPERATIONS_METRICS_HISTORY`, failover ring buffer) | `maxlen` on every deque |
| Connection pooling | SQLAlchemy pool (pool_size=5, max_overflow=10, pre-ping) | `app/db/database.py` |

Demo C (`demo_phase20b.py`) drives 12 bounded iterations of router calls +
workspace create/cleanup and asserts: zero workspace leftovers, open-task
count back to baseline, no WS growth.

## Observability

### Structured logging & correlation IDs

- `app/core/context.py` — `correlation_id` contextvar (per-request).
- `app/core/middleware.py` — `CorrelationMiddleware` generates/forwards a
  correlation ID header (`X-Correlation-ID`) and tags logs with it;
  `RequestSizeLimitMiddleware` rejects bodies over
  `DEVPILOT_MAX_REQUEST_BODY_BYTES` with 413.
- `app/core/logging.py` — a filter injects the correlation ID into every log
  record (`%(correlation_id)s`), so a single run's log lines can be followed
  across stages.

### Metrics (`SystemMetricsService`)

- **Runs** — active, started/completed totals, throughput per minute (60s
  window), average + recent durations.
- **Repositories** — processed total, average + recent processing seconds.
- **Autonomy** — active goals, total, average duration, recent terminal states.
- **Providers** — active provider, per-provider avg latency, router totals
  (requests, retries, failovers, recoveries, probes).
- **Resources** — process RSS (MiB), active WS connections, open asyncio tasks.

All windows are bounded. `snapshot()` is thread-locked and safe from any
coroutine. Orchestration hooks record run start/complete and repository
processing; the autonomy service records goal start/complete.

### API

| Endpoint | Purpose |
|---|---|
| `GET /health/live` | Liveness — always 200 while the process serves |
| `GET /health/ready` | Readiness — 200/503 from the subsystem matrix |
| `GET /api/v1/operations/status` | Full subsystem matrix + readiness summary |
| `GET /api/v1/operations/metrics` | Runtime operational metrics |
| `GET /api/v1/operations/startup-validation` | Startup config findings (fresh validation) |
| `WS /api/v1/ws/system` | Broadcast channel for system snapshots |

### CLI

```
python -m app.cli operations-status     # subsystem matrix + readiness
python -m app.cli operations-metrics    # runtime metrics
python -m app.cli validate-config       # startup config findings (exit 1 on errors)
```

## Performance Optimizations

- **Provider routing** — health ranks are O(n) with stable sort; adaptive
  timeouts avoid wasted waits; cooldown/circuit gating avoids retrying
  providers that just failed.
- **No duplicate work** — `ProviderEntry` shares one `ProviderHealth` with the
  `MetricsRegistry`, so snapshots aggregate the same objects instead of
  re-computing.
- **Bounded histories** — every rolling window is a fixed-size deque; the
  metrics snapshot never grows with uptime.
- **Probes stay passive** — probe outcomes never enter the success-rate
  window, so probing does not distort real-traffic health or selection.

## Configuration Validation

`app/core/startup_validation.py` runs deterministic, network-free checks at
startup and on demand:

- `DEVPILOT_LLM_PROVIDER` is a registered provider
- `DEVPILOT_PROVIDER_PRIORITY` / `DEVPILOT_LLM_PROVIDER_FALLBACKS` /
  `DEVPILOT_PROVIDER_DISABLED` reference only registered providers
- fallback capability keys are valid `Capability` values
- `DATABASE_URL` uses a PostgreSQL scheme when set
- `GEMINI_TIER=paid` implies a key is set
- health thresholds are coherent (degraded rate > unhealthy rate)
- disabled providers are not also primary in the priority chain
- routing is enabled with at least one configured provider

Severity `error` findings fail startup when
`DEVPILOT_STARTUP_VALIDATION_STRICT=true`; otherwise they are logged and
exposed via the operations API/CLI. `python -m app.cli validate-config`
exits non-zero when any error finding exists.

## Security Hardening

- **Secret redaction** — all provider snapshots route through `redact_dict` /
  `redact_secret`; the operations API surfaces never include key material
  (demo D asserts key names don't appear in the status blob).
- **Correlation IDs** — request tracing without logging sensitive payloads.
- **Request limits** — oversized bodies rejected (413) via
  `DEVPILOT_MAX_REQUEST_BODY_BYTES`.
- **Error messages** — subsystem detail strings are truncated (≤ 200 chars)
  and never echo credentials.
- **Readiness is conservative** — a subsystem in `unknown` (e.g. graph when
  the EKG store is absent) never fails readiness; only `error` states do.
  Note the two `providers` edge cases: an `unknown` provider status (no
  traffic yet) counts as healthy, so the providers subsystem is effectively
  `ok` whenever any provider exists; but routing enabled with **zero
  configured providers** is an `error`, making `/health/ready` return 503 in
  keyless deployments — intentional (a deployment that cannot make any LLM
  call is not ready to serve), and visible via `error_subsystems`.

## Health Endpoints

### `/health/live`

Always `200` while the process serves. Load balancers use it to keep the
instance in rotation.

### `/health/ready`

Reads the same subsystem matrix as `GET /api/v1/operations/status`:

| Subsystem | What it reports |
|---|---|
| `providers` | Router health, configured/healthy counts, per-provider status + circuit |
| `database` | PostgreSQL connectivity (redacted; `unknown` when unconfigured) |
| `graph` | EKG availability + version + node/edge counts |
| `repository_memory` | Memory service availability |
| `inference` | Routing enabled + active provider |
| `orchestration` | Active runs, completed total, throughput |
| `websocket` | Active connections per channel |
| `resources` | Process RSS + open tasks |

Returns `200` when no required subsystem is in `error`, `503` otherwise with
the failing subsystems listed.

## Dashboard

`/dashboard/operations` renders live system state from real APIs only (no
mock data):

- Readiness banner (`/health/ready` semantics)
- Key stats: active runs, throughput, avg run duration, failovers/retries/
  recoveries, active WS connections, memory
- Run queue summary (from the runs API `stats`)
- Subsystem status cards (the readiness matrix)
- Provider status table (status, circuit, probes, recoveries, latency)
- Autonomous execution + repository processing panels
- Startup configuration findings panel

## Testing

| File | Covers |
|---|---|
| `tests/test_phase20b_provider_reliability.py` | health probing, recovery detection, health-based selection (incl. min-sample guard + probe priority), adaptive timeouts, post-failure cooldown, probe loop lifecycle |
| `tests/test_phase20b_operations.py` | operations/health endpoints, readiness agreement, secret redaction, metrics sections |
| `tests/test_startup_validation.py` | startup validation findings + strict mode |
| `frontend/src/lib/api/client.test.ts` | operations API client contract |

All deterministic — no paid LLM calls (provider outages are simulated with
stub providers).

## Required Demonstrations

`python scripts/demo_phase20b.py` (in-memory, deterministic; `--pg` for live
PostgreSQL; `--json` for machine-readable output):

| Demo | Verifies |
|---|---|
| A | Provider outage → circuit opens → probe observes recovery → provider warms up → back in rotation |
| B | Database reconnect after a transient interruption (readiness degrades, then recovers) |
| C | Long-running autonomous execution without resource leaks |
| D | Operational dashboard reflects live system state (no secrets in responses) |
| E | Health endpoints report accurate subsystem status (live/ready agree with ops) |
| F | Graceful shutdown + restart recovery (loops stop, WS closed, idempotent reboot) |

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `/health/ready` returns 503 | One subsystem is `error` — read `error_subsystems`; e.g. `database` needs `DATABASE_URL` reachable |
| All LLM calls raise `AllProvidersFailedError` | No configured provider or all circuits open — check `python -m app.cli providers --json` and `validate-config` |
| A provider never gets traffic again | It is `unhealthy` with ≥ min-samples of failures while healthier options exist — the probe loop will detect recovery (check `probes`/`recoveries` on `/api/v1/providers/health`) |
| Failover feels slow | Lower `DEVPILOT_PROVIDER_TIMEOUT_SECONDS` or raise `DEVPILOT_PROVIDER_ADAPTIVE_TIMEOUT_MULTIPLIER`; check the active provider + avg latency |
| Metrics show `memory_mb: null` | `psutil` not installed and no `resource` fallback on this platform — install `psutil` for memory telemetry |
| Startup logs show validation findings | `DEVPILOT_STARTUP_VALIDATION_STRICT=true` makes error findings fatal; fix config or leave non-strict for diagnostics |

## Deployment Recommendations

- **Run with PostgreSQL** — `DATABASE_URL` configured; the metrics
  persistence loop snapshots router health to PG so dashboards survive
  restarts.
- **Set `DEVPILOT_PROVIDER_HEALTH_PROBE_INTERVAL_SECONDS`** to a small value
  (e.g. 30–60s) when recovery latency matters; the default 120s is fine for
  steady-state.
- **Probe endpoints** — wire `/health/live` to the orchestrator/load balancer
  health check and `/health/ready` to readiness gates.
- **Correlation IDs** — ship `X-Correlation-ID` from the gateway; the
  backend forwards it.
- **Keep keys out of the image** — the ops API and logs are redacted, but
  `.env`/repo secrets must never be baked into containers (`.env` is
  git-ignored).
- **Bounded concurrency** — connection pooling and bounded metric windows mean
  the platform can run indefinitely; use the Operations Dashboard to observe
  drift before it becomes an incident.

## Key files

| File | Role |
|---|---|
| `app/llm/router.py` | Selection, cooldown, probes, recovery, adaptive timeout, snapshots |
| `app/services/provider_probe.py` | Background probe loop |
| `app/services/provider_metrics_persistence.py` | Background metrics snapshot persistence |
| `app/services/system_metrics.py` | Runtime operational metrics store |
| `app/services/subsystem_status.py` | Readiness matrix |
| `app/services/ws_manager.py` | WebSocket lifecycle + channels |
| `app/services/workspace_service.py` | Workspace create/cleanup |
| `app/core/startup_validation.py` | Configuration validation |
| `app/core/context.py` / `middleware.py` / `logging.py` | Correlation IDs, request limits, structured logging |
| `app/api/health.py` | `/health`, `/health/live`, `/health/ready` |
| `app/api/v1/operations.py` | Operations API |
| `app/cli_operations.py` | `operations-status`, `operations-metrics`, `validate-config` |
| `frontend/src/app/dashboard/operations/page.tsx` | Operations Dashboard |
| `scripts/demo_phase20b.py` | Demonstrations A–F |
