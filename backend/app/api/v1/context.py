"""
Phase 13 API endpoints for Context Engineering and diagnostics.

Follows existing API conventions from Phases 1-12.
All context builds are bounded by token budget and result limits.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query

from app.models.base import Response

router = APIRouter(
    prefix="/api/v1/context",
    tags=["context"],
)

# Global ContextEngine instance (created on demand)
_engine: Optional[Any] = None


def _get_engine() -> Any:
    """Get or create the global ContextEngine instance."""
    global _engine
    if _engine is None:
        from app.services.context_engine import ContextEngine
        _engine = ContextEngine()
    return _engine


@router.post("/build", response_model=Response)
async def build_context(
    task: str = Query(..., description="Task description", min_length=1, max_length=5000),
    agent_type: str = Query("planner", description="Agent type: planner, coding, test, repair, reviewer"),
    repository_path: Optional[str] = Query(None, description="Repository path"),
    symbol_names: Optional[str] = Query(None, description="Comma-separated symbol names"),
    file_paths: Optional[str] = Query(None, description="Comma-separated file paths"),
    plan_text: Optional[str] = Query(None, description="Implementation plan text"),
    requirements_text: Optional[str] = Query(None, description="Requirements text"),
    run_id: Optional[str] = Query(None, description="Run ID for historical context"),
) -> Response:
    """Build agent-specific context using the ContextEngine.

    Assembles context from Phase 12 semantic graph, Phase 11 run history,
    Phase 13 repository memory, and current run evidence.

    Args:
        task: The task description.
        agent_type: Which agent context to build for.
        repository_path: Repository path for graph/memory context.
        symbol_names: Comma-separated symbol names to focus on.
        file_paths: Comma-separated file paths to include.
        plan_text: Implementation plan text.
        requirements_text: Requirements text.
        run_id: Run ID for historical context.

    Returns:
        Response with AgentContext prompt section and metrics.
    """
    try:
        engine = _get_engine()

        # Parse comma-separated lists
        symbols = [s.strip() for s in symbol_names.split(",") if s.strip()] if symbol_names else None
        files = [f.strip() for f in file_paths.split(",") if f.strip()] if file_paths else None

        ctx = await engine.build_context(
            task=task,
            agent_type=agent_type,
            repository_path=repository_path,
            symbol_names=symbols,
            file_paths=files,
            plan_text=plan_text,
            requirements_text=requirements_text,
            run_id=run_id,
        )

        prompt_section = ctx.build_prompt_section()
        metrics = ctx.metrics.dict_summary()
        explanation = engine.explain_context(ctx)

        return Response(
            success=True,
            data={
                "agent_type": ctx.agent_type,
                "prompt_section": prompt_section,
                "metrics": metrics,
                "explanation": explanation,
            },
            message=(
                f"Context built for {agent_type}: "
                f"{metrics['candidates']} candidates → "
                f"{metrics['selected']} selected, "
                f"{metrics['estimated_tokens']['before']} → "
                f"{metrics['estimated_tokens']['after']} tokens"
            ),
        )
    except ValueError as exc:
        return Response(success=False, error="ValueError", message=str(exc))
    except Exception as exc:
        return Response(success=False, error="ContextError", message=str(exc))


@router.get("/explain", response_model=Response)
async def explain_context_diagnostic(
    task: str = Query(..., description="Task description", min_length=1),
    agent_type: str = Query("planner", description="Agent type"),
    repository_path: Optional[str] = Query(None, description="Repository path"),
    symbol_names: Optional[str] = Query(None, description="Comma-separated symbol names"),
    include_prompt: bool = Query(False, description="Include the full prompt section"),
) -> Response:
    """Get a diagnostic explanation of context selection.

    Shows why specific context items were selected, their provenance,
    ranking, deduplication statistics, and token budget usage.

    This is a diagnostic tool — no chain-of-thought, only evidence.
    """
    try:
        engine = _get_engine()

        symbols = [s.strip() for s in symbol_names.split(",") if s.strip()] if symbol_names else None

        ctx = await engine.build_context(
            task=task,
            agent_type=agent_type,
            repository_path=repository_path,
            symbol_names=symbols,
        )

        explanation = engine.explain_context(ctx)
        metrics = ctx.metrics.dict_summary()

        data: Dict[str, Any] = {
            "agent_type": agent_type,
            "metrics": metrics,
            "explanation": explanation,
        }

        if include_prompt:
            data["prompt_section"] = ctx.build_prompt_section()

        return Response(
            success=True,
            data=data,
            message=f"Context explanation for {agent_type}",
        )
    except ValueError as exc:
        return Response(success=False, error="ValueError", message=str(exc))
    except Exception as exc:
        return Response(success=False, error="ContextError", message=str(exc))
