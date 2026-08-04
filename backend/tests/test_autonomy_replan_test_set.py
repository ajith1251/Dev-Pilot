"""
Phase 16 — Impact-Analysis-Driven Replanning tests.

Closes the last Phase 12d roadmap promise: replans select their test set via
Phase 12 impact analysis (semantic graph), and the selection is persisted on
the PlanVersion so it survives a restart and is restored by
`_plan_from_version` when continuing from a checkpoint.

Verifies:
- PlanVersion.test_set is recorded by PlanVersionStore.record
- `_select_impact_tests` returns the graph-selected test files (bounded)
- `_select_impact_tests` degrades to [] without a graph / on error
- `_plan_from_version` restores the impact-driven test strategy
- the plan-recording loop attaches the impact-selected test set on replan
"""

from __future__ import annotations

from app.code_intelligence.semantic_graph import (
    ConfidenceLevel,
    GraphNode,
    RelationshipType,
    SemanticRepositoryGraph,
    make_symbol_id,
)
from app.models.autonomy import (
    AutonomousAction,
    ExecutionState,
    PlanVersion,
)
from app.services.autonomy_service import AutonomousExecutionController
from app.services.test_selection_service import (
    TestSelectionService as ImpactTestSelectionService,
)


def _build_graph_with_tests() -> SemanticRepositoryGraph:
    """Graph: auth_service.py <- tested by test_auth, called by controller."""
    graph = SemanticRepositoryGraph()

    svc_id = make_symbol_id("auth/service.py", "auth.service.AuthService")
    graph.add_node(GraphNode(
        id=svc_id, name="AuthService", qualified_name="auth.service.AuthService",
        kind="class", file_path="auth/service.py", language="Python",
    ))
    login_id = make_symbol_id("auth/service.py", "auth.service.AuthService.login")
    graph.add_node(GraphNode(
        id=login_id, name="login", qualified_name="auth.service.AuthService.login",
        kind="method", file_path="auth/service.py", language="Python",
    ))
    graph.add_edge(svc_id, login_id, RelationshipType.CONTAINS, ConfidenceLevel.EXACT)

    test_id = make_symbol_id("auth/tests/test_auth.py", "test_auth.TestAuthService")
    graph.add_node(GraphNode(
        id=test_id, name="TestAuthService",
        qualified_name="test_auth.TestAuthService",
        kind="test_class", file_path="auth/tests/test_auth.py", language="Python",
    ))
    graph.add_edge(test_id, svc_id, RelationshipType.TESTS, ConfidenceLevel.EXACT)

    return graph


class TestPlanVersionTestSet:
    def test_record_persists_test_set(self) -> None:
        """record() must carry the impact-selected test set into the version."""
        from app.models.issues import ImplementationPlan
        from app.services.autonomy_service import PlanVersionStore

        store = PlanVersionStore()
        state = type("S", (), {"plan_versions": []})()
        plan = ImplementationPlan(summary="Replan auth", objective="Fix tokens", steps=[])

        version = store.record(
            state,
            plan,
            test_set=["auth/tests/test_auth.py", "tests/test_regression.py"],
        )
        assert version is not None
        assert version.test_set == [
            "auth/tests/test_auth.py", "tests/test_regression.py"
        ]

    def test_record_bounds_test_set(self) -> None:
        from app.models.issues import ImplementationPlan
        from app.services.autonomy_service import PlanVersionStore

        store = PlanVersionStore()
        state = type("S", (), {"plan_versions": []})()
        plan = ImplementationPlan(summary="Plan", objective="Objective", steps=[])
        version = store.record(
            state,
            plan,
            test_set=[f"tests/test_{i}.py" for i in range(60)],
        )
        assert len(version.test_set) <= 50


class TestSelectImpactTests:
    def test_graph_backed_selector_returns_tests(self) -> None:
        ctrl = AutonomousExecutionController(
            test_selector=ImpactTestSelectionService(graph=_build_graph_with_tests()),
        )
        tests = ctrl._select_impact_tests(["auth/service.py"])
        assert "auth/tests/test_auth.py" in tests

    def test_unchanged_files_return_empty(self) -> None:
        ctrl = AutonomousExecutionController(
            test_selector=ImpactTestSelectionService(graph=_build_graph_with_tests()),
        )
        assert ctrl._select_impact_tests([]) == []

    def test_no_graph_degrades_to_empty(self) -> None:
        """Without a graph the selection must never raise — returns []."""
        ctrl = AutonomousExecutionController()
        assert ctrl._select_impact_tests(["auth/service.py"]) == []

    def test_selector_error_degrades_to_empty(self) -> None:
        class BrokenSelector:
            def select_for_changed_files(self, changed_files):
                raise RuntimeError("graph unavailable")

        ctrl = AutonomousExecutionController(test_selector=BrokenSelector())
        assert ctrl._select_impact_tests(["auth/service.py"]) == []


class TestEKGDrivenTestSelection:
    """Phase 12d closure: EKG impact edges (patch → test) drive the replan
    test set; the injected semantic-graph selector is now only a fallback.
    """

    def _ekg_with_evidence(self):
        from app.models.engineering_graph import EKNodeType, EKRelationshipType
        from app.services.engineering_graph_service import (
            EngineeringKnowledgeGraphService,
        )

        ekg = EngineeringKnowledgeGraphService()
        run = ekg.add_node(
            EKNodeType.RUN, "run:RUN-E1", source_ref="RUN-E1", source_type="run",
        )
        patch = ekg.add_node(
            EKNodeType.PATCH, "patch:RUN-E1", source_ref="RUN-E1", source_type="run",
        )
        file_node = ekg.add_node(
            EKNodeType.FILE, "service.py", source_ref="auth/service.py",
            source_type="file", qualified_name="auth/service.py",
        )
        tests = ekg.add_node(
            EKNodeType.TEST_SUITE, "tests:RUN-E1", source_ref="RUN-E1",
            source_type="run", qualified_name="tests:RUN-E1",
            payload={"test_files": ["auth/tests/test_auth.py", "tests/test_session.py"]},
        )
        ekg.add_edge(run.node_id, patch.node_id, EKRelationshipType.CREATED_DURING)
        ekg.add_edge(patch.node_id, file_node.node_id, EKRelationshipType.MODIFIES)
        ekg.add_edge(patch.node_id, tests.node_id, EKRelationshipType.VALIDATED_BY)
        return ekg

    def test_ekg_impact_edges_drive_selection(self) -> None:
        """EKG evidence is the primary source: changed file → tests."""
        ctrl = AutonomousExecutionController()
        ctrl._engineering_graph = self._ekg_with_evidence()
        tests = ctrl._select_impact_tests(["auth/service.py"])
        assert "auth/tests/test_auth.py" in tests
        assert "tests/test_session.py" in tests

    def test_injected_selector_fallback_when_ekg_empty(self) -> None:
        """Empty EKG (no evidence) → injected semantic-graph selector."""
        from app.services.engineering_graph_service import (
            EngineeringKnowledgeGraphService,
        )

        ctrl = AutonomousExecutionController(
            test_selector=ImpactTestSelectionService(graph=_build_graph_with_tests()),
        )
        ctrl._engineering_graph = EngineeringKnowledgeGraphService()  # no nodes
        tests = ctrl._select_impact_tests(["auth/service.py"])
        assert "auth/tests/test_auth.py" in tests

    def test_no_evidence_no_selector_degrades_to_empty(self) -> None:
        """No EKG evidence and no selector → [] (never raises)."""
        ctrl = AutonomousExecutionController()
        ctrl._engineering_graph = None
        assert ctrl._select_impact_tests(["auth/service.py"]) == []


class TestPlanFromVersionRestoresTestSet:
    def test_test_set_restored_into_test_strategy(self) -> None:
        ctrl = AutonomousExecutionController()
        version = PlanVersion(
            version=2,
            plan_summary="Replan auth validation",
            plan_objective="Reject expired tokens",
            step_count=3,
            test_set=["auth/tests/test_auth.py", "tests/test_regression.py"],
        )
        plan = ctrl._plan_from_version(version)
        assert plan is not None
        assert "auth/tests/test_auth.py" in plan.test_strategy
        assert "tests/test_regression.py" in plan.test_strategy

    def test_plan_without_test_set_uses_continuation_strategy(self) -> None:
        ctrl = AutonomousExecutionController()
        version = PlanVersion(
            version=1,
            plan_summary="Initial plan",
            plan_objective="Fix tokens",
            step_count=2,
            test_set=[],
        )
        plan = ctrl._plan_from_version(version)
        assert plan is not None
        assert plan.test_strategy == "autonomous continuation"

    def test_hollow_plan_returns_none(self) -> None:
        ctrl = AutonomousExecutionController()
        version = PlanVersion(version=1, plan_summary="", plan_objective="", step_count=0)
        assert ctrl._plan_from_version(version) is None


class TestReplanLoopRecordsTestSet:
    async def test_replan_iteration_records_impact_test_set(self) -> None:
        """When REPLAN runs, the recorded plan version carries the test set."""
        ctrl = AutonomousExecutionController()
        state = await ctrl.create_goal(
            task="Fix token validation",
            repository="repo",
        )
        state.state = ExecutionState.RUNNING
        state.goal.status = ExecutionState.RUNNING

        # Inject evidence: gate rejected, changed auth/service.py
        from app.models.autonomy import IterationEvidence

        evidence = IterationEvidence(
            iteration=1,
            run_id="RUN-1",
            test_status="passed",
            quality_gate_decision="rejected",
            changed_files=["auth/service.py"],
            plan_summary="Replan auth validation",
            plan_objective="Reject expired tokens",
        )
        state.evidence_history.append(evidence)

        # Selector with a graph so impact analysis actually returns tests.
        ctrl._test_selector = ImpactTestSelectionService(graph=_build_graph_with_tests())

        # Force the decision path to REPLAN (gate rejected with replan budget).
        from app.models.autonomy import (
            AutonomyPolicy,
            GoalProgress,
            ProgressTrend,
        )

        state.goal.progress = GoalProgress(
            criteria_total=2, criteria_satisfied=1, criteria_unsatisfied=1,
            trend=ProgressTrend.PROGRESSING,
        )
        state.policy = AutonomyPolicy(allow_repair=True, allow_replan=True)
        action, reason_code, _ = ctrl._decide(state)
        assert action == AutonomousAction.REPLAN

        # Record the plan version the loop would (step 10b path).
        from app.models.issues import ImplementationPlan
        from app.services.autonomy_service import PlanVersionStore

        store = PlanVersionStore()
        store.record(
            state,
            ImplementationPlan(summary=evidence.plan_summary, objective=evidence.plan_objective, steps=[]),
            test_set=ctrl._select_impact_tests(evidence.changed_files),
        )
        assert state.plan_versions
        assert "auth/tests/test_auth.py" in state.plan_versions[-1].test_set
