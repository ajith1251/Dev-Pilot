"""
Unittest XML result parser — extracts structured test results from
JUnit-style XML emitted by unittest XML reporters (xmlrunner /
unittest-xml-reporting).

Parses:
    - testsuites / testsuite aggregates (tests, failures, errors, skipped)
    - per-testcase outcomes (pass, failure, error, skipped)
    - failure/error messages, types, and traceback text
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import List, Optional, Tuple

from app.models.base import new_id
from app.models.testing import (
    ExecutionStatus,
    FailureCategory,
    ProcessExecutionResult,
    TestFailure,
)
from app.testing.parsers.base import TestResultParser


class UnittestXMLParser(TestResultParser):
    """Parse JUnit-style XML output from unittest XML reporters."""

    # Traceback frame:  File "path/to/file.py", line 12, in test_assert
    LINE_RE = re.compile(r"line\s+(\d+)")

    def can_parse(self, process_result: ProcessExecutionResult) -> bool:
        """Detect JUnit-style XML (testsuites/testsuite root)."""
        combined = (process_result.stdout or "") + "\n" + (process_result.stderr or "")
        stripped = combined.lstrip()
        if not stripped.startswith("<") or "<testsuite" not in stripped:
            return False
        try:
            root = ET.fromstring(stripped)
        except ET.ParseError:
            return False
        return root.tag in ("testsuites", "testsuite")

    def parse(
        self, process_result: ProcessExecutionResult
    ) -> Tuple[ExecutionStatus, Optional[int], Optional[int], Optional[int], Optional[int], List[TestFailure]]:
        """Parse JUnit-style XML into structured results."""
        combined = (process_result.stdout or "") + "\n" + (process_result.stderr or "")
        failures: List[TestFailure] = []

        try:
            root = ET.fromstring(combined)
        except ET.ParseError:
            # Malformed XML falls back to generic handling
            if process_result.status in (ExecutionStatus.TIMEOUT, ExecutionStatus.ERROR):
                status = process_result.status
            elif process_result.exit_code == 0:
                status = ExecutionStatus.PASSED
            elif process_result.exit_code is not None:
                status = ExecutionStatus.FAILED
            else:
                status = ExecutionStatus.UNKNOWN
            return status, None, None, None, None, failures

        suites = [root] if root.tag == "testsuite" else root.findall("testsuite")

        # Aggregate counts, preferring top-level testsuites attributes
        tests = self._attr_int(root, "tests") if root.tag == "testsuites" else None
        failures_n = self._attr_int(root, "failures") if root.tag == "testsuites" else None
        errors_n = self._attr_int(root, "errors") if root.tag == "testsuites" else None
        skipped_n = self._attr_int(root, "skipped") if root.tag == "testsuites" else None

        if tests is None or failures_n is None or errors_n is None or skipped_n is None:
            suite_tests = sum(
                (self._attr_int(suite, "tests") or 0) for suite in suites
            )
            suite_failures = sum(
                (self._attr_int(suite, "failures") or 0) for suite in suites
            )
            suite_errors = sum(
                (self._attr_int(suite, "errors") or 0) for suite in suites
            )
            suite_skipped = sum(
                (self._attr_int(suite, "skipped") or 0) for suite in suites
            )
            tests = tests if tests is not None else (suite_tests or None)
            failures_n = failures_n if failures_n is not None else suite_failures
            errors_n = errors_n if errors_n is not None else suite_errors
            skipped_n = skipped_n if skipped_n is not None else suite_skipped

        failed_n = (failures_n or 0) + (errors_n or 0)
        passed_n = None
        if tests is not None:
            passed_n = max(tests - failed_n - (skipped_n or 0), 0)

        # Per-testcase failures
        for suite in suites:
            classname = suite.attrib.get("name", "")
            for testcase in suite.findall("testcase"):
                name = testcase.attrib.get("name", "")
                test_name = f"{classname}.{name}" if classname and name else (name or classname)
                file_path = self._classname_to_path(classname) or None
                for child in testcase:
                    if child.tag not in ("failure", "error"):
                        continue
                    message = child.attrib.get("message", "")
                    child_type = child.attrib.get("type", "")
                    traceback = (child.text or "").strip()
                    combined_msg = message or child_type or traceback or child.tag
                    failures.append(TestFailure(
                        failure_id=new_id(),
                        framework="unittest",
                        test_name=test_name or "unknown",
                        file_path=file_path,
                        line_number=self._extract_line(traceback or message),
                        message=combined_msg,
                        failure_type=self.classify_message(
                            f"{child_type} {message} {traceback}"
                        ),
                        stack_trace=traceback if traceback else None,
                        related_output=child_type or None,
                        step_id=process_result.step_id,
                    ))

        # Overall status
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

        return status, tests, passed_n, failed_n, skipped_n, failures

    @staticmethod
    def _attr_int(element: ET.Element, name: str) -> Optional[int]:
        """Read a numeric XML attribute (None if absent/invalid)."""
        raw = element.attrib.get(name)
        if raw is None:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    @staticmethod
    def _classname_to_path(classname: str) -> str:
        """Convert a unittest classname (module.Class or module) to a path."""
        if not classname:
            return ""
        parts = classname.split(".")
        if len(parts) >= 2 and parts[-1][:1].isupper():
            parts = parts[:-1]
        return "/".join(parts) + ".py"

    @staticmethod
    def _extract_line(traceback: str) -> Optional[int]:
        """Extract a line number from traceback text like file.py:12."""
        match = UnittestXMLParser.LINE_RE.search(traceback or "")
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                return None
        return None
