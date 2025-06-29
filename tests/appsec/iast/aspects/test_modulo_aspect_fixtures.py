# -*- encoding: utf-8 -*-
import math
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

        assert result == expected_result
        assert as_formatted_evidence(result, tag_mapping_function=None) == escaped_expected_result

    def test_modulo_when_template_is_none_then_raises_attribute_error(self):  # type: () -> None
        with pytest.raises(AttributeError):
            mod.do_modulo(None, "")

    def test_modulo_when_parameter_is_none_then_does_not_break(self):  # type: () -> None
        assert mod.do_modulo("%s", None) == "None"

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
