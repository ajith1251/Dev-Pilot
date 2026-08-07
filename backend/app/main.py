"""
FastAPI application entry point.

Run with:
    uvicorn app.main:app --reload
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api.health import router as health_router
from app.api.v1.github import router as github_router
from app.api.v1.repositories import router as repos_router
from app.config import settings
from app.core.exceptions import DevPilotError
from app.core.logging import configure_logging, logger
from app.db.database import create_async_engine, dispose_engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: startup / shutdown.

    Startup:
        - Configures logging
        - Validates configuration (Phase 20B: fail-fast diagnostics)
        - Initializes database engine (if DATABASE_URL configured)
        - Runs recovery check to find/mark stale runs from previous session
        - Starts background operational loops (provider health probes,
          provider-metric persistence)

    Shutdown:
        - Stops background loops
        - Disposes database engine
        - Closes WebSocket connections
    """
    configure_logging()
    logger.info(
        "%s v%s starting — debug=%s",
        settings.APP_NAME,
        __version__,
        settings.is_debug,
    )

    # ── Phase 20B: startup configuration validation ─────────────
    # Validate provider/database configuration up front and log clear
    # diagnostics. Strict mode (DEVPILOT_STARTUP_VALIDATION_STRICT=true)
    # raises on errors; otherwise the app starts and the findings stay
    # available via GET /api/v1/operations/startup-validation.
    try:
        from app.core.startup_validation import run_startup_validation

        findings = run_startup_validation()
        app.state.startup_validation = findings
        app.state.startup_validation_strict = bool(settings.STARTUP_VALIDATION_STRICT)
    except RuntimeError as exc:
        logger.error("Startup validation failed: %s", exc)
        raise
    except Exception as exc:
        logger.warning("Startup validation skipped (non-fatal): %s", exc)
        app.state.startup_validation = []
        app.state.startup_validation_strict = False

    # Initialize database engine if configured
    if settings.DATABASE_URL:
        engine = create_async_engine()
        if engine is not None:
            app.state.db_engine = engine
            from app.db import database as db_module

            db_module._engine = engine  # Set module-level singleton
            logger.info("Database engine initialized")

            # ── Automatic Recovery Check ────────────────────────────
            # On startup, scan for any runs that were left in a non-terminal
            # state from the previous session. Mark stale ones as FAILED
            # and log recoverable ones for the user.
            try:
                from app.workflows.orchestration import OrchestrationWorkflow

                workflow = OrchestrationWorkflow()
                recovery_result = await workflow.check_recovery()

                if recovery_result.get("recovery_supported"):
                    stale = recovery_result.get("marked_stale", 0)
                    recoverable = recovery_result.get("recoverable_found", 0)
                    if stale > 0 or recoverable > 0:
                        logger.info(
                            "Recovery: %d stale run(s) marked FAILED, "
                            "%d recoverable run(s) found",
                            stale, recoverable,
                        )
                    else:
                        logger.info("Recovery: no stale or recoverable runs found")
                else:
                    logger.info("Recovery: using InMemoryRunStore — no recovery needed")
            except Exception as exc:
                logger.warning("Startup recovery check failed (non-critical): %s", exc)
    else:
        logger.info("No DATABASE_URL configured — using InMemoryRunStore")

    # ── Phase 20B: background operational loops ──────────────────
    # Provider health probes (outage detection + recovery observation) and
    # periodic provider-metric persistence. Both stop on shutdown.
    probe_loop = None
    metrics_loop = None
    try:
        from app.services.provider_probe import get_provider_probe

        probe_loop = get_provider_probe()
        probe_loop.start()
    except Exception as exc:
        logger.warning("Provider health probe loop not started: %s", exc)
    try:
        from app.services.provider_metrics_persistence import (
            get_provider_metrics_persistence,
        )

        metrics_loop = get_provider_metrics_persistence()
        metrics_loop.start()
    except Exception as exc:
        logger.warning("Provider metrics persistence loop not started: %s", exc)

    yield

    # ── Phase 20B: stop background loops ─────────────────────────
    if probe_loop is not None:
        try:
            await probe_loop.stop()
        except Exception as exc:
            logger.debug("Provider probe loop stop (non-critical): %s", exc)
    if metrics_loop is not None:
        try:
            await metrics_loop.stop()
        except Exception as exc:
            logger.debug("Provider metrics loop stop (non-critical): %s", exc)

    # Shutdown: dispose database engine
    if hasattr(app.state, "db_engine"):
        dispose_engine(app.state.db_engine)
        logger.info("Database engine disposed")

    # Shutdown: close WebSocket connections
    try:
        from app.services.ws_manager import ws_manager

        await ws_manager.close_all()
    except Exception as exc:
        logger.debug("WebSocket shutdown (non-critical): %s", exc)


app = FastAPI(
    title=settings.APP_NAME,
    version=__version__,
    description=settings.APP_DESCRIPTION,
    lifespan=lifespan,
)

# ── Middleware ───────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Phase 20B middleware ────────────────────────────────────────
# Correlation IDs for structured logging + request-size limits.
from app.core.middleware import CorrelationIdMiddleware, RequestSizeLimitMiddleware

app.add_middleware(CorrelationIdMiddleware)
app.add_middleware(RequestSizeLimitMiddleware, max_bytes=settings.MAX_REQUEST_BODY_BYTES)


# ── Global exception handler ────────────────────────────────────


@app.exception_handler(DevPilotError)
async def devpilot_error_handler(request: Request, exc: DevPilotError) -> JSONResponse:
    """Return a structured error response for domain exceptions."""
    logger.error("Unhandled DevPilotError: %s", exc)
    return JSONResponse(
        status_code=400,
        content={
            "success": False,
            "error": exc.__class__.__name__,
            "message": str(exc),
        },
    )


# ── Routes ──────────────────────────────────────────────────────

app.include_router(health_router)
app.include_router(repos_router)
app.include_router(github_router)

# Phase 4: Planning
from app.api.v1.planning import router as planning_router

app.include_router(planning_router)

# Phase 5: Code Intelligence (RAG)
from app.api.v1.code_intelligence import router as code_intel_router

app.include_router(code_intel_router)

# Phase 6: Coding Agent + Safe Patch Engine
from app.api.v1.coding import router as coding_router

app.include_router(coding_router)

# Phase 7: Test Agent + Controlled Execution Engine
from app.api.v1.testing import router as testing_router

app.include_router(testing_router)

# Phase 8: Fix Agent + Bounded Repair Loop
from app.api.v1.repair import router as repair_router

app.include_router(repair_router)

# Phase 9: Reviewer Agent + Quality Gate
from app.api.v1.review import router as review_router

app.include_router(review_router)

# Phase 10: End-to-End Multi-Agent Orchestration
from app.api.v1.orchestration import router as orchestration_router

app.include_router(orchestration_router)

# Phase 11: WebSocket for real-time run updates
from app.api.v1.ws import router as ws_router

app.include_router(ws_router)

# Phase 12: Advanced Code Intelligence + Semantic Repository Graph
from app.api.v1.code_intelligence_v2 import router as code_intel_v2_router

app.include_router(code_intel_v2_router)

# Phase 13: Context Engineering + Repository Memory
from app.api.v1.context import router as context_router

app.include_router(context_router)

# Phase 15: Repository Memory browsing & invalidation
from app.api.v1.memory import router as memory_router
from app.api.v1.collaboration import router as collaboration_router

app.include_router(memory_router)
app.include_router(collaboration_router)

# Phase 16: Autonomous execution
from app.api.v1.autonomy import router as autonomy_router

app.include_router(autonomy_router)

# Phase 17: Evidence consensus / contradictions / engineering notebook.
from app.api.v1.reasoning import router as reasoning_router

app.include_router(reasoning_router)

# Phase 18: Engineering Knowledge Graph
from app.api.v1.engineering_graph import router as engineering_graph_router

app.include_router(engineering_graph_router)

# Phase 19: Durability report (serves scripts/durability_report.py JSON)
from app.api.v1.durability import router as durability_router

app.include_router(durability_router)

# Phase 19B: Multi-Provider Failover & Reliability Platform
from app.api.v1.providers import router as providers_router

app.include_router(providers_router)

# Phase 20B: Operations (subsystem status, runtime metrics, startup validation)
from app.api.v1.operations import router as operations_router

app.include_router(operations_router)
