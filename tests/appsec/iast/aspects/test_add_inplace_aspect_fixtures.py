# -*- encoding: utf-8 -*-
from copy import copy

import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._taint_objects import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from tests.appsec.iast.iast_utils import _iast_patched_module


mod = _iast_patched_module("benchmarks.bm.iast_fixtures.str_methods")


def do_operator_add_inplace_3_times_no_propagation(a, b):
    a += b
    a += b
    a += b
    return a


class TestOperatorAddInplaceReplacement(object):
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
        assert bytecode.codeobj.co_names == ("_ddtrace_aspects", "add_inplace_aspect")

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
        assert string_input == bytearray(b"foobar")
        assert len(get_tainted_ranges(result)) == 0


class TestOperatorAddInplaceMultipleTimesReplacement(object):
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

    @pytest.mark.parametrize(
        "string_input,add_param,expected_result,is_tainted",
        [
            (bytearray(b"foo"), bytearray(b"bar"), bytearray(b"foobarbarbar"), True),
            (bytearray(b"fo"), bytearray(b"ba"), bytearray(b"fobababa"), True),
            (bytearray(b"f"), bytearray(b"b"), bytearray(b"fbbb"), True),
            (bytearray(b"f" * 1000), bytearray(b"b" * 500), bytearray(b"f" * 1000 + b"b" * 500 * 3), True),
            (bytearray(b"foo"), b"bar", bytearray(b"foobarbarbar"), False),
            (bytearray(b"fo"), b"ba", bytearray(b"fobababa"), False),
            (bytearray(b"f"), b"b", bytearray(b"fbbb"), False),
            (bytearray(b"f" * 1000), b"b" * 500, bytearray(b"f" * 1000 + b"b" * 500 * 3), False),
        ]
        + [
            (bytearray(b"f" * i), bytearray(b"b" * j), bytearray(b"f" * i + b"b" * j * 3), True)
            for i in range(1, 20)
            for j in range(1, 20)
        ],
    )
    def test_string_operator_add_inplace_bytearray(self, string_input, add_param, expected_result, is_tainted) -> None:
        string_input_copy = copy(string_input)
        result_no_tainted = do_operator_add_inplace_3_times_no_propagation(string_input_copy, add_param)
        assert result_no_tainted == expected_result
        assert string_input_copy == expected_result

        string_input_copy = copy(string_input)
        result_no_tainted = mod.do_operator_add_inplace_3_times(string_input_copy, add_param)
        assert result_no_tainted == expected_result
        assert string_input_copy == expected_result

        string_input_copy = copy(string_input)
        string_input_copy = taint_pyobject(
            pyobject=string_input_copy, source_name="foo", source_value="foo", source_origin=OriginType.PARAMETER
        )
        bar = taint_pyobject(
            pyobject=add_param, source_name="bar", source_value="bar", source_origin=OriginType.PARAMETER
        )

        result = mod.do_operator_add_inplace_3_times(string_input_copy, bar)

        assert result == expected_result
        assert string_input_copy == expected_result
        if is_tainted:
            assert get_tainted_ranges(result)
        else:
            assert not get_tainted_ranges(result)

    def test_string_operator_add_inplace_list(self) -> None:
        list_input = ["foo1", "bar1"]
        result_no_tainted = do_operator_add_inplace_3_times_no_propagation(list_input, ["foo2", "bar2"])
        assert result_no_tainted == ["foo1", "bar1", "foo2", "bar2", "foo2", "bar2", "foo2", "bar2"]
        assert list_input == ["foo1", "bar1", "foo2", "bar2", "foo2", "bar2", "foo2", "bar2"]

        list_input = ["foo1", "bar1"]
        result_no_tainted = mod.do_operator_add_inplace_3_times(list_input, ["foo3", "bar3"])
        assert result_no_tainted == ["foo1", "bar1", "foo3", "bar3", "foo3", "bar3", "foo3", "bar3"]
        assert list_input == ["foo1", "bar1", "foo3", "bar3", "foo3", "bar3", "foo3", "bar3"]

        string_input = taint_pyobject(
            pyobject="foo1", source_name="foo", source_value="foo", source_origin=OriginType.PARAMETER
        )
        bar = taint_pyobject(
            pyobject="bar4", source_name="bar", source_value="bar4", source_origin=OriginType.PARAMETER
        )

        list_input = [string_input, "bar1"]

        result = mod.do_operator_add_inplace_3_times(list_input, ["foo4", bar])

        assert result == ["foo1", "bar1", "foo4", "bar4", "foo4", "bar4", "foo4", "bar4"]
        assert list_input == ["foo1", "bar1", "foo4", "bar4", "foo4", "bar4", "foo4", "bar4"]
        assert len(get_tainted_ranges(result)) == 0

    def test_string_operator_add_inplace_dict(self) -> None:
        list_input = {"foo1": "bar1"}
        with pytest.raises(TypeError):
            _ = do_operator_add_inplace_3_times_no_propagation(list_input, {"foo2": "bar2"})

        with pytest.raises(TypeError):
            _ = mod.do_operator_add_inplace_3_times(list_input, {"foo2": "bar2"})

    def test_string_operator_add_inplace_set(self) -> None:
        set_input = {"foo1", "bar1"}
        with pytest.raises(TypeError):
            _ = do_operator_add_inplace_3_times_no_propagation(set_input, {"foo2", "bar2"})

        with pytest.raises(TypeError):
            _ = mod.do_operator_add_inplace_3_times(set_input, {"foo2", "bar2"})

    def test_string_operator_add_inplace_tuple(self) -> None:
        list_input = tuple(["foo1", "bar1"])
        result_no_tainted = do_operator_add_inplace_3_times_no_propagation(list_input, tuple(["foo2", "bar2"]))
        assert result_no_tainted == ("foo1", "bar1", "foo2", "bar2", "foo2", "bar2", "foo2", "bar2")
        assert list_input == ("foo1", "bar1")

        list_input = tuple(["foo1", "bar1"])
        result_no_tainted = mod.do_operator_add_inplace_3_times(list_input, tuple(["foo3", "bar3"]))
        assert result_no_tainted == ("foo1", "bar1", "foo3", "bar3", "foo3", "bar3", "foo3", "bar3")
        assert list_input == ("foo1", "bar1")

        string_input = taint_pyobject(
            pyobject="foo1", source_name="foo", source_value="foo", source_origin=OriginType.PARAMETER
        )
        bar = taint_pyobject(
            pyobject="bar4", source_name="bar", source_value="bar4", source_origin=OriginType.PARAMETER
        )

        list_input = tuple([string_input, "bar1"])
        result = mod.do_operator_add_inplace_3_times(list_input, tuple(["foo4", bar]))

        assert result == ("foo1", "bar1", "foo4", "bar4", "foo4", "bar4", "foo4", "bar4")
        assert list_input == ("foo1", "bar1")
        assert len(get_tainted_ranges(result)) == 0

    def test_string_operator_add_inplace_tuple_plus_list(self) -> None:
        list_input = tuple(["foo1", "bar1"])
        with pytest.raises(TypeError):
            do_operator_add_inplace_3_times_no_propagation(list_input, ["foo2", "bar2"])

        list_input = tuple(["foo1", "bar1"])
        with pytest.raises(TypeError):
            mod.do_operator_add_inplace_3_times(list_input, ["foo3", "bar3"])

        string_input = taint_pyobject(
            pyobject="foo1", source_name="foo", source_value="foo", source_origin=OriginType.PARAMETER
        )
        bar = taint_pyobject(
            pyobject="bar4", source_name="bar", source_value="bar4", source_origin=OriginType.PARAMETER
        )

        list_input = tuple([string_input, "bar1"])
        with pytest.raises(TypeError):
            mod.do_operator_add_inplace_3_times(list_input, ["foo4", bar])

    def test_string_operator_add_inplace_list_plus_tuple(self) -> None:
        list_input = ["foo1", "bar1"]
        result_no_tainted = do_operator_add_inplace_3_times_no_propagation(list_input, tuple(["foo2", "bar2"]))
        assert result_no_tainted == ["foo1", "bar1", "foo2", "bar2", "foo2", "bar2", "foo2", "bar2"]
        assert list_input == ["foo1", "bar1", "foo2", "bar2", "foo2", "bar2", "foo2", "bar2"]

        list_input = ["foo1", "bar1"]
        result_no_tainted = mod.do_operator_add_inplace_3_times(list_input, tuple(["foo3", "bar3"]))
        assert result_no_tainted == ["foo1", "bar1", "foo3", "bar3", "foo3", "bar3", "foo3", "bar3"]
        assert list_input == ["foo1", "bar1", "foo3", "bar3", "foo3", "bar3", "foo3", "bar3"]

        string_input = taint_pyobject(
            pyobject="foo1", source_name="foo", source_value="foo", source_origin=OriginType.PARAMETER
        )
        bar = taint_pyobject(
            pyobject="bar4", source_name="bar", source_value="bar4", source_origin=OriginType.PARAMETER
        )

        list_input = [string_input, "bar1"]
        result = mod.do_operator_add_inplace_3_times(list_input, tuple(["foo4", bar]))

        assert result == ["foo1", "bar1", "foo4", "bar4", "foo4", "bar4", "foo4", "bar4"]
        assert list_input == ["foo1", "bar1", "foo4", "bar4", "foo4", "bar4", "foo4", "bar4"]
        assert len(get_tainted_ranges(result)) == 0

    def test_string_operator_add_inplace_object(self) -> None:
        class MyObject(object):
            attr_inplace = "attr_inplace"

            def __init__(self, attr_inplace):
                self.attr_inplace = attr_inplace

            def __iadd__(self, other):
                return self.attr_inplace + other * 3

        object_input = MyObject("foo1")
        result_no_tainted = do_operator_add_inplace_3_times_no_propagation(object_input, "foo2")
        assert result_no_tainted == "foo1foo2foo2foo2foo2foo2"
        assert object_input.attr_inplace == "foo1"

        object_input = MyObject("foo2")
        result_no_tainted = mod.do_operator_add_inplace_3_times(object_input, "foo3")
        assert result_no_tainted == "foo2foo3foo3foo3foo3foo3"
        assert object_input.attr_inplace == "foo2"

        string_input = taint_pyobject(
            pyobject="foo4", source_name="foo", source_value="foo", source_origin=OriginType.PARAMETER
        )
        bar = taint_pyobject(
            pyobject="bar5", source_name="bar", source_value="bar4", source_origin=OriginType.PARAMETER
        )

        object_input = MyObject(string_input)
        result_no_tainted = mod.do_operator_add_inplace_3_times(object_input, bar)
        assert result_no_tainted == "foo4bar5bar5bar5bar5bar5"
        assert object_input.attr_inplace == "foo4"
        assert len(get_tainted_ranges(object_input)) == 0
        assert len(get_tainted_ranges(object_input.attr_inplace)) == 1
        assert len(get_tainted_ranges(result_no_tainted)) == 2
