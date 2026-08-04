"""
Pytest result parser — extracts structured test results from pytest output.

Parses:
    - Test session header (collected N items)
    - Per-test results (PASSED, FAILED, ERROR)
    - Failure details (file, line, type, message)
    - Summary line (N passed, M failed, K skipped)
    - Traceback information
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from app.models.testing import (
    ExecutionStatus,
    FailureCategory,
    ProcessExecutionResult,
    TestFailure,
)
from app.testing.parsers.base import TestResultParser
from app.models.base import new_id


class PytestResultParser(TestResultParser):
    """Parse pytest output into structured TestFailure records.

    Supports both verbose (-v) and compact output.
    """

    # Pattern for test result lines: PASSED/FAILED/ERROR tests/test_file.py::test_name
    TEST_LINE_RE = re.compile(
        r"^(PASSED|FAILED|ERROR|SKIPPED)\s+"
        r"(tests/)?([^:]+?)(::([^\s]+))?"
        r"(?:\s+.*)?$",
        re.MULTILINE,
    )

    # Pattern for failure details: file.py:LINE: ErrorType: message
    FAILURE_DETAIL_RE = re.compile(
        r'^(?:E\s+)?'
        r'File\s+"([^"]+)",\s+line\s+(\d+)'
        r'(?:,\s+in\s+(\S+))?'
        r'(?:\s*\n\s*E\s+(.*))?',
        re.MULTILINE,
    )

    # Pattern for ERROR lines during collection
    ERROR_DETAIL_RE = re.compile(
        r"ERROR\s+(.*?)(?:\s+-\s+(.*))?$",
        re.MULTILINE,
    )

    # Summary line: matches "N passed, M failed, K skipped in X.YYs" in any order
    SUMMARY_RE = re.compile(
        r"=+\s+"
        r"(?:(\d+)\s+passed)?"
        r"[\s,]*"
        r"(?:(\d+)\s+failed)?"
        r"[\s,]*"
        r"(?:(\d+)\s+skipped)?"
        r"[\s,]*"
        r"(?:\d+\s+(?:error|warning)s?)?"
        r"[\s,]*"
        r"in\s+[\d.]+[sm]",
    )

    # Summary line with failed first: "N failed, M passed..."
    SUMMARY_REV_RE = re.compile(
        r"=+\s+"
        r"(?:(\d+)\s+failed)?"
        r"[\s,]*"
        r"(?:(\d+)\s+passed)?"
        r"[\s,]*"
        r"(?:(\d+)\s+skipped)?"
        r"[\s,]*"
        r"(?:\d+\s+(?:error|warning)s?)?"
        r"[\s,]*"
        r"in\s+[\d.]+[sm]",
    )

    # Collection header: "collected N items"
    COLLECTED_RE = re.compile(
        r"collected\s+(\d+)\s+items",
        re.MULTILINE,
    )

    # Short summary: "FAILED tests/test_file.py::test_name - ErrorType: message"
    # re.MULTILINE required for ^/$ to match line boundaries
    SHORT_FAILURE_RE = re.compile(
        r"^(?:FAILED|ERROR)\s+(tests/)?([^\s]+(?:::[^\s]+)?)"
        r"(?:\s+-\s+(.*))?$",
        re.MULTILINE,
    )

    def can_parse(self, process_result: ProcessExecutionResult) -> bool:
        """Check if this is pytest output by looking for pytest markers."""
        output = process_result.stdout + "\n" + process_result.stderr
        has_pytest = bool(self.COLLECTED_RE.search(output)) or bool(
            self.SUMMARY_RE.search(output)
        )
        return has_pytest

    def parse(
        self, process_result: ProcessExecutionResult
    ) -> Tuple[ExecutionStatus, Optional[int], Optional[int], Optional[int], Optional[int], List[TestFailure]]:
        """Parse pytest output."""
        combined = process_result.stdout + "\n" + process_result.stderr
        failures: List[TestFailure] = []

        # 1. Try to extract summary counts
        tests_total, tests_passed, tests_failed, tests_skipped = (
            self._parse_summary(combined)
        )

        # 2. If we have collection count, use it for total
        collected = self._parse_collected(combined)
        if collected is not None:
            tests_total = collected

        # 3. Extract individual failures
        failures = self._extract_failures(combined, process_result)

        # 4. Determine overall status
        if process_result.status in (ExecutionStatus.TIMEOUT, ExecutionStatus.ERROR):
            status = process_result.status
        elif process_result.exit_code == 0:
            status = ExecutionStatus.PASSED
        elif failures:
            status = ExecutionStatus.FAILED
        elif process_result.exit_code is not None and process_result.exit_code != 0:
            status = ExecutionStatus.FAILED
        else:
            status = ExecutionStatus.UNKNOWN

        return status, tests_total, tests_passed, tests_failed, tests_skipped, failures

    def _parse_summary(
        self, output: str
    ) -> Tuple[Optional[int], Optional[int], Optional[int], Optional[int]]:
        """Extract test counts from pytest summary line."""
        # Try standard order first (passed, failed, skipped)
        match = self.SUMMARY_RE.search(output)
        if match:
            def safe_int(val: Optional[str]) -> Optional[int]:
                return int(val) if val else 0
            passed = safe_int(match.group(1))
            failed = safe_int(match.group(2))
            skipped = safe_int(match.group(3))
            total = (passed or 0) + (failed or 0) + (skipped or 0)
            return total, passed, failed, skipped

        # Try reversed order (failed, passed, skipped)
        match = self.SUMMARY_REV_RE.search(output)
        if match:
            def safe_int(val: Optional[str]) -> Optional[int]:
                return int(val) if val else 0
            failed = safe_int(match.group(1))
            passed = safe_int(match.group(2))
            skipped = safe_int(match.group(3))
            total = (passed or 0) + (failed or 0) + (skipped or 0)
            return total, passed, failed, skipped

        # Try fallback: bare "X passed" or "X failed" at the end
        return None, None, None, None

    def _parse_collected(self, output: str) -> Optional[int]:
        """Extract total test count from collection header."""
        match = self.COLLECTED_RE.search(output)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                return None
        return None

    def _extract_failures(
        self, output: str, process_result: ProcessExecutionResult
    ) -> List[TestFailure]:
        """Extract individual test failures from output."""
        failures: List[TestFailure] = []
        seen_tests: set = set()

        # Parse short failure lines with error messages
        # Format: "FAILED tests/file.py::test_name - ErrorType: message"
        for match in self.SHORT_FAILURE_RE.finditer(output):
            test_path = match.group(2).strip()
            error_message = (match.group(3) or "").strip()

            if test_path in seen_tests:
                continue
            seen_tests.add(test_path)

            # Parse test name and file path
            file_path = test_path.split("::")[0] if "::" in test_path else test_path
            test_name = test_path.split("::")[-1] if "::" in test_path else ""

            # Classify the failure
            failure_type = FailureCategory.UNKNOWN
            if error_message:
                failure_type = self.classify_message(error_message)

            failures.append(TestFailure(
                failure_id=new_id(),
                framework="pytest",
                test_name=test_name or test_path,
                file_path=file_path,
                message=error_message or test_path,
                failure_type=failure_type,
                stack_trace=error_message if error_message else None,
                step_id=process_result.step_id,
            ))

        return failures
