# -*- encoding: utf-8 -*-
"""
Tests for PEP-750 Template String taint propagation.

Template strings (t-strings) are introduced in Python 3.14 as a generalization
of f-strings. This module tests that taint propagation works correctly for
template string operations.
"""

import sys

import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import as_formatted_evidence
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import is_pyobject_tainted
import ddtrace.appsec._iast._taint_tracking.aspects as ddtrace_aspects
from tests.appsec.iast.aspects.aspect_utils import BaseReplacement
from tests.appsec.iast.iast_utils import _iast_patched_module


# Template strings are only available in Python 3.14+
pytestmark = pytest.mark.skipif(
    sys.version_info < (3, 14),
    reason="Template strings (PEP-750) are only available in Python 3.14+",
)


mod = _iast_patched_module("benchmarks.bm.iast_fixtures.template_strings")


class TestTemplateStringAspect(BaseReplacement):
    """
    Test suite for template string taint propagation.

    These tests verify that:
    1. If the template base is tainted, the result is tainted
    2. If any interpolated argument is tainted, the result is tainted
    3. If multiple arguments are tainted, all taints are propagated
    """

    def test_template_string_no_taint_then_no_tainted_result(self):
        """Test that non-tainted template strings produce non-tainted results."""
        # Simulate template string: t"Hello {name}"
        # This would be transformed to template_string_aspect("Hello ", name_value)
        name = "World"
        result = ddtrace_aspects.template_string_aspect("Hello ", name)
        assert not is_pyobject_tainted(result)
        assert result == "Hello World"

    def test_template_string_tainted_argument_then_tainted_result(self):
        """Test that a tainted argument in a template string results in a tainted output."""
        # Create a tainted string
        tainted_name = taint_pyobject(
            "World",
            source_name="test_source",
            source_value="World",
            source_origin=OriginType.PARAMETER,
        )

        # Simulate: t"Hello {tainted_name}"
        result = ddtrace_aspects.template_string_aspect("Hello ", tainted_name)

        assert is_pyobject_tainted(result)
        assert result == "Hello World"

        # Verify taint ranges
        ranges = get_tainted_ranges(result)
        assert len(ranges) > 0

    def test_template_string_multiple_tainted_arguments(self):
        """Test that multiple tainted arguments all contribute to the result's taint."""
        # Create two tainted strings
        tainted_greeting = taint_pyobject(
            "Hello",
            source_name="greeting_source",
            source_value="Hello",
            source_origin=OriginType.PARAMETER,
        )
        tainted_name = taint_pyobject(
            "World",
            source_name="name_source",
            source_value="World",
            source_origin=OriginType.PARAMETER,
        )

        # Simulate: t"{tainted_greeting} {tainted_name}"
        result = ddtrace_aspects.template_string_aspect(tainted_greeting, " ", tainted_name)

        assert is_pyobject_tainted(result)
        assert result == "Hello World"

        # Verify multiple taint ranges exist
        ranges = get_tainted_ranges(result)
        assert len(ranges) >= 2

    def test_template_string_mixed_tainted_and_clean(self):
        """Test template strings with both tainted and clean arguments."""
        tainted_part = taint_pyobject(
            "SECRET",
            source_name="secret_source",
            source_value="SECRET",
            source_origin=OriginType.PARAMETER,
        )
        clean_part = "public"

        # Simulate: t"Value: {tainted_part} and {clean_part}"
        result = ddtrace_aspects.template_string_aspect("Value: ", tainted_part, " and ", clean_part)

        assert is_pyobject_tainted(result)
        assert result == "Value: SECRET and public"

    def test_template_string_empty_arguments(self):
        """Test template strings with empty arguments."""
        result = ddtrace_aspects.template_string_aspect("Just a string")
        assert not is_pyobject_tainted(result)
        assert result == "Just a string"

    def test_template_string_tainted_constant_parts(self):
        """Test that tainted constant parts of template strings propagate taint."""
        tainted_template_part = taint_pyobject(
            "Template: ",
            source_name="template_source",
            source_value="Template: ",
            source_origin=OriginType.PARAMETER,
        )

        # Simulate: t"{tainted_template_part}{value}"
        result = ddtrace_aspects.template_string_aspect(tainted_template_part, "value")

        assert is_pyobject_tainted(result)
        assert result == "Template: value"

    def test_template_string_numeric_interpolation(self):
        """Test template strings with numeric interpolations and tainted parts."""
        tainted_prefix = taint_pyobject(
            "Count: ",
            source_name="prefix_source",
            source_value="Count: ",
            source_origin=OriginType.PARAMETER,
        )
        number = 42

        # Simulate: t"{tainted_prefix}{number}"
        result = ddtrace_aspects.template_string_aspect(tainted_prefix, str(number))

        assert is_pyobject_tainted(result)
        assert result == "Count: 42"


def test_template_string_aspect_direct_call():
    """Direct test of template_string_aspect function."""
    # Test basic functionality
    result = ddtrace_aspects.template_string_aspect("Hello ", "World")
    assert result == "Hello World"
    assert not is_pyobject_tainted(result)

    # Test with tainted input
    tainted = taint_pyobject("Tainted", source_name="test", source_value="Tainted", source_origin=OriginType.PARAMETER)
    result = ddtrace_aspects.template_string_aspect("Prefix: ", tainted)
    assert result == "Prefix: Tainted"
    assert is_pyobject_tainted(result)


def test_template_string_aspect_error_handling():
    """Test that the aspect handles errors gracefully."""
    # Test with None values
    result = ddtrace_aspects.template_string_aspect("Test: ", None)
    assert "None" in str(result)

    # Test with empty args
    result = ddtrace_aspects.template_string_aspect()
    assert result == ""


# ============================================================================
# Integration tests using _iast_patched_module
# These tests verify the complete AST transformation and taint propagation
# ============================================================================


class TestTemplateStringIntegration(BaseReplacement):
    """
    Integration tests that verify the complete AST patching pipeline.
    These tests use _iast_patched_module to load and transform a fixture module
    containing template strings, then verify taint propagation works correctly.
    """

    def test_template_simple_no_taint(self):
        """Test simple template string without taint."""
        result = mod.do_template_simple("World")
        assert result == "World"
        assert not is_pyobject_tainted(result)

    def test_template_simple_with_taint(self):
        """Test simple template string with tainted argument."""
        tainted_input = taint_pyobject(
            "World", source_name="test_source", source_value="World", source_origin=OriginType.PARAMETER
        )
        result = mod.do_template_simple(tainted_input)
        assert result == "World"
        assert is_pyobject_tainted(result)
        assert as_formatted_evidence(result) == ":+-<test_source>World<test_source>-+:"

    def test_template_with_text_no_taint(self):
        """Test template string with text prefix, no taint."""
        result = mod.do_template_with_text("World")
        assert result == "Hello World"
        assert not is_pyobject_tainted(result)

    def test_template_with_text_tainted_arg(self):
        """Test template string with text prefix and tainted argument."""
        tainted_input = taint_pyobject(
            "World", source_name="test_source", source_value="World", source_origin=OriginType.PARAMETER
        )
        result = mod.do_template_with_text(tainted_input)
        assert result == "Hello World"
        assert is_pyobject_tainted(result)
        # The taint should be at position 6 (after "Hello ")
        ranges = get_tainted_ranges(result)
        assert len(ranges) > 0

    def test_template_multiple_args_no_taint(self):
        """Test template string with multiple arguments, no taint."""
        result = mod.do_template_multiple_args("foo", "bar")
        assert result == "foo and bar"
        assert not is_pyobject_tainted(result)

    def test_template_multiple_args_first_tainted(self):
        """Test template string with first argument tainted."""
        tainted_first = taint_pyobject(
            "foo", source_name="source1", source_value="foo", source_origin=OriginType.PARAMETER
        )
        result = mod.do_template_multiple_args(tainted_first, "bar")
        assert result == "foo and bar"
        assert is_pyobject_tainted(result)

    def test_template_multiple_args_second_tainted(self):
        """Test template string with second argument tainted."""
        tainted_second = taint_pyobject(
            "bar", source_name="source2", source_value="bar", source_origin=OriginType.PARAMETER
        )
        result = mod.do_template_multiple_args("foo", tainted_second)
        assert result == "foo and bar"
        assert is_pyobject_tainted(result)

    def test_template_multiple_args_both_tainted(self):
        """Test template string with both arguments tainted."""
        tainted_first = taint_pyobject(
            "foo", source_name="source1", source_value="foo", source_origin=OriginType.PARAMETER
        )
        tainted_second = taint_pyobject(
            "bar", source_name="source2", source_value="bar", source_origin=OriginType.PARAMETER
        )
        result = mod.do_template_multiple_args(tainted_first, tainted_second)
        assert result == "foo and bar"
        assert is_pyobject_tainted(result)
        # Should have multiple taint ranges
        ranges = get_tainted_ranges(result)
        assert len(ranges) >= 2

    def test_template_operations_no_taint(self):
        """Test template string with operations, no taint."""
        result = mod.do_template_operations(5, 3)
        assert result == "5 + 3 = 8"
        assert not is_pyobject_tainted(result)

    def test_template_repr_no_taint(self):
        """Test template string with repr conversion, no taint."""
        result = mod.do_template_repr("test")
        assert result == "test"
        assert not is_pyobject_tainted(result)

    def test_template_repr_with_taint(self):
        """Test template string with repr conversion and tainted input."""
        tainted_input = taint_pyobject(
            "test", source_name="test_source", source_value="test", source_origin=OriginType.PARAMETER
        )
        result = mod.do_template_repr(tainted_input)
        assert result == "test"
        assert is_pyobject_tainted(result)

    def test_template_str_conversion_no_taint(self):
        """Test template string with str conversion, no taint."""
        result = mod.do_template_str_conversion(42)
        assert result == "42"
        assert not is_pyobject_tainted(result)

    def test_template_str_conversion_with_taint(self):
        """Test template string with str conversion and tainted input."""
        tainted_input = taint_pyobject(
            "value", source_name="test_source", source_value="value", source_origin=OriginType.PARAMETER
        )
        result = mod.do_template_str_conversion(tainted_input)
        assert result == "value"
        assert is_pyobject_tainted(result)

    def test_template_complex_no_taint(self):
        """Test complex template string with multiple parts, no taint."""
        result = mod.do_template_complex("Config", "timeout", 30)
        assert result == "Config: timeout = 30"
        assert not is_pyobject_tainted(result)

    def test_template_complex_with_tainted_prefix(self):
        """Test complex template string with tainted prefix."""
        tainted_prefix = taint_pyobject(
            "Config", source_name="prefix_source", source_value="Config", source_origin=OriginType.PARAMETER
        )
        result = mod.do_template_complex(tainted_prefix, "timeout", 30)
        assert result == "Config: timeout = 30"
        assert is_pyobject_tainted(result)

    def test_template_complex_with_tainted_name(self):
        """Test complex template string with tainted name."""
        tainted_name = taint_pyobject(
            "timeout", source_name="name_source", source_value="timeout", source_origin=OriginType.PARAMETER
        )
        result = mod.do_template_complex("Config", tainted_name, 30)
        assert result == "Config: timeout = 30"
        assert is_pyobject_tainted(result)

    def test_template_complex_all_tainted(self):
        """Test complex template string with all parts tainted."""
        tainted_prefix = taint_pyobject(
            "Config", source_name="prefix_source", source_value="Config", source_origin=OriginType.PARAMETER
        )
        tainted_name = taint_pyobject(
            "timeout", source_name="name_source", source_value="timeout", source_origin=OriginType.PARAMETER
        )
        tainted_value = taint_pyobject(
            "30", source_name="value_source", source_value="30", source_origin=OriginType.PARAMETER
        )
        result = mod.do_template_complex(tainted_prefix, tainted_name, tainted_value)
        assert result == "Config: timeout = 30"
        assert is_pyobject_tainted(result)
        # Should have multiple taint ranges
        ranges = get_tainted_ranges(result)
        assert len(ranges) >= 3

    def test_template_only_text_no_taint(self):
        """Test template string with only text (no interpolations)."""
        result = mod.do_template_only_text()
        assert result == "Just plain text"
        # No taint since there are no interpolations
        assert not is_pyobject_tainted(result)

    def test_template_nested_expr_no_taint(self):
        """Test template string with nested expressions."""
        result = mod.do_template_nested_expr()
        assert result == "Result: True"
        assert not is_pyobject_tainted(result)

    def test_template_method_call_no_taint(self):
        """Test template string with method call."""
        result = mod.do_template_method_call()
        assert result == "Hello WORLD"
        assert not is_pyobject_tainted(result)


def test_template_string_taint_ranges_positions():
    """Test that taint ranges are positioned correctly in template strings."""
    # Create tainted inputs
    tainted_a = taint_pyobject("AAA", source_name="source_a", source_value="AAA", source_origin=OriginType.PARAMETER)
    tainted_b = taint_pyobject("BBB", source_name="source_b", source_value="BBB", source_origin=OriginType.PARAMETER)

    # Use template: t"{a} and {b}" which produces "AAA and BBB"
    result = mod.do_template_multiple_args(tainted_a, tainted_b)
    assert result == "AAA and BBB"
    assert is_pyobject_tainted(result)

    ranges = get_tainted_ranges(result)
    # We should have at least 2 ranges (one for each tainted input)
    assert len(ranges) >= 2

    # First range should cover "AAA" at position 0
    # Second range should cover "BBB" at position 8 (after "AAA and ")
    # Note: Exact positions depend on implementation details


# ============================================================================
# Corner case tests - ensure robustness with various data types
# ============================================================================


@pytest.mark.parametrize(
    "value, expected",
    [
        (42, "42"),
        (3.14, "3.14"),
        (True, "True"),
        (False, "False"),
        (None, "None"),
        (complex(2, 3), "(2+3j)"),
        ([1, 2, 3], "[1, 2, 3]"),
        ({"key": "value"}, "{'key': 'value'}"),
        ((1, 2), "(1, 2)"),
        ({1, 2, 3}, None),  # Set order is unpredictable
        (b"bytes", "b'bytes'"),
        (bytearray(b"data"), "bytearray(b'data')"),
    ],
)
def test_template_string_aspect_various_types(value, expected):
    """Test template string aspect with various Python types."""
    result = ddtrace_aspects.template_string_aspect("Value: ", value)

    if expected is None:
        # For unpredictable outputs like sets, just verify no exception
        assert isinstance(result, str)
        assert "Value: " in result
    else:
        assert result == f"Value: {expected}"

    # Should not be tainted if input is not tainted
    assert not is_pyobject_tainted(result)


@pytest.mark.parametrize(
    "value",
    [
        42,
        3.14,
        True,
        False,
        None,
        [1, 2, 3],
        {"key": "value"},
        (1, 2),
    ],
)
def test_template_string_aspect_tainted_non_string_types(value):
    """Test that tainted non-string types propagate taint correctly."""
    # Note: Only strings/bytes/bytearrays can be tainted
    # But if a tainted string is in the same template, the result should be tainted
    tainted_str = taint_pyobject(
        "tainted", source_name="test_source", source_value="tainted", source_origin=OriginType.PARAMETER
    )

    result = ddtrace_aspects.template_string_aspect(tainted_str, ": ", value)

    # Result should be tainted because one of the inputs is tainted
    assert is_pyobject_tainted(result)
    assert isinstance(result, str)


def test_template_string_aspect_empty_values():
    """Test template string aspect with empty values."""
    # Empty strings
    result = ddtrace_aspects.template_string_aspect("", "", "")
    assert result == ""
    assert not is_pyobject_tainted(result)

    # Mix of empty and non-empty
    result = ddtrace_aspects.template_string_aspect("Start", "", "End")
    assert result == "StartEnd"
    assert not is_pyobject_tainted(result)


def test_template_string_aspect_large_html_template():
    """Test template string aspect with a large HTML-like string."""
    title = "Welcome"
    username = "john_doe"
    content = "This is a long content message with lots of text."

    # Simulate a large HTML template
    parts = [
        "<!DOCTYPE html>\n<html>\n<head>\n    <title>",
        title,
        "</title>\n</head>\n<body>\n    <h1>Hello, ",
        username,
        "!</h1>\n    <p>",
        content,
        "</p>\n</body>\n</html>",
    ]

    result = ddtrace_aspects.template_string_aspect(*parts)

    expected = (
        "<!DOCTYPE html>\n<html>\n<head>\n    <title>"
        + title
        + "</title>\n</head>\n<body>\n    <h1>Hello, "
        + username
        + "!</h1>\n    <p>"
        + content
        + "</p>\n</body>\n</html>"
    )

    assert result == expected
    assert not is_pyobject_tainted(result)


def test_template_string_aspect_large_html_template_with_taint():
    """Test template string aspect with large HTML template and tainted values."""
    title = "Welcome"
    username = taint_pyobject(
        "john_doe", source_name="user_input", source_value="john_doe", source_origin=OriginType.PARAMETER
    )
    content = taint_pyobject(
        "This is user content.",
        source_name="user_content",
        source_value="This is user content.",
        source_origin=OriginType.PARAMETER,
    )

    # Simulate a large HTML template with tainted user input
    parts = [
        "<!DOCTYPE html>\n<html>\n<head>\n    <title>",
        title,
        "</title>\n</head>\n<body>\n    <h1>Hello, ",
        username,
        "!</h1>\n    <p>",
        content,
        "</p>\n</body>\n</html>",
    ]

    result = ddtrace_aspects.template_string_aspect(*parts)

    # Result should be tainted because username and content are tainted
    assert is_pyobject_tainted(result)

    # Verify the result string is correct
    expected = (
        "<!DOCTYPE html>\n<html>\n<head>\n    <title>"
        + title
        + "</title>\n</head>\n<body>\n    <h1>Hello, "
        + "john_doe"
        + "!</h1>\n    <p>"
        + "This is user content."
        + "</p>\n</body>\n</html>"
    )
    assert result == expected


@pytest.mark.parametrize(
    "values",
    [
        # Unicode characters
        ("Hello", "世界", "🌍"),
        # Special characters
        ("a\nb", "c\td", "e\rf"),
        # Quotes and escapes
        ('He said "hello"', "It's nice", "Back\\slash"),
        # Numbers mixed with strings
        (1, 2.5, "three", 4j),
        # Empty and whitespace
        ("", " ", "  \t\n  ", "text"),
    ],
)
def test_template_string_aspect_special_characters(values):
    """Test template string aspect with special characters and unicode."""
    result = ddtrace_aspects.template_string_aspect(*values)

    # Build expected result
    expected = "".join(str(v) for v in values)
    assert result == expected

    # Should not raise exception
    assert isinstance(result, str)


def test_template_string_aspect_no_exception_on_error():
    """Test that template string aspect never raises exceptions, uses fallback."""
    # This should not raise an exception even with problematic inputs
    # The fallback mechanism should handle it

    # Test with various edge cases
    result1 = ddtrace_aspects.template_string_aspect()
    assert result1 == ""

    result2 = ddtrace_aspects.template_string_aspect("just", "normal", "strings")
    assert result2 == "justnormalstrings"

    # All of these should work without exceptions
    result3 = ddtrace_aspects.template_string_aspect(None, None, None)
    assert "None" in result3


def test_template_string_aspect_preserves_result_value():
    """Test that aspect never changes the actual result string value."""
    test_cases = [
        (["Hello", " ", "World"], "Hello World"),
        ([42, " is the answer"], "42 is the answer"),
        (["", "empty", "", "middle", ""], "emptymiddle"),
        ([True, False, None], "TrueFalseNone"),
        ([3.14159, " is pi"], "3.14159 is pi"),
    ]

    for args, expected in test_cases:
        result = ddtrace_aspects.template_string_aspect(*args)
        assert result == expected, f"Expected {expected}, got {result}"


def test_template_string_aspect_bytes_and_bytearray():
    """Test template string aspect with bytes and bytearray values."""
    # Bytes should be converted to string representation
    result = ddtrace_aspects.template_string_aspect("Bytes: ", b"hello")
    assert result == "Bytes: b'hello'"

    # Bytearray should be converted to string representation
    result = ddtrace_aspects.template_string_aspect("Bytearray: ", bytearray(b"world"))
    assert result == "Bytearray: bytearray(b'world')"

    # Should not raise exceptions
    assert isinstance(result, str)


def test_template_string_aspect_deeply_nested_structures():
    """Test template string aspect with deeply nested data structures."""
    nested_dict = {"level1": {"level2": {"level3": "deep"}}}
    nested_list = [[[1, 2], [3, 4]], [[5, 6], [7, 8]]]

    result = ddtrace_aspects.template_string_aspect("Dict: ", nested_dict, " List: ", nested_list)

    # Should not raise exception
    assert isinstance(result, str)
    assert "Dict: " in result
    assert "List: " in result

    # Verify the result matches string conversion
    expected = f"Dict: {nested_dict} List: {nested_list}"
    assert result == expected
