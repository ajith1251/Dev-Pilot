# Phase 9 — Reviewer Agent & Deterministic Quality Gate

> **Status**: Complete ✅  
> **Tests**: 66 Phase 9 tests + 571 total, 0 failed, 5 skipped

---

## 1. Overview

Phases 6–8 answer:
- *Can DevPilot implement the task?*
- *Can it verify the implementation?*
- *Can it repair failures?*

Phase 9 answers:

> **Should the resulting implementation actually be accepted?**

Passing tests alone are **not** sufficient evidence of correctness. Phase 9 reviews requirements, plan, code changes, repair history, test evidence, and security invariants, producing a structured engineering decision.

### Architecture

```
Final Workspace + Requirements + ImplementationPlan + Patch History + Repair History + Test Evidence
                                    │
                                    ▼
                          ReviewContextBuilder
                                    │
                   ┌────────────────┴────────────────┐
                   ▼                                 ▼
        DeterministicReview                   ReviewerAgent
        (9 check types,                        (LLM-assisted,
         no LLM required)                       optional)
                   │                                 │
                   └────────────────┬────────────────┘
                                    ▼
                            EvidenceValidator
                                    │
                                    ▼
                              QualityGate
                           (deterministic)
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
               APPROVED        REJECTED       NEEDS HUMAN
                                              REVIEW
```

### Fundamental Rule

Reviewer Agent has **READ + REASON** authority. It has **NO** write, execute, fix, commit, or push authority.

---

## 2. Reviewer Agent

| Aspect | Detail |
|--------|--------|
| **Module** | `app/agents/reviewer.py` |
| **Input** | `ReviewerAgentInput(context: ReviewContext, use_llm: bool)` |
| **Output** | `AgentReview(findings[], requirement_assessments[], summary)` |
| **Two modes** | ① **Deterministic** — no LLM required, structured context analysis. ② **LLM-assisted** — uses `BaseLLMProvider` for semantic analysis. |
| **Fallback** | Provider unavailable → deterministic mode; Malformed JSON → empty findings |
| **Provider abstraction** | Uses `BaseLLMProvider` via `llm_factory.get_provider()` |
| **Prompt trust boundaries** | `[TRUSTED INSTRUCTIONS]`, `[UNTRUSTED REPOSITORY CONTENT]`, `[UNTRUSTED TEST OUTPUT]`, `[UNTRUSTED PATCH CONTENT]` |

Findings from ReviewerAgent are **advisory only**. The QualityGate makes the final decision.

---

## 3. Review Context Builder

| Aspect | Detail |
|--------|--------|
| **Module** | `app/services/review_context_builder.py` |
| **Inputs** | `ReviewInput` (combines models from Phases 4-8) |
| **Output** | `ReviewContext` (bounded, prioritized context) |
| **Context budget** | Configurable: `DEVPILOT_REVIEW_MAX_CONTEXT_CHARS` (default 50K), `DEVPILOT_REVIEW_MAX_FILES` (default 10), `DEVPILOT_REVIEW_MAX_CONTENT_PER_FILE` (default 3K) |
| **Final-state handling** | Reviews final workspace state (including repair changes), not just original patch |
| **Redaction** | Secrets redacted: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GITHUB_TOKEN`, `DEVPILOT_SECRET_CANARY`, `sk-*`, `ghp_*` |

### Context Priority

1. Requirements
2. Final changed code
3. Relevant tests
4. Final test evidence
5. Implementation plan
6. Repair history
7. Architecture context
8. Original patch metadata

---

## 4. Requirement Coverage

| Aspect | Detail |
|--------|--------|
| **Model** | `RequirementCoverage` in `app/models/review.py` |
| **Statuses** | `SATISFIED`, `PARTIALLY_SATISFIED`, `UNSATISFIED`, `UNVERIFIED`, `NOT_APPLICABLE` |
| **Traceability** | Requirement → Plan Step → Changed File | Verification Evidence |
| **Evidence** | Test results, plan coverage mapping, file changes |

Example:
```
REQ-001 "Expired reset tokens must be rejected"
  → STEP-002 "Update token validation"
  → auth/tokens.py
  → tests/test_tokens.py::test_expired_token → PASSED
```

---

## 5. Review Findings

| Aspect | Detail |
|--------|--------|
| **Model** | `ReviewFinding` in `app/models/review.py` |
| **Categories** | `REQUIREMENT`, `CORRECTNESS`, `TESTING`, `SECURITY`, `ARCHITECTURE`, `MAINTAINABILITY`, `SCOPE`, `REGRESSION`, `DOCUMENTATION`, `QUALITY`, `TAMPERING` |
| **Severities** | `CRITICAL`, `HIGH`, `MEDIUM`, `LOW`, `INFO` |
| **Blocking** | `blocking: bool` — blocks approval if true |
| **Evidence validation** | `ReviewEvidenceValidator` — validates file paths, requirement IDs, plan step IDs against known context |

### Hallucination Protection

The `ReviewEvidenceValidator` validates all LLM findings:
- File paths must exist in changed files list
- Requirement IDs must exist in requirements
- Plan step IDs must exist in plan
- Invalid references → finding downgraded or removed

---

## 6. Deterministic Review Checks

| Check ID | Check | Severity if Failed | Blocking |
|----------|-------|-------------------|----------|
| DET-001 | Final verification executed | HIGH | ✅ |
| DET-002 | Final tests passed | CRITICAL | ✅ |
| DET-003 | Environment ready | HIGH | ✅ |
| DET-004 | Timeout not exceeded | HIGH | ✅ |
| DET-005 | Execution error free | CRITICAL | ✅ |
| DET-006 | Test skip rate normal | MEDIUM | ❌ |
| DET-007 | Failure count manageable | MEDIUM | ❌ |
| DET-008-009 | Requirement coverage | HIGH | ✅ |
| DET-010-012 | Repair completed successfully | HIGH | ✅ |
| DET-013 | No unsafe repair | CRITICAL | ✅ |
| DET-014 | Repair attempted | MEDIUM | ❌ |
| DET-015 | No repeated patches | MEDIUM | ❌ |
| DET-016 | Changes in scope | MEDIUM | ❌ |
| DET-017 | Test files not deleted | CRITICAL | ✅ |
| DET-018 | Tests not weakened | HIGH | ✅ |
| DET-019 | No tampering in repair | HIGH | ✅ |
| DET-020 | No security violations | CRITICAL | ✅ |
| DET-021 | Original repo not modified | CRITICAL | ✅ |

---

## 7. Quality Gate

| Aspect | Detail |
|--------|--------|
| **Module** | `app/services/quality_gate.py` |
| **Inputs** | `ReviewReport`, `DeterministicReviewResult`, optional `TestRunResult` |
| **Decisions** | `APPROVED`, `REJECTED`, `NEEDS_HUMAN_REVIEW`, `INCOMPLETE` |
| **Hard blockers** | Tests failed, CRITICAL security finding, unsatisfied required requirement, test tampering, security boundary bypass, unresolved repair, missing verification |
| **Human-review rules** | Unverified requirements, insufficient evidence, conflicting evidence |
| **Reason codes** | Machine-readable: `TESTS_FAILED`, `SECURITY_BLOCKER`, `REQUIREMENT_UNSATISFIED`, `TEST_TAMPERING`, `UNRESOLVED_REPAIR`, `REVIEW_PASSED`, etc. |
| **LLM authority** | **NONE** — QualityGate is 100% deterministic. LLM findings cannot override mandatory gate rules. |

### Approval Rule

```
required requirements satisfied
AND final verification acceptable
AND no blocking findings
AND security checks acceptable
AND review evidence valid
```

### Score

A heuristic quality score (0-100) is computed for informational purposes only.  
Hard gates **always override** the score. The score is never the primary decision mechanism.

---

## 8. Testing Integration

| Aspect | Detail |
|--------|--------|
| **TestRunResult** | Consumed as primary verification evidence |
| **Skipped tests** | Distinguished as expected vs suspicious |
| **Failures** | Counted, classified, presented as evidence |
| **Final verification** | Post-repair test result is authoritative (not initial) |

---

## 9. Repair Integration

| Aspect | Detail |
|--------|--------|
| **RepairResult** | Consumed for repair history and final state |
| **Attempts** | Counted and summarized |
| **Final workspace** | Reviewed, not just original patch |
| **Best-known state** | Used as final state when repair partially succeeds |

---

## 10. API

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v1/review/run` | POST | Execute full review pipeline |
| `/api/v1/review/capabilities` | GET | List Phase 9 capabilities |

---

## 11. CLI

| Command | Purpose |
|---------|---------|
| `devpilot review --plan-file <path> --run-file <path>` | Execute full review workflow |
| `devpilot review --verbose` | Show detailed findings |

Options: `--plan-file`, `--requirements-file`, `--patch-file`, `--run-file`, `--repair-file`, `--use-llm`, `--verbose`

---

## 12. Security

| Check | Status | Detail |
|-------|--------|--------|
| Reviewer direct file writes | ❌ NONE | Agent produces findings only |
| Reviewer process execution | ❌ NONE | No execution path exists from ReviewerAgent |
| Patch application | ❌ NONE | No PatchSet generated or applied |
| Original repository mutation | ❌ NONE | Read-only review |
| Secret exposure | ❌ NONE | Redaction in context builder + tested |
| Prompt injection authority | ❌ NONE | Untrusted content boundaries enforced |
| LLM gate authority | ❌ NONE | QualityGate is 100% deterministic |
| Hallucinated evidence accepted | ❌ NONE | EvidenceValidator rejects invalid references |
| Test failure override by LLM | ❌ NONE | Deterministic checks win |
| Quality Gate deterministic | ✅ YES | Same input → same output |

---

## 13. Known Limitations

1. **Semantic review quality** depends on available context and model capability
2. **Not a comprehensive security scanner** — code-change review only
3. **Generic framework test evidence** remains less rich than pytest
4. **Requirement traceability** may be heuristic (file-path pattern matching)
5. **No persistent review history** — in-memory only
6. **No human approval UI/workflow** yet
7. **No full repository-wide static analysis** — context-bounded review only
8. **No frontend review dashboard** — API-driven only

---

## 14. Phase 10 Contract

Phase 10 (End-to-End Multi-Agent Orchestration) receives these entry points:

| Phase | Service | Module | Entry Point | Side Effects |
|-------|---------|--------|-------------|--------------|
| 1 | Health | `app.main` | `GET /health` | None |
| 2 | Repository Analysis | `app.workflows.repository_analysis.RepositoryAnalysisWorkflow` | `.run(path)` | Read-only scan |
| 3 | GitHub Integration | `app.services.github.GitHubService` | `.get_issue()`, `.get_repo_metadata()` | Network read |
| 4 | Issue Analysis | `app.agents.planner.PlannerAgent` | `.execute(TaskInput)` → `ImplementationPlan` | LLM call |
| 4 | Plan Validation | `app.services.plan_validator.PlanValidator` | `.validate(ImplementationPlan)` | Deterministic |
| 5 | Code Indexing | `app.services.index_builder.RepositoryIndexBuilder` | `.build(path)` | Read-only index |
| 5 | Hybrid Retrieval | `app.rag.retrieval.hybrid_retriever.HybridRetriever` | `.retrieve(RetrievalQuery)` | Read-only |
| 5 | Plan-Aware Retrieval | `app.rag.retrieval.plan_context_retriever.PlanContextRetriever` | `.retrieve_for_plan()` | Read-only |
| 6 | Coding Agent | `app.agents.coding_agent.CodingAgent` | `.execute(CodingAgentInput)` → `PatchSet` | LLM call |
| 6 | Patch Validation | `app.services.patch_validator.PatchValidator` | `.validate(patch, workspace_root)` | Deterministic |
| 6 | Patch Application | `app.services.safe_patch_engine.SafePatchEngine` | `.apply(PatchSet)` | Workspace write |
| 7 | Test Agent | `app.agents.test_agent.TestAgent` | `.execute(TestAgentInput)` → `ExecutionPlan` | Deterministic/LLM |
| 7 | Test Execution | `app.services.testing_service.TestingService` | `.run_tests(ExecutionPlan)` | Subprocess exec |
| 8 | Failure Diagnosis | `app.services.failure_diagnosis_service.FailureDiagnosisService` | `.diagnose(TestRunResult)` | Deterministic |
| 8 | Fix Agent | `app.agents.fix_agent.FixAgent` | `.execute(FixAgentInput)` → `RepairProposal` | LLM call |
| 8 | Repair Loop | `app.services.repair_service.RepairService` | `.run_repair(...)` → `RepairResult` | Write + exec |
| 9 | Review Context Builder | `app.services.review_context_builder.ReviewContextBuilder` | `.build(ReviewInput)` → `ReviewContext` | Read-only |
| 9 | Deterministic Review | `app.services.deterministic_review.DeterministicReview` | `.run(ReviewInput)` → `DeterministicReviewResult` | Deterministic |
| 9 | Reviewer Agent | `app.agents.reviewer.ReviewerAgent` | `.execute(ReviewerAgentInput)` → `AgentReview` | LLM call |
| 9 | Quality Gate | `app.services.quality_gate.QualityGate` | `.decide(ReviewReport, ...)` → `QualityGateResult` | Deterministic |
| 9 | Full Review | `app.workflows.review.ReviewWorkflow` | `.run(...)` → `(ReviewReport, QualityGateResult)` | See above |
