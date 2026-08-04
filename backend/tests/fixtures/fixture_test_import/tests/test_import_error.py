"""Tests with a deliberate import error to verify Phase 7 parsing."""


def test_correct():
    """A correct test."""
    assert 1 == 1


def test_import_error():
    """This test imports a module that doesn't exist."""
    from non_existent_module import magic_function
    result = magic_function()
    assert result == "expected"


def test_also_correct():
    """Another correct test — will fail due to collection error."""
    assert 2 == 2
