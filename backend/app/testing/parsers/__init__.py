"""
Test result parsers — normalize framework output into structured failures.

Each parser implements a common interface for extracting:
    - test counts (passed, failed, skipped, total)
    - individual failures with file/line/message
    - failure classification
"""
