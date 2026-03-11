# Python 3.14+ only functions (template strings / t-strings from PEP-750)
# This module contains fixtures for testing template string taint propagation
# ruff: noqa: F821  # Template string syntax not recognized in Python < 3.14
from typing import Any


def do_template_simple(tainted_input: str) -> str:
    """Simple template string with one interpolation."""
    return t"{tainted_input}"


def do_template_with_text(tainted_input: str) -> str:
    """Template string with text and interpolation."""
    return t"Hello {tainted_input}"


def do_template_multiple_args(tainted_a: str, tainted_b: str) -> str:
    """Template string with multiple interpolations."""
    return t"{tainted_a} and {tainted_b}"


def do_template_operations(a: int, b: int) -> str:
    """Template string with expressions and operations."""
    return t"{a} + {b} = {a + b}"


def do_template_with_format_spec(a: int, spec: str = "05d") -> str:
    """Template string with format specification."""
    return t"{a:{spec}}"


def do_template_repr(tainted_input: Any) -> str:
    """Template string with repr conversion."""
    return t"{tainted_input!r}"


def do_template_repr_twice(a: Any) -> str:
    """Template string with repr conversion used twice."""
    return t"{a!r} {a!r}"


def do_template_repr_twice_different(a: Any, b: Any) -> str:
    """Template string with repr conversion on different objects."""
    return t"{a!r} {b!r}"


def do_template_str_conversion(tainted_input: Any) -> str:
    """Template string with str conversion."""
    return t"{tainted_input!s}"


def do_template_ascii_conversion(a: Any) -> str:
    """Template string with ascii conversion."""
    return t"{a!a}"


def do_template_with_format(a: Any) -> str:
    """Template string with format specification."""
    return t"{a:10}"


def do_template_complex(tainted_prefix: str, tainted_name: str, tainted_value: int) -> str:
    """Complex template string with multiple parts."""
    return t"{tainted_prefix}: {tainted_name} = {tainted_value}"


def do_template_nested_expr() -> str:
    """Template string with nested expressions."""
    return t"Result: {True or False}"


def do_template_method_call() -> str:
    """Template string with method call."""
    return t"Hello {'world'.upper()}"


def do_template_only_text() -> str:
    """Template string with only text (no interpolations)."""
    return t"Just plain text"


def do_template_empty_interpolation() -> str:
    """Template string with empty string interpolation."""
    return t"Value: {''}"


def do_template_multiline(a: str, b: str) -> str:
    """Template string spanning multiple lines."""
    return t"""First: {a}
Second: {b}"""


def do_template_with_exception() -> str:
    """Template string with Exception object created inline."""
    return t"template {Exception('Testst')}"


def do_template_operations_more(a: int, b: int) -> str:
    """Template string with multiple arithmetic expressions."""
    return t"{a} + {b} = {a + b}; {a} * {b} = {a * b}; {a} - {b} = {a - b}"


def do_template_operations_with_parens(a: int, b: int) -> str:
    """Template string with parentheses and precedence-sensitive expressions."""
    return t"({a} + {b}) * 2 = {(a + b) * 2}"


def do_template_attribute_and_subscript() -> str:
    """Template string with attribute and subscript expressions."""

    class Obj:
        def __init__(self):
            self.value = "ok"

    obj = Obj()
    arr = ["x", "y"]
    return t"{obj.value}:{arr[1]}"
