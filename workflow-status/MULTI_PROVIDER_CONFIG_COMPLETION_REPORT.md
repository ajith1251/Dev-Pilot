# Multi-Provider Configuration & Backup Provider Integration — Completion Report (Session 43)

> **Status**: COMPLETE ✅ (code + deterministic tests + **live credential validation**)
> **Date**: August 6, 2026
> **Scope**: Keep NVIDIA NIM primary; add Cloudflare Workers AI, Ollama Cloud and
> OpenCode Zen as configured backup providers; centralize provider registration
> into one registry; add a `DEVPILOT_PROVIDER_DISABLED` kill-switch; update docs
> + this report. **No agent changes.**

---

## 1. What was done

| Requirement | Status |
|---|---|
| 1. NVIDIA stays the primary provider | ✅ `DEVPILOT_LLM_PROVIDER=nvidia`; canonical order starts `nvidia, gemini, cloudflare, ollama_cloud, opencode_zen, ...` |
| 2. Cloudflare Workers AI backup provider | ✅ `app/llm/providers/cloudflare.py` (`CloudflareProvider`) — OpenAI-compatible `chat`/`chat_stream`, env-driven config |
| 3. Ollama Cloud backup provider | ✅ `app/llm/providers/ollama_cloud.py` (`OllamaCloudProvider`) — hosted Ollama via `https://ollama.com/v1` |
| 4. OpenCode Zen backup provider | ✅ `app/llm/providers/opencode_zen.py` (`OpencodeZenProvider`) — OpenAI-compatible gateway, free-tier `-free` models |
| 5. Generic OpenAI-compatible backup provider | ✅ `app/llm/providers/openai_compatible.py` (`OpenAICompatibleProvider`) — any OpenAI chat-completions endpoint (vLLM/TGI/llama.cpp/LM Studio/remote Ollama) |
| 6. Centralized provider registration (one registry) | ✅ `app/llm/provider_registry.py` — `ProviderSpec` + `register_provider`/`provider_names`/`provider_classes`/`provider_availability`/`get_spec`; factory + router both derive from it |
| 7. `DEVPILOT_PROVIDER_DISABLED` kill-switch | ✅ config field + `validate_provider_disabled` validator; disabled providers keep credentials/health but are excluded from routing |
| 8. Deterministic unit tests | ✅ `test_provider_registry.py` (7) + provider tests for all four backups + `TestPhase20FProviders`/`TestProviderDisable` — all deterministic (no paid LLM) |
| 9. Docs | ✅ `docs/MULTI_PROVIDER_ROUTING.md`, `docs/ARCHITECTURE.md`, `README.md`, `workflow-status/PROJECT_STATE.md` (+ `ollama.py` docstring) |
| 10. Completion report | ✅ this file |
| 11. Do NOT modify agent architecture | ✅ zero agent changes |

## 2. Files changed

| File | Change |
|---|---|
| `backend/app/llm/provider_registry.py` | **NEW** — the single registration source (Phase 20F): `ProviderSpec` (name, class, availability attribute, always-present flag), `register_provider`, `provider_names`, `provider_classes`, `provider_availability`, `get_spec`; **11 built-ins** in canonical order `nvidia, gemini, cloudflare, ollama_cloud, opencode_zen, openai, anthropic, openrouter, ollama, openai_compatible, fake` |
| `backend/app/llm/providers/cloudflare.py` | **NEW** — `CloudflareProvider`: `CLOUDFLARE_API_KEY` required; base URL `https://api.cloudflare.com/client/v4/accounts/{id}/ai/v1` from `CLOUDFLARE_ACCOUNT_ID` or `CLOUDFLARE_BASE_URL`; default model **`@cf/meta/llama-4-scout-17b-16e-instruct`** (live-verified fastest Workers AI model, ~0.5s TTF, 17B MoE — picked to minimize failover cold start; the 2024-era `@cf/meta/llama-3.1-8b-instruct` was deprecated 2026-05-30 → HTTP 410, and the 8B `-fp8` replacement was 1.2–2.1s TTF); sentinel-aware `_resolve_model`; timeout/retries env-driven |
| `backend/app/llm/providers/ollama_cloud.py` | **NEW** — `OllamaCloudProvider`: `OLLAMA_CLOUD_API_KEY` required; base `https://ollama.com/v1`; default model **`gemma4:31b`** (live-verified reliable even at `max_tokens=32`; gpt-oss/nemotron models on this endpoint return empty content at `max_tokens<64`); timeout/retries env-driven |
| `backend/app/llm/providers/opencode_zen.py` | **NEW** — `OpencodeZenProvider`: `OPENCODE_ZEN_API_KEY` required; base `https://opencode.ai/zen/v1`; default model **`deepseek-v4-flash-free`** (free-tier ids end `-free`); timeout/retries env-driven |
| `backend/app/llm/providers/openai_compatible.py` | **NEW** — `OpenAICompatibleProvider`: `OPENAI_COMPATIBLE_BASE_URL` required; `OPENAI_COMPATIBLE_API_KEY` optional (placeholder `"openai-compatible"`); `DEVPILOT_OPENAI_COMPATIBLE_MODEL` falls back to `LLMConfig().model` |
| `backend/app/llm/factory.py` | `_providers = dict(provider_classes())`; `register_provider` forwards to the registry (always-available) so factory + router stay in sync |
| `backend/app/llm/router.py` | `_PROVIDER_AVAILABILITY = dict(provider_availability())`, `_CANONICAL_ORDER = provider_names()`; `_disabled_names()`; `_build_entries` marks `enabled=False` for disabled; `health_snapshot()` reports `enabled: false`; `config_snapshot()` gains a `"disabled"` bool; added missing test imports |
| `backend/app/config.py` | Cloudflare block (`CLOUDFLARE_API_KEY`/`CLOUDFLARE_ACCOUNT_ID`/`CLOUDFLARE_BASE_URL`/`CLOUDFLARE_MODEL`/`CLOUDFLARE_TIMEOUT_SECONDS`/`CLOUDFLARE_MAX_RETRIES`), Ollama Cloud block (`OLLAMA_CLOUD_API_KEY`/`_BASE_URL`/`OLLAMA_CLOUD_MODEL`/`_TIMEOUT_SECONDS`/`_MAX_RETRIES`), OpenCode Zen block (`OPENCODE_ZEN_API_KEY`/`_BASE_URL`/`OPENCODE_ZEN_MODEL`/`_TIMEOUT_SECONDS`/`_MAX_RETRIES`), OpenAI-compatible block (`OPENAI_COMPATIBLE_BASE_URL`/`_API_KEY`/`_MODEL`/`_TIMEOUT_SECONDS`/`_MAX_RETRIES`), `PROVIDER_DISABLED` + `validate_provider_disabled` validator |
| `backend/.env.example` | all four backup blocks, `DEVPILOT_PROVIDER_DISABLED` JSON-array examples, updated canonical-order comment |
| `backend/.env` (git-ignored) | **NEW keys live**: `CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_API_KEY`, `OLLAMA_CLOUD_API_KEY`, `OPENCODE_ZEN_API_KEY`; priority chain `["nvidia","gemini","cloudflare","ollama_cloud","opencode_zen","openai","anthropic","openrouter","ollama","fake"]` |
| `backend/tests/test_provider_registry.py` | **NEW** — 7 tests: spec registration, canonical order (11 built-ins), availability map, unknown-provider error, registry↔factory sync, cleanup of registered entries |
| `backend/tests/test_llm_providers.py` | `cloudflare` + `ollama_cloud` + `opencode_zen` + `openai_compatible` provider classes (key-required, defaults, sentinel `_resolve_model`, base URL, config parse, chat payload) + factory registration assertions; `register_provider("dummy")` now unregisters from the shared registry |
| `backend/tests/test_provider_router.py` | `TestPhase20FProviders`, `TestProviderDisable`, canonical-order slice update (`[:6] == ["nvidia","gemini","cloudflare","ollama_cloud","opencode_zen","openai"]`), `test_ollama_cloud_and_opencode_zen_registered`, key-required via `config_snapshot()` |
| `README.md` | canonical chain now **11 providers** (incl. `ollama_cloud` + `opencode_zen`); first-class providers list + Phase 20F paragraph |
| `docs/ARCHITECTURE.md` | router diagram chain (11 providers), centralized-registry bullet, new-providers + disabled bullet |
| `docs/MULTI_PROVIDER_ROUTING.md` | §2.1 registry table (11 providers) + registry-is-single-source, §2.2 disabled + new canonical order + JSON-from-env note, §2.13 "Backup providers — Cloudflare Workers AI, Ollama Cloud, OpenCode Zen + generic OpenAI-compatible", §3 config tables + `PROVIDER_DISABLED` row |
| `workflow-status/PROJECT_STATE.md` | header + Session 43 log entry |
| `backend/app/llm/providers/ollama.py` | docstring "how a future provider is added" → step 3 points at `provider_registry.py` |

## 3. How to add a provider, change priority, or disable a provider

**Add a provider** (3 steps, no factory/router changes):
1. Implement `BaseLLMProvider` in `backend/app/llm/providers/<name>.py`.
2. Add a config block in `backend/app/config.py` (key/base_url/model/timeout/retries).
3. Register one `ProviderSpec` in `backend/app/llm/provider_registry.py`.

The factory's `_providers`, the router's availability map, and the canonical
(default) priority order all derive from the registry automatically.

**Change priority**: set `DEVPILOT_LLM_PROVIDER` (moves the default to the
front of the chain) and/or `DEVPILOT_PROVIDER_PRIORITY` (explicit JSON array).
With `DEVPILOT_LLM_PROVIDER=nvidia` the chain is exactly the canonical order
above.

**Disable a provider**: set `DEVPILOT_PROVIDER_DISABLED`. From `.env`/process
env it must be a **JSON array** (`["cloudflare","openai"]`) because
pydantic-settings decodes list fields as JSON before validators run; the comma
string (`"cloudflare,openai"`) only works when passed programmatically.
Disabled providers keep their credentials and health state but are skipped
during routing (`enabled: false` in health, `disabled: true` in config
snapshots); disabling every provider raises `ProviderNotAvailableError`.

## 4. Test baseline

| Suite | Result |
|---|---|
| `backend/tests/test_provider_registry.py` | **7 passed** |
| `backend/tests/test_llm_providers.py` + `test_provider_router.py` | **167 passed** (all four backup classes, Phase 20F providers, provider-disable, canonical-order slice `[:6]`) |
| Full deterministic suite (`-m "not live and not integration"`) | **1786 passed / 17 skipped / 2 failed / 54 deselected** (17 new tests vs the 1769 baseline) |

The 2 full-suite failures are **pre-existing and unrelated** to this change:
1. `test_wrapper_skips_cleanly_without_provider` — documented env quirk (the
   `.env` provider key makes the wrapper subprocess run live).
2. `test_namespace_and_edge_roundtrip` — accumulated PG org-repository limit
   (PG holds org repos; org limit is 64).

## 5. Credential reality & live validation

- Keys for all three new backups are now set in the git-ignored `backend/.env`:
  Cloudflare (`CLOUDFLARE_API_KEY` + `CLOUDFLARE_ACCOUNT_ID`), Ollama Cloud
  (`OLLAMA_CLOUD_API_KEY`), OpenCode Zen (`OPENCODE_ZEN_API_KEY`).
- **Live-validated end-to-end through the real router** (`python -m app.cli
  provider-test --json`):
  - `DEVPILOT_PROVIDER_DISABLED=["nvidia","gemini","openrouter"]` →
    succeeds via **`cloudflare`**.
  - Disabling cloudflare too → succeeds via **`opencode_zen`**.
  - Disabling that → routes to **`ollama_cloud`** (returned empty content at the
    CLI's `max_tokens=32` until the default switch to `gemma4:31b`, after which
    it returns `provider-ok`).
- Model facts that drove the defaults:
  - **TTFT + throughput + reliability benchmark (streamed, 3 trials each):**

    | Provider / model | TTFT (mt=32) | TTFT (mt=256) | Throughput | Strict-JSON 3/3 |
    |---|---|---|---|---|
    | nvidia `meta/llama-3.1-8b-instruct` (warm pod) | 0.41–0.99s | 0.34–0.54s | 52–85 ch/s | ✅ (clean) |
    | **cloudflare `llama-4-scout-17b`** (default) | **0.52–0.60s** | **0.53–0.67s** | **71 ch/s** | fences JSON (stripped by `json_repair`) |
    | cloudflare `llama-3.1-8b-fp8` | 0.45–0.74s | 0.45–0.85s | 41 ch/s | fences JSON |
    | ollama_cloud `gemma4:31b` | 0.68–0.97s | 0.71–0.89s | 60 ch/s | ✅ (clean) |
    | opencode_zen `deepseek-v4-flash-free` | 1.9–6.1s | 1.7–6.2s | 15 ch/s | ✅ (clean) |

  - **latency verdict:** cloudflare llama-4-scout is the fastest cold-start-optimized
    backup (~0.53s TTF, consistent, highest throughput); nvidia is equally fast
    only when its pod is warm (cold start is 60–370s — the real bottleneck);
    ollama_cloud gemma4:31b is the best middle ground (0.7–0.9s, clean JSON);
    opencode_zen is the slowest (2–6s TTF, ~15 ch/s) but produces clean JSON —
    keep it as the last configured backup.
  - **llama-4-scout stream quirk + fix:** its stream can emit **non-string
    (int) `delta.content`** chunks (observed `2`, `4` on a 36-chunk response).
    `CloudflareProvider.chat_stream` now skips non-string deltas so only real
    text tokens reach the caller (regression test:
    `test_chat_stream_skips_non_string_deltas`).
  - Cloudflare `@cf/meta/llama-3.1-8b-instruct` **deprecated 2026-05-30 (410)**;
    the 8B `-fp8` replacement was 1.2–2.1s TTF, so the default is the faster
    **`@cf/meta/llama-4-scout-17b-16e-instruct`** (~0.5s TTF, 17B MoE) — the
    best cold-start fit for a failover backup. Cloudflare's OpenAI-compat
    endpoint has no `GET /models` (405).
  - Ollama Cloud `gemma4:31b` returns content 3/3 at `max_tokens=32` (~0.8s TTF);
    `gpt-oss:120b` empty 2/3, `gpt-oss:20b` + `nemotron-3-nano:30b` empty 3/3 at
    32; subscription-only models (`deepseek-v4-flash:preview`, `glm-5.1`,
    `kimi-*`, `minimax-m2.7`) → 403.
  - OpenCode Zen 61 models; free tier (`-free` ids) verified: `deepseek-v4-flash-free`
    (default), `big-pickle`, `north-mini-code-free`.
- `python -m app.cli providers` reports configured=True for nvidia, gemini,
  cloudflare, ollama_cloud, opencode_zen, openrouter, fake.
- NVIDIA live facts: cold start 60–370s; `.env` now ships the
  cold-start-optimized `DEVPILOT_PROVIDER_TIMEOUT_SECONDS=60` so a request
  fails over fast to the sub-second backups instead of waiting out the spin-up;
  default model `meta/llama-3.1-8b-instruct`; warm pod 2–8s; `nvidia → gemini`
  failover observed live; circuit breaker verified.
- Secrets rule: no API keys in source, docs, tests, README, logs, or this
  report — only in git-ignored env files.
- Working tree is **not committed**; user to review first.

## 6. Next steps (Session 44)

1. Commit the Session-40 A6 + Session-41 + Session-42 + Session-43 trees after
   user review.
2. Enterprise roadmap workstream E1 (self-hosted inference fabric).
