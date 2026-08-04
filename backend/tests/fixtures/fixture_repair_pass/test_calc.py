"""Tests for the calculator module — all should pass."""

from calc import add, multiply, is_positive


def test_add():
    assert add(1, 2) == 3
    assert add(-1, 1) == 0
    assert add(0, 0) == 0


def test_multiply():
    assert multiply(2, 3) == 6
    assert multiply(-1, 1) == -1
    assert multiply(0, 5) == 0


def test_is_positive():
    assert is_positive(5) is True
    assert is_positive(0) is False
    assert is_positive(-1) is False
