"""A buggy calculator module for repair testing — has intentional boundary bug."""


def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


def multiply(a: int, b: int) -> int:
    """Multiply two numbers."""
    return a * b


def is_positive(n: int) -> bool:
    """Check if a number is positive — BUG: returns True for 0."""
    return n >= 0  # BUG: should be n > 0
