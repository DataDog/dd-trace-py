#!/usr/bin/env python3
# -*- encoding: utf-8 -*-
from copy import copy

import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._native.taint_tracking import TaintRange_
from ddtrace.appsec._iast._taint_tracking._taint_objects import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
import ddtrace.appsec._iast._taint_tracking.aspects as ddtrace_aspects


@pytest.mark.parametrize(
    "obj1, obj2",
    [
        (3.5, 3.3),
        (complex(2, 1), complex(3, 4)),
        ("Hello ", "world"),
        ("🙀", "🌝"),
        (b"Hi", b""),
        (["a"], ["b"]),
        (bytearray("a", "utf-8"), bytearray("b", "utf-8")),
        (("a", "b"), ("c", "d")),
    ],
)
def test_add_inplace_aspect_successful(obj1, obj2):
    obj3 = copy(obj1)
    obj3 += obj2
    assert ddtrace_aspects.add_inplace_aspect(obj1, obj2) == obj3


@pytest.mark.parametrize(
    "obj1, obj2",
    [(b"Hi", ""), ("Hi", b""), ({"a", "b"}, {"c", "d"}), (dict(), dict())],
)
def test_add_inplace_aspect_type_error(obj1, obj2):
    obj3 = obj1
    with pytest.raises(TypeError) as e_info1:
        obj3 += obj2

    with pytest.raises(TypeError) as e_info2:
        ddtrace_aspects.add_inplace_aspect(obj1, obj2)

    assert str(e_info2.value) == str(e_info1.value)


@pytest.mark.parametrize(
    "obj1, obj2, should_be_tainted",
    [
        (3.5, 3.3, False),
        (complex(2, 1), complex(3, 4), False),
        ("Hello ", "world", True),
        (b"bye ", b"".join((b"bye", b" ")), True),
        ("🙀", "".join(("🙀", "")), True),
        ("a", "a", True),
        (b"a", b"a", True),
        (b"Hi", b"", True),
        (b"Hi ", b" world", True),
        (["a"], ["b"], False),
        (bytearray(b"a"), bytearray(b"b"), False),
        (("a", "b"), ("c", "d"), False),
    ],
)
def test_add_inplace_aspect_tainting_left_hand(obj1, obj2, should_be_tainted):
    if should_be_tainted:
        obj1 = taint_pyobject(
            pyobject=obj1,
            source_name="test_add_inplace_aspect_tainting_left_hand",
            source_value=obj1,
            source_origin=OriginType.PARAMETER,
        )
        if len(obj1):
            assert get_tainted_ranges(obj1)

    obj3 = copy(obj1)
    obj3 += obj2
    result = ddtrace_aspects.add_inplace_aspect(obj1, obj2)

    assert result == obj3
    if isinstance(obj2, (bytes, str, bytearray)) and len(obj2):
        assert result is not obj3
    assert is_pyobject_tainted(result) == should_be_tainted
    if should_be_tainted:
        assert get_tainted_ranges(result) == get_tainted_ranges(obj1)


@pytest.mark.parametrize(
    "obj1, obj2, should_be_tainted",
    [
        (3.5, 3.3, False),
        (complex(2, 1), complex(3, 4), False),
        ("Hello ", "world", True),
        (b"a", b"a", True),
        (b"bye ", b"bye ", True),
        ("🙀", "🌝", True),
        (b"Hi", b"", False),
        (["a"], ["b"], False),
        (bytearray("a", "utf-8"), bytearray("b", "utf-8"), True),
        (("a", "b"), ("c", "d"), False),
    ],
)
def test_add_inplace_aspect_tainting_right_hand(obj1, obj2, should_be_tainted):
    if should_be_tainted:
        obj2 = taint_pyobject(
            pyobject=obj2,
            source_name="test_add_inplace_aspect_tainting_right_hand",
            source_value=obj2,
            source_origin=OriginType.PARAMETER,
        )
        if len(obj2):
            assert get_tainted_ranges(obj2)

    result = ddtrace_aspects.add_inplace_aspect(obj1, obj2)

    assert is_pyobject_tainted(result) == should_be_tainted
    if isinstance(obj2, (str, bytes, bytearray)) and len(obj2):
        tainted_ranges = get_tainted_ranges(result)
        assert type(tainted_ranges) is list
        assert all(type(c) is TaintRange_ for c in tainted_ranges)
        assert (tainted_ranges != []) == should_be_tainted
        if should_be_tainted:
            assert len(tainted_ranges) == 1


@pytest.mark.parametrize(
    "obj1",
    [
        "abc",
        b"abc",
    ],
)
def test_add_inplace_aspect_tainting_add_inplace_itself(obj1):
    obj1 = taint_pyobject(
        pyobject=obj1,
        source_name="test_add_inplace_aspect_tainting_left_hand",
        source_value=obj1,
        source_origin=OriginType.PARAMETER,
    )

    result = ddtrace_aspects.add_inplace_aspect(obj1, obj1)
    obj3 = obj1
    obj3 += obj1
    assert result == obj3

    assert is_pyobject_tainted(result) is True
    ranges_result = get_tainted_ranges(result)
    assert len(ranges_result) == 2
    assert ranges_result[0].start == 0
    assert ranges_result[0].length == 3
    assert ranges_result[1].start == 3
    assert ranges_result[1].length == 3


@pytest.mark.parametrize(
    "obj1",
    [
        "abc",
        b"abc",
    ],
)
def test_add_inplace_aspect_tainting_add_inplace_itself_twice(obj1):
    obj1 = taint_pyobject(
        pyobject=obj1,
        source_name="test_add_inplace_aspect_tainting_left_hand",
        source_value=obj1,
        source_origin=OriginType.PARAMETER,
    )

    result = ddtrace_aspects.add_inplace_aspect(obj1, obj1)
    result = ddtrace_aspects.add_inplace_aspect(obj1, obj1)
    obj3 = obj1
    obj3 += obj1
    assert result == obj3

    assert is_pyobject_tainted(result) is True
    ranges_result = get_tainted_ranges(result)
    assert len(ranges_result) == 2
    assert ranges_result[0].start == 0
    assert ranges_result[0].length == 3
    assert ranges_result[1].start == 3
    assert ranges_result[1].length == 3


@pytest.mark.parametrize(
    "obj1, obj2",
    [
        ("abc", "def"),
        (b"abc", b"def"),
    ],
)
def test_add_inplace_aspect_tainting_add_inplace_right_twice(obj1, obj2):
    obj1 = taint_pyobject(
        pyobject=obj1,
        source_name="test_add_inplace_aspect_tainting_left_hand",
        source_value=obj1,
        source_origin=OriginType.PARAMETER,
    )

    result = ddtrace_aspects.add_inplace_aspect(obj1, obj2)
    result = ddtrace_aspects.add_inplace_aspect(obj1, obj2)
    obj3 = obj1
    obj3 += obj2
    assert result == obj3

    assert is_pyobject_tainted(result) is True
    ranges_result = get_tainted_ranges(result)
    assert len(ranges_result) == 1
    assert ranges_result[0].start == 0
    assert ranges_result[0].length == 3


@pytest.mark.parametrize(
    "obj1, obj2",
    [
        ("abc", "def"),
        (b"abc", b"def"),
    ],
)
def test_add_inplace_aspect_tainting_add_inplace_left_twice(obj1, obj2):
    obj1 = taint_pyobject(
        pyobject=obj1,
        source_name="test_add_inplace_aspect_tainting_left_hand",
        source_value=obj1,
        source_origin=OriginType.PARAMETER,
    )

    result = ddtrace_aspects.add_inplace_aspect(obj2, obj1)  # noqa
    result = ddtrace_aspects.add_inplace_aspect(obj2, obj1)
    obj3 = obj2
    obj3 += obj1
    assert result == obj3

    assert is_pyobject_tainted(result) is True
    ranges_result = get_tainted_ranges(result)
    assert len(ranges_result) == 1
    assert ranges_result[0].start == 3
    assert ranges_result[0].length == 3
