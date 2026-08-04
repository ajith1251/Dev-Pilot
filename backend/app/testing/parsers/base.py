"""
Base result parser — abstract interface for framework-specific parsers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional, Tuple

from app.models.testing import (
    ExecutionStatus,
    FailureCategory,
    ProcessExecutionResult,
    TestFailure,
)


class TestResultParser(ABC):
    """Abstract base for framework-specific test result parsers."""

    @abstractmethod
    def parse(
        self, process_result: ProcessExecutionResult
    ) -> Tuple[ExecutionStatus, Optional[int], Optional[int], Optional[int], Optional[int], List[TestFailure]]:
        """Parse a process result into structured test results.

        Args:
            process_result: The raw process execution result.

        Returns:
            Tuple of:
                - Overall status
                - tests_total (or None if unknown)
                - tests_passed (or None)
                - tests_failed (or None)
                - tests_skipped (or None)
                - List of TestFailure records
        """
        ...

    @abstractmethod
    def can_parse(self, process_result: ProcessExecutionResult) -> bool:
        """Check if this parser can handle the given process result."""
        ...

    @staticmethod
    def classify_message(message: str) -> FailureCategory:
        """Deterministically classify a failure message into a category."""
        msg_lower = message.lower()

        if "syntaxerror" in msg_lower or "invalid syntax" in msg_lower:
            return FailureCategory.SYNTAX_ERROR
        if "importerror" in msg_lower or "modulenotfounderror" in msg_lower or "cannot import" in msg_lower:
            return FailureCategory.IMPORT_ERROR
        if "typeerror" in msg_lower:
            return FailureCategory.TYPE_ERROR
        if "assert" in msg_lower or "assertionerror" in msg_lower:
            return FailureCategory.ASSERTION_FAILURE
        if "modulenotfounderror" in msg_lower or "could not be resolved" in msg_lower:
            return FailureCategory.DEPENDENCY_ERROR
        if "not found" in msg_lower and (
            "module" in msg_lower or "package" in msg_lower or "command" in msg_lower
        ):
            return FailureCategory.DEPENDENCY_ERROR
        if "configuration" in msg_lower and "error" in msg_lower:
            return FailureCategory.CONFIGURATION_ERROR
        if "timeout" in msg_lower:
            return FailureCategory.TIMEOUT

        return FailureCategory.EXECUTION_ERROR
