"""
Phase 21 — Run Replay & Deterministic Reproduction models.

A ReplayManifest is a durable, bounded snapshot of everything that happened
during an autonomous engineering run:

- repository state (path + content fingerprint + git HEAD)
- run configuration (provider chain, capability version, stage sequence)
- stage sequence + per-stage classification (deterministic / LLM-proposed /
  observational) with stable content hashes and bounded decision snapshots
- deterministic decisions (gate, patch validation, patch application, tests,
  handoff validation, consensus, contradictions, repository scope)
- agent handoffs (bounded summaries)
- reasoning / consensus summaries
- graph / memory versions consulted

Security invariant (unchanged from Phases 15-17): the manifest exposes ONLY
evidence, decisions, confidence and consensus — never chain-of-thought,
hidden prompts, or internal reasoning. Text is bounded; large outputs are
represented by content hashes (the authoritative content stays in the
RunStore context round-trip).

Replay never calls an LLM. LLMs PROPOSE, deterministic systems DECIDE. The
replay engine re-executes only deterministic stages from the recorded
evidence and classifies the outcome:

- MATCH      — every replayed stage produced an identical result
- DRIFT      — at least one replayed stage diverged (with the deterministic
               decision that caused it)
- INVALID    — the run / manifest / mode is unusable
- INCOMPLETE — replay ran but some stages could not be re-executed from the
               recorded evidence
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.models.base import new_id


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Bounds (Safety & Performance) ───────────────────────────────
# A manifest must never grow unboundedly: snapshots are bounded, heavy
# content is represented by hashes only.

MAX_STAGES_PER_MANIFEST = 64
MAX_DECISIONS_PER_MANIFEST = 200
MAX_HANDOFFS_PER_MANIFEST = 50
MAX_CONSENSUS_PER_MANIFEST = 50
MAX_CHECKS_PER_REPLAY = 100
MAX_COMPARISONS_PER_REPLAY = 64
MAX_FINGERPRINT_FILES = 5000
SUMMARY_MAX_LEN = 500
CLAIM_MAX_LEN = 300


# ── Enums ───────────────────────────────────────────────────────


class ReplayMode(str, Enum):
    """How a replay is executed."""

    EXACT = "exact"
    """Re-execute deterministic stages offline from recorded evidence (no
    workspace, no tests, no LLM). Answers: does the recorded evidence still
    reproduce the recorded decisions?"""

    DETERMINISTIC = "deterministic"
    """EXACT plus live workspace verification (repository fingerprint, patch
    application outcome, test re-execution). Answers: can the engineering
    result be reproduced from the recorded evidence on the current code?"""

    COMPARE = "compare"
    """Compare two runs (or a run vs its manifest) stage by stage. Answers:
    which stages produced identical results, and which decision diverged?"""


class ReplayVerdict(str, Enum):
    """Classification of a replay outcome."""

    MATCH = "match"          # every replayed stage identical
    DRIFT = "drift"          # at least one replayed stage diverged
    INVALID = "invalid"      # run/manifest/mode unusable
    INCOMPLETE = "incomplete"  # replay ran, but some stages could not be re-executed


class ReplayStageKind(str, Enum):
    """How a stage's output is treated by replay."""

    DETERMINISTIC = "deterministic"
    """Re-executable without an LLM (patch validation, patch application,
    testing, quality gate, handoff validation, consensus, contradictions)."""

    LLM_PROPOSED = "llm_proposed"
    """Recorded proposal, never re-executed (planning, coding, repair,
    review). Deterministic gates decide on these proposals."""

    OBSERVATIONAL = "observational"
    """Captured evidence (analysis, retrieval, acquisition, graph/memory
    ingestion). Audited via hashes, not re-executed."""


class ReplayCheckStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    NOT_REPLAYABLE = "not_replayable"


# ── Repository State ────────────────────────────────────────────


class RepositoryState(BaseModel):
    """Captured repository state at run time (audit evidence)."""

    path: str = Field(default="", max_length=1024)
    fingerprint: str = Field(
        default="",
        description="SHA-256 over the bounded file manifest "
                    "(sorted relative paths + sizes + git HEAD)",
    )
    git_head: Optional[str] = Field(default=None, max_length=64)
    file_count: int = Field(default=0, ge=0)
    changed_files: List[str] = Field(
        default_factory=list,
        description="Paths the run's patch touched (deterministic evidence)",
    )

    def summary(self) -> Dict[str, Any]:
        return {
            "path": self.path[:200],
            "fingerprint": self.fingerprint[:24],
            "git_head": self.git_head,
            "file_count": self.file_count,
            "changed_files": self.changed_files[:20],
        }


# ── Stage Records ───────────────────────────────────────────────


class ReplayStageRecord(BaseModel):
    """One stage in the replay manifest."""

    stage: str = Field(description="StageType value")
    kind: ReplayStageKind = Field(description="How replay treats this stage")
    status: str = Field(default="", description="Recorded StageStatus value")
    output_hash: str = Field(
        default="",
        description="SHA-256 of the sanitized stage output payload",
    )
    decision: Dict[str, Any] = Field(
        default_factory=dict,
        description="Bounded decision snapshot (gate verdict, test counts, "
                    "validation errors — never raw LLM output)",
    )
    captured: bool = Field(
        default=False,
        description="True when deterministic inputs were fully recorded and "
                    "the stage is re-executable",
    )
    notes: List[str] = Field(default_factory=list, max_length=10)

    def summary(self) -> Dict[str, Any]:
        return {
            "stage": self.stage,
            "kind": self.kind.value,
            "status": self.status,
            "output_hash": self.output_hash[:24],
            "decision": self.decision,
            "captured": self.captured,
        }


# ── Deterministic Decisions ─────────────────────────────────────


class ReplayDecisionRecord(BaseModel):
    """A recorded deterministic decision that replay can re-derive."""

    decision_type: str = Field(description="gate | patch_validation | "
                                           "patch_application | testing | "
                                           "handoff | consensus | "
                                           "contradiction | repository_scope")
    statement: str = Field(default="", max_length=CLAIM_MAX_LEN)
    made_by: str = Field(default="")
    value: str = Field(
        default="",
        description="Canonical decision value (e.g. gate decision, "
                    "is_valid, handoff status)",
    )
    replayable: bool = Field(
        default=True,
        description="False for LLM-proposed decisions (plan, patch, review)",
    )
    matched: Optional[bool] = Field(
        default=None,
        description="Filled at replay time: did re-execution reproduce this?",
    )

    def summary(self) -> Dict[str, Any]:
        return {
            "decision_type": self.decision_type,
            "statement": self.statement[:160],
            "made_by": self.made_by,
            "value": self.value[:160],
            "replayable": self.replayable,
            "matched": self.matched,
        }


# ── Manifest ────────────────────────────────────────────────────


class ReplayManifest(BaseModel):
    """Durable snapshot of a run for replay and audit."""

    manifest_id: str = Field(default_factory=lambda: f"RPL-{new_id().upper()[:8]}")
    run_id: str = Field(description="Owning run")
    source_run_status: str = Field(default="", description="RunStatus value")
    created_at: str = Field(default_factory=_utcnow_iso)

    repository_state: RepositoryState = Field(default_factory=RepositoryState)
    run_config: Dict[str, Any] = Field(
        default_factory=dict,
        description="Provider chain + capability version + stage sequence "
                    "(what decided, not how it reasoned)",
    )

    stage_sequence: List[str] = Field(default_factory=list, max_length=MAX_STAGES_PER_MANIFEST)
    stages: List[ReplayStageRecord] = Field(
        default_factory=list, max_length=MAX_STAGES_PER_MANIFEST
    )
    stage_output_hashes: Dict[str, str] = Field(
        default_factory=dict,
        description="stage -> output hash (stable, bounded)",
    )

    deterministic_decisions: List[ReplayDecisionRecord] = Field(
        default_factory=list, max_length=MAX_DECISIONS_PER_MANIFEST
    )
    agent_handoffs: List[Dict[str, Any]] = Field(
        default_factory=list, max_length=MAX_HANDOFFS_PER_MANIFEST
    )
    reasoning: Dict[str, Any] = Field(
        default_factory=dict,
        description="consensus / contradiction / confidence summaries "
                    "(evidence-only)",
    )
    graph_memory_versions: Dict[str, Any] = Field(
        default_factory=dict,
        description="EKG graph_version + repository memory counts consulted",
    )

    version: int = Field(default=1, description="Manifest schema version")

    def content_hash(self) -> str:
        """Hash the replay-relevant content (excludes volatile ids/timestamps).

        Two manifests built from the same run state must hash identically;
        a run mutated after capture must produce a different hash. This is
        the tamper-detection basis of EXACT replay.
        """
        import hashlib
        import json

        payload = {
            "run_id": self.run_id,
            "source_run_status": self.source_run_status,
            "repository_state": self.repository_state.model_dump(mode="json"),
            "run_config": self.run_config,
            "stage_sequence": self.stage_sequence,
            "stages": [s.model_dump(mode="json") for s in self.stages],
            "stage_output_hashes": self.stage_output_hashes,
            "deterministic_decisions": [
                d.model_dump(mode="json") for d in self.deterministic_decisions
            ],
            "agent_handoffs": self.agent_handoffs,
            "reasoning": self.reasoning,
            "graph_memory_versions": self.graph_memory_versions,
        }
        raw = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def summary(self) -> Dict[str, Any]:
        return {
            "manifest_id": self.manifest_id,
            "run_id": self.run_id,
            "source_run_status": self.source_run_status,
            "created_at": self.created_at,
            "repository_state": self.repository_state.summary(),
            "stage_count": len(self.stages),
            "decision_count": len(self.deterministic_decisions),
            "handoffs": len(self.agent_handoffs),
            "consensus": len(self.reasoning.get("consensus", [])),
            "contradictions": len(self.reasoning.get("contradictions", [])),
            "content_hash": self.content_hash()[:24],
            "version": self.version,
        }


# ── Replay Checks & Results ─────────────────────────────────────


class ReplayCheck(BaseModel):
    """One deterministic re-execution check inside a replay."""

    stage: str = Field(default="", description="Stage the check targets")
    check: str = Field(description="Check name")
    status: ReplayCheckStatus = Field(default=ReplayCheckStatus.SKIPPED)
    expected: str = Field(default="")
    actual: str = Field(default="")
    note: str = Field(default="", max_length=SUMMARY_MAX_LEN)

    @property
    def passed(self) -> bool:
        return self.status == ReplayCheckStatus.PASSED


class ReplayStageComparison(BaseModel):
    """Per-stage result of a COMPARE-mode replay."""

    stage: str = Field(description="StageType value")
    kind: str = Field(default="")
    recorded_hash: str = Field(default="")
    replay_hash: str = Field(default="")
    matched: Optional[bool] = Field(default=None)
    detail: str = Field(default="", max_length=SUMMARY_MAX_LEN)


class ReplayResult(BaseModel):
    """Outcome of one replay execution."""

    replay_id: str = Field(default_factory=lambda: f"REP-{new_id().upper()[:8]}")
    run_id: str = Field(description="Owning run")
    mode: ReplayMode = Field(description="How the replay was executed")
    verdict: ReplayVerdict = Field(default=ReplayVerdict.INCOMPLETE)

    checks: List[ReplayCheck] = Field(
        default_factory=list, max_length=MAX_CHECKS_PER_REPLAY
    )
    stage_comparisons: List[ReplayStageComparison] = Field(
        default_factory=list, max_length=MAX_COMPARISONS_PER_REPLAY
    )
    divergences: List[str] = Field(
        default_factory=list, max_length=50,
        description="Human-readable divergence explanations (bounded)",
    )

    summary: str = Field(default="", max_length=SUMMARY_MAX_LEN)
    created_at: str = Field(default_factory=_utcnow_iso)

    def summary_dict(self) -> Dict[str, Any]:
        return {
            "replay_id": self.replay_id,
            "run_id": self.run_id,
            "mode": self.mode.value,
            "verdict": self.verdict.value,
            "checks_total": len(self.checks),
            "checks_passed": sum(1 for c in self.checks if c.passed),
            "checks_failed": sum(
                1 for c in self.checks if c.status == ReplayCheckStatus.FAILED
            ),
            "checks_skipped": sum(
                1 for c in self.checks if c.status == ReplayCheckStatus.SKIPPED
            ),
            "checks_not_replayable": sum(
                1 for c in self.checks
                if c.status == ReplayCheckStatus.NOT_REPLAYABLE
            ),
            "stages_matched": sum(
                1 for c in self.stage_comparisons if c.matched is True
            ),
            "stages_diverged": sum(
                1 for c in self.stage_comparisons if c.matched is False
            ),
            "divergences": self.divergences[:10],
            "summary": self.summary[:300],
            "created_at": self.created_at,
        }


__all__ = [
    "ReplayMode",
    "ReplayVerdict",
    "ReplayStageKind",
    "ReplayCheckStatus",
    "RepositoryState",
    "ReplayStageRecord",
    "ReplayDecisionRecord",
    "ReplayManifest",
    "ReplayCheck",
    "ReplayStageComparison",
    "ReplayResult",
]
