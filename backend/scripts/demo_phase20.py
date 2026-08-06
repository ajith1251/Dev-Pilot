"""
Phase 20 — Cross-Repository Autonomous Runs + Per-Repo Scope Enforcement demo.

Deterministic and offline (no paid LLM, no network; ``source=local`` only):

    A. Multi-repo run surface (A1)     — ``RunSource.repositories`` +
       ``RunSource.repo_patches`` serialize through the run spec.
    B. Auxiliary materialization (A2)  — two local repos acquired + linked
       into the organization graph; run records auxiliary namespaces.
    C. Cross-repo planning context (A3) — the planner gets org-scope evidence
       when the run is explicitly multi-repo (forced ORGANIZATION scope).
    D. Per-repo validation (A4)        — each repository's patch is validated
       against ITS OWN checkout; a clean aux patch validates + applies there.
    E. Cross-checkout rejection (A4)   — a patch claiming the primary repo
       under repo-B's workspace is rejected deterministically (blocking).
    F. End-to-end execute_run (A1+A4)  — primary + aux patches each apply to
       their own checkout; the aux patch never touches primary; the result
       aggregates ``repo_validation`` (primary + repo-b), run APPROVED.
    G. Per-repo EKG ingestion (A5)      — after the same execute_run, the aux
       repository's OWN namespace carries RUN/PATCH/FILE evidence, and the
       org graph links the run across namespaces via REFERENCES edges;
       ``RepositoryPatchResult.changed_files`` feeds the per-repo evidence.
    H. Cross-repo run creation (A6)     — the dashboard view built from a
       created run lists primary + auxiliary repositories in order.
    I. Execution tracking (A6)          — a mid-run dashboard view carries
       per-repository progress across the six timeline stages.
    J. Live WS payload (A6)             — the run-update broadcast payload
       shape carries ``repositories`` + ``organization_summary``.
    K. Organization summary (A6)        — a completed run yields an
       organization-level execution summary (repositories, duration,
       successful/failed/repaired, decisions, consensus, quality).
    L. EKG navigation (A6)              — each repository card's graph block
       resolves to its OWN namespace stats (navigable link target).
    M. Restart recovery (A6)            — re-loading the run from the store
       (simulating a backend restart) rebuilds the identical dashboard view.

Usage:
    python scripts/demo_phase20.py          # in-memory (deterministic)
    python scripts/demo_phase20.py --pg     # PostgreSQL persistence
    python scripts/demo_phase20.py --json   # JSON summary output
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _is_pg_configured() -> bool:
    from app.config import settings

    return bool(settings.DATABASE_URL or settings.TEST_DATABASE_URL)


def _db_url() -> str:
    from app.config import settings

    return settings.DATABASE_URL or settings.TEST_DATABASE_URL or ""


def _write_local_repo(root, rid: str, files: dict) -> str:
    """Create a tiny on-disk repository for a deterministic demo."""
    d = Path(root) / rid
    d.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(content)
    return str(d)


def _reqs():
    from app.models.issues import (
        ImplementationPlan,
        ImplementationStep,
        Requirement,
        StructuredRequirements,
    )

    return (
        StructuredRequirements(
            objective="Multi-repo task",
            requirements=[Requirement(id="REQ-001", description="T")],
        ),
        ImplementationPlan(
            summary="T", objective="T",
            steps=[ImplementationStep(id="STEP-001", title="S", description="D",
                                       affected_areas=["s"])],
            test_strategy="T",
        ),
    )


def _patch(patch_id: str, path: str, content: str, repository_id: str | None = None):
    from app.models.coding import FileChange, FileOperation, PatchSet

    return PatchSet(
        patch_id=patch_id,
        changes=[FileChange(
            change_id=f"{patch_id}-C1", operation=FileOperation.CREATE,
            path=path, new_content=content,
        )],
        repository_id=repository_id,
    )


def _run_source(primary: str, aux: str, bad_repo_id: str | None = None):
    """RunSource for a primary + one aux repo, with one per-repo patch.

    ``bad_repo_id`` optionally claims the WRONG repository so demo E can show
    the deterministic cross-checkout rejection.
    """
    from app.models.orchestration import (
        RepositoryPatchInput,
        RepositorySpec,
        RunSource,
        RunSourceType,
    )

    claimed = bad_repo_id or "repo-b"
    return RunSource(
        source_type=RunSourceType.USER_TASK,
        title="Multi-repo task",
        repository_path=primary,
        repositories=[RepositorySpec(repository_id="repo-b", path=aux)],
        repo_patches=[
            RepositoryPatchInput(
                repository_id="repo-b",
                repository_namespace="repo-b",
                workspace_path=aux,
                patch=_patch("paux", "feature.py", "f1\n", repository_id=claimed),
            )
        ],
    )


async def demo_a() -> dict:
    """A. Multi-repo run surface (Phase 20 A1)."""
    from app.models.orchestration import RunSource, RunSourceType

    src = _run_source(primary="/tmp/primary", aux="/tmp/repo-b")
    assert src.repositories is not None
    assert [r.repository_id for r in src.repositories] == ["repo-b"]
    assert src.repo_patches is not None
    assert src.repo_patches[0].workspace_path == "/tmp/repo-b"

    raw = src.model_dump(mode="json")
    revived = RunSource.model_validate(raw)
    assert [r.repository_id for r in revived.repositories] == ["repo-b"]
    assert revived.repo_patches[0].repository_id == "repo-b"

    # Backwards compatible: no repositories/repo_patches means today's behavior.
    plain = RunSource(source_type=RunSourceType.USER_TASK, title="T")
    assert plain.repositories is None
    assert plain.repo_patches is None

    return {
        "repositories": [r.repository_id for r in src.repositories],
        "repo_patches": [r.repository_id for r in src.repo_patches],
        "backwards_compatible": True,
    }


async def demo_b(tmp_root: str) -> dict:
    """B. Auxiliary materialization through the org graph (Phase 20 A2)."""
    from app.models.orchestration import (
        RepositorySpec,
        RunSource,
        RunSourceType,
        StageType,
    )
    from app.services.orchestration_service import OrchestrationService

    api = _write_local_repo(tmp_root, "demo-b-api", {"src/api.py": "def get(): ..."})
    web = _write_local_repo(
        tmp_root, "demo-b-web", {"src/web.py": "import api", "index.html": "<html/>"}
    )
    specs = [
        RepositorySpec(repository_id="demo-b-api", path=api),
        RepositorySpec(
            repository_id="demo-b-web",
            path=web,
            relationships=[{
                "target_repository_id": "demo-b-api",
                "relationship": "imports_package",
                "weight": 0.9,
            }],
        ),
    ]
    orch = OrchestrationService()
    source = RunSource(
        source_type=RunSourceType.USER_TASK,
        title="Multi-repo",
        repository_path="/primary",
        repositories=specs,
    )
    run = await orch.create_run(source)
    run.current_stage = StageType.ACQUIRING_REPOSITORY
    await orch._store.update(run)

    from app.services.organization_graph_service import OrganizationKnowledgeGraphService

    org = OrganizationKnowledgeGraphService(database_url=_db_url() or None)
    with patch.object(orch, "_get_org_graph", return_value=org):
        ok = await orch._materialize_auxiliary_repositories(run)

    assert ok is True
    assert [n["repository_id"] for n in run.auxiliary_repositories] == [
        "demo-b-api", "demo-b-web",
    ]
    assert {r.repository_id for r in org.repositories()} == {
        "demo-b-api", "demo-b-web",
    }
    assert len(org.cross_edges()) == 1
    await org.dispose()

    return {
        "repos_acquired": len(run.auxiliary_repositories),
        "cross_edges": len(org.cross_edges()),
        "scope": "organization",
        "persistence": "postgresql" if _is_pg_configured() else "in-memory",
    }


async def demo_c() -> dict:
    """C. Cross-repo planning context (Phase 20 A3)."""
    from app.models.engineering_graph import (
        EKNodeType,
        EKRelationshipType,
        MultiRepoAcquisitionSpec,
        QueryScope,
    )
    from app.services.context_engine import ContextEngine
    from app.services.organization_graph_service import OrganizationKnowledgeGraphService

    org = OrganizationKnowledgeGraphService(database_url=_db_url() or None)
    specs = [
        MultiRepoAcquisitionSpec(
            repository_id="demo-c-api", name="API", source="local", path="/tmp/demo-c-api",
        ),
        MultiRepoAcquisitionSpec(
            repository_id="demo-c-web", name="WEB", source="local", path="/tmp/demo-c-web",
        ),
    ]
    # Register the two namespaces directly (deterministic, no filesystem needed).
    for spec in specs:
        org.register_repository(
            repository_id=spec.repository_id,
            path=spec.path,
            source_type="local",
        )
        g = org.get_graph(spec.repository_id)
        g.add_node(
            EKNodeType.FILE,
            f"{spec.repository_id}-shared.py",
            source_ref=f"{spec.repository_id}:src/shared.py",
            source_type="repository",
            qualified_name=f"{spec.repository_id}/src/shared.py",
            payload={"kind": "file"},
            provenance={"repository_id": spec.repository_id, "source": "demo"},
        )
    org.link_repositories(
        "demo-c-web", "demo-c-api", EKRelationshipType.IMPORTS_PACKAGE
    )

    engine = ContextEngine(organization_graph=org)
    # A multi-repo run forces the ORGANIZATION scope for the planner.
    ctx = await engine.build_context(
        "explain the shared component",
        agent_type="planner",
        include_organization_context=True,
    )
    contents = " ".join(i.content for i in ctx.raw_items)
    assert "Organization knowledge graph" in contents
    assert "repo:" in contents

    # A single-repo run (no force) stays isolated.
    ctx_local = await engine.build_context(
        "explain the shared component",
        agent_type="planner",
    )
    assert not any(
        "Organization knowledge graph" in i.content for i in ctx_local.raw_items
    )
    await org.dispose()

    return {
        "planner_org_evidence": True,
        "single_repo_isolated": True,
        "scope": QueryScope.ORGANIZATION.value,
    }


async def _make_run(tmp_root: str, bad_repo_id: str | None = None):
    """Prepared run for stage-level demos D/E: current_stage=CODING."""
    from app.models.orchestration import StageType
    from app.services.orchestration_service import OrchestrationService

    primary = _write_local_repo(tmp_root, "primary", {"main.py": "p1\n"})
    aux = _write_local_repo(tmp_root, "repo-b", {"app.py": "b1\n"})
    orch = OrchestrationService()
    run = await orch.create_run(_run_source(primary, aux, bad_repo_id))
    run.current_stage = StageType.CODING
    run.repository_path = primary
    reqs, plan = _reqs()
    run.requirements = reqs
    run.plan = plan
    await orch._store.update(run)
    return orch, run, primary, aux


async def demo_d(tmp_root: str) -> dict:
    """D. Per-repo validation against the repo's OWN checkout (Phase 20 A4)."""
    from app.models.orchestration import EventType

    orch, run, primary, aux = await _make_run(tmp_root)
    ok = await orch._stage_patch_validation(run, primary)
    assert ok is True

    res = run.repo_patches[0]
    assert res.repository_id == "repo-b"
    assert res.validation_status == "validated"
    assert res.status == "ok"
    events = {e.event_type for e in run.events}
    assert EventType.REPOSITORY_PATCH_VALIDATED in events
    assert EventType.REPOSITORY_SCOPE_VIOLATION not in events

    return {
        "repository_id": res.repository_id,
        "validation_status": res.validation_status,
        "validated_against": res.workspace_path,
        "primary_untouched": True,
    }


async def demo_e(tmp_root: str) -> dict:
    """E. Cross-checkout rejection (Phase 20 A4) — blocking isolation."""
    from app.models.orchestration import EventType

    orch, run, primary, aux = await _make_run(tmp_root, bad_repo_id="repo-primary")
    ok = await orch._stage_patch_validation(run, primary)
    assert ok is False
    assert run.failure is not None

    res = run.repo_patches[0]
    assert res.validation_status == "rejected"
    assert res.application_status == "not_attempted"
    assert any("cross-repository" in e for e in res.validation_errors)
    events = {e.event_type for e in run.events}
    assert EventType.REPOSITORY_SCOPE_VIOLATION in events
    assert not Path(aux, "evil.py").exists()
    assert not Path(primary, "feature.py").exists()

    return {
        "repository_id": res.repository_id,
        "validation_status": res.validation_status,
        "reason": res.validation_errors[0][:80],
        "scope_violation_event": True,
    }


async def demo_f(tmp_root: str) -> dict:
    """F. End-to-end execute_run: primary + aux applied to own checkouts."""
    from app.models.coding import FileChange, FileOperation, PatchSet
    from app.models.orchestration import (
        EventType,
        RunStatus,
        StageType,
    )
    from app.services.orchestration_service import OrchestrationService

    primary = _write_local_repo(tmp_root, "primary", {"main.py": "p1\n"})
    aux = _write_local_repo(tmp_root, "repo-b", {"app.py": "b1\n"})
    source = _run_source(primary, aux)
    orch = OrchestrationService()

    run = await orch.create_run(source)
    # All upstream stages are pre-populated (guards skip them) and every stage
    # method calls _transition_to directly, so current_stage must be CODING.
    run.current_stage = StageType.CODING
    run.repository_profile = AsyncMock()
    reqs, plan = _reqs()
    run.requirements = reqs
    run.plan = plan
    run.retrieved_context = AsyncMock()
    run.patch_set = PatchSet(patch_id="primary", changes=[
        FileChange(
            change_id="primary-C1", operation=FileOperation.MODIFY,
            path="main.py", new_content="p2\n",
        ),
    ])
    await orch._store.update(run)

    async def _mock_stage(target: StageType):
        async def _fn(run, *args, **kwargs):
            run.current_stage = target
            return True
        return _fn

    async def _mock_approve():
        async def _fn(run, *args, **kwargs):
            run.current_stage = StageType.QUALITY_GATE
            run.status = RunStatus.APPROVED
            return True
        return _fn

    with patch.object(OrchestrationService, "_stage_testing",
                      new_callable=AsyncMock, side_effect=await _mock_stage(StageType.TESTING)), \
         patch.object(OrchestrationService, "_stage_review",
                      new_callable=AsyncMock, side_effect=await _mock_stage(StageType.REVIEWING)), \
         patch.object(OrchestrationService, "_stage_quality_gate",
                      new_callable=AsyncMock, side_effect=await _mock_approve()):
        result = await orch.execute_run(run.run_id, workspace_root=primary)

    assert result.status == RunStatus.APPROVED
    # Primary patch applied to the primary checkout.
    assert Path(primary, "main.py").read_text() == "p2\n"
    # Auxiliary patch applied to its OWN checkout.
    assert Path(aux, "feature.py").read_text() == "f1\n"
    # The auxiliary patch never touched the primary checkout.
    assert not Path(primary, "feature.py").exists()

    # repo_validation aggregated on the result (primary + aux).
    assert [r.repository_id for r in result.repo_validation] == [
        "repo-primary", "repo-b",
    ]
    by_id = {r.repository_id: r for r in result.repo_validation}
    assert by_id["repo-primary"].application_status == "applied"
    assert by_id["repo-b"].validation_status == "validated"
    assert by_id["repo-b"].application_status == "applied"

    fresh = await orch._store.get(run.run_id)
    events = {e.event_type for e in fresh.events}
    assert EventType.REPOSITORY_PATCH_VALIDATED in events
    assert EventType.REPOSITORY_SCOPE_VIOLATION not in events

    return {
        "run_status": result.status.value,
        "primary_file": Path(primary, "main.py").read_text().strip(),
        "aux_file": Path(aux, "feature.py").read_text().strip(),
        "primary_touched_by_aux": False,
        "repo_validation": [
            {
                "repository_id": r.repository_id,
                "validation": r.validation_status,
                "application": r.application_status,
            }
            for r in result.repo_validation
        ],
        "persistence": "postgresql" if _is_pg_configured() else "in-memory",
    }


async def demo_g(tmp_root: str) -> dict:
    """G. Per-repo EKG ingestion (Phase 20 A5)."""
    from app.models.coding import FileChange, FileOperation, PatchSet
    from app.models.engineering_graph import EKNodeType, EKRelationshipType
    from app.models.orchestration import RunStatus, StageType
    from app.services.orchestration_service import OrchestrationService

    # Re-runs share tmp_root, so prior demos may have left files behind —
    # start from clean checkouts so the CREATE patches validate deterministically.
    for rid in ("primary", "repo-b"):
        shutil.rmtree(Path(tmp_root) / rid, ignore_errors=True)
    primary = _write_local_repo(tmp_root, "primary", {"main.py": "p1\n"})
    aux = _write_local_repo(tmp_root, "repo-b", {"app.py": "b1\n"})
    orch = OrchestrationService()
    run = await orch.create_run(_run_source(primary, aux))

    run.current_stage = StageType.CODING
    run.repository_profile = AsyncMock()
    reqs, plan = _reqs()
    run.requirements = reqs
    run.plan = plan
    run.retrieved_context = AsyncMock()
    run.patch_set = PatchSet(patch_id="primary", changes=[
        FileChange(
            change_id="primary-C1", operation=FileOperation.MODIFY,
            path="main.py", new_content="p2\n",
        ),
    ])
    await orch._store.update(run)

    async def _mock_stage(target: StageType):
        async def _fn(run, *args, **kwargs):
            run.current_stage = target
            return True
        return _fn

    async def _mock_approve():
        async def _fn(run, *args, **kwargs):
            run.current_stage = StageType.QUALITY_GATE
            run.status = RunStatus.APPROVED
            return True
        return _fn

    with patch.object(OrchestrationService, "_stage_testing",
                      new_callable=AsyncMock, side_effect=await _mock_stage(StageType.TESTING)), \
         patch.object(OrchestrationService, "_stage_review",
                      new_callable=AsyncMock, side_effect=await _mock_stage(StageType.REVIEWING)), \
         patch.object(OrchestrationService, "_stage_quality_gate",
                      new_callable=AsyncMock, side_effect=await _mock_approve()):
        result = await orch.execute_run(run.run_id, workspace_root=primary)

    assert result.status == RunStatus.APPROVED
    assert Path(primary, "main.py").read_text() == "p2\n"
    assert Path(aux, "feature.py").read_text() == "f1\n"

    # A5: changed_files evidence populated during per-repo validation.
    by_id = {r.repository_id: r for r in result.repo_validation}
    assert by_id["repo-b"].changed_files == ["feature.py"]

    # A5: per-repo EKG ingestion — repo-b's OWN namespace carries the evidence.
    org = orch._get_org_graph()
    assert org is not None
    assert org.get_namespace("repo-b") is not None
    graph_b = org.get_graph("repo-b")
    assert graph_b is not None
    run_nodes = graph_b.find_nodes(
        node_type=EKNodeType.RUN, source_ref=run.run_id, repository_id="repo-b"
    )
    assert len(run_nodes) == 1
    patch_nodes = graph_b.find_nodes(
        node_type=EKNodeType.PATCH, source_ref=run.run_id, repository_id="repo-b"
    )
    assert len(patch_nodes) == 1
    assert patch_nodes[0].payload["files"] == ["feature.py"]
    assert patch_nodes[0].payload["validation_status"] == "validated"
    assert patch_nodes[0].payload["application_status"] == "applied"
    files = graph_b.find_nodes(node_type=EKNodeType.FILE, repository_id="repo-b")
    assert any(n.source_ref == "feature.py" for n in files)

    # A5: the org graph links the run across namespaces.
    run_node = org._org_graph._find_run_node(run.run_id, EKNodeType.RUN)
    assert run_node is not None
    assert any(
        e.relationship == EKRelationshipType.REFERENCES
        and e.target_id == "REPO::repo-b"
        for e in org._org_graph.get_edges(run_node.node_id)
    )

    return {
        "run_status": result.status.value,
        "repo_b_namespace_ingested": True,
        "repo_b_patch_files": patch_nodes[0].payload["files"],
        "cross_namespace_link": True,
        "changed_files_evidence": by_id["repo-b"].changed_files,
        "persistence": "postgresql" if _is_pg_configured() else "in-memory",
    }


# ── Phase 20A6: Multi-Repository Dashboard demos ────────────────


def _seed_aux_namespaces(run, aux: str) -> None:
    """Record materialized auxiliary namespaces on a run (mirrors A2 output).

    Demo helpers that build a dashboard view without a full execute_run set
    the same ``auxiliary_repositories`` payload the orchestrator records after
    ``_materialize_auxiliary_repositories``.
    """
    run.auxiliary_repositories = [
        {
            "repository_id": "repo-b",
            "namespace_id": "repo-b",
            "organization_id": "default",
            "name": "repo-b",
            "path": aux,
            "source_type": "local",
            "created_at": run.created_at or "",
        }
    ]


async def _approved_multi_repo_run(tmp_root: str):
    """Run a primary + aux execute_run to APPROVED, returning
    ``(orch, result, primary, aux)``. Shares the deterministic mock-stage
    machinery of demos F/G so the A6 demos build on the same evidence."""
    from app.models.coding import FileChange, FileOperation, PatchSet
    from app.models.orchestration import RunStatus, StageType
    from app.services.orchestration_service import OrchestrationService

    # Clean checkouts so CREATE patches validate deterministically.
    for rid in ("primary", "repo-b"):
        shutil.rmtree(Path(tmp_root) / rid, ignore_errors=True)
    primary = _write_local_repo(tmp_root, "primary", {"main.py": "p1\n"})
    aux = _write_local_repo(tmp_root, "repo-b", {"app.py": "b1\n"})
    orch = OrchestrationService()
    run = await orch.create_run(_run_source(primary, aux))

    run.current_stage = StageType.CODING
    run.repository_profile = AsyncMock()
    reqs, plan = _reqs()
    run.requirements = reqs
    run.plan = plan
    run.retrieved_context = AsyncMock()
    run.patch_set = PatchSet(patch_id="primary", changes=[
        FileChange(
            change_id="primary-C1", operation=FileOperation.MODIFY,
            path="main.py", new_content="p2\n",
        ),
    ])
    await orch._store.update(run)

    async def _mock_stage(target: StageType):
        async def _fn(run, *args, **kwargs):
            run.current_stage = target
            return True
        return _fn

    async def _mock_approve():
        async def _fn(run, *args, **kwargs):
            run.current_stage = StageType.QUALITY_GATE
            run.status = RunStatus.APPROVED
            return True
        return _fn

    with patch.object(OrchestrationService, "_stage_testing",
                      new_callable=AsyncMock, side_effect=await _mock_stage(StageType.TESTING)), \
         patch.object(OrchestrationService, "_stage_review",
                      new_callable=AsyncMock, side_effect=await _mock_stage(StageType.REVIEWING)), \
         patch.object(OrchestrationService, "_stage_quality_gate",
                      new_callable=AsyncMock, side_effect=await _mock_approve()):
        result = await orch.execute_run(run.run_id, workspace_root=primary)
    return orch, result, primary, aux


async def demo_h(tmp_root: str) -> dict:
    """H. Cross-repo run creation (Phase 20 A6) — dashboard view surface."""
    from app.models.orchestration import StageType
    from app.services.orchestration_service import OrchestrationService
    from app.services.run_dashboard import build_repository_view

    primary = _write_local_repo(tmp_root, "primary", {"main.py": "p1\n"})
    aux = _write_local_repo(tmp_root, "repo-b", {"app.py": "b1\n"})
    orch = OrchestrationService()
    run = await orch.create_run(_run_source(primary, aux))
    _seed_aux_namespaces(run, aux)
    run.current_stage = StageType.CODING
    await orch._store.update(run)

    view = build_repository_view(run, org_service=orch._get_org_graph())
    assert [r["repository_id"] for r in view] == ["repo-primary", "repo-b"]
    assert view[0]["is_primary"] is True
    assert view[1]["is_primary"] is False
    assert [r["ordering"] for r in view] == [0, 1]
    assert view[0]["organization"] == "default"

    return {
        "repositories": [r["repository_id"] for r in view],
        "ordering": [r["ordering"] for r in view],
        "primary": view[0]["repository_id"],
        "aux": view[1]["repository_id"],
    }


async def demo_i(tmp_root: str) -> dict:
    """I. Execution tracking across repositories (Phase 20 A6)."""
    from app.models.orchestration import StageType
    from app.services.orchestration_service import OrchestrationService
    from app.services.run_dashboard import (
        REPOSITORY_STAGES,
        build_repository_view,
    )

    primary = _write_local_repo(tmp_root, "primary", {"main.py": "p1\n"})
    aux = _write_local_repo(tmp_root, "repo-b", {"app.py": "b1\n"})
    orch = OrchestrationService()
    run = await orch.create_run(_run_source(primary, aux))
    _seed_aux_namespaces(run, aux)

    # Simulate a mid-run snapshot: coding in flight, testing pending.
    run.current_stage = StageType.CODING
    from app.models.orchestration import StageResult, StageStatus, StageType as ST
    run.stage_results = [
        StageResult(stage=ST.PLANNING, status=StageStatus.SUCCEEDED),
        StageResult(stage=ST.CODING, status=StageStatus.RUNNING),
    ]
    await orch._store.update(run)

    view = build_repository_view(run, org_service=orch._get_org_graph())
    per_repo = view[0]["progress"]
    assert list(per_repo.keys()) == REPOSITORY_STAGES
    assert per_repo["planning"] == "succeeded"
    assert per_repo["quality_gate"] == "pending"
    assert view[0]["current_stage"] == "coding"

    return {
        "stages": REPOSITORY_STAGES,
        "primary_progress": per_repo,
        "current_stage": view[0]["current_stage"],
    }


async def demo_j(tmp_root: str) -> dict:
    """J. Live repository progress updates (Phase 20 A6) — WS payload shape."""
    from app.models.orchestration import StageType
    from app.services.orchestration_service import OrchestrationService
    from app.services.run_dashboard import build_repository_view

    primary = _write_local_repo(tmp_root, "primary", {"main.py": "p1\n"})
    aux = _write_local_repo(tmp_root, "repo-b", {"app.py": "b1\n"})
    orch = OrchestrationService()
    run = await orch.create_run(_run_source(primary, aux))
    _seed_aux_namespaces(run, aux)
    run.current_stage = StageType.TESTING
    await orch._store.update(run)

    org = orch._get_org_graph()
    # The WebSocket broadcast builds the same repository view per update.
    payload = {
        "run_id": run.run_id,
        "status": run.status.value,
        "current_stage": run.current_stage.value,
        "repositories": build_repository_view(run, org_service=org),
    }
    assert len(payload["repositories"]) == 2
    assert payload["current_stage"] == "testing"
    # Each repository card carries live stage + progress for the client.
    assert "progress" in payload["repositories"][0]
    assert payload["repositories"][0]["progress"]["testing"] == "pending"

    return {
        "run_id": run.run_id,
        "broadcast_stage": payload["current_stage"],
        "repositories_in_payload": len(payload["repositories"]),
        "live_progress": True,
    }


async def demo_k(tmp_root: str) -> dict:
    """K. Organization-level execution summary (Phase 20 A6)."""
    from app.services.run_dashboard import build_organization_summary

    orch, result, primary, aux = await _approved_multi_repo_run(tmp_root)
    summary = build_organization_summary(result, org_service=orch._get_org_graph())

    assert summary["repository_count"] == 2
    assert {p["repository_id"] for p in summary["participating_repositories"]} == {
        "repo-primary", "repo-b",
    }
    # Both checkouts carried a validated + applied patch (primary patch on the
    # primary checkout, aux patch on its own checkout).
    assert summary["successful_repositories"] == ["repo-primary", "repo-b"]
    assert summary["failed_repositories"] == []
    assert summary["quality_status"] == "approved"
    assert summary["engineering_decisions"]["count"] >= 0
    assert "consensus_summary" in summary
    assert "graph" in summary

    return {
        "repository_count": summary["repository_count"],
        "participating": summary["participating_repositories"],
        "successful": summary["successful_repositories"],
        "failed": summary["failed_repositories"],
        "duration_seconds": summary["duration_seconds"],
        "quality_status": summary["quality_status"],
    }


async def demo_l(tmp_root: str) -> dict:
    """L. Navigate from repository card to EKG (Phase 20 A6)."""
    from app.services.run_dashboard import build_repository_view

    orch, result, primary, aux = await _approved_multi_repo_run(tmp_root)
    org = orch._get_org_graph()
    view = build_repository_view(result, org_service=org)

    # Every repository card exposes a graph block. Repositories with a
    # registered namespace (the aux repo, materialized + ingested by the
    # completed run) resolve to THEIR OWN namespace — the id the org-graph
    # routes render. The primary checkout is a plain workspace, so its block
    # degrades gracefully without a namespace.
    for entry in view:
        g = entry["graph"]
        assert g.get("available") is True
        if entry["repository_id"] == "repo-b":
            ns = g.get("namespace") or {}
            assert ns.get("repository_id") == "repo-b"
        else:
            assert g.get("namespace") is None or g.get("node_count", 0) == 0

    # The repo-b card resolves to the namespace with the ingested evidence.
    stats = org.repository_stats("repo-b") or {}
    assert stats.get("run_count", 0) >= 1

    return {
        "cards": [
            {
                "repository_id": e["repository_id"],
                "namespace": (e["graph"].get("namespace") or {}).get("repository_id"),
                "node_count": e["graph"].get("node_count"),
                "run_count": e["graph"].get("run_count"),
            }
            for e in view
        ],
        "repo_b_namespace_runs": stats.get("run_count"),
        "navigable": True,
    }


async def demo_m(tmp_root: str) -> dict:
    """M. Restart recovery preserving dashboard state (Phase 20 A6)."""
    from app.services.orchestration_service import OrchestrationService
    from app.services.run_dashboard import (
        build_organization_summary,
        build_repository_view,
    )

    orch, result, primary, aux = await _approved_multi_repo_run(tmp_root)
    org = orch._get_org_graph()
    before = build_repository_view(result, org_service=org)
    before_summary = build_organization_summary(result, org_service=org)

    # Simulate a backend restart: re-load the persisted run from the store
    # and rebuild the dashboard view purely from persisted state. With
    # PostgreSQL configured a FRESH store instance proves the view survives
    # a real process restart (the completed run is persisted first); in
    # in-memory mode the store instance is shared (the restart boundary for
    # that mode).
    if _is_pg_configured():
        from app.services.postgres_run_store import PostgresRunStore

        store2 = PostgresRunStore()
        completed = await orch._store.get(result.run_id)
        assert completed is not None
        # Strip non-serializable demo mocks before persisting (the real
        # orchestrator never stores live agent objects).
        completed.repository_profile = None
        completed.retrieved_context = None
        await store2.create(completed)
    else:
        store2 = orch._store
    orch2 = OrchestrationService(run_store=store2)
    reloaded = await orch2._store.get(result.run_id)
    assert reloaded is not None
    org2 = orch2._get_org_graph()
    after = build_repository_view(reloaded, org_service=org2)
    after_summary = build_organization_summary(reloaded, org_service=org2)

    assert [r["repository_id"] for r in after] == [
        r["repository_id"] for r in before
    ]
    assert [r["progress"] for r in after] == [r["progress"] for r in before]
    assert after_summary["repository_count"] == before_summary["repository_count"]
    assert after_summary["quality_status"] == "approved"

    return {
        "reloaded_run": reloaded.run_id,
        "repositories_identical": True,
        "progress_identical": True,
        "summary_rebuilt": after_summary["repository_count"],
        "persistence": "postgresql" if _is_pg_configured() else "in-memory",
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 20 cross-repo + scope demo")
    parser.add_argument("--pg", action="store_true",
                        help="Run against PostgreSQL when configured")
    parser.add_argument("--json", action="store_true",
                        help="Output JSON summary")
    args = parser.parse_args()

    # JSON mode: keep stdout pure JSON. Handlers are created lazily at the
    # first app import (after this point), so re-pointing existing handlers
    # is not enough — redirect the file descriptor itself and restore it
    # right before the JSON payload is printed.
    if args.json:
        import os

        saved_stdout_fd = os.dup(1)
        os.dup2(2, 1)  # stdout → stderr for the whole demo run
    else:
        saved_stdout_fd = None

    tmp_root = tempfile.mkdtemp(prefix="p20-demo-")
    try:
        results = {}
        for name, fn, use_tmp in [
            ("A_multi_repo_surface", demo_a, False),
            ("B_aux_materialization", demo_b, True),
            ("C_org_planning_context", demo_c, False),
            ("D_per_repo_validation", demo_d, True),
            ("E_cross_checkout_rejection", demo_e, True),
            ("F_execute_run_end_to_end", demo_f, True),
            ("G_per_repo_ekg_ingestion", demo_g, True),
            ("H_cross_repo_run_creation", demo_h, True),
            ("I_execution_tracking", demo_i, True),
            ("J_live_ws_payload", demo_j, True),
            ("K_organization_summary", demo_k, True),
            ("L_ekg_navigation", demo_l, True),
            ("M_restart_recovery", demo_m, True),
        ]:
            try:
                results[name] = await fn(tmp_root) if use_tmp else await fn()
                results[name]["PASS"] = True
            except Exception as exc:  # pragma: no cover
                results[name] = {"PASS": False, "error": str(exc)}
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    pg = _is_pg_configured()

    if args.json:
        # Restore stdout (all demo logging went to stderr) so the ONLY
        # thing on stdout is the JSON payload.
        if saved_stdout_fd is not None:
            os.dup2(saved_stdout_fd, 1)
            os.close(saved_stdout_fd)
        print(json.dumps({
            "phase": "20",
            "persistence": "postgresql" if pg else "in-memory",
            "demonstrations": results,
        }, indent=2, default=str))
        return

    print(f"\n{'='*64}")
    print("  Phase 20 - Cross-Repository Runs + Per-Repo Scope Enforcement")
    print(f"  Persistence: {'PostgreSQL' if pg else 'In-memory'}")
    print(f"{'='*64}")

    labels = {
        "A_multi_repo_surface": "A. Multi-repo run surface (RunSource.repositories + repo_patches)",
        "B_aux_materialization": "B. Auxiliary materialization (org-graph acquisition + linking)",
        "C_org_planning_context": "C. Cross-repo planning context (forced ORGANIZATION scope)",
        "D_per_repo_validation": "D. Per-repo validation against its OWN checkout",
        "E_cross_checkout_rejection": "E. Cross-checkout rejection (blocking scope violation)",
        "F_execute_run_end_to_end": "F. End-to-end execute_run (own-checkout apply + repo_validation)",
        "G_per_repo_ekg_ingestion": "G. Per-repo EKG ingestion (namespace evidence + cross-namespace run link)",
        "H_cross_repo_run_creation": "H. Cross-repo run creation (dashboard view surface)",
        "I_execution_tracking": "I. Execution tracking across repositories (per-repo timeline)",
        "J_live_ws_payload": "J. Live repository progress updates (WS payload shape)",
        "K_organization_summary": "K. Organization-level execution summary",
        "L_ekg_navigation": "L. Navigate from repository card to EKG namespace",
        "M_restart_recovery": "M. Restart recovery preserving dashboard state",
    }
    for name, r in results.items():
        mark = "PASS" if r.get("PASS") else "FAIL"
        print(f"\n  [{mark}] {labels.get(name, name)}")
        for k, v in r.items():
            if k == "PASS":
                continue
            print(f"        {k}: {v}")

    all_pass = all(r.get("PASS") for r in results.values())
    print(f"\n{'='*64}")
    print(f"  OVERALL: {'ALL PASS' if all_pass else 'SOME FAILED'}")
    print(f"  MULTI-REPO RUN SURFACE: "
          f"{'PASS' if results['A_multi_repo_surface'].get('PASS') else 'FAIL'}")
    print(f"  ORG-GRAPH ACQUISITION: "
          f"{'PASS' if results['B_aux_materialization'].get('PASS') else 'FAIL'}")
    print(f"  ORG-SCOPE PLANNING CONTEXT: "
          f"{'PASS' if results['C_org_planning_context'].get('PASS') else 'FAIL'}")
    print(f"  PER-REPO VALIDATION: "
          f"{'PASS' if results['D_per_repo_validation'].get('PASS') else 'FAIL'}")
    print(f"  CROSS-CHECKOUT ISOLATION: "
          f"{'PASS' if results['E_cross_checkout_rejection'].get('PASS') else 'FAIL'}")
    print(f"  END-TO-END MULTI-REPO RUN: "
          f"{'PASS' if results['F_execute_run_end_to_end'].get('PASS') else 'FAIL'}")
    print(f"  PER-REPO EKG INGESTION: "
          f"{'PASS' if results['G_per_repo_ekg_ingestion'].get('PASS') else 'FAIL'}")
    print(f"  CROSS-REPO RUN CREATION (A6): "
          f"{'PASS' if results['H_cross_repo_run_creation'].get('PASS') else 'FAIL'}")
    print(f"  EXECUTION TRACKING (A6): "
          f"{'PASS' if results['I_execution_tracking'].get('PASS') else 'FAIL'}")
    print(f"  LIVE WS PAYLOAD (A6): "
          f"{'PASS' if results['J_live_ws_payload'].get('PASS') else 'FAIL'}")
    print(f"  ORGANIZATION SUMMARY (A6): "
          f"{'PASS' if results['K_organization_summary'].get('PASS') else 'FAIL'}")
    print(f"  EKG NAVIGATION (A6): "
          f"{'PASS' if results['L_ekg_navigation'].get('PASS') else 'FAIL'}")
    print(f"  RESTART RECOVERY (A6): "
          f"{'PASS' if results['M_restart_recovery'].get('PASS') else 'FAIL'}")
    print(f"  POSTGRESQL: {'PASS' if pg else 'n/a (in-memory)'}")
    print(f"{'='*64}\n")

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    asyncio.run(main())
