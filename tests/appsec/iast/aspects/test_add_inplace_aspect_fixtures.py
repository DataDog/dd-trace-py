# -*- encoding: utf-8 -*-
from copy import copy
import sys

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


def test_nostring_operator_add():
    # type: () -> None
    assert mod.do_operator_add_inplace_params(2, 3) == 5


def test_string_operator_add_inplace_none_tainted() -> None:
    string_input = "foo"
    bar = "bar"
    result = mod.do_operator_add_inplace_params(string_input, bar)
    assert not get_tainted_ranges(result)


def test_operator_add_inplace_dis() -> None:
    import dis

    bytecode = dis.Bytecode(mod.do_operator_add_inplace_params)
    dis.dis(mod.do_operator_add_inplace_params)
    assert bytecode.codeobj.co_names == ("_ddtrace_aspects", "add_inplace_aspect")


@pytest.mark.skipif(sys.version_info < (3, 9), reason="+= and dict are a inplace aspect in version 3.8")
def test_operator_add_inplace_keys_dis() -> None:
    import dis

    bytecode = dis.Bytecode(mod.do_operator_add_inplace_dict_key)
    dis.dis(mod.do_operator_add_inplace_dict_key)
    assert bytecode.codeobj.co_names == ("_ddtrace_aspects", "add_inplace_aspect", "index_aspect")


@pytest.mark.skipif(sys.version_info < (3, 9), reason="+= and dict are a inplace aspect in version 3.8")
def test_operator_add_inplace_dict_key_from_function_dis() -> None:
    import dis

    bytecode = dis.Bytecode(mod.do_operator_add_inplace_dict_key_from_function)
    dis.dis(mod.do_operator_add_inplace_dict_key_from_function)
    assert bytecode.codeobj.co_names == ("_ddtrace_aspects", "add_inplace_aspect", "_get_dictionary", "index_aspect")


@pytest.mark.skipif(sys.version_info > (3, 9), reason="+= and dict are a inplace aspect in version 3.8")
def test_operator_add_inplace_keys_dis_py38() -> None:
    import dis

    bytecode = dis.Bytecode(mod.do_operator_add_inplace_dict_key)
    dis.dis(mod.do_operator_add_inplace_dict_key)
    assert bytecode.codeobj.co_names == ("_ddtrace_aspects", "add_inplace_aspect")


def test_string_operator_add_inplace_one_tainted() -> None:
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


@pytest.mark.parametrize(
    "function",
    (
        "do_operator_add_inplace_dict_key",
        "do_operator_add_inplace_dict_key_from_function",
        "do_operator_add_inplace_dict_key_from_class",
    ),
)
def test_string_operator_add_inplace_dict_element_tainted_lhs(function) -> None:
    string_input = taint_pyobject(
        pyobject="foo",
        source_name="test_string_operator_add_inplace_dict_element_tainted_lhs",
        source_value="foo",
        source_origin=OriginType.PARAMETER,
    )
    d = {"key": string_input}
    assert get_tainted_ranges(string_input)
    result = getattr(mod, function)(d, "key", "bar")
    assert len(get_tainted_ranges(result)) == 1
    assert get_tainted_ranges(result) == get_tainted_ranges(string_input)
    assert d["key"] == "foobar"


@pytest.mark.parametrize(
    "function",
    (
        "do_operator_add_inplace_dict_key",
        "do_operator_add_inplace_dict_key_from_function",
        "do_operator_add_inplace_dict_key_from_class",
    ),
)
def test_string_operator_add_inplace_dict_element_tainted_rhs(function) -> None:
    string_input = taint_pyobject(
        pyobject="bar",
        source_name="test_string_operator_add_inplace_dict_element_tainted_rhs",
        source_value="bar",
        source_origin=OriginType.PARAMETER,
    )
    d = {"key": "foo"}
    assert get_tainted_ranges(string_input)
    result = getattr(mod, function)(d, "key", string_input)
    assert d["key"] == "foobar"
    assert len(get_tainted_ranges(d["key"])) == 1
    assert len(get_tainted_ranges(result)) == 1
    assert d["key"] == result


@pytest.mark.parametrize(
    "function",
    (
        "do_operator_add_inplace_dict_key",
        "do_operator_add_inplace_dict_key_from_function",
        "do_operator_add_inplace_dict_key_from_class",
    ),
)
def test_string_operator_add_inplace_dict_element_both_tainted(function) -> None:
    lhs = taint_pyobject(
        pyobject="foo",
        source_name="test_string_operator_add_inplace_dict_element_both_tainted_lhs",
        source_value="foo",
        source_origin=OriginType.PARAMETER,
    )
    rhs = taint_pyobject(
        pyobject="bar",
        source_name="test_string_operator_add_inplace_dict_element_both_tainted_rhs",
        source_value="bar",
        source_origin=OriginType.PARAMETER,
    )
    d = {"key": lhs}
    assert get_tainted_ranges(lhs)
    assert get_tainted_ranges(rhs)
    result = getattr(mod, function)(d, "key", rhs)
    assert d["key"] == "foobar"
    assert len(get_tainted_ranges(d["key"])) == 2
    assert len(get_tainted_ranges(result)) == 2
    assert d["key"] == result


@pytest.mark.parametrize(
    "function",
    (
        "do_operator_add_inplace_dict_key",
        "do_operator_add_inplace_dict_key_from_function",
        "do_operator_add_inplace_dict_key_from_class",
    ),
)
def test_string_operator_add_inplace_dict_element_nonexistent_key(function) -> None:
    string_input = taint_pyobject(
        pyobject="bar",
        source_name="test_string_operator_add_inplace_dict_element_nonexistent_key",
        source_value="bar",
        source_origin=OriginType.PARAMETER,
    )
    d = {}
    with pytest.raises(KeyError):
        getattr(mod, function)(d, "nonexistent", string_input)


@pytest.mark.parametrize(
    "function",
    (
        "do_operator_add_inplace_dict_key",
        "do_operator_add_inplace_dict_key_from_function",
        "do_operator_add_inplace_dict_key_from_class",
    ),
)
def test_string_operator_add_inplace_dict_element_multiple_operations(function) -> None:
    """Test multiple += operations on the same dictionary key"""
    d = {"key": "foo"}

    # First operation
    result1 = getattr(mod, function)(d, "key", "bar")
    assert d["key"] == "foobar"
    assert result1 == "foobar"

    # Second operation
    result2 = getattr(mod, function)(d, "key", "baz")
    assert d["key"] == "foobarbaz"
    assert result2 == "foobarbaz"

    # Third operation with tainted input
    tainted = taint_pyobject(
        pyobject="qux",
        source_name="test_multiple_ops",
        source_value="qux",
        source_origin=OriginType.PARAMETER,
    )
    result3 = getattr(mod, function)(d, "key", tainted)
    assert d["key"] == "foobarbazqux"
    assert result3 == "foobarbazqux"
    assert len(get_tainted_ranges(d["key"])) == 1
    assert len(get_tainted_ranges(result3)) == 1


@pytest.mark.parametrize(
    "function",
    (
        "do_operator_add_inplace_dict_key",
        "do_operator_add_inplace_dict_key_from_function",
        "do_operator_add_inplace_dict_key_from_class",
    ),
)
def test_string_operator_add_inplace_dict_nested_operations(function) -> None:
    """Test += operations with nested dictionaries"""
    d = {"outer": {"inner": "foo"}}

    # Test with untainted string
    result1 = getattr(mod, function)(d["outer"], "inner", "bar")
    assert d["outer"]["inner"] == "foobar"
    assert result1 == "foobar"

    # Test with tainted string
    tainted = taint_pyobject(
        pyobject="baz",
        source_name="test_nested_ops",
        source_value="baz",
        source_origin=OriginType.PARAMETER,
    )
    result2 = getattr(mod, function)(d["outer"], "inner", tainted)
    assert d["outer"]["inner"] == "foobarbaz"
    assert result2 == "foobarbaz"
    assert len(get_tainted_ranges(d["outer"]["inner"])) == 1
    assert len(get_tainted_ranges(result2)) == 1


@pytest.mark.parametrize(
    "function",
    (
        "do_operator_add_inplace_dict_key",
        "do_operator_add_inplace_dict_key_from_function",
        "do_operator_add_inplace_dict_key_from_class",
    ),
)
def test_string_operator_add_inplace_dict_dynamic_keys(function) -> None:
    """Test += operations with dynamic dictionary keys"""
    d = {}
    keys = ["key1", "key2", "key3"]

    # Initialize dictionary
    for key in keys:
        d[key] = "foo"

    # Test with untainted strings
    for key in keys:
        result = getattr(mod, function)(d, key, "bar")
        assert d[key] == "foobar"
        assert result == "foobar"

    # Test with tainted strings
    tainted = taint_pyobject(
        pyobject="baz",
        source_name="test_dynamic_keys",
        source_value="baz",
        source_origin=OriginType.PARAMETER,
    )
    for key in keys:
        result = getattr(mod, function)(d, key, tainted)
        assert d[key] == "foobarbaz"
        assert result == "foobarbaz"
        assert len(get_tainted_ranges(d[key])) == 1
        assert len(get_tainted_ranges(result)) == 1


@pytest.mark.parametrize(
    "function",
    (
        "do_operator_add_inplace_dict_key",
        "do_operator_add_inplace_dict_key_from_function",
        "do_operator_add_inplace_dict_key_from_class",
    ),
)
def test_string_operator_add_inplace_dict_string_values(function) -> None:
    """Test += operations with non-string dictionary values"""
    d = {
        "list": [
            taint_pyobject(
                pyobject="list_element",
                source_name="test",
                source_value="list_element",
                source_origin=OriginType.PARAMETER,
            )
        ],
        "tuple": (
            taint_pyobject(
                pyobject="tuple_element",
                source_name="test",
                source_value="tuple_element",
                source_origin=OriginType.PARAMETER,
            ),
        ),
    }

    # Test sequence operations
    result3 = getattr(mod, function)(d, "list", ["list_element2"])
    assert d["list"] == ["list_element", "list_element2"]
    assert result3 == ["list_element", "list_element2"]
    assert len(get_tainted_ranges(d["list"][0])) == 1
    assert len(get_tainted_ranges(result3[0])) == 1

    result4 = getattr(mod, function)(d, "tuple", ("tuple_element2",))
    assert d["tuple"] == ("tuple_element", "tuple_element2")
    assert result4 == ("tuple_element", "tuple_element2")
    assert len(get_tainted_ranges(d["tuple"][0])) == 1
    assert len(get_tainted_ranges(result4[0])) == 1


@pytest.mark.parametrize(
    "function",
    (
        "do_operator_add_inplace_dict_key",
        "do_operator_add_inplace_dict_key_from_function",
        "do_operator_add_inplace_dict_key_from_class",
    ),
)
def test_string_operator_add_inplace_dict_non_string_values(function) -> None:
    """Test += operations with non-string dictionary values"""
    d = {"int": 1, "float": 1.0, "list": [1], "tuple": (1,)}

    # Test numeric operations
    result1 = getattr(mod, function)(d, "int", 2)
    assert d["int"] == 3
    assert result1 == 3

    result2 = getattr(mod, function)(d, "float", 2.5)
    assert d["float"] == 3.5
    assert result2 == 3.5

    # Test sequence operations
    result3 = getattr(mod, function)(d, "list", [2])
    assert d["list"] == [1, 2]
    assert result3 == [1, 2]

    result4 = getattr(mod, function)(d, "tuple", (2,))
    assert d["tuple"] == (1, 2)
    assert result4 == (1, 2)


def test_string_operator_add_inplace_two() -> None:
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


def test_string_operator_add_inplace_two_bytes() -> None:
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


def test_string_operator_add_inplace_one_tainted_mixed_bytearray_bytes() -> None:
    string_input = taint_pyobject(
        pyobject=b"foo", source_name="foo", source_value="foo", source_origin=OriginType.PARAMETER
    )
    assert get_tainted_ranges(string_input)
    bar = bytearray("bar", encoding="utf-8")
    result = mod.do_operator_add_inplace_params(string_input, bar)
    assert result == b"foobar"
    assert len(get_tainted_ranges(result)) == 0


def test_string_operator_add_inplace_two_mixed_bytearray_bytes() -> None:
    string_input = taint_pyobject(
        pyobject=bytearray(b"foo"), source_name="foo", source_value="foo", source_origin=OriginType.PARAMETER
    )
    bar = taint_pyobject(pyobject=b"bar", source_name="bar", source_value="bar", source_origin=OriginType.PARAMETER)

    result = mod.do_operator_add_inplace_params(string_input, bar)
    assert result == bytearray(b"foobar")
    assert string_input == bytearray(b"foobar")
    assert len(get_tainted_ranges(result)) == 0


def test_nostring_operator_add_3_times():
    # type: () -> None
    assert mod.do_operator_add_inplace_3_times(2, 3) == 11


def test_string_operator_add_inplace_none_tainted_3_times() -> None:
    string_input = "foo"
    bar = "bar"
    result = mod.do_operator_add_inplace_3_times(string_input, bar)
    assert result == "foobarbarbar"
    assert not get_tainted_ranges(result)


def test_string_operator_add_inplace_one_tainted_3_times() -> None:
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


def test_string_operator_add_inplace_two_3_times() -> None:
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


def test_string_operator_add_inplace_two_bytes_3_times() -> None:
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


def test_string_operator_add_inplace_one_tainted_mixed_bytearray_bytes_3_times() -> None:
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
def test_string_operator_add_inplace_bytearray(string_input, add_param, expected_result, is_tainted) -> None:
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
    bar = taint_pyobject(pyobject=add_param, source_name="bar", source_value="bar", source_origin=OriginType.PARAMETER)

    result = mod.do_operator_add_inplace_3_times(string_input_copy, bar)

    assert result == expected_result
    assert string_input_copy == expected_result
    if is_tainted:
        assert get_tainted_ranges(result)
    else:
        assert not get_tainted_ranges(result)


def test_string_operator_add_inplace_list_3_times() -> None:
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
    bar = taint_pyobject(pyobject="bar4", source_name="bar", source_value="bar4", source_origin=OriginType.PARAMETER)

    list_input = [string_input, "bar1"]

    result = mod.do_operator_add_inplace_3_times(list_input, ["foo4", bar])

    assert result == ["foo1", "bar1", "foo4", "bar4", "foo4", "bar4", "foo4", "bar4"]
    assert list_input == ["foo1", "bar1", "foo4", "bar4", "foo4", "bar4", "foo4", "bar4"]
    assert len(get_tainted_ranges(result)) == 0


def test_string_operator_add_inplace_dict_3_times() -> None:
    list_input = {"foo1": "bar1"}
    with pytest.raises(TypeError):
        _ = do_operator_add_inplace_3_times_no_propagation(list_input, {"foo2": "bar2"})

    with pytest.raises(TypeError):
        _ = mod.do_operator_add_inplace_3_times(list_input, {"foo2": "bar2"})


def test_string_operator_add_inplace_set_3_times() -> None:
    set_input = {"foo1", "bar1"}
    with pytest.raises(TypeError):
        _ = do_operator_add_inplace_3_times_no_propagation(set_input, {"foo2", "bar2"})

    with pytest.raises(TypeError):
        _ = mod.do_operator_add_inplace_3_times(set_input, {"foo2", "bar2"})


def test_string_operator_add_inplace_tuple() -> None:
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
    bar = taint_pyobject(pyobject="bar4", source_name="bar", source_value="bar4", source_origin=OriginType.PARAMETER)

    list_input = tuple([string_input, "bar1"])
    result = mod.do_operator_add_inplace_3_times(list_input, tuple(["foo4", bar]))

    assert result == ("foo1", "bar1", "foo4", "bar4", "foo4", "bar4", "foo4", "bar4")
    assert list_input == ("foo1", "bar1")
    assert len(get_tainted_ranges(result)) == 0


def test_string_operator_add_inplace_tuple_plus_list_3_times() -> None:
    list_input = tuple(["foo1", "bar1"])
    with pytest.raises(TypeError):
        do_operator_add_inplace_3_times_no_propagation(list_input, ["foo2", "bar2"])

    list_input = tuple(["foo1", "bar1"])
    with pytest.raises(TypeError):
        mod.do_operator_add_inplace_3_times(list_input, ["foo3", "bar3"])

    string_input = taint_pyobject(
        pyobject="foo1", source_name="foo", source_value="foo", source_origin=OriginType.PARAMETER
    )
    bar = taint_pyobject(pyobject="bar4", source_name="bar", source_value="bar4", source_origin=OriginType.PARAMETER)

    list_input = tuple([string_input, "bar1"])
    with pytest.raises(TypeError):
        mod.do_operator_add_inplace_3_times(list_input, ["foo4", bar])


def test_string_operator_add_inplace_list_plus_tuple_3_times() -> None:
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
    bar = taint_pyobject(pyobject="bar4", source_name="bar", source_value="bar4", source_origin=OriginType.PARAMETER)

    list_input = [string_input, "bar1"]
    result = mod.do_operator_add_inplace_3_times(list_input, tuple(["foo4", bar]))

    assert result == ["foo1", "bar1", "foo4", "bar4", "foo4", "bar4", "foo4", "bar4"]
    assert list_input == ["foo1", "bar1", "foo4", "bar4", "foo4", "bar4", "foo4", "bar4"]
    assert len(get_tainted_ranges(result)) == 0


def test_string_operator_add_inplace_object_3_times() -> None:
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
    bar = taint_pyobject(pyobject="bar5", source_name="bar", source_value="bar4", source_origin=OriginType.PARAMETER)

    object_input = MyObject(string_input)
    result_no_tainted = mod.do_operator_add_inplace_3_times(object_input, bar)
    assert result_no_tainted == "foo4bar5bar5bar5bar5bar5"
    assert object_input.attr_inplace == "foo4"
    assert len(get_tainted_ranges(object_input)) == 0
    assert len(get_tainted_ranges(object_input.attr_inplace)) == 1
    assert len(get_tainted_ranges(result_no_tainted)) == 2
