# -*- encoding: utf-8 -*-
import math
from typing import Any
from typing import NamedTuple

import pytest


try:
    from ddtrace.appsec.iast._taint_tracking import as_formatted_evidence
    from tests.appsec.iast.aspects.aspect_utils import BaseReplacement
    from tests.appsec.iast.aspects.aspect_utils import create_taint_range_with_format
    from tests.appsec.iast.aspects.conftest import _iast_patched_module
except (ImportError, AttributeError):
    pytest.skip("IAST not supported for this Python version", allow_module_level=True)


mod = _iast_patched_module("tests.appsec.iast.fixtures.aspects.str_methods")

EscapeContext = NamedTuple("EscapeContext", [("id", Any), ("position", int)])


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
            taint_escaped_template=u":+-<input1>template⚠️<input1>-+: {}",
            taint_escaped_parameter=u":+-<input2>parameter⚠️<input2>-+:",
            expected_result=u"template⚠️ parameter⚠️",
            escaped_expected_result=u":+-<input1>template⚠️<input1>-+: " u":+-<input2>parameter⚠️<input2>-+:",
        )

    def test_format_when_tainted_unicode_emoji_strings_then_tainted_result(self):
        # type: () -> None
        self._assert_format_result(
            taint_escaped_template=u":+-<input1>template⚠️<input1>-+: {}",
            taint_escaped_parameter=u":+-<input2>parameter⚠️<input2>-+:",
            expected_result=u"template⚠️ parameter⚠️",
            escaped_expected_result=u":+-<input1>template⚠️<input1>-+: " u":+-<input2>parameter⚠️<input2>-+:",
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
        self._assert_format_result(
            taint_escaped_template="{:<25s} parameter",
            taint_escaped_parameter="a:+-<input2>aaaa<input2>-+:a",
            expected_result="aaaaaa parameter",
            escaped_expected_result="a:+-<input2>aaaa<input2>-+:a parameter",
        )
