# -*- encoding: utf-8 -*-
from enum import Enum
import logging
import math
from typing import Any
from typing import NamedTuple

from hypothesis import given
from hypothesis.strategies import text
import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import as_formatted_evidence
from ddtrace.appsec._iast._taint_tracking._context import create_context
from ddtrace.appsec._iast._taint_tracking._context import reset_context
from ddtrace.appsec._iast._taint_tracking._taint_objects import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
import ddtrace.appsec._iast._taint_tracking.aspects as ddtrace_aspects
from tests.appsec.iast.aspects.aspect_utils import BaseReplacement
from tests.appsec.iast.aspects.aspect_utils import create_taint_range_with_format
from tests.appsec.iast.iast_utils import _iast_patched_module
from tests.utils import override_global_config


mod = _iast_patched_module("benchmarks.bm.iast_fixtures.str_methods")

EscapeContext = NamedTuple("EscapeContext", [("id", Any), ("position", int)])


@given(text())
def test_format_aspect_str(text):
    assert ddtrace_aspects.format_aspect("t-{}-t".format, 1, "t-{}-t", text) == "t-{}-t".format(text)


class TestOperatorFormatReplacement(BaseReplacement):
    def test_format_when_template_is_none_then_raises_attribute_error(self):  # type: () -> None
        with pytest.raises(AttributeError):
            mod.do_format_with_positional_parameter(None, "")

    def test_format_when_parameter_is_none_then_does_not_break(self):  # type: () -> None
        assert mod.do_format_with_positional_parameter("{}", None) == "None"

    def test_format_when_parameter_dict_none_then_does_not_break(self):  # type: () -> None
        assert mod.do_format_with_named_parameter("{key}", None) == "None"

    def test_format_when_positional_no_tainted_then_no_tainted_result(self):  # type: () -> None
        result = mod.do_format_with_positional_parameter("template {}", "parameter")
        assert as_formatted_evidence(result) == "template parameter"

    def test_format_when_named_no_tainted_then_no_tainted_result(self):  # type: () -> None
        result = mod.do_format_with_named_parameter("template {key}", "parameter")
        assert as_formatted_evidence(result) == "template parameter"

    def test_format_when_tainted_parameter_then_tainted_result(self):  # type: () -> None
        self._assert_format_result(
            taint_escaped_template="template {}",
            taint_escaped_parameter=":+-<input1>parameter<input1>-+:",
            expected_result="template parameter",
            escaped_expected_result="template :+-<input1>parameter<input1>-+:",
        )

    def test_format_when_tainted_template_range_no_brackets_then_tainted_result(self):  # type: () -> None
        self._assert_format_result(
            taint_escaped_template=":+-<input1>template<input1>-+: {}",
            taint_escaped_parameter="parameter",
            expected_result="template parameter",
            escaped_expected_result=":+-<input1>template<input1>-+: parameter",
        )

    def test_format_when_tainted_template_range_with_brackets_then_tainted_result(self):
        # type: () -> None
        self._assert_format_result(
            taint_escaped_template="template :+-<input1>{}<input1>-+:",
            taint_escaped_parameter="parameter",
            expected_result="template parameter",
            escaped_expected_result="template :+-<input1>parameter<input1>-+:",
        )

    def test_format_when_tainted_template_range_no_brackets_and_tainted_param_then_tainted(
        self,
    ):
        # type: () -> None
        self._assert_format_result(
            taint_escaped_template=":+-<input1>template<input1>-+: {}",
            taint_escaped_parameter=":+-<input2>parameter<input2>-+:",
            expected_result="template parameter",
            escaped_expected_result=":+-<input1>template<input1>-+: " ":+-<input2>parameter<input2>-+:",
        )

    def test_format_when_tainted_template_range_with_brackets_and_tainted_param_then_tainted(self):
        # type: () -> None
        self._assert_format_result(
            taint_escaped_template=":+-<input1>template {}<input1>-+:",
            taint_escaped_parameter=":+-<input1>parameter<input2>-+:",
            expected_result="template parameter",
            escaped_expected_result=":+-<input1>template <input1>-+:" ":+-<input2>parameter<input2>-+:",
        )

    def test_format_when_ranges_overlap_then_give_preference_to_ranges_from_parameter(self):
        # type: () -> None
        self._assert_format_result(
            taint_escaped_template=":+-<input1>template {} range overlapping<input1>-+:",
            taint_escaped_parameter=":+-<input2>parameter<input2>-+:",
            expected_result="template parameter range overlapping",
            escaped_expected_result=":+-<input1>template <input1>-+:"
            ":+-<input2>parameter<input2>-+:"
            ":+-<input1> range overlapping<input1>-+:",
        )

    def test_format_when_tainted_str_emoji_strings_then_tainted_result(self):
        # type: () -> None
        self._assert_format_result(
            taint_escaped_template=":+-<input1>template⚠️<input1>-+: {}",
            taint_escaped_parameter=":+-<input2>parameter⚠️<input2>-+:",
            expected_result="template⚠️ parameter⚠️",
            escaped_expected_result=":+-<input1>template⚠️<input1>-+: " ":+-<input2>parameter⚠️<input2>-+:",
        )

    def test_format_when_tainted_unicode_emoji_strings_then_tainted_result(self):
        # type: () -> None
        self._assert_format_result(
            taint_escaped_template=":+-<input1>template⚠️<input1>-+: {}",
            taint_escaped_parameter=":+-<input2>parameter⚠️<input2>-+:",
            expected_result="template⚠️ parameter⚠️",
            escaped_expected_result=":+-<input1>template⚠️<input1>-+: " ":+-<input2>parameter⚠️<input2>-+:",
        )

    def test_format_when_tainted_template_range_no_brackets_and_param_not_str_then_tainted(self):
        # type: () -> None
        self._assert_format_result(
            taint_escaped_template=":+-<input1>template<input1>-+: {:.2f}",
            taint_escaped_parameter=math.pi,
            expected_result="template 3.14",
            escaped_expected_result=":+-<input1>template<input1>-+: 3.14",
        )

    def test_format_when_tainted_template_range_with_brackets_and_param_not_str_then_tainted(self):
        # type: () -> None
        self._assert_format_result(
            taint_escaped_template=":+-<input1>template {:.2f}<input1>-+:",
            taint_escaped_parameter=math.pi,
            expected_result="template 3.14",
            escaped_expected_result=":+-<input1>template 3.14<input1>-+:",
        )

    def test_format_when_texts_tainted_and_contain_escape_sequences_then_result_uncorrupted(self):
        # type: () -> None
        self._assert_format_result(
            taint_escaped_template=":+-<input1>template ::++--<0>my_code<0>--++::" "<input1>-+: {}",
            taint_escaped_parameter=":+-<input2>parameter<input2>-+: " "::++--<0>my_code<0>--++::",
            expected_result="template :+-<0>my_code<0>-+: parameter " ":+-<0>my_code<0>-+:",
            escaped_expected_result=":+-<input1>template :+-<0>my_code<0>-+:<input1>-+: "
            ":+-<input2>parameter<input2>-+: "
            ":+-<0>my_code<0>-+:",
        )

    def test_format_when_parameter_value_already_present_in_template_then_range_is_correct(self):
        # type: () -> None
        self._assert_format_result(
            taint_escaped_template="aaaaaa{}aaa",
            taint_escaped_parameter="a:+-<input1>a<input1>-+:a",
            expected_result="aaaaaaaaaaaa",
            escaped_expected_result="aaaaaaa:+-<input1>a<input1>-+:aaaa",
        )

    def test_format_with_args_and_kwargs(self):  # type: () -> None
        string_input = "-1234 {} {test_var}"
        res = mod.do_args_kwargs_1(string_input, *[6], **{"test_var": 1})  # pylint: disable=no-member
        assert as_formatted_evidence(res) == "-1234 6 1"

        string_input = "-1234 {} {test_var}"
        res = mod.do_args_kwargs_1(string_input, 6, test_var=1)  # pylint: disable=no-member
        assert as_formatted_evidence(res) == "-1234 6 1"

        string_input = create_taint_range_with_format(":+--12-+:34 {} {test_var}")
        res = mod.do_args_kwargs_1(string_input, 6, test_var=1)  # pylint: disable=no-member
        assert as_formatted_evidence(res) == ":+--12-+:34 6 1"

    def test_format_with_one_argument_args_and_kwargs(self):  # type: () -> None
        string_input = "-1234 {} {} {test_var}"
        res = mod.do_args_kwargs_2(string_input, *[6], **{"test_var": 1})  # pylint: disable=no-member
        assert as_formatted_evidence(res) == "-1234 1 6 1"

        string_input = "-1234 {} {} {test_var}"
        res = mod.do_args_kwargs_2(string_input, 6, test_var=1)  # pylint: disable=no-member
        assert as_formatted_evidence(res) == "-1234 1 6 1"

        string_input = create_taint_range_with_format(":+--12-+:34 {} {} {test_var}")
        res = mod.do_args_kwargs_2(string_input, 6, test_var=1)  # pylint: disable=no-member
        assert as_formatted_evidence(res) == ":+--12-+:34 1 6 1"

    def test_format_with_two_argument_args_and_kwargs(self):  # type: () -> None
        string_input = "-1234 {} {} {} {test_var}"
        res = mod.do_args_kwargs_3(string_input, *[6], **{"test_var": 1})  # pylint: disable=no-member
        assert as_formatted_evidence(res) == "-1234 1 2 6 1"

        string_input = "-1234 {} {} {} {test_var}"
        res = mod.do_args_kwargs_3(string_input, 6, test_var=1)  # pylint: disable=no-member
        assert as_formatted_evidence(res) == "-1234 1 2 6 1"

        string_input = create_taint_range_with_format(":+--12-+:34 {} {} {} {test_var}")
        res = mod.do_args_kwargs_3(string_input, 6, test_var=1)  # pylint: disable=no-member
        assert as_formatted_evidence(res) == ":+--12-+:34 1 2 6 1"

    def test_format_with_two_argument_two_keywordargument_args_kwargs(self):  # type: () -> None
        string_input = "-1234 {} {} {} {test_kwarg} {test_var}"
        res = mod.do_args_kwargs_4(string_input, *[6], **{"test_var": 1})  # pylint: disable=no-member
        assert as_formatted_evidence(res) == "-1234 1 2 6 3 1"

        string_input = "-1234 {} {} {} {test_kwarg} {test_var}"
        res = mod.do_args_kwargs_4(string_input, 6, test_var=1)  # pylint: disable=no-member
        assert as_formatted_evidence(res) == "-1234 1 2 6 3 1"

        string_input = create_taint_range_with_format(":+--12-+:34 {} {} {} {test_kwarg} {test_var}")
        res = mod.do_args_kwargs_4(string_input, 6, test_var=1)  # pylint: disable=no-member
        assert as_formatted_evidence(res) == ":+--12-+:34 1 2 6 3 1"

    def test_format_when_tainted_template_range_special_then_tainted_result(self):  # type: () -> None
        self._assert_format_result(
            taint_escaped_template=":+-<input1>{:<15s}<input1>-+: parameter",
            taint_escaped_parameter="parameter",
            expected_result="parameter       parameter",
            escaped_expected_result=":+-<input1>parameter      <input1>-+: parameter",
        )

    def test_format_when_tainted_template_range_special_template_then_tainted_result(self):  # type: () -> None
        # TODO format with params doesn't work correctly
        # self._assert_format_result(
        #     taint_escaped_template="{:<25s} parameter",
        #     taint_escaped_parameter="a:+-<input2>aaaa<input2>-+:a",
        #     expected_result="aaaaaa                    parameter",
        #     escaped_expected_result="a:+-<input2>aaaa<input2>-+:a parameter",
        # )
        pass

    def test_format_key_error_and_no_log_metric(self, telemetry_writer):
        with pytest.raises(KeyError):
            mod.do_format_key_error("test1")

        list_metrics_logs = list(telemetry_writer._logs)
        assert len(list_metrics_logs) == 0


@pytest.mark.skip_iast_check_logs
def test_propagate_ranges_with_no_context(caplog):
    """Test taint_pyobject without context. This test is to ensure that the function does not raise an exception."""
    string_to_taint = "-1234 {} {} {} {test_kwarg} {test_var}"
    create_context()
    string_input = taint_pyobject(
        pyobject=string_to_taint,
        source_name="test_add_aspect_tainting_left_hand",
        source_value=string_to_taint,
        source_origin=OriginType.PARAMETER,
    )
    reset_context()
    with override_global_config(dict(_iast_debug=True)), caplog.at_level(logging.DEBUG):
        result_2 = mod.do_args_kwargs_4(string_input, 6, test_var=1)

    ranges_result = get_tainted_ranges(result_2)
    log_messages = [record.message for record in caplog.get_records("call")]
    assert not any("iast::" in message for message in log_messages), log_messages
    assert len(ranges_result) == 0


class ExportType(str, Enum):
    USAGE = "Usage"
    ACTUAL_COST = "ActualCost"


def test_format_value_aspect_no_change_patched_unpatched():
    # Issue: https://datadoghq.atlassian.net/jira/software/c/projects/APPSEC/boards/1141?selectedIssue=APPSEC-53155
    fstr_unpatched = f"{ExportType.ACTUAL_COST}"
    fstr_patched = mod.do_exporttype_member_format()
    assert fstr_patched == fstr_unpatched


class CustomSpec:
    def __str__(self):
        return "str"

    def __repr__(self):
        return "repr"

    def __format__(self, format_spec):
        return "format_" + format_spec


def test_format_value_aspect_no_change_customspec():
    c = CustomSpec()
    assert f"{c}" == mod.do_customspec_simple()
    assert f"{c!s}" == mod.do_customspec_cstr()
    assert f"{c!r}" == mod.do_customspec_repr()
    assert f"{c!a}" == mod.do_customspec_ascii()
    assert f"{c!s:<20s}" == mod.do_customspec_formatspec()
