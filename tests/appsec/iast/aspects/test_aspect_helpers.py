import itertools

import pytest
from ddtrace.appsec.iast._taint_tracking._native.aspect_helpers import (common_replace,
    as_formatted_evidence)
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


def _build_sample_range(start, end, name):  # type: (int, int) -> TaintRange
    return TaintRange(start, end, Source(name, "sample_value", OriginType.PARAMETER))


def test_as_formatted_evidence():  # type: () -> None
    s = 'abcdefgh'
    set_ranges(s, (_build_sample_range(0, 5, "first"),))
    assert as_formatted_evidence(s) == ':+-<first>abcde<first>-+:fgh'

    set_ranges(s, (_build_sample_range(1, 6, "first"),))
    assert as_formatted_evidence(s) == 'a:+-<first>bcdefg<first>-+:h'

    set_ranges(
        s,
        (
            _build_sample_range(0, 2, "first"),
            _build_sample_range(3, 1, "second"),
            _build_sample_range(4, 2, "third"),
        ),
    )
    assert as_formatted_evidence(s) == ":+-<first>ab<first>-+:c:+-<second>d<second>-+::+-<third>ef<third>-+:gh"

    set_ranges(s, (_build_sample_range(3, 2, "second"), _build_sample_range(0, 2, "first")))
    assert as_formatted_evidence(s) == ':+-<first>ab<first>-+:c:+-<second>de<second>-+:fgh'