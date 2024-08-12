# -*- encoding: utf-8 -*-
import unittest

import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking import taint_pyobject
from tests.appsec.iast.aspects.conftest import _iast_patched_module


mod = _iast_patched_module("benchmarks.bm.iast_fixtures.str_methods")


class TestOperatorAddReplacement(unittest.TestCase):
    def test_nostring_operator_add(self):
        # type: () -> None
        assert mod.do_operator_add_params(2, 3) == 5

    def test_regression_operator_add_re_compile(self):  # type: () -> None
        try:
            mod.do_add_re_compile()
        except Exception as e:
            pytest.fail(e)

    def test_string_operator_add_none_tainted(
        self,
    ):  # type: () -> None
        string_input = "foo"
        bar = "bar"
        result = mod.do_operator_add_params(string_input, bar)
        assert not get_tainted_ranges(result)

    def test_operator_add_dis(
        self,
    ):  # type: () -> None
        import dis

        bytecode = dis.Bytecode(mod.do_operator_add_params)
        dis.dis(mod.do_operator_add_params)
        assert bytecode.codeobj.co_names == ("ddtrace_aspects", "add_aspect")

    def test_string_operator_add_one_tainted(self):  # type: () -> None
        string_input = taint_pyobject(
            pyobject="foo",
            source_name="test_add_aspect_tainting_left_hand",
            source_value="foo",
            source_origin=OriginType.PARAMETER,
        )
        bar = "bar"
        assert get_tainted_ranges(string_input)
        result = mod.do_operator_add_params(string_input, bar)
        assert len(get_tainted_ranges(result)) == 1

    def test_string_operator_add_two(self):  # type: () -> None
        string_input = taint_pyobject(
            pyobject="foo",
            source_name="test_string_operator_add_two",
            source_value="foo",
            source_origin=OriginType.PARAMETER,
        )
        bar = taint_pyobject(
            pyobject="bar",
            source_name="test_string_operator_add_two",
            source_value="bar",
            source_origin=OriginType.PARAMETER,
        )

        result = mod.do_operator_add_params(string_input, bar)
        assert len(get_tainted_ranges(result)) == 2

    def test_decoration_when_function_and_decorator_modify_texts_then_tainted(
        self,
    ):  # type: () -> None
        prefix = taint_pyobject(pyobject="a", source_name="a", source_value="a", source_origin=OriginType.PARAMETER)
        suffix = taint_pyobject(pyobject="b", source_name="b", source_value="b", source_origin=OriginType.PARAMETER)

        result = mod.do_add_and_uppercase(prefix, suffix)

        assert result == "AB"
        assert len(get_tainted_ranges(result)) == 2

    def test_string_operator_add_one_tainted_mixed_bytearray_bytes(self):  # type: () -> None
        string_input = taint_pyobject(
            pyobject=b"foo", source_name="foo", source_value="foo", source_origin=OriginType.PARAMETER
        )
        assert get_tainted_ranges(string_input)
        bar = bytearray("bar", encoding="utf-8")
        result = mod.do_operator_add_params(string_input, bar)
        assert result == b"foobar"
        assert len(get_tainted_ranges(result)) == 0

    def test_string_operator_add_two_mixed_bytearray_bytes(self):  # type: () -> None
        string_input = taint_pyobject(
            pyobject=bytearray(b"foo"), source_name="foo", source_value="foo", source_origin=OriginType.PARAMETER
        )
        bar = taint_pyobject(pyobject=b"bar", source_name="bar", source_value="bar", source_origin=OriginType.PARAMETER)

        result = mod.do_operator_add_params(string_input, bar)
        assert result == bytearray(b"foobar")
        assert len(get_tainted_ranges(result)) == 0
