# -*- encoding: utf-8 -*-
import unittest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking import taint_pyobject
from tests.appsec.iast.aspects.conftest import _iast_patched_module


mod = _iast_patched_module("benchmarks.bm.iast_fixtures.str_methods")


class TestOperatorAddInplaceReplacement(unittest.TestCase):
    def test_nostring_operator_add(self):
        # type: () -> None
        assert mod.do_operator_add_inplace_params(2, 3) == 5

    def test_string_operator_add_inplace_none_tainted(
        self,
    ) -> None:
        string_input = "foo"
        bar = "bar"
        result = mod.do_operator_add_inplace_params(string_input, bar)
        assert not get_tainted_ranges(result)

    def test_operator_add_inplace_dis(
        self,
    ) -> None:
        import dis

        bytecode = dis.Bytecode(mod.do_operator_add_inplace_params)
        dis.dis(mod.do_operator_add_inplace_params)
        assert bytecode.codeobj.co_names == ("ddtrace_aspects", "add_inplace_aspect")

    def test_string_operator_add_inplace_one_tainted(self) -> None:
        string_input = taint_pyobject(
            pyobject="foo",
            source_name="test_add_aspect_tainting_left_hand",
            source_value="foo",
            source_origin=OriginType.PARAMETER,
        )
        bar = "bar"
        assert get_tainted_ranges(string_input)
        result = mod.do_operator_add_inplace_params(string_input, bar)
        assert len(get_tainted_ranges(result)) == 1

    def test_string_operator_add_inplace_two(self) -> None:
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

        result = mod.do_operator_add_inplace_params(string_input, bar)
        assert len(get_tainted_ranges(result)) == 2

    def test_string_operator_add_inplace_two_bytes(self) -> None:
        string_input = taint_pyobject(
            pyobject=b"foo",
            source_name="test_string_operator_add_two",
            source_value="foo",
            source_origin=OriginType.PARAMETER,
        )
        bar = taint_pyobject(
            pyobject=b"bar",
            source_name="test_string_operator_add_two",
            source_value="bar",
            source_origin=OriginType.PARAMETER,
        )

        result = mod.do_operator_add_inplace_params(string_input, bar)
        assert len(get_tainted_ranges(result)) == 2

    def test_string_operator_add_inplace_one_tainted_mixed_bytearray_bytes(self) -> None:
        string_input = taint_pyobject(
            pyobject=b"foo", source_name="foo", source_value="foo", source_origin=OriginType.PARAMETER
        )
        assert get_tainted_ranges(string_input)
        bar = bytearray("bar", encoding="utf-8")
        result = mod.do_operator_add_inplace_params(string_input, bar)
        assert result == b"foobar"
        assert len(get_tainted_ranges(result)) == 0

    def test_string_operator_add_inplace_two_mixed_bytearray_bytes(self) -> None:
        string_input = taint_pyobject(
            pyobject=bytearray(b"foo"), source_name="foo", source_value="foo", source_origin=OriginType.PARAMETER
        )
        bar = taint_pyobject(pyobject=b"bar", source_name="bar", source_value="bar", source_origin=OriginType.PARAMETER)

        result = mod.do_operator_add_inplace_params(string_input, bar)
        assert result == bytearray(b"foobar")
        assert len(get_tainted_ranges(result)) == 0


class TestOperatorAddInplaceMultipleTimesReplacement(unittest.TestCase):
    def test_nostring_operator_add(self):
        # type: () -> None
        assert mod.do_operator_add_inplace_3_times(2, 3) == 11

    def test_string_operator_add_inplace_none_tainted(
        self,
    ) -> None:
        string_input = "foo"
        bar = "bar"
        result = mod.do_operator_add_inplace_3_times(string_input, bar)
        assert result == "foobarbarbar"
        assert not get_tainted_ranges(result)

    def test_string_operator_add_inplace_one_tainted(self) -> None:
        string_input = taint_pyobject(
            pyobject="foo",
            source_name="test_add_aspect_tainting_left_hand",
            source_value="foo",
            source_origin=OriginType.PARAMETER,
        )
        bar = "bar"
        assert get_tainted_ranges(string_input)
        result = mod.do_operator_add_inplace_3_times(string_input, bar)
        assert result == "foobarbarbar"
        assert len(get_tainted_ranges(result)) == 1

    def test_string_operator_add_inplace_two(self) -> None:
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

        result = mod.do_operator_add_inplace_3_times(string_input, bar)
        assert result == "foobarbarbar"
        assert string_input == "foo"
        assert len(get_tainted_ranges(result)) == 4

    def test_string_operator_add_inplace_two_bytes(self) -> None:
        string_input = taint_pyobject(
            pyobject=b"foo",
            source_name="test_string_operator_add_two",
            source_value=b"foo",
            source_origin=OriginType.PARAMETER,
        )
        bar = taint_pyobject(
            pyobject=b"bar",
            source_name="test_string_operator_add_two",
            source_value=b"bar",
            source_origin=OriginType.PARAMETER,
        )

        result = mod.do_operator_add_inplace_3_times(string_input, bar)
        assert result == b"foobarbarbar"
        assert string_input == b"foo"
        assert len(get_tainted_ranges(result)) == 4

    def test_string_operator_add_inplace_one_tainted_mixed_bytearray_bytes(self) -> None:
        string_input = taint_pyobject(
            pyobject=b"foo", source_name="foo", source_value="foo", source_origin=OriginType.PARAMETER
        )
        assert get_tainted_ranges(string_input)
        bar = bytearray("bar", encoding="utf-8")
        result = mod.do_operator_add_inplace_3_times(string_input, bar)
        assert result == b"foobarbarbar"
        assert string_input == b"foo"
        assert len(get_tainted_ranges(result)) == 0

    def test_string_operator_add_inplace_bytearray(self) -> None:
        def do_operator_add_inplace_3_times_no_propagation(a, b):
            a += b
            a += b
            a += b
            return a

        string_input = bytearray(b"foo")
        result_no_tainted = do_operator_add_inplace_3_times_no_propagation(string_input, bytearray(b"bar"))
        assert result_no_tainted == bytearray(b"foobarbarbar")
        assert string_input == bytearray(b"foobarbarbar")

        result_no_tainted = mod.do_operator_add_inplace_3_times(bytearray(b"foo"), bytearray(b"bar"))
        assert result_no_tainted == bytearray(b"foobarbarbar")
        assert string_input == bytearray(b"foobarbarbar")

        string_input = taint_pyobject(
            pyobject=bytearray(b"foo"), source_name="foo", source_value="foo", source_origin=OriginType.PARAMETER
        )
        bar = taint_pyobject(
            pyobject=bytearray(b"bar"), source_name="bar", source_value="bar", source_origin=OriginType.PARAMETER
        )

        result = mod.do_operator_add_inplace_3_times(string_input, bar)

        assert result == bytearray(b"foobarbarbar")
        assert string_input == bytearray(b"foobarbarbar")
        assert get_tainted_ranges(result)

    def test_string_operator_add_inplace_two_mixed_bytearray_bytes(self) -> None:
        def do_operator_add_inplace_3_times_no_propagation(a, b):
            a += b
            a += b
            a += b
            return a

        string_input = bytearray(b"foo")
        result_no_tainted = do_operator_add_inplace_3_times_no_propagation(string_input, b"bar")
        assert result_no_tainted == bytearray(b"foobarbarbar")
        assert string_input == bytearray(b"foobarbarbar")

        result_no_tainted = mod.do_operator_add_inplace_3_times(bytearray(b"foo"), b"bar")
        assert result_no_tainted == bytearray(b"foobarbarbar")
        assert string_input == bytearray(b"foobarbarbar")

        string_input = taint_pyobject(
            pyobject=bytearray(b"foo"), source_name="foo", source_value="foo", source_origin=OriginType.PARAMETER
        )
        bar = taint_pyobject(pyobject=b"bar", source_name="bar", source_value="bar", source_origin=OriginType.PARAMETER)

        result = mod.do_operator_add_inplace_3_times(string_input, bar)

        assert result == bytearray(b"foobarbarbar")
        assert string_input == bytearray(b"foobarbarbar")
        assert len(get_tainted_ranges(result)) == 0
