"""
Phase 20A6 — Multi-Repository Dashboard view builder.

Derives a repository-aware status view + an organization-level summary from
the EXISTING run data and organization graph. No backend redesign: the
orchestrator, autonomy, EKG and org graph stay exactly as they are — this
module only re-shapes what they already produce into a per-repository
dashboard view for the frontend, CLI, and WebSocket payloads.

The builders accept both ``DevPilotRun`` (full run state, used by
``GET /api/v1/runs/{id}`` + WebSocket broadcasts) and ``DevPilotRunResult``
(final result, used by ``POST /api/v1/runs``) — accessors are duck-typed so
both shapes work without branching at the call sites.

Isolation guarantees (evidence-only):
- Only namespaces actually materialized for the run are surfaced (the primary
  checkout + the run's ``auxiliary_repositories``) — never the whole org.
- Hidden reasoning, secrets and provider credentials are never included; the
  view only carries sanitized, deterministic engineering evidence.
- Graph status is derived from the org graph's per-repository stats, which
  are scoped to the repository's own namespace.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# The six stages shown per repository on the cross-repository timeline.
REPOSITORY_STAGES = [
    "planning",
    "coding",
    "testing",
    "repair",
    "review",
    "quality_gate",
]

# Global pipeline stages each per-repo stage maps to.
_GLOBAL_STAGE = {
    "planning": "planning",
    "coding": "coding",
    "testing": "testing",
    "repair": "repairing",
    "review": "reviewing",
    "quality_gate": "quality_gate",
}

_REPO_ACTIVE_STAGES = frozenset(
    {"coding", "validating_patch", "applying_patch"}
)


# ── Duck-typed accessors (DevPilotRun vs DevPilotRunResult) ─────


def _iter_stage_results(run: Any) -> List[Any]:
    results = getattr(run, "stage_results", None)
    if results:
        return results
    return list(getattr(run, "stages", None) or [])


def _stage_value(sr: Any, attr: str) -> str:
    if isinstance(sr, dict):
        return str(sr.get(attr, ""))
    value = getattr(sr, attr, None)
    if value is None:
        return ""
    return getattr(value, "value", None) or str(value)


def _iter_repo_results(run: Any) -> List[Any]:
    results = getattr(run, "repo_patches", None)
    if results:
        return results
    return list(getattr(run, "repo_validation", None) or [])


def _iter_events(run: Any) -> List[Any]:
    return list(getattr(run, "events", None) or [])


def _event_type(evt: Any) -> str:
    if isinstance(evt, dict):
        return str(evt.get("event_type", ""))
    value = getattr(evt, "event_type", None)
    if value is None:
        return ""
    return getattr(value, "value", None) or str(value)


def _event_message(evt: Any) -> str:
    if isinstance(evt, dict):
        return str(evt.get("message", ""))
    return str(getattr(evt, "message", "") or "")


def _repo_result_value(result: Any, attr: str) -> Any:
    if result is None:
        return None
    if isinstance(result, dict):
        return result.get(attr)
    return getattr(result, attr, None)


# ── View building ────────────────────────────────────────────────


def _primary_repository_id(run: Any) -> str:
    """Stable id for the primary repository (mirrors the orchestrator)."""
    path = getattr(run, "repository_path", None)
    if not path:
        path = getattr(getattr(run, "source", None), "repository_path", None)
    if not path:
        path = getattr(run, "repository", None)  # DevPilotRunResult
    if path:
        return "repo-" + Path(str(path)).resolve().name
    return "primary"


def _primary_path(run: Any) -> str:
    path = getattr(run, "repository_path", None)
    if not path:
        path = getattr(getattr(run, "source", None), "repository_path", None)
    if not path:
        path = getattr(run, "repository", None)
    return str(path or "")


def _global_stage_status(run: Any, stage: str) -> str:
    """Status of a global pipeline stage from stage results."""
    for sr in _iter_stage_results(run):
        if _stage_value(sr, "stage") == stage:
            status = _stage_value(sr, "status")
            return status or "pending"
    return "pending"


def _repo_result_by_id(run: Any, repository_id: str) -> Optional[Any]:
    """Find the run's per-repo patch result for a repository (or None)."""
    for result in _iter_repo_results(run):
        if str(_repo_result_value(result, "repository_id") or "") == repository_id:
            return result
    return None


def _event_messages(run: Any, event_type: str, limit: int = 5) -> List[str]:
    """Sanitized, evidence-only event messages of a given type."""
    out: List[str] = []
    for evt in _iter_events(run):
        if _event_type(evt) == event_type:
            out.append(_event_message(evt)[:200])
            if len(out) >= limit:
                break
    return out


def _current_stage(run: Any) -> str:
    stage = getattr(run, "current_stage", None)
    if stage is None:
        # DevPilotRunResult has no live stage — it is complete.
        return "completed"
    return getattr(stage, "value", None) or str(stage)


def _graph_status_for(
    org_service: Optional[Any], repository_id: str
) -> Dict[str, Any]:
    """Per-repository EKG status from the org graph (bounded, evidence-only).

    Returns an empty payload when the org graph is unavailable — the card
    degrades gracefully (never fatal).
    """
    if org_service is None:
        return {"available": False}
    try:
        stats = org_service.repository_stats(repository_id)
    except Exception:
        return {"available": False}
    if stats is None:
        return {"available": True, "node_count": 0, "edge_count": 0, "run_count": 0}
    ns = stats.get("namespace")
    return {
        "available": True,
        "node_count": stats.get("node_count", 0),
        "edge_count": stats.get("edge_count", 0),
        "run_count": stats.get("run_count", 0),
        "namespace": {
            "repository_id": getattr(ns, "repository_id", repository_id),
            "organization_id": getattr(ns, "organization_id", "default"),
            "name": getattr(ns, "name", ""),
            "source_type": getattr(ns, "source_type", "local"),
        } if ns is not None else None,
        "outgoing_links": stats.get("outgoing_links", [])[:10],
        "incoming_links": stats.get("incoming_links", [])[:10],
    }


def _repo_entry(
    run: Any,
    repository_id: str,
    *,
    name: str,
    namespace: str,
    organization: str,
    path: str,
    source_type: str,
    is_primary: bool,
    ordering: int,
    org_service: Optional[Any],
) -> Dict[str, Any]:
    """Build one repository status card entry."""
    result = _repo_result_by_id(run, repository_id)
    current_stage = _current_stage(run)

    # Per-repo progress across the six timeline stages.
    progress: Dict[str, str] = {}
    for stage in REPOSITORY_STAGES:
        global_stage = _GLOBAL_STAGE[stage]
        if stage == "coding":
            # Coding is the only repository-local stage: each repo's patch
            # has its own validation/application outcome.
            if result is not None:
                if _repo_result_value(result, "application_status") == "applied":
                    progress["coding"] = "succeeded"
                elif _repo_result_value(result, "validation_status") == "rejected":
                    progress["coding"] = "failed"
                elif _repo_result_value(result, "validation_status") == "validated":
                    progress["coding"] = (
                        "running" if current_stage in _REPO_ACTIVE_STAGES else "succeeded"
                    )
                else:
                    progress["coding"] = (
                        "running" if current_stage in _REPO_ACTIVE_STAGES else "pending"
                    )
            elif is_primary:
                # Primary patch is the coding-agent output.
                if getattr(run, "patch_result", None) is not None:
                    progress["coding"] = "succeeded"
                elif current_stage in _REPO_ACTIVE_STAGES:
                    progress["coding"] = "running"
                else:
                    g = _global_stage_status(run, "coding")
                    progress["coding"] = "skipped" if g in ("succeeded", "skipped") else g
            else:
                g = _global_stage_status(run, "coding")
                progress["coding"] = "skipped" if g == "succeeded" else g
            continue
        status = _global_stage_status(run, global_stage)
        # Pass the raw global stage status through: the frontend treats
        # skipped stages as done for progress % but displays them distinctly.
        progress[stage] = status

    validation_status = (
        str(_repo_result_value(result, "validation_status") or "not_attempted")
        if result is not None else "not_attempted"
    )
    application_status = (
        str(_repo_result_value(result, "application_status") or "not_attempted")
        if result is not None else "not_attempted"
    )
    changed_files = list(_repo_result_value(result, "changed_files") or [])[:20] if result else []
    validation_errors = list(_repo_result_value(result, "validation_errors") or [])[:5] if result else []

    quality_gate = _global_stage_status(run, "quality_gate")
    run_status = getattr(run.status, "value", None) or str(getattr(run, "status", ""))

    return {
        "repository_id": repository_id[:64],
        "name": name[:120],
        "namespace": namespace[:64],
        "organization": organization[:64],
        "path": path[:200],
        "source_type": source_type,
        "is_primary": is_primary,
        "ordering": ordering,
        "current_stage": current_stage,
        "progress": progress,
        "validation_status": validation_status,
        "application_status": application_status,
        "changed_files": changed_files,
        "validation_errors": validation_errors,
        "quality_gate": quality_gate,
        "quality_gate_result": run_status,
        "graph": _graph_status_for(org_service, repository_id),
    }


def build_repository_view(
    run: Any,
    org_service: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Build the per-repository status view for a run.

    Entry 0 is always the primary repository; auxiliary repositories follow
    in their materialized order (stable ``ordering``).
    """
    view: List[Dict[str, Any]] = []

    primary_id = _primary_repository_id(run)
    primary_path = _primary_path(run)
    primary_name = Path(primary_path).name if primary_path else "primary"
    view.append(
        _repo_entry(
            run,
            primary_id,
            name=primary_name or "primary",
            namespace="primary",
            organization="default",
            path=primary_path,
            source_type="local",
            is_primary=True,
            ordering=0,
            org_service=org_service,
        )
    )

    for i, aux in enumerate(
        getattr(run, "auxiliary_repositories", None) or [], start=1
    ):
        if not isinstance(aux, dict):
            continue
        aux_id = str(aux.get("repository_id", "") or aux.get("namespace_id", ""))
        if not aux_id:
            continue
        view.append(
            _repo_entry(
                run,
                aux_id,
                name=str(aux.get("name", "") or aux_id)[:120],
                namespace=str(aux.get("namespace_id", "") or aux_id),
                organization=str(aux.get("organization_id", "") or "default"),
                path=str(aux.get("path", "") or ""),
                source_type=str(aux.get("source_type", "") or "local"),
                is_primary=False,
                ordering=i,
                org_service=org_service,
            )
        )
    return view


def build_organization_summary(
    run: Any,
    org_service: Optional[Any] = None,
) -> Dict[str, Any]:
    """Build the organization-level execution summary for a completed run.

    Reuses existing deterministic evidence: run status, stage results,
    per-repo patch outcomes, reasoning/collaboration events, and org graph
    statistics. Never includes hidden reasoning or credentials.
    """
    view = build_repository_view(run, org_service=org_service)
    participating = [
        {
            "repository_id": r["repository_id"],
            "name": r["name"],
            "is_primary": r["is_primary"],
            "status": "ok" if r["quality_gate_result"] not in ("rejected", "failed") else "failed",
        }
        for r in view
    ]
    successful = [
        r["repository_id"] for r in view
        if r["validation_status"] == "validated" and r["application_status"] == "applied"
    ]
    failed = [
        r["repository_id"] for r in view
        if r["validation_status"] == "rejected" or r["application_status"] == "rejected"
    ]

    # Only the PRIMARY checkout ever goes through the bounded repair loop
    # (auxiliary repositories are deterministic validate/apply only), so a
    # repair-attempting run can only ever "repair" the primary repository.
    # Count it only when the repair loop actually ran AND its patch applied.
    repaired: List[str] = []
    repair_result = getattr(run, "repair_result", None)
    repair_summary = getattr(run, "repair_summary", None)
    repair_attempts = 0
    if repair_result is not None:
        repair_attempts = int(getattr(repair_result, "attempts", 0) or 0)
    elif isinstance(repair_summary, dict):
        repair_attempts = int(repair_summary.get("attempts", 0) or 0)
    if repair_attempts > 0:
        primary_entry = view[0]
        if primary_entry["application_status"] == "applied":
            repaired = [primary_entry["repository_id"]]

    started = getattr(run, "started_at", None)
    finished = getattr(run, "finished_at", None)
    duration_seconds: Optional[float] = None
    if started and finished:
        try:
            s = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
            f = datetime.fromisoformat(str(finished).replace("Z", "+00:00"))
            duration_seconds = round(max(0.0, (f - s).total_seconds()), 2)
        except Exception:
            duration_seconds = None

    decisions = _event_messages(run, "decision_recorded", limit=8)
    consensus_messages = _event_messages(run, "consensus_built", limit=5)
    contradiction_count = len(
        [e for e in _iter_events(run) if _event_type(e) == "conflict_detected"]
    )
    decision_count = len(
        [e for e in _iter_events(run) if _event_type(e) == "decision_recorded"]
    )

    org_stats = None
    if org_service is not None:
        try:
            s = org_service.stats()
            org_stats = {
                "repository_count": s.repository_count,
                "node_count": s.node_count,
                "edge_count": s.edge_count,
                "cross_edge_count": s.cross_edge_count,
                "version": getattr(s, "version", None) or org_service.current_version(),
            }
        except Exception:
            org_stats = None

    gate = getattr(run, "quality_gate_result", None) or getattr(run, "quality_gate", None)
    quality_gate = None
    if gate is not None:
        if isinstance(gate, dict):
            quality_gate = {
                "decision": gate.get("decision"),
                "score": gate.get("score"),
                "requirements_satisfied": gate.get("requirements_satisfied", 0),
                "requirements_unsatisfied": gate.get("requirements_unsatisfied", 0),
                "verification_status": gate.get("verification_status", ""),
            }
        else:
            decision = getattr(gate, "decision", None)
            quality_gate = {
                "decision": getattr(decision, "value", None) if decision is not None else None,
                "score": getattr(gate, "score", None),
                "requirements_satisfied": getattr(gate, "requirements_satisfied", 0),
                "requirements_unsatisfied": getattr(gate, "requirements_unsatisfied", 0),
                "verification_status": getattr(gate, "verification_status", ""),
            }

    return {
        "repository_count": len(view),
        "participating_repositories": participating[:50],
        "successful_repositories": successful[:50],
        "failed_repositories": failed[:50],
        "repaired_repositories": repaired[:50],
        "duration_seconds": duration_seconds,
        "engineering_decisions": {
            "count": decision_count,
            "recent": decisions,
        },
        "consensus_summary": {
            "count": len(consensus_messages),
            "contradictions": contradiction_count,
            "recent": consensus_messages,
        },
        "quality_status": getattr(run.status, "value", None) or str(getattr(run, "status", "")),
        "quality_gate": quality_gate,
        "graph": org_stats,
    }
