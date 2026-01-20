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
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import is_pyobject_tainted
import ddtrace.appsec._iast._taint_tracking.aspects as ddtrace_aspects
from tests.appsec.iast.aspects.aspect_utils import BaseReplacement
from tests.appsec.iast.iast_utils import _iast_patched_module


# Template strings are only available in Python 3.14+
if sys.version_info < (3, 14):
    pytest.skip("Template strings require Python 3.14+", allow_module_level=True)

from string.templatelib import Template


def _assert_template_structure_matches(result: Template, expected: Template, context: str = "") -> None:
    assert isinstance(result, Template)
    assert isinstance(expected, Template)

    prefix = f"{context}\n" if context else ""

    assert result.strings == expected.strings, (
        prefix + f"Template.strings mismatch\nExpected: {expected.strings!r}\nActual:   {result.strings!r}"
    )
    assert len(result.interpolations) == len(expected.interpolations), (
        prefix + "Template.interpolations length mismatch\n"
        f"Expected: {len(expected.interpolations)}\n"
        f"Actual:   {len(result.interpolations)}"
    )

    for idx, (result_interp, expected_interp) in enumerate(zip(result.interpolations, expected.interpolations)):
        assert result_interp.value == expected_interp.value, (
            prefix + f"Interpolation[{idx}].value mismatch\n"
            f"Expected: {expected_interp.value!r}\n"
            f"Actual:   {result_interp.value!r}"
        )
        assert result_interp.expression == expected_interp.expression, (
            prefix + f"Interpolation[{idx}].expression mismatch\n"
            f"Expected: {expected_interp.expression!r}\n"
            f"Actual:   {result_interp.expression!r}"
        )
        assert result_interp.conversion == expected_interp.conversion, (
            prefix + f"Interpolation[{idx}].conversion mismatch\n"
            f"Expected: {expected_interp.conversion!r}\n"
            f"Actual:   {result_interp.conversion!r}"
        )
        assert result_interp.format_spec == expected_interp.format_spec, (
            prefix + f"Interpolation[{idx}].format_spec mismatch\n"
            f"Expected: {expected_interp.format_spec!r}\n"
            f"Actual:   {result_interp.format_spec!r}"
        )


mod = _iast_patched_module("benchmarks.bm.iast_fixtures.template_strings")


def template_to_str(template):
    """
    Convert a Template object to its string representation.

    Template objects don't override __str__(), so str(Template) returns repr().
    This helper manually interleaves the strings and interpolation values to
    produce the actual concatenated string, applying conversions and format specs.
    """
    if not isinstance(template, Template):
        return str(template)

    parts = []
    for i, string_part in enumerate(template.strings):
        parts.append(string_part)
        if i < len(template.interpolations):
            interp = template.interpolations[i]
            value = interp.value

            # Apply conversion (!r, !s, !a)
            if interp.conversion == "r":
                value = repr(value)
            elif interp.conversion == "s":
                value = str(value)
            elif interp.conversion == "a":
                value = ascii(value)
            else:
                value = str(value)

            # Apply format spec (:05d, :10, etc.)
            if interp.format_spec:
                value = format(interp.value, interp.format_spec)

            parts.append(value)
    return "".join(parts)


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
        # AST visitor passes: ("Hello ", (name_value, "name", None, ""), "")
        name = "World"
        result = ddtrace_aspects.template_string_aspect("Hello ", (name, "name", None, ""), "")
        expected_template = t"Hello {name}"
        assert isinstance(result, Template)
        _assert_template_structure_matches(result, expected_template)
        assert not is_pyobject_tainted(result)
        assert template_to_str(result) == "Hello World"

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
        # AST visitor passes: ("Hello ", (tainted_name, "tainted_name", None, ""), "")
        result = ddtrace_aspects.template_string_aspect("Hello ", (tainted_name, "tainted_name", None, ""), "")

        expected_template = t"Hello {tainted_name}"

        assert isinstance(result, Template)
        _assert_template_structure_matches(result, expected_template)
        assert is_pyobject_tainted(result)
        assert template_to_str(result) == "Hello World"

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
        # AST visitor passes: ("", (tainted_greeting, "greeting", None, ""), " ", (tainted_name, "name", None, ""), "")
        result = ddtrace_aspects.template_string_aspect(
            "", (tainted_greeting, "greeting", None, ""), " ", (tainted_name, "name", None, ""), ""
        )

        from string.templatelib import Interpolation

        expected_template = Template(
            "",
            Interpolation(value=tainted_greeting, expression="greeting", conversion=None, format_spec=""),
            " ",
            Interpolation(value=tainted_name, expression="name", conversion=None, format_spec=""),
            "",
        )

        assert isinstance(result, Template)
        _assert_template_structure_matches(result, expected_template)
        assert is_pyobject_tainted(result)
        assert template_to_str(result) == "Hello World"

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
        # AST visitor passes tuples for interpolations
        result = ddtrace_aspects.template_string_aspect(
            "Value: ", (tainted_part, "tainted", None, ""), " and ", (clean_part, "clean", None, ""), ""
        )

        from string.templatelib import Interpolation

        expected_template = Template(
            "Value: ",
            Interpolation(value=tainted_part, expression="tainted", conversion=None, format_spec=""),
            " and ",
            Interpolation(value=clean_part, expression="clean", conversion=None, format_spec=""),
            "",
        )

        assert isinstance(result, Template)
        _assert_template_structure_matches(result, expected_template)
        assert is_pyobject_tainted(result)
        assert template_to_str(result) == "Value: SECRET and public"

    def test_template_string_empty_arguments(self):
        """Test template strings with empty arguments."""
        result = ddtrace_aspects.template_string_aspect("Just a string")
        expected_template = t"Just a string"
        assert isinstance(result, Template)
        _assert_template_structure_matches(result, expected_template)
        assert not is_pyobject_tainted(result)
        assert template_to_str(result) == "Just a string"

    def test_template_string_tainted_constant_parts(self):
        """Test that tainted constant parts of template strings propagate taint."""
        tainted_template_part = taint_pyobject(
            "Template: ",
            source_name="template_source",
            source_value="Template: ",
            source_origin=OriginType.PARAMETER,
        )

        # When a string constant is tainted, it's passed as-is (not as a tuple)
        # Simulate: t"Template: {value}" where "Template: " is tainted
        result = ddtrace_aspects.template_string_aspect(tainted_template_part, ("value", "value", None, ""), "")
        value = "value"
        expected_template = t"Template: {value}"

        assert isinstance(result, Template)
        _assert_template_structure_matches(result, expected_template)
        assert is_pyobject_tainted(result)
        assert template_to_str(result) == "Template: value"

    def test_template_string_numeric_interpolation(self):
        """Test template strings with numeric interpolations and tainted parts."""
        tainted_prefix = taint_pyobject(
            "Count: ",
            source_name="prefix_source",
            source_value="Count: ",
            source_origin=OriginType.PARAMETER,
        )
        number = 42

        # Simulate: t"Count: {number}" where "Count: " is tainted
        result = ddtrace_aspects.template_string_aspect(tainted_prefix, (number, "number", None, ""), "")

        expected_template = t"Count: {number}"

        assert isinstance(result, Template)
        _assert_template_structure_matches(result, expected_template)
        assert is_pyobject_tainted(result)
        assert template_to_str(result) == "Count: 42"


def test_template_string_aspect_direct_call():
    """Direct test of template_string_aspect function."""
    # Test basic functionality
    # Simulating t"Hello {World}"
    result = ddtrace_aspects.template_string_aspect("Hello ", ("World", "name", None, ""), "")

    from string.templatelib import Interpolation

    expected_template = Template(
        "Hello ",
        Interpolation(value="World", expression="name", conversion=None, format_spec=""),
        "",
    )
    assert isinstance(result, Template)
    _assert_template_structure_matches(result, expected_template)
    assert template_to_str(result) == "Hello World"
    assert not is_pyobject_tainted(result)

    # Test with tainted input
    tainted = taint_pyobject("Tainted", source_name="test", source_value="Tainted", source_origin=OriginType.PARAMETER)
    result = ddtrace_aspects.template_string_aspect("Prefix: ", (tainted, "tainted", None, ""), "")

    expected_template = Template(
        "Prefix: ",
        Interpolation(value=tainted, expression="tainted", conversion=None, format_spec=""),
        "",
    )
    assert isinstance(result, Template)
    _assert_template_structure_matches(result, expected_template)
    assert template_to_str(result) == "Prefix: Tainted"
    assert is_pyobject_tainted(result)


def test_template_string_aspect_error_handling():
    """Test that the aspect handles errors gracefully."""
    # Test with None values - simulating t"Test: {None}"
    result = ddtrace_aspects.template_string_aspect("Test: ", (None, "None", None, ""), "")

    from string.templatelib import Interpolation

    expected_template = Template(
        "Test: ",
        Interpolation(value=None, expression="None", conversion=None, format_spec=""),
        "",
    )
    assert isinstance(result, Template)
    _assert_template_structure_matches(result, expected_template)
    assert "None" in template_to_str(result)

    # Test with empty args - just a string constant
    result = ddtrace_aspects.template_string_aspect("")
    expected_template = t""
    assert isinstance(result, Template)
    _assert_template_structure_matches(result, expected_template)
    assert template_to_str(result) == ""


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
        tainted_input = "World"
        result = mod.do_template_simple(tainted_input)
        expected_template = t"{tainted_input}"
        assert isinstance(result, Template)
        _assert_template_structure_matches(result, expected_template)
        assert template_to_str(result) == "World"
        assert not is_pyobject_tainted(result)

    def test_template_simple_with_taint(self):
        """Test simple template string with tainted argument."""
        tainted_input = taint_pyobject(
            "World", source_name="test_source", source_value="World", source_origin=OriginType.PARAMETER
        )
        result = mod.do_template_simple(tainted_input)
        expected_template = t"{tainted_input}"
        assert isinstance(result, Template)
        _assert_template_structure_matches(result, expected_template)
        assert template_to_str(result) == "World"
        assert is_pyobject_tainted(result)
        # Template objects can be converted to evidence format for taint tracking
        ranges = get_tainted_ranges(result)
        assert len(ranges) == 1
        assert ranges[0].start == 0
        assert ranges[0].length == len("World")

    def test_template_with_text_no_taint(self):
        """Test template string with text prefix, no taint."""
        tainted_input = "World"
        result = mod.do_template_with_text(tainted_input)
        expected_template = t"Hello {tainted_input}"
        assert isinstance(result, Template)
        _assert_template_structure_matches(result, expected_template)
        assert template_to_str(result) == "Hello World"
        assert not is_pyobject_tainted(result)

    def test_template_with_text_tainted_arg(self):
        """Test template string with text prefix and tainted argument."""
        tainted_input = taint_pyobject(
            "World", source_name="test_source", source_value="World", source_origin=OriginType.PARAMETER
        )
        result = mod.do_template_with_text(tainted_input)
        expected_template = t"Hello {tainted_input}"
        # Check result is a Template
        assert isinstance(result, Template)
        _assert_template_structure_matches(result, expected_template)
        # Check taint propagation
        assert is_pyobject_tainted(result)
        ranges = get_tainted_ranges(result)
        assert len(ranges) > 0

    def test_template_multiple_args_no_taint(self):
        """Test template string with multiple arguments, no taint."""
        tainted_a = "foo"
        tainted_b = "bar"
        result = mod.do_template_multiple_args(tainted_a, tainted_b)

        from string.templatelib import Interpolation

        expected_template = Template(
            "",
            Interpolation(value=tainted_a, expression="tainted_a", conversion=None, format_spec=""),
            " and ",
            Interpolation(value=tainted_b, expression="tainted_b", conversion=None, format_spec=""),
            "",
        )
        assert isinstance(result, Template)
        _assert_template_structure_matches(result, expected_template)
        assert template_to_str(result) == "foo and bar"
        assert not is_pyobject_tainted(result)

    def test_template_multiple_args_first_tainted(self):
        """Test template string with first argument tainted."""
        tainted_first = taint_pyobject(
            "foo", source_name="source1", source_value="foo", source_origin=OriginType.PARAMETER
        )
        tainted_b = "bar"
        result = mod.do_template_multiple_args(tainted_first, tainted_b)

        from string.templatelib import Interpolation

        expected_template = Template(
            "",
            Interpolation(value=tainted_first, expression="tainted_a", conversion=None, format_spec=""),
            " and ",
            Interpolation(value=tainted_b, expression="tainted_b", conversion=None, format_spec=""),
            "",
        )
        assert isinstance(result, Template)
        _assert_template_structure_matches(result, expected_template)
        assert template_to_str(result) == "foo and bar"
        assert is_pyobject_tainted(result)

    def test_template_multiple_args_second_tainted(self):
        """Test template string with second argument tainted."""
        tainted_b = taint_pyobject("bar", source_name="source2", source_value="bar", source_origin=OriginType.PARAMETER)
        a = "foo"
        result = mod.do_template_multiple_args(a, tainted_b)

        from string.templatelib import Interpolation

        expected_template = Template(
            "",
            Interpolation(value=a, expression="tainted_a", conversion=None, format_spec=""),
            " and ",
            Interpolation(value=tainted_b, expression="tainted_b", conversion=None, format_spec=""),
            "",
        )
        assert isinstance(result, Template)
        _assert_template_structure_matches(result, expected_template)
        assert template_to_str(result) == "foo and bar"
        assert is_pyobject_tainted(result)

    def test_template_multiple_args_both_tainted(self):
        """Test template string with both arguments tainted."""
        tainted_first = taint_pyobject(
            "foo", source_name="source1", source_value="foo", source_origin=OriginType.PARAMETER
        )
        tainted_b = taint_pyobject("bar", source_name="source2", source_value="bar", source_origin=OriginType.PARAMETER)
        result = mod.do_template_multiple_args(tainted_first, tainted_b)

        from string.templatelib import Interpolation

        expected_template = Template(
            "",
            Interpolation(value=tainted_first, expression="tainted_a", conversion=None, format_spec=""),
            " and ",
            Interpolation(value=tainted_b, expression="tainted_b", conversion=None, format_spec=""),
            "",
        )
        assert isinstance(result, Template)
        _assert_template_structure_matches(result, expected_template)
        assert template_to_str(result) == "foo and bar"
        assert is_pyobject_tainted(result)
        # Should have multiple taint ranges
        ranges = get_tainted_ranges(result)
        assert len(ranges) >= 2

    def test_template_operations_no_taint(self):
        """Test template string with operations, no taint."""
        a = 5
        b = 3
        result = mod.do_template_operations(a, b)
        expected_template = t"{a} + {b} = {a + b}"
        assert isinstance(result, Template)
        _assert_template_structure_matches(result, expected_template)
        assert template_to_str(result) == "5 + 3 = 8"
        assert not is_pyobject_tainted(result)

    def test_template_operations_more_no_taint(self):
        """Test template string with multiple operations, no taint."""
        a = 10
        b = 4
        result = mod.do_template_operations_more(a, b)
        expected_template = t"{a} + {b} = {a + b}; {a} * {b} = {a * b}; {a} - {b} = {a - b}"
        assert isinstance(result, Template)
        _assert_template_structure_matches(result, expected_template)
        assert template_to_str(result) == "10 + 4 = 14; 10 * 4 = 40; 10 - 4 = 6"
        assert not is_pyobject_tainted(result)

    def test_template_operations_with_parens_no_taint(self):
        """Test precedence-sensitive parentheses expressions, no taint."""
        a = 5
        b = 3
        result = mod.do_template_operations_with_parens(a, b)
        expected_template = t"({a} + {b}) * 2 = {(a + b) * 2}"
        assert isinstance(result, Template)
        _assert_template_structure_matches(result, expected_template)
        assert template_to_str(result) == "(5 + 3) * 2 = 16"
        assert not is_pyobject_tainted(result)

    def test_template_attribute_and_subscript_no_taint(self):
        """Test template string with attribute/subscript expressions, no taint."""
        result = mod.do_template_attribute_and_subscript()

        class Obj:
            def __init__(self):
                self.value = "ok"

        obj = Obj()
        arr = ["x", "y"]
        expected_template = t"{obj.value}:{arr[1]}"
        assert isinstance(result, Template)
        _assert_template_structure_matches(result, expected_template)
        assert template_to_str(result) == "ok:y"
        assert not is_pyobject_tainted(result)

    def test_template_repr_no_taint(self):
        """Test template string with repr conversion, no taint."""
        tainted_input = "test"
        result = mod.do_template_repr(tainted_input)
        expected_template = t"{tainted_input!r}"
        assert isinstance(result, Template)
        _assert_template_structure_matches(result, expected_template)
        assert template_to_str(result) == "'test'"
        assert not is_pyobject_tainted(result)

    def test_template_repr_with_taint(self):
        """Test template string with repr conversion and tainted input."""
        tainted_input = taint_pyobject(
            "test", source_name="test_source", source_value="test", source_origin=OriginType.PARAMETER
        )
        result = mod.do_template_repr(tainted_input)
        expected_template = t"{tainted_input!r}"
        assert isinstance(result, Template)
        _assert_template_structure_matches(result, expected_template)
        assert template_to_str(result) == template_to_str(expected_template)
        assert is_pyobject_tainted(result)

    def test_template_str_conversion_no_taint(self):
        """Test template string with str conversion, no taint."""
        tainted_input = 42
        result = mod.do_template_str_conversion(tainted_input)

        from string.templatelib import Interpolation

        expected_template = Template(
            "",
            Interpolation(value=tainted_input, expression="tainted_input", conversion="s", format_spec=""),
            "",
        )
        assert isinstance(result, Template)
        _assert_template_structure_matches(result, expected_template)
        assert template_to_str(result) == "42"
        assert not is_pyobject_tainted(result)

    def test_template_str_conversion_with_taint(self):
        """Test template string with str conversion and tainted input."""
        tainted_input = taint_pyobject(
            "value", source_name="test_source", source_value="value", source_origin=OriginType.PARAMETER
        )
        result = mod.do_template_str_conversion(tainted_input)
        expected_template = t"{tainted_input!s}"
        assert isinstance(result, Template)
        _assert_template_structure_matches(result, expected_template)
        assert template_to_str(result) == "value"
        assert is_pyobject_tainted(result)

    def test_template_complex_no_taint(self):
        """Test complex template string with multiple parts, no taint."""
        tainted_prefix = "Config"
        tainted_name = "timeout"
        tainted_value = 30
        result = mod.do_template_complex(tainted_prefix, tainted_name, tainted_value)
        expected_template = t"{tainted_prefix}: {tainted_name} = {tainted_value}"
        assert isinstance(result, Template)
        _assert_template_structure_matches(result, expected_template)
        assert template_to_str(result) == "Config: timeout = 30"
        assert not is_pyobject_tainted(result)

    def test_template_complex_with_tainted_prefix(self):
        """Test complex template string with tainted prefix."""
        tainted_prefix = taint_pyobject(
            "Config", source_name="prefix_source", source_value="Config", source_origin=OriginType.PARAMETER
        )
        tainted_name = "timeout"
        tainted_value = 30
        result = mod.do_template_complex(tainted_prefix, tainted_name, tainted_value)
        expected_template = t"{tainted_prefix}: {tainted_name} = {tainted_value}"
        assert isinstance(result, Template)
        _assert_template_structure_matches(result, expected_template)
        assert template_to_str(result) == "Config: timeout = 30"
        assert is_pyobject_tainted(result)

    def test_template_complex_with_tainted_name(self):
        """Test complex template string with tainted name."""
        tainted_name = taint_pyobject(
            "timeout", source_name="name_source", source_value="timeout", source_origin=OriginType.PARAMETER
        )
        tainted_prefix = "Config"
        tainted_value = 30
        result = mod.do_template_complex(tainted_prefix, tainted_name, tainted_value)
        expected_template = t"{tainted_prefix}: {tainted_name} = {tainted_value}"
        assert isinstance(result, Template)
        _assert_template_structure_matches(result, expected_template)
        assert template_to_str(result) == "Config: timeout = 30"
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
        expected_template = t"{tainted_prefix}: {tainted_name} = {tainted_value}"
        assert isinstance(result, Template)
        _assert_template_structure_matches(result, expected_template)
        assert template_to_str(result) == "Config: timeout = 30"
        assert is_pyobject_tainted(result)
        # Should have multiple taint ranges
        ranges = get_tainted_ranges(result)
        assert len(ranges) >= 3

    def test_template_only_text_no_taint(self):
        """Test template string with only text (no interpolations)."""
        result = mod.do_template_only_text()
        expected_template = t"Just plain text"
        assert isinstance(result, Template)
        _assert_template_structure_matches(result, expected_template)
        assert template_to_str(result) == "Just plain text"
        # No taint since there are no interpolations
        assert not is_pyobject_tainted(result)

    def test_template_nested_expr_no_taint(self):
        """Test template string with nested expressions."""
        result = mod.do_template_nested_expr()
        expected_template = t"Result: {True or False}"
        assert isinstance(result, Template)
        _assert_template_structure_matches(result, expected_template)
        assert template_to_str(result) == "Result: True"
        assert not is_pyobject_tainted(result)

    def test_template_method_call_no_taint(self):
        """Test template string with method call."""
        result = mod.do_template_method_call()
        expected_template = t"Hello {'world'.upper()}"
        assert isinstance(result, Template)
        _assert_template_structure_matches(result, expected_template)
        assert template_to_str(result) == "Hello WORLD"
        assert not is_pyobject_tainted(result)

    def test_template_with_format_spec_no_taint(self):
        """Test template string with format specification, no taint."""
        result = mod.do_template_with_format_spec(42, "05d")
        # Template structure: t"{a:{spec}}" where a=42, spec="05d"
        assert isinstance(result, Template)
        assert template_to_str(result) == "00042"
        assert len(result.interpolations) == 1
        assert result.interpolations[0].value == 42
        assert result.interpolations[0].format_spec == "05d"
        assert not is_pyobject_tainted(result)

    def test_template_with_format_spec_tainted_value(self):
        """Test template string with format specification, tainted value."""
        tainted_value = taint_pyobject(
            42, source_name="test_source", source_value="42", source_origin=OriginType.PARAMETER
        )
        result = mod.do_template_with_format_spec(tainted_value, "05d")
        assert isinstance(result, Template)
        assert template_to_str(result) == "00042"
        # Non-string values don't propagate taint through format specs in this implementation
        # This behavior matches the existing taint propagation rules

    def test_template_repr_twice_no_taint(self):
        """Test template string with repr conversion used twice."""
        result = mod.do_template_repr_twice("test")
        # Template structure: t"{a!r} {a!r}" where a="test"
        assert isinstance(result, Template)
        assert template_to_str(result) == "'test' 'test'"
        assert len(result.interpolations) == 2
        assert result.interpolations[0].value == "test"
        assert result.interpolations[0].conversion == "r"
        assert result.interpolations[1].value == "test"
        assert result.interpolations[1].conversion == "r"
        assert not is_pyobject_tainted(result)

    def test_template_repr_twice_with_taint(self):
        """Test template string with repr conversion used twice, with tainted input."""
        tainted_input = taint_pyobject(
            "test", source_name="test_source", source_value="test", source_origin=OriginType.PARAMETER
        )
        result = mod.do_template_repr_twice(tainted_input)
        assert isinstance(result, Template)
        assert template_to_str(result) == "'test' 'test'"
        assert is_pyobject_tainted(result)

    def test_template_repr_twice_different_no_taint(self):
        """Test template string with repr conversion on different objects, no taint."""
        result = mod.do_template_repr_twice_different("foo", "bar")
        # Template structure: t"{a!r} {b!r}" where a="foo", b="bar"
        assert isinstance(result, Template)
        assert template_to_str(result) == "'foo' 'bar'"
        assert len(result.interpolations) == 2
        assert result.interpolations[0].value == "foo"
        assert result.interpolations[0].conversion == "r"
        assert result.interpolations[1].value == "bar"
        assert result.interpolations[1].conversion == "r"
        assert not is_pyobject_tainted(result)

    def test_template_repr_twice_different_with_taint(self):
        """Test template string with repr conversion on different objects, both tainted."""
        tainted_a = taint_pyobject(
            "foo", source_name="source_a", source_value="foo", source_origin=OriginType.PARAMETER
        )
        tainted_b = taint_pyobject(
            "bar", source_name="source_b", source_value="bar", source_origin=OriginType.PARAMETER
        )
        result = mod.do_template_repr_twice_different(tainted_a, tainted_b)
        assert isinstance(result, Template)
        assert template_to_str(result) == "'foo' 'bar'"
        assert is_pyobject_tainted(result)
        ranges = get_tainted_ranges(result)
        assert len(ranges) >= 2

    def test_template_ascii_conversion_no_taint(self):
        """Test template string with ascii conversion."""
        result = mod.do_template_ascii_conversion("test")
        # Template structure: t"{a!a}" where a="test"
        assert isinstance(result, Template)
        assert template_to_str(result) == "'test'"
        assert len(result.interpolations) == 1
        assert result.interpolations[0].value == "test"
        assert result.interpolations[0].conversion == "a"
        assert not is_pyobject_tainted(result)

    def test_template_ascii_conversion_with_taint(self):
        """Test template string with ascii conversion, tainted input."""
        tainted_input = taint_pyobject(
            "test", source_name="test_source", source_value="test", source_origin=OriginType.PARAMETER
        )
        result = mod.do_template_ascii_conversion(tainted_input)
        assert isinstance(result, Template)
        assert template_to_str(result) == "'test'"
        assert is_pyobject_tainted(result)

    def test_template_ascii_conversion_unicode(self):
        """Test template string with ascii conversion on unicode characters."""
        result = mod.do_template_ascii_conversion("café")
        # Template structure: t"{a!a}" where a="café"
        assert isinstance(result, Template)
        # ascii() escapes non-ASCII characters
        assert template_to_str(result) == "'caf\\xe9'"
        assert len(result.interpolations) == 1
        assert result.interpolations[0].value == "café"
        assert result.interpolations[0].conversion == "a"
        assert not is_pyobject_tainted(result)

    def test_template_with_format_no_taint(self):
        """Test template string with format specification."""
        result = mod.do_template_with_format("test")
        # Template structure: t"{a:10}" where a="test"
        assert isinstance(result, Template)
        assert template_to_str(result) == "test      "  # padded to 10 chars
        assert len(result.interpolations) == 1
        assert result.interpolations[0].value == "test"
        assert result.interpolations[0].format_spec == "10"
        assert not is_pyobject_tainted(result)

    def test_template_with_format_tainted(self):
        """Test template string with format specification, tainted input."""
        tainted_input = taint_pyobject(
            "test", source_name="test_source", source_value="test", source_origin=OriginType.PARAMETER
        )
        result = mod.do_template_with_format(tainted_input)
        assert isinstance(result, Template)
        assert template_to_str(result) == "test      "  # padded to 10 chars
        assert is_pyobject_tainted(result)

    def test_template_empty_interpolation_no_taint(self):
        """Test template string with empty string interpolation."""
        result = mod.do_template_empty_interpolation()
        # Template structure: t"Value: {''}"
        assert isinstance(result, Template)
        assert template_to_str(result) == "Value: "
        assert len(result.interpolations) == 1
        assert result.interpolations[0].value == ""
        assert not is_pyobject_tainted(result)

    def test_template_multiline_no_taint(self):
        """Test multiline template string."""
        result = mod.do_template_multiline("foo", "bar")
        # Template structure: t"""First: {a}\nSecond: {b}""" where a="foo", b="bar"
        assert isinstance(result, Template)
        assert template_to_str(result) == "First: foo\nSecond: bar"
        assert len(result.interpolations) == 2
        assert result.interpolations[0].value == "foo"
        assert result.interpolations[1].value == "bar"
        assert not is_pyobject_tainted(result)

    def test_template_multiline_with_taint(self):
        """Test multiline template string with tainted inputs."""
        tainted_a = taint_pyobject(
            "foo", source_name="source_a", source_value="foo", source_origin=OriginType.PARAMETER
        )
        tainted_b = taint_pyobject(
            "bar", source_name="source_b", source_value="bar", source_origin=OriginType.PARAMETER
        )
        result = mod.do_template_multiline(tainted_a, tainted_b)
        assert isinstance(result, Template)
        assert template_to_str(result) == "First: foo\nSecond: bar"
        assert is_pyobject_tainted(result)
        ranges = get_tainted_ranges(result)
        assert len(ranges) >= 2

    def test_template_with_exception_object(self):
        """Test template string with Exception object created inline."""
        result = mod.do_template_with_exception()
        # Template structure: t"template {Exception('Testst')}"
        assert isinstance(result, Template)
        # The string representation of the template includes the Exception str representation
        result_str = template_to_str(result)
        assert result_str.startswith("template ")
        assert "Testst" in result_str
        # Verify we have one interpolation
        assert len(result.interpolations) == 1
        # The interpolation value should be an Exception instance
        assert isinstance(result.interpolations[0].value, Exception)
        assert str(result.interpolations[0].value) == "Testst"
        # The expression should be the original Exception constructor call
        assert result.interpolations[0].expression == "Exception('Testst')"
        # No taint since we're not tainting any input
        assert not is_pyobject_tainted(result)


def test_template_string_aspect_exception_path_returns_template(monkeypatch):
    from string.templatelib import Interpolation
    from string.templatelib import Template

    # Force an exception inside template_string_aspect after it has constructed
    # the Template object, so the exception handler path is executed.
    def _raise(*args, **kwargs):
        raise RuntimeError("forced")

    monkeypatch.setattr(ddtrace_aspects, "taint_pyobject_with_ranges", _raise)

    tainted = taint_pyobject("World", source_name="test", source_value="World", source_origin=OriginType.PARAMETER)
    result = ddtrace_aspects.template_string_aspect("Hello ", (tainted, "tainted", None, ""), "")

    expected_template = Template(
        "Hello ",
        Interpolation(value=tainted, expression="tainted", conversion=None, format_spec=""),
        "",
    )

    assert isinstance(result, Template)
    _assert_template_structure_matches(result, expected_template)
    assert template_to_str(result) == template_to_str(expected_template)


def test_template_string_is_pyobject_tainted_raises_falls_back(monkeypatch):
    from string.templatelib import Interpolation
    from string.templatelib import Template

    def _raise(*args, **kwargs):
        raise RuntimeError("forced")

    monkeypatch.setattr(ddtrace_aspects, "is_pyobject_tainted", _raise)

    tainted = taint_pyobject("World", source_name="test", source_value="World", source_origin=OriginType.PARAMETER)
    result = ddtrace_aspects.template_string_aspect("Hello ", (tainted, "tainted", None, ""), "")

    expected_template = Template(
        "Hello ",
        Interpolation(value=tainted, expression="tainted", conversion=None, format_spec=""),
        "",
    )

    assert isinstance(result, Template)
    _assert_template_structure_matches(result, expected_template)
    assert template_to_str(result) == template_to_str(expected_template)


def test_template_string_taint_ranges_positions():
    """Test that taint ranges are positioned correctly in template strings."""
    # Create tainted inputs
    tainted_a = taint_pyobject("AAA", source_name="source_a", source_value="AAA", source_origin=OriginType.PARAMETER)
    tainted_b = taint_pyobject("BBB", source_name="source_b", source_value="BBB", source_origin=OriginType.PARAMETER)

    # Use template: t"{a} and {b}" which produces "AAA and BBB"
    result = mod.do_template_multiple_args(tainted_a, tainted_b)
    expected_template = t"{tainted_a} and {tainted_b}"
    assert isinstance(result, Template)
    _assert_template_structure_matches(result, expected_template)
    assert template_to_str(result) == "AAA and BBB"
    assert is_pyobject_tainted(result)

    ranges = get_tainted_ranges(result)
    # We should have at least 2 ranges (one for each tainted input)
    assert len(ranges) >= 2


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
    # Simulate: t"Value: {value}" - AST passes ("Value: ", (value, "value", None, ""), "")
    result = ddtrace_aspects.template_string_aspect("Value: ", (value, "value", None, ""), "")

    from string.templatelib import Interpolation

    expected_template = Template(
        "Value: ",
        Interpolation(value=value, expression="value", conversion=None, format_spec=""),
        "",
    )

    assert isinstance(result, Template)
    _assert_template_structure_matches(result, expected_template)

    if expected is None:
        # For unpredictable outputs like sets, just verify no exception
        assert "Value: " in template_to_str(result)
    else:
        assert template_to_str(result) == f"Value: {expected}"

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
def test_template_string_aspect_tainted_non_string_types(iast_context_defaults, value):
    """Test that tainted non-string types propagate taint correctly."""
    # Note: Only strings/bytes/bytearrays can be tainted
    # But if a tainted string is in the same template, the result should be tainted
    tainted_str = taint_pyobject(
        "tainted", source_name="test_source", source_value="tainted", source_origin=OriginType.PARAMETER
    )

    # Simulate: t"{tainted_str}: {value}"
    result = ddtrace_aspects.template_string_aspect(
        "", (tainted_str, "tainted", None, ""), ": ", (value, "value", None, ""), ""
    )

    from string.templatelib import Interpolation

    expected_template = Template(
        "",
        Interpolation(value=tainted_str, expression="tainted", conversion=None, format_spec=""),
        ": ",
        Interpolation(value=value, expression="value", conversion=None, format_spec=""),
        "",
    )

    # Result should be tainted because one of the inputs is tainted
    assert isinstance(result, Template)
    _assert_template_structure_matches(result, expected_template)
    assert is_pyobject_tainted(result)


def test_template_string_aspect_empty_values():
    """Test template string aspect with empty values."""
    # Empty strings - t"{a}{b}" with empty values
    result = ddtrace_aspects.template_string_aspect("", ("", "a", None, ""), "", ("", "b", None, ""), "")

    from string.templatelib import Interpolation

    expected_template = Template(
        "",
        Interpolation(value="", expression="a", conversion=None, format_spec=""),
        "",
        Interpolation(value="", expression="b", conversion=None, format_spec=""),
        "",
    )
    assert isinstance(result, Template)
    _assert_template_structure_matches(result, expected_template)
    assert template_to_str(result) == ""
    assert not is_pyobject_tainted(result)

    # Mix of empty and non-empty
    result = ddtrace_aspects.template_string_aspect("Start", ("", "a", None, ""), "End")

    expected_template = Template(
        "Start",
        Interpolation(value="", expression="a", conversion=None, format_spec=""),
        "End",
    )
    assert isinstance(result, Template)
    _assert_template_structure_matches(result, expected_template)
    assert template_to_str(result) == "StartEnd"
    assert not is_pyobject_tainted(result)


def test_template_string_aspect_large_html_template():
    """Test template string aspect with a large HTML-like string."""
    title = "Welcome"
    username = "john_doe"
    content = "This is a long content message with lots of text."

    # Simulate: t"<!DOCTYPE...><title>{title}</title>...<h1>Hello, {username}!</h1>...<p>{content}</p>..."
    result = ddtrace_aspects.template_string_aspect(
        "<!DOCTYPE html>\n<html>\n<head>\n    <title>",
        (title, "title", None, ""),
        "</title>\n</head>\n<body>\n    <h1>Hello, ",
        (username, "username", None, ""),
        "!</h1>\n    <p>",
        (content, "content", None, ""),
        "</p>\n</body>\n</html>",
    )
    expected_template = (
        t"<!DOCTYPE html>\n<html>\n<head>\n    <title>{title}</title>\n</head>\n<body>\n    <h1>Hello, {username}!"
        t"</h1>\n    <p>{content}</p>\n</body>\n</html>"
    )

    expected = (
        "<!DOCTYPE html>\n<html>\n<head>\n    <title>"
        + title
        + "</title>\n</head>\n<body>\n    <h1>Hello, "
        + username
        + "!</h1>\n    <p>"
        + content
        + "</p>\n</body>\n</html>"
    )

    assert isinstance(result, Template)
    _assert_template_structure_matches(result, expected_template)
    assert template_to_str(result) == expected
    assert not is_pyobject_tainted(result)


def test_template_string_aspect_large_html_template_with_taint(iast_context_defaults):
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
    result = ddtrace_aspects.template_string_aspect(
        "<!DOCTYPE html>\n<html>\n<head>\n    <title>",
        (title, "title", None, ""),
        "</title>\n</head>\n<body>\n    <h1>Hello, ",
        (username, "username", None, ""),
        "!</h1>\n    <p>",
        (content, "content", None, ""),
        "</p>\n</body>\n</html>",
    )
    expected_template = (
        t"<!DOCTYPE html>\n<html>\n<head>\n    <title>{title}</title>\n</head>\n<body>\n    <h1>Hello, {username}!"
        t"</h1>\n    <p>{content}</p>\n</body>\n</html>"
    )
    # Result should be tainted because username and content are tainted
    assert isinstance(result, Template)
    _assert_template_structure_matches(result, expected_template)
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
    assert template_to_str(result) == expected


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
    # Simulate: t"{v0}{v1}{v2}..." - create proper args with tuples for interpolations
    args = []
    for i, v in enumerate(values):
        if i > 0:
            args.append("")  # Empty string between interpolations
        args.append((v, f"v{i}", None, ""))
    args.append("")  # Final empty string

    result = ddtrace_aspects.template_string_aspect(*args)

    from string.templatelib import Interpolation

    expected_parts = []
    for i, v in enumerate(values):
        if i > 0:
            expected_parts.append("")
        expected_parts.append(Interpolation(value=v, expression=f"v{i}", conversion=None, format_spec=""))
    expected_parts.append("")
    expected_template = Template(*expected_parts)

    # Build expected result
    expected = "".join(str(v) for v in values)
    assert isinstance(result, Template)
    _assert_template_structure_matches(result, expected_template)
    assert template_to_str(result) == expected


def test_template_string_aspect_no_exception_on_error():
    """Test that template string aspect never raises exceptions, uses fallback."""
    # This should not raise an exception even with problematic inputs
    # The fallback mechanism should handle it

    # Test with various edge cases - just a string constant
    result1 = ddtrace_aspects.template_string_aspect("")
    expected_template1 = t""
    assert isinstance(result1, Template)
    _assert_template_structure_matches(result1, expected_template1)
    assert template_to_str(result1) == ""

    # Test with string constants only (no interpolations)
    result2 = ddtrace_aspects.template_string_aspect("justnormalstrings")
    expected_template2 = t"justnormalstrings"
    assert isinstance(result2, Template)
    _assert_template_structure_matches(result2, expected_template2)
    assert template_to_str(result2) == "justnormalstrings"

    # All of these should work without exceptions - t"{None}{None}{None}"
    result3 = ddtrace_aspects.template_string_aspect(
        "", (None, "a", None, ""), "", (None, "b", None, ""), "", (None, "c", None, ""), ""
    )

    from string.templatelib import Interpolation

    expected_template3 = Template(
        "",
        Interpolation(value=None, expression="a", conversion=None, format_spec=""),
        "",
        Interpolation(value=None, expression="b", conversion=None, format_spec=""),
        "",
        Interpolation(value=None, expression="c", conversion=None, format_spec=""),
        "",
    )
    assert isinstance(result3, Template)
    _assert_template_structure_matches(result3, expected_template3)
    assert "None" in template_to_str(result3)


def test_template_string_aspect_preserves_result_value():
    """Test that aspect never changes the actual result string value."""
    # Simulate: t"Hello World"
    result = ddtrace_aspects.template_string_aspect("Hello", (" ", "space", None, ""), "World")

    from string.templatelib import Interpolation

    expected_template = Template(
        "Hello",
        Interpolation(value=" ", expression="space", conversion=None, format_spec=""),
        "World",
    )
    assert isinstance(result, Template)
    _assert_template_structure_matches(result, expected_template)
    assert template_to_str(result) == "Hello World", f"Expected Hello World, got {template_to_str(result)}"


def test_template_string_aspect_bytes_and_bytearray():
    """Test template string aspect with bytes and bytearray values."""
    # Bytes should be converted to string representation - t"Bytes: {b'hello'}"
    result = ddtrace_aspects.template_string_aspect("Bytes: ", (b"hello", "b", None, ""), "")

    from string.templatelib import Interpolation

    expected_template = Template(
        "Bytes: ",
        Interpolation(value=b"hello", expression="b", conversion=None, format_spec=""),
        "",
    )
    assert isinstance(result, Template)
    _assert_template_structure_matches(result, expected_template)
    assert template_to_str(result) == "Bytes: b'hello'"

    # Bytearray should be converted to string representation
    result = ddtrace_aspects.template_string_aspect("Bytearray: ", (bytearray(b"world"), "ba", None, ""), "")

    expected_template = Template(
        "Bytearray: ",
        Interpolation(value=bytearray(b"world"), expression="ba", conversion=None, format_spec=""),
        "",
    )
    assert isinstance(result, Template)
    _assert_template_structure_matches(result, expected_template)
    assert template_to_str(result) == "Bytearray: bytearray(b'world')"


def test_template_string_aspect_deeply_nested_structures():
    """Test template string aspect with deeply nested data structures."""
    nested_dict = {"level1": {"level2": {"level3": "deep"}}}
    nested_list = [[[1, 2], [3, 4]], [[5, 6], [7, 8]]]

    # Simulate: t"Dict: {nested_dict} List: {nested_list}"
    result = ddtrace_aspects.template_string_aspect(
        "Dict: ", (nested_dict, "dict", None, ""), " List: ", (nested_list, "list", None, ""), ""
    )

    from string.templatelib import Interpolation

    expected_template = Template(
        "Dict: ",
        Interpolation(value=nested_dict, expression="dict", conversion=None, format_spec=""),
        " List: ",
        Interpolation(value=nested_list, expression="list", conversion=None, format_spec=""),
        "",
    )

    # Should not raise exception
    assert isinstance(result, Template)
    _assert_template_structure_matches(result, expected_template)
    assert "Dict: " in template_to_str(result)
    assert "List: " in template_to_str(result)

    # Verify the result matches string conversion
    expected = f"Dict: {nested_dict} List: {nested_list}"
    assert template_to_str(result) == expected
