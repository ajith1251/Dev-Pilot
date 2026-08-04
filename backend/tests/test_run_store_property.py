"""Property-based tests for RunStore count_runs using Hypothesis.

Generates random sets of runs with various statuses and timestamps,
then verifies count_runs() and count_runs_by_status() invariants.

Uses InMemoryRunStore which is deterministic and async-compatible.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional, Tuple

from hypothesis import assume, given, settings, strategies as st

from app.models.orchestration import RunStatus
from app.services.run_store import InMemoryRunStore

from tests.test_run_store_contract import make_run


# ---------------------------------------------------------------------------
#  Data Generators (Hypothesis Strategies)
# ---------------------------------------------------------------------------

# All valid run statuses as strings
ALL_STATUSES: List[str] = [
    "pending", "running", "approved", "rejected",
    "needs_human_review", "failed", "cancelled",
]

# Strategy: a single run status
status_strategy = st.sampled_from(ALL_STATUSES)

# Strategy: a UTC timestamp string within a 2-year window
timestamp_strategy = st.datetimes(
    min_value=datetime(2025, 1, 1),
    max_value=datetime(2026, 12, 31, 23, 59, 59),
).map(lambda dt: dt.isoformat() + "Z")

# Strategy: a list of (status, created_at) tuples representing runs
runs_strategy = st.lists(
    st.tuples(status_strategy, timestamp_strategy),
    min_size=0,
    max_size=50,
)

# Strategy: an optional status filter
optional_status: st.SearchStrategy = st.one_of(st.none(), status_strategy)

# Strategy: an optional timestamp boundary
optional_timestamp: st.SearchStrategy = st.one_of(st.none(), timestamp_strategy)


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------


async def seed_store(
    store: InMemoryRunStore,
    runs: List[Tuple[str, str]],
) -> None:
    """Seed the store with a list of (status, created_at) tuples."""
    for status_str, created_at in runs:
        run = make_run()
        run.status = RunStatus(status_str)
        run.created_at = created_at
        await store.create(run)


def manual_count(
    runs: List[Tuple[str, str]],
    *,
    status: Optional[str] = None,
    after: Optional[str] = None,
    before: Optional[str] = None,
) -> int:
    """Count runs matching the given filters using plain Python iteration.

    Mirrors the InMemoryRunStore.count_runs filtering logic so the
    property-based test can compare store counts against a reference.
    """
    count = 0
    for s, ts in runs:
        if status is not None and s != status:
            continue
        # Half-open interval semantics (mirrors InMemoryRunStore + the API
        # contract): created_after inclusive, created_before exclusive.
        if after is not None and ts < after:
            continue
        if before is not None and ts >= before:
            continue
        count += 1
    return count


def make_counts_dict(
    runs: List[Tuple[str, str]],
) -> Dict[str, int]:
    """Compute the full status-count dict that count_runs_by_status should return.

    Returns a dict with the same 8 keys as the store implementation.
    """
    counts: Dict[str, int] = {
        "total": len(runs),
        "pending": 0, "running": 0, "approved": 0,
        "rejected": 0, "needs_human_review": 0, "failed": 0, "cancelled": 0,
    }
    for s, _ in runs:
        if s in counts:
            counts[s] += 1
    return counts


# ---------------------------------------------------------------------------
#  Property Tests
# ---------------------------------------------------------------------------


class TestCountRunsProperty:
    """Hypothesis property-based tests for count_runs and count_runs_by_status.

    Each test creates a fresh InMemoryRunStore, seeds it with generated data,
    and verifies that count invariants hold.
    """

    # ...........................................................................
    #  Invariant: total count = number of seeded runs
    # ...........................................................................

    @given(runs_strategy)
    @settings(max_examples=100)
    async def test_total_count_matches_number_of_runs(
        self,
        runs: List[Tuple[str, str]],
    ) -> None:
        """count_runs() without filters should equal the number of created runs."""
        store = InMemoryRunStore()
        await seed_store(store, runs)
        expected = len(runs)
        actual = await store.count_runs()
        assert actual == expected, (
            f"Expected total count {expected}, got {actual} for {len(runs)} runs"
        )

    # ...........................................................................
    #  Invariant: each status filter count matches manual count
    # ...........................................................................

    @given(runs_strategy)
    @settings(max_examples=100)
    async def test_status_filter_counts_match_manual(
        self,
        runs: List[Tuple[str, str]],
    ) -> None:
        """For each status, count_runs(status=X) should equal a manual filter."""
        store = InMemoryRunStore()
        await seed_store(store, runs)

        for s in ALL_STATUSES:
            expected = manual_count(runs, status=s)
            actual = await store.count_runs(status=s)
            assert actual == expected, (
                f"Status '{s}': expected {expected}, got {actual} "
                f"(total runs: {len(runs)})"
            )

    # ...........................................................................
    #  Invariant: date range counts match manual count
    # ...........................................................................

    @given(runs_strategy, optional_timestamp, optional_timestamp)
    @settings(max_examples=100)
    async def test_date_range_counts_match_manual(
        self,
        runs: List[Tuple[str, str]],
        after: Optional[str],
        before: Optional[str],
    ) -> None:
        """count_runs with date filters should match manual filter."""
        assume(after is None or before is None or after <= before)

        store = InMemoryRunStore()
        await seed_store(store, runs)

        expected = manual_count(runs, after=after, before=before)
        actual = await store.count_runs(created_after=after, created_before=before)
        assert actual == expected, (
            f"Date after={after}, before={before}: "
            f"expected {expected}, got {actual}"
        )

    # ...........................................................................
    #  Invariant: combined status + date filters match manual
    # ...........................................................................

    @given(runs_strategy, optional_status, optional_timestamp, optional_timestamp)
    @settings(max_examples=200)
    async def test_combined_filters_match_manual(
        self,
        runs: List[Tuple[str, str]],
        status_filter: Optional[str],
        after: Optional[str],
        before: Optional[str],
    ) -> None:
        """Combined status + date filters should match manual intersection."""
        assume(after is None or before is None or after <= before)

        store = InMemoryRunStore()
        await seed_store(store, runs)

        expected = manual_count(runs, status=status_filter, after=after, before=before)
        actual = await store.count_runs(
            status=status_filter,
            created_after=after,
            created_before=before,
        )
        assert actual == expected, (
            f"Combined status={status_filter}, after={after}, before={before}: "
            f"expected {expected}, got {actual} (total runs: {len(runs)})"
        )

    # ...........................................................................
    #  Invariant: nonexistent status returns 0
    # ...........................................................................

    @given(runs_strategy)
    @settings(max_examples=100)
    async def test_nonexistent_status_returns_zero(
        self,
        runs: List[Tuple[str, str]],
    ) -> None:
        """Querying a status that doesn't exist among the runs returns 0."""
        store = InMemoryRunStore()
        await seed_store(store, runs)

        used_statuses = {s for s, _ in runs}
        unused = [s for s in ALL_STATUSES if s not in used_statuses]

        for s in unused:
            actual = await store.count_runs(status=s)
            assert actual == 0, (
                f"Unused status '{s}' should be 0, got {actual}"
            )

    # ...........................................................................
    #  Invariant: count_by_status.total == sum(statuses)
    # ...........................................................................

    @given(runs_strategy)
    @settings(max_examples=100)
    async def test_count_by_status_total_equals_sum(
        self,
        runs: List[Tuple[str, str]],
    ) -> None:
        """count_runs_by_status().total must equal sum of individual status counts."""
        store = InMemoryRunStore()
        await seed_store(store, runs)

        counts = await store.count_runs_by_status()
        expected = make_counts_dict(runs)

        assert counts == expected, (
            f"Status counts mismatch\n"
            f"  Expected: {expected}\n"
            f"  Got:      {counts}\n"
            f"  Runs:     {[(s, t[:10]) for s, t in runs]}"
        )

    # ...........................................................................
    #  Invariant: count_by_status.total == count_runs()
    # ...........................................................................

    @given(runs_strategy)
    @settings(max_examples=100)
    async def test_count_by_status_total_matches_count_runs(
        self,
        runs: List[Tuple[str, str]],
    ) -> None:
        """The total in count_runs_by_status should equal count_runs()."""
        store = InMemoryRunStore()
        await seed_store(store, runs)

        total = await store.count_runs()
        by_status = await store.count_runs_by_status()

        assert by_status["total"] == total, (
            f"count_by_status.total={by_status['total']} != "
            f"count_runs()={total}"
        )

    # ...........................................................................
    #  Invariant: status-filtered count <= total count
    # ...........................................................................

    @given(runs_strategy)
    @settings(max_examples=100)
    async def test_status_filter_is_subset_of_total(
        self,
        runs: List[Tuple[str, str]],
    ) -> None:
        """Filtered count should never exceed total count."""
        store = InMemoryRunStore()
        await seed_store(store, runs)

        total = await store.count_runs()

        for s in ALL_STATUSES:
            filtered = await store.count_runs(status=s)
            assert filtered <= total, (
                f"Status '{s}': filtered={filtered} > total={total}"
            )

    # ...........................................................................
    #  Invariant: empty store -> all counts are 0
    #  (Dedicated always-empty strategy so every example tests the edge case)
    # ...........................................................................

    @given(st.just([]))
    @settings(max_examples=1)
    async def test_empty_store_returns_zero(
        self,
        _runs: List[Tuple[str, str]],
    ) -> None:
        """When the store is empty, all counts must be 0."""
        store = InMemoryRunStore()

        assert await store.count_runs() == 0
        for s in ALL_STATUSES:
            assert await store.count_runs(status=s) == 0, (
                f"Empty store should have 0 '{s}' runs"
            )
        counts = await store.count_runs_by_status()
        assert all(v == 0 for v in counts.values()), (
            f"Empty store counts should all be 0, got {counts}"
        )
