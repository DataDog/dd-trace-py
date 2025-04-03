# -*- encoding: utf-8 -*-
import math
from typing import Any  # noqa:F401
from typing import Dict  # noqa:F401

import pytest

from ddtrace.appsec._iast._taint_tracking import as_formatted_evidence
from ddtrace.appsec._iast._taint_tracking import get_ranges
from tests.appsec.iast.aspects.aspect_utils import BaseReplacement
from tests.appsec.iast.iast_utils import _iast_patched_module


mod = _iast_patched_module("benchmarks.bm.iast_fixtures.str_methods")


class TestOperatorFormatMapReplacement(BaseReplacement):
    def _assert_format_map_result(
        self,
        taint_escaped_template,  # type: str
        taint_escaped_mapping,  # type: Dict[str, Any]
        expected_result,  # type: str
        escaped_expected_result,  # type: str
    ):  # type: (...) -> None
        template = self._to_tainted_string_with_origin(taint_escaped_template)
        mapping = {key: self._to_tainted_string_with_origin(value) for key, value in taint_escaped_mapping.items()}

        assert isinstance(template, str)
        result = mod.do_format_map(template, mapping)

        assert result == expected_result

        assert as_formatted_evidence(result, tag_mapping_function=None) == escaped_expected_result

    def test_format_map_when_template_is_none_then_raises_attribute_error(self):  # type: () -> None
        with pytest.raises(AttributeError):
            mod.do_format_map(None, {})

    def test_format_map_when_parameter_is_none_then_raises_type_error(self):  # type: () -> None
        with pytest.raises(TypeError):
            assert mod.do_format_map("{key}", None) == "None"

    def test_format_map_when_no_tainted_strings_then_no_tainted_result(self):  # type: () -> None
        result = mod.do_format_map("template {key}", {"key": "parameter"})
        assert result, "template parameter"
        assert not get_ranges(result)

    def test_format_map_when_tainted_parameter_then_tainted_result(self):  # type: () -> None
        self._assert_format_map_result(
            taint_escaped_template="template {key}",
            taint_escaped_mapping={"key": ":+-<input1>parameter<input1>-+:"},
            expected_result="template parameter",
            escaped_expected_result="template :+-<input1>parameter<input1>-+:",
        )

    def test_format_map_when_tainted_template_range_no_brackets_then_tainted_result(self):  # type: () -> None
        self._assert_format_map_result(
            taint_escaped_template=":+-<input1>template<input1>-+: {key}",
            taint_escaped_mapping={"key": "parameter"},
            expected_result="template parameter",
            escaped_expected_result=":+-<input1>template<input1>-+: parameter",
        )

    def test_format_map_when_tainted_template_range_with_brackets_then_tainted_result(self):  # type: () -> None
        self._assert_format_map_result(
            taint_escaped_template="template :+-<input1>{key}<input1>-+:",
            taint_escaped_mapping={"key": "parameter"},
            expected_result="template parameter",
            escaped_expected_result="template :+-<input1>parameter<input1>-+:",
        )

    def test_format_map_when_tainted_template_range_no_brackets_and_tainted_param_then_tainted(
        self,
    ):  # type: () -> None
        self._assert_format_map_result(
            taint_escaped_template=":+-<input1>template<input1>-+: {key}",
            taint_escaped_mapping={"key": ":+-<input2>parameter<input2>-+:"},
            expected_result="template parameter",
            escaped_expected_result=":+-<input1>template<input1>-+: " ":+-<input2>parameter<input2>-+:",
        )

    def test_format_map_when_tainted_template_range_with_brackets_and_tainted_param_then_tainted(
        self,
    ):  # type: () -> None
        self._assert_format_map_result(
            taint_escaped_template=":+-<input1>template {key}<input1>-+:",
            taint_escaped_mapping={"key": ":+-<input1>parameter<input2>-+:"},
            expected_result="template parameter",
            escaped_expected_result=":+-<input1>template <input1>-+:" ":+-<input2>parameter<input2>-+:",
        )

    def test_format_map_when_ranges_overlap_then_give_preference_to_ranges_from_parameter(self):  # type: () -> None
        self._assert_format_map_result(
            taint_escaped_template=":+-<input1>template {key} range overlapping<input1>-+:",
            taint_escaped_mapping={"key": ":+-<input2>parameter<input2>-+:"},
            expected_result="template parameter range overlapping",
            escaped_expected_result=":+-<input1>template <input1>-+:"
            ":+-<input2>parameter<input2>-+:"
            ":+-<input1> range overlapping<input1>-+:",
        )

    def test_format_map_when_tainted_str_emoji_strings_then_tainted_result(self):  # type: () -> None
        self._assert_format_map_result(
            taint_escaped_template=":+-<input1>template⚠️<input1>-+: {key}",
            taint_escaped_mapping={"key": ":+-<input2>parameter⚠️<input2>-+:"},
            expected_result="template⚠️ parameter⚠️",
            escaped_expected_result=":+-<input1>template⚠️<input1>-+: " ":+-<input2>parameter⚠️<input2>-+:",
        )

    def test_format_map_when_tainted_template_range_no_brackets_and_param_not_str_then_tainted(
        self,
    ):  # type: () -> None
        self._assert_format_map_result(
            taint_escaped_template=":+-<input1>template<input1>-+: {key:.2f}",
            taint_escaped_mapping={"key": math.pi},
            expected_result="template 3.14",
            escaped_expected_result=":+-<input1>template<input1>-+: 3.14",
        )

    def test_format_map_when_tainted_template_range_with_brackets_and_param_not_str_then_tainted(
        self,
    ):  # type: () -> None
        self._assert_format_map_result(
            taint_escaped_template=":+-<input1>template {key:.2f}<input1>-+:",
            taint_escaped_mapping={"key": math.pi},
            expected_result="template 3.14",
            escaped_expected_result=":+-<input1>template 3.14<input1>-+:",
        )

    def test_format_map_when_texts_tainted_and_contain_escape_sequences_then_result_uncorrupted(
        self,
    ):  # type: () -> None
        self._assert_format_map_result(
            taint_escaped_template=":+-<input1>template ::++--<0>my_code<0>--++::" "<input1>-+: {key}",
            taint_escaped_mapping={"key": ":+-<input2>parameter<input2>-+: " "::++--<0>my_code<0>--++::"},
            expected_result="template :+-<0>my_code<0>-+: parameter " ":+-<0>my_code<0>-+:",
            escaped_expected_result=":+-<input1>template :+-<0>my_code<0>-+:<input1>-+: "
            ":+-<input2>parameter<input2>-+: "
            ":+-<0>my_code<0>-+:",
        )

    def test_format_map_when_parameter_value_already_present_in_template_then_range_is_correct(
        self,
    ):  # type: () -> None
        self._assert_format_map_result(
            taint_escaped_template="aaaaaa{key}aaa",
            taint_escaped_mapping={"key": "a:+-<input1>a<input1>-+:a"},
            expected_result="aaaaaaaaaaaa",
            escaped_expected_result="aaaaaaa:+-<input1>a<input1>-+:aaaa",
        )
