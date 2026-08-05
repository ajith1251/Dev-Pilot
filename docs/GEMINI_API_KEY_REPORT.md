# Gemini API Key — Workflow Review & Full Report

*DevPilot backend · August 2026*

## 1. Executive Summary

DevPilot (a multi-agent autonomous software-engineering platform) runs its live demonstrations on **Google Gemini via a free-tier AI Studio API key**. The key unlocks the full LLM pipeline — planning, coding, testing, review, repair, and autonomous iteration — through a provider abstraction that lets the platform swap `openai` / `anthropic` / `gemini` / `fake` with one environment variable.

The integration is **working end-to-end**: the live Phase 17 demo now produces **real consensus records** (3 records, 5 contradictions in Demonstration A) from actual LLM-driven runs against a real fixture repository, with **5 patches generated and applied**. The full test suite is green at **1426 passed / 18 skipped / 0 failed**.

Production-grade hardening was added along the way: model-sentinel handling, rate-limit retry with backoff, multi-model daily-quota failover, real retrieval wiring, idempotent state transitions, lenient JSON extraction, patch-hash enrichment, and workspace structure in the coding prompt.

---

## 2. The API Key Workflow (end-to-end)

### 2.1 Where the key lives and how it travels

```
┌─────────────────────────────────────────────────────────────────────┐
│  Google AI Studio (aistudio.google.com/apikey)                      │
│  └─ Free API key (no credit card) → copied to DevPilot/backend/.env │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
  DevPilot/backend/.env          DEVPILOT_LLM_PROVIDER=gemini
                                 GEMINI_API_KEY=<key>   (53 chars)
                              │
                              ▼
  app/config.py                 Settings.GEMINI_API_KEY (pydantic-settings,
                                env_file=".env", extra="ignore")
                              │
                              ▼
  app/llm/providers/gemini.py   GeminiProvider.__init__()
                                • raises LLMConfigurationError if key missing
                                • creates genai.Client(api_key=…)
                              │
                              ▼
  app/llm/factory.py            LLMFactory._providers["gemini"] — lazy singleton
                              │
                              ▼
  Agents                        planner: factory.get_provider() at call time
                                coding: falls back to factory when not injected
                              │
                              ▼
  scripts/demo_phase15/16/17.py --live guard:
                                provider ∈ {openai, anthropic, gemini} AND
                                matching *_API_KEY present, else refuses to run
```

### 2.2 Files that define the workflow

| File | Role |
|---|---|
| `app/config.py` | `GEMINI_API_KEY: Optional[str]` field (alias, never hardcoded) |
| `app/llm/providers/gemini.py` | The provider: SDK client, model resolution, retry/failover, tier handling |
| `app/llm/factory.py` | Registry: `{"openai", "anthropic", "gemini", "fake"}` |
| `app/llm/base.py` | `BaseLLMProvider`, `LLMConfig`, `LLMMessage`, `LLMResponse` contracts |
| `.env.example` | Documents how to obtain the free key |
| `requirements.txt` | `google-genai>=1.5.0,<2.0.0` |
| `scripts/demo_phase15/16/17.py` | `--live` guards using a provider→key map |

### 2.3 What each API call does (usage description)

Every agent interaction is a `generate_content` request that DevPilot translates:

1. **System messages** are extracted and sent as `system_instruction` (Gemini's native mechanism) — not as a fake user turn.
2. **User/assistant turns** are mapped to Gemini's `{role, parts:[{text}]}` format.
3. **Generation config** maps DevPilot's `temperature` / `max_tokens` / `top_p` / `stop_sequences`.
4. **Model selection** (`_resolve_model`) deliberately ignores the OpenAI-biased `LLMConfig()` default (`gpt-4o-mini`) and uses `gemini-3.6-flash` — this fixed a latent bug where Gemini would have received an OpenAI model name and 404'd.
5. **Usage metadata** (`prompt_token_count`, `candidates_token_count`) is captured into `LLMResponse`.

Typical call volume: **~24-30 calls per full demo** (4 runs × ~5-7 LLM stages) — planning, retrieval, coding, review, repair, and autonomy iterations.

---

## 3. Why Gemini Was Chosen

| Factor | Detail |
|---|---|
| **Key availability** | A free Gemini key was available (vs. paid OpenAI/Anthropic) |
| **Zero cost** | Entire demo runs on the free tier — $0 budget required |
| **Official async SDK** | `google-genai` supports `client.aio.models` cleanly |
| **Verified live** | `gemini-3.5-flash-lite` answered real calls reliably |
| **Provider abstraction** | The factory design meant adding Gemini was ~1 new file + registry entry — no agent code changes |

---

## 4. Advantages of This API Key Setup

1. **Instant onboarding** — free key from Google AI Studio in ~1 minute, no credit card, no Cloud project.
2. **Provider portability** — flip `DEVPILOT_LLM_PROVIDER` to switch the whole platform between providers; the `fake` provider keeps CI/tests green with no key at all.
3. **Large context** — the 1M-token window comfortably holds whole repositories, ideal for repo-scale planning.
4. **Free tier is genuinely usable** — per-model daily buckets (observed `limit: 20/day` on flash), and each model has its *own* bucket, which the failover exploits.
5. **Resilience engineering** (all added & tested):
   - Per-minute 429s → exponential backoff retry (honors the API's retry-delay hint)
   - Per-day quota exhaustion → fail-fast in ~0.7s with an actionable message (was 16 min of wasted backoff)
   - **Multi-model failover** — exhausted model automatically switches to the next with fresh quota (`_CANDIDATE_MODELS`)
   - Explicit non-candidate models are never silently swapped
6. **Security hygiene** — key read only from `.env` via pydantic-settings; never logged, echoed, or committed (`.env` is gitignored; `.env.example` ships with placeholder).

---

## 5. Gemini Strengths (documented + observed)

| Strength | Evidence |
|---|---|
| **Huge context window** | 1,048,576 tokens input — an entire codebase fits in one prompt (vs GPT-4o-mini's 128K) |
| **Large output budget** | Up to ~64K output tokens — big multi-file patches in a single turn |
| **Fast + cheap paid tier** | ~$1.50/1M input, ~$7.50–9/1M output; batch/flex tiers at 50% off |
| **Genuine free tier** | Real quota (20/day/model observed; ~5-15 RPM per-minute) — we ran real planning/coding against it |
| **Quality of planning output** | The planner produced valid 2-3 step implementation plans on live calls |
| **Produces real patches** | With real retrieval + workspace structure, the coding agent generated 5 valid patches in the final demo run |
| **Stable fallback** | `gemini-3.5-flash-lite` answered consistently across many runs |

---

## 6. Gemini Weaknesses (what actually bit us)

| # | Weakness | Observed reality | Fix |
|---|---|---|---|
| 1 | **Tight per-minute rate limit** | `limit: 5/min` on free flash tier — 429s mid-burst | Retry-with-backoff ✅ |
| 2 | **Per-model daily cap** | `limit: 20/day` — a 28-call demo can't finish on one model in a day | Multi-model failover ✅ |
| 3 | **Model retirement** | `gemini-2.5-flash` → 404 "no longer available to new users" | Default moved to `3.6-flash` / `flash-lite` ✅ |
| 4 | **Free-tier daily exhaustion** | `3.5-flash` / `3.6-flash` hit "exceeded your current quota" mid-run | Fail-fast + failover ✅ |
| 5 | **Braces inside JSON strings** | `_extract_json`'s brace-depth counter miscounted `{`/`}` inside `new_content` → "Failed to parse LLM output as JSON" | First-`{`/last-`}` extraction ✅ |
| 6 | **Concatenated JSON objects** | Gemini sometimes returned two objects (note + payload) → "Expecting ',' delimiter" | Parse fallback across `{`-spans ✅ |
| 7 | **Conservative INSUFFICIENT_CONTEXT** | With zero-item retrieved context (and no file layout), the coding agent refused to hallucinate file contents | Real retrieval + workspace structure in prompt ✅ |
| 8 | **Cannot know file hashes** | LLM patches lack `original_hash` for MODIFY → validation rejected them | Orchestrator computes hashes from the workspace ✅ |
| 9 | **Enum/format leniency** | Emits `logic_bug` for `RiskCategory` (not in enum) | Added `LOGIC_BUG` + coerce unknown → `OTHER` ✅ |
| 10 | **Streaming + retry incompatibility** | Async-generator streams can't be safely retried mid-stream | Documented; agents use `chat()` path |
| 11 | **Free-tier data use** | Google may use free-key prompts for product improvement | Paid tier / Vertex AI avoids this |

### 6.1 Honest residual variance (not bugs)

- The coding LLM **sometimes** returns a valid-but-empty `changes: []` (or `INSUFFICIENT_CONTEXT`) — roughly 1 in 4-5 calls on the free tier. The run fails gracefully with "No patch produced"; a retry would reduce this but costs quota.
- The **quality gate may deterministically reject** a run whose real tests fail — that is correct behavior, and it is what produced the genuine `quality_gate:conflicted` consensus in the final demo.

---

## 7. Free Tier vs Paid — the Real Numbers

| Dimension | Free (AI Studio key) | Paid (AI Studio w/ billing) | Vertex AI |
|---|---|---|---|
| Cost | $0 | ~$1.50 in / ~$7.50-9 out per 1M tokens | Same tokens + Cloud infra |
| Rate limit | ~5-15 RPM, **20 req/day/model** | Tier 1+: hundreds of RPM | IAM-scoped, negotiable |
| Daily cap | Yes (per model) | None meaningful | None |
| Data training | **Used for product improvement** | Not used for training | Not used (enterprise terms) |
| Auth | Simple API key | API key + billing | IAM / service accounts / OAuth |
| Best for | Prototyping, demos, CI smoke tests | Production apps | Enterprise |

**Bottom line**: the free key is perfect for machine-verifiable demos and CI. For real workloads, attach billing (keeps the same key format) or move to Vertex for compliance-grade control.

### 7.1 Paid tier — the `DEVPILOT_GEMINI_TIER` knob (Phase 20B B1)

Attaching billing to the **same** AI Studio key (no key rotation, no new auth) is
now a first-class config switch. `DEVPILOT_GEMINI_TIER` selects the runtime
behavior:

| | `free` (default) | `paid` |
|---|---|---|
| Cross-model daily-quota failover | ✅ automatic (`_CANDIDATE_MODELS`) | ❌ disabled (billing = no daily buckets) |
| 24h exhaustion markers | ✅ (`_exhausted_at`, TTL-pruned) | ❌ never written |
| Quota/billing error on call | → fail over to next model | → **fail fast** with a clear "check your plan and billing" error |
| Transient per-minute 429 retry | ✅ exponential backoff | ✅ unchanged |
| Model candidates | `gemini-3.6-flash` → flash-lite → 3.5-flash | `DEVPILOT_GEMINI_PAID_MODELS` if set (first = default), else the default model |

```
# backend/.env — same key, paid mode
DEVPILOT_LLM_PROVIDER=gemini
GEMINI_API_KEY=<same key, billing attached in AI Studio>
DEVPILOT_GEMINI_TIER=paid
# Optional: pin models (first is the default). Without this the default model is used.
DEVPILOT_GEMINI_PAID_MODELS=gemini-3.6-pro-preview,gemini-3.6-flash
```

Verification: `POST /api/v1/providers/test` now returns `gemini_tier` +
`gemini_models` in its data when the active provider is Gemini, so a paid key
can be confirmed to run in the intended mode. `GET /api/v1/providers/config`
also exposes `data.gemini.tier` / `data.gemini.paid_models` (secret-safe).

---

## 8. Security & Privacy Notes

- ✅ Key stored only in `.env` (gitignored), loaded via pydantic-settings — never in source code.
- ✅ `--live` guards prevent accidental runs without a configured key.
- ✅ Error messages sanitize the key (masked `****` in DB URLs; API errors passed through are Google's, not ours).
- ⚠️ **Free-tier privacy caveat**: Google may use prompts/responses to improve products. Never send proprietary code on a free key.

---

## 9. Live Demo Results (final run — Demonstration A closed)

```
Run: RUN-934077FA (rejected by the deterministic quality gate)
Consensus records: 3
  [test_status]    CONFLICTED  confidence=low  (0.49)   decision: tests_failing
  [patch_complete] CONFLICTED  confidence=high (1.0)    decision: patch_conflicts_with_tests
  [quality_gate]   CONFLICTED  confidence=high (1.0)    decision: rejected
Contradictions: 5
  [claim_vs_test] coding claimed success but test evidence reports failure
     resolution: deterministic_wins  (test_result: failed)
  [claim_vs_gate] planner claimed readiness but the quality gate rejected
  ... (3 more, all resolved as deterministic_wins)

Autonomy goal: 3 consensus topics
  test_status:agreed:tests_passed
  patch_complete:agreed:patch_consistent
  quality_gate:conflicted:rejected

Patches: 5 generated · 5 applied · 0 quota errors (failover: 1)
```

This demonstrates the **core Phase 17 invariant** with real content: deterministic evidence (test results, quality gate) outranks LLM claims, and consensus is evidence-driven with bounded confidence — never chain-of-thought.

---

## 10. The 8 Live-Path Fixes (all regression-tested)

1. **Gemini quota failover** — `_CANDIDATE_MODELS` (3.6-flash → flash-lite → 3.5-flash); permanent daily caps fail fast, transient 429s retry with backoff.
2. **Real retrieval** — `_stage_retrieval` previously passed a `RepositoryCodeIndex` as `lexical_index` (every `retrieve()` raised `AttributeError` on `.built`); now uses `build_with_indexes()` + `set_indexes()`.
3. **Idempotent `_transition_to`** — same-stage calls (`coding → coding`) are no-ops instead of `TransitionError` crashes.
4. **Demo/autonomy context** — zero-item `retrieved_context` stubs made the coding LLM return `INSUFFICIENT_CONTEXT`; live mode now lets real retrieval run (demos 15/17 + autonomy service).
5. **JSON braces-in-strings** — `_extract_json` now takes first-`{` to last-`}` (braces inside `new_content` no longer truncate).
6. **Concatenated JSON objects** — `_load_json_with_fallback` tries every `{`-span until one parses.
7. **Patch hash enrichment** — `_enrich_patch_hashes` computes `original_hash` for MODIFY/DELETE from the workspace (hallucinated files still rejected); plus the `PatchValidator.validate()` call-site fix.
8. **Workspace structure in the coding prompt** — `workspace_structure` field on `CodingAgentInput`, surfaced from `_stage_coding` so the LLM knows which files exist.

---

## 11. Recommendations

1. **Keep `gemini-3.5-flash-lite` / `gemini-3.6-flash` as defaults** — verified working; if a default exhausts its daily bucket the failover covers it.
2. **Document the free-tier data-training caveat** in the README demo section (privacy matters for real workloads).
3. **Production path**: attach billing to the same key (keeps the same key format) — **DONE (Phase 20B B1, Session 37)**: flip `DEVPILOT_GEMINI_TIER=paid`; no code or key change required. Or move to Vertex AI for IAM + no-training guarantees.
4. **Demo cadence**: run `--live` once per day after the midnight-Pacific reset (fresh per-model buckets), or with a paid key for unlimited runs.
5. **Optional improvement**: one LLM retry when the coding agent returns an empty patch would reduce the ~20-25% empty-response variance at the cost of quota.

---

**Bottom line**: the Gemini key workflow is clean, secure, and now quota-resilient. The free tier cost $0 and exposed eight real latent bugs in the live pipeline — all fixed and regression-tested — culminating in a live demo that produces genuine evidence-driven consensus.
