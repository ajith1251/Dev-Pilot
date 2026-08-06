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
   nvidia · gemini · cloudflare · ollama_cloud · opencode_zen · openai · anthropic · openrouter · ollama · openai_compatible · fake
```

### 2.1 Provider registry

| Provider | Key/Config | Default model | Notes |
|----------|-----------|---------------|-------|
| `nvidia` | `NVIDIA_API_KEY` | `meta/llama-3.1-8b-instruct` | **OpenAI-compatible** NIM inference (hosted build / self-hosted). Per-request client timeout + transport retries via `DEVPILOT_NVIDIA_TIMEOUT_SECONDS` / `DEVPILOT_NVIDIA_MAX_RETRIES` (see §2.12) |
| `gemini` | `GEMINI_API_KEY` | `gemini-3.6-flash` | Free tier: multi-model daily-quota failover. Paid tier (`DEVPILOT_GEMINI_TIER=paid`): no daily-quota failover/markers, optional `DEVPILOT_GEMINI_PAID_MODELS` (see §2.9) |
| `cloudflare` | `CLOUDFLARE_API_KEY` | `@cf/meta/llama-4-scout-17b-16e-instruct` | **OpenAI-compatible** Workers AI. Base URL auto-built from `CLOUDFLARE_ACCOUNT_ID`, or `CLOUDFLARE_BASE_URL`. Default live-verified fastest Workers AI model (~0.5s TTF, 17B MoE); the 2024-era `@cf/meta/llama-3.1-8b-instruct` was deprecated 2026-05-30 (410). `DEVPILOT_CLOUDFLARE_TIMEOUT_SECONDS` / `_MAX_RETRIES` |
| `ollama_cloud` | `OLLAMA_CLOUD_API_KEY` | `gemma4:31b` | **OpenAI-compatible** hosted Ollama (`https://ollama.com/v1`) — GPU-only sizes without a local GPU. Default live-verified reliable even at small `max_tokens`; gpt-oss/nemotron models on this endpoint return empty content at `max_tokens<64` (see §2.13) |
| `opencode_zen` | `OPENCODE_ZEN_API_KEY` | `deepseek-v4-flash-free` | **OpenAI-compatible** gateway (`https://opencode.ai/zen/v1`). Free-tier model ids end `-free` (see §2.13) |
| `openai` | `OPENAI_API_KEY` | vendor default | |
| `anthropic` | `ANTHROPIC_API_KEY` | vendor default | |
| `openrouter` | `OPENROUTER_API_KEY` | `openrouter/auto` | OpenAI-compatible |
| `ollama` | `OLLAMA_BASE_URL` | `llama3.2` | **Keyless** — local/self-hosted |
| `openai_compatible` | `OPENAI_COMPATIBLE_BASE_URL` | `DEVPILOT_OPENAI_COMPATIBLE_MODEL` (or sentinel default) | **Generic OpenAI-compatible endpoint** — vLLM, TGI, llama.cpp, LM Studio, remote Ollama, OpenAI-compatible cloud gateways. Optional `OPENAI_COMPATIBLE_API_KEY`; `_TIMEOUT_SECONDS` / `_MAX_RETRIES` |
| `fake` | always available | `gpt-4o-mini` | Tests / no-key CI |

The registry is the **single registration point** for providers
(`app/llm/provider_registry.py`, Phase 20F). `LLMFactory` builds its provider
map, and `ProviderRouter` builds its availability checks + canonical order,
from the registry — adding a provider is one `ProviderSpec` entry there plus a
config block in `app/config.py` plus a `BaseLLMProvider` implementation; no
agent, factory or router routing code changes.

A provider is **configured** when its key (or base URL for Ollama / the
generic endpoint) is set — `fake` is always configured. A provider that is not
configured is skipped during routing, so a default `.env` with only
`NVIDIA_API_KEY` routes `nvidia → gemini → fake` and still works with **zero**
other keys.

### 2.2 Priority

Default order (deterministic): `[DEVPILOT_LLM_PROVIDER]` first, then the
canonical order `nvidia, gemini, cloudflare, ollama_cloud, opencode_zen, openai,
anthropic, openrouter, ollama, openai_compatible, fake`, deduplicated. With
`DEVPILOT_LLM_PROVIDER=nvidia` (the default) the chain is exactly
`nvidia, gemini, cloudflare, ollama_cloud, opencode_zen, openai, anthropic,
openrouter, ollama, openai_compatible, fake`. Override with `PROVIDER_PRIORITY`
(comma string or JSON list, e.g. `anthropic,ollama,fake`).

**Disabling without deleting keys** — `DEVPILOT_PROVIDER_DISABLED` excludes
providers from routing while keeping their credentials and health metadata: a
disabled provider reports `enabled=false` (and `disabled=true` in
`config_snapshot()`) and is never tried. From `.env`/process env list fields
are decoded as JSON **before** validators run, so use the JSON-array form
there (`DEVPILOT_PROVIDER_DISABLED=["anthropic","openai"]`); the comma string
only works when the value is passed programmatically (e.g.
`Settings(DEVPILOT_PROVIDER_DISABLED="anthropic,openai")`).

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

### 2.8 Streaming failover + mid-stream token-loss recovery (Phase 20B B3)

`chat_stream` failover happens **before the first token**: a provider that
errors without yielding any content is treated like a failed call (classified →
retried → failed over) and the full prompt is resent to the next provider.

A stream that fails **after** producing tokens is handled as *token loss* rather
than a plain restart. The caller has already delivered the partial output, so the
router resends the full prompt with the partial output injected as continuation
context:

```
<partial>{already-delivered text}</partial>
Continue the response from exactly where it left off; do NOT repeat any of the
partial text — produce only the remaining output.
```

The next provider in the chain therefore produces **only the remaining text** —
no duplicated tokens, no lost generation. Each mid-stream hand-off is recorded
as a `mid_stream_token_loss` failover (with `mid_stream: true`) and a resume
counter on the source provider; both are visible in `metrics_snapshot()` /
`provider_snapshots()`.

The hand-off is **bounded** by `DEVPILOT_PROVIDER_STREAM_RESUME_MAX` (default
`3`, range 0–20) per streaming call: once the budget is spent — or when the last
provider in the chain is the one that drops and no candidate remains — the
failure surfaces as `LLMError` (the caller keeps the tokens already received).
Setting the value to `0` disables mid-stream recovery entirely, restoring
surfaces-as-error behaviour.

### 2.9 Paid Gemini tier (Phase 20B B1)

Attaching billing to the **same** AI Studio `GEMINI_API_KEY` is now a first-class
config switch — no key rotation, no provider/auth change. `DEVPILOT_GEMINI_TIER`
selects the runtime behavior:

| | `free` (default) | `paid` |
|---|---|---|
| Cross-model daily-quota failover | ✅ automatic (`_CANDIDATE_MODELS`) | ❌ disabled (billing = no daily buckets) |
| 24h exhaustion markers | ✅ (`_exhausted_at`, TTL-pruned) | ❌ never written |
| Quota/billing error on a call | → mark exhausted + fail over | → **fail fast** with a clear "check your plan and billing" error |
| Transient per-minute 429 retry | ✅ exponential backoff | ✅ unchanged |
| Model pool | `gemini-3.6-flash` → flash-lite → 3.5-flash | `DEVPILOT_GEMINI_PAID_MODELS` (first = default) or the default model |

```bash
# backend/.env — same key, paid mode
DEVPILOT_LLM_PROVIDER=gemini
GEMINI_API_KEY=<same key, billing attached in AI Studio>
DEVPILOT_GEMINI_TIER=paid
DEVPILOT_GEMINI_PAID_MODELS=gemini-3.6-pro-preview,gemini-3.6-flash
```

Self-check: `POST /api/v1/providers/test` returns `gemini_tier` +
`gemini_models` when the active provider is Gemini; `GET /api/v1/providers/config`
exposes `data.gemini.{tier,paid_models}` (secret-safe).

### 2.10 Failure contract — never silent

When every provider fails, the router raises `AllProvidersFailedError` carrying
a `failures` list — one `ProviderCallFailedError` per provider attempt with its
`provider`, `kind` and message. Callers always know **which** provider failed
**why**. Router exceptions live in `app/core/exceptions.py` and are surfaced
(503) by the `/api/v1/providers/test` endpoint and the `provider-test` CLI.

### 2.11 Redaction

`app/llm/redaction.py` recursively redacts secrets from any snapshot/dict
before it leaves the backend: keys named like `api_key`, `token`, `secret`,
`password`, `credential`, `authorization` — and values that look like keys —
are replaced with masked suffixes (e.g. `sk-…a1b2`). The config surface and
API responses never contain raw credentials. This is applied at the router
boundary (`config_snapshot()`), so even a future endpoint that serializes the
full snapshot is safe. `NVIDIA_API_KEY` is covered by the same markers and
never appears in snapshots.

### 2.12 NVIDIA NIM (default provider)

NVIDIA NIM is the default provider: `backend/.env` ships
`DEVPILOT_LLM_PROVIDER=nvidia` with the priority chain
`nvidia, gemini, cloudflare, ollama_cloud, opencode_zen, openai, anthropic,
openrouter, ollama, openai_compatible, fake`. It is a thin
OpenAI-compatible wrapper (`app/llm/providers/nvidia.py`) — the same code path
used by OpenAI/OpenRouter/Cloudflare/Ollama Cloud/OpenCode Zen/the generic
endpoint — so every model a NIM endpoint serves works without provider-specific
code.

- Endpoint: `NVIDIA_BASE_URL` (default `https://integrate.api.nvidia.com/v1`,
  the hosted NIM build). Point it at a self-hosted NIM microservice for a
  private deployment.
- Model: `DEVPILOT_NVIDIA_MODEL` (default `meta/llama-3.1-8b-instruct` — the
  bake-off winner: fastest cold-start + best free-tier availability on the
  hosted   catalog, clean JSON + code; see the shortlist table in
  `workflow-status/NVIDIA_PROVIDER_COMPLETION_REPORT.md` §5), independent of
  `DEVPILOT_LLM_MODEL` so the provider keeps its own default
  across failover.
- Transport: `DEVPILOT_NVIDIA_TIMEOUT_SECONDS` (default `300`) and
  `DEVPILOT_NVIDIA_MAX_RETRIES` (default `2`) are passed straight to the
  OpenAI-compatible client (`AsyncOpenAI(..., timeout=..., max_retries=...)`).
  These are per-request, on top of router-level retry/failover.
- **Cold start:** the hosted NIM build lazily spins up an inference pod, so a
  cold first call can take **60–370s** for first token (observed on the 70B
  model). Small models (`meta/llama-3.1-8b-instruct`,
  `nvidia/nemotron-mini-4b-instruct`) keep pods warm and answer in <1s; the 49B
  is instant when warm. `.env` ships `DEVPILOT_PROVIDER_TIMEOUT_SECONDS=60` —
  the cold-start-optimized value: a request waits at most ~60s on a cold NIM
  pod, then the router retries and fails over to the sub-second backups
  (cloudflare llama-4-scout ~0.5s, ollama_cloud gemma4:31b ~0.75s) instead of
  waiting out the spin-up; NVIDIA returns automatically once its pod is warm.
  Keep `DEVPILOT_NVIDIA_TIMEOUT_SECONDS` ≥ the router timeout so the router's
  bounded retry/failover is the effective policy. Raise the router timeout only
  if you'd rather wait out a legit NVIDIA cold start than use a backup.
- A missing `NVIDIA_API_KEY` reports the provider as **not configured**; the
  router then fails over to the next configured provider (e.g. Gemini), so a
  keyless `.env` keeps working.
- Key: https://build.nvidia.com

### 2.13 Backup providers — Cloudflare Workers AI, Ollama Cloud, OpenCode Zen + generic OpenAI-compatible

Backup providers are thin OpenAI-compatible wrappers so they need **no
provider-specific agent or router code**:

- **`cloudflare`** (`app/llm/providers/cloudflare.py`) — Cloudflare Workers AI.
  Requires `CLOUDFLARE_API_KEY`; the base URL is built automatically from
  `CLOUDFLARE_ACCOUNT_ID`
  (`https://api.cloudflare.com/client/v4/accounts/{id}/ai/v1`) or overridden
  with `CLOUDFLARE_BASE_URL`. Model via `DEVPILOT_CLOUDFLARE_MODEL`. Default
  `@cf/meta/llama-4-scout-17b-16e-instruct` — live-verified **fastest** Workers
  AI model (~0.5s TTF, 17B MoE) and the best cold-start fit for a failover
  backup. The 2024-era `@cf/meta/llama-3.1-8b-instruct` was **deprecated
  2026-05-30** (returns HTTP 410); a slower 8B alternative is
  `@cf/meta/llama-3.1-8b-instruct-fp8`. Cloudflare's OpenAI-compat endpoint has
  **no `GET /models`** (405) — probe providers with a chat call, not a model
  list. Note: llama-4-scout's stream can emit **non-string (int)
  `delta.content`** chunks — `CloudflareProvider.chat_stream` skips non-string
  deltas (regression-tested).
- **`ollama_cloud`** (`app/llm/providers/ollama_cloud.py`) — hosted Ollama via
  `https://ollama.com/v1` (GPU-only model sizes without a local GPU). Requires
  `OLLAMA_CLOUD_API_KEY`; model via `DEVPILOT_OLLAMA_CLOUD_MODEL`. Default
  `gemma4:31b` was live-verified to return content even at `max_tokens=32`
  (3/3); `gpt-oss:120b`/`gpt-oss:20b` and `nemotron-3-nano:30b` on this
  endpoint can return **empty content** at `max_tokens<64` — the reason the
  default is not gpt-oss despite it being the stronger coder. A few catalog
  models (`deepseek-v4-flash:preview`, `glm-5.1`, `kimi-*`, `minimax-m2.7`) are
  subscription-only and 403 from a free key.
- **`opencode_zen`** (`app/llm/providers/opencode_zen.py`) — OpenAI-compatible
  gateway at `https://opencode.ai/zen/v1`. Requires `OPENCODE_ZEN_API_KEY`;
  model via `DEVPILOT_OPENCODE_ZEN_MODEL`. Free-tier model ids end in `-free`
  (`deepseek-v4-flash-free` default, `big-pickle`, `north-mini-code-free`,
  `laguna-s-2.1-free`, ...) — all live-verified on a free key.
- **`openai_compatible`** (`app/llm/providers/openai_compatible.py`) — any
  endpoint that speaks the OpenAI chat-completions API: self-hosted
  vLLM/TGI, llama.cpp/LM Studio, a remote Ollama server, or an
  OpenAI-compatible cloud gateway. Requires `OPENAI_COMPATIBLE_BASE_URL`;
  `OPENAI_COMPATIBLE_API_KEY` is optional (keyless local servers ignore it),
  and `DEVPILOT_OPENAI_COMPATIBLE_MODEL` names the model the endpoint serves.

All four honour per-provider timeouts/retries passed to the OpenAI-compatible
client (`DEVPILOT_CLOUDFLARE_TIMEOUT_SECONDS`/`_MAX_RETRIES`,
`DEVPILOT_OLLAMA_CLOUD_TIMEOUT_SECONDS`/`_MAX_RETRIES`,
`DEVPILOT_OPENCODE_ZEN_TIMEOUT_SECONDS`/`_MAX_RETRIES`,
`DEVPILOT_OPENAI_COMPATIBLE_TIMEOUT_SECONDS`/`_MAX_RETRIES`) on top of
router-level retry/failover. They surface automatically in the registry,
health/metrics/config snapshots, `GET /api/v1/providers*` and the CLI —
no other wiring needed.

---

## 3. Configuration

All settings live in `app/config.py`:

| Setting | Default | Meaning |
|---------|---------|---------|
| `PROVIDER_ROUTING_ENABLED` | `True` | Route via `RoutedProvider` when `get_provider(None)` is called |
| `PROVIDER_PRIORITY` | *(empty)* | Override priority (comma string or JSON list) |
| `PROVIDER_DISABLED` | *(empty)* | Providers excluded from routing; keeps their configured/health metadata. JSON array from `.env`/process env (list fields decode as JSON before validators), comma string programmatically |
| `PROVIDER_TIMEOUT_SECONDS` | `60` | Per-call timeout via `asyncio.wait_for` |
| `PROVIDER_RETRY_MAX` | `2` | Max retries per provider attempt |
| `PROVIDER_RETRY_BASE_BACKOFF_SECONDS` | `0.5` | Exponential backoff base |
| `PROVIDER_RETRY_MAX_BACKOFF_SECONDS` | `10` | Backoff cap |
| `PROVIDER_STREAM_RESUME_MAX` | `3` | Max mid-stream prefix-resends per streaming call (0 disables) |
| `PROVIDER_CIRCUIT_BREAKER_FAILURE_THRESHOLD` | `3` | Consecutive failures to open |
| `PROVIDER_CIRCUIT_BREAKER_COOLDOWN_SECONDS` | `30` | Open → half-open cooldown |
| `PROVIDER_CIRCUIT_BREAKER_HALF_OPEN_MAX_CALLS` | `2` | Half-open probe budget |
| `PROVIDER_HEALTH_WINDOW` | `100` | Rolling health window size |
| `PROVIDER_HEALTH_DEGRADED_SUCCESS_RATE` | `0.5` | Below this → degraded |
| `PROVIDER_HEALTH_UNHEALTHY_SUCCESS_RATE` | `0.3` | Below this → unhealthy |
| `PROVIDER_METRICS_PERSIST` | `True` | Best-effort PG metric snapshots |
| `GEMINI_TIER` | `free` | Gemini key tier: `free` (daily per-model quota + cross-model failover) or `paid` (billing attached — no daily-quota failover/markers, fail-fast on billing errors) |
| `GEMINI_PAID_MODELS` | *(empty)* | Paid-tier model list (comma string or JSON); first = default |
| `NVIDIA_BASE_URL` | `https://integrate.api.nvidia.com/v1` | NIM OpenAI-compatible inference endpoint (hosted build or self-hosted) |
| `NVIDIA_TIMEOUT_SECONDS` | `300` | Per-request client timeout for NIM (≥ router timeout so bounded router retry/failover is the effective policy; tolerates 60–370s cold starts) |
| `NVIDIA_MAX_RETRIES` | `2` | Transport-level retries per NIM request |
| `CLOUDFLARE_ACCOUNT_ID` | *(empty)* | Workers AI account id — builds the default base URL |
| `CLOUDFLARE_BASE_URL` | *(empty)* | Explicit Workers AI OpenAI-compatible endpoint override |
| `CLOUDFLARE_TIMEOUT_SECONDS` | `60` | Per-request client timeout for Cloudflare |
| `CLOUDFLARE_MAX_RETRIES` | `2` | Transport-level retries per Cloudflare request |
| `OLLAMA_CLOUD_BASE_URL` | `https://ollama.com/v1` | Hosted Ollama OpenAI-compatible endpoint |
| `OLLAMA_CLOUD_TIMEOUT_SECONDS` | `60` | Per-request client timeout for Ollama Cloud |
| `OLLAMA_CLOUD_MAX_RETRIES` | `2` | Transport-level retries per Ollama Cloud request |
| `OPENCODE_ZEN_BASE_URL` | `https://opencode.ai/zen/v1` | OpenCode Zen OpenAI-compatible gateway |
| `OPENCODE_ZEN_TIMEOUT_SECONDS` | `60` | Per-request client timeout for OpenCode Zen |
| `OPENCODE_ZEN_MAX_RETRIES` | `2` | Transport-level retries per OpenCode Zen request |
| `OPENAI_COMPATIBLE_BASE_URL` | *(empty)* | Generic OpenAI-compatible endpoint (required for `openai_compatible`) |
| `OPENAI_COMPATIBLE_TIMEOUT_SECONDS` | `60` | Per-request client timeout for the generic endpoint |
| `OPENAI_COMPATIBLE_MAX_RETRIES` | `2` | Transport-level retries per generic-endpoint request |

Plus the provider credentials: `NVIDIA_API_KEY`, `OPENROUTER_API_KEY`,
`OLLAMA_BASE_URL`, `CLOUDFLARE_API_KEY`, `OLLAMA_CLOUD_API_KEY`,
`OPENCODE_ZEN_API_KEY`, `OPENAI_COMPATIBLE_API_KEY`.

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
- **Mid-stream token-loss failover (resend prompt with full prefix)**
  — **✅ DONE (Phase 20B, Session 35):** streams that drop after delivering
  tokens resume on the next provider with the partial output as continuation
  context, bounded by `DEVPILOT_PROVIDER_STREAM_RESUME_MAX`; see §2.8.
- **Paid Gemini tier (billing on the existing key)**
  — **✅ DONE (Phase 20B B1, Session 37):** `DEVPILOT_GEMINI_TIER=paid` keeps
  the same `GEMINI_API_KEY` (billing attached in AI Studio) — the free-tier
  daily-quota failover and 24h exhaustion markers are disabled, a genuine
  billing/quota error fails fast, and transient 429s still retry. Optional
  `DEVPILOT_GEMINI_PAID_MODELS` pins the paid model pool. `POST
  /api/v1/providers/test` returns `gemini_tier`/`gemini_models`; see §2.9.
- Vertex AI (IAM, no-training terms) remains an optional enterprise path.
