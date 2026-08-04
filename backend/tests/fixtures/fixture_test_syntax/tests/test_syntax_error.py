"""Tests with a deliberate syntax error to verify Phase 7 parsing."""


def test_correct():
    """A correct test."""
    assert 1 == 1


def test_syntax_error():
    """This file has a deliberate syntax error below."""
    # The next line has invalid Python syntax
    if True
        print("unreachable")


def test_also_correct():
    """Another correct test — will never be reached due to syntax error."""
    assert 2 == 2
