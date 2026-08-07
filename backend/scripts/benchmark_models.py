"""
Priority 1 — Live Model Quality Optimization benchmark.

Evaluates every CONFIGURED provider (registry-derived) on IDENTICAL
engineering tasks, scoring the outputs through the SAME deterministic gates
the pipeline uses, so model quality is compared on equal footing:

    Probe A  planning   — planner prompt (real PLANNER_* prompts) → parse →
                         ImplementationPlan → PlanValidator
    Probe B  coding     — real CodingAgent bound to the provider → PatchSet →
                         hash enrichment → PatchValidator → SafePatchEngine
                         apply → fixture pytest via ControlledExecutionEngine
    Probe C  repair     — real FixAgent bound to the provider → RepairProposal
                         → patch validity
    Probe D  review     — real ReviewerAgent (LLM mode) bound to the provider
                         → AgentReview findings parsed + evidence-validated
    Probe E  json       — minimal structured-JSON adherence (first-try + repair)

Latency is measured around every provider.chat() call. Token usage is captured
when the provider reports it (cost proxy). A provider outage / quota error is
recorded as a probe failure (reliability), never a crash.

The benchmark is registry-derived: only providers whose availability attr is
set in the environment are tested (same gate as check_live_mode). With no
live provider configured it prints a skip notice and exits 0 (CI-safe).

Usage:
    python scripts/benchmark_models.py                     # all configured providers
    python scripts/benchmark_models.py --providers nvidia,gemini
    python scripts/benchmark_models.py --models "nvidia:nvidia/llama-3.3-nemotron-super-49b-v1"
    python scripts/benchmark_models.py --probes plan,coding,json
    python scripts/benchmark_models.py --json              # machine-readable results
    python scripts/benchmark_models.py --report path.md    # write markdown report
"""

from __future__ import annotations

import sys

for _stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(_stream, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="replace")

import argparse
import asyncio
import hashlib
import json
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# ── Fixture task (identical for every provider/model) ──────────────
# The fixture ships with ONE genuinely failing test: test_validate_expired_token
# creates a token with token_expiry_hours=0 (expires immediately) and asserts it
# is still valid right after creation. The correct fix gives freshly created
# tokens a minimum validity floor so they never expire instantly.
_FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "fixture_auth_app"
_TASK = "Fix AuthService token validation so a freshly created token is never " \
        "expired instantly (token_expiry_hours=0 must still yield a valid token)"
_REQUIREMENTS = [
    "A token created with token_expiry_hours=0 must validate immediately after "
    "creation (minimum validity floor)",
    "Existing auth tests (test_create_token, test_validate_expired_token, "
    "test_revoke_token, test_invalid_token) must all pass",
    "Expired tokens must still be rejected when their expiry is genuinely reached",
]
_TIMEOUT_PER_CALL = 240.0

# Default candidate models per provider (first = provider default).
_DEFAULT_MODELS: Dict[str, List[str]] = {}


def _provider_default_models() -> Dict[str, List[str]]:
    """Per-provider model candidates. Defaults come from the live factory;
    stronger candidates are curated per provider for the benchmark."""
    if _DEFAULT_MODELS:
        return _DEFAULT_MODELS
    from app.llm.provider_registry import provider_names

    curated: Dict[str, List[str]] = {
        "nvidia": [
            "meta/llama-3.1-8b-instruct",
            "nvidia/llama-3.3-nemotron-super-49b-v1",
            "deepseek-ai/deepseek-r1",
        ],
        "gemini": ["gemini-3.6-flash", "gemini-3.5-flash-lite"],
        "cloudflare": ["@cf/meta/llama-4-scout-17b-16e-instruct"],
        "ollama_cloud": ["gemma4:31b", "gpt-oss:120b"],
        "opencode_zen": ["deepseek-v4-flash-free", "deepseek-v4-flash"],
        "openrouter": ["poolside/laguna-s-2.1:free"],
    }
    from app.llm.factory import factory

    for name in provider_names():
        try:
            prov = factory.get_provider(name)
            default = prov.default_model
        except Exception:
            continue
        if name in curated and default not in curated[name]:
            curated[name] = [default] + [m for m in curated[name] if m != default]
        elif name not in curated:
            curated[name] = [default]
    _DEFAULT_MODELS.update(curated)
    return _DEFAULT_MODELS


def configured_providers() -> List[str]:
    """Registry-derived: providers whose availability attr is set (live)."""
    from app.config import settings
    from app.llm.provider_registry import provider_availability, provider_names

    out: List[str] = []
    for name in provider_names():
        if name == "fake":
            continue
        attr, _always = provider_availability().get(name, (None, False))
        if attr and getattr(settings, attr, None):
            out.append(name)
        elif _always and name != "fake":
            out.append(name)
    return out


def check_live_mode() -> bool:
    """Reuse the registry-derived live gate from demo_phase17."""
    from demo_phase17 import check_live_mode as _check_live_mode

    return _check_live_mode()


# ── Probe inputs (identical for every provider/model) ──────────────

def _build_requirements():
    from app.models.issues import Requirement, RequirementType, StructuredRequirements

    return StructuredRequirements(
        objective=_TASK,
        requirements=[
            Requirement(
                description=req,
                requirement_type=RequirementType.FUNCTIONAL,
                acceptance_note="Run the fixture test suite",
            )
            for req in _REQUIREMENTS
        ],
        constraints=[],
        risks=[],
        ambiguities=[],
    )


def _fixed_plan():
    """Deterministic reference plan so the coding probe is IDENTICAL for
    every provider (planner quality is measured separately in Probe A)."""
    from app.models.issues import ImplementationPlan, ImplementationStep

    return ImplementationPlan(
        summary="Ensure freshly created auth tokens are never instantly expired",
        objective=_TASK,
        steps=[
            ImplementationStep(
                id="STEP-001",
                title="Add minimum validity floor in AuthService.create_token",
                description=(
                    "In auth/service.py AuthService.create_token, enforce a "
                    "minimum token lifetime so token_expiry_hours=0 (or any "
                    "value <= 0) still produces a token valid immediately after "
                    "creation, while genuinely expired tokens stay rejected by "
                    "validate_token."
                ),
                affected_areas=["auth/service.py"],
                expected_changes="auth/service.py",
            ),
        ],
    )


def _build_retrieved_context():
    """Real code chunks from the fixture (identical context for all models)."""
    from app.models.rag import (
        CodeChunk,
        RetrievedContext,
        RetrievedContextItem,
        RetrievalQuery,
    )

    items: List[RetrievedContextItem] = []
    for rel in ("auth/service.py", "tests/test_auth.py"):
        path = _FIXTURE / rel
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8")
        items.append(RetrievedContextItem(
            chunk=CodeChunk(
                chunk_id=f"chunk-{rel}",
                snapshot_id="benchmark",
                file_path=rel,
                language="python",
                content=content[:4000],
                start_line=1,
                end_line=content.count("\n") + 1,
                content_hash=hashlib.sha256(content.encode()).hexdigest(),
            ),
            score=0.9,
            reasons=["fixture source file"],
        ))
    return RetrievedContext(
        query=RetrievalQuery(text=_TASK, likely_affected_areas=["auth/service.py"]),
        snapshot_id="benchmark",
        items=items,
        total_candidates=len(items),
    )


def _workspace_structure() -> str:
    return "\n".join(
        sorted(str(p.relative_to(_FIXTURE)) for p in _FIXTURE.rglob("*")
               if p.is_file() and "__pycache__" not in str(p))
    )


# ── Probe A: planning ──────────────────────────────────────────────

async def probe_planning(provider, model: str) -> Dict[str, Any]:
    """Drive the real planner prompts through the provider and validate the
    resulting plan with PlanValidator (identical task for every model)."""
    from app.llm.base import LLMConfig, LLMMessage
    from app.prompts.planning import (
        PLANNER_SYSTEM_PROMPT,
        PLANNER_USER_PROMPT,
        build_requirements_text,
    )

    reqs = _build_requirements()
    reqs_text = build_requirements_text([
        {
            "description": r.description,
            "requirement_type": r.requirement_type.value,
            "acceptance_note": r.acceptance_note,
            "is_implied": r.is_implied,
        }
        for r in reqs.requirements
    ])
    user_prompt = (
        PLANNER_USER_PROMPT
        .replace("{requirements_text}", reqs_text)
        .replace("{repo_context_text}", "Languages: Python\nKey files: auth/tokens.py, auth/service.py, auth/routes.py, products/service.py")
        .replace("{constraints_text}", "(none)")
        .replace("{risks_text}", "(none)")
        .replace("{ambiguities_text}", "(none)")
    )
    messages = [
        LLMMessage(role="system", content=PLANNER_SYSTEM_PROMPT),
        LLMMessage(role="user", content=user_prompt),
    ]
    started = time.monotonic()
    response = await provider.chat(
        messages,
        config=LLMConfig(temperature=0.1, max_tokens=4096, capability="planning"),
    )
    latency_ms = (time.monotonic() - started) * 1000.0
    raw = response.content

    from app.agents.planner import PlannerAgent

    parsed = PlannerAgent()._parse_json_response(raw)
    if not parsed:
        return {"pass": False, "latency_ms": latency_ms, "usage": response.usage,
                "reason": "planner JSON could not be parsed (even after repair)",
                "steps": 0, "gate_pass": False}
    plan = PlannerAgent()._build_plan(_TASK, parsed)
    from app.services.plan_validator import PlanValidator

    validation = PlanValidator().validate(plan)
    return {
        "pass": True,
        "latency_ms": latency_ms,
        "usage": response.usage,
        "steps": len(plan.steps),
        "gate_pass": validation.is_valid,
        "gate_errors": validation.errors[:3],
    }


# ── Probe B: coding ────────────────────────────────────────────────

async def probe_coding(provider, model: str) -> Dict[str, Any]:
    """Real CodingAgent bound to the provider; the generated PatchSet is run
    through the same deterministic gates as the pipeline (hash enrichment,
    PatchValidator, SafePatchEngine apply) and the fixture pytest suite."""
    from app.agents.coding_agent import CodingAgent
    from app.services.patch_validator import PatchValidator
    from app.services.safe_patch_engine import SafePatchEngine

    # generate_patch consumes workspace_structure (string) + retrieved context
    # chunks only — it never reads the on-disk workspace, so no copy is needed
    # for the generation call; the apply/test workspace is created below.
    agent = CodingAgent(llm_provider=provider, model=model)
    try:
        started = time.monotonic()
        patch = await agent.generate_patch(
            plan=_fixed_plan(),
            retrieved_context=_build_retrieved_context(),
            requirements=_build_requirements(),
            workspace_structure=_workspace_structure(),
        )
        latency_ms = (time.monotonic() - started) * 1000.0
    except Exception as exc:
        return {"pass": False, "latency_ms": 0.0, "reason": f"coding agent failed: {str(exc)[:200]}",
                "patch_valid": False, "applied": False, "tests_pass": False, "gate_pass": False}

    if patch is None or not patch.changes:
        return {"pass": False, "latency_ms": latency_ms,
                "reason": "no patch produced (empty PatchSet)",
                "patch_valid": False, "applied": False, "tests_pass": False, "gate_pass": False}

    # Hash enrichment (mirror orchestrator._enrich_patch_hashes) then validate.
    tmp2 = tempfile.mkdtemp(prefix="bench-code-")
    try:
        workspace = str(Path(tmp2) / "repo")
        shutil.copytree(str(_FIXTURE), workspace)
        from app.models.coding import FileOperation

        for change in patch.changes:
            if change.operation not in (FileOperation.MODIFY, FileOperation.DELETE):
                continue
            if change.original_hash:
                continue
            target = Path(workspace) / change.path
            if target.is_file():
                change.original_hash = hashlib.sha256(target.read_bytes()).hexdigest()

        validator = PatchValidator(workspace_root=workspace)
        validation = validator.validate(patch)
        patch_valid = validation.is_valid
        applied = False
        tests_pass = False
        if patch_valid:
            engine = SafePatchEngine(workspace_root=workspace)
            result = engine.apply(patch)
            applied = result.status.value == "applied"
            if applied:
                tests_pass = await _run_fixture_tests(workspace)
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)

    return {
        "pass": patch_valid,
        "latency_ms": latency_ms,
        "patch_valid": patch_valid,
        "applied": applied,
        "tests_pass": tests_pass,
        "gate_pass": patch_valid and tests_pass,
        "changes": len(patch.changes),
        "validation_errors": validation.errors[:3] if not patch_valid else [],
    }


async def _run_fixture_tests(workspace: str) -> bool:
    """Run the fixture pytest suite through ControlledExecutionEngine."""
    import sys as _sys

    from app.models.testing import CommandCategory, ExecutionStep
    from app.services.controlled_execution_engine import ControlledExecutionEngine

    engine = ControlledExecutionEngine(default_timeout=120)
    step = ExecutionStep(
        step_id="BENCH-TEST",
        category=CommandCategory.TEST,
        executable=_sys.executable,
        arguments=["-m", "pytest", "-q"],
        timeout_seconds=120,
    )
    result = await engine.execute(step, workspace_root=workspace)
    return result.status.value == "passed"


# ── Probe C: repair ────────────────────────────────────────────────

async def probe_repair(provider, model: str) -> Dict[str, Any]:
    """Real FixAgent bound to the provider with a fabricated failing-test
    diagnosis (expired token accepted); scores the resulting RepairProposal."""
    from app.agents.fix_agent import FixAgent, FixAgentInput
    from app.models.repair import (
        FailureCategory,
        FailureDiagnosis,
        Repairability,
    )
    from app.models.testing import ExecutionStatus, TestFailure, TestRunResult
    from app.services.patch_validator import PatchValidator

    diagnosis = FailureDiagnosis(
        diagnosis_id="BENCH-DIAG-1",
        run_id="BENCH-RUN-1",
        failure_ids=["BENCH-FAIL-1"],
        category=FailureCategory.ASSERTION_FAILURE,
        summary="Freshly created token with token_expiry_hours=0 is instantly "
                "expired (test_validate_expired_token fails)",
        likely_cause=(
            "AuthService.create_token sets expires_at = utcnow() + "
            "timedelta(hours=token_expiry_hours) with no minimum validity "
            "floor, so token_expiry_hours=0 expires the token immediately"
        ),
        confidence=0.9,
        repairability=Repairability.REPAIRABLE,
        affected_files=["auth/service.py"],
    )
    test_result = TestRunResult(
        run_id="BENCH-RUN-1",
        workspace_id="bench",
        status=ExecutionStatus.FAILED,
        tests_total=1, tests_passed=0, tests_failed=1,
    )
    failures = [TestFailure(
        failure_id="BENCH-FAIL-1",
        framework="pytest",
        test_name="test_validate_expired_token",
        file_path="tests/test_auth.py",
        message="assert AuthService(token_expiry_hours=0).validate_token("
                "token) is not None",
        failure_type=FailureCategory.ASSERTION_FAILURE,
    )]
    context = (_FIXTURE / "auth" / "service.py").read_text(encoding="utf-8")

    agent = FixAgent(llm_provider=provider, model=model)
    started = time.monotonic()
    out = await agent.execute(FixAgentInput(
        diagnosis=diagnosis,
        test_result=test_result,
        failures=failures,
        changed_file_context=context,
        plan=_fixed_plan(),
    ))
    latency_ms = (time.monotonic() - started) * 1000.0

    proposal = out.proposal
    if proposal.status.value != "proposed" or proposal.patch is None:
        return {"pass": False, "latency_ms": latency_ms,
                "reason": f"repair not proposed: {proposal.status.value} — {proposal.reason[:120]}",
                "patch_valid": False, "gate_pass": False}

    tmp = tempfile.mkdtemp(prefix="bench-repair-")
    try:
        workspace = str(Path(tmp) / "repo")
        shutil.copytree(str(_FIXTURE), workspace)
        # Mirror the orchestrator's hash enrichment before validation.
        from app.models.coding import FileOperation

        for change in proposal.patch.changes:
            if change.operation not in (FileOperation.MODIFY, FileOperation.DELETE):
                continue
            if change.original_hash:
                continue
            target = Path(workspace) / change.path
            if target.is_file():
                change.original_hash = hashlib.sha256(target.read_bytes()).hexdigest()
        validation = PatchValidator(workspace_root=workspace).validate(proposal.patch)
        applied = False
        tests_pass = False
        if validation.is_valid:
            from app.services.safe_patch_engine import SafePatchEngine

            result = SafePatchEngine(workspace_root=workspace).apply(proposal.patch)
            applied = result.status.value == "applied"
            if applied:
                tests_pass = await _run_fixture_tests(workspace)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return {
        "pass": validation.is_valid,
        "latency_ms": latency_ms,
        "patch_valid": validation.is_valid,
        "applied": applied,
        "tests_pass": tests_pass,
        "gate_pass": validation.is_valid and tests_pass,
        "changes": len(proposal.patch.changes),
        "validation_errors": validation.errors[:3] if not validation.is_valid else [],
    }


# ── Probe D: review ────────────────────────────────────────────────

async def probe_review(provider, model: str) -> Dict[str, Any]:
    """Real ReviewerAgent (LLM mode) bound to the provider; scores parsed +
    evidence-validated findings (no hallucinated file paths)."""
    from app.agents.reviewer import ReviewerAgent, ReviewerAgentInput
    from app.models.review import ChangedFileSummary, ReviewContext

    context = ReviewContext(
        requirements_text="\n".join(f"- {r}" for r in _REQUIREMENTS),
        plan_text=_fixed_plan().summary,
        changed_files_summaries=[
            ChangedFileSummary(
                path="auth/service.py",
                change_type="MODIFY",
                related_requirements=[_REQUIREMENTS[0]],
            ),
        ],
        changed_files_content=(
            "```python\n"
            + (_FIXTURE / "auth" / "service.py").read_text(encoding="utf-8")[:3000]
            + "\n```"
        ),
        test_evidence="tests/test_auth.py: 1 failed (test_validate_expired_token), "
                       "3 passed",
        original_patch_summary=(
            "MODIFY auth/service.py: minimum validity floor in "
            "AuthService.create_token"
        ),
    )
    agent = ReviewerAgent(llm_provider=provider, model=model)
    started = time.monotonic()
    review = await agent.execute(ReviewerAgentInput(context=context, use_llm=True))
    latency_ms = (time.monotonic() - started) * 1000.0

    return {
        "pass": len(review.findings) > 0,
        "latency_ms": latency_ms,
        "findings": len(review.findings),
        "assessments": len(review.requirement_assessments),
        "warnings": review.warnings[:3],
        "summary": review.summary[:150],
    }


# ── Probe E: strict JSON adherence ─────────────────────────────────

async def probe_json(provider, model: str) -> Dict[str, Any]:
    """Minimal structured-JSON adherence: first-try parse + repair fallback."""
    from app.agents.json_repair import repair_json_text
    from app.llm.base import LLMConfig, LLMMessage
    import json as _json

    messages = [
        LLMMessage(role="user", content=(
            "Respond with ONLY a JSON object (no prose, no code fences) with "
            'exactly these keys: {"summary": "short", "steps": [{"id": "STEP-001", '
            '"title": "t"}]}'
        )),
    ]
    started = time.monotonic()
    try:
        response = await provider.chat(
            messages,
            config=LLMConfig(temperature=0.0, max_tokens=500),
        )
    except Exception as exc:
        return {"pass": False, "latency_ms": 0.0, "reason": str(exc)[:150],
                "first_try": False, "repaired": False}
    latency_ms = (time.monotonic() - started) * 1000.0
    raw = response.content.strip()
    first_try = False
    repaired = False
    try:
        _json.loads(raw)
        first_try = True
    except Exception:
        fixed = repair_json_text(raw)
        if fixed is not None:
            try:
                _json.loads(fixed)
                repaired = True
            except Exception:
                pass
    return {
        "pass": first_try or repaired,
        "latency_ms": latency_ms,
        "usage": response.usage,
        "first_try": first_try,
        "repaired": repaired and not first_try,
    }


# ── Runner ─────────────────────────────────────────────────────────

async def run_provider_model(
    provider_name: str, model: str, probes: List[str],
) -> Dict[str, Any]:
    from app.llm.factory import factory

    try:
        provider = factory.get_provider(provider_name)
    except Exception as exc:
        return {"provider": provider_name, "model": model, "error": str(exc)[:200],
                "results": {}}

    registry: Dict[str, Any] = {}
    probe_map = {
        "plan": probe_planning,
        "coding": probe_coding,
        "repair": probe_repair,
        "review": probe_review,
        "json": probe_json,
    }
    for name in probes:
        fn = probe_map.get(name)
        if fn is None:
            continue
        try:
            result = await asyncio.wait_for(
                fn(provider, model), timeout=_TIMEOUT_PER_CALL * 2,
            )
            result["error"] = None
        except asyncio.TimeoutError:
            result = {"pass": False, "reason": "probe timed out", "error": "timeout"}
        except Exception as exc:
            result = {"pass": False, "reason": str(exc)[:200], "error": str(exc)[:200]}
        result["probe"] = name
        registry[name] = result

    passes = [r for r in registry.values() if r.get("pass")]
    # Only include measured latencies — a crash/timeout result carries 0.0 and
    # would otherwise understate the average for failing providers.
    latency = [r.get("latency_ms") or 0 for r in registry.values()
               if r.get("latency_ms")]
    usage_total = {
        "prompt_tokens": sum((r.get("usage") or {}).get("prompt_tokens", 0) or 0
                             for r in registry.values()),
        "completion_tokens": sum((r.get("usage") or {}).get("completion_tokens", 0) or 0
                                 for r in registry.values()),
    }
    return {
        "provider": provider_name,
        "model": model,
        "results": registry,
        "success_rate": round(len(passes) / max(1, len(registry)), 3),
        "avg_latency_ms": round(sum(latency) / max(1, len(latency)), 1),
        "usage": usage_total,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--providers", default=None,
                        help="Comma-separated provider names (default: all configured)")
    parser.add_argument("--models", default=None,
                        help="'provider:model1,model2;...' overrides the candidate list")
    parser.add_argument("--probes", default="plan,coding,repair,review,json",
                        help="Comma-separated probes (plan,coding,repair,review,json)")
    parser.add_argument("--json", action="store_true", help="Print JSON results")
    parser.add_argument("--report", default=None,
                        help="Write a markdown report to this path")
    args = parser.parse_args()

    if not check_live_mode():
        print("  [skip] No live LLM provider configured — benchmark not run "
              "(CI-safe). Set DEVPILOT_LLM_PROVIDER + a provider key in .env.")
        return

    configured = configured_providers()
    providers = [p.strip() for p in (args.providers or "").split(",") if p.strip()] or configured
    providers = [p for p in providers if p in configured] or configured

    models_override: Dict[str, List[str]] = {}
    if args.models:
        for chunk in args.models.split(";"):
            if ":" not in chunk:
                continue
            name, _, models = chunk.partition(":")
            models_override[name.strip()] = [m.strip() for m in models.split(",") if m.strip()]

    probes = [p.strip() for p in args.probes.split(",") if p.strip()]
    candidates = _provider_default_models()
    if models_override:
        candidates.update(models_override)

    print("=" * 72)
    print("  PRIORITY 1 — LIVE MODEL QUALITY BENCHMARK")
    print(f"  Task: {_TASK}")
    print(f"  Probes: {', '.join(probes)}")
    print(f"  Providers: {', '.join(providers)}")
    print("=" * 72)

    all_results: List[Dict[str, Any]] = []
    for name in providers:
        for model in candidates.get(name, []):
            print(f"\n  Benchmarking {name} :: {model} ...")
            result = await run_provider_model(name, model, probes)
            all_results.append(result)
            sr = result.get("success_rate", 0)
            lat = result.get("avg_latency_ms", 0)
            print(f"    success_rate={sr:.0%}  avg_latency={lat:.0f}ms")
            for probe_name, r in result.get("results", {}).items():
                mark = "PASS" if r.get("pass") else "FAIL"
                extra = r.get("reason") or ""
                if probe_name == "coding" and not r.get("pass"):
                    extra = (extra or "") + (
                        f" [patch_valid={r.get('patch_valid')} "
                        f"applied={r.get('applied')} tests_pass={r.get('tests_pass')}]")
                print(f"      [{mark}] {probe_name:<7} {extra[:110]}")

    if args.report:
        _write_report(args.report, all_results, probes)

    if args.json:
        print("\nJSON:")
        print(json.dumps(all_results, indent=2, default=str))

    print("\n" + "=" * 72)
    print("  DONE — full report in " + (args.report or "stdout (use --json)"))
    print("=" * 72)


def _write_report(path: str, results: List[Dict[str, Any]], probes: List[str]) -> None:
    from datetime import datetime, timezone

    rows = []
    for r in sorted(results, key=lambda x: (-x.get("success_rate", 0),
                                            x.get("avg_latency_ms", 0))):
        res = r.get("results", {})
        gate = all(res.get(p, {}).get("gate_pass", False)
                   for p in ("plan", "coding", "repair") if p in res)
        rows.append({
            "provider": r["provider"],
            "model": r["model"],
            "success_rate": r.get("success_rate", 0),
            "avg_latency_ms": r.get("avg_latency_ms", 0),
            "probes": res,
            "gate_pass": gate,
            "usage": r.get("usage", {}),
        })

    lines: List[str] = []
    lines.append("# Live Model Quality Benchmark — Priority 1")
    lines.append("")
    lines.append(f"> Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} "
                 f"by `scripts/benchmark_models.py` (live, registry-derived providers, "
                 f"identical task: *{_TASK}*).")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Provider | Model | Success | Avg latency | Coding gate | Probes |")
    lines.append("|---|---|---|---|---|---|")
    for row in rows:
        probes_summary = " ".join(
            f"{p}={'✓' if row['probes'].get(p, {}).get('pass') else '✗'}"
            for p in probes if p in row["probes"]
        )
        lines.append(
            f"| {row['provider']} | `{row['model']}` | "
            f"{row['success_rate']:.0%} | {row['avg_latency_ms']:.0f}ms | "
            f"{'✓' if row['gate_pass'] else '✗'} | {probes_summary} |"
        )
    lines.append("")

    lines.append("## Per-probe detail")
    lines.append("")
    for row in rows:
        lines.append(f"### {row['provider']} — `{row['model']}`")
        lines.append("")
        lines.append(f"- Success rate: {row['success_rate']:.0%}, "
                     f"avg latency {row['avg_latency_ms']:.0f}ms, "
                     f"tokens {row['usage'].get('prompt_tokens', 0)}/"
                     f"{row['usage'].get('completion_tokens', 0)} (in/out)")
        for probe_name, r in row["probes"].items():
            mark = "PASS" if r.get("pass") else "FAIL"
            detail = r.get("reason") or r.get("summary") or ""
            lines.append(f"- **{probe_name}**: {mark} — {detail[:200]}")
        lines.append("")

    lines.append("## Recommendations (per stage)")
    lines.append("")
    best_plan = _best_for(rows, "plan")
    best_coding = _best_for(rows, "coding")
    best_repair = _best_for(rows, "repair")
    best_review = _best_for(rows, "review")
    lines.append(f"- **Planning**: {best_plan or 'n/a'}")
    lines.append(f"- **Coding**: {best_coding or 'n/a'}")
    lines.append(f"- **Repair**: {best_repair or 'n/a'}")
    lines.append(f"- **Review**: {best_review or 'n/a'}")
    lines.append("")
    lines.append("> Gate pass = all of plan/coding/repair probes passed their "
                 "deterministic validators; coding gate additionally requires the "
                 "fixture pytest suite to pass after SafePatchEngine apply.")
    lines.append("")

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(lines), encoding="utf-8")
    print(f"\n  Report written to {path}")


def _best_for(rows: List[Dict[str, Any]], probe: str) -> Optional[str]:
    """Highest gate+pass score for a probe; tie-break by latency."""
    scored = []
    for row in rows:
        r = row["probes"].get(probe)
        if not r:
            continue
        score = (1 if r.get("pass") else 0) + (1 if r.get("gate_pass") else 0)
        if probe == "coding" and r.get("tests_pass"):
            score += 1
        scored.append((score, row["avg_latency_ms"], f"{row['provider']} `{row['model']}`"))
    if not scored:
        return None
    scored.sort(key=lambda x: (-x[0], x[1]))
    return scored[0][2]


if __name__ == "__main__":
    asyncio.run(main())
