"""Example tests that all pass."""

import pytest


def test_simple_math():
    """A simple passing test."""
    assert 1 + 1 == 2


def test_string_operations():
    """Another passing test."""
    name = "DevPilot"
    assert name.startswith("Dev")
    assert len(name) == 8


class TestUserOperations:
    """Grouped tests for user operations."""

    def test_create_user(self):
        user = {"id": 1, "name": "Alice"}
        assert user["id"] == 1
        assert user["name"] == "Alice"

    def test_update_user(self):
        user = {"id": 1, "name": "Bob"}
        user["name"] = "Charlie"
        assert user["name"] == "Charlie"

    def test_delete_user(self):
        users = [{"id": 1}, {"id": 2}]
        users = [u for u in users if u["id"] != 1]
        assert len(users) == 1


def test_list_operations():
    """Test basic list operations."""
    items = [1, 2, 3]
    items.append(4)
    assert items == [1, 2, 3, 4]
    assert len(items) == 4
    assert items[0] == 1
    assert items[-1] == 4


@pytest.mark.parametrize("a,b,expected", [
    (1, 2, 3),
    (0, 0, 0),
    (-1, 1, 0),
    (100, -50, 50),
])
def test_parametrized_addition(a, b, expected):
    """Test parametrized addition."""
    assert a + b == expected


@pytest.mark.skip(reason="Demonstrating skip")
def test_skipped_demo():
    """This test is intentionally skipped."""
    assert False  # Would fail if executed
