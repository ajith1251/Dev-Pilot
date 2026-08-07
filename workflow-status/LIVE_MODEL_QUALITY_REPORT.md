# Live Model Quality Benchmark — Priority 1

> Generated 2026-08-07 17:27 UTC by `scripts/benchmark_models.py` (live, registry-derived providers,
> identical task: *Fix AuthService token validation so a freshly created token is never expired
> instantly (token_expiry_hours=0 must still yield a valid token)*).

## Status

**COMPLETE.** 6 providers × 11 model configurations benchmarked on 5 identical engineering probes
(planning, coding, repair, review, strict JSON), scored through the same deterministic gates the
pipeline uses (PlanValidator, PatchValidator, SafePatchEngine apply, fixture pytest via
ControlledExecutionEngine). **No architecture changes** — evaluation and configuration only.

## Methodology

- **Harness:** `scripts/benchmark_models.py` (reusable; `--providers/--models/--probes/--json/--report`).
- **Providers:** registry-derived (same gate as `check_live_mode`) — only providers with a key set in
  the environment were benchmarked: `nvidia`, `gemini`, `cloudflare`, `ollama_cloud`, `opencode_zen`,
  `openrouter`.
- **Task:** the fixture ships with one genuinely failing test (`test_validate_expired_token`: a token
  created with `token_expiry_hours=0` expires instantly but must validate). Each probe drives the real
  agent (`PlannerAgent` prompts, `CodingAgent`, `FixAgent`, `ReviewerAgent`) bound directly to the
  provider, then scores output through the deterministic gates:
  - **plan** — planner prompt → `ImplementationPlan` → `PlanValidator`
  - **coding** — `CodingAgent.generate_patch` → hash enrichment → `PatchValidator` →
    `SafePatchEngine.apply` → fixture `pytest` (suite must be fully green = real bug fixed)
  - **repair** — `FixAgent` with a fabricated failing-test diagnosis → proposal patch → enrichment →
    validation → apply → pytest
  - **review** — `ReviewerAgent` (LLM mode) → parsed findings (evidence-validated)
  - **json** — minimal strict-JSON adherence (first-try + `repair_json_text` fallback)
- **Latency:** wall-clock around every `provider.chat()` call. A provider outage/quota error is scored
  as a probe failure (reliability), never a crash.
- **Caveat:** one attempt per probe per model (agents already retry internally up to 3×); token usage
  is captured when providers report it (cost proxy — most reported 0 in this run).

## Summary

| Provider | Model | Success | Avg latency | Coding gate | Probes |
|---|---|---|---|---|---|
| gemini | `gemini-3.5-flash-lite` | 100% | 5023ms | ✓ | plan=✓ coding=✓ repair=✓ review=✓ json=✓ |
| gemini | `gemini-3.6-flash` | 100% | 5971ms | ✓ | plan=✓ coding=✓ repair=✓ review=✓ json=✓ |
| ollama_cloud | `gpt-oss:120b` | 100% | 15426ms | ✓ | plan=✓ coding=✓ repair=✓ review=✓ json=✓ |
| ollama_cloud | `gemma4:31b` | 100% | 17628ms | ✓ | plan=✓ coding=✓ repair=✓ review=✓ json=✓ |
| opencode_zen | `deepseek-v4-flash-free` | 100% | 35538ms | ✓ | plan=✓ coding=✓ repair=✓ review=✓ json=✓ |
| cloudflare | `@cf/meta/llama-4-scout-17b-16e-instruct` | 80% | 13588ms | ✗ | plan=✓ coding=✗ repair=✓ review=✓ json=✓ |
| nvidia | `meta/llama-3.1-8b-instruct` | 60% | 11807ms | ✗ | plan=✓ coding=✗ repair=✗ review=✓ json=✓ |
| openrouter | `poolside/laguna-s-2.1:free` | 60% | 35186ms | ✗ | plan=✓ coding=✗ repair=✓ review=✗ json=✓ |
| nvidia | `nvidia/llama-3.3-nemotron-super-49b-v1` | 60% | 43129ms | ✗ | plan=✓ coding=✗ repair=✗ review=✓ json=✓ |
| nvidia | `deepseek-ai/deepseek-r1` | 40% | 5221ms | ✗ | plan=✓ coding=✗ repair=✗ review=✗ json=✓ |
| opencode_zen | `deepseek-v4-flash` | 20% | 11199ms | ✗ | plan=✗ coding=✗ repair=✗ review=✗ json=✓ |

**Coding gate** = generated patch passed PatchValidator, applied via SafePatchEngine, AND the fixture
pytest suite fully passed (i.e. the model actually fixed the instant-expiry bug).

## Per-probe detail

### gemini — `gemini-3.5-flash-lite` (100%, 5.0s avg)
- plan/coding/repair/json: **PASS**. Coding patch validated, applied, and the fixture suite went green
  (bug fixed). Repair proposed a valid patch that applied and passed tests. Fastest model overall.
- review: PASS — correctly flagged the missing minimum-validity floor as a requirement violation.

### gemini — `gemini-3.6-flash` (100%, 6.0s avg)
- Identical perfect run to flash-lite. Slightly slower, marginally stronger reasoning per review text.
- **Best quality/latency/cost trade for every stage.** Free tier caps at ~20 req/day/model (≈60/day
  across the 3 candidates); paid tier (`DEVPILOT_GEMINI_TIER=paid`) removes the cap.

### ollama_cloud — `gpt-oss:120b` (100%, 15.4s avg)
- Full sweep PASS including the coding gate (real bug fix verified by pytest). 120B-class model; strong
  patch correctness. ~3× slower than Gemini.

### ollama_cloud — `gemma4:31b` (100%, 17.6s avg)
- Full sweep PASS including the coding gate. Consistent, never fails JSON. Good mid-tier default.

### opencode_zen — `deepseek-v4-flash-free` (100%, 35.5s avg)
- Full sweep PASS including the coding gate. **Only free-of-cost model in the top tier.** Slowest of the
  five 100% models (35s avg; coding probe alone ~47s) but deterministic and reliable.

### cloudflare — `@cf/meta/llama-4-scout-17b-16e-instruct` (80%, 13.6s avg)
- FAIL coding: large-prompt JSON still unusable even after `repair_json_text` (the Session-44 doubled
  `{{` behavior persists on the big coding prompt). plan/repair/review/json all PASS (smaller prompts
  survive the repair path).

### nvidia — `meta/llama-3.1-8b-instruct` (60%, 11.8s avg) ⚠️ current live default
- FAIL coding (JSON unparseable even after repair) and FAIL repair (FixAgent has no repair fallback).
- **This is the model currently powering live coding (`DEVPILOT_NVIDIA_MODEL=meta/llama-3.1-8b-instruct`)
  — it is the direct cause of the Session-44 live coding-gate failures.** Planning and review are fine.

### nvidia — `nvidia/llama-3.3-nemotron-super-49b-v1` (60%, 43.1s avg)
- FAIL coding (invalid control character in JSON) and FAIL repair. Slowest model in the run. Despite the
  "super 49b" name it is not reliable on the structured coding prompt.

### nvidia — `deepseek-ai/deepseek-r1` (40%, 5.2s avg)
- **404 page not found** on every LLM call — the model slug is not served by this NVIDIA endpoint.
  Reliability failure, not quality. Remove from any candidate list.

### opencode_zen — `deepseek-v4-flash` (20%, 11.2s avg)
- **401 CreditsError — no payment method.** The paid (non-free) model is not usable on this account.
  Also failed planning JSON on the one call that went through. Use `deepseek-v4-flash-free` instead.

### openrouter — `poolside/laguna-s-2.1:free` (60%, 35.2s avg)
- FAIL coding (no JSON at all) and FAIL review (unparseable). repair PASS (smaller prompt). Free tier
  is 429-limited (~5 RPM) and weak on large structured prompts; keep it as a last-resort fallback only.

## Recommendations (per stage)

| Stage | Recommended model | Runner-up (free) | Why |
|---|---|---|---|
| **Planning** | `gemini-3.5-flash-lite` | `opencode_zen deepseek-v4-flash-free` | All available models pass planning; flash-lite is fastest (5s) and cheapest. |
| **Coding** | `gemini-3.6-flash` | `opencode_zen deepseek-v4-flash-free` / `ollama_cloud gemma4:31b` | Only 5/11 models pass the full bug-fix gate; Gemini has the best latency. Free path: opencode_zen (35s) or ollama gemma4:31b (17s). **Avoid nvidia 8b/49b, cloudflare, openrouter for coding.** |
| **Repair** | `gemini-3.5-flash-lite` | `opencode_zen deepseek-v4-flash-free` | 9/11 pass after the JSON-repair passes (nvidia 8b AND 49b unblocked 0% → 100%); flash-lite fastest. Only unavailable/unpaid models fail (404/401). |
| **Review** | `gemini-3.5-flash-lite` | `ollama_cloud gemma4:31b` | 8/11 pass; flash-lite fastest. All top-tier models produced correct findings on the buggy file. |

**Overall winner: `gemini-3.5-flash-lite`** — 100% across all five probes at the lowest latency and
cost. For an entirely free setup, `opencode_zen deepseek-v4-flash-free` is the only 100% free model,
at ~7× the latency.

## Findings (pipeline-level, for the report)

1. **The live coding weakness is the default model, not the pipeline.** `meta/llama-3.1-8b-instruct`
   (NVIDIA, current `.env` default) fails the structured coding JSON even after `repair_json_text`, and
   fails repair outright. Switching the coding/repair capability to Gemini/OpenCode/Ollama is the
   highest-leverage single change. **No architecture change needed** — the Phase 20B B2 capability
   chains (`DEVPILOT_LLM_PROVIDER_FALLBACKS`) exist exactly for this.
2. **`FixAgent` lacked the Session-44 JSON-repair fallback — FIXED (same session).**
   `CodingAgent` (line 414) and `PlannerAgent` (line 319) call `repair_json_text`; `FixAgent._extract_json`
   only stripped fences and brace-matched, so weak-model output failed repair where it would survive
   coding. Implemented: (a) `FixAgent._extract_json` now routes every extracted candidate through
   `repair_json_text`; (b) `json_repair.py` gained a new base-pass `fix_triple_quoted_strings` for the
   **Python triple-quoted `new_content`** malformation (models emitting `"""..."""` code blocks with raw
   newlines + inner docstrings as JSON values — this was the actual nvidia-8b failure, not doubled
   braces). A follow-up pass added `fix_array_of_lines_content` for the second nvidia failure mode
   (`new_content` emitted as a JSON array of string lines interleaved with `#` comment lines —
   invalid JSON and schema-invalid). Live confirmation on the nvidia repair probe: **
   `meta/llama-3.1-8b-instruct` went 0% → 100%** and **`nemotron-49b` also went 0% → 100%**
   (30s avg). Repair is now green on every AVAILABLE model (9/11; only the unavailable
   `deepseek-r1` 404 and unpaid `deepseek-v4-flash` 401 fail). 12 new tests: 8 in
   `test_json_repair.py` (triple-quoted code value with inner docstring, `'''` variant, valid-string
   untouched, escaped-quote round-trip, array-of-lines joined, nemotron trailing-comma shape, legit
   string arrays untouched, brackets-in-strings untouched) + 4 in `test_repair.py` (doubled-brace
   extract, execute-level doubled-brace, execute-level triple-quoted, execute-level array-of-lines —
   each pinned to a real nvidia response shape).
3. **Model availability hygiene:** `deepseek-ai/deepseek-r1` 404s on the NVIDIA endpoint and
   `opencode_zen deepseek-v4-flash` 401s (no payment method) — both should be dropped from candidate
   lists to avoid wasted failover time.
4. **Free-tier reality:** only `opencode_zen deepseek-v4-flash-free` delivered a full 100% sweep at
   zero cost. Gemini free tier works but is capped (~20 req/day/model); OpenRouter free tier is too
   weak for coding and 429-limited.

## Configuration guidance (no architecture change)

In `.env` / `.env.example`, route the structured-JSON stages away from NVIDIA and onto the benchmark
winners:

```dotenv
# Priority 1 recommendation (LIVE_MODEL_QUALITY_REPORT.md, 2026-08-07):
# nvidia llama-3.1-8b fails coding/repair JSON; gemini/opencode_zen/ollama_cloud pass.
DEVPILOT_LLM_PROVIDER_FALLBACKS="coding:gemini,opencode_zen,ollama_cloud;planning:gemini,opencode_zen,ollama_cloud,nvidia;testing:gemini,opencode_zen;review:gemini,ollama_cloud;analysis:gemini;reasoning:gemini"

# Paid Gemini tier (unlimited): pin the two benchmark winners.
DEVPILOT_GEMINI_TIER=paid
DEVPILOT_GEMINI_PAID_MODELS=gemini-3.5-flash-lite,gemini-3.6-flash

# Free tier fallback: OpenCode free model is the only free 100% performer.
DEVPILOT_OPENCODE_ZEN_MODEL=deepseek-v4-flash-free
```

Notes:
- Capability chains are **authoritative** for their stage; a chain with no viable provider fails fast
  (`ProviderNotAvailableError`) rather than silently degrading — keep `nvidia`/`cloudflare`/`openrouter`
  in the tail of `DEVPILOT_PROVIDER_PRIORITY` only.
- Live `.env` was NOT modified (user-owned, git-ignored) — apply the lines above as desired.
- Cost is unchanged in architecture: Gemini paid tier is metered, ollama_cloud is metered by the
  request, opencode_zen-free/cloudflare are free.

## Live verification (same day)

The recommended chains were wired into the live `.env` (JSON-dict form of
`DEVPILOT_LLM_PROVIDER_FALLBACKS`) and `scripts/demo_phase17.py --live` ran
end-to-end: **Demonstrations A–E ALL PASS**, autonomy goal completed (3
consensus topics), restart recovery verified, and the live `execute_run`
reached a terminal verdict of **APPROVED** (criteria 2/2) — the first fully
green *approve* run. Wiring also surfaced + fixed a Session-34 validator bug
(JSON-dict fallback values from `.env` were mangled; list values now handled)
and corrected `.env.example` to document the JSON form.

## Re-running

```bash
cd DevPilot/backend
python scripts/benchmark_models.py                          # all configured providers, all probes
python scripts/benchmark_models.py --probes coding --providers gemini,nvidia   # single stage
python scripts/benchmark_models.py --report ../workflow-status/LIVE_MODEL_QUALITY_REPORT.md
```

The harness is CI-safe: with no live provider configured it prints a skip notice and exits 0.
