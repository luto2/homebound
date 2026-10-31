"""The shared argument checks."""
import pytest

from homebound.validation import require_choice, require_integer, require_number, require_text, require_type


def test_require_type() -> None:
    """Matching values pass through; others raise TypeError naming the argument."""
    assert require_type("x", str, "name") == "x"
    assert require_type(None, (str, type(None)), "name") is None
    with pytest.raises(TypeError, match="name must be str or NoneType, not int"):
        require_type(1, (str, type(None)), "name")


def test_require_integer() -> None:
    """Bools are not integers; the minimum and None are checked."""
    assert require_integer(5, "n", minimum=5) == 5
    assert require_integer(None, "n", optional=True) is None
    for bad in (True, 1.0, "1", None):
        with pytest.raises(TypeError):
            require_integer(bad, "n")
    with pytest.raises(ValueError):
        require_integer(4, "n", minimum=5)


def test_require_number() -> None:
    """Ints and floats within the bounds pass."""
    assert require_number(0.5, "x", 0, 1) == 0.5 and require_number(3, "x") == 3
    with pytest.raises(TypeError):
        require_number(False, "x")
    with pytest.raises(ValueError):
        require_number(1.5, "x", 0, 1)


def test_require_text() -> None:
    """Blank strings fail unless allowed; None only when optional."""
    assert require_text("a", "t") == "a" and require_text(" ", "t", allow_empty=True) == " "
    assert require_text(None, "t", optional=True) is None
    with pytest.raises(ValueError):
        require_text(" ", "t")
    with pytest.raises(TypeError):
        require_text(None, "t")


def test_require_choice() -> None:
    """Values outside the choices raise ValueError listing them."""
    assert require_choice("a", ("a", "b"), "c") == "a"
    with pytest.raises(ValueError, match=r"\['a', 'b'\]"):
        require_choice("z", ("a", "b"), "c")
