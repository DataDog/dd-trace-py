import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import Source
from ddtrace.appsec._iast._taint_tracking import TaintRange
from ddtrace.appsec._iast._taint_tracking import as_formatted_evidence
from ddtrace.appsec._iast._taint_tracking import common_replace
from ddtrace.appsec._iast._taint_tracking import get_ranges
from ddtrace.appsec._iast._taint_tracking import set_ranges
from ddtrace.appsec._iast._taint_tracking import set_ranges_on_splitted
from ddtrace.appsec._iast._taint_tracking.aspects import _convert_escaped_text_to_tainted_text


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
        _ = common_replace("rjust", s, "10", 35)


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
    s = "abcdefgh"
    set_ranges(s, (_build_sample_range(0, 5, "first"),))
    assert as_formatted_evidence(s) == ":+-<first>abcde<first>-+:fgh"

    set_ranges(s, (_build_sample_range(1, 6, "first"),))
    assert as_formatted_evidence(s) == "a:+-<first>bcdefg<first>-+:h"

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
    assert as_formatted_evidence(s) == ":+-<first>ab<first>-+:c:+-<second>de<second>-+:fgh"


def test_as_formatted_evidence_convert_escaped_text_to_tainted_text():  # type: () -> None
    from ddtrace.appsec._iast._taint_tracking import TagMappingMode

    s = "abcdefgh"
    ranges = _build_sample_range(0, 5, "2")
    set_ranges(s, (ranges,))
    assert (
        as_formatted_evidence(s, tag_mapping_function=TagMappingMode.Mapper) == ":+-<1750328947>abcde<1750328947>-+:fgh"
    )
    assert _convert_escaped_text_to_tainted_text(":+-<1750328947>abcde<1750328947>-+:fgh", [ranges]) == "abcdefgh"


def test_set_ranges_on_splitted_str() -> None:
    s = "abc|efgh"
    range1 = _build_sample_range(0, 2, "first")
    range2 = _build_sample_range(4, 2, "second")
    set_ranges(s, (range1, range2))
    ranges = get_ranges(s)
    assert ranges

    parts = s.split("|")
    ok = set_ranges_on_splitted(s, ranges, parts)
    assert ok
    assert get_ranges(parts[0]) == [TaintRange(0, 2, Source("first", "sample_value", OriginType.PARAMETER))]
    assert get_ranges(parts[1]) == [TaintRange(0, 2, Source("second", "sample_value", OriginType.PARAMETER))]


def test_set_ranges_on_splitted_bytes() -> None:
    s = b"abc|efgh|ijkl"
    range1 = _build_sample_range(0, 2, "first")  # ab -> 0, 2
    range2 = _build_sample_range(5, 1, "second")  # f -> 1, 1
    range3 = _build_sample_range(11, 2, "third")  # jkl -> 1, 3
    set_ranges(s, (range1, range2, range3))
    ranges = get_ranges(s)
    assert ranges

    parts = s.split(b"|")
    ok = set_ranges_on_splitted(s, ranges, parts)
    assert ok
    assert get_ranges(parts[0]) == [TaintRange(0, 2, Source("first", "sample_value", OriginType.PARAMETER))]
    assert get_ranges(parts[1]) == [TaintRange(1, 1, Source("second", "sample_value", OriginType.PARAMETER))]
    assert get_ranges(parts[2]) == [TaintRange(2, 2, Source("third", "sample_value", OriginType.PARAMETER))]


def test_set_ranges_on_splitted_bytearray() -> None:
    s = bytearray(b"abc|efgh|ijkl")
    range1 = _build_sample_range(0, 2, "first")  # ab -> 0, 2
    range2 = _build_sample_range(5, 1, "second")  # f -> 1, 1 and 1, 6
    range3 = _build_sample_range(5, 6, "third")  # ij -> 0, 6

    set_ranges(s, (range1, range2, range3))
    ranges = get_ranges(s)
    assert ranges

    parts = s.split(b"|")
    ok = set_ranges_on_splitted(s, ranges, parts)
    assert ok
    assert get_ranges(parts[0]) == [TaintRange(0, 2, Source("first", "sample_value", OriginType.PARAMETER))]
    assert get_ranges(parts[1]) == [
        TaintRange(1, 1, Source("second", "sample_value", OriginType.PARAMETER)),
        TaintRange(1, 6, Source("second", "sample_value", OriginType.PARAMETER)),
    ]
    assert get_ranges(parts[2]) == [TaintRange(0, 6, Source("third", "sample_value", OriginType.PARAMETER))]
