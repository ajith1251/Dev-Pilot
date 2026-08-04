"""Tests with known failures for Phase 7 testing."""


def test_assertion_failure():
    """A test with a deliberate assertion failure."""
    expected = 42
    actual = 0
    assert actual == expected, f"Expected {expected}, got {actual}"


def test_string_mismatch():
    """A test with string comparison failure."""
    result = "hello world"
    expected = "Hello World"
    assert result == expected, f"Case mismatch: '{result}' != '{expected}'"


class TestCalculationErrors:
    """Grouped tests with calculation errors."""

    def test_division_by_zero_expected(self):
        """This test expects a wrong result."""
        result = 10 / 2
        assert result == 0, f"Division gave {result}, expected 0"

    def test_percentage_calculation(self):
        """Wrong percentage calculation."""
        value = 200
        percent = 10
        result = value * (percent / 100)
        assert result == 10, f"10% of 200 should be 20, got {result}"


def test_list_contains():
    """Test list membership."""
    fruits = ["apple", "banana", "cherry"]
    assert "durian" in fruits, "durian should be in the fruit list"


def test_type_error():
    """Test that triggers a type error."""
    value = "not_a_number"
    result = value + 10  # TypeError: can only concatenate str (not int) to str
    assert result == "not_a_number10"
