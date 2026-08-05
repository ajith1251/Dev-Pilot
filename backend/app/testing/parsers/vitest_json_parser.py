"""
Vitest JSON result parser — extracts structured test results from the
Vitest JSON reporter output (--reporter=json / --json).

Parses:
    - Top-level test counts (numTotalTests, numPassedTests,
      numFailedTests, numPendingTests)
    - Per-suite testResults with file names
    - Per-assertion outcomes with failure messages and stack traces
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from app.models.base import new_id
from app.models.testing import (
    ExecutionStatus,
    FailureCategory,
    ProcessExecutionResult,
    TestFailure,
)
from app.testing.parsers.base import TestResultParser


class VitestJsonParser(TestResultParser):
    """Parse Vitest JSON reporter output into structured TestFailure records."""

    # "path/to/file.test.js:12:5" — extract the line number
    LOCATION_RE = re.compile(r"[:\s](\d+):\d+\s*$")

    def can_parse(self, process_result: ProcessExecutionResult) -> bool:
        """Detect Vitest JSON output.

        Vitest and Jest share the top-level shape, but Vitest suites
        never carry a ``perfStats`` key (Jest always does), which is the
        primary discriminator.
        """
        data = self._load_json(process_result)
        if not isinstance(data, dict):
            return False
        suites = data.get("testResults")
        if not isinstance(suites, list) or not suites:
            return False
        return all(isinstance(s, dict) for s in suites) and not any(
            "perfStats" in s for s in suites if isinstance(s, dict)
        )

    def parse(
        self, process_result: ProcessExecutionResult
    ) -> Tuple[ExecutionStatus, Optional[int], Optional[int], Optional[int], Optional[int], List[TestFailure]]:
        """Parse Vitest JSON into structured results."""
        data = self._load_json(process_result) or {}
        failures: List[TestFailure] = []

        tests_total = self._int_or_none(data.get("numTotalTests"))
        tests_passed = self._int_or_none(data.get("numPassedTests"))
        tests_failed = self._int_or_none(data.get("numFailedTests"))
        tests_skipped = self._int_or_none(data.get("numPendingTests"))

        suites = data.get("testResults")
        if isinstance(suites, list):
            for suite in suites:
                if not isinstance(suite, dict):
                    continue
                file_path = suite.get("name") or None
                for assertion in suite.get("assertionResults") or []:
                    if not isinstance(assertion, dict):
                        continue
                    self._append_failure(assertion, file_path, process_result, failures)

        status = self._overall_status(process_result, failures, tests_failed)
        return status, tests_total, tests_passed, tests_failed, tests_skipped, failures

    def _append_failure(
        self,
        assertion: Dict[str, Any],
        file_path: Optional[str],
        process_result: ProcessExecutionResult,
        failures: List[TestFailure],
    ) -> None:
        """Append a TestFailure for a failing/pending assertion."""
        status = assertion.get("status", "")
        if status not in ("failed", "pending", "todo"):
            return

        messages = assertion.get("failureMessages") or []
        message = messages[0] if messages else (status or "unknown failure")
        title = assertion.get("title", "") or ""
        ancestor = " ".join(str(a) for a in (assertion.get("ancestorTitles") or []))
        full_name = assertion.get("fullName") or (f"{ancestor} {title}".strip() or title)

        failures.append(TestFailure(
            failure_id=new_id(),
            framework="vitest",
            test_name=full_name or title or "unknown",
            file_path=file_path,
            line_number=self._extract_line(message),
            message=message,
            failure_type=self.classify_message(message),
            stack_trace="\n".join(messages) if messages else None,
            step_id=process_result.step_id,
        ))

    @staticmethod
    def _load_json(process_result: ProcessExecutionResult) -> Optional[Dict[str, Any]]:
        """Best-effort parse of JSON from stdout/stderr."""
        combined = (process_result.stdout or "") + "\n" + (process_result.stderr or "")
        if not combined.strip():
            return None
        try:
            parsed = json.loads(combined)
            return parsed if isinstance(parsed, dict) else None
        except (ValueError, TypeError):
            pass
        # JSON may be embedded in surrounding text: try the first object
        start = combined.find("{")
        end = combined.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(combined[start : end + 1])
                return parsed if isinstance(parsed, dict) else None
            except (ValueError, TypeError):
                return None
        return None

    @staticmethod
    def _int_or_none(value: Any) -> Optional[int]:
        return int(value) if isinstance(value, int) and not isinstance(value, bool) else None

    @classmethod
    def _extract_line(cls, message: str) -> Optional[int]:
        """Extract a line number from the first stack frame in a message."""
        for line in message.splitlines():
            match = cls.LOCATION_RE.search(line)
            if match:
                try:
                    return int(match.group(1))
                except ValueError:
                    continue
        return None

    @staticmethod
    def _overall_status(
        process_result: ProcessExecutionResult,
        failures: List[TestFailure],
        tests_failed: Optional[int],
    ) -> ExecutionStatus:
        if process_result.status in (ExecutionStatus.TIMEOUT, ExecutionStatus.ERROR):
            return process_result.status
        if process_result.exit_code == 0:
            return ExecutionStatus.PASSED
        if failures or (tests_failed or 0) > 0:
            return ExecutionStatus.FAILED
        if process_result.exit_code is not None and process_result.exit_code != 0:
            return ExecutionStatus.FAILED
        return ExecutionStatus.UNKNOWN
