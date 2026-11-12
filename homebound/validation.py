"""Argument checks shared by the whole package.

Each helper returns the value it was given, so a check can sit inline::

    self.system_id = require_integer(system_id, "system_id", minimum=1)

All of them raise ``TypeError`` for a value of the wrong type and ``ValueError`` for a value of
the right type that is out of range, so callers get the same errors everywhere.
"""
from collections.abc import Collection
from typing import Any, Literal, TypeVar, overload

Value = TypeVar("Value")


def is_int(value: object) -> bool:
    """Return whether a value is an int (``bool`` doesn't count)."""
    return isinstance(value, int) and not isinstance(value, bool)


def is_number(value: object) -> bool:
    """Return whether a value is an int or float (``bool`` doesn't count)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def require_type(value: Value, expected: type | tuple[type, ...], name: str) -> Value:
    """Check that a value is an instance of the expected type or types.

    Args:
        value: The value to check.
        expected: A type or a tuple of types, as for ``isinstance``.
        name: The argument name, used in the error message.

    Returns:
        The value, unchanged.

    Raises:
        TypeError: If the value is not an instance of ``expected``.
    """
    if not isinstance(value, expected):
        names = expected.__name__ if isinstance(expected, type) else " or ".join(expected_type.__name__ for expected_type in expected)
        raise TypeError(f"{name} must be {names}, not {type(value).__name__}")
    return value


@overload
def require_integer(value: Any, name: str, minimum: int | None = None, optional: Literal[False] = False) -> int: ...
@overload
def require_integer(value: Any, name: str, minimum: int | None = None, *, optional: bool) -> int | None: ...
def require_integer(value: Any, name: str, minimum: int | None = None, optional: bool = False) -> int | None:
    """Check that a value is an integer (``bool`` doesn't count), optionally with a lower bound.

    Args:
        value: The value to check.
        name: The argument name, used in the error message.
        minimum: The smallest allowed value, or None for no bound.
        optional: Whether None is accepted as well.

    Returns:
        The value, unchanged.

    Raises:
        TypeError: If the value is not an integer (or None when ``optional``).
        ValueError: If the value is below ``minimum``.
    """
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be int, not {type(value).__name__}")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be at least {minimum}, got {value}")
    return value


def require_number(value: Any, name: str, minimum: float | None = None, maximum: float | None = None) -> float:
    """Check that a value is an int or float (``bool`` doesn't count) within optional bounds.

    Args:
        value: The value to check.
        name: The argument name, used in the error message.
        minimum: The smallest allowed value, or None for no bound.
        maximum: The largest allowed value, or None for no bound.

    Returns:
        The value, unchanged.

    Raises:
        TypeError: If the value is not a number.
        ValueError: If the value is outside the bounds.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number, not {type(value).__name__}")
    if (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
        raise ValueError(f"{name} must be between {minimum} and {maximum}, got {value}")
    return value


@overload
def require_text(value: Any, name: str, allow_empty: bool = False, optional: Literal[False] = False) -> str: ...
@overload
def require_text(value: Any, name: str, allow_empty: bool = False, *, optional: bool) -> str | None: ...
def require_text(value: Any, name: str, allow_empty: bool = False, optional: bool = False) -> str | None:
    """Check that a value is a string, by default a non-blank one.

    Args:
        value: The value to check.
        name: The argument name, used in the error message.
        allow_empty: Whether an empty or whitespace-only string is accepted.
        optional: Whether None is accepted as well.

    Returns:
        The value, unchanged.

    Raises:
        TypeError: If the value is not a string (or None when ``optional``).
        ValueError: If the string is blank and ``allow_empty`` is False.
    """
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{name} must be str, not {type(value).__name__}")
    if not allow_empty and not value.strip():
        raise ValueError(f"{name} must not be empty")
    return value


def require_choice(value: Value, choices: Collection, name: str) -> Value:
    """Check that a value is one of a fixed set of choices.

    Args:
        value: The value to check.
        choices: The allowed values.
        name: The argument name, used in the error message.

    Returns:
        The value, unchanged.

    Raises:
        ValueError: If the value is not in ``choices``.
    """
    if value not in choices:
        raise ValueError(f"{name} must be one of {sorted(map(str, choices))}, got {value!r}")
    return value
