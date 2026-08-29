"""
Priority 2 — Enterprise Repository Scalability Validation (Session 48).

Measurement-only harness. No new features. Validates DevPilot's existing
subsystems against large real-world repositories (50k–500k+ LOC, multiple
languages, extensive test suites) and emits machine-readable metrics plus a
human summary for ENTERPRISE_SCALABILITY_REPORT.md.

Measured dimensions (wall time + tracemalloc heap peak + counts):
    1. Corpus acquisition     — shallow git clone wall time, disk size, LOC
    2. Repository Intelligence — RepositoryAnalysisWorkflow scan
    3. Indexing               — RepositoryIndexBuilder (tree-sitter) + hybrid retrieval latency
    4. Knowledge Graph        — EKG record_run ingestion (nodes/edges/semantic/PG persist)
    5. Organization Graph     — multi-repo acquisition, cross links, cross-namespace run ingestion
    6. ContextEngine          — build_context latency + context volume
    7. ProviderRouter         — health/metrics snapshot latency (no LLM spend)
    8. Multi-agent pipeline   — full OrchestrationService.execute_run (fake provider,
                                deterministic, zero LLM cost) via `app.cli run --json`
    9. PostgreSQL             — table row counts, table sizes, query latencies, recover() timing
   10. Dashboard API          — in-process ASGI latency for graph/org/providers endpoints

Usage:
    python scripts/scalability_validation.py              # clone + measure (PG when configured)
    python scripts/scalability_validation.py --repos django/django,facebook/react
    python scripts/scalability_validation.py --no-pipeline # skip the orchestrator phase
    python scripts/scalability_validation.py --keep        # keep the corpus checkout

Outputs:
    workflow-status/scalability_metrics.json  (raw metrics)
    stdout summary table
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import tracemalloc
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_BACKEND = Path(__file__).resolve().parent.parent
_REPORT_DIR = Path(__file__).resolve().parent.parent.parent / "workflow-status"


def _checkpoint(metrics: dict) -> None:
    """Write metrics after each phase so a mid-run abort never loses everything."""
    _REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _REPORT_DIR / "scalability_metrics.json"
    out_path.write_text(json.dumps(metrics, indent=2, default=str), encoding="utf-8")

# Extensions that are source-like enough to count as code LOC.
_SOURCE_EXTS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".c", ".h", ".cc", ".cpp", ".hpp",
    ".cs", ".go", ".java", ".rb", ".rs", ".php", ".swift", ".kt", ".scala",
    ".m", ".mm", ".sh", ".sql", ".html", ".css", ".scss", ".vue", ".svelte",
}
_SKIP_DIRS = {".git", "node_modules", "dist", "build", "vendor", ".venv", "venv",
              "__pycache__", ".next", "target", "coverage", ".tox", "site-packages"}
_SKIP_SUFFIXES = (".min.js", ".min.css", ".map", ".pyc", ".so", ".dll", ".dylib",
                  ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".woff", ".woff2", ".lock")


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _db_url() -> str:
    from app.config import settings
    return settings.DATABASE_URL or settings.TEST_DATABASE_URL or ""


# ── 1. Corpus ────────────────────────────────────────────────────────────

def _clone_repo(owner_repo: str, corpus_dir: Path, keep: bool) -> dict:
    owner, repo = owner_repo.split("/")
    dest = corpus_dir / repo
    if dest.exists():
        return {"owner_repo": owner_repo, "path": str(dest), "cloned": "cached"}
    t0 = time.perf_counter()
    subprocess.run(
        ["git", "clone", "--depth", "1", "--quiet", f"https://github.com/{owner}/{repo}.git", str(dest)],
        check=True, capture_output=True, timeout=600,
    )
    dt = time.perf_counter() - t0
    return {"owner_repo": owner_repo, "path": str(dest), "clone_seconds": round(dt, 2)}


def _count_loc(root: Path) -> dict:
    """Count files + lines per source extension, skipping vendored/binary dirs."""
    files = 0
    lines = 0
    by_ext = defaultdict(lambda: {"files": 0, "lines": 0})
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fname in filenames:
            if fname.endswith(_SKIP_SUFFIXES) or fname.startswith("."):
                continue
            ext = Path(fname).suffix.lower()
            if ext not in _SOURCE_EXTS:
                continue
            fp = Path(dirpath) / fname
            try:
                n = sum(1 for _ in open(fp, "r", encoding="utf-8", errors="ignore"))
            except OSError:
                continue
            files += 1
            lines += n
            by_ext[ext]["files"] += 1
            by_ext[ext]["lines"] += n
    top = sorted(
        ({"ext": e, "files": v["files"], "lines": v["lines"]} for e, v in by_ext.items()),
        key=lambda x: -x["lines"],
    )[:6]
    return {"files": files, "lines": lines, "languages": top}


# ── 2. Repository Intelligence ───────────────────────────────────────────

async def _repo_intelligence(path: str) -> dict:
    from app.workflows.repository_analysis import RepositoryAnalysisWorkflow
    workflow = RepositoryAnalysisWorkflow()
    t0 = time.perf_counter()
    state = await workflow.run(path)
    dt = time.perf_counter() - t0
    profile = state.profile
    scan = profile.scan if profile else None
    return {
        "seconds": round(dt, 3),
        "status": state.status,
        "files_scanned": scan.total_files_scanned if scan else 0,
        "dirs_scanned": scan.total_dirs_scanned if scan else 0,
        "files_ignored": scan.total_files_ignored if scan else 0,
        "scan_duration_seconds": round(scan.duration_seconds, 3) if scan else None,
    }


# ── 3. Indexing + retrieval ──────────────────────────────────────────────

def _index_repo(path: str) -> dict:
    from app.services.index_builder import RepositoryIndexBuilder
    builder = RepositoryIndexBuilder(enable_embeddings=False)
    t0 = time.perf_counter()
    index = builder.build(path)
    dt = time.perf_counter() - t0
    stats = index.statistics
    return {
        "seconds": round(dt, 3),
        "files_indexed": stats.files_indexed,
        "symbols_extracted": stats.symbols_extracted,
        "chunks": getattr(stats, "chunks", None),
        "index_duration_seconds": round(stats.duration_seconds, 3),
    }


def _retrieval_latency(path: str, query: str) -> dict:
    from app.services.index_builder import RepositoryIndexBuilder
    from app.rag.retrieval.hybrid_retriever import HybridRetriever
    from app.models.rag import RetrievalQuery
    t0 = time.perf_counter()
    builder = RepositoryIndexBuilder(enable_embeddings=False)
    code_index, lex_idx, sym_idx, vec_idx = builder.build_with_indexes(path)
    build_dt = time.perf_counter() - t0
    retriever = HybridRetriever(lexical_index=lex_idx, symbol_index=sym_idx, vector_index=vec_idx)
    retriever.set_indexes(lex_idx, sym_idx, vec_idx, code_index.chunks)
    rq = RetrievalQuery(text=query, top_k=10)
    t1 = time.perf_counter()
    result = retriever.retrieve(rq)
    retr_dt = time.perf_counter() - t1
    return {
        "index_build_seconds": round(build_dt, 3),
        "retrieve_seconds": round(retr_dt, 3),
        "results": len(result.items),
    }


# ── 4. Knowledge Graph (EKG single-repo) ────────────────────────────────

def _synthetic_run(run_id: str, title: str, repo_path: str, changed_files: list[str]):
    """Deterministic DevPilotRun mirroring the production lineage (Phase 18 demo)."""
    from app.models.orchestration import (
        DevPilotRun, RunSource, RunSourceType, RunStatus, StageType,
    )
    from app.models.issues import ImplementationPlan, ImplementationStep
    from app.models.testing import ExecutionStatus, TestRunResult
    from app.models.repair import RepairResult
    from app.models.review import QualityGateDecision, QualityGateResult, ReviewReport
    from app.models.collaboration import EvidenceRef, EvidenceType
    from app.models.reasoning import (
        ConfidenceScore, ConfidenceTier, ConsensusStatus, ContradictionKind,
        ContradictionRecord, EngineeringNotebook, EvidenceConsensus,
        NotebookEntry, NotebookEntryType,
    )
    from app.models.coding import PatchSet, FileChange, FileOperation

    source = RunSource(source_type=RunSourceType.USER_TASK, title=title)
    return DevPilotRun(
        run_id=run_id,
        source=source,
        repository_path=repo_path,
        status=RunStatus.APPROVED,
        current_stage=StageType.QUALITY_GATE,
        plan=ImplementationPlan(
            summary=f"Plan for {title}", objective=f"Implement {title}",
            steps=[ImplementationStep(
                id="STEP-001", title=title, description=f"Implement {title}",
                affected_areas=["src"],
            )],
            test_strategy=f"impact-driven tests: {', '.join(changed_files[:3])}",
        ),
        patch_set=PatchSet(
            patch_id=f"PS-{run_id[:8]}",
            summary=f"Patch for {title}",
            changes=[FileChange(
                change_id=f"CHANGE-{i:03d}", operation=FileOperation.MODIFY,
                path=cf, new_content=f"# change to {cf}\n",
            ) for i, cf in enumerate(changed_files)],
        ),
        test_result=TestRunResult(
            run_id=run_id, workspace_id=f"ws-{run_id[:8]}",
            tests_total=128, tests_failed=0,
            status=ExecutionStatus.PASSED,
        ),
        repair_result=RepairResult.model_construct(attempts=1, stop_reason="No repairs needed"),
        review_report=ReviewReport(review_id=f"REV-{run_id[:8]}", findings=[]),
        quality_gate_result=QualityGateResult(
            review_id=f"REV-{run_id[:8]}",
            decision=QualityGateDecision.APPROVED,
            blocking_findings=[], requirements_satisfied=4, requirements_unsatisfied=0,
        ),
    ), {
        "consensus": [
            EvidenceConsensus(
                consensus_id=f"CS-{run_id[:8]}", run_id=run_id,
                topic=f"{title} design", summary=f"Approach for {title} is sound",
                status=ConsensusStatus.AGREED,
                confidence=ConfidenceScore(value=0.93, tier=ConfidenceTier.HIGH,
                                           evidence_count=4, deterministic_count=3),
                supporting_evidence=[EvidenceRef(type=EvidenceType.TEST_RESULT, reference=run_id)],
                conflicting_evidence=[], final_decision="Proceed",
                contributing_agents=["planner", "coding"],
            )
        ],
        "contradictions": [
            ContradictionRecord(
                contradiction_id=f"CD-{run_id[:8]}", run_id=run_id,
                kind=ContradictionKind.CLAIM_VS_TEST, description="No contradictions",
                claim_evidence=EvidenceRef(type=EvidenceType.AGENT_CLAIM, reference="n/a"),
                deterministic_evidence=None, resolution="unresolved",
            )
        ],
        "notebook": EngineeringNotebook(
            notebook_id=f"NB-{run_id[:8]}", run_id=run_id, task=title,
            accepted_decisions=[{"statement": f"Implement {title}"}], rejected_decisions=[],
            conflicts=[], resolved_conflicts=[], consensus=[],
            timeline=[NotebookEntry(run_id=run_id, entry_type=NotebookEntryType.TIMELINE, label=title)],
        ),
    }


async def _ekg_ingest(db_url: str, repo_path: str, repo_id: str) -> dict:
    from app.services.engineering_graph_service import EngineeringKnowledgeGraphService
    graph = EngineeringKnowledgeGraphService(database_url=db_url or None, repository_id=repo_id)
    await graph.recover()
    run, outcome = _synthetic_run(
        f"RUN-SCALE-{repo_id.upper()[:6]}", f"Enterprise validation {repo_id}",
        repo_path, ["django/http/response.py", "django/core/checks/__init__.py"][:0] or _pick_files(repo_path),
    )
    t0 = time.perf_counter()
    version = await graph.record_run(run, reasoning_outcome=outcome)
    dt = time.perf_counter() - t0
    stats = graph.stats()
    result = {
        "seconds": round(dt, 3),
        "nodes": stats.node_count,
        "edges": stats.edge_count,
        "node_types": stats.node_types,
        "semantic_embedded": graph.semantic_stats().get("embedded", 0),
        "version": version.version,
    }
    await graph.dispose()
    return result


def _pick_files(repo_path: str) -> list[str]:
    """Pick 4 real source files deterministically (first .py under django/)."""
    root = Path(repo_path)
    candidates = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fname in sorted(filenames):
            if fname.endswith(".py") and "/tests/" not in dirpath.replace("\\", "/"):
                rel = os.path.relpath(os.path.join(dirpath, fname), root).replace("\\", "/")
                candidates.append(rel)
            if len(candidates) >= 4:
                break
        if len(candidates) >= 4:
            break
    return candidates or ["django/__init__.py"]


# ── 5. Organization Graph ────────────────────────────────────────────────

async def _org_graph(db_url: str, repos: list[tuple[str, str]]) -> dict:
    from app.services.organization_graph_service import OrganizationKnowledgeGraphService
    from app.models.engineering_graph import MultiRepoAcquisitionSpec, CrossRepositoryLinkSpec
    org = OrganizationKnowledgeGraphService(database_url=db_url or None)
    specs = [
        MultiRepoAcquisitionSpec(repository_id=repo_id, name=repo_id, source="local", path=path)
        for repo_id, path in repos
    ]
    # Explicit chain: r1 -> r2 -> r3 (deterministic, evidence-only).
    for i in range(len(specs) - 1):
        specs[i].relationships.append(
            CrossRepositoryLinkSpec(
                target_repository_id=specs[i + 1].repository_id,
                relationship="DEPENDS_ON_REPOSITORY", weight=0.9,
            )
        )
    t0 = time.perf_counter()
    acq = await org.acquire_and_link_repositories(specs, ingest=True)
    acq_dt = time.perf_counter() - t0
    stats = org.stats()
    run, outcome = _synthetic_run(
        "RUN-SCALE-ORG", "Cross-repository enterprise validation", repos[0][1],
        ["django/http/response.py", "django/urls/resolvers.py"],
    )
    # Per-repo patch results (Phase 20A5 shape).
    from app.models.orchestration import RepositoryPatchResult
    run.repo_patches = [
        RepositoryPatchResult.model_construct(
            repository_id=repos[0][0], patch_id="P1", validation_status="validated",
            application_status="applied", changes_applied=2, changes_attempted=2,
            changed_files=["django/http/response.py", "django/urls/resolvers.py"],
        ),
        RepositoryPatchResult.model_construct(
            repository_id=repos[1][0], patch_id="P2", validation_status="validated",
            application_status="applied", changes_applied=1, changes_attempted=1,
            changed_files=["packages/react/src/index.ts"],
        ),
    ]
    run.auxiliary_repositories = [
        {"repository_id": repos[2][0]}, {"repository_id": repos[1][0]},
    ]
    t1 = time.perf_counter()
    await org.record_run_across_namespaces(run, reasoning_outcome=outcome)
    ingest_dt = time.perf_counter() - t1

    # Query latencies at each scope.
    from app.models.engineering_graph import QueryScope
    queries = {}
    for scope in (QueryScope.AUTO, QueryScope.LOCAL, QueryScope.ORGANIZATION):
        for qlabel, qtext in (
            ("graph", "affected tests for caching"),
            ("semantic", "authentication middleware security"),
        ):
            t2 = time.perf_counter()
            res = await org.query(qtext, limit=20, scope=scope)
            queries[f"{scope.value}:{qlabel}"] = {
                "seconds": round(time.perf_counter() - t2, 3),
                "nodes": len(res.nodes), "edges": len(res.edges),
                "strategy": res.strategy.value,
            }

    result = {
        "acquire_seconds": round(acq_dt, 3),
        "ingest_seconds": round(ingest_dt, 3),
        "repositories": stats.repository_count,
        "nodes": stats.node_count,
        "edges": stats.edge_count,
        "cross_edges": stats.cross_edge_count,
        "ingested_files": acq.get("ingested_files"),
        "per_repo_nodes": {
            rid: (org.repository_stats(rid) or {}).get("node_count", 0)
            for rid, _ in repos
        },
        "queries": queries,
    }
    await org.dispose()
    return result


# ── 6. ContextEngine ─────────────────────────────────────────────────────

async def _context_engine(db_url: str, repo_path: str, repo_id: str) -> dict:
    from app.services.context_engine import ContextEngine
    from app.services.engineering_graph_service import EngineeringKnowledgeGraphService
    graph = EngineeringKnowledgeGraphService(database_url=db_url or None)
    await graph.recover()
    engine = ContextEngine(engineering_graph=graph, organization_graph=None)
    results = {}
    for agent in ("planner", "coding", "reviewer"):
        t0 = time.perf_counter()
        ctx = await engine.build_context(
            task="Add a new cache-backend validation check to the core checks module",
            agent_type=agent, repository_path=repo_path, run_id=f"RUN-CTX-{agent[:3].upper()}",
            include_organization_context=True,
        )
        dt = time.perf_counter() - t0
        results[agent] = {
            "seconds": round(dt, 3),
            "items": len(getattr(ctx, "items", []) or []),
            "tokens": getattr(getattr(ctx, "usage", None), "total_tokens", None),
        }
    return results


# ── 7. ProviderRouter ───────────────────────────────────────────────────

def _provider_router() -> dict:
    from app.llm.router import ProviderRouter
    router = ProviderRouter()
    t0 = time.perf_counter()
    health = router.health_snapshot()
    health_dt = time.perf_counter() - t0
    t1 = time.perf_counter()
    metrics = router.metrics_snapshot()
    metrics_dt = time.perf_counter() - t1
    return {
        "health_snapshot_seconds": round(health_dt, 3),
        "metrics_snapshot_seconds": round(metrics_dt, 3),
        "providers": len(health.get("providers", []) or []),
        "active_provider": health.get("active_provider"),
        "routing_enabled": health.get("routing_enabled"),
        "totals": metrics.get("totals"),
    }


# ── 8. Multi-agent pipeline (subprocess, fake provider) ─────────────────

def _pipeline(repo_path: str, repo_id: str) -> dict:
    env = os.environ.copy()
    env["DEVPILOT_LLM_PROVIDER"] = "fake"
    env["DEVPILOT_PROVIDER_PRIORITY"] = '["fake"]'
    env["DEVPILOT_LLM_PROVIDER_FALLBACKS"] = "{}"
    env["PYTHONUNBUFFERED"] = "1"
    cmd = [
        sys.executable, "-m", "app.cli", "run", repo_path,
        "--task", f"Add a deterministic validation check to {repo_id}",
        "--description", "Scalability validation run (fake provider, deterministic).",
        "--json",
    ]
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd, cwd=str(_BACKEND), env=env, capture_output=True, text=True,
            timeout=900,
        )
    except subprocess.TimeoutExpired:
        return {"verdict": "TIMEOUT (900s)", "seconds": 900.0}
    total = time.perf_counter() - t0
    out = proc.stdout
    payload = _extract_json_object(out)
    stages = []
    for s in (payload or {}).get("stages", []) or []:
        stages.append({
            "stage": s.get("stage"), "status": s.get("status"),
            "seconds": round((s.get("duration_seconds") or 0), 2),
        })
    result = {
        "seconds": round(total, 2),
        "exit_code": proc.returncode,
        "verdict": (payload or {}).get("status"),
        "quality_gate": (payload or {}).get("quality_gate") or {},
        "stages": stages,
        "stderr_tail": proc.stderr.strip().splitlines()[-3:] if proc.stderr.strip() else [],
    }
    if result["quality_gate"]:
        result["quality_gate"].pop("findings", None)
    return result


def _extract_json_object(text: str) -> dict | None:
    """Robust JSON object extraction (logging may interleave stdout)."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


# ── 9. PostgreSQL ────────────────────────────────────────────────────────

async def _postgres(db_url: str) -> dict:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    engine = create_async_engine(db_url)
    out = {}
    try:
        async with engine.connect() as conn:
            counts = {}
            for table in ("ekg_nodes", "ekg_edges", "ekg_versions", "ekg_embeddings",
                          "runs", "ekg_repository_namespaces", "ekg_cross_repository_edges"):
                try:
                    t0 = time.perf_counter()
                    row = (await conn.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()
                    counts[table] = {"rows": row, "query_seconds": round(time.perf_counter() - t0, 3)}
                except Exception as exc:
                    counts[table] = {"error": str(exc)[:80]}
            out["row_counts"] = counts
            sizes = {}
            for table in ("ekg_nodes", "ekg_edges", "ekg_versions", "runs"):
                row = (await conn.execute(text(
                    f"SELECT pg_size_pretty(pg_total_relation_size('{table}'))"
                ))).scalar_one()
                sizes[table] = row
            out["table_sizes"] = sizes
            # Representative latency probes.
            probes = {
                "org_stats_like": text(
                    "SELECT repository_id, count(*) FROM ekg_nodes GROUP BY repository_id ORDER BY count(*) DESC LIMIT 10"
                ),
                "runs_recent": text("SELECT run_id, status FROM runs ORDER BY created_at DESC LIMIT 10"),
                "version_latest": text("SELECT version, summary FROM ekg_versions ORDER BY version DESC LIMIT 5"),
                "node_lookup": text("SELECT node_id, node_type FROM ekg_nodes WHERE repository_id = :rid LIMIT 50"),
            }
            lat = {}
            for name, q in probes.items():
                t0 = time.perf_counter()
                await conn.execute(q, {"rid": "django"})
                lat[name] = round(time.perf_counter() - t0, 4)
            out["probe_seconds"] = lat
    finally:
        await engine.dispose()

    # recover() timing — restart-recovery scaling.
    from app.services.engineering_graph_service import EngineeringKnowledgeGraphService
    graph = EngineeringKnowledgeGraphService(database_url=db_url)
    t0 = time.perf_counter()
    await graph.recover()
    rec = time.perf_counter() - t0
    out["recover_seconds"] = round(rec, 3)
    out["recovered_nodes"] = len(graph._nodes)
    out["recovered_edges"] = graph._edges_count()
    await graph.dispose()
    return out


# ── 10. Dashboard API (in-process ASGI) ─────────────────────────────────

async def _dashboard_api() -> dict:
    import httpx
    from app.main import app
    transport = httpx.ASGITransport(app=app)
    routes = [
        ("GET", "/api/v1/health", None),
        ("GET", "/api/v1/graph/version", None),
        ("GET", "/api/v1/graph/org/stats", None),
        ("GET", "/api/v1/graph/org/repositories", None),
        ("GET", "/api/v1/graph/query?query=cache+middleware&limit=10&scope=organization", None),
        ("GET", "/api/v1/providers/metrics", None),
        ("GET", "/api/v1/operations/metrics", None),
        ("GET", "/api/v1/runs", None),
    ]
    out = {}
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=120) as client:
        for method, path, _ in routes:
            t0 = time.perf_counter()
            try:
                resp = await client.request(method, path)
                dt = time.perf_counter() - t0
                out[path] = {"seconds": round(dt, 3), "status": resp.status_code}
            except Exception as exc:
                out[path] = {"error": str(exc)[:120]}
    return out


# ── Main ────────────────────────────────────────────────────────────────

async def _run_all(args) -> dict:
    tracemalloc.start()
    metrics: dict = {
        "generated_at": _now(),
        "python": sys.version.split()[0],
        "corpus_dir": args.corpus_dir,
        "phases": {},
    }
    corpus_dir = Path(args.corpus_dir)
    corpus_dir.mkdir(parents=True, exist_ok=True)

    # Phase 1: corpus
    print("\n=== [1/10] Corpus acquisition (shallow git clones) ===")
    repos = []
    for owner_repo in args.repos.split(","):
        info = _clone_repo(owner_repo.strip(), corpus_dir, args.keep)
        info["loc"] = _count_loc(Path(info["path"]))
        repos.append(info)
        print(f"  {info['owner_repo']:<28} clone={info.get('clone_seconds', 'cached')}s "
              f"files={info['loc']['files']} lines={info['loc']['lines']}")
    metrics["phases"]["1_corpus"] = repos
    _checkpoint(metrics)

    db_url = _db_url() if not args.no_pg else ""
    print(f"  PostgreSQL: {'ENABLED' if db_url else 'in-memory only'}")

    skip = getattr(args, "skip_phases", set())

    # Phase 2: repository intelligence
    print("\n=== [2/10] Repository Intelligence (RepositoryAnalysisWorkflow) ===")
    ri = {}
    for info in repos if 2 not in skip else []:
        m = await _repo_intelligence(info["path"])
        ri[info["owner_repo"]] = m
        print(f"  {info['owner_repo']:<28} {m['seconds']}s  files={m['files_scanned']} "
              f"dirs={m['dirs_scanned']} ignored={m['files_ignored']}")
    metrics["phases"]["2_repo_intelligence"] = ri
    _checkpoint(metrics)

    # Phase 3: indexing + retrieval
    print("\n=== [3/10] Indexing (RepositoryIndexBuilder, tree-sitter) ===")
    idx = {}
    django = None
    for info in repos if 3 not in skip else []:
        m = _index_repo(info["path"])
        idx[info["owner_repo"]] = m
        print(f"  {info['owner_repo']:<28} {m['seconds']}s  files={m['files_indexed']} "
              f"symbols={m['symbols_extracted']}")
    if 3 not in skip:
        django = next(r for r in repos if r["owner_repo"].endswith("django"))
        idx["retrieval"] = _retrieval_latency(django["path"], "cache backend middleware security")
        print(f"  retrieval(query): {idx['retrieval']['retrieve_seconds']}s "
              f"(index build {idx['retrieval']['index_build_seconds']}s, {idx['retrieval']['results']} hits)")
    metrics["phases"]["3_indexing"] = idx
    _checkpoint(metrics)

    # Phase 4: EKG single-repo ingestion
    print("\n=== [4/10] Knowledge Graph (EKG record_run) ===")
    ekgs = {}
    for i, info in enumerate(repos if 4 not in skip else []):
        m = await _ekg_ingest(db_url, info["path"], info["owner_repo"].split("/")[1])
        ekgs[info["owner_repo"]] = m
        print(f"  {info['owner_repo']:<28} {m['seconds']}s  nodes={m['nodes']} edges={m['edges']} "
              f"semantic={m['semantic_embedded']}")
    metrics["phases"]["4_ekg"] = ekgs
    _checkpoint(metrics)

    # Phase 5: organization graph
    print("\n=== [5/10] Organization Graph (multi-repo) ===")
    org = {}
    if 5 not in skip:
        org = await _org_graph(db_url, [(r["owner_repo"].split("/")[1], r["path"]) for r in repos])
        print(f"  acquire+link {org['repositories']} repos: {org['acquire_seconds']}s, "
              f"ingested_files={org['ingested_files']}")
        print(f"  org stats: nodes={org['nodes']} edges={org['edges']} cross={org['cross_edges']}")
        print(f"  cross-namespace run ingest: {org['ingest_seconds']}s")
        for qname, qm in list(org["queries"].items())[:3]:
            print(f"  query[{qname}] {qm['seconds']}s nodes={qm['nodes']}")
    else:
        print("  skipped (--skip 5)")
    metrics["phases"]["5_org_graph"] = org
    _checkpoint(metrics)

    # Phase 6: ContextEngine
    print("\n=== [6/10] ContextEngine ===")
    ctx = {}
    if 6 not in skip and django:
        ctx = await _context_engine(db_url, django["path"], "django")
    for agent, m in ctx.items():
        print(f"  {agent:<10} {m['seconds']}s  items={m['items']}")
    metrics["phases"]["6_context_engine"] = ctx
    _checkpoint(metrics)

    # Phase 7: ProviderRouter
    print("\n=== [7/10] ProviderRouter (health/metrics snapshots) ===")
    router = {}
    if 7 not in skip:
        router = _provider_router()
        print(f"  health_snapshot={router['health_snapshot_seconds']}s  "
              f"metrics_snapshot={router['metrics_snapshot_seconds']}s  providers={router['providers']}")
    else:
        print("  skipped (--skip 7)")
    metrics["phases"]["7_provider_router"] = router
    _checkpoint(metrics)

    # Phase 8: multi-agent pipeline
    print("\n=== [8/10] Multi-agent pipeline (execute_run, fake provider) ===")
    if not args.no_pipeline:
        pipe = _pipeline(django["path"], "django")
        print(f"  total={pipe['seconds']}s  verdict={pipe.get('verdict')}  exit={pipe.get('exit_code')}")
        for s in pipe.get("stages", []):
            print(f"    {s['stage']:<24} {s['status']:<12} {s['seconds']}s")
        metrics["phases"]["8_pipeline"] = pipe
    else:
        print("  skipped (--no-pipeline)")
    _checkpoint(metrics)

    # Phase 9: PostgreSQL
    print("\n=== [9/10] PostgreSQL performance ===")
    if db_url and 9 not in skip:
        pg = await _postgres(db_url)
        for table, c in pg["row_counts"].items():
            print(f"  {table:<28} rows={c['rows']:<8} count_query={c['query_seconds']}s")
        print(f"  recover(): {pg['recover_seconds']}s -> {pg['recovered_nodes']} nodes, {pg['recovered_edges']} edges")
        metrics["phases"]["9_postgres"] = pg
    else:
        print("  skipped (no DB)")
    _checkpoint(metrics)

    # Phase 10: dashboard API
    print("\n=== [10/10] Dashboard API (in-process ASGI) ===")
    api = {}
    if 10 not in skip:
        api = await _dashboard_api()
    for path, m in api.items():
        if "error" in m:
            print(f"  {path:<48} ERROR {m['error'][:60]}")
        else:
            print(f"  {path:<48} {m['seconds']}s  [{m['status']}]")
    metrics["phases"]["10_dashboard_api"] = api
    _checkpoint(metrics)

    tracemalloc.stop()
    return metrics


def main() -> None:
    ap = argparse.ArgumentParser(description="DevPilot enterprise scalability validation (measurement only)")
    ap.add_argument("--repos", default="django/django,facebook/react,redis/redis",
                    help="Comma-separated owner/repo list")
    ap.add_argument("--corpus-dir", default=str(Path(tempfile.gettempdir()) / "devpilot-scalability-corpus"),
                    help="Corpus checkout directory")
    ap.add_argument("--no-pipeline", action="store_true", help="Skip the full pipeline phase")
    ap.add_argument("--no-pg", action="store_true", help="Force in-memory graph (skip PostgreSQL phases)")
    ap.add_argument("--keep", action="store_true", help="Keep the corpus directory")
    ap.add_argument("--skip", default="",
                    help="Comma-separated phase numbers to skip (e.g. 4,5,8)")
    args = ap.parse_args()
    args.skip_phases = {int(x) for x in args.skip.split(",") if x.strip()}

    if not args.keep:
        import atexit
        def _cleanup():
            if Path(args.corpus_dir).exists():
                shutil.rmtree(Path(args.corpus_dir), ignore_errors=True)
        atexit.register(_cleanup)

    metrics = asyncio.run(_run_all(args))

    _REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _REPORT_DIR / "scalability_metrics.json"
    out_path.write_text(json.dumps(metrics, indent=2, default=str), encoding="utf-8")
    print(f"\nMetrics written: {out_path}")
    print("Validation complete.")


if __name__ == "__main__":
    main()
