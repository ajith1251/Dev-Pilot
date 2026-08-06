"""
Orchestration API — Phase 10/11 endpoints.

POST   /api/v1/runs                        — Create and execute a run
GET    /api/v1/runs                        — List runs
GET    /api/v1/runs/{run_id}               — Get run details
POST   /api/v1/runs/{run_id}/cancel        — Request cancellation
GET    /api/v1/runs/{run_id}/events        — Get run events
POST   /api/v1/runs/{run_id}/resume        — Resume a run (Phase 11)
GET    /api/v1/orchestration/capabilities  — List capabilities
POST   /api/v1/orchestration/recovery      — Check for recoverable runs (Phase 11)
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException

from app.models.orchestration import (
    RepositoryPatchInput,
    RepositorySpec,
    RunSourceType,
)
from app.workflows.orchestration import OrchestrationWorkflow

router = APIRouter(prefix="/api/v1", tags=["orchestration"])

workflow = OrchestrationWorkflow()


@router.post("/runs")
async def create_run(body: Dict[str, Any]) -> Dict[str, Any]:
    """Create and execute a DevPilot run.

    Accepts a user task or GitHub issue as input.
    Executes the full pipeline and returns the final result.
    """
    try:
        source_type = body.get("source", "user_task")
        title = body.get("title", "")
        description = body.get("description", "")
        repository = body.get("repository", "")

        # Phase 20: optional auxiliary repositories materialized via the org
        # graph. Validated here so malformed specs fail fast with a 400 rather
        # than failing mid-run (the primary repo is `repository`).
        repositories = None
        raw_repos = body.get("repositories")
        if raw_repos:
            if not isinstance(raw_repos, list):
                raise HTTPException(status_code=400, detail="repositories must be a list")
            try:
                repositories = [RepositorySpec.model_validate(r) for r in raw_repos]
            except Exception as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"invalid repositories spec: {exc}",
                ) from exc

        # Phase 20A4: optional per-repository patch inputs, each validated +
        # applied against its OWN checkout only (repository isolation).
        repo_patches = None
        raw_patches = body.get("repo_patches")
        if raw_patches:
            if not isinstance(raw_patches, list):
                raise HTTPException(status_code=400, detail="repo_patches must be a list")
            try:
                repo_patches = [
                    RepositoryPatchInput.model_validate(r) for r in raw_patches
                ]
            except Exception as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"invalid repo_patches spec: {exc}",
                ) from exc

        # Phase 20A6: advisory run-creation metadata (acceptance criteria +
        # execution budget) recorded on the source and surfaced on the
        # dashboard. Non-list / non-dict values are rejected fast.
        acceptance_criteria = body.get("acceptance_criteria")
        if acceptance_criteria is not None and not isinstance(acceptance_criteria, list):
            raise HTTPException(status_code=400, detail="acceptance_criteria must be a list of strings")
        execution_budget = body.get("execution_budget")
        if execution_budget is not None and not isinstance(execution_budget, dict):
            raise HTTPException(status_code=400, detail="execution_budget must be an object")

        if source_type == "github_issue":
            issue_number = body.get("issue_number")
            if not issue_number:
                raise HTTPException(status_code=400, detail="issue_number required for github_issue")
            result = await workflow.run_github_issue(
                repo_url=repository,
                issue_number=issue_number,
                title=title,
                description=description,
                repositories=repositories,
                repo_patches=repo_patches,
                acceptance_criteria=acceptance_criteria,
                execution_budget=execution_budget,
            )
        else:
            result = await workflow.run_user_task(
                title=title,
                description=description,
                repository_path=repository or None,
                workspace_root=body.get("workspace_root"),
                repositories=repositories,
                repo_patches=repo_patches,
                acceptance_criteria=acceptance_criteria,
                execution_budget=execution_budget,
            )

        return {"success": True, "data": _sanitize_result(result, org_service=workflow.organization_graph())}

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/runs")
async def list_runs(
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    sort_by: str = "newest",
    created_after: Optional[str] = None,
    created_before: Optional[str] = None,
) -> Dict[str, Any]:
    """List DevPilot runs with optional filtering, sorting, and date range.

    Sort options:
      - "newest" (default) — created_at DESC
      - "oldest" — created_at ASC
      - "duration" — total_duration_ms DESC

    Date range filters (half-open interval [created_after, created_before)):
      - created_after: ISO datetime string (inclusive)
      - created_before: ISO datetime string (exclusive)

    Returns aggregate stats in `stats` key (always unfiltered) so the
    frontend stat cards show correct counts regardless of active filters.
    """
    try:
        # Batch all three queries into a single session
        runs, total_count, stats = await workflow.list_runs_with_stats(
            status=status, limit=limit, offset=offset, sort_by=sort_by,
            created_after=created_after, created_before=created_before,
        )
        # Note: total_count respects status + date filters (for accurate pagination)
        #       stats is unfiltered (for stat cards regardless of filter)
        return {
            "success": True,
            "data": [
                {
                    "run_id": r.run_id,
                    "status": r.status.value,
                    "source": r.source.source_type.value,
                    "title": r.source.title[:200],
                    "current_stage": r.current_stage.value,
                    "created_at": r.created_at,
                    "total_duration_ms": r.total_duration_ms,
                    # Phase 20A6: participating repository count so the run
                    # list can badge multi-repository runs.
                    "repository_count": 1 + len(r.auxiliary_repositories or []),
                }
                for r in runs
            ],
            "count": len(runs),
            "total_count": total_count,
            "stats": stats,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> Dict[str, Any]:
    """Get detailed information about a specific run."""
    try:
        run = await workflow.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
        return {"success": True, "data": _sanitize_run(run, org_service=workflow.organization_graph())}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str) -> Dict[str, Any]:
    """Request cancellation of a running run."""
    try:
        success = await workflow.request_cancellation(run_id)
        if not success:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot cancel run {run_id}: not found or already terminal",
            )
        return {"success": True, "message": f"Cancellation requested for {run_id}"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/runs/{run_id}/resume")
async def resume_run(run_id: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Resume a previously interrupted run (Phase 11).

    Requires a non-terminal run with persisted state in PostgreSQL.
    Returns the resumed run result.
    """
    try:
        workspace_root = body.get("workspace_root") if body else None
        result = await workflow.resume_run(
            run_id=run_id,
            workspace_root=workspace_root,
        )
        if not result:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot resume run {run_id}: not found, terminal, or cancellation requested",
            )
        return {"success": True, "data": _sanitize_result(result)}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/runs/{run_id}/events")
async def get_run_events(run_id: str) -> Dict[str, Any]:
    """Get sanitized events for a run."""
    try:
        events = await workflow.get_events(run_id)
        return {"success": True, "data": events, "count": len(events)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/orchestration/capabilities")
async def get_capabilities() -> Dict[str, Any]:
    """List Phase 10/11 orchestration capabilities."""
    try:
        caps = workflow.get_capabilities()
        return {"success": True, "data": caps}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/orchestration/recovery")
async def check_recovery() -> Dict[str, Any]:
    """Check for recoverable runs after backend restart (Phase 11).

    Scans PostgreSQL for non-terminal runs and marks old ones as stale.
    Returns diagnostics about recoverable and stale runs.
    """
    try:
        result = await workflow.check_recovery()
        return {"success": True, "data": result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Sanitizers ──────────────────────────────────────────────────


def _sanitize_run(run: Any, org_service: Any = None) -> Dict[str, Any]:
    """Sanitize a DevPilotRun for API response."""
    from app.services.run_dashboard import (
        build_organization_summary,
        build_repository_view,
    )

    return {
        "run_id": run.run_id,
        "status": run.status.value,
        "source": {
            "source_type": run.source.source_type.value,
            "title": run.source.title[:200],
            "description": run.source.description[:500] if run.source.description else "",
            "repository_path": run.source.repository_path,
            "issue_number": run.source.issue_number,
            # Phase 20A6: acceptance criteria + execution budgets (advisory
            # metadata the run was created with).
            "acceptance_criteria": getattr(run.source, "acceptance_criteria", None) or [],
            "execution_budget": getattr(run.source, "execution_budget", None) or {},
        },
        "current_stage": run.current_stage.value,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "stage_results": [
            {
                "stage": s.stage.value,
                "status": s.status.value,
                "started_at": s.started_at,
                "finished_at": s.finished_at,
                "duration_ms": s.duration_ms,
                "error": s.error,
            }
            for s in run.stage_results
        ],
        "failure": {
            "stage": run.failure.stage.value,
            "code": run.failure.code.value,
            "message": run.failure.message[:500],
        } if run.failure else None,
        "warnings": run.warnings[:10],
        "total_duration_ms": run.total_duration_ms,
        "cancellation_requested": run.cancellation_requested,
        "auxiliary_repositories": run.auxiliary_repositories,
        "repo_validation": [r.summary() for r in run.repo_patches],
        # Phase 20A6: repository-aware dashboard surface.
        "repositories": build_repository_view(run, org_service=org_service),
        "organization_summary": build_organization_summary(run, org_service=org_service),
    }


def _sanitize_result(result: Any, org_service: Any = None) -> Dict[str, Any]:
    """Sanitize a DevPilotRunResult for API response."""
    from app.services.run_dashboard import (
        build_organization_summary,
        build_repository_view,
    )

    return {
        "run_id": result.run_id,
        "status": result.status.value,
        "source": {
            "source_type": result.source.source_type.value,
            "title": result.source.title[:200],
        },
        "repository": result.repository,
        "auxiliary_repositories": result.auxiliary_repositories,
        "stages": result.stages,
        "events": result.events[:50],
        # Phase 20A4: per-repository validation/application outcomes.
        "repo_validation": [r.summary() for r in result.repo_validation],
        # Phase 20A6: repository-aware dashboard surface.
        "repositories": build_repository_view(result, org_service=org_service),
        "organization_summary": build_organization_summary(result, org_service=org_service),
        "failure": {
            "stage": result.failure.stage.value,
            "code": result.failure.code.value,
            "message": result.failure.message[:500],
        } if result.failure else None,
        "warnings": result.warnings[:10],
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "duration_seconds": result.duration_seconds,
    }
