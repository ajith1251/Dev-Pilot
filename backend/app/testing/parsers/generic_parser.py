"""
Generic result parser — fallback when no framework-specific parser matches.

Preserves exit code, stdout, and stderr without fabricating test counts.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from app.models.testing import (
    ExecutionStatus,
    FailureCategory,
    FailureCategory,
    ProcessExecutionResult,
    TestFailure,
)
from app.models.base import new_id
from app.testing.parsers.base import TestResultParser


class GenericResultParser(TestResultParser):
    """Generic fallback parser.

    Does not try to interpret framework-specific output.
    Preserves raw output and exit code faithfully.
    """

    def can_parse(self, process_result: ProcessExecutionResult) -> bool:
        """Generic parser can handle any result (lowest priority)."""
        return True

    def parse(
        self, process_result: ProcessExecutionResult
    ) -> Tuple[ExecutionStatus, Optional[int], Optional[int], Optional[int], Optional[int], List[TestFailure]]:
        """Parse using generic fallback — no fabricated test counts."""
        failures: List[TestFailure] = []

        # Determine status
        if process_result.status == ExecutionStatus.TIMEOUT:
            status = ExecutionStatus.TIMEOUT
        elif process_result.status == ExecutionStatus.REJECTED:
            status = ExecutionStatus.REJECTED
        elif process_result.exit_code == 0:
            status = ExecutionStatus.PASSED
        elif process_result.exit_code is not None and process_result.exit_code != 0:
            status = ExecutionStatus.FAILED
            # Create a generic failure
            combined = (process_result.stderr or "") + "\n" + (process_result.stdout or "")
            message = combined[:2000] if combined else f"Exit code: {process_result.exit_code}"
            failure_type = self._classify_from_output(process_result)

            failures.append(TestFailure(
                failure_id=new_id(),
                framework="generic",
                test_name=f"Command: {process_result.command[:100]}",
                message=message,
                failure_type=failure_type,
                step_id=process_result.step_id,
            ))
        else:
            status = ExecutionStatus.UNKNOWN

        return status, None, None, None, None, failures

    @staticmethod
    def _classify_from_output(process_result: ProcessExecutionResult) -> FailureCategory:
        """Classify failure from stderr/stdout content."""
        if process_result.timed_out:
            return FailureCategory.TIMEOUT

        combined = (process_result.stderr or "") + "\n" + (process_result.stdout or "")
        # Use the base class classify_message as primary classification
        return TestResultParser.classify_message(combined)
