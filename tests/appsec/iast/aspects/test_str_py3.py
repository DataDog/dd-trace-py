# -*- encoding: utf-8 -*-
import pytest

from ddtrace.appsec._iast._taint_tracking import as_formatted_evidence
from tests.appsec.iast.aspects.aspect_utils import BaseReplacement
from tests.appsec.iast.aspects.aspect_utils import create_taint_range_with_format
from tests.appsec.iast.aspects.conftest import _iast_patched_module


mod = _iast_patched_module("benchmarks.bm.iast_fixtures.str_methods")
mod_py3 = _iast_patched_module("benchmarks.bm.iast_fixtures.str_methods_py3")


class TestOperatorsReplacement(BaseReplacement):
    @staticmethod
    def test_taint():  # type: () -> None
        string_input = "foo"
        assert as_formatted_evidence(string_input) == "foo"

        string_input = create_taint_range_with_format(":+-foo-+:")
        assert as_formatted_evidence(string_input) == ":+-foo-+:"

    def test_string_build_string_tainted(self):  # type: () -> None
        string_input = "foo"
        result = mod_py3.do_fmt_value(string_input)  # pylint: disable=no-member
        assert result == "foo     bar"

        string_input = create_taint_range_with_format(":+-foo-+:")
        result = mod_py3.do_fmt_value(string_input)  # pylint: disable=no-member
        assert result == "foo     bar"
        assert as_formatted_evidence(result) == ":+-foo-+:     bar"

    def test_string_fstring_tainted(self):
        # type: () -> None
        string_input = "foo"
        result = mod_py3.do_repr_fstring(string_input)
        assert result == "'foo'"

        string_input = create_taint_range_with_format(":+-foo-+:")

        result = mod_py3.do_repr_fstring(string_input)  # pylint: disable=no-member
        assert as_formatted_evidence(result) == "':+-foo-+:'"

    def test_string_fstring_with_format_tainted(self):
        # type: () -> None
        string_input = "foo"
        result = mod_py3.do_repr_fstring_with_format(string_input)
        assert result == "'foo'     "

        string_input = create_taint_range_with_format(":+-foo-+:")

        result = mod_py3.do_repr_fstring_with_format(string_input)  # pylint: disable=no-member
        assert as_formatted_evidence(result) == "':+-foo-+:'     "

    def test_int_fstring_zero_padding_tainted(self):
        int_input = 5
        result = mod_py3.do_zero_padding_fstring(int_input)  # pylint: disable=no-member
        assert result == "00005"

    def test_string_fstring_repr_str_twice_tainted(self):
        # type: () -> None
        string_input = "foo"

        result = mod_py3.do_repr_fstring_twice(string_input)  # pylint: disable=no-member
        assert result == "'foo' 'foo'"

        string_input = create_taint_range_with_format(":+-foo-+:")

        result = mod_py3.do_repr_fstring_twice(string_input)  # pylint: disable=no-member
        assert result == "'foo' 'foo'"
        assert as_formatted_evidence(result) == "':+-foo-+:' ':+-foo-+:'"

    def test_string_fstring_repr_object_twice_tainted(self):
        # type: () -> None
        string_input = "foo"
        result = mod.MyObject(string_input)
        assert repr(result) == "foo a"

        result = mod_py3.do_repr_fstring_twice(result)  # pylint: disable=no-member
        assert result == "foo a foo a"

        string_input = create_taint_range_with_format(":+-foo-+:")
        obj = mod.MyObject(string_input)  # pylint: disable=no-member

        result = mod_py3.do_repr_fstring_twice(obj)  # pylint: disable=no-member
        assert result == "foo a foo a"
        assert as_formatted_evidence(result) == ":+-foo-+: a :+-foo-+: a"

    def test_string_fstring_twice_different_objects_tainted(self):  # type: () -> None
        string_input = create_taint_range_with_format(":+-foo-+:")
        obj = mod.MyObject(string_input)  # pylint: disable=no-member
        obj2 = mod.MyObject(string_input)  # pylint: disable=no-member

        result = mod_py3.do_repr_fstring_twice_different_objects(obj, obj2)  # pylint: disable=no-member
        assert result == "foo a foo a"
        assert as_formatted_evidence(result) == ":+-foo-+: a :+-foo-+: a"

    def test_string_fstring_twice_different_objects_tainted_twice(self):  # type: () -> None
        string_input = create_taint_range_with_format(":+-foo-+:")
        obj = mod.MyObject(string_input)  # pylint: disable=no-member

        result = mod_py3.do_repr_fstring_with_format_twice(obj)  # pylint: disable=no-member
        assert result == "foo a      foo a      "
        assert as_formatted_evidence(result) == ":+-foo-+: a      :+-foo-+: a      "

    @pytest.mark.parametrize(
        "function",
        [
            mod_py3.do_repr_fstring_with_expression1,
            mod_py3.do_repr_fstring_with_expression2,
            mod_py3.do_repr_fstring_with_expression3,
            mod_py3.do_repr_fstring_with_expression4,
            mod_py3.do_repr_fstring_with_expression5,
        ],
    )
    def test_string_fstring_non_string(self, function):  # type: () -> None
        result = function()  # pylint: disable=no-member
        assert result == "Hello world, True!"
