# Phase 16 — Autonomous Execution

> **Status**: Complete ✅
> **Tests**: 84 new deterministic autonomy tests (models, controller, API, CLI, security)
> **Database**: PostgreSQL — 5 new tables (execution_goals, plan_versions,
> autonomous_decisions, execution_checkpoints, human_escalations), migration 007
> **Last updated**: August 1, 2026

---

## 1. Overview

Phase 16 adds a **higher-level autonomous controller** above the Phase 10/15
orchestrator. The orchestrator executes engineering stages; the controller
decides *what happens next* — when a task is complete, needs another iteration,
needs a replan, is stuck, exceeded its budget, or requires a human.

```
                     User Goal
                         ↓
                 Autonomous Controller
                         ↓
                Goal / Criteria Model
                         ↓
                 Execution Strategy
                         ↓
                    Orchestrator
                         ↓
        Planner → Coding → Testing → Review
                    ↑        │
                    └ Repair ┘
                         ↓
                Progress Evaluation
                  ↙      ↓       ↘
             CONTINUE  REPLAN   COMPLETE
                         │
                  ESCALATE / STOP
```

**Architecture rule**: the autonomy layer does NOT replace orchestration.

| Layer | Responsibility |
|-------|---------------|
| Autonomous Controller | decides WHAT happens next |
| Orchestrator | executes engineering stages |
| Agents | perform specialized work |
| Quality Gate | determines final engineering approval |

Agents cannot create uncontrolled execution loops — the controller bounds every
dimension of execution.

---

## 2. Goal Model

`ExecutionGoal` is the structured goal the controller pursues:

```text
goal_id, task, repository
acceptance_criteria      — measurable criteria
constraints              — explicit bounds
status, attempt, replan_count
progress, created_at, updated_at
```

`AcceptanceCriterion` carries a deterministic verification hint:

```text
criterion_id, description, criterion_type
status        — PENDING / SATISFIED / UNSATISFIED / BLOCKED / UNKNOWN
confidence, evidence, verification
```

Verification hints select the deterministic evidence rule:

| Hint | Satisfied when |
|------|---------------|
| `test:<name>` | the named test passes |
| `suite:pass` | the whole suite passes |
| `gate:approved` | the quality gate approves |
| `file:<path>` | the file was changed by the patch |
| `review:no_blocking` | no blocking review findings |

Criteria are extracted from explicit texts, Phase 4 `StructuredRequirements`,
and plan steps (deduplicated, capped at 20 per goal).

---

## 3. Evidence-Based Goal Evaluation

`GoalEvaluator` is **deterministic-first**. A criterion is only marked
SATISFIED when deterministic evidence supports it — test results, patch
evidence, review findings, and the quality gate. An LLM saying "the task looks
done" is never sufficient (§5/§36).

```text
C1 Expired tokens rejected
   ↓
test_validate_expired_token PASS
   ↓
SATISFIED

C3 Existing auth tests pass
   ↓
pytest suite PASS
   ↓
SATISFIED
```

---

## 4. Execution Budgets

`ExecutionBudget` bounds every dimension (§8):

```text
max_iterations, max_replans, max_repairs
max_agent_calls, max_llm_calls
max_files_changed, max_test_runs
max_execution_time_seconds
```

`BudgetManager` checks remaining budget before expensive operations and records
usage after them. When a **global** limit is exhausted the run stops or
escalates (`BUDGET_EXHAUSTED`) — it never silently exceeds configured limits
(§9).

A limit of `0` means the action is *disabled* (routed by the decision logic),
not instantly exhausted. Repair/replan budgets are consumed by the controller's
own decisions and routed to REPLAN/ESCALATE when exhausted, so the loop cannot
loop on repairs forever.

---

## 5. Progress Tracking & Stuck Detection

`ProgressEvaluator` compares iterations using criteria satisfaction, test
failures, and failure class:

```text
Iteration 1: 3 failing tests   →  Iteration 2: 1 failing test   → PROGRESSING
Iteration 1: same failure      →  Iteration 2: same failure     → STALLED
```

`StuckDetector` deterministically detects looping before budgets run out (§11):

- same failing tests repeatedly (`LOOPING`)
- same error fingerprint repeatedly (`STALLED`)
- repeated quality-gate rejection (`STALLED`)
- no criteria improvement with failing tests (`STALLED`)
- replanning to an identical plan (`LOOPING`)
- environment failures (`BLOCKED`)

Three consecutive stalled iterations escalate before the budget is exhausted.

---

## 6. Autonomous Decisions

`AutonomousDecision` records every controller decision — action, reason code,
short rationale, and evidence refs. **Chain-of-thought is never persisted**
(§34).

```text
CONTINUE → REPAIR → REPLAN → REVIEW → COMPLETE → ESCALATE → STOP
```

Decision order (§6/§7):

1. Stuck / looping takes priority (escalate or stop).
2. All criteria satisfied + gate approved → **COMPLETE**.
3. Tests failing → **REPAIR** (while repair budget remains), else **REPLAN**,
   else escalate/stop.
4. Gate rejected with tests passing → **REPLAN** (while replan budget remains).
5. Tests passed, gate pending → **REVIEW**.

---

## 7. Dynamic Replanning & Plan Versioning

`PlanVersionStore` keeps immutable plan history (§12/§13). Replans:

- pass the existing deterministic `PlanValidator`
- must not repeat an identical failed plan
- must not drop unresolved requirements (non-empty steps)
- must not silently expand scope

Plan versions are persisted and never overwritten — a superseded version keeps
its `superseded_reason`, completed steps, and remaining criteria.

---

## 8. Scope Control

`TaskScope` tracks allowed modules, expected change area, and forbidden areas.
Changes outside scope increment `scope_expansion_requests`; when expansion is
not allowed, the run **escalates** for approval (§15). Large scope expansion
must never silently touch unrelated repository areas.

---

## 9. Human Escalation

`HumanEscalation` is structured (§16) — it records what happened, what DevPilot
attempted, current evidence, remaining criteria, and the specific input needed.
Reasons include `AMBIGUOUS_REQUIREMENT`, `BUDGET_EXHAUSTED`, `STUCK`,
`SCOPE_EXPANSION`, and more. When waiting for a human, the run is
`WAITING_FOR_HUMAN`; `provide_input()` records the clarification, resolves the
open escalations, and resumes from the safe next action.

---

## 10. Pause / Resume / Cancellation

- **Pause** sets a pause flag checked between operations; the loop stops
  cleanly and persists a checkpoint (`PAUSED`).
- **Resume** clears the flag and re-enters the loop from the safe state.
- **Cancel** is authoritative (§18): it sets the cancellation flag and persists
  a checkpoint. No further agent invocation occurs after cancellation is
  observed. A goal awaiting human input never auto-resumes.

---

## 11. Checkpoints, Recovery & Concurrency

- **Checkpoints** (§24) persist goal state, criteria, budget usage, iteration,
  plan version, the latest autonomous decision, and evidence after every
  iteration.
- **Recovery** (§25) reloads the goal, plan versions, decisions, checkpoints,
  and escalations from PostgreSQL and resumes from the last safe checkpoint —
  it never blindly re-runs the previous agent.
- **Concurrency** (§27) uses optimistic versioning: every checkpoint write is a
  compare-and-swap on `execution_goals.version`. If another worker advanced the
  run, `ConcurrencyConflictError` is raised and the run may be reloaded from its
  last durable checkpoint. Two autonomous workers cannot advance the same goal
  independently.

---

## 12. Environment vs Code Failure

`classify_failure()` distinguishes CODE / TEST / ENVIRONMENT / CONFIG /
DEPENDENCY failures (§19/§20) before any repair decision. An environment
failure (e.g. `environment_not_ready`) blocks the run and escalates — it never
triggers a CodingAgent to "fix" database code.

---

## 13. Autonomy Policy & Stop Conditions

`AutonomyPolicy` determines what autonomy may do (§28):

```text
allow_repair, allow_replan, allow_test_execution
allow_scope_expansion, allow_human_escalation
max_scope_expansions
```

Deterministic stop conditions (§37): `GOAL_COMPLETED`, `CANCELLED`,
`BUDGET_EXHAUSTED`, `STUCK`, `HUMAN_INPUT_REQUIRED`, `UNRECOVERABLE_FAILURE`,
`SECURITY_BLOCK`. No infinite autonomous loop is possible.

---

## 14. Dry-Run Mode

`dry_run()` estimates scope, budget, and the likely workflow without any
mutations (§29) — useful before starting an expensive autonomous run.

---

## 15. Security

All repository content, task text, test output, memory, and handoffs are
treated as untrusted (§35). Injected instructions cannot:

- fabricate completion (criteria need deterministic evidence)
- bypass budgets (limits are enforced on the model, not on LLM claims)
- expand scope silently (escalation required)
- expose hidden reasoning (only decisions + evidence are exposed)

---

## 16. API

| Endpoint | Purpose |
|----------|---------|
| `POST /api/v1/autonomy/run` | create a goal and run the bounded loop |
| `GET /api/v1/autonomy/dry-run` | estimate without mutations |
| `GET /api/v1/autonomy/{goal_id}` | full run status |
| `GET /api/v1/autonomy/{goal_id}/progress` | criteria progress |
| `GET /api/v1/autonomy/{goal_id}/decisions` | recorded decisions |
| `POST /api/v1/autonomy/{goal_id}/pause` | pause between operations |
| `POST /api/v1/autonomy/{goal_id}/resume` | resume |
| `POST /api/v1/autonomy/{goal_id}/cancel` | authoritative cancel |
| `POST /api/v1/autonomy/{goal_id}/input` | resolve a human escalation |

Run state is validated before every mutation.

---

## 17. CLI

```text
devpilot autonomous-run <repo> "<task>"
devpilot autonomous-status <goal_id>
devpilot autonomous-dry-run <repo> "<task>"
devpilot autonomous-pause <goal_id>
devpilot autonomous-resume <goal_id>
devpilot autonomous-cancel <goal_id>
```

---

## 18. Frontend

The `/devpilot-context` dashboard gains an **Autonomous Execution** section:
start / dry-run, load status, pause / resume / cancel, acceptance criteria with
evidence, the latest decision, plan versions, and human-escalation input. It
uses the real APIs and exposes decisions + evidence only.

---

## 19. Persistence

Migration 007 adds five tables:

| Table | Purpose |
|-------|---------|
| `execution_goals` | goal, criteria, budget, policy, scope (JSONB) + version |
| `plan_versions` | immutable plan history |
| `autonomous_decisions` | controller decisions |
| `execution_checkpoints` | durable iteration checkpoints |
| `human_escalations` | structured human input requests |

All persistence degrades gracefully to in-memory when PostgreSQL is
unavailable, so deterministic tests never require a live database.
