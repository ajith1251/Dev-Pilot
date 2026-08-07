"""
Application configuration.

Uses pydantic-settings to load configuration from environment variables
and .env files. Never hard-code secrets.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Global application settings loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ──────────────────────────────────────────────
    APP_NAME: str = "DevPilot"
    APP_VERSION: str = "0.1.0"
    APP_DESCRIPTION: str = "Autonomous Multi-Agent Software Engineering Platform"
    DEBUG: bool = Field(default=False, alias="DEVPILOT_DEBUG")
    LOG_LEVEL: str = Field(default="INFO", alias="DEVPILOT_LOG_LEVEL")

    # ── Server ───────────────────────────────────────────────────
    HOST: str = Field(default="0.0.0.0", alias="DEVPILOT_HOST")
    PORT: int = Field(default=8000, alias="DEVPILOT_PORT")
    CORS_ORIGINS: List[str] = Field(
        default=["*"], alias="DEVPILOT_CORS_ORIGINS"
    )

    # ─── LLM Provider (default) ──────────────────────────────────
    LLM_PROVIDER: str = Field(default="openai", alias="DEVPILOT_LLM_PROVIDER")
    LLM_MODEL: str = Field(default="gpt-4o-mini", alias="DEVPILOT_LLM_MODEL")
    LLM_TEMPERATURE: float = Field(default=0.3, alias="DEVPILOT_LLM_TEMPERATURE")
    LLM_MAX_TOKENS: int = Field(default=4096, alias="DEVPILOT_LLM_MAX_TOKENS")

    # ─── OpenAI ──────────────────────────────────────────────────
    OPENAI_API_KEY: Optional[str] = Field(default=None, alias="OPENAI_API_KEY")
    OPENAI_BASE_URL: Optional[str] = Field(default=None, alias="OPENAI_BASE_URL")

    # ─── Anthropic ───────────────────────────────────────────────
    ANTHROPIC_API_KEY: Optional[str] = Field(default=None, alias="ANTHROPIC_API_KEY")

    # ─── Gemini (Google AI) ──────────────────────────────────────
    GEMINI_API_KEY: Optional[str] = Field(default=None, alias="GEMINI_API_KEY")
    GEMINI_TIER: str = Field(
        default="free", alias="DEVPILOT_GEMINI_TIER",
        description="Gemini key tier (Phase 20B B1): 'free' (Google AI "
                    "Studio free tier — each model has its own ~20 req/day "
                    "bucket, so calls fail over across candidate models and "
                    "exhausted models are remembered for 24h) or 'paid' "
                    "(billing attached to the key — no daily-quota failover "
                    "and no exhaustion markers; transient per-minute 429s "
                    "are still retried).",
    )
    GEMINI_PAID_MODELS: List[str] = Field(
        default=[], alias="DEVPILOT_GEMINI_PAID_MODELS",
        description="Comma-separated Gemini models for the paid tier, e.g. "
                    "'gemini-3.6-pro-preview,gemini-3.6-flash'. The first is "
                    "the default model. Only used when DEVPILOT_GEMINI_TIER="
                    "paid; empty keeps the provider default.",
    )

    # ─── OpenRouter (Phase 19B) ──────────────────────────────────
    OPENROUTER_API_KEY: Optional[str] = Field(
        default=None, alias="OPENROUTER_API_KEY",
        description="OpenRouter API key (OpenAI-compatible multi-model router)",
    )
    OPENROUTER_BASE_URL: str = Field(
        default="https://openrouter.ai/api/v1", alias="OPENROUTER_BASE_URL",
    )
    OPENROUTER_MODEL: Optional[str] = Field(
        default=None, alias="DEVPILOT_OPENROUTER_MODEL",
        description="OpenRouter model override (OpenAI-compatible multi-model "
                    "router), e.g. 'poolside/laguna-s-2.1:free' for the free "
                    "tier. Unset defaults to 'openrouter/auto'. Independent of "
                    "DEVPILOT_LLM_MODEL (which is OpenAI-biased) so the "
                    "provider keeps its own default across failover.",
    )

    # ─── Ollama (Phase 19B) ──────────────────────────────────────
    OLLAMA_BASE_URL: Optional[str] = Field(
        default=None, alias="OLLAMA_BASE_URL",
        description="Local Ollama OpenAI-compatible endpoint (e.g. "
                    "http://localhost:11434/v1). When unset Ollama is "
                    "reported as not-configured.",
    )

    # ─── NVIDIA NIM ──────────────────────────────────────────────
    NVIDIA_API_KEY: Optional[str] = Field(
        default=None, alias="NVIDIA_API_KEY",
        description="NVIDIA NIM hosted API key (OpenAI-compatible inference "
                    "endpoint). Get one at https://build.nvidia.com — required "
                    "for the 'nvidia' provider. When unset NVIDIA is reported "
                    "as not-configured and the router fails over.",
    )
    NVIDIA_BASE_URL: str = Field(
        default="https://integrate.api.nvidia.com/v1", alias="NVIDIA_BASE_URL",
        description="NVIDIA NIM OpenAI-compatible chat-completions endpoint. "
                    "Defaults to the hosted build; point this at a self-hosted "
                    "NIM microservice (or an NGC catalog endpoint) for a "
                    "private deployment.",
    )
    NVIDIA_MODEL: Optional[str] = Field(
        default=None, alias="DEVPILOT_NVIDIA_MODEL",
        description="NVIDIA NIM model override, e.g. 'meta/llama-3.1-8b-"
                    "instruct' or 'nvidia/llama-3.3-nemotron-super-49b-v1'. "
                    "Unset defaults to 'meta/llama-3.1-8b-instruct' (measured "
                    "fastest cold-start + best free-tier availability of the "
                    "hosted NIM catalog on the live key; see the bake-off "
                    "table in workflow-status/NVIDIA_PROVIDER_COMPLETION_"
                    "REPORT.md). Independent of DEVPILOT_LLM_MODEL (which is "
                    "OpenAI-biased) so the provider keeps its own default "
                    "across failover.",
    )
    NVIDIA_TIMEOUT_SECONDS: float = Field(
        default=300.0, ge=1.0, le=600.0,
        alias="DEVPILOT_NVIDIA_TIMEOUT_SECONDS",
        description="Per-request timeout passed to the OpenAI-compatible NIM "
                    "client (transport level). Generous because the hosted "
                    "build can take 60-370s to cold-start an inference pod on "
                    "the first call; keep it >= the router timeout "
                    "(DEVPILOT_PROVIDER_TIMEOUT_SECONDS) so the router's "
                    "bounded retry/failover is the effective policy.",
    )
    NVIDIA_MAX_RETRIES: int = Field(
        default=2, ge=0, le=10,
        alias="DEVPILOT_NVIDIA_MAX_RETRIES",
        description="Transport-level retries the OpenAI-compatible NIM client "
                    "performs per request (on top of router-level failover).",
    )

    # ─── Cloudflare Workers AI (Phase 20F) ───────────────────────
    CLOUDFLARE_API_KEY: Optional[str] = Field(
        default=None, alias="CLOUDFLARE_API_KEY",
        description="Cloudflare API token (Workers AI). Get one at "
                    "https://dash.cloudflare.com/profile/api-tokens — required "
                    "for the 'cloudflare' provider. When unset Cloudflare is "
                    "reported as not-configured and the router fails over.",
    )
    CLOUDFLARE_ACCOUNT_ID: Optional[str] = Field(
        default=None, alias="CLOUDFLARE_ACCOUNT_ID",
        description="Cloudflare account id, used to build the default Workers "
                    "AI OpenAI-compatible base URL. Optional when "
                    "CLOUDFLARE_BASE_URL is set explicitly.",
    )
    CLOUDFLARE_BASE_URL: Optional[str] = Field(
        default=None, alias="CLOUDFLARE_BASE_URL",
        description="Cloudflare Workers AI OpenAI-compatible chat-completions "
                    "endpoint. Defaults to "
                    "https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}"
                    "/ai/v1 when CLOUDFLARE_ACCOUNT_ID is set.",
    )
    CLOUDFLARE_MODEL: Optional[str] = Field(
        default=None, alias="DEVPILOT_CLOUDFLARE_MODEL",
        description="Cloudflare Workers AI model override, e.g. "
                    "'@cf/meta/llama-4-scout-17b-16e-instruct'. Unset defaults "
                    "to that model (live-verified fastest Workers AI model, "
                    "~0.5s TTF, 17B MoE; the 2024-era "
                    "'@cf/meta/llama-3.1-8b-instruct' was deprecated by "
                    "Cloudflare in 2026). Independent of DEVPILOT_LLM_MODEL "
                    "(which is OpenAI-biased) so the provider keeps its own "
                    "default across failover.",
    )
    CLOUDFLARE_TIMEOUT_SECONDS: float = Field(
        default=60.0, ge=1.0, le=600.0,
        alias="DEVPILOT_CLOUDFLARE_TIMEOUT_SECONDS",
        description="Per-request timeout passed to the OpenAI-compatible "
                    "Workers AI client (transport level). Keep it >= the "
                    "router timeout (DEVPILOT_PROVIDER_TIMEOUT_SECONDS) so "
                    "the router's bounded retry/failover is the effective "
                    "policy.",
    )
    CLOUDFLARE_MAX_RETRIES: int = Field(
        default=2, ge=0, le=10,
        alias="DEVPILOT_CLOUDFLARE_MAX_RETRIES",
        description="Transport-level retries the OpenAI-compatible Workers AI "
                    "client performs per request (on top of router-level "
                    "failover).",
    )

    # ─── Generic OpenAI-compatible endpoint (Phase 20F) ──────────
    OPENAI_COMPATIBLE_BASE_URL: Optional[str] = Field(
        default=None, alias="OPENAI_COMPATIBLE_BASE_URL",
        description="Base URL of an arbitrary OpenAI-compatible chat-"
                    "completions endpoint (self-hosted vLLM/TGI, llama.cpp, "
                    "LM Studio, an OpenAI-compatible cloud gateway, a remote "
                    "Ollama server, etc.). Required for the "
                    "'openai_compatible' provider; when unset it is reported "
                    "as not-configured and the router fails over.",
    )
    OPENAI_COMPATIBLE_API_KEY: Optional[str] = Field(
        default=None, alias="OPENAI_COMPATIBLE_API_KEY",
        description="Optional API key for the OpenAI-compatible endpoint. "
                    "Most cloud gateways require one; local servers usually "
                    "ignore it, so it may stay unset.",
    )
    OPENAI_COMPATIBLE_MODEL: Optional[str] = Field(
        default=None, alias="DEVPILOT_OPENAI_COMPATIBLE_MODEL",
        description="Model served by the OpenAI-compatible endpoint, e.g. "
                    "'meta-llama/Meta-Llama-3.1-8B-Instruct'. Set this for "
                    "real use; unset falls back to the OpenAI-sentinel "
                    "default. Independent of DEVPILOT_LLM_MODEL so the "
                    "provider keeps its own default across failover.",
    )
    OPENAI_COMPATIBLE_TIMEOUT_SECONDS: float = Field(
        default=60.0, ge=1.0, le=600.0,
        alias="DEVPILOT_OPENAI_COMPATIBLE_TIMEOUT_SECONDS",
        description="Per-request timeout passed to the OpenAI-compatible "
                    "client (transport level). Keep it >= the router timeout "
                    "(DEVPILOT_PROVIDER_TIMEOUT_SECONDS).",
    )
    OPENAI_COMPATIBLE_MAX_RETRIES: int = Field(
        default=2, ge=0, le=10,
        alias="DEVPILOT_OPENAI_COMPATIBLE_MAX_RETRIES",
        description="Transport-level retries the OpenAI-compatible client "
                    "performs per request (on top of router-level failover).",
    )

    # ─── Ollama Cloud (Phase 20F) ─────────────────────────────────
    OLLAMA_CLOUD_API_KEY: Optional[str] = Field(
        default=None, alias="OLLAMA_CLOUD_API_KEY",
        description="Ollama Cloud API key (https://ollama.com/settings/keys). "
                    "Required for the 'ollama_cloud' provider; when unset it "
                    "is reported as not-configured and the router fails over.",
    )
    OLLAMA_CLOUD_BASE_URL: Optional[str] = Field(
        default=None, alias="OLLAMA_CLOUD_BASE_URL",
        description="Ollama Cloud OpenAI-compatible chat-completions endpoint. "
                    "Defaults to https://ollama.com/v1.",
    )
    OLLAMA_CLOUD_MODEL: Optional[str] = Field(
        default=None, alias="DEVPILOT_OLLAMA_CLOUD_MODEL",
        description="Ollama Cloud model override, e.g. 'gemma4:31b' or "
                    "'gpt-oss:120b'. Unset defaults to 'gemma4:31b' "
                    "(live-verified to return content even at small "
                    "max_tokens; gpt-oss/nemotron models on this endpoint "
                    "can return empty content at max_tokens<64). Independent "
                    "of DEVPILOT_LLM_MODEL so the provider keeps its own "
                    "default across failover.",
    )
    OLLAMA_CLOUD_TIMEOUT_SECONDS: float = Field(
        default=60.0, ge=1.0, le=600.0,
        alias="DEVPILOT_OLLAMA_CLOUD_TIMEOUT_SECONDS",
        description="Per-request timeout passed to the Ollama Cloud "
                    "OpenAI-compatible client (transport level). Keep it >= the "
                    "router timeout (DEVPILOT_PROVIDER_TIMEOUT_SECONDS).",
    )
    OLLAMA_CLOUD_MAX_RETRIES: int = Field(
        default=2, ge=0, le=10,
        alias="DEVPILOT_OLLAMA_CLOUD_MAX_RETRIES",
        description="Transport-level retries the Ollama Cloud client performs "
                    "per request (on top of router-level failover).",
    )

    # ─── OpenCode Zen (Phase 20F) ────────────────────────────────
    OPENCODE_ZEN_API_KEY: Optional[str] = Field(
        default=None, alias="OPENCODE_ZEN_API_KEY",
        description="OpenCode Zen API key (https://opencode.ai/zen). Required "
                    "for the 'opencode_zen' provider; when unset it is "
                    "reported as not-configured and the router fails over.",
    )
    OPENCODE_ZEN_BASE_URL: Optional[str] = Field(
        default=None, alias="OPENCODE_ZEN_BASE_URL",
        description="OpenCode Zen OpenAI-compatible chat-completions endpoint. "
                    "Defaults to https://opencode.ai/zen/v1.",
    )
    OPENCODE_ZEN_MODEL: Optional[str] = Field(
        default=None, alias="DEVPILOT_OPENCODE_ZEN_MODEL",
        description="OpenCode Zen model override, e.g. 'deepseek-v4-flash'. "
                    "Unset defaults to a curated fast model. Independent of "
                    "DEVPILOT_LLM_MODEL so the provider keeps its own default "
                    "across failover.",
    )
    OPENCODE_ZEN_TIMEOUT_SECONDS: float = Field(
        default=60.0, ge=1.0, le=600.0,
        alias="DEVPILOT_OPENCODE_ZEN_TIMEOUT_SECONDS",
        description="Per-request timeout passed to the OpenCode Zen "
                    "OpenAI-compatible client (transport level). Keep it >= the "
                    "router timeout (DEVPILOT_PROVIDER_TIMEOUT_SECONDS).",
    )
    OPENCODE_ZEN_MAX_RETRIES: int = Field(
        default=2, ge=0, le=10,
        alias="DEVPILOT_OPENCODE_ZEN_MAX_RETRIES",
        description="Transport-level retries the OpenCode Zen client performs "
                    "per request (on top of router-level failover).",
    )

    # ─── GitHub ──────────────────────────────────────────────────
    GITHUB_TOKEN: Optional[str] = Field(default=None, alias="GITHUB_TOKEN")

    # ─── Database (PostgreSQL) ────────────────────────────────────
    DATABASE_URL: Optional[str] = Field(
        default=None, alias="DATABASE_URL",
        description="PostgreSQL connection string for development",
    )
    TEST_DATABASE_URL: Optional[str] = Field(
        default=None, alias="TEST_DATABASE_URL",
        description="PostgreSQL connection string for integration tests",
    )

    # ─── Embedding Provider (Phase 5) ────────────────────────────
    EMBEDDING_PROVIDER: str = Field(
        default="fake", alias="DEVPILOT_EMBEDDING_PROVIDER",
        description="Embedding provider: 'fake', 'openai', 'anthropic'",
    )
    EMBEDDING_MODEL: str = Field(
        default="text-embedding-3-small", alias="DEVPILOT_EMBEDDING_MODEL",
        description="Embedding model name (e.g. 'text-embedding-3-small')",
    )
    EMBEDDING_DIMENSION: int = Field(
        default=256, ge=64, le=3072, alias="DEVPILOT_EMBEDDING_DIMENSION",
        description="Embedding vector dimension",
    )

    # ── Coding & Patching (Phase 6) ────────────────────────────
    CODING_MAX_FILES_PER_PATCH: int = Field(
        default=20, ge=1, le=100,
        alias="DEVPILOT_CODING_MAX_FILES_PER_PATCH",
        description="Maximum files per PatchSet",
    )
    CODING_MAX_FILE_SIZE: int = Field(
        default=500_000, ge=1000, le=10_000_000,
        alias="DEVPILOT_CODING_MAX_FILE_SIZE",
        description="Maximum file size in bytes for coding modifications",
    )
    CODING_MAX_PATCH_SIZE: int = Field(
        default=1_000_000, ge=1000, le=50_000_000,
        alias="DEVPILOT_CODING_MAX_PATCH_SIZE",
        description="Maximum total patch size in bytes",
    )
    CODING_ALLOW_DELETE: bool = Field(
        default=False, alias="DEVPILOT_CODING_ALLOW_DELETE",
        description="Whether to allow DELETE operations in patches",
    )
    CODING_WORKSPACE_ROOT: Optional[str] = Field(
        default=None, alias="DEVPILOT_CODING_WORKSPACE_ROOT",
        description="Base directory for coding workspaces (default: system temp)",
    )

    # ── Testing & Execution (Phase 7) ────────────────────────────
    TEST_DEFAULT_TIMEOUT: int = Field(
        default=60, ge=1, le=600,
        alias="DEVPILOT_TEST_DEFAULT_TIMEOUT",
        description="Default per-command timeout in seconds",
    )
    TEST_MAX_OUTPUT_BYTES: int = Field(
        default=1_048_576, ge=1024, le=100_000_000,
        alias="DEVPILOT_TEST_MAX_OUTPUT_BYTES",
        description="Maximum captured stdout/stderr bytes per process",
    )
    TEST_MAX_COMMANDS: int = Field(
        default=10, ge=1, le=50,
        alias="DEVPILOT_TEST_MAX_COMMANDS",
        description="Maximum commands per test run",
    )
    TEST_ALLOW_BUILD: bool = Field(
        default=False, alias="DEVPILOT_TEST_ALLOW_BUILD",
        description="Whether to allow build commands in test runs",
    )
    TEST_ALLOW_LINT: bool = Field(
        default=False, alias="DEVPILOT_TEST_ALLOW_LINT",
        description="Whether to allow lint commands in test runs",
    )
    TEST_ALLOW_TYPECHECK: bool = Field(
        default=False, alias="DEVPILOT_TEST_ALLOW_TYPECHECK",
        description="Whether to allow typecheck commands in test runs",
    )

    # ── Repair (Phase 8) ─────────────────────────────────────────
    REPAIR_MAX_ATTEMPTS: int = Field(
        default=3, ge=1, le=5,
        alias="DEVPILOT_REPAIR_MAX_ATTEMPTS",
        description="Maximum repair attempts per session (1-5)",
    )
    REPAIR_PROVIDER_RETRIES: int = Field(
        default=1, ge=0, le=3,
        alias="DEVPILOT_REPAIR_PROVIDER_RETRIES",
        description="Number of times to retry LLM provider on failure",
    )
    REPAIR_MAX_CONTEXT_BYTES: int = Field(
        default=200_000, ge=1000, le=5_000_000,
        alias="DEVPILOT_REPAIR_MAX_CONTEXT_BYTES",
        description="Maximum context bytes for repair (file contents sent to LLM)",
    )
    REPAIR_ALLOW_TEST_MODIFICATION: bool = Field(
        default=False, alias="DEVPILOT_REPAIR_ALLOW_TEST_MODIFICATION",
        description="Whether repair can modify test files (default: prefer production code fixes)",
    )
    REPAIR_ALLOW_CONFIG_MODIFICATION: bool = Field(
        default=False, alias="DEVPILOT_REPAIR_ALLOW_CONFIG_MODIFICATION",
        description="Whether repair can modify config files (default: protect verification config)",
    )

    # ── Review & Quality Gate (Phase 9) ────────────────────────────
    REVIEW_MAX_CONTEXT_CHARS: int = Field(
        default=50_000, ge=1000, le=500_000,
        alias="DEVPILOT_REVIEW_MAX_CONTEXT_CHARS",
        description="Maximum context characters sent to ReviewerAgent",
    )
    REVIEW_MAX_FILES: int = Field(
        default=10, ge=1, le=50,
        alias="DEVPILOT_REVIEW_MAX_FILES",
        description="Maximum files to include in review context",
    )
    REVIEW_MAX_CONTENT_PER_FILE: int = Field(
        default=3000, ge=500, le=50_000,
        alias="DEVPILOT_REVIEW_MAX_CONTENT_PER_FILE",
        description="Maximum characters per file in review context",
    )
    REVIEW_REQUIRE_LLM: bool = Field(
        default=False, alias="DEVPILOT_REVIEW_REQUIRE_LLM",
        description="Whether LLM-based review is required for gate decisions",
    )
    REVIEW_REQUIRE_HUMAN_FOR_UNVERIFIED: bool = Field(
        default=True, alias="DEVPILOT_REVIEW_REQUIRE_HUMAN_FOR_UNVERIFIED",
        description="Whether unverified requirements trigger NEEDS_HUMAN_REVIEW",
    )

    # ─── Durability report (Phase 19) ────────────────────────────
    DURABILITY_REPORT_PATH: Optional[str] = Field(
        default=None, alias="DURABILITY_REPORT_PATH",
        description="Path to the latest scripts/durability_report.py JSON "
                    "output served by GET /api/v1/durability/report",
    )

    # ─── Provider Router (Phase 19B) ─────────────────────────────
    PROVIDER_ROUTING_ENABLED: bool = Field(
        default=True, alias="DEVPILOT_PROVIDER_ROUTING_ENABLED",
        description="Route LLM calls through ProviderRouter (failover, "
                    "circuit breakers, health-aware selection). When False, "
                    "factory.get_provider() returns the plain configured "
                    "provider exactly as before Phase 19B.",
    )
    PROVIDER_PRIORITY: List[str] = Field(
        default=[], alias="DEVPILOT_PROVIDER_PRIORITY",
        description="Comma-separated provider priority list, e.g. "
                    "'gemini,openai,anthropic,fake'. When empty the router "
                    "uses [LLM_PROVIDER] followed by every available provider.",
    )
    PROVIDER_DISABLED: List[str] = Field(
        default=[], alias="DEVPILOT_PROVIDER_DISABLED",
        description="Provider names to disable entirely, e.g. 'anthropic,openai' "
                    "or ['anthropic','openai']. NOTE: from .env / process env "
                    "pydantic-settings decodes list fields as JSON before "
                    "validators run, so use the JSON array form "
                    "DEVPILOT_PROVIDER_DISABLED=['anthropic','openai'] there; "
                    "the comma string only works when the value is passed "
                    "programmatically. Disabled providers keep their "
                    "configured/health metadata but are excluded from routing "
                    "and report enabled=false. Empty = every configured "
                    "provider is eligible.",
    )
    LLM_PROVIDER_FALLBACKS: Dict[str, List[str]] = Field(
        default={}, alias="DEVPILOT_LLM_PROVIDER_FALLBACKS",
        description="Per-capability typed provider fallback chains, e.g. "
                    "'planning:anthropic,gemini;coding:gemini,openai'. Each "
                    "capability (analysis|planning|coding|testing|review|"
                    "reasoning|general) restricts which providers are tried "
                    "for calls of that kind instead of the global "
                    "PROVIDER_PRIORITY. Calls without a capability keep the "
                    "global chain.",
    )
    PROVIDER_TIMEOUT_SECONDS: float = Field(
        default=60.0, ge=1.0, le=600.0,
        alias="DEVPILOT_PROVIDER_TIMEOUT_SECONDS",
        description="Per-call timeout applied around a provider request. "
                    "Default 60s is the cold-start-optimized value: it bounds "
                    "how long a cold NVIDIA NIM pod (60-370s first call) "
                    "delays a request before the router fails over to the "
                    "sub-second backups (cloudflare llama-4-scout ~0.5s, "
                    "ollama_cloud gemma4:31b ~0.75s). Raise it only to wait "
                    "out a legit NVIDIA cold start instead of failing over.",
    )
    PROVIDER_RETRY_MAX: int = Field(
        default=2, ge=0, le=10,
        alias="DEVPILOT_PROVIDER_RETRY_MAX",
        description="Bounded retry count per provider on recoverable failures.",
    )
    PROVIDER_RETRY_BASE_BACKOFF_SECONDS: float = Field(
        default=0.5, ge=0.0, le=120.0,
        alias="DEVPILOT_PROVIDER_RETRY_BASE_BACKOFF_SECONDS",
        description="Exponential backoff base between retries.",
    )
    PROVIDER_RETRY_MAX_BACKOFF_SECONDS: float = Field(
        default=10.0, ge=0.1, le=300.0,
        alias="DEVPILOT_PROVIDER_RETRY_MAX_BACKOFF_SECONDS",
        description="Exponential backoff ceiling between retries.",
    )
    PROVIDER_STREAM_RESUME_MAX: int = Field(
        default=3, ge=0, le=20,
        alias="DEVPILOT_PROVIDER_STREAM_RESUME_MAX",
        description="Max mid-stream prefix-resends (Phase 20B token-loss "
                    "recovery) for a single streaming call. When a stream is "
                    "cut off after tokens were already delivered, the router "
                    "resends the full prompt with the partial output injected "
                    "as continuation context to the next provider in the "
                    "chain, so the response continues instead of restarting. "
                    "This caps the number of such resends per call; 0 "
                    "disables mid-stream recovery entirely.",
    )
    PROVIDER_CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = Field(
        default=3, ge=1, le=100,
        alias="DEVPILOT_PROVIDER_CIRCUIT_BREAKER_FAILURE_THRESHOLD",
        description="Consecutive failures that trip a provider circuit open.",
    )
    PROVIDER_CIRCUIT_BREAKER_COOLDOWN_SECONDS: float = Field(
        default=30.0, ge=0.0, le=3600.0,
        alias="DEVPILOT_PROVIDER_CIRCUIT_BREAKER_COOLDOWN_SECONDS",
        description="Seconds a circuit stays open before a half-open probe.",
    )
    PROVIDER_CIRCUIT_BREAKER_HALF_OPEN_MAX_CALLS: int = Field(
        default=2, ge=1, le=50,
        alias="DEVPILOT_PROVIDER_CIRCUIT_BREAKER_HALF_OPEN_MAX_CALLS",
        description="Max half-open probe calls before re-tripping.",
    )
    PROVIDER_HEALTH_WINDOW: int = Field(
        default=100, ge=1, le=10_000,
        alias="DEVPILOT_PROVIDER_HEALTH_WINDOW",
        description="Rolling request window used to compute success rate.",
    )
    PROVIDER_HEALTH_DEGRADED_SUCCESS_RATE: float = Field(
        default=0.5, ge=0.0, le=1.0,
        alias="DEVPILOT_PROVIDER_HEALTH_DEGRADED_SUCCESS_RATE",
        description="Success rate below which a provider is 'degraded'.",
    )
    PROVIDER_HEALTH_UNHEALTHY_SUCCESS_RATE: float = Field(
        default=0.3, ge=0.0, le=1.0,
        alias="DEVPILOT_PROVIDER_HEALTH_UNHEALTHY_SUCCESS_RATE",
        description="Success rate below which a provider is 'unhealthy'.",
    )
    PROVIDER_HEALTH_MIN_SAMPLES: int = Field(
        default=5, ge=1, le=1000,
        alias="DEVPILOT_PROVIDER_HEALTH_MIN_SAMPLES",
        description="Minimum number of real-traffic samples before a "
                    "provider's success rate may rank it degraded/unhealthy "
                    "for health-based selection. Fewer samples than this are "
                    "treated as 'unknown' so a single early failure (cold "
                    "start, one bad call) does not permanently starve the "
                    "provider of traffic and prevent its circuit breaker "
                    "from ever accumulating consecutive failures.",
    )
    PROVIDER_METRICS_PERSIST: bool = Field(
        default=True, alias="DEVPILOT_PROVIDER_METRICS_PERSIST",
        description="Persist provider-metric snapshots to PostgreSQL when "
                    "DATABASE_URL is configured.",
    )

    # ─── Phase 20B: production reliability & operational hardening ───
    # Provider reliability: health probing, recovery detection, adaptive
    # timeouts, health-based selection, post-failure cooldown.
    PROVIDER_HEALTH_PROBE_ENABLED: bool = Field(
        default=True, alias="DEVPILOT_PROVIDER_HEALTH_PROBE_ENABLED",
        description="Periodically issue minimal health probes to configured "
                    "providers so outages are detected and recovery is "
                    "observed without waiting for real traffic.",
    )
    PROVIDER_HEALTH_PROBE_INTERVAL_SECONDS: float = Field(
        default=120.0, ge=0.0, le=86400.0,
        alias="DEVPILOT_PROVIDER_HEALTH_PROBE_INTERVAL_SECONDS",
        description="Seconds between automatic provider health probes. 0 "
                    "disables the background probe loop entirely.",
    )
    PROVIDER_HEALTH_PROBE_TIMEOUT_SECONDS: float = Field(
        default=10.0, ge=1.0, le=300.0,
        alias="DEVPILOT_PROVIDER_HEALTH_PROBE_TIMEOUT_SECONDS",
        description="Per-probe timeout. Probes are lightweight (tiny prompt, "
                    "max_tokens=1) so a slow/cold-starting provider fails over "
                    "fast instead of blocking the probe loop.",
    )
    PROVIDER_HEALTH_BASED_SELECTION: bool = Field(
        default=True, alias="DEVPILOT_PROVIDER_HEALTH_BASED_SELECTION",
        description="Prefer healthier providers within the configured "
                    "priority order: healthy > warming > unknown > degraded, "
                    "with unhealthy providers tried only as a last resort "
                    "(smarter provider selection based on recent health).",
    )
    PROVIDER_ADAPTIVE_TIMEOUT_ENABLED: bool = Field(
        default=True, alias="DEVPILOT_PROVIDER_ADAPTIVE_TIMEOUT_ENABLED",
        description="Scale the per-call timeout from recent observed latency: "
                    "effective timeout = max(base, avg_latency * multiplier) "
                    "capped at PROVIDER_ADAPTIVE_TIMEOUT_MAX_SECONDS. "
                    "Providers that have been answering slowly get a longer "
                    "budget instead of timing out (request timeout "
                    "optimization).",
    )
    PROVIDER_ADAPTIVE_TIMEOUT_MULTIPLIER: float = Field(
        default=3.0, ge=1.0, le=100.0,
        alias="DEVPILOT_PROVIDER_ADAPTIVE_TIMEOUT_MULTIPLIER",
        description="Latency multiplier used for adaptive per-call timeouts.",
    )
    PROVIDER_ADAPTIVE_TIMEOUT_MAX_SECONDS: float = Field(
        default=300.0, ge=1.0, le=600.0,
        alias="DEVPILOT_PROVIDER_ADAPTIVE_TIMEOUT_MAX_SECONDS",
        description="Ceiling for adaptive per-call timeouts. Keep it at or "
                    "below the provider client's own transport timeout (e.g. "
                    "DEVPILOT_NVIDIA_TIMEOUT_SECONDS) so the router's wait_for "
                    "is not the tighter bound for a request the client would "
                    "have completed.",
    )
    PROVIDER_COOLDOWN_AFTER_FAILURE_SECONDS: float = Field(
        default=5.0, ge=0.0, le=3600.0,
        alias="DEVPILOT_PROVIDER_COOLDOWN_AFTER_FAILURE_SECONDS",
        description="Seconds a provider is skipped entirely after a failed "
                    "attempt (configurable cooldown after failures — the "
                    "provider recovers before being tried again).",
    )
    PROVIDER_WARM_UP_SECONDS: float = Field(
        default=30.0, ge=0.0, le=3600.0,
        alias="DEVPILOT_PROVIDER_WARM_UP_SECONDS",
        description="Seconds a recovered provider is treated as 'warming up' "
                    "(eligible, but ranked below fully-healthy providers) "
                    "after a recovery is detected.",
    )
    PROVIDER_METRICS_PERSIST_INTERVAL_SECONDS: float = Field(
        default=300.0, ge=5.0, le=86400.0,
        alias="DEVPILOT_PROVIDER_METRICS_PERSIST_INTERVAL_SECONDS",
        description="Seconds between automatic provider-metric snapshot "
                    "persistence to PostgreSQL (independent of the probe "
                    "loop).",
    )

    # Operational hardening: startup config validation + request limits.
    STARTUP_VALIDATION_STRICT: bool = Field(
        default=False, alias="DEVPILOT_STARTUP_VALIDATION_STRICT",
        description="When True, fail fast (raise at startup) on configuration "
                    "errors found by startup validation. When False, errors "
                    "are logged and exposed via GET /api/v1/operations/"
                    "startup-validation (and the CLI validate-config command) "
                    "but the app still starts.",
    )
    MAX_REQUEST_BODY_BYTES: int = Field(
        default=10_485_760, ge=1024, le=1_073_741_824,
        alias="DEVPILOT_MAX_REQUEST_BODY_BYTES",
        description="Maximum accepted HTTP request body size in bytes; larger "
                    "bodies are rejected with 413 (request limits).",
    )

    @field_validator("PROVIDER_PRIORITY", mode="before")
    @classmethod
    def validate_provider_priority(cls, v) -> List[str]:
        """Accept a comma-separated env string or a JSON list."""
        if v is None or v == "":
            return []
        if isinstance(v, str):
            items = [p.strip().lower() for p in v.split(",") if p.strip()]
            return items
        if isinstance(v, (list, tuple)):
            return [str(p).strip().lower() for p in v if str(p).strip()]
        return [str(v)]

    @field_validator("LLM_PROVIDER_FALLBACKS", mode="before")
    @classmethod
    def validate_provider_fallbacks(cls, v) -> Dict[str, List[str]]:
        """Accept a 'cap:prov1,prov2;cap2:prov3' env string or a JSON dict.

        Keys are lower-cased capability names; values are lower-cased,
        de-duplicated provider names in priority order. Empty entries and
        malformed segments are dropped.
        """
        if v is None or v == "":
            return {}
        if isinstance(v, dict):
            out: Dict[str, List[str]] = {}
            for cap, names in v.items():
                items = [
                    str(n).strip().lower() for n in str(names).split(",") if str(n).strip()
                ]
                if str(cap).strip() and items:
                    out[str(cap).strip().lower()] = items
            return out
        if isinstance(v, str):
            out = {}
            for chunk in v.split(";"):
                chunk = chunk.strip()
                if not chunk:
                    continue
                sep = ":" if ":" in chunk else ("=" if "=" in chunk else None)
                if sep is None:
                    continue
                cap, _, names = chunk.partition(sep)
                items = [n.strip().lower() for n in names.split(",") if n.strip()]
                if cap.strip() and items:
                    out[cap.strip().lower()] = items
            return out
        return {}

    @field_validator("TEST_DEFAULT_TIMEOUT")
    @classmethod
    def validate_test_timeout(cls, v: int) -> int:
        if v < 1 or v > 600:
            raise ValueError(f"TEST_DEFAULT_TIMEOUT must be between 1 and 600")
        return v

    @field_validator("TEST_MAX_OUTPUT_BYTES")
    @classmethod
    def validate_max_output(cls, v: int) -> int:
        if v < 1024 or v > 100_000_000:
            raise ValueError(f"TEST_MAX_OUTPUT_BYTES must be between 1024 and 100,000,000")
        return v

    @field_validator("LOG_LEVEL")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {allowed}")
        return upper

    @field_validator("EMBEDDING_PROVIDER")
    @classmethod
    def validate_embedding_provider(cls, v: str) -> str:
        val = v.lower()
        # NOTE: 'anthropic' was previously listed here but Anthropic offers no
        # embeddings endpoint — create_embedding_service raises
        # NotImplementedError for it, so it is NOT a valid config value.
        # 'hashed' is the deterministic similarity-preserving provider used
        # by the Phase 19 EKG semantic layer (no API required).
        allowed = {"fake", "hashed", "openai"}
        if val not in allowed:
            raise ValueError(
                f"EMBEDDING_PROVIDER must be one of {allowed}, got '{v}'"
            )
        return val

    @field_validator("GEMINI_TIER")
    @classmethod
    def validate_gemini_tier(cls, v: str) -> str:
        val = v.strip().lower()
        if val not in {"free", "paid"}:
            raise ValueError(
                f"GEMINI_TIER must be 'free' or 'paid', got '{v}'"
            )
        return val

    @field_validator("PROVIDER_DISABLED", mode="before")
    @classmethod
    def validate_provider_disabled(cls, v) -> List[str]:
        """Accept a comma-separated env string or a JSON list of names.

        Names are lower-cased and de-duplicated preserving first-seen order.
        Empty entries are dropped; an unset value yields an empty list.
        """
        if v is None or v == "":
            return []
        if isinstance(v, str):
            items = [m.strip().lower() for m in v.split(",") if m.strip()]
        elif isinstance(v, (list, tuple)):
            items = [str(m).strip().lower() for m in v if str(m).strip()]
        else:
            items = [str(v).strip().lower()]
        seen: set = set()
        out: List[str] = []
        for m in items:
            if m and m not in seen:
                seen.add(m)
                out.append(m)
        return out

    @field_validator("GEMINI_PAID_MODELS", mode="before")
    @classmethod
    def validate_gemini_paid_models(cls, v) -> List[str]:
        """Accept a comma-separated env string or a JSON list of model names.

        Names are lower-cased and de-duplicated preserving first-seen order.
        Empty entries are dropped; an unset value yields an empty list.
        """
        if v is None or v == "":
            return []
        if isinstance(v, str):
            items = [m.strip().lower() for m in v.split(",") if m.strip()]
        elif isinstance(v, (list, tuple)):
            items = [str(m).strip().lower() for m in v if str(m).strip()]
        else:
            items = [str(v).strip().lower()]
        seen: set = set()
        out: List[str] = []
        for m in items:
            if m and m not in seen:
                seen.add(m)
                out.append(m)
        return out

    @property
    def is_debug(self) -> bool:
        return self.DEBUG


# Global singleton
settings = Settings()
