# -*- encoding: utf-8 -*-
import math
import re
from typing import Any  # noqa:F401
from typing import List  # noqa:F401
from typing import Text  # noqa:F401

from hypothesis import given
from hypothesis.strategies import text
import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import as_formatted_evidence
from ddtrace.appsec._iast._taint_tracking import get_ranges
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import get_tainted_ranges
from tests.appsec.iast.aspects.aspect_utils import BaseReplacement
from tests.appsec.iast.aspects.aspect_utils import _to_tainted_string_with_origin
from tests.appsec.iast.iast_utils import _iast_patched_module


mod = _iast_patched_module("benchmarks.bm.iast_fixtures.str_methods")


class TestOperatorModuloReplacement(BaseReplacement):
    def _assert_modulo_result(
        self,
        taint_escaped_template,  # type: Text
        taint_escaped_parameter,  # type: Any
        expected_result,  # type: Text
        escaped_expected_result,  # type: Text
    ):  # type: (...) -> None
        template = _to_tainted_string_with_origin(taint_escaped_template)

        parameter = tuple()  # type: Any
        if isinstance(taint_escaped_parameter, (tuple, List)):
            parameter = tuple([_to_tainted_string_with_origin(item) for item in taint_escaped_parameter])
        else:
            parameter = _to_tainted_string_with_origin(taint_escaped_parameter)

        result = mod.do_modulo(template, parameter)

        assert result == expected_result, f"Expected  {expected_result}. RESULT: {result}"
        assert as_formatted_evidence(result, tag_mapping_function=None) == escaped_expected_result

    def test_modulo_when_template_is_none_then_raises_attribute_error(self):  # type: () -> None
        with pytest.raises(TypeError, match=re.escape("unsupported operand type(s) for %: 'NoneType' and 'str'")):
            mod.do_modulo(None, "")

    def test_modulo_when_parameter_is_none_then_does_not_break(self):  # type: () -> None
        assert mod.do_modulo("%s", None) == "None"

    def test_modulo_between_ints(self):  # type: () -> None
        exists = False
        for counter in range(100):
            if mod.do_modulo(counter, 10) == 0:
                exists = True
        assert exists

    def test_modulo_when_positional_no_tainted_then_no_tainted_result(self):  # type: () -> None
        result = mod.do_modulo("template %s", "parameter")
        assert result, "template parameter"
        assert not get_ranges(result)

    def test_modulo_when_tainted_parameter_then_tainted_result(self):  # type: () -> None
        self._assert_modulo_result(
            taint_escaped_template="template %s",
            taint_escaped_parameter=":+-<input1>parameter<input1>-+:",
            expected_result="template parameter",
            escaped_expected_result="template :+-<input1>parameter<input1>-+:",
        )

    def test_modulo_when_tainted_template_range_no_percent_then_tainted_result(self):  # type: () -> None
        self._assert_modulo_result(
            taint_escaped_template=":+-<input1>template<input1>-+: %s",
            taint_escaped_parameter="parameter",
            expected_result="template parameter",
            escaped_expected_result=":+-<input1>template<input1>-+: parameter",
        )

    def test_modulo_when_tainted_template_range_with_percent_then_tainted_result(self):  # type: () -> None
        self._assert_modulo_result(
            taint_escaped_template="template :+-<input1>%s<input1>-+:",
            taint_escaped_parameter="parameter",
            expected_result="template parameter",
            escaped_expected_result="template :+-<input1>parameter<input1>-+:",
        )

    def test_modulo_when_multiple_tainted_parameter_then_tainted_result(self):  # type: () -> None
        self._assert_modulo_result(
            taint_escaped_template="template %s %s",
            taint_escaped_parameter=[":+-<input1>p1<input1>-+:", ":+-<input2>p2<input2>-+:"],
            expected_result="template p1 p2",
            escaped_expected_result="template :+-<input1>p1<input1>-+: :+-<input2>p2<input2>-+:",
        )

    def test_modulo_when_parameters_and_tainted_template_range_no_percent_then_tainted_result(self):  # type: () -> None
        self._assert_modulo_result(
            taint_escaped_template=":+-<input1>template<input1>-+: %s %s",
            taint_escaped_parameter=["p1", "p2"],
            expected_result="template p1 p2",
            escaped_expected_result=":+-<input1>template<input1>-+: p1 p2",
        )

    def test_modulo_when_parameters_and_tainted_template_range_with_percent_then_tainted_result(
        self,
    ):  # type: () -> None
        self._assert_modulo_result(
            taint_escaped_template="template :+-<input1>%s %s<input1>-+:",
            taint_escaped_parameter=["p1", "p2"],
            expected_result="template p1 p2",
            escaped_expected_result="template :+-<input1>p1 p2<input1>-+:",
        )

    def test_modulo_when_tainted_template_range_no_percent_and_tainted_param_then_tainted(self):  # type: () -> None
        self._assert_modulo_result(
            taint_escaped_template=":+-<input1>template<input1>-+: %s",
            taint_escaped_parameter=":+-<input2>parameter<input2>-+:",
            expected_result="template parameter",
            escaped_expected_result=":+-<input1>template<input1>-+: :+-<input2>parameter<input2>-+:",
        )

    def test_modulo_when_tainted_template_range_with_percent_and_tainted_param_then_tainted(self):  # type: () -> None
        self._assert_modulo_result(
            taint_escaped_template=":+-<input1>template %s<input1>-+:",
            taint_escaped_parameter=":+-<input1>parameter<input2>-+:",
            expected_result="template parameter",
            escaped_expected_result=":+-<input1>template <input1>-+::+-<input2>parameter<input2>-+:",
        )

    def test_modulo_when_ranges_overlap_then_give_preference_to_ranges_from_parameter(self):  # type: () -> None
        self._assert_modulo_result(
            taint_escaped_template=":+-<input1>template %s range overlapping<input1>-+:",
            taint_escaped_parameter=":+-<input2>parameter<input2>-+:",
            expected_result="template parameter range overlapping",
            escaped_expected_result=":+-<input1>template <input1>-+:"
            ":+-<input2>parameter<input2>-+:"
            ":+-<input1> range overlapping<input1>-+:",
        )

    def test_modulo_when_tainted_str_emoji_strings_then_tainted_result(self):  # type: () -> None
        self._assert_modulo_result(
            taint_escaped_template=":+-<input1>template⚠️<input1>-+: %s",
            taint_escaped_parameter=":+-<input2>parameter⚠️<input2>-+:",
            expected_result="template⚠️ parameter⚠️",
            escaped_expected_result=":+-<input1>template⚠️<input1>-+: :+-<input2>parameter⚠️<input2>-+:",
        )

    def test_modulo_when_tainted_unicode_emoji_strings_then_tainted_result(self):  # type: () -> None
        self._assert_modulo_result(
            taint_escaped_template=":+-<input1>template⚠️<input1>-+: %s",
            taint_escaped_parameter=":+-<input2>parameter⚠️<input2>-+:",
            expected_result="template⚠️ parameter⚠️",
            escaped_expected_result=":+-<input1>template⚠️<input1>-+: :+-<input2>parameter⚠️<input2>-+:",
        )

    def test_modulo_when_tainted_template_range_no_percent_and_param_not_str_then_tainted(self):  # type: () -> None
        self._assert_modulo_result(
            taint_escaped_template=":+-<input1>template<input1>-+: %0.2f",
            taint_escaped_parameter=math.pi,
            expected_result="template 3.14",
            escaped_expected_result=":+-<input1>template<input1>-+: 3.14",
        )

    def test_modulo_when_tainted_template_range_with_percent_and_param_not_str_then_tainted(self):  # type: () -> None
        self._assert_modulo_result(
            taint_escaped_template=":+-<input1>template %0.2f<input1>-+:",
            taint_escaped_parameter=math.pi,
            expected_result="template 3.14",
            escaped_expected_result=":+-<input1>template 3.14<input1>-+:",
        )

    def test_modulo_when_texts_tainted_and_contain_escape_sequences_then_result_uncorrupted(
        self,
    ):  # type: () -> None
        self._assert_modulo_result(
            taint_escaped_template=":+-<input1>template ::++--<0>my_code<0>--++::<input1>-+: %s",
            taint_escaped_parameter=":+-<input2>parameter<input2>-+: ::++--<0>my_code<0>--++::",
            expected_result="template :+-<0>my_code<0>-+: parameter :+-<0>my_code<0>-+:",
            escaped_expected_result=":+-<input1>template :+-<0>my_code<0>-+:<input1>-+: "
            ":+-<input2>parameter<input2>-+: "
            ":+-<0>my_code<0>-+:",
        )

    def test_modulo_when_parameter_value_already_present_in_template_then_range_is_correct(self):  # type: () -> None
        self._assert_modulo_result(
            taint_escaped_template="aaaaaa%saaa",
            taint_escaped_parameter="a:+-<input1>a<input1>-+:a",
            expected_result="aaaaaaaaaaaa",
            escaped_expected_result="aaaaaaa:+-<input1>a<input1>-+:aaaa",
        )


@pytest.mark.parametrize("is_tainted", [True, False])
@given(text())
def test_psycopg_queries_dump_bytes(is_tainted, string_data):
    string_data_to_bytes = string_data.encode("utf-8")
    bytes_to_test_orig = b"'%s'" % (string_data.encode("utf-8"))
    if is_tainted:
        bytes_to_test = taint_pyobject(
            pyobject=bytes_to_test_orig,
            source_name="string_data_to_bytes",
            source_value=bytes_to_test_orig,
            source_origin=OriginType.PARAMETER,
        )
    else:
        bytes_to_test = bytes_to_test_orig

    result = mod.psycopg_queries_dump_bytes((bytes_to_test,))
    assert (
        result
        == b'INSERT INTO "show_client" ("username") VALUES (\'%s\') RETURNING "show_client"."id"' % string_data_to_bytes
    )

    if is_tainted and string_data_to_bytes:
        ranges = get_tainted_ranges(result)
        assert len(ranges) == 1
        assert ranges[0].start == 47
        assert ranges[0].length == len(bytes_to_test_orig)
        assert result[ranges[0].start : (ranges[0].start + ranges[0].length)] == bytes_to_test_orig

    result = mod.psycopg_queries_dump_bytes_with_keys({b"name": bytes_to_test})
    assert (
        result
        == b'INSERT INTO "show_client" ("username") VALUES (\'%s\') RETURNING "show_client"."id"' % string_data_to_bytes
    )

    with pytest.raises(TypeError):
        mod.psycopg_queries_dump_bytes(
            (
                bytes_to_test,
                bytes_to_test,
            )
        )

    with pytest.raises(TypeError):
        _ = b'INSERT INTO "show_client" ("username") VALUES (%s) RETURNING "show_client"."id"' % ((1,))

    with pytest.raises(TypeError):
        mod.psycopg_queries_dump_bytes((1,))

    with pytest.raises(KeyError):
        _ = b'INSERT INTO "show_client" ("username") VALUES (%(name)s) RETURNING "show_client"."id"' % {
            "name": bytes_to_test
        }

    with pytest.raises(KeyError):
        mod.psycopg_queries_dump_bytes_with_keys({"name": bytes_to_test})


class TestModuloAspectEdgeCases:
    """Test cases for edge cases and various input types in the modulo aspect."""

    def test_modulo_with_dict_parameter(self):
        """Test modulo operation with a dictionary parameter."""
        result = mod.do_modulo("User: %(name)s, Age: %(age)d", {"name": "Alice", "age": 30})
        assert result == "User: Alice, Age: 30"

    def test_modulo_with_list_parameter(self):
        """Test modulo operation with a list parameter."""
        with pytest.raises(TypeError, match="not enough arguments for format string"):
            mod.do_modulo("%s %s", ["Hello", "World"])

    def test_modulo_with_generator_parameter_error(self):
        """Test modulo operation with a generator expression."""
        gen = (str(i) for i in [1, 2, 3])
        with pytest.raises(TypeError, match="not enough arguments for format string"):
            mod.do_modulo("%s %s %s", gen)

    def test_modulo_with_generator_parameter(self):
        """Test modulo operation with a generator expression."""
        gen = (str(i) for i in [1, 2, 3])
        result = mod.do_modulo("%s", gen)
        assert result.startswith("<generator object")

    def test_modulo_with_callable_parameter(self):
        """Test modulo operation with a callable parameter."""

        def get_name():
            return "Alice"

        result = mod.do_modulo_function("Hello, %s!", get_name)
        assert result == "Hello, Alice!"

    def test_modulo_with_callable_raising_exception(self):
        """Test modulo operation with a callable that raises an exception."""

        def problematic():
            raise ValueError("Simulated error")

        with pytest.raises(ValueError, match="Simulated error"):
            mod.do_modulo_function("Test: %s", problematic)

    def test_modulo_with_custom_object(self):
        """Test modulo operation with a custom object that has a __str__ method."""

        class TestObject:
            def __str__(self):
                return "TestObject string representation"

        result = mod.do_modulo_function("Object: %s", TestObject)
        assert result == "Object: TestObject string representation"

    def test_modulo_with_problematic_repr(self):
        """Test modulo operation with an object that has a problematic __repr__."""

        class MyException(Exception):
            pass

        class ProblematicRepr:
            def __repr__(self):
                raise MyException("Problematic __repr__")

        with pytest.raises(MyException, match="Problematic __repr__"):
            mod.do_modulo("Test: %s", ProblematicRepr())

    def test_modulo_with_named_placeholders(self):
        """Test modulo operation with named placeholders."""
        result = mod.do_modulo("%(greeting)s, %(name)s!", {"greeting": "Hello", "name": "World"})
        assert result == "Hello, World!"

    def test_modulo_with_mixed_positional_named_placeholders(self):
        """Test modulo operation with mixed positional and named placeholders."""
        with pytest.raises(TypeError, match="format requires a mapping"):
            mod.do_modulo("%(greeting)s, %s!", ({"greeting": "Hello"}, "World"))

    def test_modulo_with_empty_parameters(self):
        """Test modulo operation with empty parameters."""
        with pytest.raises(TypeError, match="not enough arguments for format string"):
            mod.do_modulo("Test: %s", ())

    def test_modulo_with_format_specifiers(self):
        """Test modulo operation with various format specifiers."""
        result = mod.do_modulo("Integer: %d, Float: %.2f, String: %s", (42, 3.14159, "test"))
        assert result == "Integer: 42, Float: 3.14, String: test"

    def test_modulo_with_unicode_characters(self):
        """Test modulo operation with unicode characters."""
        result = mod.do_modulo("Hello, %s!", "世界")
        assert result == "Hello, 世界!"

    def test_modulo_with_tainted_named_parameters(self):
        """Test modulo operation with tainted named parameters."""
        template = "%(greeting)s, %(name)s!"
        params = {
            "greeting": _to_tainted_string_with_origin(":+-<input1>Hello<input1>-+:"),
            "name": _to_tainted_string_with_origin(":+-<input2>World<input2>-+:"),
        }
        result = mod.do_modulo(template, params)
        assert result == "Hello, World!"

        # Note: Currently, taint propagation is not fully supported for named parameters in modulo operations.
        # The taint ranges are not preserved when using named placeholders in the format string.
        # This is a known limitation that may be addressed in future updates.
        ranges = get_tainted_ranges(result)
        assert len(ranges) == 0
        # The following assertions are commented out as they would fail until named parameter taint propagation
        # is implemented
        # assert ranges[0].source.name == "input1"
        # assert ranges[1].source.name == "input2"


@pytest.mark.parametrize("is_tainted", [True, False])
@given(text())
def test_psycopg_queries_dump_bytearray(is_tainted, string_data):
    string_data_to_bytesarray = bytearray(string_data.encode("utf-8"))
    bytesarray_to_test_orig = bytearray(b"'%s'" % (string_data.encode("utf-8")))
    if is_tainted:
        bytesarray_to_test = taint_pyobject(
            pyobject=bytesarray_to_test_orig,
            source_name="string_data_to_bytes",
            source_value=bytesarray_to_test_orig,
            source_origin=OriginType.PARAMETER,
        )
    else:
        bytesarray_to_test = bytesarray_to_test_orig

    result = mod.psycopg_queries_dump_bytearray((bytesarray_to_test,))
    assert (
        result
        == b'INSERT INTO "show_client" ("username") VALUES (\'%s\') RETURNING "show_client"."id"'
        % string_data_to_bytesarray
    )

    if is_tainted and string_data_to_bytesarray:
        ranges = get_tainted_ranges(result)
        assert len(ranges) == 1
        assert ranges[0].start == 47
        assert ranges[0].length == len(bytesarray_to_test_orig)
        assert result[ranges[0].start : (ranges[0].start + ranges[0].length)] == bytesarray_to_test_orig

    with pytest.raises(TypeError):
        mod.psycopg_queries_dump_bytearray(
            (
                bytesarray_to_test,
                bytesarray_to_test,
            )
        )

        with pytest.raises(TypeError):
            mod.psycopg_queries_dump_bytearray((1,))
