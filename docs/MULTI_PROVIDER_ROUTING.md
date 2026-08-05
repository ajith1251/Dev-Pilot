# Multi-Provider Failover & Reliability Platform (Phase 19B)

> **Status**: COMPLETE ✅
> **Phase**: 19B
> **Components**: `app/llm/router.py`, `app/llm/redaction.py`, new providers
> (`openrouter`, `ollama`), `app/services/provider_metrics_store.py`,
> migration `014`, `/api/v1/providers` API, CLI commands, dashboard page.

---

## 1. Motivation

DevPilot previously resolved exactly **one** provider at a time. If that
provider's free-tier quota expired (Gemini's ~24h TTL), hit a rate limit, or
suffered an outage, every LLM-powered stage in the pipeline
(planning → coding → review → repair) had **no alternative path**. Failover
only swapped *models within a single vendor*, never between vendors.

Phase 19B introduces a **health-aware router** that makes provider resilience
a first-class, deterministic platform concern:

- A single LLM call can now try `gemini → openai → anthropic → openrouter →
  ollama` in order, gated by circuit breakers and retry budgets.
- **Quota exhaustion is detected and handled explicitly** — permanent quota
  errors fail over immediately instead of burning retry backoff time.
- Every failure, retry, failover, latency and health sample is observable via
  API, CLI and a dashboard page, and optionally persisted to PostgreSQL.

**Agents are untouched.** The router sits *behind* `llm_factory.get_provider()`
and is exposed through a `RoutedProvider` facade, so every existing agent and
service that already calls `llm_factory.get_provider()` gains failover for free.

---

## 2. Architecture

```text
Agents / services (planner, coding_agent, reviewer, fix_agent, ...)
      │
      ▼
llm_factory.get_provider() ──► RoutedProvider (facade, same BaseLLMProvider API)
      │                              ▲
      │   PROVIDER_ROUTING_ENABLED=1 │
      ▼                              │
 ProviderRouter ◄────────────────────┘
      │  • priority chain (deterministic, configurable)
      │  • per-provider CircuitBreaker  closed → open → half_open
      │  • bounded RetryStrategy        exponential backoff, recoverable-only
      │  • FailureKind classification   quota vs rate-limit vs timeout vs ...
      │  • streaming failover           pre-first-token + mid-stream
      │  • ProviderHealth               rolling window, latency EMA, uptime
      │  • MetricsRegistry              totals + failover event ring buffer
      │  • AllProvidersFailedError      never silent, per-provider failures
      ▼
 BaseLLMProvider implementations
   openai · anthropic · gemini · openrouter · ollama · fake
```

### 2.1 Provider registry

| Provider | Key/Config | Default model | Notes |
|----------|-----------|---------------|-------|
| `gemini` | `GEMINI_API_KEY` | `gemini-3.6-flash` | Free tier; multi-model daily-quota failover |
| `openai` | `OPENAI_API_KEY` | vendor default | |
| `anthropic` | `ANTHROPIC_API_KEY` | vendor default | |
| `openrouter` | `OPENROUTER_API_KEY` | `openrouter/auto` | OpenAI-compatible |
| `ollama` | `OLLAMA_BASE_URL` | `llama3.2` | **Keyless** — local/self-hosted |
| `fake` | always available | `gpt-4o-mini` | Tests / no-key CI |

A provider is **configured** when its key (or base URL for Ollama) is set —
`fake` is always configured. A provider that is not configured is skipped
during routing, so a default `.env` with only `GEMINI_API_KEY` routes
`gemini → fake` and still works with **zero** other keys.

### 2.2 Priority

Default order (deterministic): `[DEVPILOT_LLM_PROVIDER]` first, then the
canonical order `gemini, openai, anthropic, openrouter, ollama, fake`,
deduplicated. Override with `PROVIDER_PRIORITY` (comma string or JSON list,
e.g. `anthropic,ollama,fake`).

### 2.3 Failure classification (`FailureKind`)

`classify_failure()` inspects the raised exception and returns a kind:

| Kind | Examples | Router behaviour |
|------|----------|------------------|
| `QUOTA` | "quota exceeded", "rate limit exceeded" (permanent), "insufficient_quota", billing errors | **Fail over immediately** (no retry) |
| `RATE_LIMIT` | "429", "rate limit", "too many requests" | Retry with backoff, then fail over |
| `TIMEOUT` | timeout, "read timeout" | Retry with backoff, then fail over |
| `NETWORK` | connection error, DNS failure | Retry with backoff, then fail over |
| `SERVER` | 5xx, internal error | Retry with backoff, then fail over |
| `UNKNOWN` | anything unexpected | Retry with backoff, then fail over |
| `PERMANENT` | invalid model, auth, 4xx client | **No retry, no failover** — surface immediately |

Quota markers are matched **before** generic rate-limit markers so a permanent
quota error is never mistaken for a transient 429.

### 2.4 Retry strategy

- `PROVIDER_RETRY_MAX` (default `2`) retries per provider attempt, only for
  recoverable kinds (`RATE_LIMIT`, `TIMEOUT`, `NETWORK`, `SERVER`, `UNKNOWN`).
- Backoff is exponential and bounded:
  `base_backoff_seconds * 2^(attempt-1)` capped at `max_backoff_seconds`.
- After the retry budget is exhausted the router **fails over** to the next
  provider in priority order.

### 2.5 Circuit breaker

Per provider, fully deterministic:

```text
CLOSED ──(consecutive_failures >= threshold)──► OPEN
OPEN ──(cooldown elapsed)──► HALF_OPEN
HALF_OPEN ──(probe succeeds)──► CLOSED
HALF_OPEN ──(probe fails)──► OPEN (fresh cooldown)
```

- While `OPEN`, the provider is skipped entirely.
- `HALF_OPEN` admits a bounded number of probe calls
  (`PROVIDER_CIRCUIT_BREAKER_HALF_OPEN_MAX_CALLS`, default `2`); the first
  probe success re-closes the circuit and recovery is automatic.
- Defaults: threshold `3` consecutive failures, cooldown `30s`.

### 2.6 Health windows

`ProviderHealth` keeps a single bounded rolling window of request outcomes
(`PROVIDER_HEALTH_WINDOW`, default `100`). Stale results age out so a provider
that recovered stops looking unhealthy.

| Status | Condition |
|--------|-----------|
| `healthy` | success_rate ≥ `degraded_threshold` (0.5) |
| `degraded` | 0.3 ≤ success_rate < 0.5 |
| `unhealthy` | success_rate < 0.3 **or** circuit `OPEN` |
| `unknown` | no results yet |

Also tracked: latency exponential moving average, last success/failure time,
uptime, per-provider retries and failover counters.

### 2.7 Typed per-capability fallbacks (Phase 20B)

`PROVIDER_PRIORITY` is one global chain for every call. Phase 20B adds
**per-capability typed chains** so a stage like *coding* (long generation,
big context) can fall back along a different provider list than *analysis*
(short, cheap classifications):

```dotenv
DEVPILOT_LLM_PROVIDER_FALLBACKS=coding:gemini,openai;planning:anthropic,gemini
```

- **Format**: `capability:prov1,prov2;capability2:prov3` (also accepts `=`
  separators, a JSON dict, or mixed case — all normalised to lowercase).
- **Capabilities**: `analysis`, `planning`, `coding`, `testing`, `review`,
  `reasoning`, `general`. Each agent stage labels its calls: repo/issue
  analyzers → `analysis`, planner → `planning`, coding + fix agents → `coding`,
  test agent → `testing`, reviewer → `review`.
- **Semantics**: a configured capability chain is **authoritative** for that
  kind of call — failover only walks providers in the chain, never the global
  list. Unlabelled calls (CLI chat, API probes) and capabilities without an
  override keep the global `PROVIDER_PRIORITY`. If no provider in the typed
  chain is configured/circuit-healthy the router raises
  `ProviderNotAvailableError` — it does not silently leak into the global list.
- **Capability transport**: `LLMConfig.capability` (a plain field, ignored by
  providers). The router reads it from `config.capability` or the explicit
  `chat(..., capability=...)` kwarg.
- **Observability**: providers referenced only in a capability chain are still
  registered (health, circuit breakers, metrics) even when excluded from the
  global priority. `GET /api/v1/providers/config` and the CLI expose
  `provider_fallbacks` alongside `provider_priority`.

### 2.8 Streaming failover

`chat_stream` failover happens **before the first token**. If a provider
errors before yielding any content it is treated like a failed call (classified
→ retried → failed over). A stream that has already produced tokens is **not**
abandoned mid-flight — swapping providers mid-stream would corrupt output, so
the error is surfaced to the caller instead.

### 2.9 Failure contract — never silent

When every provider fails, the router raises `AllProvidersFailedError` carrying
a `failures` list — one `ProviderCallFailedError` per provider attempt with its
`provider`, `kind` and message. Callers always know **which** provider failed
**why**. Router exceptions live in `app/core/exceptions.py` and are surfaced
(503) by the `/api/v1/providers/test` endpoint and the `provider-test` CLI.

### 2.10 Redaction

`app/llm/redaction.py` recursively redacts secrets from any snapshot/dict
before it leaves the backend: keys named like `api_key`, `token`, `secret`,
`password`, `credential`, `authorization` — and values that look like keys —
are replaced with masked suffixes (e.g. `sk-…a1b2`). The config surface and
API responses never contain raw credentials. This is applied at the router
boundary (`config_snapshot()`), so even a future endpoint that serializes the
full snapshot is safe.

---

## 3. Configuration

All settings live in `app/config.py`:

| Setting | Default | Meaning |
|---------|---------|---------|
| `PROVIDER_ROUTING_ENABLED` | `True` | Route via `RoutedProvider` when `get_provider(None)` is called |
| `PROVIDER_PRIORITY` | *(empty)* | Override priority (comma string or JSON list) |
| `PROVIDER_TIMEOUT_SECONDS` | `60` | Per-call timeout via `asyncio.wait_for` |
| `PROVIDER_RETRY_MAX` | `2` | Max retries per provider attempt |
| `PROVIDER_RETRY_BASE_BACKOFF_SECONDS` | `0.5` | Exponential backoff base |
| `PROVIDER_RETRY_MAX_BACKOFF_SECONDS` | `10` | Backoff cap |
| `PROVIDER_CIRCUIT_BREAKER_FAILURE_THRESHOLD` | `3` | Consecutive failures to open |
| `PROVIDER_CIRCUIT_BREAKER_COOLDOWN_SECONDS` | `30` | Open → half-open cooldown |
| `PROVIDER_CIRCUIT_BREAKER_HALF_OPEN_MAX_CALLS` | `2` | Half-open probe budget |
| `PROVIDER_HEALTH_WINDOW` | `100` | Rolling health window size |
| `PROVIDER_HEALTH_DEGRADED_SUCCESS_RATE` | `0.5` | Below this → degraded |
| `PROVIDER_HEALTH_UNHEALTHY_SUCCESS_RATE` | `0.3` | Below this → unhealthy |
| `PROVIDER_METRICS_PERSIST` | `True` | Best-effort PG metric snapshots |

Plus the new provider credentials: `OPENROUTER_API_KEY`, `OLLAMA_BASE_URL`.

---

## 4. Metrics persistence (migration `014`)

`app/services/provider_metrics_store.py` persists a compact per-provider
snapshot after each routed call:

```text
provider_metric_snapshots
├── id                 (PK)
├── provider           (composite index)
├── status             healthy / degraded / unhealthy / unknown
├── circuit_state      closed / open / half_open
├── total_requests
├── successful_requests
├── failed_requests
├── retries
├── failovers
├── success_rate
├── avg_latency_ms
├── uptime_seconds
├── recorded_at        (composite index, newest-first queries)
```

Persistence is **best-effort**: with no `DATABASE_URL` (or
`PROVIDER_METRICS_PERSIST=False`) every store call is a clean no-op —
`record_snapshot()` returns `False`, `latest()/history()` return
`None/[]`. The router never depends on persistence for correctness.

---

## 5. API (`/api/v1/providers`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/providers` | Registered providers, priority, active provider |
| GET | `/api/v1/providers/health` | Per-provider status, circuit, latency, success rate |
| GET | `/api/v1/providers/metrics` | Totals, per-provider counters, failover events (+ persisted snapshot when PG available) |
| GET | `/api/v1/providers/metrics/history?provider=&limit=` | Persisted per-provider history, newest first |
| GET | `/api/v1/providers/config` | Redacted routing configuration (masked key suffixes) |
| POST | `/api/v1/providers/test` | Route one benign call through the router (503 on total failure) |

All responses are wrapped `{success, data}` and are secret-safe.

---

## 6. CLI

| Command | Output |
|---------|--------|
| `python -m app.cli providers` | Registered providers, priority, active |
| `python -m app.cli provider-health` | Per-provider health + circuit state |
| `python -m app.cli provider-metrics` | Totals, per-provider counters, failover events |
| `python -m app.cli provider-test` | Route one benign call through the router |

All commands accept `--json` for machine-readable output.

---

## 7. Dashboard

`/dashboard/providers` (Next.js 14 page backed by `src/lib/api/client.ts`
`providersApi`) shows:

- active provider + routing status + totals (requests / success rate / failovers)
- a "Run test call" button that routes one benign call through the router
- per-provider cards: status badge, circuit state, success rate, latency,
  retries/failovers, uptime, configured flag, priority
- a failover-event table (time / from / to / reason)
- the persisted snapshot recovered from PostgreSQL when available
- the redacted routing configuration

---

## 8. Deterministic testing (no paid LLM)

`tests/test_provider_router.py` (43 tests) is fully deterministic: the router
accepts injectable `factory`, `settings`, `sleep` and `now_fn`, so tests use a
fake factory whose providers raise scripted failures and an inline `sleep`
that never blocks. Coverage includes:

- failure classification (quota before rate-limit, permanent, unknown…)
- retry budgets + backoff sequence (`[0.5, 1.0]`)
- circuit breaker: open on threshold, cooldown, half-open budget, probe
  fail re-trip, automatic recovery
- health window single-deque correctness + status thresholds
- routing/failover: quota-immediate failover, retry-then-failover, circuit
  skip + recovery, priority order
- streaming failover pre-first-token + mid-stream behaviour
- snapshots: redaction, priority, uptime, metrics totals
- `RoutedProvider` facade, factory wiring, provider registration
- `ProviderMetricsStore` no-DB no-op contract
- TestClient API endpoints (`/api/v1/providers`, `/health`, `/metrics`,
  `/config`, `/metrics/history`, `/test`)

`tests/test_migration.py` covers migration `014` (columns, indexes,
round-trip insert/select) and the `_drop_all_tables` teardown.

**Baseline:** `tests/test_provider_router.py` + `tests/test_migration.py` +
`tests/test_run_store_contract.py` = **110 passed**. Full live-PG suite =
**1573 passed / 18 skipped**; the only failures are the pre-existing
live-Gemini durability tests that require a fresh free-tier quota key.

---

## 9. Future directions

- **Typed per-capability fallback lists** (`DEVPILOT_LLM_PROVIDER_FALLBACKS`)
  — **✅ DONE (Phase 20B, Session 34):** each agent stage routes through its
  own provider chain via `LLMConfig.capability`; see §2.7.
- Billing/Vertex AI path for production-grade Gemini reliability.
- Mid-stream token-loss failover (resend prompt with full prefix) for long
  generations.
