"""Tests for the buggy calculator module — is_positive(0) will FAIL."""

from calc_buggy import add, multiply, is_positive


def test_add():
    assert add(1, 2) == 3
    assert add(-1, 1) == 0
    assert add(0, 0) == 0


def test_multiply():
    assert multiply(2, 3) == 6
    assert multiply(-1, 1) == -1
    assert multiply(0, 5) == 0


def test_is_positive():
    """This test FAILS because is_positive(0) returns True (bug: n >= 0)."""
    assert is_positive(5) is True
    assert is_positive(0) is False  # This assertion FAILS!
    assert is_positive(-1) is False
