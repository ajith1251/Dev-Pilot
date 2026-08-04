"""
Phase 13 — Context Engineering models.

Defines the canonical context structures for the ContextEngine:
- ContextBudget: token allocation among categories
- ContextSource / Provenance: where each context item comes from
- ContextItem: a single unit of context with provenance and score
- AgentContext: the complete context delivered to an agent
"""

from __future__ import annotations

from copy import deepcopy
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── Context Sources ────────────────────────────────────────────


class ContextSourceType(str, Enum):
    """Origin of a context item."""

    GRAPH = "graph"                       # Phase 12 semantic graph
    VECTOR = "vector"                     # Phase 5 vector/semantic retrieval
    LEXICAL = "lexical"                   # Phase 5 lexical retrieval
    IMPACT = "impact"                     # Phase 12 impact analysis
    RUN_MEMORY = "run_memory"            # Historical run memory
    REPOSITORY_MEMORY = "repository_memory"  # Phase 13 repository knowledge memory
    TEST_FAILURE = "test_failure"         # Phase 7 test failure
    REPAIR_HISTORY = "repair_history"     # Phase 8 repair history
    REVIEW_FINDING = "review_finding"     # Phase 9 review finding
    IMPLEMENTATION_PLAN = "plan"          # Phase 4 implementation plan
    REQUIREMENTS = "requirements"         # Phase 4 requirements
    WORKSPACE = "workspace"               # Workspace metadata
    CACHED = "cached"                     # Process-local context cache
    CROSS_AGENT = "cross_agent"           # Shared notes/context from prior agents
    HANDOFF = "handoff"                   # Phase 15 structured agent handoff


class Provenance(BaseModel):
    """Where a context item came from and why it was selected."""

    source: ContextSourceType = Field(description="Origin of this evidence")
    score: float = Field(default=0.0, ge=0.0, le=1.0, description="Normalized relevance score")
    distance: Optional[int] = Field(default=None, description="Graph distance (if from graph)")
    relationship: Optional[str] = Field(default=None, description="Relationship type (e.g. CALLS, IMPORTS)")
    run_id: Optional[str] = Field(default=None, description="Associated run (if from historical run)")
    memory_id: Optional[str] = Field(default=None, description="Associated memory (if from repository memory)")
    symbol_id: Optional[str] = Field(default=None, description="Associated symbol (if from graph)")
    file_path: Optional[str] = Field(default=None, description="Associated file path")
    test_name: Optional[str] = Field(default=None, description="Associated test (if from test failure)")
    plan_step_id: Optional[str] = Field(default=None, description="Associated plan step")
    detail: str = Field(default="", description="Human-readable reason for selection")


# ── Context Categories ─────────────────────────────────────────


class ContextCategory(str, Enum):
    """Category of context for token budgeting and agent-specific selection."""

    TASK = "task"
    REPOSITORY_SUMMARY = "repository_summary"
    PRIMARY_CODE = "primary_code"
    DEPENDENCIES = "dependencies"
    CALLERS = "callers"
    CALLEES = "callees"
    RELATED_TESTS = "related_tests"
    CODE_CHUNKS = "code_chunks"
    IMPLEMENTATION_PLAN = "implementation_plan"
    GRAPH_EVIDENCE = "graph_evidence"
    RUN_HISTORY = "run_history"
    PREVIOUS_FAILURES = "previous_failures"
    PREVIOUS_REPAIRS = "previous_repairs"
    REVIEW_FINDINGS = "review_findings"
    REPOSITORY_MEMORY = "repository_memory"
    CONSTRAINTS = "constraints"
    WARNINGS = "warnings"
    AGENT_NOTES = "agent_notes"           # Cross-agent shared notes
    AGENT_HANDOFF = "agent_handoff"       # Phase 15 structured handoff evidence


# ── Context Item ───────────────────────────────────────────────


class ContextItem(BaseModel):
    """A single unit of context with provenance and scoring."""

    content: str = Field(description="The actual context content (formatted text)")
    category: ContextCategory = Field(description="Category for budgeting")
    provenance: Provenance = Field(description="Where this item came from")

    # Token estimation
    estimated_tokens: int = Field(default=0, ge=0, description="Estimated token count")

    # Deduplication
    dedup_key: str = Field(default="", description="Canonical key for deduplication")
    is_duplicate: bool = Field(default=False)
    merged_provenances: List[Provenance] = Field(
        default_factory=list,
        description="Provenance records from duplicate items merged into this survivor",
    )

    # Metadata
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ── Token Budget ────────────────────────────────────────────────


class BudgetCategory(BaseModel):
    """Token allocation for a single context category."""

    category: ContextCategory = Field(description="Category")
    percentage: float = Field(default=0.0, ge=0.0, le=100.0, description="Percentage of available context tokens")
    max_tokens: int = Field(default=0, ge=0, description="Hard max tokens for this category")


class ContextBudget(BaseModel):
    """Token budget configuration for agent context.

    Allocates available context tokens among categories.
    Categories are processed in priority order — higher-priority
    categories get their budget first.
    """

    max_total_tokens: int = Field(default=8000, ge=1000, le=100000, description="Hard total token cap")
    reserved_instructions: int = Field(default=2000, ge=500, le=10000, description="Reserved for system instructions + task")
    reserved_output: int = Field(default=2000, ge=500, le=10000, description="Reserved for agent output")

    categories: List[BudgetCategory] = Field(default_factory=list)
    priority_order: List[ContextCategory] = Field(
        default_factory=list,
        description="Categories ordered by priority (higher first)",
    )

    @property
    def available_context_tokens(self) -> int:
        """Tokens available for context content."""
        return self.max_total_tokens - self.reserved_instructions - self.reserved_output

    def config_for_agent(self, agent_type: str) -> ContextBudget:
        """Return a budget configured for a specific agent type.

        Different agents get different category allocations.
        """
        budget = deepcopy(self)

        if agent_type == "planner":
            budget.categories = [
                BudgetCategory(category=ContextCategory.TASK, percentage=10, max_tokens=1000),
                BudgetCategory(category=ContextCategory.REPOSITORY_SUMMARY, percentage=15, max_tokens=1500),
                BudgetCategory(category=ContextCategory.PRIMARY_CODE, percentage=20, max_tokens=2000),
                BudgetCategory(category=ContextCategory.DEPENDENCIES, percentage=15, max_tokens=1500),
                BudgetCategory(category=ContextCategory.CALLERS, percentage=10, max_tokens=1000),
                BudgetCategory(category=ContextCategory.RELATED_TESTS, percentage=10, max_tokens=1000),
                BudgetCategory(category=ContextCategory.RUN_HISTORY, percentage=10, max_tokens=1000),
                BudgetCategory(category=ContextCategory.REPOSITORY_MEMORY, percentage=10, max_tokens=1000),
            ]
        elif agent_type == "coding":
            budget.categories = [
                BudgetCategory(category=ContextCategory.TASK, percentage=10, max_tokens=1000),
                BudgetCategory(category=ContextCategory.PRIMARY_CODE, percentage=35, max_tokens=3000),
                BudgetCategory(category=ContextCategory.DEPENDENCIES, percentage=15, max_tokens=1500),
                BudgetCategory(category=ContextCategory.CALLERS, percentage=10, max_tokens=1000),
                BudgetCategory(category=ContextCategory.CALLEES, percentage=10, max_tokens=1000),
                BudgetCategory(category=ContextCategory.CODE_CHUNKS, percentage=10, max_tokens=1000),
                BudgetCategory(category=ContextCategory.GRAPH_EVIDENCE, percentage=10, max_tokens=1000),
            ]
        elif agent_type == "test":
            budget.categories = [
                BudgetCategory(category=ContextCategory.TASK, percentage=10, max_tokens=1000),
                BudgetCategory(category=ContextCategory.RELATED_TESTS, percentage=30, max_tokens=2500),
                BudgetCategory(category=ContextCategory.PRIMARY_CODE, percentage=20, max_tokens=2000),
                BudgetCategory(category=ContextCategory.DEPENDENCIES, percentage=10, max_tokens=1000),
                BudgetCategory(category=ContextCategory.PREVIOUS_FAILURES, percentage=10, max_tokens=1000),
                BudgetCategory(category=ContextCategory.GRAPH_EVIDENCE, percentage=10, max_tokens=1000),
                BudgetCategory(category=ContextCategory.RUN_HISTORY, percentage=10, max_tokens=1000),
            ]
        elif agent_type == "repair":
            budget.categories = [
                BudgetCategory(category=ContextCategory.TASK, percentage=10, max_tokens=1000),
                BudgetCategory(category=ContextCategory.PREVIOUS_FAILURES, percentage=20, max_tokens=2000),
                BudgetCategory(category=ContextCategory.PREVIOUS_REPAIRS, percentage=15, max_tokens=1500),
                BudgetCategory(category=ContextCategory.PRIMARY_CODE, percentage=20, max_tokens=2000),
                BudgetCategory(category=ContextCategory.DEPENDENCIES, percentage=10, max_tokens=1000),
                BudgetCategory(category=ContextCategory.CALLERS, percentage=10, max_tokens=1000),
                BudgetCategory(category=ContextCategory.RELATED_TESTS, percentage=10, max_tokens=1000),
                BudgetCategory(category=ContextCategory.WARNINGS, percentage=5, max_tokens=500),
            ]
        elif agent_type == "reviewer":
            budget.categories = [
                BudgetCategory(category=ContextCategory.TASK, percentage=10, max_tokens=1000),
                BudgetCategory(category=ContextCategory.IMPLEMENTATION_PLAN, percentage=15, max_tokens=1500),
                BudgetCategory(category=ContextCategory.PRIMARY_CODE, percentage=20, max_tokens=2000),
                BudgetCategory(category=ContextCategory.RELATED_TESTS, percentage=10, max_tokens=1000),
                BudgetCategory(category=ContextCategory.DEPENDENCIES, percentage=10, max_tokens=1000),
                BudgetCategory(category=ContextCategory.CALLERS, percentage=5, max_tokens=500),
                BudgetCategory(category=ContextCategory.REVIEW_FINDINGS, percentage=10, max_tokens=1000),
                BudgetCategory(category=ContextCategory.PREVIOUS_REPAIRS, percentage=10, max_tokens=1000),
                BudgetCategory(category=ContextCategory.RUN_HISTORY, percentage=5, max_tokens=500),
                BudgetCategory(category=ContextCategory.WARNINGS, percentage=5, max_tokens=500),
            ]
        else:
            # Default allocation for unknown agent types
            budget.categories = [
                BudgetCategory(category=ContextCategory.TASK, percentage=15, max_tokens=1500),
                BudgetCategory(category=ContextCategory.PRIMARY_CODE, percentage=30, max_tokens=3000),
                BudgetCategory(category=ContextCategory.DEPENDENCIES, percentage=15, max_tokens=1500),
                BudgetCategory(category=ContextCategory.GRAPH_EVIDENCE, percentage=15, max_tokens=1500),
                BudgetCategory(category=ContextCategory.RUN_HISTORY, percentage=10, max_tokens=1000),
                BudgetCategory(category=ContextCategory.WARNINGS, percentage=5, max_tokens=500),
            ]

        # Phase 15: ensure cross-agent notes always have a guaranteed
        # allocation (otherwise they'd fall back to the generic 10% cap
        # and could be dropped under token contention).
        budget.categories = [
            *budget.categories,
            BudgetCategory(
                category=ContextCategory.AGENT_NOTES,
                percentage=15,
                max_tokens=800,
            ),
            BudgetCategory(
                category=ContextCategory.AGENT_HANDOFF,
                percentage=20,
                max_tokens=1200,
            ),
        ]

        budget.priority_order = [
            ContextCategory.TASK,
            ContextCategory.REPOSITORY_SUMMARY,
            ContextCategory.IMPLEMENTATION_PLAN,
            ContextCategory.PRIMARY_CODE,
            ContextCategory.DEPENDENCIES,
            ContextCategory.CALLERS,
            ContextCategory.CALLEES,
            ContextCategory.RELATED_TESTS,
            ContextCategory.CODE_CHUNKS,
            ContextCategory.GRAPH_EVIDENCE,
            ContextCategory.PREVIOUS_FAILURES,
            ContextCategory.PREVIOUS_REPAIRS,
            ContextCategory.REVIEW_FINDINGS,
            ContextCategory.REPOSITORY_MEMORY,
            ContextCategory.RUN_HISTORY,
            ContextCategory.CONSTRAINTS,
            ContextCategory.WARNINGS,
            ContextCategory.AGENT_NOTES,
            ContextCategory.AGENT_HANDOFF,
        ]

        return budget


# ── Agent Context ───────────────────────────────────────────────


class ContextMetrics(BaseModel):
    """Quality and deduplication metrics for a context build."""

    candidates_considered: int = Field(default=0)
    items_selected: int = Field(default=0)
    duplicates_removed: int = Field(default=0)
    tokens_before: int = Field(default=0)
    tokens_after: int = Field(default=0)

    graph_items: int = Field(default=0)
    rag_items: int = Field(default=0)
    memory_items: int = Field(default=0)
    run_history_items: int = Field(default=0)
    test_failure_items: int = Field(default=0)
    repair_history_items: int = Field(default=0)
    plan_items: int = Field(default=0)
    cross_agent_items: int = Field(default=0)
    handoff_items: int = Field(default=0)

    def dict_summary(self) -> Dict[str, Any]:
        """Return a compact summary for logging and API responses."""
        return {
            "candidates": self.candidates_considered,
            "selected": self.items_selected,
            "duplicates_removed": self.duplicates_removed,
            "estimated_tokens": {
                "before": self.tokens_before,
                "after": self.tokens_after,
            },
            "by_source": {
                "graph": self.graph_items,
                "rag": self.rag_items,
                "memory": self.memory_items,
                "run_history": self.run_history_items,
                "test_failure": self.test_failure_items,
                "repair_history": self.repair_history_items,
                "plan": self.plan_items,
                "cross_agent": self.cross_agent_items,
            },
        }


class AgentContext(BaseModel):
    """Complete context for an agent, assembled by the ContextEngine.

    This is the canonical input that agents receive — replacing
    ad-hoc context assembly in each agent's prompt builder.
    Agents should NOT independently assemble arbitrary repository context.
    """

    # ── Task ───────────────────────────────────────────────────
    task: str = Field(default="", description="The task description / requirements")
    agent_type: str = Field(default="", description="Which agent this context is for")

    # ── Repository ─────────────────────────────────────────────
    repository_path: Optional[str] = Field(default=None)
    repository_summary: str = Field(default="", description="Languages, frameworks, structure")

    # ── Primary Code Evidence ──────────────────────────────────
    primary_symbols: str = Field(default="", description="Primary symbols and their implementations")
    related_symbols: str = Field(default="", description="Neighboring/related symbols")

    # ── Dependencies ───────────────────────────────────────────
    dependencies: str = Field(default="", description="What primary symbols depend on")
    callers: str = Field(default="", description="What calls the primary symbols")
    callees: str = Field(default="", description="What the primary symbols call")

    # ── Code Chunks ────────────────────────────────────────────
    relevant_files: str = Field(default="", description="List of relevant file paths")
    code_chunks: str = Field(default="", description="Code chunks from Phase 5 RAG")

    # ── Tests ──────────────────────────────────────────────────
    related_tests: str = Field(default="", description="Related test files and symbols")

    # ── Plan ───────────────────────────────────────────────────
    implementation_plan: str = Field(default="", description="The implementation plan (if available)")

    # ── Previous Attempts ──────────────────────────────────────
    previous_failures: str = Field(default="", description="Failures from previous runs")
    previous_repairs: str = Field(default="", description="Repair history")
    review_findings: str = Field(default="", description="Previous review findings")

    # ── Memory ─────────────────────────────────────────────────
    repository_memory: str = Field(default="", description="Relevant repository knowledge memory")
    historical_memory: str = Field(default="", description="Historical run memory")

    # ── Constraints ────────────────────────────────────────────
    constraints: str = Field(default="", description="Task/repository constraints")
    warnings: str = Field(default="", description="Warnings about context quality")

    # ── Cross-Agent Notes ──────────────────────────────────────
    agent_notes: str = Field(
        default="",
        description="Shared notes from prior agents in the same run (cross-agent context sharing)",
    )
    agent_handoffs: str = Field(
        default="",
        description="Structured handoffs from prior agents (Phase 15 collaboration evidence)",
    )

    # ── Token Budget ───────────────────────────────────────────
    budget: ContextBudget = Field(default_factory=ContextBudget)
    metrics: ContextMetrics = Field(default_factory=ContextMetrics)

    # ── Internal: raw items for context building pipeline ──────
    raw_items: List[ContextItem] = Field(default_factory=list, exclude=True)

    def build_prompt_section(self) -> str:
        """Format the context into a structured prompt section.

        Returns a markdown-formatted string with clear provenance
        labels for each section.
        """
        sections = []

        sections.append(f"=== TASK ===\n{self.task}\n")

        if self.repository_summary:
            sections.append(f"=== REPOSITORY ===\n{self.repository_summary}\n")

        if self.implementation_plan:
            sections.append(f"=== IMPLEMENTATION PLAN ===\n{self.implementation_plan}\n")

        if self.primary_symbols:
            sections.append(f"=== PRIMARY SYMBOLS ===\n{self.primary_symbols}\n")

        if self.related_symbols:
            sections.append(f"=== RELATED SYMBOLS ===\n{self.related_symbols}\n")

        if self.dependencies:
            sections.append(f"=== DEPENDENCIES ===\n{self.dependencies}\n")

        if self.callers:
            sections.append(f"=== CALLERS ===\n{self.callers}\n")

        if self.callees:
            sections.append(f"=== CALLEES ===\n{self.callees}\n")

        if self.code_chunks:
            sections.append(f"=== CODE CHUNKS (Phase 5 RAG) ===\n{self.code_chunks}\n")

        if self.related_tests:
            sections.append(f"=== RELATED TESTS ===\n{self.related_tests}\n")

        if self.previous_failures:
            sections.append(f"=== PREVIOUS FAILURES ===\n{self.previous_failures}\n")

        if self.previous_repairs:
            sections.append(f"=== PREVIOUS REPAIRS ===\n{self.previous_repairs}\n")

        if self.review_findings:
            sections.append(f"=== REVIEW FINDINGS ===\n{self.review_findings}\n")

        if self.repository_memory:
            sections.append(f"=== REPOSITORY MEMORY ===\n{self.repository_memory}\n")

        if self.historical_memory:
            sections.append(f"=== HISTORICAL RUNS ===\n{self.historical_memory}\n")

        if self.constraints:
            sections.append(f"=== CONSTRAINTS ===\n{self.constraints}\n")

        if self.warnings:
            sections.append(f"=== CONTEXT WARNINGS ===\n{self.warnings}\n")

        if self.agent_notes:
            sections.append(f"=== PRIOR AGENT NOTES (CROSS-AGENT) ===\n{self.agent_notes}\n")

        if self.agent_handoffs:
            sections.append(f"=== PRIOR AGENT HANDOFFS (COLLABORATION) ===\n{self.agent_handoffs}\n")

        if self.relevant_files:
            sections.append(f"=== RELEVANT FILES ===\n{self.relevant_files}\n")

        return "\n".join(sections)
