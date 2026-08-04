"""
Phase 5 Demonstration Script — Index and search the fixture_auth_app.

Run from the backend directory:
    python scripts/demo_phase5.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.index_builder import RepositoryIndexBuilder
from app.rag.retrieval.hybrid_retriever import HybridRetriever
from app.rag.retrieval.plan_context_retriever import PlanContextRetriever
from app.models.rag import RetrievalQuery
from app.models.issues import ImplementationPlan, ImplementationStep


async def main():
    repo = str(Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "fixture_auth_app")

    print("=" * 70)
    print("  PHASE 5 DEMONSTRATION — Code-Aware Indexing & Hybrid Retrieval")
    print("=" * 70)
    print(f"  Repository: {Path(repo).name}")
    print()

    # ── Step 1: Build Index ──────────────────────────────────
    print("[1/4] Building repository code index...")
    builder = RepositoryIndexBuilder(max_files_to_index=50)
    code_index, lex_idx, sym_idx, vec_idx = builder.build_with_indexes(repo)

    print(f"       Files indexed: {code_index.statistics.files_indexed}")
    print(f"       Symbols:       {code_index.statistics.symbols_extracted}")
    print(f"       Chunks:        {code_index.statistics.chunks_created}")
    print(f"       Duration:      {code_index.statistics.duration_seconds:.3f}s")
    print()

    # Create retriever
    retriever = HybridRetriever(
        lexical_index=lex_idx,
        symbol_index=sym_idx,
        vector_index=vec_idx,
    )
    retriever.set_indexes(lex_idx, sym_idx, vec_idx, code_index.chunks)

    # ── Step 2: Query 1 — Auth/Token Related ─────────────────
    print("[2/4] Query: 'password reset token expiration'")
    print("-" * 70)
    query = RetrievalQuery(text="password reset token expiration", top_k=6)
    result = retriever.retrieve(query)

    for i, item in enumerate(result.items, 1):
        chunk = item.chunk
        print(f"  [{i}] Score: {item.score:.4f}")
        print(f"      File: {chunk.file_path}:L{chunk.start_line}-L{chunk.end_line}")
        if chunk.symbol_name:
            print(f"      Symbol: {chunk.symbol_name} ({chunk.symbol_kind.value})")
        print(f"      Breakdown: lex={item.lexical_score:.3f} sem={item.semantic_score:.3f} sym={item.symbol_score:.3f} str={item.structural_score:.3f}")
        for r in item.reasons:
            print(f"      -> {r}")
    print()

    # ── Step 3: Query 2 — Unrelated Query ────────────────────
    print("[3/4] Query: 'product pricing catalog'")
    print("-" * 70)
    query2 = RetrievalQuery(text="product pricing catalog", top_k=4)
    result2 = retriever.retrieve(query2)

    for i, item in enumerate(result2.items, 1):
        chunk = item.chunk
        print(f"  [{i}] Score: {item.score:.4f}")
        print(f"      File: {chunk.file_path}:L{chunk.start_line}-L{chunk.end_line}")
        if chunk.symbol_name:
            print(f"      Symbol: {chunk.symbol_name} ({chunk.symbol_kind.value})")
        print(f"      Breakdown: lex={item.lexical_score:.3f} sem={item.semantic_score:.3f} sym={item.symbol_score:.3f} str={item.structural_score:.3f}")
        for r in item.reasons:
            print(f"      -> {r}")
    print()

    # ── Step 4: Phase 4 Integration (Plan-Aware Retrieval) ────
    print("[4/4] Plan-Aware Retrieval (Phase 4 Integration)")
    print("-" * 70)

    plan = ImplementationPlan(
        summary="Fix password reset token expiration",
        objective="Add expiration validation to password reset tokens",
        steps=[
            ImplementationStep(
                id="STEP-001",
                title="Add token expiration field and validation to auth service",
                description="Extend the authentication service to include token expiration validation. The token model should have an expires_at field, and validate_token should check it.",
                affected_areas=["auth", "tokens", "service"],
                expected_changes="Add expires_at to TokenManager, update validate_reset_token",
            ),
            ImplementationStep(
                id="STEP-002",
                title="Update password reset route with expiration check",
                description="Add expiration check in the password reset API endpoint before allowing password reset.",
                affected_areas=["routes", "api", "auth"],
                expected_changes="Add token expiry validation before password reset",
            ),
            ImplementationStep(
                id="STEP-003",
                title="Add tests for token expiration behavior",
                description="Write tests verifying expired tokens are rejected and non-expired tokens pass validation.",
                affected_areas=["tests", "auth"],
                expected_changes="New test cases for token expiry",
            ),
        ],
        test_strategy="Unit tests for TokenManager covering valid, expired, and used tokens",
    )

    plan_retriever = PlanContextRetriever(index_builder=builder, retriever=retriever)
    plan_retriever.retriever.set_indexes(lex_idx, sym_idx, vec_idx, code_index.chunks)

    plan_result = await plan_retriever.retrieve_for_plan(
        plan=plan,
        repository_path=repo,
        top_k_per_step=4,
    )

    for step_ctx in plan_result.steps:
        print(f"  Step {step_ctx.step_id}: {step_ctx.step_title}")
        print(f"  Query: {step_ctx.query[:90]}...")
        for item in step_ctx.context.items:
            chunk = item.chunk
            print(f"    Score={item.score:.4f} {chunk.file_path}:L{chunk.start_line}-L{chunk.end_line}")
            if chunk.symbol_name:
                print(f"    Symbol: {chunk.symbol_name}")
            for r in item.reasons[:2]:
                print(f"    -> {r}")
        print()

    print("=" * 70)
    print("  DEMONSTRATION COMPLETE")
    print("  No code was modified — read-only indexing and retrieval only.")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
