# Enterprise Roadmap — Self-Hosted Autonomous Engineering Platform

> **Status**: PROPOSED — Phase 21+ planning document
> **Date**: August 5, 2026
> **Basis**: Phase 20 is COMPLETE (A1–A6, B1–B3, D, E). DevPilot is a proven
> end-to-end multi-agent autonomous engineering platform (1696+ backend tests,
> 49 frontend vitest, live multi-provider routing). The execution layer works;
> the commercial layer does not yet exist. This roadmap turns DevPilot from a
> **single-tenant, API-key-coupled demo** into a **self-hosted, org-governed,
> data-owning enterprise platform** that multinational GitHub-based companies
> can adopt, and that investors and collaborators can defend.

---

## 1. The Thesis

**Current state (the problem):** DevPilot's thinking is powered by cloud LLM
API keys. An enterprise hears "wrapper around someone else's model" — a
commodity. Source code egress, per-token cost, vendor lock-in, and zero
tenant isolation are all adoption blockers for regulated multinational orgs.

**The pivot:** Process every stage *inside the customer's infrastructure*,
fine-tune models on *their* codebase, and govern every patch with the
deterministic gates DevPilot already has. Source never leaves the tenant
boundary; the platform is the durable value — not the API key.

> **Investor one-liner:** *"DevPilot is a self-hosted autonomous engineering
> platform — source code never leaves your infrastructure, models are
> fine-tuned on your codebase, and every patch is governed by deterministic
> quality gates."* That is infrastructure, not a wrapper.

---

## 2. Strategic Pillars

```
PILLAR 1  Self-Hosted Inference Fabric   — local-first, offline-capable, no API key required
PILLAR 2  Data Moat & Fine-Tuning        — models that learn the tenant's codebase
PILLAR 3  GitHub Multinational Wedge     — org-scope GitHub App, PR checks, policy-as-code
PILLAR 4  Multi-Tenancy & Enterprise Sec — SSO/RBAC, tenant isolation, audit, SOC 2
PILLAR 5  Distributed Execution          — queue-driven workers, GPU scheduling, HA
PILLAR 6  Commercial Readiness           — metering/billing, SLOs, observability, versioning
PILLAR 7  Career / Analytics Product     — DORA-aligned engineering analytics + playbook marketplace
```

---

## 3. Workstreams

> Workstream letters are **E** (Enterprise) to avoid clashing with Phase 20's
> A/B/C/D. Each workstream is sliced so a slice is independently testable and
> shippable, matching DevPilot's deterministic-gate + test-first culture.

### E1. Self-Hosted Inference Fabric  ← THE CORE PIVOT

Goal: prove "no API key required" on any GPU box, today.

- **E1a. ModelRegistry** (`backend/app/llm/model_registry.py` — new):
  maps model id → backend (`ollama`, `vllm`, `sglang`, `tgi`, `llama.cpp`),
  weights/quantization, GPU placement, capability tags. Config-driven:
  `DEVPILOT_MODEL_REGISTRY` (JSON) + `DEVPILOT_INFERENCE_MODE=local-first|cloud-burst|offline`.
- **E1b. Local provider backends**: extend the keyless `ollama` provider
  (`backend/app/llm/providers/ollama.py`) plus a new OpenAI-compatible
  `vllm`/`tgi` provider (same protocol as `openrouter` — cheap to add).
- **E1c. Local-first routing policy** in `ProviderRouter`
  (`backend/app/llm/router.py`): local providers are tried FIRST; cloud is
  burst-fallback only when `cloud-burst`; `offline` mode = zero egress,
  hard-fails if a local model is unavailable. Existing `Capability` enum
  (Phase 20B B2) selects the per-capability local model
  (coding → big coder model, planning/review → small fast model).
- **E1d. Local embeddings**: `sentence-transformers`-style local embedding
  provider so the Phase 5 RAG vector index and Phase 12 pgvector semantic
  layer run fully offline (no OpenAI embeddings dependency).
- **E1e. Offline/air-gap profile**: a single config bundle (`DEVPILOT_INFERENCE_MODE=offline`)
  proving a complete run with zero external network calls; test asserts no
  outbound traffic (mocked `httpx` transport must never be hit).
- **E1f. GPU/resource governor**: per-tenant concurrency, warm/cold model
  loading, quantized weight autodownload (with air-gap mirror support).

**Exit criteria**: a full E2E run executed against local models with
`DEVPILOT_INFERENCE_MODE=offline`; new `tests/test_local_inference.py`
suite green.

### E2. Data Moat & Fine-Tuning Pipeline

Goal: the platform's value compounds per tenant.

- **E2a. Preference-data extraction**: every repair loop, reviewer rejection,
  and quality-gate verdict is labeled. New `FineTuneService` exports
  `(prompt, accepted_patch)` / `(prompt, rejected_patch)` pairs plus
  reviewer/evidence annotations to a dataset store.
- **E2b. Run telemetry dataset** (`provider_metric_snapshots` + new
  `run_telemetry` table): structured signals per run stage — latency, tokens,
  failover events, repair iterations, gate decisions.
- **E2c. Fine-tune launcher**: kick LoRA fine-tunes (QLoRA, on tenant GPU or
  burst cloud) on the extracted dataset; register the artifact back in the
  ModelRegistry so routing can prefer the tenant-tuned model.
- **E2d. Retrieval-over-repair** (leverages Phase 19 semantic EKG retrieval):
  index past repairs so the RAG layer (Phase 5 hybrid retrieval) surfaces
  prior fixes — "never fix the same bug twice."
- **E2e. Internal benchmark**: a fixed corpus of runs with known outcomes →
  week-over-week "model quality" dashboard. Investor-grade numbers.

**Exit criteria**: a documented dataset artifact + benchmark table produced
from existing run history; fine-tune launcher covered by tests
(mocked training backend).

### E3. GitHub Multinational Wedge

Goal: the acquisition channel into orgs. DevPilot becomes a required status
check on PRs across whole organizations.

- **E3a. GitHub App (OAuth, org scope)**: replaces raw `GITHUB_TOKEN`.
  Install at organization scope; act across thousands of repos with one
  install. Uses the existing `GitHubService`/`RepoAcquirer` foundations.
- **E3b. Checks API + branch protection**: post quality-gate verdicts as
  `check_run`; inline DET-XXX findings as `pull_request_review` comments;
  configurable "never auto-merge" per policy.
- **E3c. Webhooks**: auto-trigger runs on `pull_request` / `push` /
  `check_suite` events (existing `WS` infra style).
- **E3d. Policy-as-code**: `devpilot.yaml` in repo root — allowed executors,
  blocked paths, severity thresholds, per-team review mode. `ExecutionPolicy`
  / `RepairPolicy` / `QualityGate` become per-tenant configurable.
- **E3e. Fleet analytics**: org-wide dashboard — run health, defect density
  per repo, repair rates. The artifact engineering leaders show VPs.

**Exit criteria**: org-installed app runs a PR check end-to-end against a
private org; policy file respected; 8+ new tests for policy parsing and
check payloads.

### E4. Multi-Tenancy & Enterprise Security

Goal: the checkbox that unblocks enterprise pilots.

- **E4a. Identity**: SSO via SAML/OIDC (Okta, Entra ID, GitHub SSO); RBAC
  roles (admin/dev/reviewer/auditor); MFA; SCIM provisioning.
- **E4b. Tenant isolation**: tenant-scoped EKG namespaces (leverage Phase
  19C/20 namespaces as tenant boundaries) + `tenant_id` on runs, graphs,
  indexes; schema-per-tenant or row-level security in PostgreSQL.
- **E4c. Audit trail**: append-only `AuditEvent` log of every agent decision,
  file write, model call, gate verdict, and admin action. Reuse `RunEvent`
  patterns + add immutability.
- **E4d. Secrets via KMS/Vault**: replace `.env` key handling with
  HashiCorp Vault / cloud KMS-backed provider credentials (existing
  redaction layer `app/llm/redaction.py` extended to never expose even
  masked values outside the tenant).
- **E4e. Compliance artifact pack**: SOC 2 / ISO 27001 evidence generation —
  audit export, data-retention controls, encryption at rest/in transit.

**Exit criteria**: two tenants run concurrently with zero cross-tenant data
leak (asserted by tests); SSO login flow; audit export endpoint.

### E5. Distributed Execution

Goal: prove enterprise-grade under load.

- **E5a. Job queue**: Redis/Celery (or Kafka + worker pool) — runs become
  durable, prioritizable jobs with per-tenant quotas. `OrchestrationService`
  stages decouple from the request lifecycle.
- **E5b. Worker topology**: analysis workers, GPU coding workers, sandboxed
  test workers (`ControlledExecutionEngine` gains a remote/sandbox target).
- **E5c. HA & scale**: read replicas, connection pooling at scale,
  multi-region deployment, backups/DR. `DATABASE_URL` is already
  config-driven — the platform becomes deployable as Helm chart.
- **E5d. Offline/on-prem packaging**: `docker-compose` + Helm chart, no
  phone-home, air-gap install path.

**Exit criteria**: 10 concurrent runs across a 3-worker cluster; run
survives worker restart (resumes from checkpoint — leverage Phase 16
autonomy checkpoints).

### E6. Commercial Readiness

Goal: sellable, measurable, observable.

- **E6a. Metering/billing**: per-tenant usage (GPU-hours, agent calls, run
  count) → usage records; pricing hooks.
- **E6b. SLOs + observability**: OpenTelemetry traces, Prometheus metrics,
  run SLOs. Lift the Phase 19B provider health/metrics surface to the whole
  platform.
- **E6c. API hardening**: explicit `/api/v1` versioning, rate limits,
  API-key/JWT auth on all endpoints, audit on API access.
- **E6d. Docs & onboarding**: deployment guide, security model, pricing
  tier docs, migration guide from single-tenant.

**Exit criteria**: usage report generated per tenant; SLO dashboard live;
public API v1 documented with auth.

### E7. Career / Analytics & Marketplace

Goal: the product surfaces that make engineering leaders subscribe.

- **E7a. DORA-aligned analytics**: map run data → deployment frequency,
  change lead time, change failure rate, MTTR dashboards.
- **E7b. Playbook marketplace**: pluggable detectors, fix patterns, review
  rules as installable plugins (mirrors DevPilot's detector-service +
  DET-XXX design), shareable across orgs.

**Exit criteria**: a shareable playbook installs and changes review behavior
(asserted by test); analytics page renders from real run telemetry.

---

## 4. 90-Day Prioritized Plan

| Sprint | Ship | Why first |
|---|---|---|
| 1 | **E1** — local-first provider + ModelRegistry + `DEVPILOT_INFERENCE_MODE` (+ offline test) | Proves the no-API-key thesis on any GPU box, immediately demoable |
| 2 | **E2** — preference-data extraction + fine-tune launcher + benchmark | The moat + the investor demo ("learns your codebase") |
| 3 | **E3** — GitHub App org-install + Checks API + webhooks + policy-as-code | The acquisition channel into multinational orgs |
| 4 | **E4** — SSO/RBAC + tenant isolation + audit log | The enterprise checkbox that unblocks pilots |
| 5 | **E5** — queue/worker scale-out + on-prem packaging | Proves enterprise-grade under load |
| 6 | **E6 + E7** — metering, SLOs, analytics, marketplace | Closes the deal and starts recurring revenue |

**Dependency notes:** E1 is the foundation for E2 (fine-tuning needs the
model registry to register artifacts). E4 must land before E5/E6 multi-tenant
metering. E3 is independent and can start in parallel once E1's provider
abstraction is stable.

---

## 5. Success Metrics

- **No-API-key demo**: full E2E run with `DEVPILOT_INFERENCE_MODE=offline` on a
  consumer GPU — 0 external requests.
- **Tenant isolation**: multi-tenant test suite proves zero cross-tenant leak.
- **Org adoption**: one GitHub App install drives runs across 100+ repos.
- **Model improvement**: benchmark table showing tenant-tuned model beats
  base model on repair-success rate.
- **Engineering health**: full suite stays green (>1696 backend tests) with
  every workstream slice.

---

## 6. Investor Positioning

**Problem:** autonomous engineering is commoditized at the wrapper layer;
regulated enterprises won't ship code to third-party model APIs and won't
adopt a platform without tenant isolation, audit, and policy control.

**Solution:** self-hosted inference + fine-tuning on the tenant's codebase +
deterministic quality gates + org-scope GitHub integration + enterprise
security (SSO, RBAC, audit, SOC2 artifacts).

**Differentiators vs. GitHub Copilot / Cursor / Codeium / AWS CodeWhisperer:**
autonomous end-to-end (not autocomplete), self-hosted (not SaaS-only),
fine-tuned per tenant (not one generic model), deterministic gated output
(not "just suggestions"), org-wide policy + checks API integration.

**TAM path:** engineering analytics + autonomous engineering
subscription = seat-based + usage-based revenue from multinational
engineering orgs.

---

## 7. Open Questions

1. Primary onboarding target: mid-market SaaS (fast sales, lower bar) vs.
   regulated enterprises (slow, high-value, needs SOC2 first)? Recommend
   starting mid-market, E4 compliance pack for enterprise later.
2. GPU capacity assumption for E1 default: is `offline` mode aimed at a
   single GPU dev box (Qwen2.5-Coder-32B-GGUF, quantized) or GPU clusters
   (vLLM)? Slices E1a–E1f are written to support both.
3. Fine-tuning (E2) initially tenant-on-prem or burst-to-cloud with
   data-stay-guardrails? Recommend on-prem by default to preserve the thesis.
