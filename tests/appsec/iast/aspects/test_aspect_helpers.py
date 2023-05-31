import pytest
from ddtrace.appsec.iast._taint_tracking._native.aspect_helpers import common_replace
from ddtrace.appsec.iast._taint_tracking import (
    Source,
    OriginType,
    TaintRange,
    set_ranges,
    get_ranges,
)

_SOURCE1 = Source(name="name", value="value", origin=OriginType.COOKIE)
_SOURCE2 = Source(name="name2", value="value2", origin=OriginType.BODY)

_RANGE1 = TaintRange(0, 2, _SOURCE1)
_RANGE2 = TaintRange(1, 3, _SOURCE2)


def test_common_replace_untainted():
    s = "foobar"
    assert common_replace("upper", s) == "FOOBAR"


def test_common_replace_untainted_missing_method():
    s = "foobar"
    with pytest.raises(AttributeError):
        common_replace("doesntexist", s)

    with pytest.raises(AttributeError):
        common_replace("", s)


def test_common_replace_untainted_args_method():
    s = "foobar"
    # with pytest.raises(AttributeError):
    res = common_replace("rjust", s, 10, " ")
    assert res == "    foobar"


def test_common_replace_untainted_wrong_args_method():
    s = "foobar"
    # with pytest.raises(AttributeError):
    with pytest.raises(TypeError):
        res = common_replace("rjust", s, "10", 35)


def test_common_replace_tainted_str():
    s = "FooBar"
    set_ranges(s, [_RANGE1, _RANGE2])
    s2 = common_replace("lower", s)
    assert s2 == "foobar"
    assert get_ranges(s2) == [_RANGE1, _RANGE2]


def test_common_replace_tainted_bytes():
    s = b"FooBar"
    set_ranges(s, [_RANGE1, _RANGE2])
    s2 = common_replace("lower", s)
    assert s2 == b"foobar"
    assert get_ranges(s2) == [_RANGE1, _RANGE2]


def test_common_replace_tainted_bytearray():
    s = b"FooBar"
    set_ranges(s, [_RANGE1, _RANGE2])
    s2 = common_replace("lower", s)
    assert s2 == b"foobar"
    assert get_ranges(s2) == [_RANGE1, _RANGE2]
